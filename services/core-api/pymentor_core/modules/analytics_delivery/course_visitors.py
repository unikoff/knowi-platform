"""Authorize course aggregates and classify observed lifecycles inside Core."""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from secrets import token_hex

from fastapi import HTTPException
from sqlalchemy import text

from pymentor_core.app.async_request import run_request_sql
from pymentor_core.app.wiring import course_analytics_client, role_admin_reads
from pymentor_core.modules.identity.activity_policy import MOSCOW_TZ

def _read_scope(db, principal, course_id, expected_revision=None):
    scope = dict(role_admin_reads().authorize(db, principal, course_id))
    if expected_revision is not None and scope["revision"] != expected_revision:
        raise HTTPException(403, "Course authority changed")
    return scope


def _capture(db, principal, course_id, at, nonce, candidates, expected_revision=None):
    scope = _read_scope(db, principal, course_id, expected_revision)
    if not candidates:
        return scope, []
    # Compare salted pseudonyms inside Core. Only the requested course's
    # actually observed candidates receive a subscription classification.
    subscription = "COALESCE(u.subscription_until > :at, FALSE)"
    if expected_revision is not None:
        # Study time excludes current staff even if they generated facts as learners.
        # Null marks a classified staff subject, distinct from an unknown lifecycle.
        subscription = f"""CASE WHEN u.is_admin OR EXISTS (
               SELECT 1 FROM course_leadership l WHERE l.user_id = u.id
                 AND l.account_ref = a.lifecycle_id AND l.revoked_at IS NULL
           ) THEN NULL ELSE {subscription} END"""
    memberships = db.execute(text(f"""
        WITH subjects AS (
           SELECT encode(sha256(convert_to(:nonce || CAST(a.lifecycle_id AS text), 'UTF8')), 'hex') AS digest,
                  {subscription} AS active
           FROM users u JOIN account_lifecycles a ON a.user_id = u.id
        )
        SELECT digest, active FROM subjects WHERE digest = ANY(CAST(:candidates AS text[]))
    """), {"at": at, "nonce": nonce, "candidates": candidates}).all()
    if {row[0] for row in memberships} != set(candidates) or len(memberships) != len(candidates):
        raise ValueError("unknown course visitor membership")
    return scope, memberships


def _signed_body(sign, principal, course_id, values):
    body = json.dumps(values, separators=(",", ":")).encode()
    return body, sign(subject=principal.account_ref, course_id=course_id, body_sha256=sha256(body).hexdigest())


async def read_course_visits(request, principal, course_id):
    return await _read_course_cohorts(request, principal, course_id)


async def read_course_study_time(request, principal, course_id, *, expected_revision):
    return await _read_course_cohorts(request, principal, course_id, expected_revision=expected_revision)


async def _read_course_cohorts(request, principal, course_id, *, expected_revision=None):
    unavailable = {"status": "unavailable", "quality": "unknown", "daily": []}
    study_time = expected_revision is not None
    signer = getattr(request.app.state, "analytics_grant_signer", None)
    client_attr = "analytics_course_study_time_client" if study_time else "analytics_course_visitors_client"
    client = getattr(request.app.state, client_attr, None)
    endpoint = os.environ.get("PYMENTOR_ANALYTICS_QUERY_URL")
    configured = signer is not None and (client is not None or bool(endpoint))
    if not configured and not study_time:
        return unavailable
    try:
        authorize = lambda db: _read_scope(db, principal, course_id, expected_revision)
        before = await run_request_sql(request, authorize)
        if not configured:
            return unavailable
        if client is None:
            client = course_analytics_client(endpoint, study_time=study_time)
        sign = signer.course_study_time if study_time else signer.course_visitors
        at = datetime.now(timezone.utc)
        nonce = token_hex(16)
        snapshot = {"at": at.isoformat(), "nonce": nonce}
        try:
            body, grant = _signed_body(sign, principal, course_id, {**snapshot, "operation": "discover"})
            candidates = await client.discover(course_id=course_id, body=body, grant=grant)
            capture = lambda db: _capture(db, principal, course_id, at, nonce, candidates, expected_revision)
            scope, memberships = await run_request_sql(request, capture)
            if scope != before:
                return unavailable
            aggregation = {
                **snapshot, "operation": "aggregate",
                "active_subscriptions": [digest for digest, active in memberships if active is True],
                "inactive_subscriptions": [digest for digest, active in memberships if active is False],
            }
            if study_time:
                aggregation["excluded_subjects"] = [digest for digest, active in memberships if active is None]
            body, grant = _signed_body(sign, principal, course_id, aggregation)
            result = await client.read(course_id=course_id, today=at.astimezone(MOSCOW_TZ).date(), body=body, grant=grant)
        except (OSError, ValueError, TimeoutError):
            result = unavailable
        if study_time and result["status"] == "available":
            after, current_memberships = await run_request_sql(request, capture)
            if set(current_memberships) != set(memberships):
                return unavailable
        else:
            after = await run_request_sql(request, authorize)
        return result if before == after else unavailable
    except HTTPException as exc:
        # A revocation also invalidates the encompassing overview request.
        if exc.status_code in {401, 403, 404}:
            raise
        return unavailable
    except (OSError, ValueError, TimeoutError):
        return unavailable
