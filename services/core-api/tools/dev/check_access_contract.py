"""Check the agreed matrix against existing pure policy, without database imports."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / 'services/core-api'))
from pymentor_core.modules.learning.access import get_lesson_access


def main():
    folder = ROOT / 'docs/multicourse/MC1.3'
    fixture = ROOT / 'services/core-api/tests/fixtures/contracts/multicourse/MC1.3/access-contract.json'
    contract = json.loads(fixture.read_text(encoding='utf-8'))
    anchor = datetime.fromisoformat(contract['clock'])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return anchor.astimezone(tz or timezone.utc)
    observed = []
    with patch('pymentor_core.modules.identity.access.datetime', Clock):
        for actor in contract['actors']:
            user = (SimpleNamespace(subscription_until=(datetime.fromisoformat(actor['until'])
                    if actor['until'] else None), is_admin=actor.get('admin', False))
                    if actor['user'] else None)
            for course in contract['course_ids']:
                for access in ('free', 'registered', 'subscription'):
                    allowed, reason = get_lesson_access({'access':access, 'course_id':course}, user)
                    assert allowed is actor[access], (actor['id'], course, access)
                    observed.append({'actor':actor['id'], 'course':course,
                                     'access':access, 'allowed':allowed})
    assert not any(p in sys.modules for p in ('sqlite3','sqlalchemy','psycopg','asyncpg'))
    report = {'level':'existing pure helper compatibility; not course-aware HTTP/PG proof',
              'cases':len(observed),'status':'PASS','observed':observed,
              'database_imports':False,'database_connections':False}
    (folder / 'source-policy-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'source_policy_compatibility':'PASS','cases':len(observed),
                      'runtime_multicourse_checked':False}))


if __name__ == '__main__':
    main()
