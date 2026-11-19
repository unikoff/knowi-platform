from datetime import date, datetime
from types import SimpleNamespace

from sqlalchemy import text

from pymentor_core.adapters.sql import role_admin_reads as reads
from pymentor_core.modules.learning.progress_policy import (
    chapter_one_completion_revision, chapter_one_completion_task_id,
)


def test_course_completion_trend_counts_each_current_proof_once(synthetic_engine):
    now = datetime.fromisoformat('2026-11-19T22:15:00+00:00')
    mixed = {'id': 'mixed', 'manual_practice': True,
             'tasks': [{'id': 'mixed-task', 'revision': 'r'}], 'progress_policy': {'completion': 'lesson'}}
    marker = {'id': 'marker', 'self_check': ['done']}
    alternative = {'id': 'alternative', 'tasks': [{'id': 'current-task', 'revision': 'r'}]}
    lessons = [mixed, marker, alternative, {'id': 'first'}, {'id': 'future'}, {'id': 'other'},
               {'id': 'overview', 'progress_policy': {'overview': True}}, {'id': 'ignored'}]
    catalog = SimpleNamespace(LESSONS=lessons, get_tracks_summary=lambda: [
        {'countable_lesson_ids': [item['id'] for item in lessons if item['id'] != 'ignored']},
    ])
    with synthetic_engine.begin() as db:
        db.execute(text('CREATE TEMP TABLE users (id int PRIMARY KEY, is_admin bool, subscription_until timestamptz)'))
        db.execute(text('''CREATE TEMP TABLE user_lesson_progress (
            user_id int, course_id text, lesson_id text, completed_at timestamptz,
            UNIQUE(course_id,user_id,lesson_id))'''))
        db.execute(text('''CREATE TEMP TABLE user_task_progress (
            user_id int, course_id text, task_id text, task_revision text NOT NULL, completed_at timestamptz,
            UNIQUE(course_id,user_id,task_id))'''))
        db.execute(text('''INSERT INTO users VALUES
            (1,false,'2026-12-01 00:00Z'),(2,false,:now),(3,true,NULL),(4,false,NULL)'''), {'now': now})
        db.execute(text('''INSERT INTO user_lesson_progress VALUES
            (1,'selected','first','2026-10-21 21:00Z'),
            (1,'selected','mixed','2026-10-20 00:00Z'),
            (1,'selected','alternative','2026-10-21 20:59:59Z'),
            (1,'selected','future','2026-11-19 22:16Z'),
            (1,'foreign','other','2026-11-19 22:00Z'),
            (3,'selected','first','2026-11-19 21:00Z'),
            (4,'selected','first','2026-11-19 21:00Z'),
            (4,'selected','overview','2026-11-19 21:00Z'),
            (4,'selected','ignored','2026-11-19 21:00Z')'''))
        db.execute(text('''INSERT INTO user_task_progress VALUES
            (1,'selected','mixed-task','r','2026-11-19 20:59:59Z'),
            (1,'selected','current-task','r','2026-11-19 21:00Z'),
            (2,'selected',:marker,:revision,'2026-11-19 21:00Z'),
            (4,'selected','current-task','stale','2026-11-19 21:00Z')'''),
            {'marker': chapter_one_completion_task_id(marker['id']), 'revision': chapter_one_completion_revision(marker)})
        trend = reads._course_completion_trend(db, 'selected', catalog, now)
        other = reads._course_completion_trend(db, 'foreign', catalog, now)
        db.execute(text('DELETE FROM users WHERE id=4'))
        after_deletion = reads._course_completion_trend(db, 'selected', catalog, now)

    assert trend['status'] == 'available' and trend['quality'] == 'complete'
    assert len(trend['daily']) == 30
    assert trend['daily'][0]['date'] == date(2026, 10, 22)
    assert trend['daily'][-1]['date'] == date(2026, 11, 20)
    assert {(row['date'], row['with_subscription'], row['without_subscription'])
            for row in trend['daily'] if row['with_subscription'] or row['without_subscription']} == {
        (date(2026, 10, 22), 1, 0),
        (date(2026, 11, 19), 1, 0),
        (date(2026, 11, 20), 0, 2),
    }
    assert other['daily'][-1] == {'date': date(2026, 11, 20), 'with_subscription': 1, 'without_subscription': 0}
    assert after_deletion['daily'][-1]['without_subscription'] == 1
