"""Bounded HTTP transport for Core's learner read proxy."""
import json
from urllib.parse import urlencode, urlsplit

from pymentor_core.adapters.analytics_http.http import request
from pymentor_core.modules.analytics_delivery.errors import LearnerReadUnavailable


class LearnerAnalyticsClient:
    _paths = {
        "history": "/internal/analytics/v1/legacy/history",
        "measured": "/api/analytics/reports/measured-calendar",
        "statistics": "/api/analytics/reports/activity",
    }

    def __init__(self, endpoint, *, transport=None, verify=True):
        url = urlsplit(endpoint)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in {"", "/"}
                or (url.scheme == "http" and url.hostname not in {"127.0.0.1", "localhost", "::1"})):
            raise ValueError("invalid Analytics query endpoint")
        self.endpoint = endpoint.rstrip("/")
        self.transport = transport
        self.verify = verify

    async def read(self, kind, grant, params):
        body = await request("GET", self.endpoint + self._paths[kind] + "?" + urlencode(params),
            headers={"Authorization": "Bearer " + grant, "Accept": "application/json"}, body=None,
            deadline_seconds=4, max_bytes=16384, transport=self.transport, verify=self.verify)
        if body is None or body[0] != 200:
            raise LearnerReadUnavailable()

        def unique(pairs):
            values = dict(pairs)
            if len(values) != len(pairs):
                raise ValueError("duplicate property")
            return values

        try:
            result = json.loads(body[1], object_pairs_hook=unique)
            if not isinstance(result, dict):
                raise ValueError("invalid response")
            return result
        except (ValueError, UnicodeError, RecursionError):
            raise LearnerReadUnavailable() from None
