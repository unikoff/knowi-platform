"""Day grouping must not change published lesson/progress identities."""

import re
import unittest
from pathlib import Path
from urllib.parse import unquote

from pymentor_core.adapters.course_local import catalog as content
from pymentor_core.adapters.course_local.runtime import default_runtime as _course_runtime

_authoring = _course_runtime().context("backend").get_authoring
course = _authoring("chapter_04")
LESSON_NUMBERS = getattr(_authoring("chapter_04.numbering"), "LESSON_NUMBERS")


# Published stable IDs and original alias slots, in current teaching order.
# Absorbed lessons leave gaps in the slots of the remaining lessons.
PUBLISHED_IDENTITIES = {
    "61": ("lesson-d438792e0b20b056865121c2", 2),
    "62": ("lesson-fa803d31a77d5cb090ee19f5", 3),
    "62.1": ("lesson-21af84be662ab431322a27e2", 4),
    "63": ("lesson-c9198aef312ff777073f9191", 5),
    "63.1": ("lesson-a47be9475c5312cbde17009d", 6),
    "64": ("lesson-aaca6af71bca62419e58a534", 7),
    "65": ("lesson-91397bc595142f136b0d98be", 8),
    "65.1": ("lesson-399a6e34a03544bbd0844bae", 9),
    "65.2": ("lesson-b5d7b23858669f037891b010", 10),
    "66": ("lesson-af71dba4333c5c9c260509d0", 11),
    "66.1": ("lesson-f5193aeda62d2acb47ef0ea9", 12),
    "67": ("lesson-e4f11f77b8cc75613259c3d0", 13),
    "67.1": ("lesson-b84803d6d929a9a5702f1e9d", 14),
    "68": ("lesson-52f2ee321da8eaacd8374031", 15),
    "68.1": ("lesson-ab0c922cfe3c21be3cf77269", 16),
    "68.2": ("lesson-1c3efe1c67d4dea24d3622dd", 17),
    "69": ("lesson-0573e2213a9183f287ccca25", 18),
    "69.1": ("lesson-9cfec0837a5316f8d49bc7df", 19),
    "70": ("lesson-c4878f92ca23a93b777d9fa8", 20),
    "70.1": ("lesson-9770b9dad477e6f1de445e9d", 21),
    "70.2": ("lesson-d2718adb26807676f460fc98", 22),
    "71": ("lesson-64c9d597a164bbdb4f145d5a", 23),
    "72": ("lesson-7cc77c89e9b728736cecb4ff", 25),
    "72.1": ("lesson-b098ede7bff903dc17251a8e", 26),
    "73": ("lesson-4d0e92d5a45c9a88004f2f6e", 27),
    "73.2": ("lesson-816d61d45c842f03786c56af", None),
    "74": ("lesson-41523ca5ae49d3525b9ca066", 29),
    "74.1": ("lesson-9cee7e8928cda7f5550a3f6d", 30),
    "75": ("lesson-b8a449709e7fe7cf344312b4", 31),
    "76": ("lesson-95b574372e3e051bc6d8781f", 33),
    "76.1": ("lesson-d2f4f5206c199ede6641d41c", 34),
    "77": ("lesson-d9fe108a4aa647f485491ad4", 35),
    "78": ("lesson-37cee89307c04b52620eaaa3", 36),
    "79": ("lesson-c6beca9892147bed6dec3d4b", 37),
    "80": ("lesson-ff63f70a4af3a9cd62790ec6", 38),
}
NUMBERS = list(PUBLISHED_IDENTITIES)
ABSORBED_NUMBERS = {"71.1", "72.2", "73.1", "75.1"}


class DatabaseDayNumberingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.track = content.find_track(course.TRACK_ID)
        cls.lessons = cls.track["lessons"][1:]
        cls.repo = Path(__file__).resolve().parents[4]

    def test_days_and_active_sublesson_order(self):
        actual = [re.match(r"^(\d+(?:\.\d+)*)\. ", l["title"])[1] for l in self.lessons]
        self.assertEqual(actual, NUMBERS)
        self.assertEqual(len(self.track["lessons"]), 36)
        self.assertEqual(sum("." not in n for n in actual), 20)
        for block, count in zip(range(13, 17), (9, 12, 8, 6)):
            rows = [l for l in self.lessons if f"/block_{block:02d}/" in l["source_file"]]
            self.assertEqual(len(rows), count)
            parts = [content._lesson_number_parts(Path(l["source_file"]).name) for l in rows]
            self.assertEqual([p[0] for p in parts if len(p) == 1], list(range(block * 5 - 4, block * 5 + 1)))

    def test_every_active_id_and_original_alias_is_preserved(self):
        by_number = {
            re.match(r"^(\d+(?:\.\d+)*)\. ", lesson["title"])[1]: lesson
            for lesson in self.lessons
        }
        self.assertEqual(set(by_number), set(PUBLISHED_IDENTITIES))
        for number, (lesson_id, slot) in PUBLISHED_IDENTITIES.items():
            with self.subTest(number=number):
                lesson = by_number[number]
                alias = lesson_id if slot is None else f"{course.TRACK_ID}-lesson-{slot:02d}"
                self.assertEqual(lesson["id"], lesson_id)
                self.assertEqual(lesson["legacy_id"], alias)
                self.assertIs(content.find_lesson(alias), lesson)
                self.assertIs(content.find_lesson(lesson_id), lesson)

    def test_practice_and_access_follow_the_active_numbers(self):
        for number, lesson in zip(NUMBERS, self.lessons):
            key = number if "." in number else int(number)
            self.assertEqual(lesson["access"], "subscription")
            self.assertEqual(lesson["tasks"], [])
            self.assertEqual(len(lesson["manual_practice"]), 1)
            self.assertEqual(len(course.MANUAL_PRACTICE[key]), 1)
        self.assertEqual(set(course.MANUAL_PRACTICE), {n if "." in n else int(n) for n in NUMBERS})
        self.assertFalse(any(course.CODE_TASKS.values()))

    def test_reworked_lessons_keep_plans_and_answers_private(self):
        public = {lesson["id"]: lesson for lesson in content.get_public_lessons(course.TRACK_ID)}
        lessons = [
            lesson for lesson in self.lessons
            if lesson["source_file"].startswith(("chapter_04/block_15/", "chapter_04/block_16/"))
        ]
        self.assertEqual(len(lessons), 14)
        track_summary = next(t for t in content.get_tracks_summary() if t["id"] == course.TRACK_ID)
        for lesson in lessons:
            with self.subTest(source=lesson["source_file"]):
                self.assertTrue(lesson["private_manual_answers"])
                self.assertEqual(lesson["theory_markdown"], "")
                delivered = public[lesson["id"]]
                self.assertEqual(delivered["theory"], [])
                self.assertNotIn("theory_markdown", delivered)
                self.assertTrue(delivered["self_check"])
                self.assertEqual(delivered["tasks"], [])
                self.assertIsNone(delivered["video"])
                self.assertIn(lesson["id"], track_summary["countable_lesson_ids"])
                exercise, = delivered["manual_practice"]
                self.assertNotIn("answer", exercise)
                self.assertTrue(exercise["tasks"])
                self.assertTrue(all("answer" not in task for task in exercise["tasks"]))

    def test_frontend_pages_and_markdown_are_bound_once(self):
        root = self.repo / "courses/backend/frontend/course/chapter_04"
        entries = dict(re.findall(r'"([^"]+\.md)": "([^"]+)"', (root / "index.ts").read_text(encoding="utf-8")))
        self.assertEqual(len(entries), len(NUMBERS) + 1)
        self.assertEqual(len(set(entries.values())), len(entries))
        for number, lesson in zip(NUMBERS, self.lessons):
            relative = lesson["source_file"].removeprefix("chapter_04/")
            page = entries[relative]
            source = (root / f"{page}.tsx").read_text(encoding="utf-8")
            self.assertIn(f"export function {page.split('/')[-1]}", source)
            self.assertRegex(source, r'<RichHero\b[\s\S]*?\btitle=')
            markdown = (content.COURSE_ROOT / lesson["source_file"]).read_text(encoding="utf-8")
            self.assertTrue(markdown.startswith(f"# {number}. "))

    def test_concept_passports_match_the_menu_and_mapping(self):
        numbers = []
        for block in range(13, 17):
            text = (self.repo / f"docs/concept/chapter_04/BLOCK_{block}.md").read_text(encoding="utf-8")
            numbers.extend(re.findall(r"^### (\d+(?:\.\d+)*)\. ", text, re.M))
        self.assertEqual(numbers, NUMBERS)
        text = (self.repo / "docs/concept/chapter_04/COURSE.md").read_text(encoding="utf-8")
        block_15 = (self.repo / "docs/concept/chapter_04/BLOCK_15.md").read_text(encoding="utf-8")
        for number in ABSORBED_NUMBERS:
            self.assertIn(f"| `{number}`.", block_15)
        rows = re.findall(r"^\| (?:Пройденный|Перепроектированный) \| .+$", text, re.M)
        block_links = re.findall(r"\[\d+\]\((BLOCK_\d+\.md)\)", "\n".join(rows))
        self.assertEqual(block_links, [f"BLOCK_{block}.md" for block in range(13, 17)])
        expected_legacy = {
            str(slot + 67): number for number, (_, slot) in PUBLISHED_IDENTITIES.items() if slot is not None
        }
        expected_legacy.update({"91": "71.1", "95": "73.1", "99": "75.1"})
        self.assertEqual(LESSON_NUMBERS, expected_legacy)

    def test_learner_roadmap_lists_every_lesson_in_order(self):
        root = self.repo / "courses/backend/frontend/course/chapter_04"
        page = (root / "block_00/LearningRoadmap.tsx").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r'title: "(\d+(?:\.\d+)*) · ', page), NUMBERS)

    def test_numeric_local_links_point_to_existing_materials(self):
        paths = list((self.repo / "docs/concept/chapter_04").glob("*.md"))
        paths += list((content.COURSE_ROOT / "chapter_04").rglob("*.md"))
        checked = 0
        for p in paths:
            text = p.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)]+)\)", text):
                target = unquote(target.strip("<>")).split("#")[0]
                if "://" in target or not re.search(r"(?:^|/)\d+(?:\.\d+)* - .+\.md$", target):
                    continue
                self.assertTrue((p.parent / target).is_file(), (p, target))
                checked += 1
        self.assertGreaterEqual(checked, len(NUMBERS))


if __name__ == "__main__":
    unittest.main()
