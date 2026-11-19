import re
import unittest
from pathlib import Path

from pymentor_core.adapters.course_local import catalog
from pymentor_core.adapters.course_local.runtime import default_runtime as _course_runtime
_authoring = _course_runtime().context("backend").get_authoring
chapter_05 = _authoring("chapter_05")


class Chapter05ScaffoldTests(unittest.TestCase):
    def test_catalog_and_frontend_routes_match(self):
        root = Path(__file__).resolve().parents[4]
        track = catalog.find_track(chapter_05.TRACK_ID)
        lessons = track["lessons"]
        self.assertEqual(35, len(lessons))  # overview and 34 planned lessons
        self.assertEqual(35, len({lesson["id"] for lesson in lessons}))
        other_ids = {lesson["id"] for lesson in catalog.LESSONS if lesson["track"] != chapter_05.TRACK_ID}
        manifest = (root / "courses/backend/frontend/course/chapter_05/index.ts").read_text(encoding="utf-8")
        routes = dict(re.findall(r'^  "([^"]+)": "([^"]+)",?$', manifest, re.MULTILINE))
        self.assertEqual(35, len(routes))
        counts = {"block_17": 6, "block_18": 8, "block_19": 11, "block_20": 9}
        for block, count in counts.items():
            self.assertEqual(count, sum(lesson["source_file"].startswith(f"chapter_05/{block}/") for lesson in lessons))
        expected_block17 = {
            "chapter_05/block_17/81 - Создаём согласованный резерв Planner.md": "lesson-2c72a2e8f15b3d99765d3a4d",
            "chapter_05/block_17/81.1 - Восстанавливаем рабочую копию из резерва.md": "lesson-79ad20d8bad8ea5e8682a7ce",
            "chapter_05/block_17/82 - Создаём исходную историю на новой базе.md": "lesson-a63726516955a18a16be6c0d",
            "chapter_05/block_17/83 - Подключаем заполненную копию к истории.md": "lesson-0df09001e79b8b3a9b510600",
            "chapter_05/block_17/84 - Добавляем пользователя в схему Planner.md": "lesson-c5597af4286aa5f6de00eccd",
            "chapter_05/block_17/85 - Записываем уникальные учётные записи.md": "lesson-0576d7e8df944d238cbb79e4",
        }
        self.assertEqual(expected_block17, {
            lesson["source_file"]: lesson["id"] for lesson in lessons
            if lesson["source_file"].startswith("chapter_05/block_17/")
        })
        for lesson in lessons:
            with self.subTest(source=lesson["source_file"]):
                self.assertNotIn(lesson["id"], other_ids)
                self.assertEqual(lesson["id"], lesson["legacy_id"])
                self.assertIs(catalog.find_lesson(lesson["id"]), lesson)
                authored = lesson["source_file"] in expected_block17
                overview = lesson["source_file"].startswith("chapter_05/block_00/")
                self.assertEqual(overview or authored, catalog._is_countable(lesson))
                self.assertEqual(authored, bool(lesson["manual_practice"]))
                self.assertIs(authored, lesson["self_check"])
                self.assertEqual([], lesson["tasks"])
                source = lesson["source_file"].removeprefix("chapter_05/")
                self.assertIn(source, routes)
                page = root / "courses/backend/frontend/course/chapter_05" / (routes[source] + ".tsx")
                self.assertTrue(page.is_file())
                self.assertIn(f"export function {page.stem}(", page.read_text(encoding="utf-8"))

    def test_only_authored_block17_has_manual_project_practice(self):
        self.assertEqual({}, chapter_05.OVERVIEW_PRACTICE)
        keys = {81, "81.1", 82, 83, 84, 85}
        self.assertEqual(keys, set(chapter_05.MANUAL_PRACTICE))
        self.assertEqual(keys, set(chapter_05.block_17.MANUAL_PRACTICE))
        self.assertTrue(all(chapter_05.block_17.MANUAL_PRACTICE.values()))
        for block in (chapter_05.block_18, chapter_05.block_19, chapter_05.block_20):
            self.assertEqual({}, block.MANUAL_PRACTICE)
            self.assertEqual({}, block.CODE_TASKS)
        self.assertEqual({}, chapter_05.block_17.CODE_TASKS)
        self.assertEqual({}, chapter_05.CODE_TASKS)


if __name__ == "__main__":
    unittest.main()
