"""Pin configured course leaders to their original account lifecycle."""
from alembic import op
import sqlalchemy as sa

revision = 'c528_course_leaders'
down_revision = 'c527_role_admin'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('course_leader_bindings',
        sa.Column('course_id', sa.String(80), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('account_ref', sa.String(36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))


def downgrade():
    raise RuntimeError('Refusing downgrade of lifecycle-pinned course leaders')
