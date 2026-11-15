from copy import deepcopy
from types import SimpleNamespace

import pytest

from pymentor_core.modules.learning.api import service
from pymentor_core.modules.learning.api.schemas import LessonMaterialResponse
from pymentor_core.modules.learning.course_context import course_scope, current_course_id


class Content:
    def __init__(self, sources, public):
        self.sources = sources
        self.public = public

    def find_track(self, track_id):
        assert track_id == "track" and current_course_id() == "third-course"
        return {"lessons": deepcopy(self.sources)}

    def get_public_lessons(self, track_id):
        assert track_id == "track" and current_course_id() == "third-course"
        return deepcopy(self.public)


def test_public_projection_uses_private_policy_without_exposing_it(monkeypatch):
    tasks = [{"id": "required", "revision": "r", "title": "Required", "level": "basic"}, {"id": "optional", "revision": "r", "title": "Optional", "level": "basic", "required": False}]
    source = [
        {"id": "untracked", "tasks": deepcopy(tasks), "progress_policy": {"revision_tracked": False, "completion": "lesson"}},
        {"id": "cards", "tasks": deepcopy(tasks), "progress_policy": {"revision_tracked": True, "completion": "cards"}},
        {"id": "lesson", "tasks": deepcopy(tasks), "progress_policy": {"revision_tracked": True, "completion": "lesson"}},
    ]
    public = [{"id": item["id"], "title": "Lesson", "status": "open", "tasks": deepcopy(tasks)} for item in source]
    seen = []
    monkeypatch.setattr(service, "get_lesson_access", lambda lesson, user: (True, None))
    monkeypatch.setattr(service.progress, "get_completed_lesson_ids", lambda *a, **kw: {"untracked"})
    def revisions(*args, **kwargs):
        assert kwargs["course_id"] == "third-course"
        seen.extend(kwargs["task_ids"])
        return {"required": "r"}
    monkeypatch.setattr(service.progress, "get_completed_task_revisions", revisions)
    with course_scope("third-course"):
        result = service.build_lessons_payload("track", SimpleNamespace(id=7), object(), content=Content(source, public))
    untracked, cards, lesson = result
    assert untracked["completed"] is True and all("completed" not in task for task in untracked["tasks"])
    assert cards["completed"] is True and lesson["completed"] is False
    assert [task["completed"] for task in cards["tasks"]] == [True, False]
    assert seen == ["required", "optional", "required", "optional"]
    assert all("progress_policy" not in item for item in result)
    rendered = [LessonMaterialResponse.model_validate({"lesson": item}).model_dump(exclude_unset=True) for item in result]
    assert "completed" not in rendered[0]["lesson"]["tasks"][0]
    assert rendered[1]["lesson"]["tasks"][0]["completed"] is True
    assert all("progress_policy" not in item["lesson"] for item in rendered)


@pytest.mark.parametrize("source_ids,public_ids", [(["a", "a"], ["a"]), (["a"], ["a", "a"]), (["a"], ["b"]), (["a", "b"], ["a"])])
def test_mismatched_or_duplicate_identities_fail_closed(source_ids, public_ids):
    source = [{"id": value} for value in source_ids]
    public = [{"id": value} for value in public_ids]
    with course_scope("third-course"), pytest.raises(RuntimeError, match="identities do not match"):
        service.build_lessons_payload("track", None, None, content=Content(source, public))
