"""HTTP output types for the implemented subset of role-admin v1."""
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, RootModel, StringConstraints, field_validator
from pymentor_core.modules.administration.api.schemas import AdminOverviewResponse, DailyActiveStudentsResponse

Id = Annotated[int, Field(gt=0)]
Count = Annotated[int, Field(ge=0)]
Year = Annotated[int, Field(ge=2000, le=2100)]
Month = Annotated[int, Field(ge=1, le=12)]
Course = Annotated[str, Field(min_length=1, max_length=100)]


class DTO(BaseModel):
    model_config = ConfigDict(extra='forbid')


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
    granted: Count
    used: Count
    remaining: Count


class QuotaEnvelope(DTO):
    quota: Quota


class QuotaGrant(DTO):
    year: Annotated[int, Field(strict=True, ge=2000, le=2100)]
    month: Annotated[int, Field(strict=True, ge=1, le=12)]
    add: Annotated[int, Field(strict=True, ge=1, le=100)]
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class SubscriptionExtend(DTO):
    days: Annotated[int, Field(strict=True, ge=1, le=3650)]
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class SubscriptionQuotaGrant(QuotaGrant, SubscriptionExtend):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ReasonCommand(DTO):
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class SubscriptionReset(ReasonCommand):
    confirmed: Literal[True]

    @field_validator('confirmed', mode='before')
    @classmethod
    def require_boolean_true(cls, value):
        if value is not True:
            raise ValueError('explicit confirmation required')
        return value


class AdminRoleCommand(ReasonCommand):
    is_admin: Annotated[bool, Field(strict=True)]
    confirmed: Literal[True]

    @field_validator('confirmed', mode='before')
    @classmethod
    def require_boolean_true(cls, value):
        if value is not True:
            raise ValueError('explicit confirmation required')
        return value


class AdminRoleResult(DTO):
    user_id: Id
    is_admin: bool
    auth_revision: Count


class AssignmentCommand(ReasonCommand):
    user_id: Annotated[int, Field(strict=True, gt=0)]
    account_ref: UUID


class Assignment(DTO):
    course_id: Course
    user_id: Id
    active: bool
    revision: Annotated[int, Field(ge=1)]


class AssignmentEnvelope(DTO):
    assignment: Assignment


class Person(DTO):
    id: Id
    account_ref: UUID
    username: str
    masked_email: str


class RosterBooking(DTO):
    kind: Literal['upcoming', 'latest']
    starts_at: datetime
    ends_at: datetime
    status: Literal['booked', 'completed']


class RosterPerson(Person):
    last_booking_status: Literal['booked', 'cancelled', 'completed'] | None
    display_booking: RosterBooking | None = None
    subscription_until: datetime | None
    remaining_lessons: Count | None = None


class Roster(DTO):
    course_id: Course
    users: list[RosterPerson]
    next_cursor: str | None
    history_coverage: Literal['complete', 'legacy_partial']


class SearchResult(DTO):
    course_id: Course
    users: Annotated[list[Person], Field(max_length=10)]
    next_cursor: str | None


class CompletedItem(DTO):
    id: str
    completed_at: datetime


class Progress(DTO):
    course_id: Course
    user_id: Id
    completed_lessons: list[CompletedItem]
    completed_tasks: list[CompletedItem]
    lessons_count: Count
    tasks_count: Count


class StatisticsBlock(DTO):
    id: str
    title: str
    completed: Count
    total: Count
    lesson_ids: list[str]


class StatisticsTrack(DTO):
    id: str
    title: str
    lessons_completed: Count
    lessons_countable: Count
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
    starts_at: datetime
    ends_at: datetime
    status: Literal['booked', 'cancelled', 'completed']


class StudentProfileDetails(DTO):
    course_id: Course
    user_id: Id
    account_ref: UUID
    period: Period
    subscription_until: datetime | None
    subscription_plan: str | None
    bookings: Annotated[list[ProfileBooking], Field(max_length=100)]
    next_cursor: str | None


class Dashboard(AdminOverviewResponse):
    active_students_7d: int | None
    inactive_students_7d: int | None
    daily_active_students: list[DailyActiveStudentsResponse] | None


class ActivityToday(DTO):
    date: date
    unique_users: Count | None
    coverage_since: datetime | None
    partial_day: bool


class CourseTrendDay(DTO):
    date: date
    with_subscription: Count | None
    without_subscription: Count | None


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
    roster_count: Count
    booking_count: Count
    lessons_granted: Count
    lessons_used: Count
    completed_lessons: Count
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


class CalendarOwner(DTO):
    id: Id
    username: str
    active: bool
    pending_slots: Count


class CalendarContext(DTO):
    course_id: Course
    owners: Annotated[list[CalendarOwner], Field(max_length=1)]


class PlatformOverview(DTO):
    users: Count
    active_subscribers: Count
    courses: Count
    course_id: Course | None
    timezone: Literal['Europe/Moscow']
    activity_today: ActivityToday
    dashboard: Dashboard


class CourseSummary(DTO):
    course_id: Course
    leader_id: Id | None


class Courses(DTO):
    courses: list[CourseSummary]
    next_cursor: str | None


class Subscription(DTO):
    scope: Literal['platform']
    user_id: Id
    subscription_until: datetime | None
    subscription_plan: str | None


class SubscriptionQuotaResult(Subscription):
    quota: Quota


class PlatformUser(DTO):
    id: Id
    account_ref: UUID
    username: str
    email: str
    role: Literal['user', 'leader', 'admin']
    subscription: Subscription
    quotas: list[Quota]


class PlatformUsers(DTO):
    users: list[PlatformUser]
    next_cursor: str | None


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
    created_at: datetime
    anonymized: bool


class Operations(DTO):
    operations: list[Operation]
    next_cursor: str | None


class CourseSlotCreate(DTO):
    owner_id: Annotated[int, Field(strict=True, gt=0)]
    slot_date: date
    start_time: Annotated[str, Field(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')]
    duration_minutes: Literal[60, 90, 120]

    @field_validator('duration_minutes', mode='before')
    @classmethod
    def duration_is_integer(cls, value):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError('duration must be an integer')
        return value


class Slot(DTO):
    id: Id
    course_id: Course
    owner_id: Id
    starts_at: datetime
    ends_at: datetime
    status: Literal['free', 'booked', 'archived']
    student_id: Id | None


class StudentSlot(DTO):
    id: Id
    course_id: Course
    owner_id: Id
    starts_at: datetime
    ends_at: datetime
    status: Literal['free', 'mine']
    can_cancel: bool


class Booking(DTO):
    id: Id
    course_id: Course
    slot_id: Id
    student_id: Id
    owner_at_booking_id: Id | None
    status: Literal['booked', 'cancelled', 'completed']
    starts_at: datetime
    ends_at: datetime
    created_at: datetime | None
    cancelled_at: datetime | None
    completed_at: datetime | None
    legacy_coverage: bool


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


class BookingContext(DTO):
    course_id: Course
    active_owner_id: Id | None
    active_owner_name: str | None
    my_upcoming_bookings: list[Booking]


class SlotEnvelope(DTO):
    slot: Slot


class BookingEnvelope(DTO):
    booking: Booking
    quota: Quota


class Transfer(ReasonCommand):
    from_owner_id: Annotated[int, Field(strict=True, gt=0)]
    to_owner_id: Annotated[int, Field(strict=True, gt=0)]
    slot_ids: Annotated[list[Annotated[int, Field(strict=True, gt=0)]], Field(min_length=1, max_length=100)]
    confirmed: Literal[True]

    @field_validator('confirmed', mode='before')
    @classmethod
    def require_boolean_true(cls, value):
        if value is not True:
            raise ValueError('explicit confirmation required')
        return value


class TransferResult(DTO):
    course_id: Course
    transferred_slot_ids: list[Id]
