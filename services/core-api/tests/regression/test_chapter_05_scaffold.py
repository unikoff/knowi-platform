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
        self.assertEqual(41, len(lessons))  # overview and 40 lesson passports
        self.assertEqual(41, len({lesson["id"] for lesson in lessons}))
        other_ids = {lesson["id"] for lesson in catalog.LESSONS if lesson["track"] != chapter_05.TRACK_ID}
        manifest = (root / "courses/backend/frontend/course/chapter_05/index.ts").read_text(encoding="utf-8")
        routes = dict(re.findall(r'^  "([^"]+)": "([^"]+)",?$', manifest, re.MULTILINE))
        self.assertEqual(41, len(routes))
        counts = {"block_17": 10, "block_18": 10, "block_19": 11, "block_20": 9}
        for block, count in counts.items():
            self.assertEqual(count, sum(lesson["source_file"].startswith(f"chapter_05/{block}/") for lesson in lessons))
        for lesson in lessons:
            with self.subTest(source=lesson["source_file"]):
                self.assertNotIn(lesson["id"], other_ids)
                self.assertEqual(lesson["id"], lesson["legacy_id"])
                self.assertIs(catalog.find_lesson(lesson["id"]), lesson)
                registered = lesson["source_file"].startswith("chapter_05/block_17/")
                self.assertEqual(registered or lesson["source_file"].startswith("chapter_05/block_00/"), catalog._is_countable(lesson))
                self.assertEqual(registered, bool(lesson["manual_practice"]))
                self.assertEqual([], lesson["tasks"])
                source = lesson["source_file"].removeprefix("chapter_05/")
                self.assertIn(source, routes)
                page = root / "courses/backend/frontend/course/chapter_05" / (routes[source] + ".tsx")
                self.assertTrue(page.is_file())
                self.assertIn(f"export function {page.stem}(", page.read_text(encoding="utf-8"))

    def test_only_authored_block_has_project_practice(self):
        self.assertEqual({}, chapter_05.OVERVIEW_PRACTICE)
        self.assertEqual({81, "81.1", 82, "82.1", 83, "83.1", 84, "84.1", 85, "85.1"}, set(chapter_05.MANUAL_PRACTICE))
        for block in (chapter_05.block_18, chapter_05.block_19, chapter_05.block_20):
            self.assertEqual({}, block.MANUAL_PRACTICE)
        self.assertEqual({}, chapter_05.CODE_TASKS)


if __name__ == "__main__":
    unittest.main()
