"""Transport-independent helpers and fail-closed default ports."""
from pymentor_analytics.contracts.v2 import AcceptedEvent
from pymentor_analytics.modules.collector.ports import IntakeUnavailable
from pymentor_analytics.contracts.auth_v1 import FenceUnavailable


class UnavailableIntake:
    async def accept(self, record: AcceptedEvent, *, deadline: float):
        raise IntakeUnavailable()


class UnavailableCatalog:
    async def known(self, *, course_id: str, catalog_version: str, lesson_id: str | None, deadline: float) -> bool:
        raise IntakeUnavailable()


class UnavailableFence:
    async def permits(self, subject_ref):
        raise FenceUnavailable()
