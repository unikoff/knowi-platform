"""Verify the authored manual catalog and its private-answer boundary."""
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request
from pymentor_core.adapters.course_local.runtime import default_runtime as _course_runtime
_authoring = _course_runtime().context("backend").get_authoring
chapter_05 = _authoring("chapter_05")
from pymentor_core.adapters.course_local import catalog
from pymentor_core.modules.learning.api import service


def authored_lessons():
    return [lesson for lesson in catalog.LESSONS if lesson["source_file"].startswith("chapter_05/block_17/")]


def assert_public(test, value):
    if isinstance(value, dict):
        test.assertFalse({"answer", "reference_code", "tests"}.intersection(value))
        for item in value.values():
            assert_public(test, item)
    elif isinstance(value, list):
        for item in value:
            assert_public(test, item)


class Block17ManualPrivacyTests(TestCase):
    def test_every_registered_lesson_has_complete_manual_cards(self):
        lessons = authored_lessons()
        self.assertEqual(10, len(lessons))
        for lesson in lessons:
            with self.subTest(source=lesson["source_file"]):
                self.assertTrue(lesson["manual_practice"])
                self.assertTrue(lesson["self_check"])
                self.assertEqual([], lesson["tasks"])
                for exercise in lesson["manual_practice"]:
                    self.assertTrue(exercise["task"].strip())
                    self.assertTrue(exercise["tasks"])
                    for task in exercise["tasks"]:
                        for key in ("title", "task", "result", "hint"):
                            self.assertTrue(task[key].strip(), key)
                        self.assertTrue(task["requirements"])
                        answer = task["answer"]
                        self.assertTrue(answer["explanation"].strip())
                        self.assertTrue(answer.get("steps") or answer.get("code"))

    def test_public_projection_never_contains_manual_answers(self):
        lessons = [lesson for lesson in catalog.get_public_lessons(chapter_05.TRACK_ID) if lesson["source_file"].startswith("chapter_05/block_17/")]
        self.assertEqual(10, len(lessons))
        for lesson in lessons:
            self.assertTrue(lesson["manual_practice"])
            assert_public(self, lesson)

    def test_authoring_plans_are_not_delivered_as_learner_theory(self):
        request = Request({"type": "http", "headers": []})
        for lesson in authored_lessons():
            with self.subTest(source=lesson["source_file"]):
                self.assertEqual([], lesson["theory"])
                self.assertEqual("", lesson["theory_markdown"])
                with patch.object(service, "get_optional_user", return_value=object()), patch.object(service, "get_lesson_access", return_value=(True, None)):
                    payload = service.lesson_theory(lesson["id"], request, None)
                self.assertEqual("", payload["theory_markdown"])

    def test_private_answer_checks_access_and_returns_independent_copy(self):
        request = Request({"type": "http", "headers": []})
        for lesson in authored_lessons():
            for exercise_index, exercise in enumerate(lesson["manual_practice"]):
                for task_index, task in enumerate(exercise["tasks"]):
                    with self.subTest(source=lesson["source_file"], task=task_index):
                        with patch.object(service, "get_optional_user", return_value=object()), patch.object(service, "get_lesson_access", return_value=(True, None)):
                            payload = service.manual_practice_answer(lesson["id"], exercise_index, task_index, request, None)
                        self.assertEqual(task["answer"], payload["answer"])
                        payload["answer"]["explanation"] = "changed test copy"
                        self.assertNotEqual("changed test copy", task["answer"]["explanation"])
            with patch.object(service, "get_optional_user", return_value=None), patch.object(service, "get_lesson_access", return_value=(False, "subscription")):
                with self.assertRaises(HTTPException) as error:
                    service.manual_practice_answer(lesson["id"], 0, 0, request, None)
                self.assertEqual(403, error.exception.status_code)
            with patch.object(service, "get_optional_user", return_value=object()), patch.object(service, "get_lesson_access", return_value=(True, None)):
                with self.assertRaises(HTTPException) as error:
                    service.manual_practice_answer(lesson["id"], len(lesson["manual_practice"]), 0, request, None)
                self.assertEqual(404, error.exception.status_code)
