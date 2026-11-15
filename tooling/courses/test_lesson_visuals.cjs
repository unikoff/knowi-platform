'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { checkLessonSource, checkCssOwnership, checkPaletteUsage } = require('./check_lesson_visuals.cjs');

test('current shared components and custom teaching diagrams remain valid', () => {
  const source = `import { Callout, ContentList } from '../shared';
    const page = <><Callout tone="success">Note</Callout><ContentList ordered items={[]} />
    <div className="lesson42-diagram"><ul><li>Text</li></ul></div></>;`;
  assert.deepEqual(checkLessonSource(source), []);
  assert.deepEqual(checkCssOwnership('.lesson42-diagram { display: grid; }', 'courses/backend/frontend/course/chapter_01/diagram.css'), []);
  assert.deepEqual(checkCssOwnership('.rich-lesson .lesson42-diagram code { padding: 0; }', 'courses/backend/frontend/course/chapter_01/diagram.css'), []);
});

test('retired props are rejected, including renamed imports', () => {
  const source = `import { Callout as Notice, LinkedNotes } from '../shared';
    const page = <><Notice tone="info">Note</Notice><Notice tone={"info"}>Note</Notice><LinkedNotes variant="cards" items={[]} /></>;`;
  assert.equal(checkLessonSource(source).length, 3);
  assert.equal(checkLessonSource('import { Callout as Notice } from "@course-ui"; const page = <Notice tone="info"/>;').length, 1);
});

test('shared syntax and status colors use known platform roles', () => {
  const known = new Set(['--syntax-string', '--negative']);
  assert.deepEqual(checkPaletteUsage('.code { color: var(--syntax-string) } .error { color: var(--negative) }', 'shared.css', known), []);
  assert.equal(checkPaletteUsage('.code { color: #ffffff } .error { background: rgb(0 0 0 / .1) }', 'shared.css', known).length, 2);
  assert.equal(checkPaletteUsage('.code { color: var(--other-brand) }', 'shared.css', known).length, 1);
});

test('hand-written shared lists and dynamically assembled old classes are rejected', () => {
  assert.equal(checkLessonSource('<ul className="linked-notes"><li className="linked-note" /></ul>').length, 2);
  assert.equal(checkLessonSource('<aside className={`lesson-callout--info ${tone}`} />').length, 1);
  assert.equal(checkLessonSource('<div className="tracer"><div className="tracer-code" /></div>').length, 2);
  assert.equal(checkLessonSource('<ol className="task-spec__list task-spec__list--ordered"><li>Step</li></ol>', 'TaskBrief.tsx').length, 2);
});

test('lessons cannot override the common appearance, but its owner can change it', () => {
  const css = '.lesson42 .lesson-callout { background: blue; } .answer-option.is-wrong { color: green; }';
  assert.equal(checkCssOwnership(css, 'courses/backend/frontend/course/chapter_01/lesson42.css').length, 2);
  assert.deepEqual(checkCssOwnership(css, 'courses/shared/ui/components/styles/cards.css'), []);
  assert.deepEqual(checkCssOwnership('/* .lesson-callout is owned by the library */ .lesson42 {}', 'courses/backend/frontend/course/chapter_01/lesson42.css'), []);
  assert.equal(checkCssOwnership('.answer-option .custom-label { color: blue; }', 'courses/backend/frontend/course/chapter_01/lesson42.css').length, 1);
});

test('platform entry animations cannot become an exemption for component colors', () => {
  assert.deepEqual(checkCssOwnership('.lesson-transition .rich-lesson { animation: appear 180ms ease; }', 'apps/web/src/features/learning/lesson-loading.css'), []);
  assert.equal(checkCssOwnership('.lesson-transition .rich-lesson { animation: appear 180ms ease; color: red; }', 'apps/web/src/features/learning/lesson-loading.css').length, 1);
});
