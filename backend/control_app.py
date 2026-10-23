"""Wi-Fi control service. Fail-closed authentication; explicit synthetic demo."""
import argparse
import binascii
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import secrets
import threading
import time
from pathlib import Path
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.exceptions import RequestValidationError
try:
    from .control_models import Telemetry, CommandRequest, NoteRequest, LoginRequest, DemoBattery
    from .control_store import SQLiteArchive, PostgresArchive, Conflict, PERIODS
except ImportError:
    from control_models import Telemetry, CommandRequest, NoteRequest, LoginRequest, DemoBattery
    from control_store import SQLiteArchive, PostgresArchive, Conflict, PERIODS

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env', override=False)
COOKIE = 'urjagrid_session'


def create_app(settings=None, archive=None):
    cfg = dict(os.environ) if settings is None else dict(settings)
    demo = cfg.get('URJAGRID_DEMO') == '1'
    hosted = bool(cfg.get('VERCEL'))
    device_id = cfg.get('DEVICE_ID', 'urjagrid-01')
    origin = cfg.get('APP_ORIGIN', '').rstrip('/')
    state_lock = threading.RLock()
    app = FastAPI(title='UrjaGrid Monitor', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.archive = archive
    app.state.demo_seq = 0
    app.state.demo_last = 0.0
    app.state.demo_states = {'bulb': True, 'fan': True}
    app.state.demo_soc = None
    app.state.demo_fan_locked = False

    def store():
        with state_lock:
            if app.state.archive is None:
                if demo:
                    if hosted:
                        raise HTTPException(503, 'Demo must run locally; it cannot control a device')
                    path = Path(cfg.get('DEMO_DATABASE', str(ROOT / 'backend/data/control-demo.sqlite3')))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    app.state.archive = SQLiteArchive(path, device_id=device_id, demo=True)
                elif cfg.get('DATABASE_URL'):
                    app.state.archive = PostgresArchive(cfg['DATABASE_URL'], device_id=device_id)
                elif hosted:
                    raise HTTPException(503, 'Configure DATABASE_URL before deployment')
                else:
                    path = ROOT / 'backend/data/control-live.sqlite3'
                    path.parent.mkdir(parents=True, exist_ok=True)
                    app.state.archive = SQLiteArchive(path, device_id=device_id)
            return app.state.archive

    def auth_mode():
        if demo:
            return 'demo'
        if cfg.get('SUPABASE_URL') and cfg.get('SUPABASE_PUBLISHABLE_KEY') and cfg.get('ALLOWED_EMAILS'):
            return 'supabase'
        if not hosted and len(cfg.get('DASHBOARD_PASSWORD', '')) >= 12 and len(cfg.get('SESSION_SECRET', '')) >= 32:
            return 'local_password'
        return 'unconfigured'

    def check_origin(request):
        supplied = request.headers.get('origin')
        expected = origin or (str(request.base_url).rstrip('/') if not hosted else '')
        if hosted and not origin:
            raise HTTPException(503, 'Configure APP_ORIGIN')
        if supplied and supplied != expected:
            raise HTTPException(403, 'Cross-origin request rejected')
        if request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403, 'Cross-site request rejected')

    def local_session():
        payload = base64.urlsafe_b64encode(json.dumps({'exp': int(time.time()) + 3600}).encode()).decode()
        signature = hmac.new(cfg['SESSION_SECRET'].encode(), payload.encode(), hashlib.sha256).hexdigest()
        return payload + '.' + signature

    def actor(request):
        check_origin(request)
        mode = auth_mode()
        if mode == 'demo':
            return 'demo user'
        if mode == 'unconfigured':
            raise HTTPException(503, 'Authentication is not configured. See docs/SOFTWARE_SETUP.md')
        token = request.cookies.get(COOKIE, '')
        if mode == 'local_password':
            try:
                payload, signature = token.rsplit('.', 1)
                expected = hmac.new(cfg['SESSION_SECRET'].encode(), payload.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise ValueError()
                if json.loads(base64.urlsafe_b64decode(payload))['exp'] <= time.time():
                    raise ValueError()
                return 'local operator'
            except (ValueError, KeyError, TypeError, binascii.Error):
                raise HTTPException(401, 'Please sign in')
        if not token:
            raise HTTPException(401, 'Please sign in')
        try:
            with httpx.Client(timeout=5) as client:
                response = client.get(cfg['SUPABASE_URL'].rstrip('/') + '/auth/v1/user',
                    headers={'apikey': cfg['SUPABASE_PUBLISHABLE_KEY'], 'Authorization': 'Bearer ' + token})
            if response.status_code != 200:
                raise HTTPException(401, 'Session expired; sign in again')
            email = str(response.json().get('email', '')).lower()
            if email not in {e.strip().lower() for e in cfg['ALLOWED_EMAILS'].split(',')}:
                raise HTTPException(403, 'User is not authorized for this device')
            return email
        except httpx.HTTPError:
            raise HTTPException(503, 'Authentication service unavailable')

    def demo_tick():
        if not demo:
            return
        with state_lock:
            now = time.time()
            if now - app.state.demo_last < 4:
                return
            archive = store()
            if app.state.demo_seq == 0:
                # Seed 30 days at five-minute intervals, once per demo database.
                with archive.engine.connect() as conn:
                    count = conn.execute(archive.samples.select().limit(1)).first()
                if count is None:
                    records = []
                    previous = None
                    for i in range(30 * 24 * 12 + 1):
                        ts = now - 30 * 86400 + i * 300
                        sample = synthetic(ts, i, {'bulb': True, 'fan': True})
                        record = dict(device_id=device_id, boot_id='demo-history', sequence=i, ts=ts,
                            measured_at=ts, payload=dict(sample, received_at=ts, source='demo'),
                            interval_s=300 if previous else 0, battery_voltage=sample['battery_voltage'],
                            soc_estimate=sample['soc_estimate'], temperature_c=sample['temperature_c'])
                        for load in ('bulb', 'fan'):
                            for k in ('voltage_v', 'current_a', 'power_w'):
                                record[load + '_' + k] = sample[load][k]
                            record[load + '_wh'] = ((previous[load]['power_w'] + sample[load]['power_w']) / 2 * 300 / 3600) if previous else 0
                            record[load + '_coverage_s'] = 300 if previous else 0
                        records.append(record)
                        previous = sample
                    with archive.transaction() as conn:
                        conn.execute(archive.samples.insert(), records)
                # Unique boot per process: retained pending demo commands must not replay.
                app.state.demo_boot = 'demo-' + secrets.token_hex(8)
            app.state.demo_seq += 1
            sample = synthetic(now, app.state.demo_seq, app.state.demo_states)
            sample['boot_id'] = app.state.demo_boot
            response = archive.sync(sample, now)
            acks = []
            for command in response['commands']:
                app.state.demo_states[command['load']] = command['on']
                acks.append(dict(id=command['id'], status='applied', reason='Simulated, no hardware'))
            if acks:
                app.state.demo_seq += 1
                sample = synthetic(now, app.state.demo_seq, app.state.demo_states)
                sample.update(boot_id=app.state.demo_boot, acks=acks)
                archive.sync(sample, now)
            app.state.demo_last = now

    def synthetic(ts, sequence, states):
        phase = ts / 86400 * 2 * math.pi
        voltage = 12.55 + .15 * math.sin(phase)
        soc = app.state.demo_soc if app.state.demo_soc is not None else 72 + 8 * math.sin(phase)
        if app.state.demo_soc is not None:
            voltage = 11.8 + soc / 100
            if soc < 50:
                app.state.demo_fan_locked = True
                states['fan'] = False
            elif soc >= 55:
                app.state.demo_fan_locked = False
        sample = dict(protocol=1, device_id=device_id, boot_id='demo-history', sequence=sequence,
            uptime_ms=sequence * 5000, measured_at=ts, battery_voltage=voltage,
            soc_estimate=soc, soc_source='generic_vrla_voltage_v1',
            temperature_c=27 + 2 * math.sin(phase), acks=[])
        for load, nominal in (('bulb', 8.5), ('fan', 1.1)):
            power = nominal * (1 + .06 * math.sin(phase * 3)) if states[load] else 0
            sample[load] = dict(valid=True, voltage_v=voltage, current_a=power / voltage,
                                power_w=power, on=states[load], blocked_reason=(
                                    'soc_below_50_or_recovery_below_55' if load == 'fan' and app.state.demo_fan_locked else ''))
        return sample

    @app.middleware('http')
    async def security(request, call_next):
        # Body cap before JSON parsing, including chunked requests.
        if request.method == 'POST':
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 32768:
                    return JSONResponse({'detail': 'Request too large'}, status_code=413)
            sent = False
            async def receive():
                nonlocal sent
                if not sent:
                    sent = True
                    return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
                return {'type': 'http.disconnect'}
            request._receive = receive
            request._body = bytes(body)
        try:
            response = await call_next(request)
        except Exception as exc:
            # Do not put connection strings, credentials or SQL statements in HTTP errors.
            logging.getLogger('urjagrid').error('Request failed: %s (details withheld to protect configuration)', type(exc).__name__)
            return JSONResponse({'detail': 'Service unavailable; check server configuration'}, status_code=503)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request, exc):
        # Do not reflect passwords, tokens or non-JSON NaN inputs in an error.
        return JSONResponse({'detail': 'Invalid request fields', 'errors': [
            {'field': '.'.join(map(str, e['loc'])), 'message': e['msg']} for e in exc.errors()]}, status_code=422)

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'frontend/control.html')

    @app.get('/assets/{name}')
    def asset(name: str):
        if name not in ('control.js', 'control.css'):
            raise HTTPException(404)
        return FileResponse(ROOT / 'frontend' / name)

    @app.get('/api/config')
    def config():
        return dict(mode='demo' if demo else 'live', auth=auth_mode(), device_id=device_id)

    @app.post('/api/login')
    def login(data: LoginRequest, request: Request):
        check_origin(request)
        mode = auth_mode()
        if mode == 'demo':
            return {'ok': True}
        if mode == 'unconfigured':
            raise HTTPException(503, 'Configure authentication first')
        if mode == 'local_password':
            # Slow comparison limits password guessing on a local development server.
            supplied = hashlib.pbkdf2_hmac('sha256', data.password.encode(), b'urjagrid-login-v1', 200000)
            expected = hashlib.pbkdf2_hmac('sha256', cfg['DASHBOARD_PASSWORD'].encode(), b'urjagrid-login-v1', 200000)
            if not hmac.compare_digest(supplied, expected):
                raise HTTPException(401, 'Invalid credentials')
            token = local_session()
            expiry = 3600
        else:
            if data.email.lower() not in {e.strip().lower() for e in cfg['ALLOWED_EMAILS'].split(',')}:
                raise HTTPException(401, 'Invalid credentials')
            try:
                with httpx.Client(timeout=8) as client:
                    result = client.post(cfg['SUPABASE_URL'].rstrip('/') + '/auth/v1/token?grant_type=password',
                        headers={'apikey': cfg['SUPABASE_PUBLISHABLE_KEY']},
                        json={'email': data.email, 'password': data.password})
                if result.status_code != 200:
                    raise HTTPException(401, 'Invalid credentials')
                token = result.json()['access_token']
                expiry = min(int(result.json().get('expires_in', 3600)), 3600)
            except httpx.HTTPError:
                raise HTTPException(503, 'Authentication service unavailable')
        response = JSONResponse({'ok': True})
        response.set_cookie(COOKIE, token, max_age=expiry, httponly=True,
                            secure=hosted or request.url.scheme == 'https', samesite='strict', path='/')
        return response

    @app.post('/api/logout')
    def logout(request: Request):
        check_origin(request)
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE, path='/')
        return response

    @app.post('/api/device/sync')
    def device_sync(data: Telemetry, request: Request):
        if demo:
            raise HTTPException(403, 'Demo never accepts real device telemetry')
        token = cfg.get('DEVICE_TOKEN', '')
        if len(token) < 32 or token.startswith(('REPLACE_', 'YOUR_')):
            raise HTTPException(503, 'Configure a DEVICE_TOKEN of at least 32 characters')
        if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + token):
            raise HTTPException(401, 'Invalid device credential')
        return store().sync(data.model_dump(mode='json'))

    @app.get('/api/live')
    def live(request: Request):
        actor(request)
        demo_tick()
        return store().live()

    @app.post('/api/commands')
    def command(data: CommandRequest, request: Request):
        user = actor(request)
        demo_tick()
        values = data.model_dump(mode='json')
        try:
            return store().request_command(values, user)
        except Conflict as exc:
            store().record_rejection(values, user, str(exc))
            raise

    @app.get('/api/history')
    def history(request: Request, period: str = '24h'):
        actor(request)
        if period not in PERIODS:
            raise HTTPException(400, 'Choose 24h, week or month')
        demo_tick()
        return store().history(period)

    @app.post('/api/notes')
    def note(data: NoteRequest, request: Request):
        user = actor(request)
        try:
            store().note(data.text, user)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {'saved': True}

    @app.post('/api/demo/soc')
    def demo_soc(data: DemoBattery, request: Request):
        actor(request)
        if not demo:
            raise HTTPException(404, 'Simulation is unavailable in live mode')
        with state_lock:
            app.state.demo_soc = data.soc
            app.state.demo_last = 0
            demo_tick()
        return {'simulated_soc': data.soc}

    @app.get('/api/export.csv')
    def export(request: Request, period: str = '24h'):
        actor(request)
        if period not in PERIODS:
            raise HTTPException(400, 'Choose 24h, week or month')
        demo_tick()
        return StreamingResponse(store().export_rows(period), media_type='text/csv',
            headers={'Content-Disposition': 'attachment; filename="urjagrid-history.csv"'})

    return app


app = create_app()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='UrjaGrid Wi-Fi dashboard; no serial port needed')
    parser.add_argument('--demo', action='store_true', help='Isolated synthetic simulation; never controls hardware')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()
    settings = dict(os.environ)
    if args.demo:
        settings['URJAGRID_DEMO'] = '1'
        if args.host != '127.0.0.1':
            parser.error('Unauthenticated demo is loopback-only')
    import uvicorn
    print(f"Dashboard: http://{args.host}:{args.port} ({'SYNTHETIC DEMO' if args.demo else 'LIVE / authenticated'})", flush=True)
    uvicorn.run(create_app(settings), host=args.host, port=args.port, access_log=False)
