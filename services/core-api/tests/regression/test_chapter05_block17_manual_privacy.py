"""Deliver Block 17 project tasks while keeping requested answers private."""
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
        test.assertFalse({"answer", "reference_code", "solution", "tests", "hidden_tests"}.intersection(value))
        for item in value.values():
            assert_public(test, item)
    elif isinstance(value, list):
        for item in value:
            assert_public(test, item)


class Block17ManualPrivacyTests(TestCase):
    def test_every_lesson_has_manual_project_tasks_and_no_runner_tasks(self):
        lessons = authored_lessons()
        self.assertEqual(6, len(lessons))
        for lesson in lessons:
            with self.subTest(source=lesson["source_file"]):
                self.assertTrue(lesson["manual_practice"])
                self.assertIs(True, lesson["self_check"])
                self.assertTrue(catalog._is_countable(lesson))
                self.assertEqual([], lesson["tasks"])
                for exercise in lesson["manual_practice"]:
                    self.assertTrue(exercise["tasks"])
                    for task in exercise["tasks"]:
                        self.assertTrue(task["task"])
                        self.assertTrue(task["requirements"])
                        self.assertIsInstance(task["answer"], dict)
                        self.assertTrue(task["answer"])

    def test_public_projection_never_contains_manual_answers(self):
        lessons = [lesson for lesson in catalog.get_public_lessons(chapter_05.TRACK_ID) if lesson["source_file"].startswith("chapter_05/block_17/")]
        self.assertEqual(6, len(lessons))
        for lesson in lessons:
            private = catalog.find_lesson(lesson["id"])
            self.assertEqual(len(private["manual_practice"]), len(lesson["manual_practice"]))
            for expected, delivered in zip(private["manual_practice"], lesson["manual_practice"]):
                self.assertEqual(len(expected["tasks"]), len(delivered["tasks"]))
                for source_task, public_task in zip(expected["tasks"], delivered["tasks"]):
                    self.assertEqual(source_task["task"], public_task["task"])
                    self.assertEqual(source_task["requirements"], public_task["requirements"])
            self.assertEqual([], lesson["tasks"])
            self.assertIs(True, lesson["self_check"])
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

    def test_requested_answers_require_access_and_preserve_missing_index_errors(self):
        request = Request({"type": "http", "headers": []})
        for lesson in authored_lessons():
            with patch.object(service, "get_optional_user", return_value=None), patch.object(service, "get_lesson_access", return_value=(False, "subscription")):
                with self.assertRaises(HTTPException) as error:
                    service.manual_practice_answer(lesson["id"], 0, 0, request, None)
                self.assertEqual(403, error.exception.status_code)
            practice = lesson["manual_practice"]
            for exercise_index, exercise in enumerate(practice):
                for task_index, task in enumerate(exercise["tasks"]):
                    with self.subTest(source=lesson["source_file"], exercise=exercise_index, task=task_index):
                        with patch.object(service, "get_optional_user", return_value=object()), patch.object(service, "get_lesson_access", return_value=(True, None)):
                            payload = service.manual_practice_answer(lesson["id"], exercise_index, task_index, request, None)
                        self.assertEqual({"answer": task["answer"]}, payload)
            missing = [(-1, 0), (len(practice), 0)]
            for exercise_index, exercise in enumerate(practice):
                missing.extend(((exercise_index, -1), (exercise_index, len(exercise["tasks"]))))
            for exercise_index, task_index in missing:
                with self.subTest(source=lesson["source_file"], exercise=exercise_index, task=task_index):
                    with patch.object(service, "get_optional_user", return_value=object()), patch.object(service, "get_lesson_access", return_value=(True, None)):
                        with self.assertRaises(HTTPException) as error:
                            service.manual_practice_answer(lesson["id"], exercise_index, task_index, request, None)
                    self.assertEqual(404, error.exception.status_code)
        retired_ids = (
            "lesson-252746d29145389459e23957", "lesson-cf461fcb268d053ccc07be33",
            "lesson-6c737a081d63ade3dea5fb1b", "lesson-967649f2c2d8bf79af5323ee",
            "lesson-1a05a796c41e8e68de3f6f63", "lesson-11a71cec9cc9732c9e2c852e",
        )
        for lesson_id in retired_ids:
            with self.subTest(retired=lesson_id):
                self.assertIsNone(catalog.find_lesson(lesson_id))
                with self.assertRaises(HTTPException) as error:
                    service.manual_practice_answer(lesson_id, 0, 0, request, None)
                self.assertEqual(404, error.exception.status_code)

    def test_manual_project_completion_requires_saved_lesson_progress(self):
        for lesson in authored_lessons():
            with self.subTest(source=lesson["source_file"]):
                self.assertFalse(service._lesson_completion_status(lesson, set(), {}))
                self.assertFalse(service._lesson_completion_status(lesson, {"another-lesson"}, {}))
                self.assertTrue(service._lesson_completion_status(lesson, {lesson["id"]}, {}))
