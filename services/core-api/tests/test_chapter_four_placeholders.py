from datetime import datetime, timezone
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from pymentor_core.adapters.course_local import catalog
from pymentor_core.adapters.course_local.runtime import default_runtime
from pymentor_core.modules.learning.api import service
from pymentor_core.modules.learning.progress_policy import is_countable_lesson


TRACK_ID = default_runtime().context("backend").get_authoring("chapter_04").TRACK_ID
PREFIXES = ("chapter_04/block_15/", "chapter_04/block_16/")
CARD_COUNTS = {
    "71": 3, "72": 2, "72.1": 4, "73": 4, "73.2": 2, "74": 3, "74.1": 4, "75": 6,
    "76": 3, "76.1": 3, "77": 3, "78": 3, "79": 6, "80": 5,
}
ABSORBED_RECEIPT_IDS = {
    "lesson-6fba6a4aadfbd60afad41707",  # 71.1
    "lesson-f27f30ace5891ddae44faa0a",  # 72.2
    "lesson-ae74df09d7d35d88a34f52c0",  # 73.1
    "lesson-68a97f28ebcb4f5e7776430b",  # 75.1
}
RECEIPT_TIME = datetime(2026, 11, 20, tzinfo=timezone.utc)


@pytest.fixture
def subscriber():
    return SimpleNamespace(id=7, subscription_until=datetime(2099, 1, 1, tzinfo=timezone.utc))


@pytest.fixture
def ready_lessons():
    lessons = {}
    for lesson in catalog.find_track(TRACK_ID)["lessons"]:
        source = lesson["source_file"]
        if source.startswith(PREFIXES):
            number = PurePosixPath(source).name.partition(" - ")[0]
            assert number not in lessons
            lessons[number] = lesson
    assert list(lessons) == list(CARD_COUNTS)
    return lessons


def test_reopened_lessons_deliver_cards_and_gate_their_answers(monkeypatch, subscriber, ready_lessons):
    monkeypatch.setattr(service.progress, "get_completed_lesson_ids", lambda *args, **kwargs: set())
    monkeypatch.setattr(service.progress, "get_completed_task_revisions", lambda *args, **kwargs: {})
    visible = {entry["id"]: entry for entry in service.build_lessons_payload(TRACK_ID, subscriber, None, content=catalog)}
    guest = {entry["id"]: entry for entry in service.build_lessons_payload(TRACK_ID, None, None, content=catalog)}

    for number, lesson in ready_lessons.items():
        lesson_id = lesson["id"]
        assert lesson["self_check"] and is_countable_lesson(lesson), number
        assert lesson["private_manual_answers"], number
        assert not guest[lesson_id]["is_available"], number
        assert not guest[lesson_id]["manual_practice"], number
        assert visible[lesson_id]["is_available"], number
        assert visible[lesson_id]["theory"] == [], number
        assert "theory_markdown" not in visible[lesson_id], number
        theory = service.lesson_theory(lesson_id, None, None, principal=subscriber, content=catalog)
        assert theory["theory_markdown"] == "", number

        exercise, = visible[lesson_id]["manual_practice"]
        assert "answer" not in exercise, number
        cards = exercise["tasks"]
        assert len(cards) == CARD_COUNTS[number], number
        with pytest.raises(HTTPException) as denied:
            service.manual_practice_answer(lesson_id, 0, 0, None, None, principal=None, content=catalog)
        assert denied.value.status_code == 403, number

        for index, card in enumerate(cards):
            assert "answer" not in card, (number, index)
            response = service.manual_practice_answer(
                lesson_id, 0, index, None, None, principal=subscriber, content=catalog,
            )
            authored = lesson["manual_practice"][0]["tasks"][index]["answer"]
            assert authored["steps"] and response == {"answer": authored}, (number, index)
        with pytest.raises(HTTPException) as missing:
            service.manual_practice_answer(lesson_id, 0, len(cards), None, None, principal=subscriber, content=catalog)
        assert missing.value.status_code == 404, number


@pytest.mark.parametrize("active_receipts, absorbed_receipts", [(False, False), (False, True), (True, False), (True, True)])
def test_catalog_and_lesson_list_keep_completion_on_the_original_ids(
    monkeypatch, subscriber, ready_lessons, active_receipts, absorbed_receipts,
):
    lesson_ids = {lesson["id"] for lesson in ready_lessons.values()}
    completed = lesson_ids.copy() if active_receipts else set()
    if absorbed_receipts:
        completed.update(ABSORBED_RECEIPT_IDS)
    facts = dict.fromkeys(completed, RECEIPT_TIME)
    monkeypatch.setattr(service.progress, "get_completion_facts", lambda *args, **kwargs: (facts, {}))
    monkeypatch.setattr(service.progress, "get_completed_lesson_ids", lambda *args, **kwargs: completed)
    monkeypatch.setattr(service.progress, "get_completed_task_revisions", lambda *args, **kwargs: {})

    progress = service.build_catalog_progress(None, user_id=subscriber.id, content=catalog)
    rows = {entry["id"]: entry for entry in progress["courses"][TRACK_ID]}
    visible = {entry["id"]: entry for entry in service.build_lessons_payload(TRACK_ID, subscriber, None, content=catalog)}
    assert not (ABSORBED_RECEIPT_IDS & rows.keys())
    assert not (ABSORBED_RECEIPT_IDS & visible.keys())
    for lesson_id in lesson_ids:
        item = rows[lesson_id]
        assert item["counts_toward_statistics"], lesson_id
        assert item["completed"] == active_receipts, lesson_id
        assert item["completed_on"] == (RECEIPT_TIME.date() if active_receipts else None), lesson_id
        assert visible[lesson_id]["completed"] == active_receipts, lesson_id


def test_reopened_lessons_accept_completion_under_the_stable_id(monkeypatch, subscriber, ready_lessons):
    writes = []
    monkeypatch.setattr(service.progress, "mark_lesson_completed", lambda **kwargs: writes.append(kwargs["lesson_id"]))

    for lesson in ready_lessons.values():
        response = service.complete_lesson(
            lesson["legacy_id"], None, None, principal=subscriber, content=catalog,
        )
        assert response == {"lesson_id": lesson["id"], "completed": True}
    assert writes == [lesson["id"] for lesson in ready_lessons.values()]
