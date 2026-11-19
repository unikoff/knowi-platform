"""Add isolated raw browser tool facts; retain facts on rollback."""
from alembic import op

revision = "a014"
down_revision = "a013"
branch_labels = depends_on = None


def upgrade():
    op.execute("ALTER TABLE analytics.activity_facts DROP CONSTRAINT activity_fact_source_shape")
    op.execute("""ALTER TABLE analytics.activity_facts ADD CONSTRAINT activity_fact_source_shape CHECK (
        (source_kind='browser_v2' AND course_id IS NOT NULL AND catalog_version IS NOT NULL AND started_at IS NOT NULL)
        OR (source_kind='core_transfer_v1' AND course_id IS NULL AND catalog_version IS NULL AND lesson_id IS NULL AND started_at IS NULL)
        OR (source_kind='browser_tool_v1' AND course_id IS NOT NULL AND catalog_version IS NOT NULL AND catalog_version='platform-tools-v1'
            AND lesson_id IS NULL AND started_at IS NOT NULL))""")


def downgrade():
    conn = op.get_bind()
    if conn.exec_driver_sql("SELECT EXISTS(SELECT 1 FROM analytics.activity_facts WHERE source_kind='browser_tool_v1')").scalar_one():
        raise RuntimeError("cannot downgrade a014 while tool facts exist")
    op.execute("ALTER TABLE analytics.activity_facts DROP CONSTRAINT activity_fact_source_shape")
    op.execute("""ALTER TABLE analytics.activity_facts ADD CONSTRAINT activity_fact_source_shape CHECK (
        (source_kind='browser_v2' AND course_id IS NOT NULL AND catalog_version IS NOT NULL AND started_at IS NOT NULL)
        OR (source_kind='core_transfer_v1' AND course_id IS NULL AND catalog_version IS NULL AND lesson_id IS NULL AND started_at IS NULL))""")
