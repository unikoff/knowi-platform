"""Private Core-to-Query contract. Neither request nor response carries account IDs."""
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from pymentor_analytics.contracts.v2 import Contract, Identifier

MOSCOW = timezone(timedelta(hours=3))
MAX_MEMBERSHIPS = 1000
MAX_BODY_BYTES = 70_000
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
NumericDate = Annotated[int, Field(gt=0)]
ObservedCount = Annotated[int, Field(gt=0)]


class CourseAggregateGrant(Contract):
    grant_version: Literal[1]
    iss: Identifier
    sub: UUID
    iat: NumericDate
    exp: NumericDate
    nbf: NumericDate
    course_id: Identifier
    body_sha256: Digest

    @model_validator(mode="after")
    def short_lived(self):
        if self.nbf != self.iat or not 0 < self.exp - self.iat <= 15:
            raise ValueError("invalid course visitor credential lifetime")
        return self


class CourseVisitorGrant(CourseAggregateGrant):
    aud: Literal["analytics-course-visitors"]


class CourseStudyTimeGrant(CourseAggregateGrant):
    aud: Literal["analytics-course-study-time"]


class CourseVisitorSnapshot(Contract):
    at: datetime
    nonce: Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]

    @model_validator(mode="after")
    def utc_snapshot(self):
        if self.at.utcoffset() != timedelta(0):
            raise ValueError("invalid course visitor snapshot time")
        return self


class CourseVisitorDiscovery(CourseVisitorSnapshot):
    operation: Literal["discover"]


class CourseVisitorAggregation(CourseVisitorSnapshot):
    operation: Literal["aggregate"]
    active_subscriptions: Annotated[list[Digest], Field(max_length=MAX_MEMBERSHIPS)]
    inactive_subscriptions: Annotated[list[Digest], Field(max_length=MAX_MEMBERSHIPS)]

    @model_validator(mode="after")
    def scoped_snapshot(self):
        memberships = self.memberships()
        if len(memberships) > MAX_MEMBERSHIPS or len(set(memberships)) != len(memberships):
            raise ValueError("invalid course visitor memberships")
        return self

    def memberships(self):
        return self.active_subscriptions + self.inactive_subscriptions


CourseVisitorRequest = Annotated[CourseVisitorDiscovery | CourseVisitorAggregation, Field(discriminator="operation")]


class CourseStudyTimeAggregation(CourseVisitorAggregation):
    excluded_subjects: Annotated[list[Digest], Field(max_length=MAX_MEMBERSHIPS)]

    def memberships(self):
        return super().memberships() + self.excluded_subjects


CourseStudyTimeRequest = Annotated[CourseVisitorDiscovery | CourseStudyTimeAggregation, Field(discriminator="operation")]


class CourseVisitorCandidates(Contract):
    course_id: Identifier
    subject_digests: Annotated[list[Digest], Field(max_length=MAX_MEMBERSHIPS)]


class CourseVisitorDay(Contract):
    date: date
    with_subscription: ObservedCount | None
    without_subscription: ObservedCount | None


class CourseVisitorReport(Contract):
    course_id: Identifier
    quality: Literal["unknown"] = "unknown"
    daily: Annotated[list[CourseVisitorDay], Field(min_length=30, max_length=30)]


class CourseStudyTimeCohort(Contract):
    active_ms: Annotated[int, Field(gt=0, le=MAX_MEMBERSHIPS * 86_400_000)]
    learners: Annotated[int, Field(gt=0, le=MAX_MEMBERSHIPS)]

    @model_validator(mode="after")
    def bounded_duration(self):
        if not self.learners <= self.active_ms <= self.learners * 86_400_000:
            raise ValueError("invalid duration per active learner")
        return self


class CourseStudyTimeDay(Contract):
    date: date
    with_subscription: CourseStudyTimeCohort | None
    without_subscription: CourseStudyTimeCohort | None


class CourseStudyTimeReport(Contract):
    course_id: Identifier
    quality: Literal["unknown"] = "unknown"
    daily: Annotated[list[CourseStudyTimeDay], Field(min_length=30, max_length=30)]
