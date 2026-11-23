from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from pymentor_core.adapters.course_local import catalog
from pymentor_core.adapters.course_local.runtime import default_runtime
from pymentor_core.modules.learning.api import service
from pymentor_core.modules.learning.progress_policy import is_countable_lesson


TRACK_ID = default_runtime().context("backend").get_authoring("chapter_04").TRACK_ID
PREFIXES = ("chapter_04/block_15/", "chapter_04/block_16/")


def test_ready_lesson_has_three_private_answer_cards():
    lesson = catalog.find_lesson("lesson-64c9d597a164bbdb4f145d5a")
    assert lesson["self_check"]
    assert is_countable_lesson(lesson)
    visible = next(item for item in catalog.get_public_lessons(TRACK_ID) if item["id"] == lesson["id"])
    cards = visible["manual_practice"][0]["tasks"]
    assert len(cards) == 3
    assert all("answer" not in card for card in cards)
    for index in range(3):
        response = service.manual_practice_answer(
            lesson["id"], 0, index, None, None,
            principal=SimpleNamespace(id=7, subscription_until=datetime(2099, 1, 1, tzinfo=timezone.utc)),
            content=catalog,
        )
        assert response["answer"]["steps"]


def test_placeholders_preserve_existing_completion_and_reject_new_writes(monkeypatch):
    lessons = [
        item for item in catalog.find_track(TRACK_ID)["lessons"]
        if item["source_file"].startswith(PREFIXES) and item["id"] != "lesson-64c9d597a164bbdb4f145d5a"
    ]
    assert len(lessons) == 17
    monkeypatch.setattr(service, "get_lesson_access", lambda *args: (True, None))
    monkeypatch.setattr(service.progress, "mark_lesson_completed", lambda **kwargs: pytest.fail("unexpected progress write"))
    principal = SimpleNamespace(id=7)
    for lesson in lessons:
        assert is_countable_lesson(lesson)
        assert service._lesson_completion_status(lesson, {lesson["id"]}, {})
        assert not service._lesson_completion_status(lesson, set(), {})
        timestamp = datetime(2026, 11, 20, tzinfo=timezone.utc)
        assert service._lesson_completion_timestamp(lesson, timestamp, {}) == timestamp
        with pytest.raises(HTTPException) as completion:
            service.complete_lesson(lesson["legacy_id"], None, None, principal=principal, content=catalog)
        assert completion.value.status_code == 400
        with pytest.raises(HTTPException) as answer:
            service.manual_practice_answer(lesson["id"], 0, 0, None, None, principal=principal, content=catalog)
        assert answer.value.status_code == 404
        assert service.lesson_theory(lesson["id"], None, None, principal=principal, content=catalog)["theory_markdown"] == ""


def test_catalog_and_lesson_list_preserve_completed_receipt(monkeypatch):
    lesson = next(
        item for item in catalog.find_track(TRACK_ID)["lessons"]
        if item["source_file"].startswith(PREFIXES) and not item["self_check"]
    )
    timestamp = datetime(2026, 11, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(service.progress, "get_completion_facts", lambda *args, **kwargs: ({lesson["id"]: timestamp}, {}))
    monkeypatch.setattr(service.progress, "get_completed_lesson_ids", lambda *args, **kwargs: {lesson["id"]})
    monkeypatch.setattr(service.progress, "get_completed_task_revisions", lambda *args, **kwargs: {})
    monkeypatch.setattr(service, "get_lesson_access", lambda *args: (True, None))
    result = service.build_catalog_progress(None, user_id=7, content=catalog)
    item = next(x for x in result["courses"][TRACK_ID] if x["id"] == lesson["id"])
    assert item["completed"] and item["counts_toward_statistics"]
    assert item["completed_on"] == timestamp.date()
    items = service.build_lessons_payload(TRACK_ID, SimpleNamespace(id=7), None, content=catalog)
    assert next(x for x in items if x["id"] == lesson["id"])["completed"]
