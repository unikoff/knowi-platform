'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const { pathToFileURL } = require('node:url');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../..');
const app = path.join(root, 'apps/web');
const requireFromApp = createRequire(path.join(app, 'package.json'));
const ts = requireFromApp('typescript');
const React = requireFromApp('react');
const { renderToStaticMarkup } = requireFromApp('react-dom/server');
const loaded = new Map();

// Run the real local TSX through the installed compiler and React SSR. CSS is
// checked separately by check_lesson_visuals; this harness is not browser QA.
function loadLocal(request, parent = root) {
  let candidate;
  if (request === '@course-ui') candidate = path.join(root, 'courses/shared/ui/index');
  else if (request.startsWith('@course-ui/')) candidate = path.join(root, 'courses/shared/ui', request.slice(11));
  else if (request.startsWith('@platform/')) candidate = path.join(app, 'src', request.slice(10));
  else if (request.startsWith('.') || path.isAbsolute(request)) candidate = path.resolve(parent, request);
  else return requireFromApp(request);
  const filename = [candidate, `${candidate}.ts`, `${candidate}.tsx`, path.join(candidate, 'index.ts'), path.join(candidate, 'index.tsx')]
    .find(name => fs.existsSync(name) && fs.statSync(name).isFile());
  assert.ok(filename, `Missing UI dependency: ${request}`);
  if (filename.endsWith('.css')) return {};
  if (loaded.has(filename)) return loaded.get(filename).exports;
  const module = { exports: {} };
  loaded.set(filename, module);
  const source = fs.readFileSync(filename, 'utf8').replaceAll('import.meta.url', JSON.stringify(pathToFileURL(filename).href));
  const compiled = ts.transpileModule(source, { compilerOptions: {
    jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
  } }).outputText;
  new Function('require', 'module', 'exports', compiled)(request => loadLocal(request, path.dirname(filename)), module, module.exports);
  return module.exports;
}

const ui = loadLocal('@course-ui');
const hero = () => React.createElement(ui.RichHero, { title: 'Новая тема', intro: 'Объяснение', chip: 'Авторская подпись' });
function renderHero(props) {
  return renderToStaticMarkup(React.createElement(ui.LessonMetadataProvider, props, hero()));
}

test('course resolver supplies hero metadata and course tags; null stays null', () => {
  let received;
  const html = renderHero({ source: 'lesson', module: 'Module', title: 'Catalog title', resolveMetadata: (...args) => {
    received = args;
    return { block: 'Другой курс', blockNumber: 9, lessonNumber: '8.2', tags: ['своя тема'] };
  } });
  assert.deepEqual(received, ['lesson', 'Module', 'Catalog title']);
  assert.match(html, /Другой курс/);
  assert.match(html, /своя тема/);
  assert.doesNotMatch(html, /Авторская подпись|Python|FastAPI/);
  const empty = renderHero({ source: 'chapter_01/block_01/Lesson01', resolveMetadata: () => null });
  assert.match(empty, /Авторская подпись/);
  assert.doesNotMatch(empty, /Блок 1|Темы занятия/);
});

test('absent resolver uses neutral fallback without leaking previous course metadata', () => {
  const html = renderHero({ source: 'chapter_03/block_10/Lesson54' });
  assert.match(html, /Блок 10/);
  assert.match(html, /тема занятия/);
  assert.doesNotMatch(html, /Postman|FastAPI|Другой курс|своя тема/);
});

test('both course facades render their current lesson with course-owned metadata', () => {
  for (const course of ['backend', 'backend_copy']) {
    const courseDir = path.join(root, 'courses', course, 'frontend/course');
    const { getLessonMetadata } = loadLocal(path.join(courseDir, 'metadata'));
    const { Lesson01 } = loadLocal(path.join(courseDir, 'chapter_01/block_01/Lesson01'));
    const html = renderToStaticMarkup(React.createElement(ui.LessonMetadataProvider, {
      source: 'chapter_01/block_01/Lesson01', title: 'Как запускается программа', resolveMetadata: getLessonMetadata,
    }, React.createElement(Lesson01)));
    assert.match(html, /Первые шаги в Python/);
    assert.match(html, /Как запускается программа/);
    assert.match(html, /Темы занятия/);
  }
});

test('every retained gallery variant renders through the real shared API', t => {
  const { catalogGroups } = loadLocal(path.join(root, 'courses/shared/demo/examples'));
  assert.equal(catalogGroups.length, 5);
  let tools = 0, variants = 0;
  for (const group of catalogGroups) {
    for (const item of group.examples) {
      assert.equal(typeof ui[item.api], 'function', item.api);
      tools += 1;
      for (const variant of item.variants) {
        assert.ok(renderToStaticMarkup(React.createElement(ui.RichLesson, null, variant.content)).length > 0, variant.id);
        variants += 1;
      }
    }
  }
  t.diagnostic(`${tools} tools and ${variants} gallery variants rendered`);
});

test('real platform theme bootstrap sets shared CSS variables for both schemes', () => {
  const { applyTheme, themes } = loadLocal(path.join(app, 'src/shared/theme/catalog'));
  const previous = global.document;
  try {
    for (const id of ['dark', 'light']) {
      const values = new Map();
      const element = { dataset: {}, style: { setProperty: (name, value) => values.set(name, value) } };
      global.document = { documentElement: element };
      applyTheme(id);
      assert.equal(element.dataset.theme, id);
      assert.equal(element.dataset.colorScheme, id);
      assert.equal(values.size, Object.keys(themes[id].palette).length);
      for (const role of ['--text', '--accent', '--negative', '--syntax-string', '--diagram-bg', '--surface-shadow']) {
        assert.ok(values.get(role), `${id}: ${role}`);
      }
    }
  } finally {
    if (previous === undefined) delete global.document;
    else global.document = previous;
  }
});
