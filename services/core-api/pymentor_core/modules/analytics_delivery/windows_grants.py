"""Window grants and current per-lifecycle authorization in Core."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from pymentor_core.app.async_request import request_principal, run_request_sql
from pymentor_core.app.course_context import enabled_course_ids
from pymentor_core.modules.analytics_delivery.grants import PLATFORM_TOOL_SCOPE, authorize_grant
from pymentor_core.modules.learning.access import get_lesson_access

AUDIENCE = "analytics-windows"
GATEWAY_AUDIENCE = "analytics-windows-gateway"
ATTESTATION_HEADER = "X-Analytics-Windows-Authorization"
MAX_TOKEN_BYTES = 16 * 1024
MAX_SCOPE_FILE_BYTES = 64 * 1024
MAX_KEYS = 16
MAX_CONTEXTS = 32
MAX_BATCH_BYTES = 64 * 1024
MAX_SCOPE_COURSES = 64
MAX_TOOL_SCOPES = 80
MAX_GRANT_SECONDS = 300
TOOL_IDS = frozenset({
    "workspace", "lessons", "theory", "practice", "interpreter", "account",
    "account-profile", "account-statistics", "account-history", "account-booking",
    "account-settings", "account-payments", "account-achievements", "catalog",
    "catalog-preview", "course-about", "course-personal",
})
PLATFORM_TOOLS = frozenset({"catalog", "catalog-preview"})
COURSE_TOOLS = TOOL_IDS - PLATFORM_TOOLS
Identifier = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")]


class WindowHTTPError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message


class WindowGrantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol_version: Literal[1]
    writer_id: UUID
    writer_generation: Annotated[int, Field(ge=1, le=(1 << 53) - 1, strict=True)]


class ToolScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    tool_id: Identifier
    course_id: Identifier | None


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    course_ids: list[Identifier] = Field(max_length=MAX_SCOPE_COURSES)
    tool_scopes: list[ToolScope] = Field(max_length=MAX_TOOL_SCOPES)


@dataclass(frozen=True)
class WindowPolicy:
    cohort: frozenset[str]
    t0: datetime
    scope_file: Path | None
    all_eligible: bool = False

    def permits(self, subject: str) -> bool:
        return bool(subject and (self.all_eligible or subject in self.cohort)
                    and datetime.now(timezone.utc) >= self.t0)

    def scope(self, subject: str) -> Scope:
        try:
            if self.scope_file is None:
                raise ValueError
            with self.scope_file.open("rb") as handle:
                raw = handle.read(MAX_SCOPE_FILE_BYTES + 1)
            if len(raw) > MAX_SCOPE_FILE_BYTES:
                raise ValueError
            parsed = decode_json(raw)
            if not isinstance(parsed, dict) or set(parsed) - self.cohort:
                raise ValueError
            if subject not in parsed:
                raise WindowHTTPError(403, "forbidden", "Access denied")
            scope = Scope.model_validate(parsed[subject])
            courses, tools = set(scope.course_ids), set(scope.tool_scopes)
            if (len(courses) != len(scope.course_ids) or len(tools) != len(scope.tool_scopes)
                    or PLATFORM_TOOL_SCOPE in courses
                    or any(row.tool_id not in TOOL_IDS or
                           (row.course_id is not None and row.course_id not in courses)
                           for row in tools)):
                raise ValueError
            return scope
        except (OSError, ValueError):
            raise WindowHTTPError(503, "unavailable", "Collection scope unavailable") from None


def configure_policy(env) -> WindowPolicy | None:
    enabled = env.get("ANALYTICS_WINDOWS_INTAKE_ENABLED", "0")
    if enabled == "0":
        return None
    try:
        if enabled != "1":
            raise ValueError
        all_eligible = env.get("ANALYTICS_WINDOWS_ALL_ELIGIBLE", "0")
        if all_eligible not in {"0", "1"}:
            raise ValueError
        t0 = datetime.fromisoformat(env["ANALYTICS_WINDOWS_T0"])
        if t0.utcoffset() != timedelta():
            raise ValueError
        if all_eligible == "1":
            if env.get("ANALYTICS_WINDOWS_COHORT") or env.get("ANALYTICS_WINDOWS_SCOPE_FILE"):
                raise ValueError
            return WindowPolicy(frozenset(), t0, None, all_eligible=True)
        cohort = env["ANALYTICS_WINDOWS_COHORT"].split(",")
        if (not 1 <= len(cohort) <= 1000 or len(cohort) != len(set(cohort))
                or any(str(UUID(value)) != value for value in cohort)):
            raise ValueError
        path = Path(env["ANALYTICS_WINDOWS_SCOPE_FILE"])
        if not path.is_absolute() or not path.is_file():
            raise ValueError
    except (KeyError, OSError, ValueError):
        raise ValueError("Invalid Analytics windows policy configuration") from None
    return WindowPolicy(frozenset(cohort), t0, path)


def decode_json(raw: bytes):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("invalid JSON number")

    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid_constant)


def window_clock():
    return datetime.now(timezone.utc)


async def current_scope(request, policy: WindowPolicy):
    try:
        principal = await request_principal(request)
        if not policy.permits(principal.account_ref):
            raise WindowHTTPError(403, "forbidden", "Access denied")
        await run_request_sql(request, lambda db: authorize_grant(
            db, principal, purpose="collect", course_id=PLATFORM_TOOL_SCOPE, target_ref=None,
        ))
        return principal, await run_in_threadpool(enabled_scope, policy, principal.account_ref)
    except HTTPException as exc:
        if exc.status_code in (401, 403):
            code = "unauthorized" if exc.status_code == 401 else "forbidden"
            raise WindowHTTPError(exc.status_code, code, "Access denied") from None
        raise WindowHTTPError(503, "unavailable", "Collection scope unavailable") from None


def enabled_scope(policy: WindowPolicy, subject: str) -> Scope:
    enabled = set(enabled_course_ids()) - {PLATFORM_TOOL_SCOPE}
    if policy.all_eligible:
        courses = sorted(enabled)
        tools = [{"tool_id": tool, "course_id": None} for tool in sorted(PLATFORM_TOOLS)]
        tools.extend({"tool_id": tool, "course_id": course}
                     for course in courses for tool in sorted(COURSE_TOOLS))
        try:
            return Scope.model_validate({"course_ids": courses, "tool_scopes": tools})
        except ValueError:
            raise WindowHTTPError(503, "unavailable", "Collection scope unavailable") from None
    scope = policy.scope(subject)
    courses = sorted(set(scope.course_ids).intersection(enabled))
    tools = [row for row in scope.tool_scopes if row.course_id is None or row.course_id in courses]
    return Scope(course_ids=courses, tool_scopes=tools)


def issue(signer, principal, scope, request: WindowGrantRequest):
    instant = int(time.time())
    expiry = instant + MAX_GRANT_SECONDS
    claims = {
        **request.model_dump(mode="json"), "grant_version": 1,
        "iss": signer.issuer, "aud": AUDIENCE, "sub": principal.account_ref,
        "iat": instant, "nbf": instant, "exp": expiry, "scope": "windows:collect",
        **scope.model_dump(mode="json"),
    }
    token = signer.window_token(claims)
    return {
        **request.model_dump(mode="json"), "grant": token, "subject_ref": principal.account_ref,
        "server_time": window_clock().isoformat(), "expires_at": datetime.fromtimestamp(expiry, timezone.utc).isoformat(),
    }


def batch_permissions(raw: bytes, principal, scope, claims, *, load_lessons):
    """Core attests exactly this body and validates each snapshot as a unit."""
    try:
        parsed = decode_json(raw)
        if not isinstance(parsed, dict) or set(parsed) != {"protocol_version", "windows"}:
            raise ValueError
        if type(parsed["protocol_version"]) is not int or parsed["protocol_version"] != 1:
            raise WindowHTTPError(400, "unsupported_version", "Window protocol unsupported")
        windows = parsed["windows"]
        if not isinstance(windows, list) or not 1 <= len(windows) <= MAX_KEYS:
            raise ValueError
    except (UnicodeError, RecursionError, ValueError):
        raise WindowHTTPError(400, "invalid_request", "Invalid window batch") from None
    courses = set(scope.course_ids).intersection(claims["course_ids"])
    tools = {(row.tool_id, row.course_id) for row in scope.tool_scopes}
    catalogs = {}
    output = []
    for item in windows:
        rows = item.get("stats") if isinstance(item, dict) else None
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CONTEXTS:
            output.append(None)
            continue
        code = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            course, lesson, tool = row.get("course_id"), row.get("lesson_id"), row.get("tool_id")
            if (any(value is not None and not isinstance(value, str) for value in (course, lesson, tool))
                    or tool is None):
                continue
            if course is not None and course not in courses:
                code = "course_forbidden"
                break
            if lesson is None:
                if (tool, course) not in tools:
                    code = "tool_forbidden"
                    break
                continue
            if course is None:
                continue
            if course not in catalogs:
                try:
                    catalogs[course] = {entry["id"]: entry for entry in load_lessons(course)}
                except (OSError, ValueError, LookupError, TypeError):
                    raise WindowHTTPError(503, "unavailable", "Course access unavailable") from None
            entry = catalogs[course].get(lesson)
            if entry is None or not get_lesson_access(entry, principal)[0]:
                code = "course_forbidden"
                break
        output.append(code)
    return output
