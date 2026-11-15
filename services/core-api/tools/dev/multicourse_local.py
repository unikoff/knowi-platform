"""Run or probe only this task's disposable PostgreSQL/Redis Core instance."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[4]
DEFAULT_RUN = 'mc460-20261113-2048'


def configure(run_id: str) -> dict:
    if not re.fullmatch(r'mc460-[0-9-]+', run_id):
        raise SystemExit('Only the explicit multicourse lab identity is accepted')
    private = Path(os.environ['LOCALAPPDATA']) / 'Pymentor' / 'c225' / run_id
    state = json.loads((private / 'state.json').read_text(encoding='utf-8'))
    if (state['run_id'] != run_id or state['phase'] != 'migrated'
            or state['port'] != 57152 or state['database'] != 'pymentor_test'):
        raise SystemExit('Lab must be owned, migrated and bound to its guarded port')
    password = (private / 'app_password').read_text(encoding='ascii').strip()
    if not re.fullmatch(r'[0-9a-f]{64}', password):
        raise SystemExit('Unexpected lab credential format')
    recovery = private / 'multicourse_recovery_key'
    if not recovery.exists():
        with recovery.open('x', encoding='ascii') as output:
            output.write(secrets.token_hex(32))
    for key in list(os.environ):
        if key.startswith(('PYMENTOR_SMTP_', 'PYMENTOR_PLATFORM_TELEGRAM_',
                           'PYMENTOR_EMAIL_', 'KNOWI_REDIS_', 'KNOWI_LOGIN_')):
            os.environ.pop(key, None)
    os.environ.update({
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHON_DOTENV_DISABLED': '1',
        'PYMENTOR_PROFILE': 'postgres_redis', 'PYMENTOR_SCHEMA_MODE': 'managed',
        'PYMENTOR_REQUIRE_DATABASE_URL': '1',
        'DATABASE_URL': f'postgresql+psycopg://pymentor_app:{password}@127.0.0.1:57152/pymentor_test',
        'REDIS_URL': 'redis://127.0.0.1:57146/0',
        'PYMENTOR_REDIS_PREFIX': f'multicourse:{run_id}:',
        'PYMENTOR_LOGIN_RECOVERY_KEY': recovery.read_text(encoding='ascii').strip(),
        'PYMENTOR_COURSE_TRANSPORT': 'local',
        'PYMENTOR_LANDING_BOT_CREDENTIALS': '{}',
        'PYMENTOR_COOKIE_SECURE': '0',
        'PYMENTOR_LOCAL_NOTIFICATIONS': '0',
    })
    os.environ.pop('PYMENTOR_DUAL_LOCAL_TEST', None)
    for p in (ROOT / 'services' / 'core-api',):
        sys.path.insert(0, str(p))
    return state


def probe(output: Path) -> dict:
    if (output / 'api-baseline.json').exists():
        raise SystemExit('Evidence already exists; choose a new --output directory')
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from pymentor_core.app.asgi import create_app
    from pymentor_core.app.database import engine
    app = create_app()
    responses = {}
    with TestClient(app) as client:
        for route in ('/health/live', '/health/ready', '/api/learning/tracks',
                      '/api/learning/catalog', '/backend', '/backend/landing'):
            response = client.get(route)
            item = {'status': response.status_code,
                    'content_type': response.headers.get('content-type', '')}
            if 'json' in item['content_type']:
                item['body'] = response.json()
            else:
                item['body_sha256'] = hashlib.sha256(response.content).hexdigest()
            responses[route] = item
        openapi = app.openapi()
        with engine.connect() as connection:
            identity = connection.execute(text('SELECT current_database(), current_user')).one()
            revision = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
            counts = {name: connection.execute(text(f'SELECT count(*) FROM {name}')).scalar_one()
                      for name in ('users', 'user_lesson_progress', 'user_task_progress')}
    report = {'mode': 'actual disposable PostgreSQL/Redis + in-process HTTP',
              'original_sqlite_opened': False, 'external_messages_sent': False,
              'identity': list(identity), 'schema_revision': revision,
              'counts': counts, 'responses': responses}
    output.mkdir(parents=True, exist_ok=True)
    (output / 'api-baseline.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'openapi-baseline.json').write_text(json.dumps(openapi, ensure_ascii=False, indent=2), encoding='utf-8')
    summary = {'identity': list(identity), 'schema_revision': revision,
               'counts': counts, 'statuses': {p: r['status'] for p, r in responses.items()}}
    print(json.dumps(summary, ensure_ascii=False))
    if any(value['status'] != 200 for value in responses.values()):
        raise SystemExit(2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('probe', 'run'))
    parser.add_argument('--run-id', default=DEFAULT_RUN)
    parser.add_argument('--port', type=int, default=18001)
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'docs/multicourse/runtime-checks' / uuid4().hex)
    args = parser.parse_args()
    configure(args.run_id)
    if args.action == 'probe':
        probe(args.output)
    else:
        import uvicorn
        uvicorn.run('pymentor_core.app.asgi:create_app', factory=True,
                    host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
