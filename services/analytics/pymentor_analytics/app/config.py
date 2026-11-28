"""Strict, independent process settings; never log connection strings."""
from datetime import datetime, timedelta
import os
from pathlib import Path
import json
import re
from typing import Mapping
from urllib.parse import unquote, urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pymentor_analytics.contracts.work_v1 import SYNTHETIC_SUBSCRIPTIONS


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    database_url: SecretStr
    pool_size: int = Field(default=2, ge=1, le=20)
    pool_overflow: int = Field(default=0, ge=0, le=10)
    db_timeout_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)
    concurrency: int = Field(default=4, ge=1, le=100)
    shutdown_timeout_seconds: int = Field(default=10, ge=1, le=60)
    real_collection: bool = False
    issuer_mode: str = "synthetic"
    host_boot_recovery_gate: bool = False
    grant_issuer: str | None = None
    grant_keys_json: SecretStr | None = None
    producer_issuer: str | None = None
    producer_keys_json: SecretStr | None = None
    producer_allowlist_json: SecretStr | None = None
    trust_manifest_json: SecretStr | None = None
    privacy_authority_json: SecretStr | None = None
    core_deletion_source_json: SecretStr | None = None
    core_deletion_status_database_url: SecretStr | None = None
    core_deletion_relay_json: SecretStr | None = None
    fixture_catalog_json: SecretStr | None = None
    core_transfer_enabled: bool = False
    core_transfer_proof_enabled: bool = False
    core_transfer_namespaces_json: SecretStr | None = None
    core_transfer_status_database_url: SecretStr | None = None
    core_transfer_namespace: str | None = None
    core_transfer_roster_json: SecretStr | None = None
    catalog_enabled: bool = False
    catalog_namespace: str | None = None
    windows_intake_enabled: bool = False
    windows_reconciliation_enabled: bool = False
    windows_reconciliation_proof_file: Path | None = None
    windows_all_eligible: bool = False
    windows_cohort: frozenset[str] = frozenset()
    windows_t0: datetime | None = None
    activity_projection_enabled: bool = False
    activity_projection_run_id: UUID | None = None
    activity_day_worker_enabled: bool = False
    activity_course_worker_enabled: bool = False
    activity_boundary_credit_enabled: bool = False
    activity_day_query_enabled: bool = False
    activity_course_query_enabled: bool = False
    processor_bucket_worker_enabled: bool = True
    processor_generic_worker_enabled: bool = True
    processor_max_inflight: int = Field(default=2, ge=1, le=2)
    processor_activity_poll_seconds: float = Field(default=0.5, ge=0.05, le=0.5, allow_inf_nan=False)
    processor_poll_min_seconds: float = Field(default=0.25, ge=0.05, le=5, allow_inf_nan=False)
    processor_poll_max_seconds: float = Field(default=5, ge=0.1, le=30, allow_inf_nan=False)
    processor_job_timeout_seconds: float = Field(default=10, ge=0.1, le=60, allow_inf_nan=False)
    processor_consumers: str = "synthetic.activity,synthetic.independent"

    @field_validator("windows_cohort", mode="before")
    @classmethod
    def safe_windows_cohort(cls, value):
        entries = value.split(",") if isinstance(value, str) and value else value or ()
        if (len(entries) > 1000 or len(entries) != len(set(entries))
                or any(str(UUID(entry)) != entry for entry in entries)):
            raise ValueError("invalid windows cohort")
        return frozenset(entries)

    @field_validator("windows_reconciliation_proof_file")
    @classmethod
    def safe_windows_proof_path(cls, value):
        if value is not None and not value.is_absolute():
            raise ValueError("window reconciliation proof requires an absolute operator path")
        return value

    @model_validator(mode="after")
    def safe_windows_flags(self):
        if self.windows_all_eligible and self.windows_cohort:
            raise ValueError("windows require exactly one audience")
        if (self.windows_intake_enabled or self.windows_reconciliation_enabled
                or self.windows_all_eligible or self.windows_cohort or self.windows_t0 is not None):
            if (not (self.windows_all_eligible or self.windows_cohort)
                    or self.windows_t0 is None or self.windows_t0.utcoffset() != timedelta()):
                raise ValueError("windows require an audience and UTC observation start")
        return self

    @field_validator("processor_consumers")
    @classmethod
    def safe_processor_consumers(cls, value: str) -> str:
        allowed = {subscription.consumer for subscription in SYNTHETIC_SUBSCRIPTIONS}
        entries = value.split(",")
        if not entries or len(entries) > 2 or len(set(entries)) != len(entries) or not set(entries) <= allowed:
            raise ValueError("invalid processor consumers")
        return value

    @field_validator("database_url")
    @classmethod
    def safe_database(cls, value: SecretStr) -> SecretStr:
        try:
            parsed = urlsplit(value.get_secret_value())
            port = parsed.port
            name = unquote(parsed.path.lstrip("/"))
            benchmark_name = re.fullmatch(r"analytics_test_[0-9a-f]{32}", name)
            benchmark_user = unquote(parsed.username or "")
            benchmark_allowed = (parsed.hostname == "127.0.0.1" and port in (62332,62333) and benchmark_name
                                 and benchmark_user in {name + "_" + role for role in
                                     ("migrate", "collector", "processor", "query", "privacy", "transfer_status")})
            if (parsed.scheme not in {"postgresql", "postgresql+asyncpg"}
                    or not parsed.hostname or not parsed.username
                    or not port or not name.startswith("analytics_")
                    or len(name) <= len("analytics_")
                    or not name.replace("_", "").isalnum()
                    or parsed.query or parsed.fragment
                    or port == 57382
                    or (parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"} and port != 57392 and not benchmark_allowed)
                    or (port in (62332,62333) and not benchmark_allowed)):
                raise ValueError
        except ValueError:
            raise ValueError("invalid or unsafe Analytics database URL") from None
        return value

    @field_validator("core_transfer_status_database_url")
    @classmethod
    def safe_status_database(cls, value):
        return cls.safe_database(value) if value is not None else None

    @field_validator("core_deletion_status_database_url")
    @classmethod
    def safe_deletion_status_database(cls, value):
        return cls.safe_database(value) if value is not None else None

    @model_validator(mode="after")
    def synthetic_only(self):
        if self.real_collection and self.issuer_mode != "production":
            parsed = urlsplit(self.database_url.get_secret_value())
            name = unquote(parsed.path.lstrip("/"))
            if (self.issuer_mode != "local" or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                    or parsed.port != 57392 or not re.fullmatch(r"analytics_live_[0-9a-f]{32}", name)
                    or self.fixture_catalog_json is not None or self.trust_manifest_json is None
                    or self.privacy_authority_json is None or self.core_deletion_source_json is None):
                raise ValueError("invalid local real collection configuration")
            try:
                authority = _json_unique(self.privacy_authority_json)
                if authority.get("fixture_mode") is not False or authority.get("runtime_mode") != "local":
                    raise ValueError
                from pymentor_analytics.adapters.core_privacy.client import SourceConfig
                SourceConfig.from_json(self.core_deletion_source_json.get_secret_value())
            except Exception:
                raise ValueError("invalid local real collection configuration") from None
        elif self.issuer_mode == "production":
            try:
                if (self.trust_manifest_json is None or self.privacy_authority_json is None
                        or self.fixture_catalog_json is not None or self.core_deletion_source_json is None):
                    raise ValueError
                from pymentor_analytics.adapters.privacy_authority.client import AuthorityConfig
                authority = AuthorityConfig.from_json(self.privacy_authority_json.get_secret_value())
                from pymentor_analytics.adapters.core_privacy.client import SourceConfig
                source = SourceConfig.from_json(self.core_deletion_source_json.get_secret_value())
                if authority.runtime_mode != "production" or source.runtime_mode != "production":
                    raise ValueError
                if self.real_collection and not self.catalog_enabled:
                    raise ValueError
            except Exception:
                raise ValueError("invalid production authority configuration") from None
        elif (unquote(urlsplit(self.database_url.get_secret_value()).path.lstrip("/")).startswith("analytics_live_")
              or self.issuer_mode != "synthetic" or self.core_deletion_source_json is not None
              or self.core_deletion_status_database_url is not None or self.core_deletion_relay_json is not None):
            raise ValueError("only synthetic mode is supported")
        if self.processor_poll_min_seconds > self.processor_poll_max_seconds:
            raise ValueError("invalid processor poll bounds")
        return self


def _json_unique(secret):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate configuration key")
            result[key] = value
        return result
    raw = secret.get_secret_value()
    if len(raw.encode("utf-8")) > 32 * 1024:
        raise ValueError("configuration exceeds bound")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def _producer_namespaces(settings):
    if settings.core_transfer_namespaces_json is None:
        raise ValueError
    namespaces = _json_unique(settings.core_transfer_namespaces_json)
    pattern = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
    if (not isinstance(namespaces, dict) or not 1 <= len(namespaces) <= 100
            or any(not isinstance(k, str) or not isinstance(v, str) or not re.fullmatch(pattern, k)
                   or not re.fullmatch(pattern, v) or v.startswith("browser:") for k, v in namespaces.items())
            or len(set(namespaces.values())) != len(namespaces)):
        raise ValueError
    return namespaces


def core_transfer_config(settings, role):
    """Validate role-specific options without ever including secrets in errors."""
    try:
        if settings.core_transfer_proof_enabled and (not settings.core_transfer_enabled or role not in {"collector", "processor", "query"}):
            raise ValueError
        options = (settings.core_transfer_namespaces_json, settings.core_transfer_status_database_url,
                   settings.core_transfer_namespace, settings.core_transfer_roster_json)
        if not settings.core_transfer_enabled:
            if any(value is not None for value in options[1:]) or (options[0] is not None and not (role == "collector" and settings.catalog_enabled)):
                raise ValueError
            return None
        if role not in {"collector", "processor", "query"} or settings.trust_manifest_json is None or settings.privacy_authority_json is None or settings.db_timeout_seconds < .1:
            raise ValueError
        pattern = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}"
        if role == "collector":
            if settings.core_transfer_namespaces_json is None or settings.core_transfer_status_database_url is None or settings.core_transfer_namespace is not None or settings.core_transfer_roster_json is not None:
                raise ValueError
            namespaces = _producer_namespaces(settings)
            source, target = (urlsplit(value.get_secret_value()) for value in (settings.database_url, settings.core_transfer_status_database_url))
            database = unquote(source.path.lstrip("/"))
            if (source.hostname != target.hostname or source.port != target.port or source.path != target.path
                    or unquote(target.username or "") != database + "_transfer_status"):
                raise ValueError
            return namespaces
        if settings.core_transfer_namespaces_json is not None or settings.core_transfer_status_database_url is not None:
            raise ValueError
        if role == "processor":
            if settings.core_transfer_namespace is not None or settings.core_transfer_roster_json is not None:
                raise ValueError
            return True
        namespace = settings.core_transfer_namespace
        if not isinstance(namespace, str) or not re.fullmatch(pattern, namespace) or namespace.startswith("browser:"):
            raise ValueError
        roster = None
        if settings.core_transfer_roster_json is not None:
            roster = _json_unique(settings.core_transfer_roster_json)
            if (not isinstance(roster, dict) or set(roster) != {"complete", "expires_at", "subjects", "students"}
                    or roster["complete"] is not True or type(roster["expires_at"]) is not int or roster["expires_at"] < 1
                    or not isinstance(roster["subjects"], list) or not isinstance(roster["students"], list)
                    or len(roster["subjects"]) > 200):
                raise ValueError
            subjects = [UUID(item) for item in roster["subjects"]]
            students = [UUID(item) for item in roster["students"]]
            if (any(str(v) != raw for v, raw in zip(subjects, roster["subjects"]))
                    or any(str(v) != raw for v, raw in zip(students, roster["students"]))
                    or len(set(subjects)) != len(subjects) or len(set(students)) != len(students)
                    or not set(students) <= set(subjects)):
                raise ValueError
            roster = {"expires_at": roster["expires_at"], "subjects": tuple(subjects), "students": frozenset(str(v) for v in students)}
        return {"namespace": namespace, "roster": roster}
    except Exception:
        raise ValueError("invalid Core transfer configuration") from None


def catalog_config(settings, role):
    try:
        if not settings.catalog_enabled:
            if settings.catalog_namespace is not None:
                raise ValueError
            return None
        if (role != "collector" or settings.trust_manifest_json is None or settings.privacy_authority_json is None
                or settings.fixture_catalog_json is not None or settings.db_timeout_seconds < .1):
            raise ValueError
        namespaces = _producer_namespaces(settings)
        selected = {producer: namespace for producer, namespace in namespaces.items() if namespace == settings.catalog_namespace}
        if not selected:
            raise ValueError
        return {"namespace": settings.catalog_namespace, "mapping": selected}
    except Exception:
        raise ValueError("invalid catalog configuration") from None


def activity_projection_config(settings, role):
    if role == "processor" and settings.issuer_mode == "production" and (
        settings.processor_generic_worker_enabled
        or (settings.processor_bucket_worker_enabled and not settings.activity_projection_enabled)
    ):
        raise ValueError("production may run only the configured activity projection worker")
    if settings.activity_boundary_credit_enabled and not (role == "processor" and settings.activity_course_worker_enabled):
        raise ValueError("boundary credit requires the processor course worker")
    if settings.activity_course_worker_enabled and not (role == "processor" and settings.activity_day_worker_enabled):
        raise ValueError("activity course worker requires the processor day worker")
    if settings.activity_course_query_enabled and not (role == "query" and settings.activity_day_query_enabled):
        raise ValueError("activity course query requires the day query projection")
    if ((settings.activity_day_worker_enabled and role != "processor")
            or (settings.activity_day_query_enabled and role != "query")
            or ((settings.activity_day_worker_enabled or settings.activity_day_query_enabled)
                and not settings.activity_projection_enabled)):
        raise ValueError("invalid activity day projection configuration")
    if not settings.activity_projection_enabled:
        return None
    if (role not in {"processor", "query"} or settings.activity_projection_run_id is None
            or settings.trust_manifest_json is None or settings.privacy_authority_json is None
            or settings.db_timeout_seconds < .1):
        raise ValueError("invalid activity projection configuration")
    return settings.activity_projection_run_id


def local_deletion_config(settings, role):
    if not settings.real_collection and settings.issuer_mode != "production":
        return None
    if role not in {"collector", "processor", "query", "privacy"}:
        raise ValueError("invalid local deletion configuration")
    if role in ({"collector", "processor", "query"} if settings.issuer_mode == "production" else {"collector", "query"}):
        if settings.core_deletion_status_database_url is None or settings.core_deletion_relay_json is not None:
            raise ValueError("invalid local deletion configuration")
        source = urlsplit(settings.database_url.get_secret_value())
        status = urlsplit(settings.core_deletion_status_database_url.get_secret_value())
        name = unquote(source.path.lstrip("/"))
        if (source.hostname != status.hostname or source.port != status.port or source.path != status.path
                or unquote(status.username or "") != name + "_transfer_status"):
            raise ValueError("invalid local deletion configuration")
        if settings.issuer_mode == "local" and role == "query" and not (settings.activity_projection_enabled and settings.activity_day_query_enabled):
            raise ValueError("local query requires day projection")
    elif role == "privacy":
        if settings.core_deletion_status_database_url is not None or settings.core_deletion_relay_json is None:
            raise ValueError("invalid local deletion configuration")
    elif (settings.core_deletion_status_database_url is not None or settings.core_deletion_relay_json is not None
          or settings.processor_generic_worker_enabled):
        raise ValueError("invalid local deletion configuration")
    from pymentor_analytics.adapters.core_privacy.client import SourceConfig
    return SourceConfig.from_json(settings.core_deletion_source_json.get_secret_value())


ROLES = ("collector", "processor", "query", "privacy", "migrate")


def load_settings(role: str, environ: Mapping[str, str] | None = None) -> Settings:
    if role not in ROLES:
        raise ValueError("unknown role")
    env = os.environ if environ is None else environ
    prefix = f"ANALYTICS_{role.upper()}_"
    if role != "processor" and any(prefix + name.upper() in env for name in Settings.model_fields if name.startswith("processor_")):
        raise ValueError("processor settings belong to processor")
    fields = {}
    for name in Settings.model_fields:
        key = prefix + (name.removeprefix("processor_") if role == "processor" else name).upper()
        if key in env:
            fields[name] = env[key]
    for name in ("windows_intake_enabled", "windows_reconciliation_enabled",
                 "windows_all_eligible", "windows_cohort", "windows_t0"):
        if prefix + name.upper() in env:
            raise ValueError("windows configuration must be shared")
        key = "ANALYTICS_" + name.upper()
        if key in env and (name != "windows_t0" or env[key]):
            if (name.endswith("_enabled") or name == "windows_all_eligible") and env[key] not in {"0", "1"}:
                raise ValueError("invalid windows feature flag")
            fields[name] = env[key]
    if "ANALYTICS_WINDOWS_RECONCILIATION_PROOF_FILE" in env:
        if role != "processor":
            raise ValueError("window reconciliation proof belongs only to the processor")
        fields["windows_reconciliation_proof_file"] = env["ANALYTICS_WINDOWS_RECONCILIATION_PROOF_FILE"]
    # One deployment-wide source identity; credentials are never exposed in errors.
    if prefix + "CORE_DELETION_SOURCE_JSON" in env:
        raise ValueError("Core deletion source configuration must be shared")
    if "ANALYTICS_CORE_DELETION_SOURCE_JSON" in env:
        fields["core_deletion_source_json"] = env["ANALYTICS_CORE_DELETION_SOURCE_JSON"]
    # One shared deployment input, never role-specific independent trust rings.
    if prefix + "TRUST_MANIFEST_JSON" in env:
        raise ValueError("invalid authentication configuration")
    if "ANALYTICS_TRUST_MANIFEST_JSON" in env:
        fields["trust_manifest_json"] = env["ANALYTICS_TRUST_MANIFEST_JSON"]
    if prefix + "PRIVACY_AUTHORITY_JSON" in env:
        raise ValueError("authority configuration must be shared")
    if "ANALYTICS_PRIVACY_AUTHORITY_JSON" in env:
        fields["privacy_authority_json"] = env["ANALYTICS_PRIVACY_AUTHORITY_JSON"]
    if prefix + "FIXTURE_CATALOG_JSON" in env:
        raise ValueError("fixture catalog configuration must be shared")
    if "ANALYTICS_FIXTURE_CATALOG_JSON" in env:
        fields["fixture_catalog_json"] = env["ANALYTICS_FIXTURE_CATALOG_JSON"]
    if prefix + "ACTIVITY_PROJECTION_RUN_ID" in env:
        raise ValueError("activity projection dataset identity must be shared")
    if "ANALYTICS_ACTIVITY_PROJECTION_RUN_ID" in env:
        fields["activity_projection_run_id"] = env["ANALYTICS_ACTIVITY_PROJECTION_RUN_ID"]
    settings=Settings.model_validate(fields)
    if settings.host_boot_recovery_gate and (settings.issuer_mode != "production" or role == "migrate"):
        raise ValueError("host-boot recovery gate belongs to the production runtime")
    if settings.real_collection and settings.issuer_mode == "production" and role != "collector":
        raise ValueError("production browser collection belongs to collector")
    if role == "processor" and settings.issuer_mode == "production":
        activity_projection_config(settings, role)
    parsed=urlsplit(settings.database_url.get_secret_value())
    if parsed.port in (62332,62333) and unquote(parsed.username or "") != unquote(parsed.path.lstrip("/"))+"_"+role:
        raise ValueError("invalid synthetic benchmark database role")
    return settings
