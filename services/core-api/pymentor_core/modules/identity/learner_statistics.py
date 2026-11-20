"""Locally owned validation of the published Analytics report contract."""
from datetime import date, timedelta
from typing import Annotated, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

Count = Annotated[StrictInt, Field(ge=0)]
Seconds = Annotated[float, Field(ge=0, strict=True, allow_inf_nan=False)]

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Period(Contract):
    days: Literal[7, 30]
    start: date
    end: date
    timezone: Literal["Europe/Moscow"]

class Scope(Contract):
    course_id: str | None
    activity: Literal["platform"]

class Coverage(Contract):
    visits: Literal["legacy_global", "partial"]
    time: Literal["unavailable", "partial", "measured"]
    progress: Literal["core"]

class Summary(Contract):
    active_days_total: Count | None
    current_streak: Count | None
    active_days_period: Count | None
    completed_lessons_period: Count
    active_seconds: Seconds | None
    theory_seconds: Seconds | None
    practice_seconds: Seconds | None

class Day(Contract):
    date: date
    visited: StrictBool | None
    completed_lessons: Count
    active_seconds: Seconds | None
    theory_seconds: Seconds | None
    practice_seconds: Seconds | None

class LearnerReport(Contract):
    schema_version: Literal[1]
    source: Literal["analytics"]
    as_of: AwareDatetime
    period: Period
    scope: Scope
    coverage: Coverage
    summary: Summary
    days: list[Day]

    @model_validator(mode="after")
    def valid_report(self):
        p, s = self.period, self.summary
        if (p.start != p.end - timedelta(days=p.days - 1)
            or [d.date for d in self.days] != [p.start + timedelta(days=i) for i in range(p.days)]
            or (s.active_days_period is not None and (any(d.visited is None for d in self.days)
                or s.active_days_period != sum(d.visited is True for d in self.days)))
            or s.completed_lessons_period != sum(d.completed_lessons for d in self.days)
            or (self.coverage.visits == "partial") != (s.active_days_total is None and s.current_streak is None)
            or (s.active_days_total is None) != (s.current_streak is None)):
            raise ValueError("invalid report")
        return self


class ProjectionValues(Contract):
    unique_visitors: Count
    active_ms: Count
    theory_ms: Count
    practice_ms: Count

    @model_validator(mode="after")
    def valid_values(self):
        if self.active_ms != self.theory_ms + self.practice_ms or self.unique_visitors > 1:
            raise ValueError("invalid self projection")
        if self.active_ms and not self.unique_visitors:
            raise ValueError("missing visitor")
        return self


class ProjectionDay(ProjectionValues):
    date: date


class ProjectionReport(Contract):
    schema_version: Literal[1]
    status: Literal["ok"]
    quality: Literal["unknown", "partial", "complete"]
    as_of: AwareDatetime | None
    lag_seconds: Count | None
    timezone: Literal["Europe/Moscow"]
    metric_version: Literal["activity-v1"]
    course_id: None
    from_time: AwareDatetime
    to_time: AwareDatetime
    totals: ProjectionValues
    daily: list[ProjectionDay] = Field(max_length=31)
    trusted_progress: None

    @model_validator(mode="after")
    def valid_projection(self):
        dates = [day.date for day in self.daily]
        if dates != sorted(set(dates)):
            raise ValueError("invalid daily ordering")
        if self.totals.unique_visitors and self.as_of is None:
            raise ValueError("missing projection timestamp")
        for field in ("active_ms", "theory_ms", "practice_ms"):
            if sum(getattr(day, field) for day in self.daily) != getattr(self.totals, field):
                raise ValueError("invalid projection totals")
        if any(day.unique_visitors > self.totals.unique_visitors for day in self.daily):
            raise ValueError("invalid visitor total")
        return self
