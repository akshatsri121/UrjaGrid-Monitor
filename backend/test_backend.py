import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from server import Archive, parse_line, handler_for


class BackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = Archive(Path(self.tmp.name)/'test.sqlite3')
        self.values = parse_line('[SOLAR] 18.00V, 0.50A, 9.00W | [BATT] 12.40V, SoC: 75% | [LOAD] 6.00W | [TEMP] 27.0C')

    def tearDown(self):
        self.tmp.cleanup()

    def test_parser_and_persistence(self):
        self.assertEqual(self.values['load_power'],6)
        self.assertIsNone(parse_line('Connecting to Wi-Fi'))
        self.archive.add(self.values,ts=1000,solar_ready=False,load_ready=True)
        reopened = Archive(self.archive.path)
        row = reopened.window('24h',now=1001)['latest']
        self.assertIsNone(row['solar_power'])
        self.assertEqual(row['load_power'],6)
        self.assertIn('solar_sensor_missing',row['quality'])

    def test_energy_and_gap_handling(self):
        for ts in [1000,1060,1120,2000]:
            self.archive.add(self.values,ts=ts,solar_ready=True,load_ready=True)
        summary = self.archive.window('24h',now=2001)['summary']
        self.assertAlmostEqual(summary['load_energy_wh_estimate'],.2)
        self.assertAlmostEqual(summary['solar_energy_wh_estimate'],.3)
        self.assertAlmostEqual(summary['load_coverage_percent'],120/86400*100)

    def test_recent_events_bounded_and_rebuilt(self):
        for i in range(205):
            self.archive.event('test', str(i))
        recent=self.archive.recent_event_snapshot()
        self.assertEqual(len(recent),200)
        self.assertEqual({int(r['message']) for r in recent},set(range(5,205)))
        reopened=Archive(self.archive.path)
        self.assertEqual(reopened.recent_event_snapshot(),recent)
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],205)

    def test_recursive_timeline_merges_tables(self):
        with patch('server.time.time', return_value=1000):
            self.archive.event('test','Early event')
        with self.archive.connect() as db:
            db.execute('INSERT INTO notes(ts,text) VALUES (?,?)',(1500,'Middle note'))
        with patch('server.time.time', return_value=2000):
            self.archive.event('test','Late event')
        report=self.archive.window('24h',now=2100)
        self.assertEqual([r['ts'] for r in report['activity']],[2000,1500,1000])
        self.assertEqual([r['category'] for r in report['activity']],['event','note','event'])

    def test_concurrent_event_updates(self):
        workers=[threading.Thread(target=lambda: [self.archive.event('test','Concurrent') for _ in range(10)]) for _ in range(4)]
        for t in workers:
            t.start()
        for t in workers:
            t.join()
        records=self.archive.recent_event_snapshot()
        self.assertEqual(len(records),40)
        self.assertEqual(len({r['id'] for r in records}),40)
        with self.archive.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM events').fetchone()[0],40)

    def test_http_history_csv_notes(self):
        import time
        self.archive.add(self.values)
        httpd = ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.archive))
        t = threading.Thread(target=httpd.serve_forever,daemon=True)
        t.start()
        base='http://127.0.0.1:'+str(httpd.server_port)
        try:
            with urlopen(base+'/api/history?period=month') as r:
                self.assertEqual(json.load(r)['samples'],1)
            with urlopen(base+'/api/export.csv') as r:
                self.assertIn('timestamp_utc',r.read().decode())
            req=Request(base+'/api/notes',data=json.dumps({'text':'Battery inspected'}).encode(),headers={'Content-Type':'application/json'})
            with urlopen(req) as r:
                self.assertEqual(r.status,201)
            self.assertEqual(self.archive.window('24h')['notes'][0]['text'],'Battery inspected')
            self.archive.event('test','Endpoint event')
            with urlopen(base+'/api/recent-events') as r:
                recent=json.load(r)
                self.assertEqual(recent['events'][0]['message'],'Endpoint event')
                self.assertEqual(recent['capacity'],200)
            with urlopen(base+'/api/history') as r:
                self.assertEqual(len(json.load(r)['activity']),2)
            with self.assertRaises(HTTPError) as err:
                urlopen(base+'/api/history?period=wrong')
            self.assertEqual(err.exception.code,400)
            req=Request(base+'/api/notes',data=b'{"text":"bad"}',headers={'Origin':'https://untrusted.example'})
            with self.assertRaises(HTTPError) as err:
                urlopen(req)
            self.assertEqual(err.exception.code,403)
            with urlopen(base+'/') as r:
                self.assertIn('Microgrid History',r.read().decode())
        finally:
            httpd.shutdown()
            httpd.server_close()
            t.join()

if __name__=='__main__':
    unittest.main()
