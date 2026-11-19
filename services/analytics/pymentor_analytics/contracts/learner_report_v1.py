"""Private, versioned learner report wire contract."""
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, field_validator

Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class LessonCompletion(Contract):
    course_id: Identifier
    lesson_id: Identifier
    completed_at: AwareDatetime


class LearnerSnapshot(Contract):
    schema_version: Literal[1]
    subject_ref: UUID
    snapshot_at: AwareDatetime
    period_days: Literal[7, 30]
    course_id: Identifier | None
    activity_complete: StrictBool
    activity_dates: Annotated[list[date], Field(max_length=10000)]
    lesson_completions: Annotated[list[LessonCompletion], Field(max_length=5000)]

    @field_validator("activity_dates", mode="before")
    @classmethod
    def canonical_dates(cls, value):
        import re
        if isinstance(value, list) and any(not isinstance(item, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item) for item in value):
            raise ValueError("invalid date format")
        return value


class Period(Contract):
    days: Literal[7, 30]
    start: date
    end: date
    timezone: Literal["Europe/Moscow"] = "Europe/Moscow"


class Scope(Contract):
    course_id: str | None
    activity: Literal["platform"] = "platform"


class Coverage(Contract):
    visits: Literal["legacy_global", "partial"]
    time: Literal["unavailable"] = "unavailable"
    progress: Literal["core"] = "core"


class Summary(Contract):
    active_days_total: int | None
    current_streak: int | None
    active_days_period: int
    completed_lessons_period: int
    active_seconds: None = None
    theory_seconds: None = None
    practice_seconds: None = None


class Day(Contract):
    date: date
    visited: bool
    completed_lessons: int
    active_seconds: None = None
    theory_seconds: None = None
    practice_seconds: None = None


class LearnerReport(Contract):
    schema_version: Literal[1] = 1
    source: Literal["analytics"] = "analytics"
    as_of: datetime
    period: Period
    scope: Scope
    coverage: Coverage
    summary: Summary
    days: list[Day]


class ErrorResponse(Contract):
    code: str
