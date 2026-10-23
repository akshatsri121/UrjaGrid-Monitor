"""Persistent command outbox and archive. SQLite locally, PostgreSQL in hosting.

Commands and acknowledgements share a transaction with telemetry. In-memory DSA
views are reconstructed from committed records, never used as cloud storage.
"""
import csv
import io
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from sqlalchemy import (MetaData, Table, Column, String, Float, Integer, BigInteger,
                        Boolean, JSON, UniqueConstraint, Index, select, insert,
                        update, func, cast, case, create_engine, text)
from sqlalchemy.pool import NullPool
try:
    from .data_structures import RecentEventList, recursive_merge_sort
except ImportError:
    from data_structures import RecentEventList, recursive_merge_sort

PERIODS = {'24h': 86400, 'week': 604800, 'month': 2592000}
METRICS = ('battery_voltage', 'soc_estimate', 'temperature_c', 'bulb_voltage_v',
           'bulb_current_a', 'bulb_power_w', 'fan_voltage_v', 'fan_current_a', 'fan_power_w')
ONLINE_SECONDS = 20
COMMAND_SECONDS = 15
MAX_ENERGY_GAP = 30


class Conflict(ValueError):
    pass


class ArchiveBase(ABC):
    """Inheritance keeps storage replaceable without duplicating control rules."""
    @abstractmethod
    def sync(self, sample, now=None): ...

    @abstractmethod
    def history(self, period, now=None): ...


class SQLArchive(ArchiveBase):
    def __init__(self, url, device_id='urjagrid-01', demo=False):
        self.device_id, self.demo = device_id, demo
        self.postgres = url.startswith('postgresql')
        self.engine = create_engine(url, poolclass=NullPool,
                                    connect_args={'prepare_threshold': None} if self.postgres else {'timeout': 20})
        self.meta = MetaData(schema='urjagrid' if self.postgres else None)
        self.devices = Table('ug_devices', self.meta,
            Column('id', String(64), primary_key=True), Column('boot_id', String(64)),
            Column('sequence', BigInteger), Column('last_seen', Float), Column('latest', JSON))
        self.boots = Table('ug_boots', self.meta,
            Column('device_id', String(64), primary_key=True),
            Column('boot_id', String(64), primary_key=True))
        self.samples = Table('ug_samples', self.meta,
            Column('id', Integer, primary_key=True), Column('device_id', String(64), nullable=False),
            Column('boot_id', String(64), nullable=False), Column('sequence', BigInteger, nullable=False),
            Column('ts', Float, nullable=False), Column('measured_at', Float),
            *[Column(k, Float) for k in METRICS], Column('payload', JSON, nullable=False),
            Column('interval_s', Float, nullable=False, default=0),
            Column('bulb_wh', Float, nullable=False, default=0),
            Column('fan_wh', Float, nullable=False, default=0),
            Column('bulb_coverage_s', Float, nullable=False, default=0),
            Column('fan_coverage_s', Float, nullable=False, default=0),
            UniqueConstraint('device_id', 'boot_id', 'sequence'))
        Index('ug_samples_device_time', self.samples.c.device_id, self.samples.c.ts)
        self.commands = Table('ug_commands', self.meta,
            Column('id', String(36), primary_key=True), Column('device_id', String(64), nullable=False),
            Column('boot_id', String(64), nullable=False), Column('load', String(10), nullable=False),
            Column('on', Boolean, nullable=False), Column('status', String(20), nullable=False),
            Column('created_at', Float, nullable=False), Column('expires_at', Float, nullable=False),
            Column('updated_at', Float, nullable=False), Column('actor', String(254), nullable=False),
            Column('reason', String(200), nullable=False, default=''))
        Index('ug_commands_device_time', self.commands.c.device_id, self.commands.c.created_at)
        # Additive table: existing SQLite/Postgres archives need no column rewrite.
        # Bind new commands to the device-confirmed mode at request time.
        self.command_context = Table('ug_command_context', self.meta,
            Column('command_id', String(36), primary_key=True),
            Column('mode_revision', String(64), nullable=False))
        self.activity = Table('ug_activity', self.meta,
            Column('id', Integer, primary_key=True), Column('device_id', String(64), nullable=False),
            Column('ts', Float, nullable=False), Column('kind', String(32), nullable=False),
            Column('message', String(2200), nullable=False))
        Index('ug_activity_device_time', self.activity.c.device_id, self.activity.c.ts)
        if self.postgres:
            with self.engine.begin() as conn:
                conn.execute(text('CREATE SCHEMA IF NOT EXISTS urjagrid'))
        self.meta.create_all(self.engine)
        # Keep these tables outside Supabase's default exposed public schema.
        with self.transaction() as conn:
            self._ensure_device(conn)

    @contextmanager
    def transaction(self):
        with self.engine.connect() as conn:
            if not self.postgres:
                conn.exec_driver_sql('BEGIN IMMEDIATE')
            else:
                conn.begin()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def _ensure_device(self, conn):
        if self.postgres:
            from sqlalchemy.dialects.postgresql import insert as upsert
        else:
            from sqlalchemy.dialects.sqlite import insert as upsert
        conn.execute(upsert(self.devices).values(id=self.device_id).on_conflict_do_nothing())

    def _device(self, conn):
        return conn.execute(select(self.devices).where(self.devices.c.id == self.device_id)
                            .with_for_update()).mappings().one()

    def _event(self, conn, now, kind, message):
        conn.execute(insert(self.activity).values(device_id=self.device_id, ts=now,
                                                  kind=kind, message=message))

    def _expire(self, conn, now):
        pending = self.commands.c.status.in_(['queued', 'delivered'])
        conn.execute(update(self.commands).where(self.commands.c.device_id == self.device_id,
            pending, self.commands.c.expires_at <= now).values(status='expired', updated_at=now,
                                                            reason='No timely acknowledgement'))

    def _apply_acks(self, conn, sample, now):
        for ack in sample['acks']:
            command = conn.execute(select(self.commands).where(self.commands.c.id == str(ack['id']),
                self.commands.c.device_id == self.device_id,
                self.commands.c.boot_id == sample['boot_id'])).mappings().first()
            if not command or command['status'] not in ('queued', 'delivered'):
                continue
            # Acknowledged application must agree with this device-reported state.
            status, reason = ack['status'], ack['reason']
            if status == 'applied':
                if command['load'] == 'automation':
                    matches = (sample.get('automatic_protection') is command['on']
                               and sample.get('mode_revision') == command['id'])
                else:
                    matches = sample[command['load']]['on'] == command['on']
                    context = conn.execute(select(self.command_context.c.mode_revision).where(
                        self.command_context.c.command_id == command['id'])).scalar_one_or_none()
                    if command['on'] and context is not None:
                        matches = matches and context == sample.get('mode_revision')
                if not matches:
                    status, reason = 'rejected', 'Reported state or mode differs from request'
            conn.execute(update(self.commands).where(self.commands.c.id == command['id'])
                         .values(status=status, reason=reason, updated_at=now))
            self._event(conn, now, 'command', f"{command['load']} {'ON' if command['on'] else 'OFF'}: {status} {reason}")

    def sync(self, sample, now=None):
        now = time.time() if now is None else now
        if sample['device_id'] != self.device_id:
            raise Conflict('Unknown device')
        with self.transaction() as conn:
            device = self._device(conn)
            self._expire(conn, now)
            known_boot = conn.execute(select(self.boots).where(self.boots.c.device_id == self.device_id,
                                      self.boots.c.boot_id == sample['boot_id'])).first()
            if device['boot_id'] != sample['boot_id']:
                if known_boot:
                    raise Conflict('Stale boot session; restart device')
                conn.execute(insert(self.boots).values(device_id=self.device_id, boot_id=sample['boot_id']))
                conn.execute(update(self.commands).where(self.commands.c.device_id == self.device_id,
                    self.commands.c.status.in_(['queued', 'delivered']))
                    .values(status='cancelled', reason='Device restarted; fresh request required', updated_at=now))
                self._event(conn, now, 'restart', 'Device booted; previous pending commands cancelled')
            elif sample['sequence'] <= device['sequence']:
                # Retries acknowledge an existing sample but never refresh online status,
                # overwrite the latest state or deliver a new command on stale telemetry.
                exists = conn.execute(select(self.samples.c.id).where(self.samples.c.device_id == self.device_id,
                    self.samples.c.boot_id == sample['boot_id'], self.samples.c.sequence == sample['sequence'])).first()
                if exists:
                    return {'accepted': True, 'duplicate': True, 'commands': []}
                raise Conflict('Out-of-order telemetry')
            self._apply_acks(conn, sample, now)
            if sample.get('mode_revision') is not None:
                stale_contexts = select(self.command_context.c.command_id).where(
                    self.command_context.c.mode_revision != sample['mode_revision'])
                conn.execute(update(self.commands).where(self.commands.c.device_id == self.device_id,
                    self.commands.c.status.in_(['queued', 'delivered']),
                    self.commands.c.id.in_(stale_contexts),
                    (self.commands.c.on.is_(True) | (self.commands.c.load == 'automation')))
                    .values(status='cancelled', reason='Control mode changed; fresh request required', updated_at=now))
            values = {key: sample.get(key) for key in ('battery_voltage', 'soc_estimate', 'temperature_c')}
            for load in ('bulb', 'fan'):
                for suffix in ('voltage_v', 'current_a', 'power_w'):
                    values[f'{load}_{suffix}'] = sample[load][suffix]
            dt = now - device['last_seen'] if device['last_seen'] is not None else 0
            valid_interval = device['boot_id'] == sample['boot_id'] and 0 < dt <= MAX_ENERGY_GAP
            interval = dt if valid_interval else 0
            previous = device['latest'] or {}
            if sample.get('automatic_protection') is not None and (
                    previous.get('automatic_protection') != sample['automatic_protection']):
                state = 'ON (automatic)' if sample['automatic_protection'] else 'OFF (manual battery override)'
                self._event(conn, now, 'automation', 'Device confirmed automatic protection ' + state)
            for load in ('bulb', 'fan'):
                before = previous.get(load, {})
                good = valid_interval and before.get('valid') and sample[load]['valid']
                # Negative current is not silently converted to consumption.
                good = good and before['power_w'] >= 0 and sample[load]['power_w'] >= 0
                values[load + '_wh'] = (before['power_w'] + sample[load]['power_w']) / 2 * dt / 3600 if good else 0
                values[load + '_coverage_s'] = dt if good else 0
                if previous and (before.get('on'), before.get('blocked_reason')) != (sample[load]['on'], sample[load]['blocked_reason']):
                    self._event(conn, now, 'device_state', f"{load}: {'ON' if sample[load]['on'] else 'OFF'}; {sample[load]['blocked_reason'] or 'no lockout'}")
            saved = dict(sample, acks=[], received_at=now, source='demo' if self.demo else 'device')
            conn.execute(insert(self.samples).values(device_id=self.device_id, boot_id=sample['boot_id'],
                sequence=sample['sequence'], ts=now, measured_at=sample.get('measured_at'), payload=saved,
                interval_s=interval, **values))
            conn.execute(update(self.devices).where(self.devices.c.id == self.device_id).values(
                boot_id=sample['boot_id'], sequence=sample['sequence'], last_seen=now, latest=saved))
            queued = conn.execute(select(self.commands).where(self.commands.c.device_id == self.device_id,
                self.commands.c.boot_id == sample['boot_id'], self.commands.c.status.in_(['queued', 'delivered']),
                self.commands.c.expires_at > now).order_by(self.commands.c.created_at)).mappings().all()
            for command in queued:
                conn.execute(update(self.commands).where(self.commands.c.id == command['id'])
                             .values(status='delivered', updated_at=now))
            wire_commands = []
            for command in queued:
                item = dict(id=command['id'], load=command['load'], on=command['on'],
                            ttl_ms=int((command['expires_at'] - now) * 1000))
                revision = conn.execute(select(self.command_context.c.mode_revision).where(
                    self.command_context.c.command_id == command['id'])).scalar_one_or_none()
                if revision is not None:
                    item['mode_revision'] = revision
                wire_commands.append(item)
            return {'accepted': True, 'duplicate': False, 'commands': wire_commands}

    def request_command(self, request, actor, now=None):
        now = time.time() if now is None else now
        request_id = str(request['request_id'])
        with self.transaction() as conn:
            device = self._device(conn)
            self._expire(conn, now)
            existing = conn.execute(select(self.commands).where(self.commands.c.id == request_id)).mappings().first()
            if existing:
                if existing['device_id'] != self.device_id or existing['load'] != request['load'] or existing['on'] != request['on']:
                    raise Conflict('Request ID reused with different content')
                return dict(existing)
            if device['last_seen'] is None or now - device['last_seen'] > ONLINE_SECONDS:
                raise Conflict('Device offline; no command queued')
            latest = device['latest']
            automatic = latest.get('automatic_protection') is not False
            pending_mode = conn.execute(select(self.commands.c.id).where(
                self.commands.c.device_id == self.device_id, self.commands.c.load == 'automation',
                self.commands.c.status.in_(['queued', 'delivered']))).first()
            if request['load'] == 'automation':
                if latest.get('automatic_protection') is None or not latest.get('mode_revision'):
                    raise Conflict('Update ESP32 firmware to support the automation switch')
                # A mode change cannot revive an earlier ON. OFF requests are retained.
                conn.execute(update(self.commands).where(self.commands.c.device_id == self.device_id,
                    self.commands.c.load.in_(['bulb', 'fan']), self.commands.c.on.is_(True),
                    self.commands.c.status.in_(['queued', 'delivered']))
                    .values(status='cancelled', reason='Mode change requested; fresh ON required', updated_at=now))
            else:
                branch = latest[request['load']]
                if request['on'] and pending_mode:
                    raise Conflict('Wait for the ESP32 to confirm the requested control mode')
                if request['on'] and (not branch['valid'] or latest['battery_voltage'] is None
                                      or latest['temperature_c'] is None):
                    raise Conflict('ON blocked: required sensor data unavailable')
                if request['on'] and branch['blocked_reason']:
                    raise Conflict('ON blocked: ' + branch['blocked_reason'])
                if request['on'] and automatic and latest.get('automatic_protection') is True and latest['battery_voltage'] <= 11.8:
                    raise Conflict('ON blocked: critical battery voltage')
                if request['on'] and automatic and request['load'] == 'fan' and (
                    latest['soc_estimate'] is None or latest['soc_estimate'] < 50):
                    raise Conflict('ON blocked: battery estimate unavailable or below 50%')
            conn.execute(update(self.commands).where(self.commands.c.device_id == self.device_id,
                self.commands.c.load == request['load'], self.commands.c.status.in_(['queued', 'delivered']))
                .values(status='cancelled', reason='Superseded by a newer request', updated_at=now))
            command = dict(id=request_id, device_id=self.device_id, boot_id=device['boot_id'],
                load=request['load'], on=request['on'], status='queued', created_at=now,
                expires_at=now + COMMAND_SECONDS, updated_at=now, actor=actor, reason='')
            conn.execute(insert(self.commands).values(**command))
            if latest.get('mode_revision'):
                conn.execute(insert(self.command_context).values(command_id=request_id,
                                                                  mode_revision=latest['mode_revision']))
            self._event(conn, now, 'request', f"{actor} requested {request['load']} {'ON' if request['on'] else 'OFF'}")
            return command

    def live(self, now=None):
        now = time.time() if now is None else now
        with self.transaction() as conn:
            device = self._device(conn)
            self._expire(conn, now)
            commands = conn.execute(select(self.commands).where(self.commands.c.device_id == self.device_id)
                .order_by(self.commands.c.created_at.desc()).limit(12)).mappings().all()
            return dict(mode='demo' if self.demo else 'live', device_id=self.device_id,
                online=device['last_seen'] is not None and now - device['last_seen'] <= ONLINE_SECONDS,
                last_seen=device['last_seen'], latest=device['latest'], commands=[dict(c) for c in commands])

    def note(self, note, actor):
        note = note.strip()
        if not note:
            raise ValueError('Note cannot be blank')
        with self.transaction() as conn:
            self._event(conn, time.time(), 'note', f'{actor}: {note}')

    def record_rejection(self, request, actor, reason):
        """Audit a rejected API request without ever putting it in the outbox."""
        now = time.time()
        with self.transaction() as conn:
            device = self._device(conn)
            exists = conn.execute(select(self.commands.c.id).where(self.commands.c.id == str(request['request_id']))).first()
            if not exists:
                conn.execute(insert(self.commands).values(id=str(request['request_id']), device_id=self.device_id,
                    boot_id=device['boot_id'] or 'not-started', load=request['load'], on=request['on'],
                    status='rejected', created_at=now, expires_at=now, updated_at=now, actor=actor, reason=reason[:200]))
            self._event(conn, now, 'request_rejected', f"{actor}: {request['load']} {'ON' if request['on'] else 'OFF'} rejected: {reason}")

    def history(self, period, now=None):
        end = time.time() if now is None else now
        start, width = end - PERIODS[period], PERIODS[period] / 720
        s = self.samples.c
        filt = (s.device_id == self.device_id, s.ts >= start, s.ts <= end)
        bucket = case((s.ts >= end, 719), else_=cast((s.ts - start) / width, Integer))
        averages = [func.avg(s[k]).label(k) for k in METRICS]
        # Stored interval integrals avoid loading a month of raw rows on each poll.
        # Clip a boundary-crossing interval rather than counting pre-window energy.
        fraction = case((s.interval_s <= 0, 0.0),
                        (s.ts - s.interval_s < start, (s.ts - start) / s.interval_s), else_=1.0)
        integrals = []
        for load in ('bulb', 'fan'):
            # Preserve the linear trapezoid shape when the first interval crosses
            # the requested boundary, rather than proportionally scaling energy.
            energy = s[load + '_wh']
            partial = energy * fraction + (s[load + '_power_w'] * s.interval_s / 3600 - energy) * fraction * (1 - fraction)
            integrals.append(func.sum(case((s[load + '_coverage_s'] > 0, partial), else_=0)).label(load + '_wh'))
            integrals.append(func.sum(s[load + '_coverage_s'] * fraction).label(load + '_coverage_s'))
        with self.engine.connect() as conn:
            grouped = conn.execute(select(bucket.label('bucket'), func.count().label('count'), *averages)
                .where(*filt).group_by(bucket).order_by(bucket)).mappings().all()
            totals = conn.execute(select(func.count().label('samples'),
                *integrals,
                func.max(s.temperature_c).label('maximum_temperature_c'),
                func.max(s.bulb_power_w).label('peak_bulb_w'), func.max(s.fan_power_w).label('peak_fan_w'))
                .where(*filt)).mappings().one()
            events = conn.execute(select(self.activity).where(self.activity.c.device_id == self.device_id,
                self.activity.c.ts >= start, self.activity.c.ts <= end)
                .order_by(self.activity.c.id.desc()).limit(200)).mappings().all()
            recent = conn.execute(select(self.activity).where(self.activity.c.device_id == self.device_id)
                .order_by(self.activity.c.id.desc()).limit(200)).mappings().all()
        linked = RecentEventList(200)
        for event in reversed(recent):
            linked.append(dict(event))
        notes = [dict(e, category='note') for e in events if e['kind'] == 'note']
        activity = [dict(e, category='event') for e in events if e['kind'] != 'note'] + notes
        activity = recursive_merge_sort(activity, key=lambda r: (r['ts'], r['id']), reverse=True)
        points = [dict(ts=start + min(r['bucket'] + .5, 720) * width,
                       **{k: v for k, v in r.items() if k != 'bucket'}) for r in grouped]
        summary = {k: totals[k] or 0 for k in ('bulb_wh', 'fan_wh')}
        summary.update(load_wh=summary['bulb_wh'] + summary['fan_wh'],
            bulb_coverage_percent=(totals['bulb_coverage_s'] or 0) / PERIODS[period] * 100,
            fan_coverage_percent=(totals['fan_coverage_s'] or 0) / PERIODS[period] * 100,
            maximum_temperature_c=totals['maximum_temperature_c'],
            peak_bulb_w=totals['peak_bulb_w'], peak_fan_w=totals['peak_fan_w'])
        return dict(period=period, start=start, end=end, samples=totals['samples'],
                    points=points, summary=summary, activity=activity, recent_events=linked.snapshot()[::-1])

    def export_rows(self, period):
        end = time.time()
        with self.engine.connect() as conn:
            result = conn.execution_options(stream_results=True).execute(select(self.samples).where(
                self.samples.c.device_id == self.device_id, self.samples.c.ts >= end - PERIODS[period],
                self.samples.c.ts <= end).order_by(self.samples.c.ts))
            keys = ('timestamp_utc', *METRICS, 'bulb_on', 'fan_on', 'bulb_valid', 'fan_valid',
                    'soc_source', 'source', 'automatic_protection', 'mode_revision')
            out = io.StringIO()
            writer = csv.writer(out)
            writer.writerow(keys)
            yield out.getvalue()
            for row in result.mappings():
                out.seek(0)
                out.truncate(0)
                p = row['payload']
                writer.writerow([datetime.fromtimestamp(row['ts'], timezone.utc).isoformat(),
                    *[row[k] for k in METRICS], p['bulb']['on'], p['fan']['on'],
                    p['bulb']['valid'], p['fan']['valid'], p['soc_source'], p['source'],
                    p.get('automatic_protection'), p.get('mode_revision')])
                yield out.getvalue()


class SQLiteArchive(SQLArchive):
    """Local durable archive; deliberately separate from the legacy serial DB."""
    def __init__(self, path, **kwargs):
        super().__init__('sqlite:///' + str(path).replace('\\', '/'), **kwargs)


class PostgresArchive(SQLArchive):
    """Same protocol and policy checks, durable across serverless instances."""
    def __init__(self, url, **kwargs):
        if not url.startswith(('postgresql://', 'postgresql+psycopg://')):
            raise ValueError('PostgreSQL URL required')
        url = url.replace('postgresql://', 'postgresql+psycopg://', 1)
        super().__init__(url, **kwargs)
