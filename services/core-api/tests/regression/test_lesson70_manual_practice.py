"""The third manual card must render and its answers must be accessible on request."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from pymentor_core.adapters.course_local.provider import LocalCourseProvider
from pymentor_core.modules.learning.api import service


LESSON_ID = "lesson-c4878f92ca23a93b777d9fa8"


def test_lesson70_public_cards_and_requested_answers():
    content = LocalCourseProvider()
    lesson = content.find_lesson(LESSON_ID, course_id="backend")
    track = next(t for t in content.runtime.snapshot("backend").catalog.TRACKS
                 if any(item["id"] == LESSON_ID for item in t["lessons"]))
    public = next(item for item in content.get_public_lessons(track["id"], course_id="backend")
                  if item["id"] == LESSON_ID)
    tasks = public["manual_practice"][0]["tasks"]
    assert len(tasks) == 3
    assert all(task.get("requirements") and "answer" not in task for task in tasks)
    with patch.object(service, "get_lesson_access", return_value=(True, None)):
        for index in range(3):
            result = service.manual_practice_answer(LESSON_ID, 0, index, None, None,
                                                    principal=object(), content=content)
            assert result["answer"] == lesson["manual_practice"][0]["tasks"][index]["answer"]
    with patch.object(service, "get_lesson_access", return_value=(False, "subscription")):
        with pytest.raises(HTTPException) as error:
            service.manual_practice_answer(LESSON_ID, 0, 2, None, None,
                                           principal=None, content=content)
        assert error.value.status_code == 403
