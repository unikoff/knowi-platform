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


PREVIOUS_NUMBERS = ["61","62","62.1","63","63.1","64","65","65.1","65.2","66","66.1","67","67.1","68","68.1","68.2","69","69.1","70","70.1","70.2","71","71.1","72","72.1","73","73.1","74","74.1","75","75.1","76","76.1","77","78","79","80"]
PREVIOUS_IDS = ["lesson-d438792e0b20b056865121c2","lesson-fa803d31a77d5cb090ee19f5","lesson-21af84be662ab431322a27e2","lesson-c9198aef312ff777073f9191","lesson-a47be9475c5312cbde17009d","lesson-aaca6af71bca62419e58a534","lesson-91397bc595142f136b0d98be","lesson-399a6e34a03544bbd0844bae","lesson-b5d7b23858669f037891b010","lesson-af71dba4333c5c9c260509d0","lesson-f5193aeda62d2acb47ef0ea9","lesson-e4f11f77b8cc75613259c3d0","lesson-b84803d6d929a9a5702f1e9d","lesson-52f2ee321da8eaacd8374031","lesson-ab0c922cfe3c21be3cf77269","lesson-1c3efe1c67d4dea24d3622dd","lesson-0573e2213a9183f287ccca25","lesson-9cfec0837a5316f8d49bc7df","lesson-c4878f92ca23a93b777d9fa8","lesson-9770b9dad477e6f1de445e9d","lesson-d2718adb26807676f460fc98","lesson-64c9d597a164bbdb4f145d5a","lesson-6fba6a4aadfbd60afad41707","lesson-7cc77c89e9b728736cecb4ff","lesson-b098ede7bff903dc17251a8e","lesson-4d0e92d5a45c9a88004f2f6e","lesson-ae74df09d7d35d88a34f52c0","lesson-41523ca5ae49d3525b9ca066","lesson-9cee7e8928cda7f5550a3f6d","lesson-b8a449709e7fe7cf344312b4","lesson-68a97f28ebcb4f5e7776430b","lesson-95b574372e3e051bc6d8781f","lesson-d2f4f5206c199ede6641d41c","lesson-d9fe108a4aa647f485491ad4","lesson-37cee89307c04b52620eaaa3","lesson-c6beca9892147bed6dec3d4b","lesson-ff63f70a4af3a9cd62790ec6"]


NEW_IDS = {
    "72.2": "lesson-f27f30ace5891ddae44faa0a",
    "73.2": "lesson-816d61d45c842f03786c56af",
}
NUMBERS = sorted(
    [*PREVIOUS_NUMBERS, *NEW_IDS],
    key=lambda number: tuple(map(int, number.split("."))),
)


class DatabaseDayNumberingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.track = content.find_track(course.TRACK_ID)
        cls.lessons = cls.track["lessons"][1:]
        cls.repo = Path(__file__).resolve().parents[4]

    def test_days_and_contiguous_sublessons(self):
        actual = [re.match(r"^(\d+(?:\.\d+)*)\. ", l["title"])[1] for l in self.lessons]
        self.assertEqual(actual, NUMBERS)
        self.assertEqual(len(self.track["lessons"]), len(NUMBERS) + 1)
        self.assertEqual(sum("." not in n for n in actual), 20)
        for block, count in zip(range(13, 17), (9, 12, 12, 6)):
            rows = [l for l in self.lessons if f"/block_{block:02d}/" in l["source_file"]]
            self.assertEqual(len(rows), count)
            parts = [content._lesson_number_parts(Path(l["source_file"]).name) for l in rows]
            self.assertEqual([p[0] for p in parts if len(p) == 1], list(range(block * 5 - 4, block * 5 + 1)))
        for day in range(61, 81):
            rows = [n for n in actual if int(n.split(".")[0]) == day]
            self.assertEqual(rows, [str(day)] + [f"{day}.{i}" for i in range(1, len(rows))])

    def test_every_existing_id_and_positional_alias_is_preserved(self):
        by_number = {
            re.match(r"^(\d+(?:\.\d+)*)\. ", lesson["title"])[1]: lesson
            for lesson in self.lessons
        }
        self.assertEqual([by_number[number]["id"] for number in PREVIOUS_NUMBERS], PREVIOUS_IDS)
        for index, number in enumerate(PREVIOUS_NUMBERS, 2):
            lesson = by_number[number]
            alias = f"{course.TRACK_ID}-lesson-{index:02d}"
            self.assertEqual(lesson["legacy_id"], alias)
            self.assertIs(content.find_lesson(alias), lesson)
            self.assertIs(content.find_lesson(lesson["id"]), lesson)
        for number, lesson_id in NEW_IDS.items():
            lesson = by_number[number]
            self.assertEqual(lesson["id"], lesson_id)
            self.assertEqual(lesson["legacy_id"], lesson_id)
            self.assertIs(content.find_lesson(lesson_id), lesson)

    def test_practice_and_access_follow_the_new_numbers(self):
        for number, lesson in zip(NUMBERS, self.lessons):
            key = number if "." in number else int(number)
            self.assertEqual(lesson["access"], "subscription")
            self.assertEqual(lesson["tasks"], [])
            has_practice = number == "71" or not lesson["source_file"].startswith(
                ("chapter_04/block_15/", "chapter_04/block_16/")
            )
            self.assertEqual(len(lesson["manual_practice"]), int(has_practice))
            self.assertEqual(len(course.MANUAL_PRACTICE[key]), int(has_practice))
        self.assertEqual(set(course.MANUAL_PRACTICE), {n if "." in n else int(n) for n in NUMBERS})
        self.assertFalse(any(course.CODE_TASKS.values()))

    def test_reworked_lessons_keep_plans_and_answers_private(self):
        public = {lesson["id"]: lesson for lesson in content.get_public_lessons(course.TRACK_ID)}
        lessons = [
            lesson for lesson in self.lessons
            if lesson["source_file"].startswith(("chapter_04/block_15/", "chapter_04/block_16/"))
        ]
        self.assertEqual(len(lessons), 18)
        for lesson in lessons:
            with self.subTest(source=lesson["source_file"]):
                self.assertTrue(lesson["private_manual_answers"])
                self.assertEqual(lesson["theory_markdown"], "")
                delivered = public[lesson["id"]]
                self.assertEqual(delivered["theory"], [])
                self.assertNotIn("theory_markdown", delivered)
                if lesson["id"] == "lesson-64c9d597a164bbdb4f145d5a":
                    self.assertTrue(delivered["self_check"])
                else:
                    self.assertEqual(delivered["manual_practice"], [])
                    self.assertEqual(delivered["tasks"], [])
                    self.assertIsNone(delivered["video"])
                    self.assertFalse(delivered["self_check"])
                self.assertIn(lesson["id"], next(t for t in content.get_tracks_summary() if t["id"] == course.TRACK_ID)["countable_lesson_ids"])

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
            if number != "71" and relative.startswith(("block_15/", "block_16/")):
                self.assertIn("return null;", source)
            else:
                self.assertRegex(source, r'<RichHero\b[\s\S]*?\btitle=')
            markdown = (content.COURSE_ROOT / lesson["source_file"]).read_text(encoding="utf-8")
            self.assertTrue(markdown.startswith(f"# {number}. "))

    def test_concept_passports_match_the_menu_and_mapping(self):
        numbers = []
        for block in range(13, 17):
            text = (self.repo / f"docs/concept/chapter_04/BLOCK_{block}.md").read_text(encoding="utf-8")
            numbers.extend(re.findall(r"^### (\d+(?:\.\d+)*)\. ", text, re.M))
        absorbed = {"71.1", "72.2", "73.1", "75.1"}
        self.assertEqual(numbers, [number for number in NUMBERS if number not in absorbed])
        block_15 = (self.repo / "docs/concept/chapter_04/BLOCK_15.md").read_text(encoding="utf-8")
        for number in absorbed:
            self.assertIn(f"| `{number}`.", block_15)
        text = (self.repo / "docs/concept/chapter_04/COURSE.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\| (?:Пройденный|Перепроектированный) \| .+$", text, re.M)
        block_links = re.findall(r"\[\d+\]\((BLOCK_\d+\.md)\)", "\n".join(rows))
        self.assertEqual(block_links, [f"BLOCK_{block}.md" for block in range(13, 17)])
        self.assertEqual(list(LESSON_NUMBERS.values()), PREVIOUS_NUMBERS)

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
