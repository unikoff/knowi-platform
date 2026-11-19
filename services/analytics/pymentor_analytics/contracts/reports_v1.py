"""Isolated report DTO; not the existing v2 ReportResponse wire contract."""
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, ClassVar, Literal
from uuid import UUID
from pydantic import Field, model_validator
from pymentor_analytics.contracts.v2 import Contract, Identifier, AwareTime

Count = Annotated[int, Field(ge=0)]
MAX_SELECTED_LESSONS = 2000

class ActivityQuery(Contract):
    from_time: AwareTime
    to_time: AwareTime
    timezone: Literal["Europe/Moscow"] = "Europe/Moscow"
    course_id: Identifier | None
    subject_ref: UUID | None
    lesson_id: Identifier | None = None

    @model_validator(mode="after")
    def bounds(self):
        if self.lesson_id is not None and self.course_id is None:
            raise ValueError("lesson_id requires course_id")
        if self.from_time.utcoffset() != timedelta(0) or self.to_time.utcoffset() != timedelta(0):
            raise ValueError("report interval must use UTC")
        if not timedelta(0) < self.to_time - self.from_time <= timedelta(days=31):
            raise ValueError("interval must be positive and at most 31 days")
        if self.from_time.microsecond % 1000 or self.to_time.microsecond % 1000:
            raise ValueError("interval must be millisecond aligned")
        return self


class ActivityLifetimeQuery(Contract):
    """Internal query. Only the server can set these bounds."""

    course_id: Identifier
    from_time: ClassVar[datetime] = datetime(1970, 1, 1, tzinfo=timezone.utc)
    subject_ref: UUID | None = None
    lesson_id: ClassVar[None] = None
    to_time: AwareTime

    @model_validator(mode="after")
    def bounds(self):
        if self.to_time.utcoffset() != timedelta(0) or self.to_time.microsecond % 1000 or self.to_time <= self.from_time:
            raise ValueError("invalid lifetime activity cutoff")
        return self


class ActivityValues(Contract):
    unique_visitors: Count
    active_ms: Count
    theory_ms: Count
    practice_ms: Count

    @model_validator(mode="after")
    def sum_sections(self):
        if self.active_ms != self.theory_ms + self.practice_ms:
            raise ValueError("active time must equal theory plus practice")
        return self

class ActivityDay(ActivityValues):
    date: date


class ActivityLifetimeSnapshot(Contract):
    course_id: Identifier
    status: Literal["ok"] = "ok"
    quality: Literal["complete", "unknown"]
    as_of: AwareTime | None
    totals: ActivityValues


class TrustedProgress(Contract):
    state: Literal["known", "unavailable"]
    completed_lessons: Count | None

    @model_validator(mode="after")
    def known_value(self):
        if (self.state == "known") != (self.completed_lessons is not None):
            raise ValueError("progress value and state disagree")
        return self

class ActivityReport(Contract):
    schema_version: Literal[1] = 1
    status: Literal["ok", "no_data", "stale", "unavailable"]
    quality: Literal["complete", "partial", "unknown"]
    as_of: AwareTime | None
    lag_seconds: Count | None
    timezone: Literal["Europe/Moscow"] = "Europe/Moscow"
    metric_version: Literal["activity-v1"] = "activity-v1"
    course_id: Identifier | None
    from_time: AwareTime
    to_time: AwareTime
    totals: ActivityValues | None
    daily: Annotated[list[ActivityDay], Field(max_length=32)]
    trusted_progress: TrustedProgress | None = None

    @model_validator(mode="after")
    def availability(self):
        if self.status == "unavailable" and (self.quality != "unknown" or self.totals is not None or self.daily):
            raise ValueError("unavailable report cannot carry measurements")
        if self.status == "ok" and self.totals is None:
            raise ValueError("ok requires observed values")
        if self.status == "no_data" and (self.quality != "complete" or self.totals is None or self.as_of is None):
            raise ValueError("no_data requires proven coverage, snapshot and values")
        if self.status == "no_data" and any(self.totals.model_dump().values()):
            raise ValueError("no_data requires zero observations")
        dates = [row.date for row in self.daily]
        if dates != sorted(set(dates)):
            raise ValueError("daily dates must be unique and ordered")
        if self.totals is not None:
            for field in ("active_ms", "theory_ms", "practice_ms"):
                if sum(getattr(row, field) for row in self.daily) != getattr(self.totals, field):
                    raise ValueError("daily time must sum to total")
        return self

class ActivityBreakdownRow(ActivityValues):
    level: Literal["course", "lesson"]
    course_id: Identifier
    lesson_id: Identifier | None

    @model_validator(mode="after")
    def level_identity(self):
        if (self.level == "course") != (self.lesson_id is None):
            raise ValueError("breakdown level and lesson identity disagree")
        return self

class ActivityDetailsReport(Contract):
    report: ActivityReport
    lesson_id: Identifier | None
    rows: Annotated[list[ActivityBreakdownRow], Field(max_length=100)]
    next_offset: Annotated[int, Field(ge=0, le=20000)] | None

class ChapterActivitySelection(Contract):
    chapter_id: Annotated[str, Field(min_length=1, max_length=128)]
    lesson_ids: list[Identifier]


def _validate_activity_selection(selection: list[ChapterActivitySelection]) -> None:
    chapters = [chapter.chapter_id for chapter in selection]
    lessons = [lesson for chapter in selection for lesson in chapter.lesson_ids]
    if len(chapters) != len(set(chapters)):
        raise ValueError("duplicate chapter id")
    if len(lessons) > MAX_SELECTED_LESSONS or len(lessons) != len(set(lessons)):
        raise ValueError(f"lesson membership must be unique and limited to {MAX_SELECTED_LESSONS}")


class ChapterActivityRequest(Contract):
    course_id: Identifier
    from_time: Annotated[AwareTime, Field(strict=False)]
    to_time: Annotated[AwareTime, Field(strict=False)]
    chapters: Annotated[list[ChapterActivitySelection], Field(max_length=100)]

    @model_validator(mode="after")
    def validate_selection(self):
        ActivityQuery(from_time=self.from_time, to_time=self.to_time, course_id=self.course_id, subject_ref=None)
        _validate_activity_selection(self.chapters)
        return self


class LifetimeActivityRequest(Contract):
    course_id: Identifier
    chapters: Annotated[list[ChapterActivitySelection], Field(max_length=100)]

    @model_validator(mode="after")
    def validate_selection(self):
        _validate_activity_selection(self.chapters)
        return self


class StudentLifetimeActivityRequest(LifetimeActivityRequest):
    subject_ref: Annotated[UUID, Field(strict=False)]


class ChapterActivityRow(Contract):
    chapter_id: str
    active_ms: Count | None
    theory_ms: Count | None
    practice_ms: Count | None

class ChapterActivityReport(Contract):
    course_id: Identifier
    from_time: AwareTime
    to_time: AwareTime
    status: Literal["ok", "no_data", "stale", "unavailable"]
    quality: Literal["complete", "partial", "unknown"]
    as_of: AwareTime | None
    chapters: list[ChapterActivityRow]


class OutlineActivityLesson(Contract):
    lesson_id: Identifier
    active_ms: Count
    theory_ms: Count
    practice_ms: Count

    @model_validator(mode="after")
    def sum_sections(self):
        if self.active_ms != self.theory_ms + self.practice_ms:
            raise ValueError("active time must equal theory plus practice")
        return self


class OutlineActivityReport(ChapterActivityReport):
    lessons: Annotated[list[OutlineActivityLesson], Field(
        max_length=MAX_SELECTED_LESSONS,
        description="One row per requested lesson, with observed zero for missing activity.",
    )]
    unmapped_lessons: Annotated[list[OutlineActivityLesson], Field(
        description="Observed course lessons outside the requested outline, ordered by lesson ID.",
    )]
