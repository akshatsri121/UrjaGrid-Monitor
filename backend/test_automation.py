"""Mode commands use temporary archives; these tests never contact a real device."""
import tempfile
import unittest
import csv
import io
import time
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import ValidationError
try:
    from .control_app import create_app
    from .control_models import Telemetry
    from .control_store import SQLiteArchive, Conflict
    from .test_control import sample
except ImportError:
    from control_app import create_app
    from control_models import Telemetry
    from control_store import SQLiteArchive, Conflict
    from test_control import sample


def mode_sample(sequence=1, automatic=True, revision='boot-a', soc=75, voltage=12.6, boot='boot-a'):
    data = sample(sequence, soc=soc, boot=boot)
    data.update(automatic_protection=automatic, mode_revision=revision, battery_voltage=voltage)
    for name in ('bulb', 'fan'):
        data[name]['voltage_v'] = voltage
        data[name]['blocked_reason'] = ('critical_battery_voltage' if automatic and voltage <= 11.8 else
            'soc_below_50_or_recovery_below_55' if automatic and name == 'fan' and soc < 50 else '')
    return data


class AutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'mode.sqlite3'
        self.store = SQLiteArchive(self.path)

    def tearDown(self):
        self.store.engine.dispose()
        self.tmp.cleanup()

    def request(self, load='automation', on=False, now=1001):
        return self.store.request_command(dict(request_id=str(uuid4()), load=load, on=on), 'operator', now)

    def test_mode_is_device_confirmed_not_optimistic_and_persists(self):
        self.store.sync(mode_sample(soc=45), 1000)
        command = self.request()
        self.assertTrue(self.store.live(1002)['latest']['automatic_protection'])
        wire = self.store.sync(mode_sample(2, soc=45), 1005)['commands'][0]
        self.assertEqual(wire['load'], 'automation')
        self.assertEqual(wire['mode_revision'], 'boot-a')
        data = mode_sample(3, automatic=False, revision=command['id'], soc=45)
        data['acks'] = [dict(id=command['id'], status='applied', reason='')]
        self.store.sync(data, 1010)
        live = self.store.live(1011)
        self.assertFalse(live['latest']['automatic_protection'])
        self.assertEqual(live['commands'][0]['status'], 'applied')
        self.assertEqual(self.request('fan', True, 1011)['status'], 'queued')
        reopened = SQLiteArchive(self.path)
        self.assertFalse(reopened.live(1012)['latest']['automatic_protection'])
        reopened.engine.dispose()

    def test_manual_bypasses_battery_rules_not_faults(self):
        data = mode_sample(automatic=False, soc=0, voltage=11.4)
        self.store.sync(data, 1000)
        self.assertEqual(self.request('bulb', True)['status'], 'queued')
        self.assertEqual(self.request('fan', True)['status'], 'queued')
        data = mode_sample(2, automatic=False, soc=0, voltage=11.4)
        data['bulb'] = dict(valid=False, voltage_v=None, current_a=None, power_w=None,
                            on=False, blocked_reason='load_sensor_unavailable')
        self.store.sync(data, 1005)
        with self.assertRaises(Conflict):
            self.request('bulb', True, 1006)
        self.assertEqual(self.request('bulb', False, 1006)['status'], 'queued')
        data['sequence'] = 3
        data['temperature_c'] = None
        self.store.sync(data, 1010)
        with self.assertRaises(Conflict):
            self.request('fan', True, 1011)
        data = mode_sample(4, automatic=False, soc=0, voltage=11.4)
        data['fan']['blocked_reason'] = 'current_trip_send_OFF_to_reset'
        self.store.sync(data, 1015)
        with self.assertRaises(Conflict):
            self.request('fan', True, 1016)

    def test_automatic_server_checks_low_voltage_and_fan_soc(self):
        data = mode_sample(soc=0, voltage=11.4)
        data['bulb']['blocked_reason'] = ''
        self.store.sync(data, 1000)
        with self.assertRaises(Conflict):
            self.request('bulb', True)
        data = mode_sample(2, soc=45)
        data['fan']['blocked_reason'] = ''
        self.store.sync(data, 1005)
        with self.assertRaises(Conflict):
            self.request('fan', True, 1006)

    def test_pending_mode_blocks_on_preserves_off_and_cancels_older_on(self):
        self.store.sync(mode_sample(), 1000)
        old_on = self.request('bulb', True)
        off = self.request('fan', False)
        mode = self.request(now=1002)
        commands = {c['id']: c for c in self.store.live(1003)['commands']}
        self.assertEqual(commands[old_on['id']]['status'], 'cancelled')
        self.assertEqual(commands[off['id']]['status'], 'queued')
        with self.assertRaises(Conflict):
            self.request('fan', True, 1003)
        self.assertEqual(self.request('bulb', False, 1003)['status'], 'queued')
        wire = self.store.sync(mode_sample(2), 1005)['commands']
        self.assertIn(mode['id'], [c['id'] for c in wire])
        self.assertNotIn(old_on['id'], [c['id'] for c in wire])

    def test_mode_ack_must_match_boolean_and_revision(self):
        self.store.sync(mode_sample(), 1000)
        command = self.request()
        data = mode_sample(2, automatic=False)  # Wrong revision despite correct Boolean.
        data['acks'] = [dict(id=command['id'], status='applied', reason='')]
        self.store.sync(data, 1005)
        self.assertEqual(self.store.live(1006)['commands'][0]['status'], 'rejected')
        self.store.sync(mode_sample(3), 1010)
        command = self.request(now=1011)
        data = mode_sample(4, automatic=True, revision=command['id'])  # Wrong Boolean.
        data['acks'] = [dict(id=command['id'], status='applied', reason='')]
        self.store.sync(data, 1015)
        self.assertEqual(self.store.live(1016)['commands'][0]['status'], 'rejected')

    def test_mode_change_cancels_stale_on_and_preserves_off(self):
        self.store.sync(mode_sample(automatic=False), 1000)
        on = self.request('fan', True)
        off = self.request('bulb', False)
        data = mode_sample(2, automatic=True, revision=str(uuid4()))
        wire = self.store.sync(data, 1005)['commands']
        self.assertEqual([c['id'] for c in wire], [off['id']])
        commands = {c['id']: c for c in self.store.live(1006)['commands']}
        self.assertEqual(commands[on['id']]['status'], 'cancelled')

    def test_load_ack_cannot_claim_application_in_different_mode(self):
        self.store.sync(mode_sample(automatic=False), 1000)
        command = self.request('fan', True)
        data = mode_sample(2, automatic=True, revision=str(uuid4()))
        data['fan']['on'] = True
        data['acks'] = [dict(id=command['id'], status='applied', reason='')]
        self.store.sync(data, 1005)
        self.assertEqual(self.store.live(1006)['commands'][0]['status'], 'rejected')

    def test_mode_expiry_reboot_and_legacy_compatibility(self):
        self.store.sync(mode_sample(), 1000)
        self.request()
        self.assertEqual(self.store.live(1017)['commands'][0]['status'], 'expired')
        self.store.sync(mode_sample(2), 1020)
        self.request(now=1021)
        self.store.sync(mode_sample(boot='boot-b', revision='boot-b'), 1025)
        self.assertEqual(self.store.live(1026)['commands'][0]['status'], 'cancelled')
        self.assertTrue(self.store.live(1026)['latest']['automatic_protection'])
        self.store.sync(sample(2, boot='boot-b'), 1030)
        with self.assertRaisesRegex(Conflict, 'Update ESP32'):
            self.request(now=1031)
        self.assertEqual(self.request('bulb', True, 1031)['status'], 'queued')
        with self.assertRaises(Conflict):
            self.request('automation', False, 1060)

    def test_additive_context_table_preserves_existing_archive(self):
        self.store.sync(sample(), 1000)
        self.store.command_context.drop(self.store.engine)
        reopened = SQLiteArchive(self.path)
        self.assertEqual(reopened.history('month', 1001)['samples'], 1)
        self.assertTrue(reopened.live(1001)['online'])
        reopened.engine.dispose()

    def test_mode_schema_and_authenticated_api(self):
        legacy = Telemetry.model_validate(sample())
        self.assertIsNone(legacy.automatic_protection)
        for value in ('false', 0):
            data = mode_sample()
            data['automatic_protection'] = value
            with self.assertRaises(ValidationError):
                Telemetry.model_validate(data)
        data = mode_sample()
        data.pop('mode_revision')
        with self.assertRaises(ValidationError):
            Telemetry.model_validate(data)
        settings = dict(DEVICE_TOKEN='t' * 48, DASHBOARD_PASSWORD='correct-long-password', SESSION_SECRET='s' * 48)
        with TestClient(create_app(settings, self.store)) as client:
            request = dict(request_id=str(uuid4()), load='automation', on=False)
            self.assertEqual(client.post('/api/commands', json=request).status_code, 401)
            self.assertEqual(client.post('/api/login', json={'password': 'correct-long-password'}).status_code, 200)
            headers = {'Authorization': 'Bearer ' + settings['DEVICE_TOKEN']}
            self.assertEqual(client.post('/api/device/sync', json=mode_sample(), headers=headers).status_code, 200)
            self.assertEqual(client.post('/api/commands', json=request,
                headers={'Origin': 'https://evil.example'}).status_code, 403)
            bad = dict(request, on='false')
            self.assertEqual(client.post('/api/commands', json=bad).status_code, 422)
            self.assertEqual(client.post('/api/commands', json=request).status_code, 200)
            self.assertTrue(client.get('/api/live').json()['latest']['automatic_protection'])

    def test_export_preserves_mode_and_leaves_legacy_unknown(self):
        now = time.time()
        self.store.sync(mode_sample(automatic=False), now - 10)
        self.store.sync(sample(2), now - 5)
        rows = list(csv.DictReader(io.StringIO(''.join(self.store.export_rows('24h')))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['automatic_protection'], 'False')
        self.assertEqual(rows[0]['mode_revision'], 'boot-a')
        self.assertEqual(rows[1]['automatic_protection'], '')
        self.assertEqual(rows[1]['mode_revision'], '')

    def test_demo_manual_and_automatic_round_trip(self):
        demo = SQLiteArchive(Path(self.tmp.name) / 'demo.sqlite3', demo=True)
        app = create_app({'URJAGRID_DEMO': '1'}, demo)
        with TestClient(app) as client:
            client.get('/api/live')
            client.post('/api/demo/soc', json={'soc': 45})
            self.assertFalse(client.get('/api/live').json()['latest']['fan']['on'])
            def send(load, on):
                response = client.post('/api/commands', json=dict(request_id=str(uuid4()), load=load, on=on))
                self.assertEqual(response.status_code, 200, response.text)
                app.state.demo_last = 0
                return client.get('/api/live').json()
            live = send('automation', False)
            self.assertFalse(live['latest']['automatic_protection'])
            self.assertEqual(live['commands'][0]['status'], 'applied')
            live = send('fan', True)
            self.assertTrue(live['latest']['fan']['on'])
            live = send('automation', True)
            self.assertTrue(live['latest']['automatic_protection'])
            self.assertFalse(live['latest']['fan']['on'])
            self.assertTrue(live['latest']['fan']['blocked_reason'])
            client.post('/api/demo/soc', json={'soc': 60})
            self.assertFalse(client.get('/api/live').json()['latest']['fan']['on'])
        demo.engine.dispose()


if __name__ == '__main__':
    unittest.main()
