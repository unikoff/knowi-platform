"""Disposable PostgreSQL gate: preserve paid data while switching writer modes."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
from threading import Event

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tools.maintenance.role_admin_reconcile import activate_scoped, inventory, quota_snapshot, reconcile, slot_snapshot
from pymentor_core.adapters.sql.role_admin_cutover import require_mode
from pymentor_core.adapters.sql.role_admin_reads import _require_reconciled

_spec = importlib.util.spec_from_file_location('role_migration_cutover', Path(__file__).with_name('test_role_admin_migration.py'))
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)
database = _m.database


def test_cutover_blocks_unmapped_and_drains_legacy_writer(database):
    _m.migrate(database, 'c527_role_admin')
    engine = create_engine(database.set(drivername='postgresql+psycopg'))
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO users(id,username,email,password_hash) VALUES (1,'Student','s@example.test','x'),(2,'Owner','o@example.test','x')"))
            c.execute(text("INSERT INTO booking_slots(id,slot_date,start_time,duration_minutes,student_id) VALUES (10,'2099-11-20','10:00',60,1)"))
            c.execute(text("INSERT INTO booking_quotas(id,user_id,year,month,granted_slots) VALUES (20,1,2099,11,3)"))
            s = c.execute(text('SELECT * FROM booking_slots WHERE id=10')).mappings().one()
            q = c.execute(text('SELECT * FROM booking_quotas WHERE id=20')).mappings().one()
            manifest = {'slots': [{'expected': slot_snapshot(s), 'course_id': 'backend', 'owner_id': 2}],
                        'quotas': [{'expected': quota_snapshot(q), 'course_id': 'backend'}]}
            assert activate_scoped(c, apply=False, confirmed=False, course_enabled=lambda _: True)['reason'] == 'unreconciled_active_data'
            assert not c.execute(text('SELECT scoped_enabled FROM role_admin_cutover WHERE id=1')).scalar_one()
            reconcile(c, manifest, apply=True, course_enabled=lambda x: x == 'backend')
        with engine.begin() as c:
            assert not inventory(c)['mapping_blocked']
            c.execute(text('UPDATE booking_quotas SET granted_slots=0 WHERE id=20'))
            assert inventory(c)['active_insufficient_quota'] == 1
            assert activate_scoped(c, apply=False, confirmed=False, course_enabled=lambda _: True)['status'] == 'blocked'
            c.execute(text('UPDATE booking_quotas SET granted_slots=3 WHERE id=20'))
            c.execute(text('UPDATE booking_slots SET archived_at=now() WHERE id=10'))
            assert inventory(c)['active_archived_bookings'] == 1
            assert activate_scoped(c, apply=False, confirmed=False, course_enabled=lambda _: True)['status'] == 'blocked'
            c.execute(text('UPDATE booking_slots SET archived_at=NULL WHERE id=10'))
            assert activate_scoped(c, apply=False, confirmed=False, course_enabled=lambda _: True)['status'] == 'ready'
            assert activate_scoped(c, apply=True, confirmed=False, course_enabled=lambda _: True)['reason'] == 'legacy_retirement_confirmation_required'
        attempted, complete = Event(), Event()
        def cutover():
            with engine.begin() as c:
                attempted.set()
                result = activate_scoped(c, apply=True, confirmed=True, course_enabled=lambda _: True)
            complete.set()
            return result
        with engine.connect() as c:
            transaction = c.begin()
            require_mode(c, scoped=False)
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(cutover)
                assert attempted.wait(5)
                try:
                    assert not complete.wait(.2)
                finally:
                    transaction.commit()
                assert pending.result(timeout=10)['status'] == 'applied'
        with engine.begin() as c:
            require_mode(c, scoped=True)
            assert inventory(c)['cutover_ready']
            with pytest.raises(HTTPException) as retired:
                require_mode(c, scoped=False)
            assert retired.value.status_code == 503
            assert c.execute(text('SELECT granted_slots FROM booking_quotas WHERE id=20')).scalar_one() == 3
            assert c.execute(text('SELECT student_id FROM booking_slots WHERE id=10')).scalar_one() == 1
            assert c.execute(text('SELECT count(*) FROM booking_history WHERE slot_id=10')).scalar_one() == 1
            # An anonymized former owner's past current-month slot remains a
            # valid consumed booking when its matching history is intact.
            local_now = c.execute(text("SELECT now() AT TIME ZONE 'UTC'+interval '3 hours'")).scalar_one()
            month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if local_now > month_start.replace(hour=1):
                c.execute(text('''INSERT INTO booking_slots(id,course_id,calendar_owner_id,slot_date,start_time,duration_minutes,student_id,archived_at)
                    VALUES (11,'backend',NULL,:day,'00:00',60,1,now())'''), {'day':month_start.date()})
                c.execute(text('''INSERT INTO booking_history(course_id,slot_id,student_id,owner_at_booking_id,status,starts_at,ends_at)
                    VALUES ('backend',11,1,NULL,'completed',(CAST(:day AS date) + time '00:00') AT TIME ZONE 'Europe/Moscow',
                            (CAST(:day AS date) + time '01:00') AT TIME ZONE 'Europe/Moscow')'''),
                    {'day':month_start.date()})
                c.execute(text('''INSERT INTO booking_quotas(course_id,user_id,year,month,granted_slots)
                    VALUES ('backend',1,:year,:month,1)'''), {'year':month_start.year,'month':month_start.month})
                assert inventory(c)['active_archived_bookings'] == 0
                _require_reconciled(c)
        with pytest.raises(DBAPIError):
            with engine.begin() as c:
                c.execute(text('UPDATE role_admin_cutover SET scoped_enabled=false WHERE id=1'))
    finally:
        engine.dispose()
