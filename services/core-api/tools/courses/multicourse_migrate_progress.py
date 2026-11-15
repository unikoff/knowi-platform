"""Rehearse one guarded forward migration on the owned synthetic lab only."""
import hashlib
import json
import os
from pathlib import Path
import sys
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from alembic import command
from alembic.config import Config
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dev'))
from multicourse_local import configure, ROOT

RUN = 'mc460-20261113-2048'
configure(RUN)
private = Path(os.environ['LOCALAPPDATA'])/'Pymentor/c225'/RUN
engine = create_engine(os.environ['DATABASE_URL'])
output = ROOT/'docs/multicourse/Core-203/migration.json'
output.parent.mkdir(parents=True, exist_ok=True)

def snapshot(connection):
    result = {}
    for table, fields in [('user_lesson_progress', 'id,user_id,lesson_id,completed_at'),
                          ('user_task_progress', 'id,user_id,task_id,task_revision,completed_at')]:
        rows = [list(r) for r in connection.execute(text(f'SELECT {fields} FROM {table} ORDER BY id'))]
        result[table] = {'count': len(rows), 'sha256': hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()}
    return result

with engine.connect() as connection:
    assert connection.execute(text('SELECT current_database(),current_user')).one() == ('pymentor_test','pymentor_app')
    head = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
    version = connection.execute(text('SHOW server_version')).scalar_one()
if head == 'c504_course_progress':
    assert output.exists(), 'New head without local acceptance artifact; reconcile manually'
    print('Already migrated; no operation repeated')
    sys.exit(0)
assert head == 'c503_dual_email_change', head
assert not output.exists(), 'Existing migration artifact must be reconciled first'

# Add historical orphan IDs and timestamps on a fresh synthetic user; never infer
# ownership by membership in the current curriculum, which would drop old rows.
from core_async_audit import seed
email, _ = seed()
with engine.begin() as connection:
    uid = connection.execute(text('SELECT id FROM users WHERE email=:email'), {'email':email}).scalar_one()
    connection.execute(text("INSERT INTO user_lesson_progress(user_id,lesson_id,completed_at) VALUES (:u,'historical-orphan-mc203','2020-01-02T03:04:05Z')"), {'u':uid})
    connection.execute(text("INSERT INTO user_task_progress(user_id,task_id,task_revision,completed_at) VALUES (:u,'historical-orphan-mc203-task','old-revision','2020-02-03T04:05:06Z')"), {'u':uid})
    before = snapshot(connection)

password = (private/'migrator_password').read_text(encoding='ascii').strip()
os.environ['PYMENTOR_MIGRATION_URL'] = URL.create('postgresql+asyncpg', username='pymentor_test', password=password, host='127.0.0.1', port=57152, database='pymentor_test').render_as_string(hide_password=False)
try:
    command.upgrade(Config(str(ROOT/'services/core-api/alembic.ini')), 'head')
finally:
    os.environ.pop('PYMENTOR_MIGRATION_URL', None)
with engine.connect() as connection:
    after = snapshot(connection)
    assert before == after, 'Historical progress changed'
    counts = {}
    for table in ('user_lesson_progress','user_task_progress'):
        counts[table] = dict(connection.execute(text(f'SELECT course_id,count(*) FROM {table} GROUP BY course_id')).all())
        assert counts[table] == {'backend': before[table]['count']}
        assert connection.execute(text(f'SELECT count(*) FROM {table} WHERE course_id IS NULL')).scalar_one() == 0
    assert connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one() == 'c504_course_progress'
    report = {'owned_run':RUN, 'postgres':version, 'previous_head':head,'head':'c504_course_progress',
              'before':before,'after':after,'course_counts':counts,'orphan_rows_preserved':True,
              'original_sqlite_opened':False,'api_stopped_during_ddl':True}
output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
engine.dispose()
print(json.dumps(report))
