"""Analytics wire contracts. Validation alone is not authorization or durable acceptance."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

MAX_EVENT_BYTES = 4096
MAX_BATCH_BYTES = 256 * 1024
MAX_BATCH_ITEMS = 100
MAX_INTERVAL_MS = 60_000  # provisional segment cap; idle window is separately 300 seconds
IDLE_SECONDS = 300
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]
AwareTime = Annotated[AwareDatetime, Field(description="Timezone-aware RFC 3339 timestamp")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class BrowserEvent(Contract):
    schema_version: Literal[2]
    event_id: UUID
    type: Literal["course_opened", "activity_segment", "tool_segment"]
    course_id: Identifier
    catalog_version: Identifier
    lesson_id: Identifier | None
    section: Literal["theory", "practice"] | None
    session_id: UUID  # learning session, never the authentication session
    started_at: AwareTime
    ended_at: AwareTime
    last_action_at: AwareTime
    visible: Literal[True]
    focused: Literal[True]
    active_ms: Annotated[int, Field(ge=0, le=MAX_INTERVAL_MS)]
    # Deliberately empty: arbitrary text, URLs, code and PII are not accepted.
    payload: "EmptyPayload | ToolPayload"

    @field_validator("visible", "focused", mode="before")
    @classmethod
    def must_be_true_bool(cls, value):
        if value is not True:
            raise ValueError("must be the boolean true")
        return value

    @model_validator(mode="after")
    def valid_interval(self):
        for stamp in (self.started_at, self.ended_at, self.last_action_at):
            if stamp.utcoffset() is None:
                raise ValueError("timestamps must have timezone offsets")
        delta = self.ended_at - self.started_at
        elapsed_us = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
        elapsed = elapsed_us // 1000
        if elapsed_us % 1000 or elapsed < 0 or elapsed > MAX_INTERVAL_MS or self.active_ms != elapsed:
            raise ValueError("invalid or oversized interval")
        if self.last_action_at > self.ended_at or self.ended_at > self.last_action_at + timedelta(seconds=IDLE_SECONDS):
            raise ValueError("segment exceeds last action idle window")
        if self.type != "tool_segment" and (self.course_id == "platform-tools" or not isinstance(self.payload, EmptyPayload)):
            raise ValueError("invalid legacy event scope or payload")
        if self.type == "tool_segment":
            if (self.lesson_id is not None or self.section is not None or self.active_ms == 0
                    or self.catalog_version != "platform-tools-v1" or not isinstance(self.payload, ToolPayload)):
                raise ValueError("invalid tool segment")
        elif self.type == "course_opened":
            if self.lesson_id is not None or self.section is not None or self.active_ms != 0 or elapsed != 0:
                raise ValueError("course_opened has no lesson, section or duration")
        elif self.lesson_id is None or self.section is None or self.active_ms == 0:
            raise ValueError("activity_segment requires lesson, section and positive active_ms")
        return self


class EmptyPayload(Contract):
    pass


class ToolPayload(Contract):
    tool_id: Literal["workspace", "lessons", "theory", "practice", "interpreter", "account",
                     "account-profile", "account-statistics", "account-history", "account-booking",
                     "account-settings", "account-payments", "account-achievements", "catalog",
                     "catalog-preview", "course-about", "course-personal"]


class BrowserBatch(Contract):
    events: Annotated[list[BrowserEvent], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]


class WireBatch(Contract):
    """Envelope accepts arbitrary JSON items for independent item validation and ACK."""
    events: Annotated[list[Any], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]


class AcceptedEvent(Contract):
    producer_namespace: Identifier
    subject_ref: UUID  # opaque lifecycle identity, derived from verified grant
    source: Literal["browser"]
    event: BrowserEvent
    received_at: AwareTime


class ItemAck(Contract):
    index: Annotated[int, Field(ge=0, lt=MAX_BATCH_ITEMS)]
    event_id: UUID | None
    status: Literal["accepted", "duplicate", "conflict", "rejected", "pending"]
    code: Literal["committed", "same_payload", "payload_conflict", "invalid", "too_large", "unknown_catalog", "unsupported_version", "future_clock", "too_late"]

    @model_validator(mode="after")
    def valid_code(self):
        pairs = {"accepted": {"committed"}, "duplicate": {"same_payload"}, "conflict": {"payload_conflict"},
                 "rejected": {"invalid", "too_large", "unsupported_version", "future_clock", "too_late"}, "pending": {"unknown_catalog"}}
        if self.code not in pairs[self.status]:
            raise ValueError("status and code disagree")
        return self


class BatchAck(Contract):
    items: Annotated[list[ItemAck], Field(min_length=1, max_length=MAX_BATCH_ITEMS)]


class ErrorResponse(Contract):
    code: Literal["invalid_request", "unauthorized", "forbidden", "too_large", "rate_limited", "unavailable"]
    message: Annotated[str, Field(max_length=256)]
    correlation_id: UUID


class JwtHeader(Contract):
    typ: Literal["JWT"]
    alg: Literal["EdDSA"]
    kid: Identifier


class LegacyRosterMember(Contract):
    subject_ref: UUID
    revision: Annotated[int, Field(ge=0, le=(1 << 63) - 1)]
    student: bool


class LegacyAdminRosterV1(Contract):
    version: Literal[1]
    members: Annotated[tuple[LegacyRosterMember, ...], Field(max_length=80)]

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("invalid roster version")
        return value

    @model_validator(mode="after")
    def unique_subjects(self):
        if len({member.subject_ref for member in self.members}) != len(self.members):
            raise ValueError("duplicate roster subject")
        return self


class CourseCompletionRosterV1(LegacyAdminRosterV1):
    course_id: Identifier
    year: Annotated[int, Field(ge=2000, le=2100)] | None
    month: Annotated[int, Field(ge=1, le=12)] | None

    @model_validator(mode="after")
    def validate_period(self):
        if (self.year is None) != (self.month is None):
            raise ValueError("period requires year and month together")
        return self


class GrantClaims(Contract):
    grant_version: Literal[1]
    iss: Identifier
    aud: Literal["analytics"]
    sub: UUID
    iat: Annotated[int, Field(ge=0, description="JWT NumericDate seconds since Unix epoch")]
    nbf: Annotated[int, Field(ge=0, description="JWT NumericDate seconds since Unix epoch")]
    exp: Annotated[int, Field(ge=0, description="JWT NumericDate seconds since Unix epoch")]
    role: Literal["user", "leader", "admin"]
    scope: Literal["collect", "report:read", "report:student"]
    course_ids: Annotated[list[Identifier], Field(max_length=100)]
    subject_refs: Annotated[list[UUID], Field(max_length=1000)]
    global_read: bool
    legacy_admin_roster_v1: LegacyAdminRosterV1 | None = None
    course_completion_roster_v1: CourseCompletionRosterV1 | None = None

    @model_validator(mode="after")
    def validate_shape(self):
        if self.legacy_admin_roster_v1 is not None and (self.role != "admin" or self.scope != "report:read" or not self.global_read):
            raise ValueError("legacy roster requires admin report grant")
        roster = self.course_completion_roster_v1
        if roster is not None:
            if self.scope != "report:read" or self.legacy_admin_roster_v1 is not None or self.role not in {"admin", "leader"}:
                raise ValueError("course roster requires a dedicated staff grant")
            if self.role == "leader" and (self.course_ids != [roster.course_id]
                    or sorted(self.subject_refs) != sorted(member.subject_ref for member in roster.members)):
                raise ValueError("course roster must match leader scope")
        if not self.iat <= self.nbf < self.exp:
            raise ValueError("invalid grant lifetime")
        if self.scope == "report:student":
            if (self.role not in {"leader", "admin"} or self.global_read
                    or len(self.course_ids) != 1 or len(self.subject_refs) != 1):
                raise ValueError("student reports require staff and one explicit course and subject")
        elif self.scope == "collect":
            if self.role != "user" or self.global_read or self.subject_refs or not self.course_ids:
                raise ValueError("collection is restricted to user and granted courses")
        elif self.role == "user":
            if self.global_read or self.subject_refs or not self.course_ids:
                raise ValueError("user reports are restricted to self and granted courses")
        elif self.role == "leader":
            if self.global_read or len(self.course_ids) != 1:
                raise ValueError("leader requires one course and explicit subjects")
        elif not self.global_read or self.course_ids or self.subject_refs:
            raise ValueError("admin report grant requires global read")
        return self


class CatalogLesson(Contract):
    lesson_id: Identifier


class CatalogSnapshot(Contract):
    schema_version: Literal[2]
    kind: Literal["catalog_snapshot"]
    control_id: UUID
    course_id: Identifier
    catalog_version: Identifier
    track_id: Annotated[str, Field(min_length=1, max_length=256)]
    lessons: Annotated[list[CatalogLesson], Field(max_length=10000)]


class ProgressFact(Contract):
    schema_version: Literal[2]
    kind: Literal["progress_fact"]
    control_id: UUID
    subject_ref: UUID
    course_id: Identifier
    catalog_version: Identifier
    lesson_id: Identifier
    task_id: Identifier | None
    task_revision: Identifier | None
    completed: bool
    occurred_at: AwareTime

    @model_validator(mode="after")
    def task_identity_pair(self):
        if (self.task_id is None) != (self.task_revision is None):
            raise ValueError("task_id and task_revision must appear together")
        return self


class DeletionFence(Contract):
    schema_version: Literal[2]
    kind: Literal["deletion_fence"]
    control_id: UUID
    subject_ref: UUID
    epoch: Annotated[int, Field(ge=1)]
    occurred_at: AwareTime


CoreControl = Annotated[CatalogSnapshot | ProgressFact | DeletionFence, Field(discriminator="kind")]
core_control_adapter = TypeAdapter(CoreControl)


class ReportRequest(Contract):
    course_id: Identifier | None
    subject_ref: UUID | None
    from_time: AwareTime
    to_time: AwareTime
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    limit: Annotated[int, Field(ge=1, le=100)] = 100

    @model_validator(mode="after")
    def valid_range(self):
        if self.from_time.utcoffset() is None or self.to_time.utcoffset() is None:
            raise ValueError("timestamps must have timezone offsets")
        if not timedelta(0) < self.to_time - self.from_time <= timedelta(days=366):
            raise ValueError("period must be positive and at most 366 days")
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            if self.timezone != "UTC":
                ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("unknown IANA timezone") from None
        return self


class ReportRow(Contract):
    course_id: Identifier
    subject_ref: UUID
    active_ms: Annotated[int, Field(ge=0)]


class ReportResponse(Contract):
    status: Literal["ok", "no_data", "stale", "unavailable"]
    as_of: AwareTime
    lag_seconds: Annotated[int, Field(ge=0)]
    quality: Literal["complete", "partial", "unknown"]
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    metric_version: Identifier
    projection_version: Identifier
    rows: Annotated[list[ReportRow], Field(max_length=100)]

    @model_validator(mode="after")
    def validate_status(self):
        if self.as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if self.status in ("no_data", "unavailable") and self.rows:
            raise ValueError("no_data/unavailable cannot carry rows")
        if self.status == "ok" and (not self.rows or self.quality != "complete"):
            raise ValueError("ok requires complete data; zero is represented by a zero row")
        if self.status == "unavailable" and self.quality != "unknown":
            raise ValueError("unavailable quality must be unknown")
        return self


def validate_fixture_claims(header: JwtHeader, claims: GrantClaims, *, now: datetime,
                            issuer: str, known_kids: set[str]) -> None:
    """Fixture-only claim checks: caller MUST first verify JWT signature in runtime."""
    if now.utcoffset() is None or header.kid not in known_kids or claims.iss != issuer:
        raise ValueError("unknown key or issuer")
    timestamp = now.timestamp()
    if not claims.nbf <= timestamp < claims.exp or claims.iat > timestamp:
        raise ValueError("grant expired or not yet valid")
    max_ttl = 300 if claims.scope == "collect" else 60
    if claims.exp - claims.iat > max_ttl:
        raise ValueError("grant lifetime exceeds contract")


def authorize_fixture(claims: GrantClaims, *, scope: str, course_id: str,
                      subject_ref: UUID) -> None:
    """Policy oracle only; never call on unverified wire claims in a service."""
    if claims.scope != scope:
        raise ValueError("insufficient scope")
    if claims.role == "admin" and scope == "report:read" and claims.global_read:
        return
    if course_id not in claims.course_ids:
        raise ValueError("course outside grant")
    if scope == "report:student" and subject_ref in claims.subject_refs:
        return
    if claims.role == "leader" and scope == "report:read" and subject_ref in claims.subject_refs:
        return
    if claims.role == "user" and subject_ref == claims.sub:
        return
    raise ValueError("subject outside grant")


def parse_browser_items(raw: bytes) -> list[bytes]:
    """Limit actual UTF-8 batch and individual JSON item bytes before acceptance."""
    if len(raw) > MAX_BATCH_BYTES:
        raise ValueError("batch too large")
    text = raw.decode("utf-8")
    decoder = json.JSONDecoder(parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    # Read every raw array item independently, including its interior whitespace.
    def ws(pos):
        while pos < len(text) and text[pos] in " \t\r\n":
            pos += 1
        return pos
    pos = ws(0)
    if pos >= len(text) or text[pos] != "{":
        raise ValueError("expected batch object")
    pos = ws(pos + 1)
    key, pos = decoder.raw_decode(text, pos)
    pos = ws(pos)
    if key != "events" or pos >= len(text) or text[pos] != ":":
        raise ValueError("expected events field")
    pos = ws(pos + 1)
    if pos >= len(text) or text[pos] != "[":
        raise ValueError("expected events array")
    pos = ws(pos + 1)
    items = []
    while pos < len(text) and text[pos] != "]":
        start = pos
        _, pos = decoder.raw_decode(text, pos)
        items.append(text[start:pos].encode("utf-8"))
        if len(items) > MAX_BATCH_ITEMS:
            raise ValueError("too many events")
        pos = ws(pos)
        if pos < len(text) and text[pos] == ",":
            pos = ws(pos + 1)
        elif pos >= len(text) or text[pos] != "]":
            raise ValueError("invalid events array")
    # Full parse enforces JSON syntax. No second top-level field is permitted.
    envelope = decoder.decode(text)
    if not isinstance(envelope, dict) or set(envelope) != {"events"}:
        raise ValueError("invalid batch envelope")
    pos = ws(pos + 1)
    if pos >= len(text) or text[pos] != "}" or ws(pos + 1) != len(text):
        raise ValueError("invalid batch envelope")
    if not items:
        raise ValueError("empty batch")
    return items


def parse_browser_item(raw: bytes) -> BrowserEvent:
    if len(raw) > MAX_EVENT_BYTES:
        raise ValueError("event too large")
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    json.loads(raw, object_pairs_hook=no_duplicates,
               parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON constant")))
    event = BrowserEvent.model_validate_json(raw)
    # The persisted fact contract can still decode events from the former tab.
    if isinstance(event.payload, ToolPayload) and event.payload.tool_id == "account-history":
        raise ValueError("retired tool")
    return event


def canonical_hash(record: AcceptedEvent) -> str:
    """SHA-256 canonical JSON v1, excludes server received_at; not a dedup store."""
    data = record.model_dump(mode="json", exclude={"received_at"})
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def map_v1_track(track_id: str, explicit_mapping: dict[str, str]) -> str:
    """Resolve a legacy c226 test track only through an explicit mapping."""
    if track_id not in explicit_mapping:
        raise ValueError("missing explicit v1 track mapping")
    product_id = explicit_mapping[track_id]
    return TypeAdapter(Identifier).validate_python(product_id, strict=True)
