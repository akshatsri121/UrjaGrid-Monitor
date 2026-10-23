"""Integration tests use temporary databases and simulated telemetry, never USB."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch, MagicMock
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy import select, func
try:
    from .control_app import create_app
    from .control_store import SQLiteArchive, Conflict
    from .control_models import Telemetry
except ImportError:
    from control_app import create_app
    from control_store import SQLiteArchive, Conflict
    from control_models import Telemetry


def sample(sequence=1, soc=75, boot='boot-a'):
    return dict(protocol=1, device_id='urjagrid-01', boot_id=boot, sequence=sequence,
        uptime_ms=sequence * 5000, measured_at=None, battery_voltage=12.6,
        soc_estimate=soc, soc_source='generic_vrla_voltage_v1', temperature_c=27,
        bulb=dict(valid=True, voltage_v=12.6, current_a=.75, power_w=9.45, on=False, blocked_reason=''),
        fan=dict(valid=True, voltage_v=12.6, current_a=.1, power_w=1.26, on=False,
                 blocked_reason='soc_below_50_or_recovery_below_55' if soc < 50 else ''), acks=[])


class ControlStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteArchive(Path(self.tmp.name) / 'test.sqlite3')

    def tearDown(self):
        self.store.engine.dispose()
        self.tmp.cleanup()

    def request(self, load='fan', on=True, now=1001):
        return self.store.request_command(dict(request_id=str(uuid4()), load=load, on=on), 'operator', now)

    def test_telemetry_persistence_and_duplicate_does_not_refresh_online(self):
        self.store.sync(sample(), 1000)
        self.assertTrue(self.store.sync(sample(), 1005)['duplicate'])
        self.assertFalse(self.store.live(1021)['online'])
        with self.store.engine.connect() as conn:
            self.assertEqual(conn.execute(select(func.count()).select_from(self.store.samples)).scalar_one(), 1)
        reopened = SQLiteArchive(Path(self.tmp.name) / 'test.sqlite3')
        self.assertEqual(reopened.live(1001)['latest']['soc_estimate'], 75)
        reopened.engine.dispose()

    def test_command_delivery_ack_and_idempotence(self):
        self.store.sync(sample(), 1000)
        command = self.request()
        repeated = self.store.request_command(dict(request_id=command['id'], load='fan', on=True), 'operator', 1002)
        self.assertEqual(repeated['id'], command['id'])
        reply = self.store.sync(sample(2), 1005)
        self.assertEqual(reply['commands'][0]['id'], command['id'])
        data = sample(3)
        data['fan']['on'] = True
        data['acks'] = [dict(id=command['id'], status='applied', reason='')]
        self.store.sync(data, 1010)
        self.assertEqual(self.store.live(1011)['commands'][0]['status'], 'applied')
        self.assertEqual(self.store.sync(sample(4), 1012)['commands'], [])

    def test_rejected_ack_and_expiry(self):
        self.store.sync(sample(), 1000)
        c = self.request()
        data = sample(2, soc=49)
        data['acks'] = [dict(id=c['id'], status='rejected', reason='soc_below_50')]
        self.store.sync(data, 1005)
        self.assertEqual(self.store.live(1005)['commands'][0]['status'], 'rejected')
        self.store.sync(sample(3), 1006)
        self.request(now=1007)
        self.assertEqual(self.store.live(1023)['commands'][0]['status'], 'expired')

    def test_fan_rule_missing_estimate_offline_and_bulb_independence(self):
        self.store.sync(sample(soc=49), 1000)
        with self.assertRaises(Conflict): self.request()
        self.assertEqual(self.request(load='bulb')['status'], 'queued')
        self.assertEqual(self.request(on=False)['status'], 'queued')
        with self.assertRaises(Conflict): self.request(load='bulb', now=1030)
        data = sample(2)
        data['battery_voltage'] = data['soc_estimate'] = None
        self.store.sync(data, 1031)
        with self.assertRaises(Conflict): self.request(now=1032)

    def test_reboot_cancels_commands_and_old_session_cannot_return(self):
        self.store.sync(sample(), 1000)
        self.request()
        self.store.sync(sample(1, boot='boot-b'), 1005)
        self.assertEqual(self.store.live(1006)['commands'][0]['status'], 'cancelled')
        with self.assertRaises(Conflict): self.store.sync(sample(2, boot='boot-a'), 1007)

    def test_superseding_and_conflicting_request_id(self):
        self.store.sync(sample(), 1000)
        first = self.request()
        self.request(on=False, now=1002)
        self.assertEqual(self.store.live(1003)['commands'][1]['status'], 'cancelled')
        with self.assertRaises(Conflict):
            self.store.request_command(dict(request_id=first['id'], load='bulb', on=True), 'operator', 1003)

    def test_ack_must_match_reported_state(self):
        self.store.sync(sample(), 1000)
        c = self.request()
        data = sample(2)
        data['acks'] = [dict(id=c['id'], status='applied', reason='')]
        self.store.sync(data, 1005)
        self.assertEqual(self.store.live(1006)['commands'][0]['status'], 'rejected')

    def test_energy_gaps_nulls_boundaries_and_buckets(self):
        self.store.sync(sample(1), 1000)
        self.store.sync(sample(2), 1010)
        self.store.sync(sample(3), 1020)
        self.store.sync(sample(4), 1100)  # Excluded long gap.
        summary = self.store.history('24h', 1101)['summary']
        self.assertAlmostEqual(summary['bulb_wh'], 9.45 * 20 / 3600)
        self.assertAlmostEqual(summary['fan_wh'], 1.26 * 20 / 3600)
        # Only five seconds of the first interval remain inside this window.
        boundary = self.store.history('24h', 1005 + 86400)['summary']
        self.assertAlmostEqual(boundary['bulb_wh'], 9.45 * 15 / 3600)
        data = sample(5)
        data['fan'] = dict(valid=False, voltage_v=None, current_a=None, power_w=None, on=False, blocked_reason='missing')
        self.store.sync(data, 1105)
        # A bucket can contain both good and missing samples; averages use only
        # available values, while the live payload preserves the missing value.
        self.assertIsNone(self.store.live(1106)['latest']['fan']['power_w'])
        self.assertAlmostEqual(self.store.history('24h', 1106)['summary']['fan_wh'], 1.26 * 20 / 3600)

    def test_events_notes_dsa_rebuilt_and_concurrency(self):
        self.store.sync(sample(), 1000)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: self.store.note('Note ' + str(i), 'operator'), range(12)))
        history = self.store.history('month')
        self.assertEqual(len(history['activity']), 12)
        self.assertEqual(len(history['recent_events']), 13)
        self.assertEqual(history['activity'], sorted(history['activity'], key=lambda e: (e['ts'], e['id']), reverse=True))
        reopened = SQLiteArchive(Path(self.tmp.name) / 'test.sqlite3')
        self.assertEqual(reopened.history('month')['recent_events'], history['recent_events'])
        reopened.engine.dispose()

    def test_boundary_integration_preserves_linear_power_ramp(self):
        a, b = sample(), sample(2)
        a['bulb']['power_w'] = 0
        b['bulb']['power_w'] = 10
        self.store.sync(a, 1000)
        self.store.sync(b, 1010)
        result = self.store.history('24h', 1005 + 86400)
        self.assertAlmostEqual(result['summary']['bulb_wh'], 37.5 / 3600)


class ControlAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteArchive(Path(self.tmp.name) / 'live.sqlite3')
        self.settings = dict(DEVICE_TOKEN='t' * 48, DASHBOARD_PASSWORD='correct-long-password',
                             SESSION_SECRET='s' * 48)
        self.client = TestClient(create_app(self.settings, self.store))

    def tearDown(self):
        self.client.close()
        self.store.engine.dispose()
        self.tmp.cleanup()

    def login(self):
        response = self.client.post('/api/login', json={'password': 'correct-long-password'})
        self.assertEqual(response.status_code, 200, response.text)

    def test_fail_closed_login_cookies_and_csrf(self):
        self.assertEqual(self.client.get('/api/live').status_code, 401)
        self.assertEqual(self.client.post('/api/login', json={'password': 'wrong'}).status_code, 401)
        self.login()
        self.assertEqual(self.client.get('/api/live').status_code, 200)
        response = self.client.post('/api/notes', json={'text': 'bad'}, headers={'Origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.post('/api/notes', json={'text': 'good'}).status_code, 200)
        self.client.post('/api/logout')
        self.assertEqual(self.client.get('/api/live').status_code, 401)

    def test_device_auth_schema_and_dashboard_does_not_authorize_device(self):
        self.login()
        self.assertEqual(self.client.post('/api/device/sync', json=sample()).status_code, 401)
        headers = {'Authorization': 'Bearer ' + self.settings['DEVICE_TOKEN']}
        response = self.client.post('/api/device/sync', json=sample(), headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        invalid = sample(2); invalid['fan']['valid'] = False
        self.assertEqual(self.client.post('/api/device/sync', json=invalid, headers=headers).status_code, 422)
        invalid = sample(2); invalid['soc_estimate'] = 101
        self.assertEqual(self.client.post('/api/device/sync', json=invalid, headers=headers).status_code, 422)
        self.assertEqual(self.client.post('/api/demo/soc', json={'soc': 45}).status_code, 404)

    def test_body_cap_assets_export_and_notes(self):
        self.assertEqual(self.client.post('/api/device/sync', content=b'x' * 33000).status_code, 413)
        self.login()
        self.assertEqual(self.client.get('/api/history?period=bad').status_code, 400)
        self.assertEqual(self.client.post('/api/notes', json={'text': '  '}).status_code, 400)
        self.assertIn('UrjaGrid', self.client.get('/').text)
        self.assertEqual(self.client.get('/assets/control.js').status_code, 200)
        self.assertIn("script-src 'self'", self.client.get('/').headers['content-security-policy'])
        self.assertEqual(self.client.get('/assets/nope').status_code, 404)
        self.assertIn('bulb_power_w', self.client.get('/api/export.csv').text)

    def test_unconfigured_and_hosted_password_auth_disabled(self):
        for settings in ({}, dict(self.settings, VERCEL='1')):
            client = TestClient(create_app(settings, self.store))
            self.assertEqual(client.get('/api/config').json()['auth'], 'unconfigured')
            self.assertEqual(client.get('/api/live').status_code, 503)
            client.close()

    def test_nonfinite_values_strict_booleans_and_validation_redaction(self):
        headers = {'Authorization': 'Bearer ' + self.settings['DEVICE_TOKEN'], 'Content-Type': 'application/json'}
        data = sample()
        data['temperature_c'] = float('nan')
        response = self.client.post('/api/device/sync', content=json.dumps(data), headers=headers)
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('NaN', response.text)
        data = sample(); data['fan']['on'] = 'false'
        self.assertEqual(self.client.post('/api/device/sync', json=data, headers=headers).status_code, 422)
        private_input = 'private-password-must-not-be-reflected' * 20
        response = self.client.post('/api/login', json={'password': private_input})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn('private-password', response.text)

    def test_api_rejection_is_audited_and_never_delivered(self):
        self.login()
        self.client.post('/api/device/sync', json=sample(soc=45),
                         headers={'Authorization': 'Bearer ' + self.settings['DEVICE_TOKEN']})
        response = self.client.post('/api/commands', json=dict(request_id=str(uuid4()), load='fan', on=True))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.client.get('/api/live').json()['commands'][0]['status'], 'rejected')
        self.assertEqual(self.client.get('/api/history').json()['activity'][0]['kind'], 'request_rejected')

    def test_supabase_allowlist_and_secure_cookie(self):
        cfg = dict(VERCEL='1', APP_ORIGIN='https://testserver', SUPABASE_URL='https://example.supabase.co',
                   SUPABASE_PUBLISHABLE_KEY='public-key', ALLOWED_EMAILS='owner@example.com')
        client = TestClient(create_app(cfg, self.store), base_url='https://testserver')
        import_path = create_app.__module__ + '.httpx.Client'
        with patch(import_path) as mocked:
            remote = mocked.return_value.__enter__.return_value
            remote.post.return_value = MagicMock(status_code=200)
            remote.post.return_value.json.return_value = {'access_token': 'verified-access-token', 'expires_in': 3600}
            response = client.post('/api/login', json={'email': 'owner@example.com', 'password': 'secret'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('Secure', response.headers['set-cookie'])
            self.assertIn('HttpOnly', response.headers['set-cookie'])
            remote.get.return_value = MagicMock(status_code=200)
            remote.get.return_value.json.return_value = {'email': 'owner@example.com'}
            self.assertEqual(client.get('/api/live').status_code, 200)
            remote.get.return_value.json.return_value = {'email': 'stranger@example.com'}
            self.assertEqual(client.get('/api/live').status_code, 403)
        client.close()

    def test_demo_isolation_history_and_hysteresis(self):
        demo_store = SQLiteArchive(Path(self.tmp.name) / 'demo.sqlite3', demo=True)
        client = TestClient(create_app({'URJAGRID_DEMO': '1'}, demo_store))
        self.assertEqual(client.post('/api/device/sync', json=sample()).status_code, 403)
        self.assertEqual(client.get('/api/live').json()['mode'], 'demo')
        history = client.get('/api/history?period=month').json()
        self.assertGreater(history['samples'], 8000)
        self.assertLessEqual(len(history['points']), 721)
        client.post('/api/demo/soc', json={'soc': 45})
        live = client.get('/api/live').json()
        self.assertFalse(live['latest']['fan']['on'])
        self.assertTrue(live['latest']['fan']['blocked_reason'])
        client.post('/api/demo/soc', json={'soc': 52})
        self.assertTrue(client.get('/api/live').json()['latest']['fan']['blocked_reason'])
        client.post('/api/demo/soc', json={'soc': 60})
        live = client.get('/api/live').json()
        self.assertFalse(live['latest']['fan']['on'])
        self.assertEqual(live['latest']['fan']['blocked_reason'], '')
        request = dict(request_id=str(uuid4()), load='fan', on=True)
        self.assertEqual(client.post('/api/commands', json=request).status_code, 200)
        client.post('/api/demo/soc', json={'soc': 60})
        live = client.get('/api/live').json()
        self.assertEqual(live['commands'][0]['status'], 'applied')
        self.assertTrue(live['latest']['fan']['on'])
        self.assertIsNone(self.store.live()['latest'])
        client.close(); demo_store.engine.dispose()


if __name__ == '__main__':
    unittest.main()
