"""Shared browser admission budget; no fact or job deletion.

Revision ID: a003
"""
import re
from alembic import op

revision = "a003"
down_revision = "a002"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    name = conn.exec_driver_sql("SELECT current_database()").scalar_one()
    if not re.fullmatch(r"analytics_[a-z0-9_]{1,42}", name):
        raise ValueError("unsafe Analytics database name")
    quote = conn.dialect.identifier_preparer.quote
    collector, privacy = (quote(f"{name}_{role}") for role in ("collector", "privacy"))
    op.execute("""CREATE TABLE analytics.admission_budget (
        singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
        subject_per_minute integer NOT NULL CHECK (subject_per_minute BETWEEN 1 AND 6000),
        global_per_minute integer NOT NULL CHECK (global_per_minute BETWEEN 1 AND 60000),
        max_outstanding_jobs integer NOT NULL CHECK (max_outstanding_jobs BETWEEN 1 AND 100000),
        max_database_bytes bigint NOT NULL CHECK (max_database_bytes BETWEEN 1 AND 1099511627776),
        warning_percent integer NOT NULL CHECK (warning_percent BETWEEN 1 AND 98),
        critical_percent integer NOT NULL CHECK (critical_percent BETWEEN 2 AND 99),
        window_epoch bigint NOT NULL DEFAULT 0,
        accepted_count integer NOT NULL DEFAULT 0 CHECK (accepted_count >= 0),
        CHECK (warning_percent < critical_percent),
        CHECK (subject_per_minute <= global_per_minute)
    )""")
    op.execute("""INSERT INTO analytics.admission_budget
        (subject_per_minute,global_per_minute,max_outstanding_jobs,max_database_bytes,warning_percent,critical_percent)
        VALUES (60,600,10000,536870912,80,90)""")
    op.execute("""CREATE TABLE analytics.admission_subject_windows (
        subject_ref uuid PRIMARY KEY, window_epoch bigint NOT NULL,
        accepted_count integer NOT NULL CHECK (accepted_count >= 0)
    )""")
    op.execute("CREATE INDEX admission_subject_windows_expiry ON analytics.admission_subject_windows (window_epoch)")
    op.execute("CREATE INDEX processing_jobs_outstanding ON analytics.processing_jobs (status) WHERE status <> 'completed'")
    op.execute(f"GRANT SELECT ON analytics.admission_budget TO {collector}")
    op.execute(f"GRANT UPDATE (window_epoch, accepted_count) ON analytics.admission_budget TO {collector}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON analytics.admission_subject_windows TO {collector}")
    op.execute(f"GRANT SELECT, DELETE ON analytics.admission_subject_windows TO {privacy}")


def downgrade():
    raise RuntimeError("a003 is forward-only; rollback must preserve facts, jobs and budgets")
