"""Isolate progress by product; every historical row belongs to backend."""
from alembic import op
import sqlalchemy as sa

revision = 'c504_course_progress'
down_revision = 'c503_dual_email_change'
branch_labels = None
depends_on = None


def upgrade():
    # Local rehearsal with API stopped. Bounded DDL lock; one transactional revision.
    # PG16 constant default backfills existing/orphan IDs without rewriting times.
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    for table, entity in [('user_lesson_progress', 'lesson'), ('user_task_progress', 'task')]:
        op.add_column(table, sa.Column('course_id', sa.String(80), nullable=False, server_default='backend'))
        op.create_unique_constraint(f'uq_course_user_{entity}_progress', table, ['course_id', 'user_id', f'{entity}_id'])
        op.drop_constraint(f'uq_user_{entity}_progress', table, type_='unique')


def downgrade():
    raise RuntimeError('Refusing to merge or discard progress from distinct courses')
