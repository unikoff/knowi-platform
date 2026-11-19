"""RA1 contract DTOs. Design artifact only; not imported by the running API.

Generate the JSON schemas with ``python role-admin-v1.py``. The HTTP operations,
authorization and transactional semantics are specified in role-admin-v1.md.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, RootModel, StringConstraints, model_validator

Id = Annotated[int, Field(gt=0)]
Year = Annotated[int, Field(ge=2000, le=2100)]
Month = Annotated[int, Field(ge=1, le=12)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]
Course = Annotated[str, Field(min_length=1, max_length=100)]
Role = Literal['user', 'leader', 'admin']
Capability = Literal['course.read', 'course.roster', 'course.search_grant',
                     'course.quota.grant', 'course.subscription.extend',
                     'course.calendar.manage', 'platform.read', 'platform.manage']


class DTO(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class User(DTO):
    id: Id
    account_ref: UUID
    username: str
    email: str
    subscription_until: AwareDatetime | None
    subscription_plan: str | None
    has_active_subscription: bool
    is_admin: bool
    verified: bool
    role: Role
    capabilities: list[Capability]
    course_id: Course | None
    auth_revision: Annotated[int, Field(ge=0)]

    @model_validator(mode='after')
    def validate_role(self):
        if self.is_admin != (self.role == 'admin'):
            raise ValueError('is_admin means platform admin only')
        if (self.course_id is not None) != (self.role == 'leader'):
            raise ValueError('only a leader has one assigned course')
        return self


class Me(DTO):
    user: User


class Error(DTO):
    detail: str
    code: str


class Period(DTO):
    year: Year
    month: Month


class Quota(DTO):
    course_id: Course
    user_id: Id
    year: Year
    month: Month
    granted: Annotated[int, Field(ge=0)]
    used: Annotated[int, Field(ge=0)]
    remaining: Annotated[int, Field(ge=0)]

    @model_validator(mode='after')
    def balance(self):
        if self.remaining != self.granted - self.used:
            raise ValueError('remaining must equal granted minus used')
        return self


class Person(DTO):
    id: Id
    account_ref: UUID
    username: str
    masked_email: str


class SearchResult(DTO):
    course_id: Course
    users: Annotated[list[Person], Field(max_length=10)]
    next_cursor: str | None


class RosterBooking(DTO):
    kind: Literal['upcoming', 'latest']
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    status: Literal['booked', 'completed']


class RosterPerson(Person):
    last_booking_status: Literal['booked', 'cancelled', 'completed'] | None
    display_booking: RosterBooking | None = None
    subscription_until: AwareDatetime | None
    remaining_lessons: Annotated[int, Field(ge=0)] | None = None


class Roster(DTO):
    course_id: Course
    users: list[RosterPerson]
    next_cursor: str | None
    history_coverage: Literal['complete', 'legacy_partial']


class CompletedItem(DTO):
    id: str
    completed_at: AwareDatetime


class Progress(DTO):
    course_id: Course
    user_id: Id
    completed_lessons: list[CompletedItem]
    completed_tasks: list[CompletedItem]
    lessons_count: Annotated[int, Field(ge=0)]
    tasks_count: Annotated[int, Field(ge=0)]


class StatisticsBlock(DTO):
    id: str
    title: str
    completed: Annotated[int, Field(ge=0)]
    total: Annotated[int, Field(ge=0)]
    lesson_ids: list[str]


class StatisticsTrack(DTO):
    id: str
    title: str
    lessons_completed: Annotated[int, Field(ge=0)]
    lessons_countable: Annotated[int, Field(ge=0)]
    blocks: list[StatisticsBlock]


class StatisticsLesson(DTO):
    id: str
    title: str
    completed: bool
    completed_on: date | None
    counts_toward_statistics: bool


class StudentStatistics(DTO):
    course_id: Course
    user_id: Id
    account_ref: UUID
    tracks: list[StatisticsTrack]
    courses: dict[str, list[StatisticsLesson]]


class ProfileBooking(DTO):
    id: Id
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    status: Literal['booked', 'cancelled', 'completed']


class StudentProfileDetails(DTO):
    course_id: Course
    user_id: Id
    account_ref: UUID
    period: Period
    subscription_until: AwareDatetime | None
    subscription_plan: str | None
    bookings: Annotated[list[ProfileBooking], Field(max_length=100)]
    next_cursor: str | None


class DashboardSubscription(DTO):
    id: int
    username: str
    subscription_plan: str | None
    subscription_until: str | None


class DashboardSlots(DTO):
    booked: int
    total: int
    free: int


class DashboardSlotsMonth(DashboardSlots):
    occupancy_percent: int | None = None


class DashboardStudent(DTO):
    id: int
    username: str
    email: str


class DashboardBooking(DTO):
    id: int
    date: str
    start_time: str
    duration_minutes: int
    student: DashboardStudent


class DashboardActive(DTO):
    id: int
    username: str
    lessons_completed: int


class DashboardDayActive(DTO):
    date: str
    active_students: int


class DashboardDayLessons(DTO):
    date: str
    lessons_completed: int


class Dashboard(DTO):
    total_students: int
    active_students_7d: int | None
    inactive_students_7d: int | None
    active_subscriptions_count: int
    lessons_completed_7d: int
    expiring_subscriptions_count: int
    expiring_subscriptions: list[DashboardSubscription]
    slots_30d: DashboardSlotsMonth
    slots_7d: DashboardSlots
    upcoming_bookings: list[DashboardBooking]
    most_active_students: list[DashboardActive]
    daily_active_students: list[DashboardDayActive] | None
    daily_lessons_completed: list[DashboardDayLessons]


class ActivityToday(DTO):
    date: date
    unique_users: Annotated[int, Field(ge=0)] | None
    coverage_since: AwareDatetime | None
    partial_day: bool


class CourseTrendDay(DTO):
    date: date
    with_subscription: Annotated[int, Field(ge=0)] | None
    without_subscription: Annotated[int, Field(ge=0)] | None


class AvailableCourseTrend(DTO):
    status: Literal['available']
    daily: Annotated[list[CourseTrendDay], Field(min_length=30, max_length=30)]
    quality: Literal['unknown', 'complete'] | None = None


class UnavailableCourseTrend(DTO):
    status: Literal['unavailable']
    daily: Annotated[list[CourseTrendDay], Field(max_length=0)]
    quality: Literal['unknown', 'complete'] | None = None


class CourseTrends(DTO):
    visits: AvailableCourseTrend | UnavailableCourseTrend
    completions: AvailableCourseTrend | UnavailableCourseTrend


class CourseOverview(DTO):
    course_id: Course
    period: Period
    roster_count: Annotated[int, Field(ge=0)]
    booking_count: Annotated[int, Field(ge=0)]
    lessons_granted: Annotated[int, Field(ge=0)]
    lessons_used: Annotated[int, Field(ge=0)]
    completed_lessons: Annotated[int, Field(ge=0)]
    course_activity_days: None
    dashboard: Dashboard
    course_trends: CourseTrends | None = None


class CourseStudyTimeCohort(DTO):
    active_ms: Annotated[int, Field(gt=0, le=86_400_000_000)]
    learners: Annotated[int, Field(gt=0, le=1000)]


class CourseStudyTimeDay(DTO):
    date: date
    with_subscription: CourseStudyTimeCohort | None
    without_subscription: CourseStudyTimeCohort | None


class AvailableCourseStudyTime(DTO):
    status: Literal['available']
    quality: Literal['unknown']
    daily: Annotated[list[CourseStudyTimeDay], Field(min_length=30, max_length=30)]


class UnavailableCourseStudyTime(DTO):
    status: Literal['unavailable']
    quality: Literal['unknown']
    daily: Annotated[list[CourseStudyTimeDay], Field(max_length=0)]


class CourseStudyTime(RootModel[AvailableCourseStudyTime | UnavailableCourseStudyTime]):
    pass


class QuotaGrant(DTO):
    year: Year
    month: Month
    add: Annotated[int, Field(ge=1, le=100)]
    reason: Reason


class SubscriptionExtend(DTO):
    days: Annotated[int, Field(ge=1, le=3650)]
    reason: Reason


class SubscriptionQuotaGrant(QuotaGrant, SubscriptionExtend):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class SubscriptionReset(DTO):
    reason: Reason
    confirmed: Literal[True]


class Subscription(DTO):
    scope: Literal['platform']
    user_id: Id
    subscription_until: AwareDatetime | None
    subscription_plan: str | None


class SubscriptionQuotaResult(Subscription):
    quota: Quota


class SlotCreate(DTO):
    owner_id: Id
    slot_date: date
    start_time: Annotated[str, Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')]
    duration_minutes: Literal[60, 90, 120]


class Slot(DTO):
    id: Id
    course_id: Course
    owner_id: Id
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    status: Literal['free', 'booked', 'archived']
    student_id: Id | None


class StudentSlot(DTO):
    id: Id
    course_id: Course
    owner_id: Id
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    status: Literal['free', 'mine']
    can_cancel: bool


class Booking(DTO):
    id: Id
    course_id: Course
    slot_id: Id
    student_id: Id
    owner_at_booking_id: Id | None
    status: Literal['booked', 'cancelled', 'completed']
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    created_at: AwareDatetime | None
    cancelled_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    legacy_coverage: bool

    @model_validator(mode='after')
    def known_creation_time_for_new_booking(self):
        if self.created_at is None and not self.legacy_coverage:
            raise ValueError('new bookings require their actual creation time')
        return self


class BookingContext(DTO):
    course_id: Course
    active_owner_id: Id | None
    active_owner_name: str | None
    my_upcoming_bookings: list[Booking]


class CalendarOwner(DTO):
    id: Id
    username: str
    active: bool
    pending_slots: Annotated[int, Field(ge=0)]


class CalendarContext(DTO):
    course_id: Course
    owners: Annotated[list[CalendarOwner], Field(max_length=1)]


class Slots(DTO):
    course_id: Course
    owner_id: Id
    period: Period
    slots: list[Slot]


class StudentSlots(DTO):
    course_id: Course
    owner_id: Id
    period: Period
    slots: list[StudentSlot]
    quota: Quota


class Transfer(DTO):
    from_owner_id: Id
    to_owner_id: Id
    slot_ids: Annotated[list[Id], Field(min_length=1, max_length=100)]
    reason: Reason
    confirmed: Literal[True]


class AssignmentCommand(DTO):
    user_id: Id
    account_ref: UUID
    reason: Reason


class ReasonCommand(DTO):
    reason: Reason


class AdminRoleCommand(DTO):
    is_admin: bool
    reason: Reason
    confirmed: Literal[True]


class AdminRoleResult(DTO):
    user_id: Id
    is_admin: bool
    auth_revision: Annotated[int, Field(ge=0)]


class Assignment(DTO):
    course_id: Course
    user_id: Id
    active: bool
    revision: Annotated[int, Field(ge=1)]


class Operation(DTO):
    command_id: str
    actor_id: Id | None
    actor_account_ref: UUID | None
    actor_role: Literal['user', 'leader', 'admin', 'system']
    authority_course_id: Course | None
    target_user_id: Id | None
    target_account_ref: UUID | None
    scope: Literal['platform', 'course']
    course_id: Course | None
    action: str
    before: dict | None
    after: dict | None
    reason: str | None
    created_at: AwareDatetime
    anonymized: bool

    @model_validator(mode='after')
    def reason_redacted_only_after_anonymization(self):
        if self.reason is None and not self.anonymized:
            raise ValueError('only anonymized operations may redact their reason')
        return self


class PlatformOverview(DTO):
    users: Annotated[int, Field(ge=0)]
    active_subscribers: Annotated[int, Field(ge=0)]
    courses: Annotated[int, Field(ge=0)]
    course_id: Course | None
    timezone: Literal['Europe/Moscow']
    activity_today: ActivityToday
    dashboard: Dashboard


class PlatformUser(DTO):
    id: Id
    account_ref: UUID
    username: str
    email: str
    role: Role
    subscription: Subscription
    quotas: list[Quota]


class CourseSummary(DTO):
    course_id: Course
    leader_id: Id | None


class PlatformUsers(DTO):
    users: list[PlatformUser]
    next_cursor: str | None


class Courses(DTO):
    courses: list[CourseSummary]
    next_cursor: str | None


class Operations(DTO):
    operations: list[Operation]
    next_cursor: str | None


class BookingEnvelope(DTO):
    booking: Booking
    quota: Quota


class QuotaEnvelope(DTO):
    quota: Quota


class SlotEnvelope(DTO):
    slot: Slot


class AssignmentEnvelope(DTO):
    assignment: Assignment


class TransferResult(DTO):
    course_id: Course
    transferred_slot_ids: list[Id]


MODELS = [Me, Error, Quota, SearchResult, Roster, Progress, StudentStatistics, StudentProfileDetails, CourseOverview, CourseStudyTime, Dashboard, ActivityToday,
          QuotaGrant, SubscriptionExtend, SubscriptionQuotaGrant, SubscriptionReset, Subscription, SubscriptionQuotaResult, SlotCreate,
          Slot, StudentSlot, Booking, BookingContext, CalendarOwner, CalendarContext, Slots, StudentSlots, ReasonCommand,
          AdminRoleCommand, AdminRoleResult,
          Operation, PlatformOverview, PlatformUser, PlatformUsers, Courses,
          Operations, BookingEnvelope, QuotaEnvelope, SlotEnvelope]


if __name__ == '__main__':
    from pydantic.json_schema import models_json_schema
    _, bundle = models_json_schema([(m, 'validation') for m in MODELS], title='Role admin v1 DTOs')
    bundle['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    Path(__file__).with_name('role-admin-v1.schema.json').write_text(
        json.dumps(bundle, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
