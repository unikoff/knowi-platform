import contextlib
import io
import json
import shutil
import uuid
import unittest
from unittest import mock
from pathlib import Path

if __package__:
    from . import lesson_tools as lt
else:
    import lesson_tools as lt


class LessonToolsTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / ('lesson_tools_fixture_' + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.base = self.root / 'courses/backend/backend/plans/chapter_01/block_01'
        self.practice = self.root / 'courses/backend/backend/practice/chapter_01/block_01'
        self.front = self.root / 'courses/backend/frontend/course/chapter_01'
        self.base.mkdir(parents=True)
        self.practice.mkdir(parents=True)
        (self.front / 'block_01').mkdir(parents=True)
        (self.base.parent / 'track.json').write_text('{"id":"a title"}', encoding='utf-8')
        self.md = self.base / '01.1 - One.md'
        self.plan = ('# plan\n\n## 00. Вступление\n'
                     'Точка старта\nНовый вопрос\nРезультат\n\n'
                     '## 01. Что мы будем делать в практике\nДействие и результат.\n')
        self.md.write_text(self.plan, encoding='utf-8')
        (self.front / 'index.ts').write_text('defineLessonPages("chapter_01", {"block_01/01.1 - One.md":"block_01/Lesson01_1"});', encoding='utf-8')
        (self.front / 'block_01/Lesson01_1.tsx').write_text('''export function Lesson01_1() { return <RichLesson>
          <Section number="00"><LinkedNotes items={[
            {title:"Точка старта", description:"Начало"}, {title:"Новый вопрос", description:"Вопрос"}, {title:"Результат", description:"Итог"}]}/></Section>
          <Section number="01"><Diagram/><QuizCard/></Section>
          <Section number="02" title="Что мы будем делать в практике">
            <LinkedNotes items={[{title:"Проверим результат", description:"Сверим вывод"}]}/>
          </Section>
        </RichLesson>; }''', encoding='utf-8')
        (self.practice / 'manual.py').write_text('MANUAL_PRACTICE = {"1.1": [{"title": "ok", "task": "work", "answer": "SECRET_ANSWER"}]}', encoding='utf-8')
        (self.practice / 'code_tasks.py').write_text('CODE_TASKS = {"1.1": []}', encoding='utf-8')
        self.inv = lt.Inventory(self.root, 'chapter_01')

    def test_exact_decimal_and_ambiguity(self):
        other = self.base / '01.10 - Ten.md'
        other.write_text('plan', encoding='utf-8')
        self.assertEqual(self.inv.resolve('1.1'), self.md)
        self.assertEqual(self.inv.resolve('1.10'), other)
        duplicate = self.base / '1.1 - Duplicate.md'
        duplicate.write_text('plan', encoding='utf-8')
        with self.assertRaises(lt.ResolutionError) as err:
            self.inv.resolve('1.1')
        self.assertEqual(len(err.exception.candidates), 2)
        self.assertEqual(self.inv.resolve(source='chapter_01/block_01/01.1 - One.md'), self.md)

    def test_missing_and_empty(self):
        self.md.unlink()
        self.assertEqual(self.inv.context()['blocks'], [])
        with self.assertRaises(lt.ResolutionError) as err:
            self.inv.resolve('1.1')
        self.assertEqual(err.exception.candidates, [])

    def test_context_never_exposes_answer(self):
        output = json.dumps(self.inv.context(self.md))
        self.assertNotIn('SECRET_ANSWER', output)
        self.assertIn('sha256', output)

    def test_visible_practice_reads_only_ordered_prompts_for_exact_lesson(self):
        (self.practice / 'manual.py').write_text('''
from .practice_1_1 import MANUAL_PRACTICE as target
MANUAL_PRACTICE = merge_lesson_maps(target)
''', encoding='utf-8')
        (self.practice / 'practice_1_1.py').write_text('''
raise RuntimeError('MODULE_MUST_NOT_EXECUTE')
MANUAL_PRACTICE = {
    '1.10': [{'title': 'OTHER_LESSON', 'task': 'outside'}],
    '1.1': [{
        'title': 'Visible card', 'task': 'Practice here', 'result': 'Observe',
        'answer': {'title': 'PRIVATE_ANSWER'},
        'tasks': [
            {'title': 'First', 'task': 'Try', 'requirements': ['Keep state'],
             'hint': load('PRIVATE_HINT'), 'answer': {'task': 'PRIVATE_NESTED'},
             'checks': ['PRIVATE_CHECK']},
            {'title': 'Second', 'task': 'Compare', 'result': 'Match'},
        ],
    }],
}
''', encoding='utf-8')
        code, output = self.run_visible_practice()
        self.assertEqual(code, 0, output)
        self.assertNotIn('PRIVATE_', output)
        self.assertNotIn('OTHER_LESSON', output)
        self.assertEqual(json.loads(output)['cards'], [{
            'title': 'Visible card', 'task': 'Practice here', 'result': 'Observe',
            'tasks': [
                {'title': 'First', 'task': 'Try', 'requirements': ['Keep state']},
                {'title': 'Second', 'task': 'Compare', 'result': 'Match'},
            ],
        }])

    def test_visible_practice_rejects_computed_prompts_without_returning_cards(self):
        (self.practice / 'manual.py').write_text('''
MANUAL_PRACTICE = {'1.1': [
    {'title': 'PARTIAL_CARD', 'task': 'Try'},
    {'title': 'Computed', 'task': load('PRIVATE_EXPRESSION')},
]}
''', encoding='utf-8')
        code, output = self.run_visible_practice()
        self.assertEqual(code, 2)
        self.assertIn('error', json.loads(output))
        self.assertNotIn('PRIVATE_EXPRESSION', output)
        self.assertNotIn('PARTIAL_CARD', output)

    def run_visible_practice(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = lt.main([
                'visible-practice', '--root', str(self.root), '--chapter', 'chapter_01',
                '--lesson', '01.1', '--json',
            ])
        return code, output.getvalue()

    def test_selected_course_uses_its_own_plans_practice_and_preview(self):
        shutil.copytree(self.root / 'courses/backend', self.root / 'courses/example')
        own_plan = self.root / 'courses/example/backend/plans/chapter_01/block_01' / self.md.name
        own_plan.write_text('independent plan', encoding='utf-8')
        inv = lt.Inventory(self.root, 'chapter_01', 'example')
        self.assertEqual(inv.resolve('1.1'), own_plan)
        context = inv.context(own_plan)
        self.assertEqual(context['course'], 'example')
        self.assertIn('&course=example', context['preview_url'])
        self.assertTrue(all(item['path'].startswith('courses/example/') for item in context['practice']))
        self.assertEqual(self.inv.resolve('1.1').read_text(encoding='utf-8'), self.plan)

    def test_cli_preview_origin_is_validated_for_context_and_check(self):
        for command in ('context', 'check'):
            args = [command, '--root', str(self.root), '--chapter', 'chapter_01',
                    '--lesson', '1.1', '--json', '--preview-origin', 'http://127.0.0.1:5181']
            if command == 'check':
                args += ['--stage', '2']
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(lt.main(args), 0)
            self.assertEqual(json.loads(output.getvalue())['preview_url'],
                             'http://127.0.0.1:5181/authoring.html?lesson=chapter_01/block_01/Lesson01_1')
        for origin in ('https://pymentor.ru', 'http://localhost:5181', 'http://127.0.0.1.evil:5181',
                       'http://user@127.0.0.1:5181', 'http://127.0.0.1:65536',
                       'http://127.0.0.1:5181/path', 'http://127.0.0.1:5181?x', 'http://127.0.0.1:5181#x'):
            output = io.StringIO()
            with self.subTest(origin=origin), contextlib.redirect_stdout(output):
                code = lt.main(['context', '--root', str(self.root), '--chapter', 'chapter_01',
                                '--lesson', '1.1', '--json', '--preview-origin', origin])
            self.assertEqual(code, 2)
            self.assertIn('origin', json.loads(output.getvalue())['error'].lower())

    def test_invalid_or_missing_course_never_falls_back(self):
        for course in ('../backend', 'shared', 'missing'):
            with self.subTest(course=course), self.assertRaises(lt.ResolutionError):
                lt.Inventory(self.root, 'chapter_01', course)

    def test_import_and_opaque_value(self):
        (self.practice / 'manual.py').write_text('from .manual_1_1 import MANUAL_PRACTICE as LESSON\nMANUAL_PRACTICE = merge_lesson_maps(LESSON)', encoding='utf-8')
        (self.practice / 'manual_1_1.py').write_text('MANUAL_PRACTICE = {"1.1": [{"title": compute()}]}', encoding='utf-8')
        result = lt.check(self.inv, self.md, '3')
        self.assertTrue(any('manual_1_1.py' in s['path'] for s in result['practice_sources']))
        self.assertTrue(any(c['status'] == 'UNKNOWN' and 'expression' in c['detail'] for c in result['checks']))

    def test_numbered_module_catalog_finds_only_target_without_importing(self):
        (self.practice / 'manual.py').write_text(
            'from . import practice_1_1 as target, practice_2\n'
            'MANUAL_PRACTICE = merge_lesson_maps(target.MANUAL_PRACTICE, practice_2.MANUAL_PRACTICE)', encoding='utf-8')
        (self.practice / 'practice_1_1.py').write_text(
            'raise RuntimeError("must not execute")\n'
            'MANUAL_PRACTICE = {"1.1": [{"title": "target", "task": "work", "answer": "SECRET_ANSWER"}]}', encoding='utf-8')
        (self.practice / 'practice_2.py').write_text('MANUAL_PRACTICE = {2: []}', encoding='utf-8')
        context = self.inv.context(self.md)
        names = [Path(item['path']).name for item in context['practice']]
        self.assertIn('practice_1_1.py', names)
        self.assertNotIn('practice_2.py', names)
        self.assertNotIn('SECRET_ANSWER', json.dumps(context))
        checks = lt.check(self.inv, self.md, '3')['checks']
        self.assertTrue(any(c['status'] == 'PASS' and 'target has 1 literal cards' in c['detail'] for c in checks))

    def test_numbered_module_direct_symbol_import(self):
        (self.practice / 'manual.py').write_text(
            'from .practice_1_1 import MANUAL_PRACTICE as target\n'
            'MANUAL_PRACTICE = merge_lesson_maps(target)', encoding='utf-8')
        (self.practice / 'practice_1_1.py').write_text('MANUAL_PRACTICE = {"1.1": []}', encoding='utf-8')
        self.assertIn('practice_1_1.py', json.dumps(self.inv.context(self.md)))

    def test_numbered_module_builder_remains_unknown(self):
        (self.practice / 'manual.py').write_text(
            'from . import practice_1_1\n'
            'MANUAL_PRACTICE = merge_lesson_maps(practice_1_1.build())', encoding='utf-8')
        (self.practice / 'practice_1_1.py').write_text('raise RuntimeError("must not execute")', encoding='utf-8')
        checks = lt.check(self.inv, self.md, '3')['checks']
        self.assertTrue(any(c['status'] == 'UNKNOWN' and 'completeness not verified' in c['detail'] for c in checks))

    def test_missing_tsx_and_invalid_field(self):
        (self.front / 'block_01/Lesson01_1.tsx').unlink()
        (self.practice / 'manual.py').write_text('MANUAL_PRACTICE = {"1.1": [{"title": 7}]}', encoding='utf-8')
        checks = lt.check(self.inv, self.md, '3')['checks']
        failures = [c['detail'] for c in checks if c['status'] == 'FAIL']
        self.assertTrue(any('TSX missing' in f for f in failures))
        self.assertTrue(any('field title' in f for f in failures))

    def test_stage2_allows_unimplemented_plan_and_checks_links(self):
        (self.front / 'index.ts').unlink()
        (self.front / 'block_01/Lesson01_1.tsx').unlink()
        (self.practice / 'manual.py').unlink()
        (self.practice / 'code_tasks.py').unlink()
        (self.base / 'source.md').write_text('reference', encoding='utf-8')
        self.md.write_text(self.plan + '[local](source.md)', encoding='utf-8')
        checks = lt.check(self.inv, self.md, '2')['checks']
        self.assertFalse(any(c['status'] == 'FAIL' for c in checks))
        self.assertTrue(any(c['status'] == 'PLANNED' for c in checks))
        self.md.write_text('[missing](missing.md)', encoding='utf-8')
        self.assertTrue(any(c['status'] == 'FAIL' and 'missing.md' in c['detail'] for c in lt.check(self.inv, self.md, '2')['checks']))

    def test_concept_sources_chapter_and_lesson(self):
        concept = self.root / 'docs/concept/chapter_01'
        concept.mkdir(parents=True)
        for name in ('COURSE.md', 'BLOCK_1.md', 'BLOCK_2.md'):
            (concept / name).write_text(name, encoding='utf-8')
        self.md.write_text('[block](../../../../../docs/concept/chapter_01/BLOCK_1.md)', encoding='utf-8')
        chapter = self.inv.context()['concept']
        lesson = self.inv.context(self.md)['concept']
        self.assertEqual(len(chapter), 3)
        self.assertEqual({Path(c['path']).name for c in lesson}, {'COURSE.md', 'BLOCK_1.md'})
        self.assertTrue(all(len(c['sha256']) == 64 for c in chapter))
        self.assertEqual(self.inv.context(self.md)['preview_url'], 'http://127.0.0.1:5173/authoring.html?lesson=chapter_01/block_01/Lesson01_1')

    def test_explanation_only_answer_requires_review_not_structural_failure(self):
        exercise = {'title': 'Reasoning', 'task': 'Known input', 'result': 'Explained',
                    'tasks': [{'title': 'Explain', 'task': 'Explain the result',
                               'requirements': ['Name the cause'], 'hint': 'Recall the rule',
                               'result': 'Cause named',
                               'answer': {'explanation': 'The condition is false.'}}]}
        (self.practice / 'manual.py').write_text(
            'MANUAL_PRACTICE = ' + repr({'1.1': [exercise]}), encoding='utf-8')
        rows, _ = lt.parse_practice(self.inv, self.practice / 'manual.py', '1.1', 'manual')
        self.assertFalse(any(status == 'FAIL' for status, _ in rows))
        self.assertTrue(any(status == 'UNKNOWN' and 'steps' in detail for status, detail in rows))

    def test_empty_tasks_duplicate_and_nested_types(self):
        (self.practice / 'manual.py').write_text('''MANUAL_PRACTICE = {
 "1.1": [{"title": "a", "task": "b", "result": "c", "tasks": []}],
 "1.1": [{"title": "a", "task": "b", "result": "c", "tasks": [
    {"title": "t", "task": "x", "requirements": [1], "hint": "h", "result": 3, "answer": {"steps": [7]}}
 ]}]
}''', encoding='utf-8')
        rows, _ = lt.parse_practice(self.inv, self.practice / 'manual.py', '1.1', 'manual')
        text = '\n'.join(detail for _, detail in rows)
        self.assertIn('multiple literal entries', text)
        self.assertIn('tasks is empty', text)
        self.assertIn('requirements items must be strings', text)
        self.assertIn('field result must be str', text)
        self.assertIn('answer.steps items must be strings', text)

    def test_imports_only_target_and_mark_incomplete(self):
        (self.practice / 'manual.py').write_text('from .manual_1_1 import MANUAL_PRACTICE as A\nfrom .manual_2 import MANUAL_PRACTICE as B\nfrom .manual_3 import MANUAL_PRACTICE as C\nMANUAL_PRACTICE = merge_lesson_maps(A, B, C)', encoding='utf-8')
        (self.practice / 'manual_1_1.py').write_text('MANUAL_PRACTICE = {"1.1": []}', encoding='utf-8')
        (self.practice / 'manual_2.py').write_text('MANUAL_PRACTICE = {2: []}', encoding='utf-8')
        (self.practice / 'manual_3.py').write_text('MANUAL_PRACTICE = external()', encoding='utf-8')
        rows, sources = lt.parse_practice(self.inv, self.practice / 'manual.py', '1.1', 'manual')
        names = {Path(s['path']).name for s in sources}
        self.assertEqual(names, {'manual.py', 'manual_1_1.py'})
        self.assertTrue(any(status == 'UNKNOWN' and 'manual_3.py' in detail for status, detail in rows))

    def test_compact_and_full_json(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(lt.main(['context', '--chapter', 'chapter_01', '--root', str(self.root)]), 0)
        self.assertIn('sha256:', out.getvalue())
        self.assertFalse(out.getvalue().lstrip().startswith('{'))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            lt.main(['context', '--chapter', 'chapter_01', '--root', str(self.root), '--json'])
        self.assertEqual(len(json.loads(out.getvalue())['track']['sha256']), 64)

    def test_check_source_resolves_ambiguity(self):
        (self.base / '1.1 - Duplicate.md').write_text('plan', encoding='utf-8')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = lt.main(['check', '--chapter', 'chapter_01', '--source', 'chapter_01/block_01/01.1 - One.md', '--stage', '2', '--root', str(self.root), '--json'])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out.getvalue())['lesson_source']['path'].endswith('/01.1 - One.md'))

    def test_angled_links_with_spaces_and_encoded_characters(self):
        concept = self.root / 'docs/concept/chapter_01/sub folder'
        concept.mkdir(parents=True)
        (concept / 'BLOCK_1.md').write_text('concept', encoding='utf-8')
        (self.base / 'file with spaces.md').write_text('local', encoding='utf-8')
        self.md.write_text(self.plan + '[concept](<../../../../../docs/concept/chapter_01/sub folder/BLOCK%5F1.md> "title")\n[local](<file with spaces.md>)', encoding='utf-8')
        self.assertTrue(any(c['path'].endswith('/sub folder/BLOCK_1.md') for c in self.inv.context(self.md)['concept']))
        checks = lt.check(self.inv, self.md, '2')['checks']
        self.assertFalse(any(c['status'] == 'FAIL' for c in checks))
        self.assertEqual(sum(c['status'] == 'PASS' and 'Local link exists' in c['detail'] for c in checks), 2)

    def test_empty_practices_are_unknown_only_when_both_empty(self):
        (self.practice / 'manual.py').write_text('MANUAL_PRACTICE = {"1.1": []}', encoding='utf-8')
        checks = lt.check(self.inv, self.md, '3')['checks']
        self.assertTrue(any(c['status'] == 'UNKNOWN' and 'Both manual and editor practice are empty' in c['detail'] for c in checks))
        self.assertFalse(any(c['status'] == 'FAIL' for c in checks))
        (self.practice / 'manual.py').write_text('MANUAL_PRACTICE = {"1.1": [{"title": "t", "task": "x", "result": "r"}]}', encoding='utf-8')
        checks = lt.check(self.inv, self.md, '3')['checks']
        self.assertFalse(any('Both manual and editor practice are empty' in c['detail'] for c in checks))

    def test_plan_structure_does_not_accept_titles_only_inside_code(self):
        self.md.write_text('```md\n' + self.plan + '\n```', encoding='utf-8')
        checks = lt.check(self.inv, self.md, '2')['checks']
        self.assertTrue(any(c['status'] == 'FAIL' and '00' in c['detail'] for c in checks))
        self.assertTrue(any(c['status'] == 'FAIL' and 'практике' in c['detail'] for c in checks))

    def test_numbered_document_title_is_not_a_teaching_section(self):
        for title in ("# 81. Проверяем Planner", "# 81.1. Сверяем схему"):
            with self.subTest(title=title):
                self.md.write_text(self.plan.replace("# plan", title), encoding="utf-8")
                checks = lt.check(self.inv, self.md, "2")["checks"]
                self.assertFalse(any(c["status"] == "FAIL" for c in checks))

    def test_numbered_document_title_does_not_hide_wrong_teaching_order(self):
        contents = self.plan.replace("# plan", "# 81. Проверяем Planner")
        contents = contents.replace("## 00. Вступление", "## 01. Слишком ранний шаг\n## 00. Вступление")
        self.md.write_text(contents, encoding="utf-8")
        checks = lt.check(self.inv, self.md, "2")["checks"]
        self.assertTrue(any(c["status"] == "FAIL" and "first teaching section" in c["detail"] for c in checks))

    def test_plan_requires_ordered_intro_and_final_practice_bridge(self):
        self.md.write_text(self.plan.replace('Точка старта\nНовый вопрос', 'Новый вопрос\nТочка старта')
                           + '\n## 02. Another teaching section\n', encoding='utf-8')
        checks = lt.check(self.inv, self.md, '2')['checks']
        self.assertTrue(any(c['status'] == 'FAIL' and 'ordered' in c['detail'] for c in checks))
        self.assertTrue(any(c['status'] == 'FAIL' and 'практике' in c['detail'] for c in checks))

    def test_tsx_structural_findings_are_part_of_existing_stage3_check(self):
        with mock.patch.object(lt, 'check_lesson_tsx', return_value=[('FAIL', 'Missing educational visual')]):
            checks = lt.check(self.inv, self.md, '3')['checks']
        self.assertTrue(any(c['status'] == 'FAIL' and 'educational visual' in c['detail'] for c in checks))

    def test_cli_missing_is_nonzero_and_archive_is_separate(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = lt.main(['context', '--chapter', 'chapter_01_archive', '--lesson', '1.1', '--root', str(self.root), '--json'])
        self.assertEqual(code, 2)
        self.assertIn('error', json.loads(out.getvalue()))


if __name__ == '__main__':
    unittest.main()
