"""Expose only publication-backed, learner-accessible Analytics identities."""
from fastapi import APIRouter, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from pymentor_core.app.analytics_catalog import read_production_catalog, read_published_catalog
from pymentor_core.app.async_request import request_principal, run_request_sql
from pymentor_core.modules.analytics_delivery.grants import authorize_grant
from pymentor_core.modules.learning.access import get_lesson_access

analytics_catalog_router = APIRouter()


@analytics_catalog_router.get("/learning/analytics-catalog")
async def analytics_catalog(request: Request, response: Response, course_id: str = Query(min_length=1, max_length=128)):
    response.headers["Cache-Control"] = "no-store"
    if len(request.query_params.getlist("course_id")) != 1 or set(request.query_params) != {"course_id"}:
        raise HTTPException(400, "Invalid course", headers={"Cache-Control": "no-store"})
    try:
        principal = await request_principal(request)
        await run_request_sql(request, lambda db: authorize_grant(db, principal, purpose="collect", course_id=course_id, target_ref=None))
    except HTTPException as exc:
        raise HTTPException(exc.status_code, exc.detail, headers={**(exc.headers or {}), "Cache-Control": "no-store"}) from None
    if (getattr(request.app.state, "analytics_browser_presence_required", False) is True
            and getattr(request.app.state, "analytics_legacy_read_mode", "off") == "analytics"):
        response.headers["X-Analytics-Browser-Presence-Required"] = "true"
    production = getattr(request.app.state, "analytics_production_catalog", None)
    local = getattr(request.app.state, "analytics_local_collection", None)
    if production is not None or local is not None:
        response.headers["X-Analytics-Collection-Mode"] = "parallel"
    def metadata():
        unavailable = {"course_id": course_id, "catalog_version": None, "lesson_ids": []}
        try:
            if production is None:
                if getattr(request.app.state, "analytics_browser_gateway", None) is not None:
                    return unavailable
                snapshot, message = read_published_catalog(course_id, local)
            else:
                snapshot, message = read_production_catalog(course_id, production)
            image = message["catalog"]
            tracks = {t["track_id"] for t in image["tracks"] if not t["tombstone"]}
            active = {l["lesson_id"] for l in image["lessons"] if not l["tombstone"] and l["track_id"] in tracks}
            lessons = [l["id"] for l in snapshot.catalog.LESSONS if l["id"] in active and get_lesson_access(l, principal)[0]]
            return {"course_id": course_id, "catalog_version": message["catalog_version"], "lesson_ids": lessons}
        except (ValueError, OSError, KeyError, TypeError):
            return unavailable
    return await run_in_threadpool(metadata)
