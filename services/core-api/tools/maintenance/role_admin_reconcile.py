"""Inventory and explicitly reconcile confirmed legacy booking scope in the disposable lab."""
from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path

from sqlalchemy import create_engine, text
from pymentor_core.adapters.course_local.runtime import default_runtime
from tools.maintenance.role_admin_bootstrap import allowlisted_url


def inventory(conn):
    row = conn.execute(text("""
        SELECT
          (SELECT count(*) FROM booking_quotas WHERE course_id IS NULL) AS unknown_quotas,
          (SELECT count(*) FROM booking_quotas WHERE course_id IS NULL AND granted_slots>0
            AND (year,month) >= (extract(year FROM (now() AT TIME ZONE 'UTC' + interval '3 hours'))::int,
                                extract(month FROM (now() AT TIME ZONE 'UTC' + interval '3 hours'))::int)) AS active_unknown_quotas,
          (SELECT count(*) FROM booking_slots WHERE course_id IS NULL OR calendar_owner_id IS NULL) AS unknown_slots,
          (SELECT count(*) FROM booking_slots WHERE (course_id IS NULL OR calendar_owner_id IS NULL)
             AND slot_date >= (now() AT TIME ZONE 'UTC' + interval '3 hours')::date AND archived_at IS NULL) AS future_unknown_slots,
          (SELECT count(*) FROM booking_slots WHERE (course_id IS NULL OR calendar_owner_id IS NULL)
             AND student_id IS NOT NULL AND archived_at IS NULL
             AND date_trunc('month', slot_date::timestamp)=date_trunc('month',now() AT TIME ZONE 'UTC' + interval '3 hours')) AS current_month_unknown_bookings,
          (SELECT count(*) FROM booking_slots s WHERE s.student_id IS NOT NULL AND s.archived_at IS NULL
             AND (s.slot_date >= (now() AT TIME ZONE 'UTC' + interval '3 hours')::date
               OR s.slot_date >= date_trunc('month',now() AT TIME ZONE 'UTC' + interval '3 hours')::date)
             AND NOT EXISTS(SELECT 1 FROM booking_history h WHERE h.slot_id=s.id AND h.student_id=s.student_id
               AND h.course_id=s.course_id AND h.status IN ('booked','completed'))) AS active_missing_history,
          (SELECT count(*) FROM booking_slots s WHERE s.student_id IS NOT NULL AND s.archived_at IS NOT NULL
             AND ((s.slot_date+s.start_time::time) > (now() AT TIME ZONE 'UTC'+interval '3 hours')
               OR (s.slot_date >= date_trunc('month',now() AT TIME ZONE 'UTC'+interval '3 hours')::date
                 AND NOT EXISTS(SELECT 1 FROM booking_history h WHERE h.slot_id=s.id AND h.student_id=s.student_id
                   AND h.course_id=s.course_id AND h.status IN ('booked','completed'))))) AS active_archived_bookings,
          (SELECT count(*) FROM (
              SELECT h.course_id,h.student_id,
                extract(year FROM (h.starts_at AT TIME ZONE 'UTC'+interval '3 hours'))::int AS year,
                extract(month FROM (h.starts_at AT TIME ZONE 'UTC'+interval '3 hours'))::int AS month,
                count(*) AS used
              FROM booking_history h WHERE h.status IN ('booked','completed')
                AND (h.starts_at AT TIME ZONE 'UTC'+interval '3 hours') >= date_trunc('month',now() AT TIME ZONE 'UTC'+interval '3 hours')
              GROUP BY h.course_id,h.student_id,year,month
          ) usage LEFT JOIN booking_quotas q ON q.course_id=usage.course_id AND q.user_id=usage.student_id
             AND q.year=usage.year AND q.month=usage.month WHERE q.id IS NULL OR q.granted_slots<usage.used) AS active_insufficient_quota,
          (SELECT count(*) FROM booking_slots WHERE student_id IS NOT NULL AND (course_id IS NULL OR calendar_owner_id IS NULL)) AS unclassified_bookings
    """)).mappings().one()
    result = dict(row)
    result['mapping_blocked'] = bool(result['active_unknown_quotas'] or result['future_unknown_slots']
                                     or result['current_month_unknown_bookings'] or result['active_missing_history']
                                     or result['active_archived_bookings'] or result['active_insufficient_quota'])
    scoped = conn.execute(text('SELECT scoped_enabled FROM role_admin_cutover WHERE id=1')).scalar_one()
    result['writer_cutover_blocked'] = not scoped
    result['cutover_ready'] = bool(scoped and not result['mapping_blocked'])
    return result


def activate_scoped(conn, *, apply: bool, confirmed: bool, course_enabled):
    """Exclusive mode lock drains legacy writes before the final inventory."""
    conn.execute(text("SET LOCAL lock_timeout = '5s'"))
    conn.execute(text("SET LOCAL statement_timeout = '30s'"))
    enabled = conn.execute(text('SELECT scoped_enabled FROM role_admin_cutover WHERE id=1 FOR UPDATE')).scalar_one()
    if enabled:
        return {'status': 'already_applied', 'inventory': inventory(conn)}
    report = inventory(conn)
    if report['mapping_blocked']:
        return {'status': 'blocked', 'reason': 'unreconciled_active_data', 'inventory': report}
    courses = conn.execute(text('''SELECT DISTINCT course_id FROM booking_quotas WHERE course_id IS NOT NULL
        UNION SELECT DISTINCT course_id FROM booking_slots WHERE course_id IS NOT NULL''')).scalars().all()
    if any(not course_enabled(course) for course in courses):
        return {'status': 'blocked', 'reason': 'unknown_or_disabled_course', 'inventory': report}
    if apply and not confirmed:
        return {'status': 'blocked', 'reason': 'legacy_retirement_confirmation_required', 'inventory': report}
    if apply:
        conn.execute(text('UPDATE role_admin_cutover SET scoped_enabled=true,applied_at=clock_timestamp() WHERE id=1'))
    return {'status': 'applied' if apply else 'ready', 'inventory': inventory(conn)}


def slot_snapshot(row):
    return {'id': row['id'], 'slot_date': row['slot_date'].isoformat(), 'start_time': row['start_time'],
            'duration_minutes': row['duration_minutes'], 'student_id': row['student_id'],
            'created_at': row['created_at'].isoformat()}


def quota_snapshot(row):
    return {key: row[key] for key in ('id', 'user_id', 'year', 'month', 'granted_slots')}


def reconcile(conn, manifest, *, apply: bool, course_enabled):
    """Atomically apply an operator-confirmed manifest only when source snapshots still match."""
    enabled = conn.execute(text('SELECT scoped_enabled FROM role_admin_cutover WHERE id=1 FOR SHARE')).scalar_one_or_none()
    if enabled is not False:
        raise ValueError('Reconciliation is unavailable after scoped cutover')
    if set(manifest) != {'slots', 'quotas'} or not all(isinstance(manifest[k], list) for k in manifest):
        raise ValueError('Manifest must contain only slots and quotas arrays')
    for kind in ('slots', 'quotas'):
        ids = [item.get('expected', {}).get('id') for item in manifest[kind]]
        if any(not isinstance(i, int) or i <= 0 for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('Invalid or duplicate source ID')
    conn.execute(text("SET LOCAL lock_timeout = '5s'"))
    conn.execute(text("SET LOCAL statement_timeout = '30s'"))
    # Reconcile only in a maintenance window; lock legacy writers while verifying/applying.
    conn.execute(text('LOCK TABLE booking_slots, booking_quotas IN SHARE ROW EXCLUSIVE MODE'))
    plans = []
    for item in manifest['slots']:
        if set(item) != {'expected', 'course_id', 'owner_id'} or not course_enabled(item['course_id']):
            raise ValueError('Invalid or disabled slot mapping')
        row = conn.execute(text('SELECT * FROM booking_slots WHERE id=:id FOR UPDATE'), {'id': item['expected']['id']}).mappings().first()
        if row is None or slot_snapshot(row) != item['expected']:
            raise ValueError('Slot changed or not found')
        if not isinstance(item['owner_id'], int) or item['owner_id'] <= 0:
            raise ValueError('Explicit owner required')
        if not conn.execute(text('SELECT EXISTS(SELECT 1 FROM users WHERE id=:id)'), {'id': item['owner_id']}).scalar_one():
            raise ValueError('Owner not found')
        if row['course_id'] not in (None, item['course_id']) or row['calendar_owner_id'] not in (None, item['owner_id']):
            raise ValueError('Slot mapping conflicts with existing scope')
        plans.append(('slot', item, row))
    for item in manifest['quotas']:
        if set(item) != {'expected', 'course_id'} or not course_enabled(item['course_id']):
            raise ValueError('Invalid or disabled quota mapping')
        row = conn.execute(text('SELECT * FROM booking_quotas WHERE id=:id FOR UPDATE'), {'id': item['expected']['id']}).mappings().first()
        if row is None or quota_snapshot(row) != item['expected']:
            raise ValueError('Quota changed or not found')
        if row['course_id'] not in (None, item['course_id']):
            raise ValueError('Quota mapping conflicts with existing scope')
        plans.append(('quota', item, row))
    if apply:
        for kind, item, row in plans:
            if kind == 'quota':
                conn.execute(text('UPDATE booking_quotas SET course_id=:course WHERE id=:id'), {'course': item['course_id'], 'id': row['id']})
                continue
            conn.execute(text('UPDATE booking_slots SET course_id=:course, calendar_owner_id=:owner WHERE id=:id'),
                         {'course': item['course_id'], 'owner': item['owner_id'], 'id': row['id']})
            if row['student_id'] is not None:
                hour, minute = map(int, row['start_time'].split(':'))
                start = datetime.combine(row['slot_date'], time(hour, minute), tzinfo=timezone(timedelta(hours=3))).astimezone(timezone.utc)
                conn.execute(text('''INSERT INTO booking_history(slot_id,course_id,student_id,owner_at_booking_id,status,starts_at,ends_at,created_at,legacy_coverage)
                    VALUES (:slot,:course,:student,:owner,'booked',:start,:end,NULL,true)
                    ON CONFLICT (slot_id) WHERE legacy_coverage DO NOTHING'''),
                    {'slot': row['id'], 'course': item['course_id'], 'student': row['student_id'], 'owner': item['owner_id'],
                     'start': start, 'end': start + timedelta(minutes=row['duration_minutes'])})
    return {'status': 'applied' if apply else 'ready', 'slots': len(manifest['slots']), 'quotas': len(manifest['quotas'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--activate-scoped', action='store_true')
    parser.add_argument('--confirm-retire-legacy', action='store_true')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--inventory', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.activate_scoped and (args.inventory or args.manifest is not None):
        parser.error('--activate-scoped is separate from inventory and manifest reconciliation')
    if not args.inventory and not args.activate_scoped and args.manifest is None:
        parser.error('--manifest is required for reconciliation')
    url = allowlisted_url(args.database_url)
    registry = default_runtime().registry
    def enabled(course):
        try:
            registry.require_enabled(course)
            return True
        except Exception:
            return False
    engine = create_engine(url, hide_parameters=True)
    try:
        with engine.begin() as conn:
            database, user, version = conn.execute(text("SELECT current_database(),current_user,current_setting('server_version_num')::int")).one()
            if (database, user) != (url.database, 'pymentor_test') or not 160000 <= version < 170000:
                raise RuntimeError('Connected database is not the allowlisted PostgreSQL 16 test instance')
            if args.activate_scoped:
                result = activate_scoped(conn, apply=args.apply, confirmed=args.confirm_retire_legacy, course_enabled=enabled)
            else:
                result = inventory(conn) if args.inventory else reconcile(conn, json.loads(args.manifest.read_text(encoding='utf-8')), apply=args.apply, course_enabled=enabled)
        print(json.dumps(result, ensure_ascii=False))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
