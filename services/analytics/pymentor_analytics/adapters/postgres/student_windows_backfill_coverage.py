"""Pinned read-only Core source and independent coverage witness for historical verification."""
from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_address
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.engine import make_url

from pymentor_analytics.adapters.postgres.student_windows_backfill import BackfillError

CORE_QUERIES = {
    "user_activity_days": """SELECT d.user_id,d.activity_date,d.visited_at,l.lifecycle_id
        FROM public.user_activity_days d LEFT JOIN public.account_lifecycles l ON l.user_id=d.user_id ORDER BY d.id""",
    "user_lesson_progress": """SELECT d.user_id,d.course_id,d.lesson_id,d.completed_at,l.lifecycle_id
        FROM public.user_lesson_progress d LEFT JOIN public.account_lifecycles l ON l.user_id=d.user_id ORDER BY d.id""",
    "user_task_progress": """SELECT d.user_id,d.course_id,d.task_id,d.task_revision,d.completed_at,l.lifecycle_id
        FROM public.user_task_progress d LEFT JOIN public.account_lifecycles l ON l.user_id=d.user_id ORDER BY d.id""",
}
CORE_COLUMNS = {
    "user_activity_days": ("id", "user_id", "activity_date", "visited_at"),
    "user_lesson_progress": ("id", "user_id", "course_id", "lesson_id", "completed_at"),
    "user_task_progress": ("id", "user_id", "course_id", "task_id", "task_revision", "completed_at"),
    "account_lifecycles": ("user_id", "lifecycle_id"),
}

SqlIdentity = Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(ge=0)]
Port = Annotated[int, Field(ge=1, le=65535)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CoreTarget(Model):
    host: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")]
    port: Port
    server_port: Port
    server_addresses: Annotated[tuple[str, ...], Field(min_length=1, max_length=16)]
    database: SqlIdentity
    owner: SqlIdentity
    reader: SqlIdentity
    standby: bool

    @model_validator(mode="after")
    def exact_target(self):
        if self.reader == self.owner or any(str(ip_address(value)) != value for value in self.server_addresses):
            raise ValueError("Core scope must identify exact endpoints and a distinct reader")
        return self


class Measure(Model):
    count: Count
    active_ms: Literal[0]
    sha256: Digest


class CoreMeasure(Measure):
    mapped: Count
    unmapped: Count

    @model_validator(mode="after")
    def covers_every_row(self):
        if self.count != self.mapped + self.unmapped:
            raise ValueError("Core lifecycle resolution must cover the complete source")
        return self


class CoverageWitness(Model):
    version: Literal[1]
    run_id: UUID
    t0: datetime
    evidence_sha256: Digest
    core_target: CoreTarget
    core_sources: dict[str, CoreMeasure]
    analytics_sources: dict[str, Measure]

    @model_validator(mode="after")
    def complete(self):
        if self.t0.utcoffset() is None or set(self.core_sources) != set(CORE_QUERIES):
            raise ValueError("Core witness must cover all three direct sources at a named R1 cut")
        if not {"activity_credit_days", "activity_credit_windows"} <= set(self.analytics_sources):
            raise ValueError("Analytics witness must include the independent credit sources")
        return self


@dataclass(frozen=True)
class CoreObservation:
    target: CoreTarget
    identity: dict
    snapshot: str
    capture_timestamp: datetime
    sources: dict[str, CoreMeasure]
    transfer_comparison: dict[str, int] | None

    def report(self):
        return dict(identity=self.identity, snapshot=self.snapshot, capture_timestamp=self.capture_timestamp,
                    transfer_comparison=self.transfer_comparison,
                    **{name: measure.model_dump() for name, measure in sorted(self.sources.items())})


def check_core_url(dsn, target):
    url = make_url(dsn)
    if (url.drivername not in {"postgresql", "postgresql+psycopg"} or not url.password or url.query
            or (url.host, url.port, url.database, url.username)
            != (target.host, target.port, target.database, target.reader)):
        raise BackfillError("Core DSN does not match the independently pinned reader endpoint")
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


def inspect_core(connection, target, *, require_tls=False):
    actual = connection.execute("""SELECT current_database(),current_user,session_user,
            (SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()),
            host(inet_server_addr()),inet_server_port(),pg_is_in_recovery(),current_setting('transaction_read_only'),
            (SELECT rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls
                FROM pg_roles WHERE rolname=current_user),
            EXISTS (SELECT FROM pg_roles WHERE
                (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls
                 OR rolname IN ('pg_write_all_data','pg_read_all_data'))
                AND pg_has_role(oid,'MEMBER')),
            has_schema_privilege('public','CREATE'),
            (SELECT pg_has_role(datdba,'MEMBER') FROM pg_database WHERE datname=current_database()),
            COALESCE((SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),false)
    """).fetchone()
    if (actual is None or actual[:4] != (target.database, target.reader, target.reader, target.owner)
            or actual[4] not in target.server_addresses or actual[5:8] != (target.server_port, target.standby, "on")
            or any(actual[8:12]) or (require_tls and actual[12] is not True)):
        raise BackfillError("actual Core database, endpoint, role, mode or privilege differs from the pinned reader")
    for table, columns in CORE_COLUMNS.items():
        relation = f"public.{table}"
        privileges = connection.execute("""WITH source AS (SELECT to_regclass(%s) AS relation)
          SELECT EXISTS (SELECT FROM pg_class c WHERE c.oid=source.relation AND c.relkind IN ('r','p')
                AND NOT pg_has_role(c.relowner,'MEMBER')),
            (SELECT bool_and(has_column_privilege(source.relation, expected.name, 'SELECT'))
              FROM unnest(%s::text[]) expected(name)),
            has_table_privilege(source.relation,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'),
            has_any_column_privilege(source.relation,'INSERT,UPDATE,REFERENCES'),
            EXISTS (SELECT FROM pg_attribute a
                WHERE a.attrelid=source.relation AND a.attnum>0 AND NOT a.attisdropped
                  AND a.attname::text <> ALL(%s) AND has_column_privilege(a.attrelid,a.attnum,'SELECT'))
          FROM source
        """, (relation, list(columns), list(columns))).fetchone()
        if privileges != (True, True, False, False, False):
            raise BackfillError("Core reader source grants or schema differ from the scoped read-only contract")
    extended_access = connection.execute("""SELECT EXISTS (SELECT FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')
          AND c.relname::text <> ALL(%s) AND (
              has_table_privilege(c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
              OR has_any_column_privilege(c.oid,'SELECT,INSERT,UPDATE,REFERENCES')))""",
        (list(CORE_QUERIES) + ["account_lifecycles"],)).fetchone()
    if extended_access != (False,):
        raise BackfillError("Core reader has access outside its historical source relations")
    return dict(database=actual[0], reader=actual[1], owner=actual[3], server_address=actual[4],
                server_port=actual[5], standby=actual[6], read_only=True, tls=actual[12])
