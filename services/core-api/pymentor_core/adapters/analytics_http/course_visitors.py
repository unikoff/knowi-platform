"""Bounded private Analytics read. The browser never sees this token or body."""
import json
from datetime import timedelta
import re
from urllib.parse import urlsplit

from pymentor_core.adapters.analytics_http.http import request

MAX_MEMBERSHIPS = 1000
_DIGEST = re.compile(r"[a-f0-9]{64}")


class _CourseCohortClient:
    def __init__(self, endpoint, *, transport=None, verify=True):
        url = urlsplit(endpoint)
        if (url.scheme not in {"http", "https"} or not url.hostname
                or url.username or url.password or url.query or url.fragment
                or (url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"})):
            raise ValueError("invalid Analytics query endpoint")
        self._url = endpoint.rstrip("/") + self._path
        self._transport = transport
        self._verify = verify

    async def _request(self, *, body, grant, max_bytes):
        result = await request("POST", self._url, body=body, deadline_seconds=3, max_bytes=max_bytes,
                               transport=self._transport, verify=self._verify, headers={"Authorization": "Bearer " + grant,
                                   "Content-Type": "application/json", "Accept": "application/json"})
        if result is None or result[0] != 200:
            raise ValueError("course analytics report unavailable")
        return json.loads(result[1])

    async def discover(self, *, course_id, body, grant):
        data = await self._request(body=body, grant=grant, max_bytes=70_000)
        if (not isinstance(data, dict) or set(data) != {"course_id", "subject_digests"}
                or data["course_id"] != course_id or not isinstance(data["subject_digests"], list)):
            raise ValueError("invalid course visitor candidates")
        candidates = data["subject_digests"]
        if (len(candidates) > MAX_MEMBERSHIPS
                or any(not isinstance(value, str) or not _DIGEST.fullmatch(value) for value in candidates)
                or candidates != sorted(set(candidates))):
            raise ValueError("invalid course visitor candidate set")
        return candidates


class CourseVisitorsClient(_CourseCohortClient):
    _path = "/internal/analytics/v1/course/visitors"

    async def read(self, *, course_id, today, body, grant):
        data = await self._request(body=body, grant=grant, max_bytes=4096)
        if (not isinstance(data, dict) or set(data) != {"course_id", "quality", "daily"}
                or data["course_id"] != course_id or data["quality"] != "unknown"
                or not isinstance(data["daily"], list) or len(data["daily"]) != 30):
            raise ValueError("invalid course visitor report")
        for index, row in enumerate(data["daily"]):
            cohorts = ("with_subscription", "without_subscription")
            if (not isinstance(row, dict) or set(row) != {"date", *cohorts}
                    or row["date"] != (today - timedelta(days=29 - index)).isoformat()
                    or any(row[key] is not None and (type(row[key]) is not int or row[key] <= 0)
                           for key in cohorts)):
                raise ValueError("invalid course visitor series")
        return {"status": "available", "quality": "unknown", "daily": data["daily"]}


class CourseStudyTimeClient(_CourseCohortClient):
    _path = "/internal/analytics/v1/course/study-time"

    async def read(self, *, course_id, today, body, grant):
        data = await self._request(body=body, grant=grant, max_bytes=10_240)
        if (not isinstance(data, dict) or set(data) != {"course_id", "quality", "daily"}
                or data["course_id"] != course_id or data["quality"] != "unknown"
                or not isinstance(data["daily"], list) or len(data["daily"]) != 30):
            raise ValueError("invalid course study time report")

        payload = json.loads(body)
        cohort_sizes = {
            "with_subscription": len(payload["active_subscriptions"]),
            "without_subscription": len(payload["inactive_subscriptions"]),
        }
        for index, row in enumerate(data["daily"]):
            if (not isinstance(row, dict) or set(row) != {"date", *cohort_sizes}
                    or row["date"] != (today - timedelta(days=29 - index)).isoformat()):
                raise ValueError("invalid course study time series")
            for cohort, limit in cohort_sizes.items():
                value = row[cohort]
                if value is None:
                    continue
                if (not isinstance(value, dict) or set(value) != {"active_ms", "learners"}
                        or type(value["learners"]) is not int or not 0 < value["learners"] <= limit
                        or type(value["active_ms"]) is not int
                        or not value["learners"] <= value["active_ms"] <= value["learners"] * 86_400_000):
                    raise ValueError("invalid course study time observation")
        return {"status": "available", "quality": "unknown", "daily": data["daily"]}
