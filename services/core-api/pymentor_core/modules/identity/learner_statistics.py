"""Locally owned validation of the published Analytics report contract."""
from datetime import date, timedelta
from typing import Annotated, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

Count = Annotated[StrictInt, Field(ge=0)]

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
    time: Literal["unavailable"]
    progress: Literal["core"]

class Summary(Contract):
    active_days_total: Count | None
    current_streak: Count | None
    active_days_period: Count
    completed_lessons_period: Count
    active_seconds: None
    theory_seconds: None
    practice_seconds: None

class Day(Contract):
    date: date
    visited: StrictBool
    completed_lessons: Count
    active_seconds: None
    theory_seconds: None
    practice_seconds: None

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
            or s.active_days_period != sum(d.visited for d in self.days)
            or s.completed_lessons_period != sum(d.completed_lessons for d in self.days)
            or (self.coverage.visits == "partial") != (s.active_days_total is None and s.current_streak is None)
            or (s.active_days_total is None) != (s.current_streak is None)):
            raise ValueError("invalid report")
        return self
