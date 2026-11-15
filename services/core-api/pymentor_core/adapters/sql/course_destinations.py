"""Guarded sync from the existing trusted course registry and settings parser."""

from __future__ import annotations

from typing import Callable

from pymentor_core.adapters.course_local.packages import load_registry
from pymentor_core.adapters.course_local.personal_settings import read_personal_settings
from pymentor_core.adapters.sql import landings
from pymentor_core.adapters.telegram.transport import verify_telegram_destination
from pymentor_core.app.paths import COURSES_ROOT
from pymentor_core.modules.landings.contracts import LandingError
from pymentor_core.modules.landings.contracts import VerifiedCourseDestination
from pymentor_core.adapters.course_local.landing_catalog import course_landing_slugs


def read_trusted_course_settings() -> dict[str, dict]:
    """Read settings only for the two allowlisted courses in the trusted registry."""
    registry = load_registry(COURSES_ROOT)
    result: dict[str, dict] = {}
    for course_id in course_landing_slugs():
        parsed = read_personal_settings(registry, course_id)
        result[course_id] = {
            "schema_version": 1,
            "course_id": parsed.course_id,
            "contacts": parsed.contacts,
            "telegram": {
                "enabled": parsed.telegram_enabled,
                "chat_id": parsed.chat_id,
                "topic_id": parsed.topic_id,
            },
        }
    return result


def sync_from_trusted_settings(
    session_factory,
    *,
    expected_versions: dict[str, int],
    apply: bool = False,
    settings_reader: Callable[[], dict[str, dict]] | None = None,
    verifier: Callable[[str, str], str] | None = None,
) -> dict[str, dict]:
    """Verify each destination without a DB checkout, then apply under version guards."""
    reader = settings_reader or read_trusted_course_settings
    settings = reader()
    with session_factory() as db:
        preview = landings.sync_course_destinations(
            db,
            settings=settings,
            expected_versions=expected_versions,
            apply=False,
        )
        db.rollback()
    if not apply:
        return preview

    secret_ref = next(iter(preview.values()))["secret_ref"]
    token = landings.resolve_landing_bot_token(secret_ref)
    verify = verifier or verify_telegram_destination
    verified: dict[str, VerifiedCourseDestination] = {}
    for course_id, row in preview.items():
        bot_username = verify(token, row["chat_id"])
        if (
            not isinstance(bot_username, str)
            or not bot_username.strip()
            or len(bot_username) > 64
            or any(ord(char) < 32 for char in bot_username)
        ):
            raise LandingError(status_code=422, detail=f"Telegram-проверка курса {course_id} не подтверждена")
        verified[course_id] = VerifiedCourseDestination(
            course_id=course_id,
            secret_ref=secret_ref,
            chat_id=row["chat_id"],
            bot_username=bot_username.strip(),
        )

    if reader() != settings:
        raise LandingError(status_code=409, detail="Настройки курсов изменились во время проверки Telegram")
    with session_factory() as db:
        return landings.sync_course_destinations(
            db,
            settings=settings,
            expected_versions=expected_versions,
            apply=True,
            verified_destinations=verified,
        )
