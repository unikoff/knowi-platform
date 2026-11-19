"""Expand role-admin storage without attributing unverified legacy data."""
from alembic import context, op
import sqlalchemy as sa

revision = 'c527_role_admin'
down_revision = 'c504_course_progress'
branch_labels = None
depends_on = None


def upgrade():
    if context.get_context().dialect.name != 'postgresql':
        raise RuntimeError('role-admin migration requires PostgreSQL')
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    # Legacy NULLs remain unclassified until evidence-backed reconciliation.
    op.add_column('booking_slots', sa.Column('course_id', sa.String(80), nullable=True))
    op.add_column('booking_slots', sa.Column('calendar_owner_id', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
    op.add_column('booking_slots', sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('booking_quotas', sa.Column('course_id', sa.String(80), nullable=True))
    op.add_column('booking_notification_outbox', sa.Column('course_id', sa.String(80), nullable=True))
    op.add_column('booking_notification_outbox', sa.Column('calendar_owner_id', sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True))
    op.add_column('booking_notification_outbox', sa.Column('booking_id', sa.Integer(), nullable=True))
    op.create_table('role_admin_cutover',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('scoped_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('applied_at', sa.DateTime(timezone=True)),
        sa.CheckConstraint('id=1', name='ck_role_admin_cutover_singleton'))
    op.execute('INSERT INTO role_admin_cutover(id,scoped_enabled) VALUES (1,false)')
    op.execute("""CREATE FUNCTION pymentor_prevent_cutover_reversal() RETURNS trigger AS $$
        BEGIN
            IF TG_OP='DELETE' THEN RAISE EXCEPTION 'role_admin_cutover_irreversible'; END IF;
            IF OLD.scoped_enabled AND NOT NEW.scoped_enabled THEN
                RAISE EXCEPTION 'role_admin_cutover_irreversible';
            END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql""")
    op.execute('CREATE TRIGGER pymentor_cutover_irreversible_v1 BEFORE UPDATE OR DELETE ON role_admin_cutover FOR EACH ROW EXECUTE FUNCTION pymentor_prevent_cutover_reversal()')
    op.drop_constraint('uq_booking_slot_datetime', 'booking_slots', type_='unique')
    op.create_index('uq_booking_slot_owner_datetime', 'booking_slots',
                    ['calendar_owner_id', 'slot_date', 'start_time'], unique=True,
                    postgresql_where=sa.text('calendar_owner_id IS NOT NULL AND archived_at IS NULL'))
    op.create_index('uq_booking_slot_legacy_datetime', 'booking_slots',
                    ['slot_date', 'start_time'], unique=True,
                    postgresql_where=sa.text('calendar_owner_id IS NULL AND archived_at IS NULL'))
    op.drop_constraint('uq_booking_quota_month', 'booking_quotas', type_='unique')
    op.create_index('uq_booking_quota_course_month', 'booking_quotas',
                    ['course_id', 'user_id', 'year', 'month'], unique=True,
                    postgresql_where=sa.text('course_id IS NOT NULL'))
    # Until reconciliation, a legacy NULL and backend row must not double a balance.
    op.create_index('uq_booking_quota_legacy_backend', 'booking_quotas',
                    ['user_id', 'year', 'month'], unique=True,
                    postgresql_where=sa.text("course_id IS NULL OR course_id = 'backend'"))

    op.create_table('course_leadership',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('course_id', sa.String(80), nullable=False),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('account_ref', sa.String(36), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True)),
        sa.Column('assigned_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.CheckConstraint('revision > 0', name='ck_course_leadership_revision'))
    op.create_index('uq_course_leadership_active_course', 'course_leadership', ['course_id'], unique=True,
                    postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_index('uq_course_leadership_active_user', 'course_leadership', ['user_id'], unique=True,
                    postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_table('role_admin_revisions',
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='0'),
        sa.CheckConstraint('revision >= 0', name='ck_role_admin_revision'))
    # All authority writers, including legacy maintenance, share this counter.
    op.execute("""
        CREATE FUNCTION pymentor_bump_admin_revision() RETURNS trigger AS $$
        BEGIN
            INSERT INTO role_admin_revisions(user_id,revision) VALUES (NEW.id,1)
            ON CONFLICT (user_id) DO UPDATE SET revision=role_admin_revisions.revision+1;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER pymentor_admin_revision_v1 AFTER UPDATE OF is_admin ON users
        FOR EACH ROW WHEN (OLD.is_admin IS DISTINCT FROM NEW.is_admin)
        EXECUTE FUNCTION pymentor_bump_admin_revision()""")
    op.execute("""
        CREATE FUNCTION pymentor_bump_leadership_revision() RETURNS trigger AS $$
        DECLARE affected integer; old_uid integer; new_uid integer;
        BEGIN
            -- A cascading user deletion must not recreate its revision row.
            IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN RETURN OLD; END IF;
            IF TG_OP <> 'INSERT' THEN
                IF OLD.revoked_at IS NULL THEN old_uid := OLD.user_id; END IF;
            END IF;
            IF TG_OP <> 'DELETE' THEN
                IF NEW.revoked_at IS NULL THEN new_uid := NEW.user_id; END IF;
            END IF;
            FOR affected IN
                SELECT DISTINCT candidate FROM unnest(ARRAY[old_uid,new_uid]) AS ids(candidate)
                WHERE candidate IS NOT NULL ORDER BY candidate
            LOOP
                INSERT INTO role_admin_revisions(user_id,revision)
                SELECT id,1 FROM users WHERE id=affected
                ON CONFLICT (user_id) DO UPDATE SET revision=role_admin_revisions.revision+1;
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute("""CREATE TRIGGER pymentor_leadership_revision_insert_v1 AFTER INSERT ON course_leadership
        FOR EACH ROW WHEN (NEW.revoked_at IS NULL) EXECUTE FUNCTION pymentor_bump_leadership_revision()""")
    op.execute("""CREATE TRIGGER pymentor_leadership_revision_update_v1
        AFTER UPDATE OF user_id,course_id,account_ref,revoked_at ON course_leadership FOR EACH ROW
        WHEN (OLD.user_id IS DISTINCT FROM NEW.user_id OR OLD.course_id IS DISTINCT FROM NEW.course_id
           OR OLD.account_ref IS DISTINCT FROM NEW.account_ref OR OLD.revoked_at IS DISTINCT FROM NEW.revoked_at)
        EXECUTE FUNCTION pymentor_bump_leadership_revision()""")
    op.execute("""CREATE TRIGGER pymentor_leadership_revision_delete_v1 AFTER DELETE ON course_leadership
        FOR EACH ROW WHEN (OLD.revoked_at IS NULL) EXECUTE FUNCTION pymentor_bump_leadership_revision()""")
    op.create_table('role_admin_bootstrap_markers',
        sa.Column('marker', sa.String(80), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('account_ref', sa.String(36)),
        sa.Column('applied_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_table('booking_history',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('slot_id', sa.Integer(), sa.ForeignKey('booking_slots.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('course_id', sa.String(80), nullable=False),
        sa.Column('student_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_at_booking_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        sa.Column('cancelled_at', sa.DateTime(timezone=True)),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.Column('legacy_coverage', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("status IN ('booked','cancelled','completed')", name='ck_booking_history_status'),
        sa.CheckConstraint('ends_at > starts_at', name='ck_booking_history_interval'),
        sa.CheckConstraint('created_at IS NOT NULL OR legacy_coverage', name='ck_booking_history_created_at'))
    op.create_index('ix_booking_history_course_student', 'booking_history', ['course_id', 'student_id'])
    op.create_index('uq_booking_history_active_slot', 'booking_history', ['slot_id'], unique=True,
                    postgresql_where=sa.text("status = 'booked'"))
    op.create_index('uq_booking_history_legacy_slot', 'booking_history', ['slot_id'], unique=True,
                    postgresql_where=sa.text('legacy_coverage'))
    op.create_table('role_admin_audit',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('command_id', sa.String(36), nullable=False),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('actor_account_ref', sa.String(36)),
        sa.Column('actor_role', sa.String(16), nullable=False),
        sa.Column('authority_course_id', sa.String(80)),
        sa.Column('target_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('target_account_ref', sa.String(36)),
        sa.Column('scope', sa.String(16), nullable=False),
        sa.Column('course_id', sa.String(80)),
        sa.Column('action', sa.String(80), nullable=False),
        sa.Column('before_value', sa.JSON()), sa.Column('after_value', sa.JSON()),
        sa.Column('subject_user_ids', sa.ARRAY(sa.Integer()), nullable=False, server_default=sa.text("'{}'::integer[]")),
        sa.Column('reason', sa.Text()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('anonymized', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("scope IN ('platform','course')", name='ck_role_admin_audit_scope'))
    op.create_table('role_admin_commands',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('actor_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('actor_account_ref', sa.String(36), nullable=False),
        sa.Column('target_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL')),
        sa.Column('target_account_ref', sa.String(36)),
        sa.Column('operation', sa.String(80), nullable=False),
        sa.Column('command_id', sa.String(36), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('response_status', sa.Integer(), nullable=False),
        sa.Column('response_body', sa.JSON(), nullable=False),
        sa.Column('subject_user_ids', sa.ARRAY(sa.Integer()), nullable=False, server_default=sa.text("'{}'::integer[]")),
        sa.Column('redacted', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('actor_account_ref', 'operation', 'command_id', name='uq_role_admin_command'))
    op.execute("""
        CREATE FUNCTION pymentor_role_admin_anonymize() RETURNS trigger AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM booking_slots s
                WHERE s.calendar_owner_id=OLD.id
                  AND (s.slot_date + s.start_time::time) > (now() AT TIME ZONE 'UTC' + interval '3 hours')
                  AND (s.student_id IS NOT NULL OR EXISTS (
                      SELECT 1 FROM booking_history h WHERE h.slot_id=s.id AND h.status='booked'
                  ))
            ) THEN
                RAISE EXCEPTION 'role_admin_pending_booking_transfer_required' USING ERRCODE='23514';
            END IF;
            UPDATE booking_slots SET archived_at=COALESCE(archived_at, now())
            WHERE calendar_owner_id=OLD.id;
            UPDATE role_admin_audit SET actor_id = CASE WHEN actor_id=OLD.id THEN NULL ELSE actor_id END,
                actor_account_ref = CASE WHEN actor_id=OLD.id THEN NULL ELSE actor_account_ref END,
                target_user_id = CASE WHEN target_user_id=OLD.id THEN NULL ELSE target_user_id END,
                target_account_ref = CASE WHEN target_user_id=OLD.id THEN NULL ELSE target_account_ref END,
                before_value=NULL, after_value=NULL, reason=NULL, subject_user_ids='{}'::integer[], anonymized=true
            WHERE actor_id=OLD.id OR target_user_id=OLD.id OR OLD.id=ANY(subject_user_ids);
            UPDATE role_admin_commands SET target_user_id=NULL, target_account_ref=NULL,
                request_hash=repeat('0',64), response_status=409, response_body='{}'::json,
                subject_user_ids='{}'::integer[], redacted=true
            WHERE actor_id<>OLD.id AND (target_user_id=OLD.id OR OLD.id=ANY(subject_user_ids));
            UPDATE role_admin_bootstrap_markers SET user_id=NULL, account_ref=NULL WHERE user_id=OLD.id;
            RETURN OLD;
        END; $$ LANGUAGE plpgsql
    """)
    op.execute('CREATE TRIGGER pymentor_role_admin_anonymize_v1 BEFORE DELETE ON users FOR EACH ROW EXECUTE FUNCTION pymentor_role_admin_anonymize()')


def downgrade():
    raise RuntimeError('Refusing downgrade: role-admin course and history data may be lost')
