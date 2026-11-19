"""Read-only, static lesson source inventory. Never imports material modules."""
import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\s+-\s+.*\.md$")
PAIR = re.compile(r'''["'](block_[^"']+\.md)["']\s*:\s*["'](block_[^"']+)["']''')


def markdown_targets(contents):
    """Extract inline destinations, including angle-bracket paths containing spaces."""
    for match in re.finditer(r'(?<!!)\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[^)]*)?\s*\)', contents):
        yield match.group(1) if match.group(1) is not None else match.group(2)


class ResolutionError(Exception):
    def __init__(self, message, candidates=()):
        super().__init__(message)
        self.candidates = list(candidates)


def local_link_target(root, source, target):
    resolved = (source.parent / target).resolve()
    if resolved.is_file():
        return resolved
    # Concept links are repository resources; retain them when authoring moves
    # between course/service packages without rewriting historical Markdown.
    parts = Path(target).parts
    for index in range(len(parts) - 1):
        if parts[index:index + 2] == ('docs', 'concept'):
            candidate = (root / Path(*parts[index:])).resolve()
            if candidate.is_relative_to(root / 'docs/concept') and candidate.is_file():
                return candidate
    return resolved


def number_key(value):
    if not re.fullmatch(r"\d+(?:\.\d+)*", value):
        raise ResolutionError("Lesson number must be a dotted string of digits")
    # Normalize leading zeroes only, never collapse decimal components.
    return tuple(int(part) for part in value.split('.'))


def validate_preview_origin(origin):
    match = re.fullmatch(r'http://127\.0\.0\.1(?::([1-9][0-9]{0,4}))?', origin)
    if not match or (match.group(1) and int(match.group(1)) > 65535):
        raise ResolutionError('Invalid preview origin: expected HTTP 127.0.0.1 with a valid port')
    return origin


class Inventory:
    def __init__(self, root, chapter, course='backend', preview_origin='http://127.0.0.1:5173'):
        self.preview_origin = validate_preview_origin(preview_origin)
        self.root = Path(root).resolve()
        if not re.fullmatch(r'[a-z0-9]+(?:_[a-z0-9]+)*', course) or course == 'shared':
            raise ResolutionError("Invalid course name")
        if not re.fullmatch(r"chapter_\d+(?:_archive)?", chapter):
            raise ResolutionError("Invalid chapter name")
        self.course = course
        self.chapter = chapter
        course_root = self.root / 'courses' / course
        self.base = course_root / 'backend/plans' / chapter
        self.practice = course_root / 'backend/practice' / chapter
        self.front = course_root / 'frontend/course' / chapter
        if not self.base.is_dir():
            raise ResolutionError("Chapter not found", [chapter])
        self.track = self.base / 'track.json'
        self.index = self.front / 'index.ts'

    def rel(self, path):
        return path.relative_to(self.root).as_posix()

    def source(self, path):
        return {'path': self.rel(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} if path.is_file() else None

    def candidates(self):
        return sorted(p for p in self.base.glob('block_*/*.md') if NUMBER.match(p.name))

    def resolve(self, lesson=None, source=None):
        candidates = self.candidates()
        if source:
            wanted = source.replace('\\', '/')
            matches = [p for p in candidates if self.rel(p) == wanted or p.relative_to(self.base.parent).as_posix() == wanted]
        else:
            key = number_key(lesson)
            matches = [p for p in candidates if number_key(NUMBER.match(p.name).group(1)) == key]
        if len(matches) != 1:
            raise ResolutionError('Lesson source not found or ambiguous', [self.rel(p) for p in (matches or candidates)])
        return matches[0]

    def mapping(self, md):
        if not self.index.is_file():
            return []
        text = self.index.read_text(encoding='utf-8')
        if not re.search(r'defineLessonPages\s*\(\s*["\']' + re.escape(self.chapter) + r'["\']\s*,', text):
            return []
        key = md.relative_to(self.base).as_posix()
        return [v for k, v in PAIR.findall(text) if k == key]

    def context(self, md=None):
        result = {'course': self.course, 'chapter': self.chapter, 'track': self.source(self.track), 'frontend_mapping': self.source(self.index), 'identity': 'UNKNOWN: core identity was not evaluated', 'interpretation': 'Sources describe plans and materials; they do not establish explanation delivered or learner mastery.'}
        if md is None:
            counts = {}
            for path in self.candidates():
                counts[path.parent.name] = counts.get(path.parent.name, 0) + 1
            result['blocks'] = [{'block': name, 'lesson_count': count} for name, count in counts.items()]
            concept_dir = self.root / 'docs/concept' / self.chapter
            result['concept'] = [self.source(p) for p in [concept_dir / 'COURSE.md', *sorted(concept_dir.glob('BLOCK_*.md'))] if p.is_file()]
            return result
        mapping = self.mapping(md)
        result.update({'lesson_source': self.source(md), 'number': NUMBER.match(md.name).group(1), 'mapping_targets': mapping, 'tsx': [self.source(self.front / (item + '.tsx')) or {'path': self.rel(self.front / (item + '.tsx')), 'status': 'MISSING'} for item in mapping]})
        result['preview_url'] = (self.preview_origin + '/authoring.html?lesson=' + quote(self.chapter + '/' + mapping[0], safe='/')) if len(mapping) == 1 else None
        if result['preview_url'] and self.course != 'backend':
            result['preview_url'] += '&course=' + quote(self.course, safe='')
        result['preview_scope'] = 'Early theory preview; API and practice are not verified.'
        practice = []
        practice_unknown = []
        lesson = '.'.join(str(int(x)) for x in result['number'].split('.'))
        for kind, filename in [('manual', 'manual.py'), ('code', 'code_tasks.py')]:
            rows, sources = parse_practice(self, self.practice / md.parent.relative_to(self.base) / filename, lesson, kind)
            practice.extend(sources)
            practice_unknown.extend(detail for status, detail in rows if status == 'UNKNOWN')
        result['practice'] = list({item['path']: item for item in practice}.values())
        result['practice_unknown'] = practice_unknown
        # Concept provenance is accepted only from an explicit local Markdown link.
        course = self.root / 'docs/concept' / self.chapter / 'COURSE.md'
        concepts = [self.source(course)] if course.is_file() else []
        for target in markdown_targets(md.read_text(encoding='utf-8')):
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            path = local_link_target(self.root, md, unquote(parsed.path))
            if path.is_file() and path.is_relative_to(self.root / 'docs/concept'):
                item = self.source(path)
                if item not in concepts:
                    concepts.append(item)
        result['concept'] = concepts
        if not any(re.fullmatch(r'BLOCK_\d+\.md', Path(item['path']).name) for item in concepts):
            result['concept_block'] = 'UNKNOWN: no explicit resolvable block concept link in lesson source'
        return result


def parse_practice(inv, path, lesson, kind, seen=None, imported=False, *, literal_entries=None):
    """Inspect target literals in local numbered catalogs without importing Python."""
    seen = seen or set()
    checks, sources = [], []
    if path in seen:
        return [('UNKNOWN', 'cyclic import')], []
    seen.add(path)
    if not path.is_file():
        return [('UNKNOWN', 'practice file missing: ' + inv.rel(path))], []
    sources.append(inv.source(path))
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    except (SyntaxError, UnicodeError) as exc:
        if imported:
            return [('UNKNOWN', f'Imported practice cannot be inspected: {inv.rel(path)}: {exc}')], []
        return [('FAIL', f'Python syntax: {inv.rel(path)}: {exc}')], sources
    checks.append(('PASS', 'Python syntax: ' + inv.rel(path)))
    name = 'MANUAL_PRACTICE' if kind == 'manual' else 'CODE_TASKS'
    found = []
    opaque = False
    imports = {}
    modules = {}
    numbered_module = re.compile(r'(?:manual|practice)_\d+(?:_\d+)*')
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or node.level != 1:
            continue
        if node.module and numbered_module.fullmatch(node.module):
            for alias in node.names:
                if alias.name == name:
                    imports[alias.asname or alias.name] = node.module
        elif node.module is None:
            for alias in node.names:
                if numbered_module.fullmatch(alias.name):
                    modules[alias.asname or alias.name] = alias.name
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                if isinstance(value, ast.Dict):
                    for key, val in zip(value.keys, value.values):
                        try:
                            k = ast.literal_eval(key)
                        except (ValueError, TypeError):
                            opaque = True
                            continue
                        if isinstance(k, (str, int)) and not isinstance(k, bool) and str(k).replace('_', '.') == lesson:
                            found.append(val)
                elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'merge_lesson_maps':
                    matched_imports = 0
                    for arg in value.args:
                        module = None
                        if isinstance(arg, ast.Name) and arg.id in imports:
                            module = imports[arg.id]
                        elif (isinstance(arg, ast.Attribute) and arg.attr == name
                              and isinstance(arg.value, ast.Name) and arg.value.id in modules):
                            module = modules[arg.value.id]
                        if module:
                            subchecks, subsources = parse_practice(
                                inv, path.parent / (module + '.py'), lesson, kind, seen,
                                imported=True, literal_entries=literal_entries,
                            )
                            checks.extend(subchecks)
                            sources.extend(subsources)
                            if subsources:
                                matched_imports += 1
                        else:
                            opaque = True
                    if matched_imports > 1:
                        checks.append(('UNKNOWN', f'{name} target occurs in multiple imported catalogs'))
                    if value.keywords:
                        opaque = True
                else:
                    opaque = True
            elif isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == name:
                try:
                    key = ast.literal_eval(target.slice)
                except (ValueError, TypeError):
                    opaque = True
                    continue
                if str(key) == lesson:
                    found.append(value)
    if len(found) > 1:
        checks.append(('UNKNOWN', f'{name} target has multiple literal entries or assignments; override is ambiguous'))
    for node in found:
        if not isinstance(node, (ast.List, ast.Tuple)):
            status = 'FAIL' if isinstance(node, (ast.Constant, ast.Dict, ast.Set)) else 'UNKNOWN'
            checks.append((status, f'{name} target value is not a literal list'))
            continue
        if literal_entries is not None:
            literal_entries.append(node.elts)
            continue
        for i, card in enumerate(node.elts):
            if not isinstance(card, ast.Dict):
                status = 'FAIL' if isinstance(card, (ast.Constant, ast.List, ast.Tuple, ast.Set)) else 'UNKNOWN'
                checks.append((status, f'{name} card {i+1} must be a literal dict or is an expression'))
                continue
            validate_card(card, kind, f'{name} card {i+1}', checks)
        checks.append(('PASS', f'{name} target has {len(node.elts)} literal cards'))
    if opaque:
        checks.append(('UNKNOWN', f'{name} contains unsupported expression or import; completeness not verified'))
    if not found and not imports and not modules and not imported:
        checks.append(('UNKNOWN', f'{name} target not statically found'))
    if imported and not found and not imports and not modules:
        if opaque:
            return [('UNKNOWN', 'Imported practice may contain target: ' + inv.rel(path))], []
        return [], []
    return checks, sources


def visible_manual_card(node):
    """Project only learner-facing literals, without decoding hidden values."""
    error = 'Manual practice has unsupported or ambiguous card fields'
    if not isinstance(node, ast.Dict):
        raise ResolutionError(error)
    card = {}
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            raise ResolutionError(error)
        field = key.value
        if field in card:
            raise ResolutionError(error)
        if field in ('title', 'task', 'result'):
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                raise ResolutionError(error)
            card[field] = value.value
        elif field in ('steps', 'requirements', 'tasks'):
            if not isinstance(value, (ast.List, ast.Tuple)):
                raise ResolutionError(error)
            if field == 'tasks':
                card[field] = [visible_manual_card(task) for task in value.elts]
            elif all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in value.elts):
                card[field] = [item.value for item in value.elts]
            else:
                raise ResolutionError(error)
    if not card.get('title') or not card.get('task'):
        raise ResolutionError(error)
    return card


def visible_practice(inv, md):
    number = '.'.join(map(str, number_key(NUMBER.match(md.name).group(1))))
    path = inv.practice / md.parent.relative_to(inv.base) / 'manual.py'
    entries = []
    checks, sources = parse_practice(inv, path, number, 'manual', literal_entries=entries)
    if len(entries) != 1 or not entries[0] or any(status != 'PASS' for status, _ in checks):
        raise ResolutionError('No unambiguous static manual practice for this lesson')
    return {
        'course': inv.course,
        'chapter': inv.chapter,
        'number': number,
        'scope': 'STATIC_MANUAL_PROMPTS',
        'practice_sources': sources,
        'cards': [visible_manual_card(card) for card in entries[0]],
    }


def validate_card(card, kind, label, checks, nested=False):
    """Validate literal fields individually, never evaluate a material expression."""
    fields = {}
    unknown_keys = False
    for key, value in zip(card.keys, card.values):
        try:
            key = ast.literal_eval(key)
        except (ValueError, TypeError):
            unknown_keys = True
            continue
        if not isinstance(key, str):
            checks.append(('FAIL', f'{label} field name must be a string'))
        elif key in fields:
            checks.append(('FAIL', f'{label} duplicate field {key}'))
        else:
            fields[key] = value
    if unknown_keys:
        checks.append(('UNKNOWN', f'{label} contains computed field names'))
    required = (('title', 'task', 'requirements', 'hint') if nested else ('title', 'task', 'result')) if kind == 'manual' else ('title', 'prompt', 'starter_code')
    for field in required:
        if field not in fields:
            checks.append(('UNKNOWN' if unknown_keys else 'FAIL', f'{label} missing field {field}'))
    expected = {'title': str, 'task': str, 'result': str, 'hint': str, 'requirements': list, 'steps': list} if kind == 'manual' else {'title': str, 'prompt': str, 'starter_code': str, 'hints': list, 'requirements': dict, 'contract': dict}
    if kind == 'manual':
        expected.update({'tasks': list, 'answer': dict})
    for field, expected_type in expected.items():
        if field not in fields:
            continue
        value = fields[field]
        # Containers are checked structurally so an unrelated dynamic value does not hide errors.
        if expected_type is list and isinstance(value, (ast.List, ast.Tuple)):
            if field == 'tasks':
                if not value.elts:
                    checks.append(('FAIL', f'{label} field tasks is empty'))
                for i, task in enumerate(value.elts):
                    if isinstance(task, ast.Dict):
                        validate_card(task, 'manual', f'{label} task {i+1}', checks, nested=True)
                    elif isinstance(task, (ast.Call, ast.Name, ast.Attribute, ast.Subscript)):
                        checks.append(('UNKNOWN', f'{label} task {i+1} is an expression'))
                    else:
                        checks.append(('FAIL', f'{label} task {i+1} must be dict'))
            elif field in ('requirements', 'steps'):
                for item in value.elts:
                    if isinstance(item, ast.Constant) and not isinstance(item.value, str):
                        checks.append(('FAIL', f'{label} field {field} items must be strings'))
                    elif not isinstance(item, ast.Constant):
                        checks.append(('UNKNOWN', f'{label} field {field} item is an expression'))
            continue
        if expected_type is dict and isinstance(value, ast.Dict):
            if field == 'answer':
                answer_keys = [k.value for k in value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
                if 'steps' not in answer_keys:
                    checks.append(('UNKNOWN', f'{label} answer has no steps; verify whether this is a procedural task or an explanation-only answer'))
                for k, v in zip(value.keys, value.values):
                    if isinstance(k, ast.Constant) and k.value == 'steps':
                        if isinstance(v, (ast.List, ast.Tuple)):
                            for step in v.elts:
                                if isinstance(step, ast.Constant) and not isinstance(step.value, str):
                                    checks.append(('FAIL', f'{label} answer.steps items must be strings'))
                                elif not isinstance(step, ast.Constant):
                                    checks.append(('UNKNOWN', f'{label} answer.steps item is an expression'))
                        elif isinstance(v, ast.Constant):
                            checks.append(('FAIL', f'{label} answer.steps must be list'))
                        else:
                            checks.append(('UNKNOWN', f'{label} answer.steps is an expression'))
            continue
        try:
            literal = ast.literal_eval(value)
        except (ValueError, TypeError):
            checks.append(('UNKNOWN', f'{label} field {field} is an expression'))
            continue
        if not isinstance(literal, expected_type):
            checks.append(('FAIL', f'{label} field {field} must be {expected_type.__name__}'))


def check_plan_structure(contents):
    """Check teaching sections below H1; the document title is not a lesson section."""
    headings = list(re.finditer(r'^#{2,6}\s+(\d{2})(?:[.)\s:\-]+)(.+?)\s*$', contents, re.M))
    checks = []
    first = headings[0] if headings else None
    valid_intro = bool(first and first.group(1) == '00')
    checks.append(('PASS' if valid_intro else 'FAIL', 'Plan first teaching section is 00'))
    if valid_intro:
        intro = contents[first.end():headings[1].start() if len(headings) > 1 else len(contents)]
        positions = [intro.find(label) for label in ('Точка старта', 'Новый вопрос', 'Результат')]
        ordered = all(p >= 0 for p in positions) and positions == sorted(positions)
        checks.append(('PASS' if ordered else 'FAIL', 'Plan 00 has ordered Точка старта, Новый вопрос, Результат'))
    ending = bool(headings and headings[-1].group(2).strip(' *`') == 'Что мы будем делать в практике')
    checks.append(('PASS' if ending else 'FAIL', 'Plan last teaching section: Что мы будем делать в практике'))
    checks.append(('NOT_VERIFIED', 'Clarity, theory-practice transfer, planned visual and meaningful interaction require content acceptance P4'))
    return checks


def check_lesson_tsx(path):
    """Read JSX structure through the installed compiler, without executing the lesson."""
    node = shutil.which('node')
    helper = ROOT / 'tooling/courses/lesson_structure.cjs'
    if not node or not helper.is_file():
        return [('UNKNOWN', 'TSX structure checker unavailable; inspect the required lesson frame manually')]
    try:
        result = subprocess.run([node, str(helper), str(path)], capture_output=True,
                                text=True, encoding='utf-8', timeout=10, check=False)
        if result.returncode:
            return [('UNKNOWN', 'TSX structure checker failed: ' + result.stderr.strip()[:250])]
        rows = json.loads(result.stdout)
        if not isinstance(rows, list) or not rows or any(
            not isinstance(row, dict) or row.get('status') not in ('PASS', 'FAIL', 'UNKNOWN')
            or not isinstance(row.get('detail'), str) for row in rows
        ):
            return [('UNKNOWN', 'TSX structure checker returned an unsupported result')]
        return [(row['status'], row['detail']) for row in rows]
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as exc:
        return [('UNKNOWN', 'TSX structure was not checked: ' + str(exc)[:250])]


def check(inv, md, stage):
    data = inv.context(md)
    checks = [('PASS', 'Markdown source exists')]
    # Only local Markdown links are checked; network links and fragment-only links are not fetched.
    contents = re.sub(r'(?ms)^\s*```.*?^\s*```[^\n]*', '', md.read_text(encoding='utf-8'))
    for target in markdown_targets(contents):
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        local = local_link_target(inv.root, md, unquote(parsed.path))
        if not local.is_relative_to(inv.root):
            checks.append(('UNKNOWN', 'Local link outside root: ' + target))
        elif local.exists():
            checks.append(('PASS', 'Local link exists: ' + inv.rel(local)))
        else:
            checks.append(('FAIL', 'Local link missing: ' + inv.rel(local)))
    if stage == '2':
        checks.extend(check_plan_structure(contents))
        checks.append(('PLANNED', 'Frontend and practice implementation are not required at Stage 2'))
        checks.append(('NOT_VERIFIED', 'Browser, API, runner and public payload were not checked'))
        data.update({'stage': stage, 'scope': 'STATIC_ONLY', 'dynamic': 'NOT_VERIFIED', 'checks': [{'status': s, 'detail': d} for s, d in checks], 'practice_sources': []})
        return data
    mapping = data['mapping_targets']
    if len(mapping) != 1:
        checks.append(('FAIL', f'Expected one frontend mapping, found {len(mapping)}'))
    for item in mapping:
        path = inv.front / (item + '.tsx')
        if not path.is_file():
            checks.append(('FAIL', 'TSX missing: ' + inv.rel(path)))
        elif not re.search(r'\bexport\s+(?:(?:async\s+)?function|const|class)\s+' + re.escape(path.stem) + r'\b|\bexport\s*\{[^}]*\b' + re.escape(path.stem) + r'\b', path.read_text(encoding='utf-8')):
            checks.append(('FAIL', 'Expected named TSX export missing: ' + path.stem))
        else:
            checks.append(('PASS', 'TSX and named export: ' + inv.rel(path)))
            checks.extend(check_lesson_tsx(path))
    sources = []
    empty_kinds = []
    lesson = '.'.join(str(int(x)) for x in data['number'].split('.'))
    for kind, filename in [('manual', 'manual.py'), ('code', 'code_tasks.py')]:
        rows, files = parse_practice(inv, inv.practice / md.parent.relative_to(inv.base) / filename, lesson, kind)
        checks.extend(rows)
        sources.extend(files)
        card_rows = [detail for status, detail in rows if status == 'PASS' and 'target has ' in detail]
        empty_kinds.append(bool(card_rows) and all('target has 0 literal cards' in row for row in card_rows) and not any(status == 'UNKNOWN' for status, _ in rows))
    if all(empty_kinds):
        checks.append(('UNKNOWN', 'Both manual and editor practice are empty; intended practice is not verified'))
    checks.append(('NOT_VERIFIED', 'Public payload, duplicate IDs, hidden-data leakage, data consistency, browser, API and runner require verified inputs or dynamic execution'))
    data.update({'stage': stage, 'scope': 'STATIC_ONLY', 'dynamic': 'NOT_VERIFIED', 'checks': [{'status': s, 'detail': d} for s, d in checks], 'practice_sources': list({item['path']: item for item in sources}.values())})
    return data


def human_output(value, prefix=''):
    """Render compact, readable output while keeping every reported status visible."""
    lines = []
    if isinstance(value, dict) and 'path' in value:
        suffix = ' sha256:' + value['sha256'][:12] if 'sha256' in value else ' ' + value.get('status', '')
        return [f'{prefix}: {value["path"]}{suffix}'.strip(': ')]
    if isinstance(value, dict):
        for key, item in value.items():
            if key == 'checks':
                lines.extend(f"  {row['status']}: {row['detail']}" for row in item)
            elif key == 'blocks':
                lines.append('blocks: ' + ', '.join(f"{x['block']} ({x['lesson_count']})" for x in item))
            elif isinstance(item, (dict, list)):
                lines.extend(human_output(item, key))
            elif item is not None:
                lines.append(f'{key}: {item}')
    elif isinstance(value, list):
        for item in value:
            lines.extend(human_output(item, prefix))
    else:
        lines.append(f'{prefix}: {value}')
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for command in ('context', 'check', 'visible-practice'):
        p = sub.add_parser(command)
        p.add_argument('--chapter', required=True)
        p.add_argument('--course', default='backend', help='Course folder (default: backend)')
        p.add_argument('--root', type=Path, default=ROOT)
        p.add_argument('--preview-origin', default='http://127.0.0.1:5173', help='Local HTTP 127.0.0.1 preview origin')
        p.add_argument('--json', action='store_true')
        group = p.add_mutually_exclusive_group(required=command != 'context')
        group.add_argument('--lesson')
        group.add_argument('--source')
        if command == 'check':
            p.add_argument('--stage', required=True, choices=('2', '3'))
    args = parser.parse_args(argv)
    try:
        inv = Inventory(args.root, args.chapter, args.course, args.preview_origin)
        md = inv.resolve(args.lesson, getattr(args, 'source', None)) if args.lesson or getattr(args, 'source', None) else None
        if args.command == 'context':
            data = inv.context(md)
        elif args.command == 'visible-practice':
            data = visible_practice(inv, md)
        else:
            data = check(inv, md, args.stage)
        code = 1 if args.command == 'check' and any(c['status'] == 'FAIL' for c in data['checks']) else 0
    except (ResolutionError, OSError, UnicodeError) as exc:
        data = {'error': str(exc), 'candidates': getattr(exc, 'candidates', [])}
        code = 2
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else '\n'.join(human_output(data)))
    return code


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    sys.exit(main())
