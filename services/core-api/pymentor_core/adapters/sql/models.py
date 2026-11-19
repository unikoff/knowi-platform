from datetime import date
from datetime import datetime
from datetime import timezone
from uuid import uuid4

from pymentor_core.modules.identity.passwords import hash_password as _hash_password
from pymentor_core.modules.identity.passwords import is_bcrypt_hash as _is_bcrypt_hash
from pymentor_core.modules.identity.passwords import verify_password as _verify_password
from sqlalchemy import Boolean
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Integer
from sqlalchemy import JSON
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import func
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from pymentor_core.app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    _password_hash: Mapped[str] = mapped_column(
        "password_hash", String(255), nullable=False
    )
    subscription_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None, index=True
    )
    subscription_plan: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default=None
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    @property
    def password(self):
        return self._password_hash

    @password.setter
    def password(self, plain_password: str):
        if not plain_password:
            raise ValueError("Password cannot be empty")
        if _is_bcrypt_hash(plain_password):
            self._password_hash = plain_password
        else:
            self._password_hash = _hash_password(plain_password)

    def verify_password(self, plain_password: str) -> bool:
        return _verify_password(plain_password, self._password_hash)


class ConsumedEmailChange(Base):
    __tablename__ = "consumed_email_changes"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmailChangeRequest(Base):
    __tablename__ = "email_change_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    old_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    new_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")


class Admin(Base):
    __tablename__ = "admins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    _password_hash: Mapped[str] = mapped_column(
        "password_hash", String(255), nullable=False
    )

    @property
    def password(self):
        return self._password_hash

    @password.setter
    def password(self, plain_password: str):
        if not plain_password:
            raise ValueError("Password cannot be empty")
        if _is_bcrypt_hash(plain_password):
            self._password_hash = plain_password
        else:
            self._password_hash = _hash_password(plain_password)

    def verify_password(self, plain_password: str) -> bool:
        return _verify_password(plain_password, self._password_hash)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )
    session_token: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserActivityDay(Base):
    __tablename__ = "user_activity_days"
    __table_args__ = (
        UniqueConstraint("user_id", "activity_date", name="uq_user_activity_day"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )
    activity_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    visited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserLessonProgress(Base):
    __tablename__ = "user_lesson_progress"
    __table_args__ = (
        UniqueConstraint("course_id", "user_id", "lesson_id", name="uq_course_user_lesson_progress"),
        Index("ix_user_lesson_progress_completed_at", "completed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )
    lesson_id: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    course_id: Mapped[str] = mapped_column(String(80), nullable=False, default="backend", server_default="backend")
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserTaskProgress(Base):
    __tablename__ = "user_task_progress"
    __table_args__ = (
        UniqueConstraint("course_id", "user_id", "task_id", name="uq_course_user_task_progress"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )
    task_id: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    course_id: Mapped[str] = mapped_column(String(80), nullable=False, default="backend", server_default="backend")
    task_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BookingSlot(Base):
    """Свободное окно для занятия. Создаёт админ, бронирует студент (1 слот = 1 студент)."""

    __tablename__ = "booking_slots"
    __table_args__ = (
        Index("uq_booking_slot_owner_datetime", "calendar_owner_id", "slot_date", "start_time", unique=True,
              postgresql_where=text("calendar_owner_id IS NOT NULL AND archived_at IS NULL")),
        Index("uq_booking_slot_legacy_datetime", "slot_date", "start_time", unique=True,
              postgresql_where=text("calendar_owner_id IS NULL AND archived_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    slot_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)  # "HH:MM"
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    course_id: Mapped[str | None] = mapped_column(String(80), nullable=True, default="backend")
    calendar_owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BookingQuota(Base):
    """Сколько занятий студент может забронировать в конкретном месяце (выдаёт админ)."""

    __tablename__ = "booking_quotas"
    __table_args__ = (
        Index("uq_booking_quota_course_month", "course_id", "user_id", "year", "month", unique=True,
              postgresql_where=text("course_id IS NOT NULL")),
        Index("uq_booking_quota_legacy_backend", "user_id", "year", "month", unique=True,
              postgresql_where=text("course_id IS NULL OR course_id = 'backend'")),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=False
    )
    course_id: Mapped[str | None] = mapped_column(String(80), nullable=True, default="backend")
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    granted_slots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class CourseLeadership(Base):
    __tablename__ = 'course_leadership'
    __table_args__ = (
        Index('uq_course_leadership_active_course', 'course_id', unique=True,
              postgresql_where=text('revoked_at IS NULL')),
        Index('uq_course_leadership_active_user', 'user_id', unique=True,
              postgresql_where=text('revoked_at IS NULL')),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[str] = mapped_column(String(80), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    account_ref: Mapped[str] = mapped_column(String(36), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))


class RoleAdminRevision(Base):
    __tablename__ = 'role_admin_revisions'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default='0')


class RoleAdminBootstrapMarker(Base):
    __tablename__ = 'role_admin_bootstrap_markers'
    marker: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    account_ref: Mapped[str | None] = mapped_column(String(36))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class BookingHistory(Base):
    __tablename__ = 'booking_history'
    __table_args__ = (
        Index('ix_booking_history_course_student', 'course_id', 'student_id'),
        Index('uq_booking_history_active_slot', 'slot_id', unique=True,
              postgresql_where=text("status = 'booked'")),
        Index('uq_booking_history_legacy_slot', 'slot_id', unique=True,
              postgresql_where=text('legacy_coverage')),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey('booking_slots.id', ondelete='RESTRICT'), nullable=False)
    course_id: Mapped[str] = mapped_column(String(80), nullable=False)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    owner_at_booking_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, server_default=func.now())
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    legacy_coverage: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default='false')


class RoleAdminAudit(Base):
    __tablename__ = 'role_admin_audit'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    command_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    actor_account_ref: Mapped[str | None] = mapped_column(String(36))
    actor_role: Mapped[str] = mapped_column(String(16), nullable=False)
    authority_course_id: Mapped[str | None] = mapped_column(String(80))
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    target_account_ref: Mapped[str | None] = mapped_column(String(36))
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    course_id: Mapped[str | None] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    before_value: Mapped[dict | None] = mapped_column(JSON)
    after_value: Mapped[dict | None] = mapped_column(JSON)
    subject_user_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False, server_default=text("'{}'::integer[]"))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    anonymized: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default='false')


class RoleAdminCommand(Base):
    __tablename__ = 'role_admin_commands'
    __table_args__ = (UniqueConstraint('actor_account_ref', 'operation', 'command_id', name='uq_role_admin_command'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    actor_account_ref: Mapped[str] = mapped_column(String(36), nullable=False)
    target_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    target_account_ref: Mapped[str | None] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(80), nullable=False)
    command_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_status: Mapped[int] = mapped_column(Integer, nullable=False)
    response_body: Mapped[dict] = mapped_column(JSON, nullable=False)
    subject_user_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False, server_default=text("'{}'::integer[]"))
    redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default='false')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RoleAdminCutover(Base):
    __tablename__ = 'role_admin_cutover'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scoped_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default='false')
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BookingNotificationOutbox(Base):
    """Durable Telegram notifications created in the booking transaction."""

    __tablename__ = "booking_notification_outbox"
    __table_args__ = (
        Index("ix_booking_notification_ready", "status", "next_attempt_at"),
        Index("ix_booking_notification_slot_order", "slot_id", "id"),
        Index("ix_booking_notification_retention", "status", "sent_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[str] = mapped_column(
        String(36), unique=True, nullable=False, default=lambda: str(uuid4())
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    slot_id: Mapped[int] = mapped_column(Integer, nullable=False)
    course_id: Mapped[str | None] = mapped_column(String(80))
    calendar_owner_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='SET NULL'))
    booking_id: Mapped[int | None] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(100), nullable=False)
    slot_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[str] = mapped_column(String(5), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lock_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BookingNotificationDeliveryState(Base):
    """Shared Telegram cooldown, including notifications created during a rate limit."""

    __tablename__ = "booking_notification_delivery_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    blocked_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Landing(Base):
    """Stable backend identity and delivery switches for a public landing page."""

    __tablename__ = "landings"
    __table_args__ = (UniqueConstraint("slug", name="uq_landings_slug"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    telegram_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    config_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class LandingTelegramConfig(Base):
    """Immutable destination version; credentials stay in the external secret store."""

    __tablename__ = "landing_telegram_configs"
    __table_args__ = (
        UniqueConstraint(
            "landing_id", "version", name="uq_landing_telegram_config_version"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    landing_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("landings.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    secret_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    bot_username: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="verified")
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LandingNotificationOutbox(Base):
    """Allowlisted landing event routed to one immutable Telegram config version."""

    __tablename__ = "landing_notification_outbox"
    __table_args__ = (
        UniqueConstraint(
            "landing_id", "event_id", name="uq_landing_notification_event"
        ),
        Index("ix_landing_notification_ready", "status", "next_attempt_at"),
        Index("ix_landing_notification_retention", "status", "sent_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    landing_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("landings.id", ondelete="RESTRICT"), nullable=False
    )
    destination_config_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("landing_telegram_configs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    manual_retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lock_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LandingNotificationDeliveryState(Base):
    """Per-bot Telegram rate-limit cooldown for landing notifications."""

    __tablename__ = "landing_notification_delivery_state"

    destination_config_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("landing_telegram_configs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    blocked_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
