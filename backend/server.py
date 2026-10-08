"""Local microgrid archive. No firmware credentials or cloud accounts required."""
import argparse
import csv
import io
import json
import math
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from data_structures import RecentEventList, recursive_merge_sort

ROOT = Path(__file__).resolve().parent
PERIODS = {'24h': 86400, 'week': 7*86400, 'month': 30*86400}
FIELDS = ['solar_voltage', 'solar_current', 'solar_power', 'battery_voltage',
          'soc', 'load_power', 'temperature']
PATTERN = re.compile(r'\[SOLAR\]\s*([-\d.]+)V,\s*([-\d.]+)A,\s*([-\d.]+)W\s*\|\s*\[BATT\]\s*([-\d.]+)V,\s*SoC:\s*(\d+)%\s*\|\s*\[LOAD\]\s*([-\d.]+)W\s*\|\s*\[TEMP\]\s*([-\d.]+)C')


def parse_line(line):
    match = PATTERN.search(line)
    if not match:
        return None
    values = [float(x) for x in match.groups()]
    if not all(math.isfinite(x) for x in values):
        return None
    return dict(zip(FIELDS, values))


class Archive:
    def __init__(self, path):
        self.path = str(path)
        self._event_lock = threading.RLock()
        self.recent_events = RecentEventList(capacity=200)
        with self.connect() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS measurements (
                  id INTEGER PRIMARY KEY, ts REAL NOT NULL,
                  solar_voltage REAL, solar_current REAL, solar_power REAL,
                  battery_voltage REAL, soc REAL, load_power REAL, temperature REAL,
                  source TEXT NOT NULL, quality TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS measurement_time ON measurements(ts);
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL,
                  message TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_time ON events(ts);
                CREATE TABLE IF NOT EXISTS notes (
                  id INTEGER PRIMARY KEY, ts REAL NOT NULL, text TEXT NOT NULL);
            ''')
            # Restore the cache on restart. Timestamp order is handled separately
            # by merge sort; insertion ID determines which 200 events are recent.
            saved = db.execute('SELECT * FROM events ORDER BY id DESC LIMIT 200').fetchall()
            for row in reversed(saved):
                self.recent_events.append(dict(row))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, values, ts=None, source='serial', solar_ready=None, load_ready=None, temp_ready=None):
        values = dict(values)
        flags = []
        if solar_ready is False:
            for key in ['solar_voltage', 'solar_current', 'solar_power']:
                values[key] = None
            flags.append('solar_sensor_missing')
        elif solar_ready is None:
            flags.append('solar_sensor_validity_unknown')
        if load_ready is False:
            for key in ['battery_voltage', 'soc', 'load_power']:
                values[key] = None
            flags.append('load_sensor_missing')
        elif load_ready is None:
            flags.append('load_sensor_validity_unknown')
        if temp_ready is not True:
            flags.append('temperature_validity_unknown')
        with self.connect() as db:
            db.execute('INSERT INTO measurements(ts,' + ','.join(FIELDS) + ',source,quality) VALUES (' + ','.join(['?']*10) + ')',
                       [ts if ts is not None else time.time()] + [values.get(k) for k in FIELDS] + [source, ','.join(flags) or 'ok'])

    def event(self, kind, message):
        with self._event_lock:
            ts = time.time()
            with self.connect() as db:
                cursor = db.execute('INSERT INTO events(ts,kind,message) VALUES (?,?,?)', (ts, kind, message))
                event_id = cursor.lastrowid
            # Publish to the linked list only after the database commit succeeds.
            self.recent_events.append({'id':event_id, 'ts':ts, 'kind':kind, 'message':message})

    def recent_event_snapshot(self):
        with self._event_lock:
            records = self.recent_events.snapshot()
        return recursive_merge_sort(records, key=lambda r: (r['ts'], r['id']), reverse=True)

    def window(self, period, now=None):
        end = time.time() if now is None else now
        start = end - PERIODS[period]
        with self.connect() as db:
            # Include one preceding point for boundary integration; never bridge gaps >180s.
            rows = [dict(r) for r in db.execute('SELECT * FROM measurements WHERE ts BETWEEN ? AND ? ORDER BY ts,id', (start, end))]
            prev = db.execute('SELECT * FROM measurements WHERE ts < ? ORDER BY ts DESC LIMIT 1', (start,)).fetchone()
            events = [dict(r) for r in db.execute('SELECT * FROM events WHERE ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT 200', (start,end))]
            notes = [dict(r) for r in db.execute('SELECT * FROM notes WHERE ts BETWEEN ? AND ? ORDER BY ts DESC LIMIT 200', (start,end))]
        integration = ([dict(prev)] if prev else []) + rows
        energy = {'load_power': 0., 'solar_power': 0.}
        covered = {'load_power': 0., 'solar_power': 0.}
        for a,b in zip(integration, integration[1:]):
            dt = b['ts']-a['ts']
            if not 0 < dt <= 180:
                continue
            lo,hi = max(start,a['ts']), min(end,b['ts'])
            if hi <= lo:
                continue
            for key in energy:
                if a[key] is None or b[key] is None:
                    continue
                v0 = a[key] + (b[key]-a[key])*(lo-a['ts'])/dt
                v1 = a[key] + (b[key]-a[key])*(hi-a['ts'])/dt
                energy[key] += (v0+v1)/2*(hi-lo)/3600
                covered[key] += hi-lo
        # <=720 time buckets; each carries min/mean/max, not raw million-point series.
        width = PERIODS[period]/720
        buckets = {}
        for row in rows:
            bucket = int((row['ts']-start)/width)
            buckets.setdefault(bucket, []).append(row)
        points = []
        for bucket, entries in sorted(buckets.items()):
            point = {'ts': start+(bucket+.5)*width, 'count': len(entries)}
            for key in FIELDS:
                vals = [r[key] for r in entries if r[key] is not None]
                point[key] = sum(vals)/len(vals) if vals else None
                point[key+'_min'] = min(vals) if vals else None
                point[key+'_max'] = max(vals) if vals else None
            points.append(point)
        temps = [r['temperature'] for r in rows if r['temperature'] is not None]
        powers = [r['load_power'] for r in rows if r['load_power'] is not None]
        # Events and maintenance notes come from different tables. Merge their
        # records into a chronological report timeline using explicit recursion.
        activity = [dict(r, category='event') for r in events]
        activity += [dict(r, category='note') for r in notes]
        activity = recursive_merge_sort(activity, key=lambda r: r['ts'], reverse=True)
        return {'period':period, 'start':start, 'end':end, 'samples':len(rows),
                'latest':rows[-1] if rows else None, 'points':points, 'events':events, 'notes':notes,
                'activity':activity,
                'summary': {'load_energy_wh_estimate':energy['load_power'],
                            'solar_energy_wh_estimate':energy['solar_power'],
                            'load_coverage_percent':covered['load_power']/PERIODS[period]*100,
                            'solar_coverage_percent':covered['solar_power']/PERIODS[period]*100,
                            'peak_load_w':max(powers) if powers else None,
                            'maximum_temperature_c':max(temps) if temps else None}}


def collect(archive, port):
    import serial
    while True:
        try:
            # Opening some ESP32 USB adapters can reset the board.
            with serial.Serial(port, 115200, timeout=1) as device:
                archive.event('connection', 'Serial connected: '+port)
                solar_ready = load_ready = None
                buffer = b''
                previous_status = None
                while True:
                    buffer += device.read(1024)
                    if len(buffer) > 65536:
                        buffer = b''
                    while b'\n' in buffer:
                        raw, buffer = buffer.split(b'\n',1)
                        line = raw.decode('utf-8', errors='replace').strip()
                        if 'Starting Smart Renewable' in line:
                            solar_ready = load_ready = None
                            archive.event('restart','Firmware restarted')
                        if 'Solar INA219' in line:
                            solar_ready = 'not detected' not in line.lower() and 'detected' in line.lower()
                        if 'Load INA219' in line:
                            load_ready = 'not detected' not in line.lower() and 'detected' in line.lower()
                        values = parse_line(line)
                        if values:
                            archive.add(values, solar_ready=solar_ready, load_ready=load_ready)
                        if line.startswith('[STATUS]') and line != previous_status:
                            archive.event('device_status', line[:1000])
                            previous_status = line
        except (serial.SerialException, OSError) as exc:
            archive.event('connection_error', str(exc)[:500])
            time.sleep(5)


def handler_for(archive):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, body, mime='application/json', status=200):
            data = body.encode() if isinstance(body,str) else json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', mime+'; charset=utf-8')
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlsplit(self.path)
            period = parse_qs(url.query).get('period',['24h'])[0]
            if period not in PERIODS:
                return self.respond({'error':'period must be 24h, week, or month'}, status=400)
            if url.path == '/':
                frontend = ROOT.parent/'frontend'/'index.html'
                if not frontend.exists():
                    frontend = ROOT/'index.html'
                return self.respond(frontend.read_text(encoding='utf-8'), 'text/html')
            if url.path == '/api/history':
                return self.respond(archive.window(period))
            if url.path == '/api/recent-events':
                return self.respond({'events':archive.recent_event_snapshot(), 'capacity':200})
            if url.path == '/api/health':
                with archive.connect() as db:
                    last = db.execute('SELECT MAX(ts) FROM measurements').fetchone()[0]
                return self.respond({'last_sample':last, 'stale':last is None or time.time()-last>180})
            if url.path == '/api/export.csv':
                report = archive.window(period)
                out = io.StringIO()
                writer = csv.writer(out)
                keys = ['ts']+FIELDS+['source','quality']
                writer.writerow(['timestamp_utc']+FIELDS+['source','quality'])
                with archive.connect() as db:
                    for r in db.execute('SELECT * FROM measurements WHERE ts BETWEEN ? AND ? ORDER BY ts,id',(report['start'],report['end'])):
                        writer.writerow([datetime.fromtimestamp(r['ts'],timezone.utc).isoformat()]+[r[k] for k in keys[1:]])
                return self.respond(out.getvalue(),'text/csv')
            return self.respond({'error':'not found'},status=404)

        def do_POST(self):
            if self.path != '/api/notes':
                return self.respond({'error':'not found'},status=404)
            # Browser same-origin guard. Server is loopback-only, not a hosted/authenticated service.
            if self.headers.get('Origin') not in (None, 'http://'+self.headers.get('Host','')):
                return self.respond({'error':'origin rejected'},status=403)
            try:
                size = int(self.headers.get('Content-Length','0'))
                if not 0<size<=8192:
                    raise ValueError('invalid request size')
                data = json.loads(self.rfile.read(size))
                note = data['text']
                if not isinstance(note,str) or not 1<=len(note.strip())<=2000:
                    raise ValueError('note must contain 1–2000 characters')
                with archive.connect() as db:
                    db.execute('INSERT INTO notes(ts,text) VALUES (?,?)',(time.time(),note.strip()))
                self.respond({'saved':True},status=201)
            except (ValueError,KeyError,TypeError) as exc:
                self.respond({'error':str(exc)},status=400)

        def log_message(self,*args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--serial', help='ESP32 COM port, e.g. COM5')
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--db',default=str(ROOT/'data'/'microgrid.sqlite3'))
    parser.add_argument('--demo',action='store_true',help='Synthetic data in a SEPARATE demo database')
    args = parser.parse_args()
    if args.demo and args.serial:
        parser.error('Choose --demo OR --serial')
    if args.demo:
        args.db = str(ROOT/'data'/'demo.sqlite3')
    Path(args.db).parent.mkdir(parents=True,exist_ok=True)
    archive = Archive(args.db)
    if args.demo:
        with archive.connect() as db:
            empty = db.execute('SELECT COUNT(*) FROM measurements').fetchone()[0]==0
        if empty:
            end = time.time()
            with archive.connect() as db:
                records = []
                for n in range(30*24*60+1):
                    ts = end-30*86400+n*60
                    phase = (ts%86400)/86400*2*math.pi
                    power = 3+2*math.sin(phase)**2
                    vals = [18., .5, max(0,9*math.sin(phase)),12.4,75.,power,27+3*math.sin(phase)]
                    records.append([ts]+vals+['demo','synthetic'])
                db.executemany('INSERT INTO measurements(ts,'+','.join(FIELDS)+',source,quality) VALUES ('+','.join(['?']*10)+')',records)
            archive.event('demo','Synthetic demonstration data, not hardware readings')
    if args.serial:
        try:
            import serial
        except ImportError:
            parser.error('Install requirements first: python -m pip install -r requirements.txt')
        threading.Thread(target=collect,args=(archive,args.serial),daemon=True).start()
    print('History dashboard: http://127.0.0.1:'+str(args.port), flush=True)
    print('Database: '+args.db, flush=True)
    try:
        ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(archive)).serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()
