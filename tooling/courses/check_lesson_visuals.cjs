'use strict';
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../../apps/web/node_modules/typescript');

const root = path.resolve(__dirname, '../..');
const libraryStyles = 'courses/shared/ui/components/styles/';
const structuralClasses = /^(?:rich-(?:hero(?:-.*)?|lesson|section(?:-.*)?|lead)|lesson-callout(?:--.*)?|linked-notes?|takeaways(?:-.*)?|lesson-list|tracer(?:-.*)?)$/;
const retiredClasses = /^(?:lesson-callout--info|linked-notes--(?:cards|connected)|lesson-route(?:-.*)?|task-spec__list(?:--.*)?)$/;

function filesIn(directory, extension) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const name = path.join(directory, entry.name);
    return entry.isDirectory() ? filesIn(name, extension) : name.endsWith(extension) ? [name] : [];
  });
}

function checkLessonSource(source, filename = 'lesson.tsx') {
  const file = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const problems = [];
  const names = new Map();
  const add = (node, message) => problems.push({ file: filename, line: file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1, message });
  for (const node of file.statements) {
    if (!ts.isImportDeclaration(node) || !ts.isStringLiteral(node.moduleSpecifier) || !/(?:\/shared|@course-ui)$/.test(node.moduleSpecifier.text)) continue;
    const bindings = node.importClause?.namedBindings;
    if (bindings && ts.isNamedImports(bindings)) for (const item of bindings.elements) names.set(item.name.text, (item.propertyName || item.name).text);
  }
  function visit(node) {
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      const tag = node.tagName.getText(file);
      const component = names.get(tag) || tag;
      for (const attribute of node.attributes.properties) {
        if (!ts.isJsxAttribute(attribute)) continue;
        const name = attribute.name.getText(file);
        const value = attribute.initializer && ts.isJsxExpression(attribute.initializer) ? attribute.initializer.expression : attribute.initializer;
        if (component === 'Callout' && name === 'tone' && value && ts.isStringLiteral(value) && value.text === 'info') add(attribute, 'Use Callout tone="success"; the info alias has been removed.');
        if (['LinkedNotes', 'RichHero'].includes(component) && name === 'variant') add(attribute, `${component} has one appearance; remove variant.`);
        if (name === 'className' && value) {
          function checkClass(part) {
            if (ts.isStringLiteral(part) || ts.isNoSubstitutionTemplateLiteral(part) || ts.isTemplateHead(part) || ts.isTemplateMiddle(part) || ts.isTemplateTail(part)) {
              for (const token of part.text.split(/\s+/)) {
                if (retiredClasses.test(token)) add(attribute, `Retired visual class: ${token}.`);
                else if (/^[a-z]/.test(tag) && structuralClasses.test(token)) add(attribute, `Use the shared component instead of hand-written ${token} markup.`);
              }
            }
            ts.forEachChild(part, checkClass);
          }
          checkClass(value);
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(file);
  return problems;
}

function checkCssOwnership(source, filename) {
  if (filename.replaceAll('\\', '/').startsWith(libraryStyles)) return [];
  const problems = [];
  const uncommented = source.replace(/\/\*[\s\S]*?\*\//g, match => match.replace(/[^\n]/g, ' '));
  for (const match of uncommented.matchAll(/\.([a-zA-Z_][\w-]*)/g)) {
    const token = match[1];
    const tail = uncommented.slice(match.index + match[0].length).split('{')[0].split(',')[0];
    // A library container may scope a lesson's own diagram, not restyle the container.
    if (/^rich-(?:lesson|section)$/.test(token) && /^\s+(?:[>+~]\s*)?\.[\w-]+/.test(tail)) continue;
    // Platform loading transitions own entry/exit animation, not the component skin.
    if (token === 'rich-lesson' && ['apps/web/src/app/styles/motion.css', 'apps/web/src/features/learning/lesson-loading.css'].includes(filename.replaceAll('\\', '/'))) {
      const declarations = uncommented.slice(match.index).match(/^[^{]*\{([^{}]*)\}/)?.[1];
      if (declarations && !declarations.replace(/animation(?:-[\w-]+)?\s*:[^;]+;?/g, '').trim()) continue;
    }
    if (structuralClasses.test(token) || retiredClasses.test(token) || /^(?:answer-option|quiz-option|fill-option|match-item|interactive-primary|interactive-secondary)(?:-|$)/.test(token)) {
      problems.push({ file: filename, line: source.slice(0, match.index).split('\n').length, message: `Shared visual style ${token} must be defined in ${libraryStyles}.` });
    }
  }
  return problems;
}

function checkPaletteUsage(source, filename, knownTokens) {
  const problems = [];
  const uncommented = source.replace(/\/\*[\s\S]*?\*\//g, match => match.replace(/[^\n]/g, ' '));
  const add = (index, message) => problems.push({ file: filename, line: source.slice(0, index).split('\n').length, message });
  for (const match of uncommented.matchAll(/#[\da-f]{3,8}\b|\b(?:rgb|hsl)a?\(/gi)) {
    add(match.index, 'Shared UI colors must consume platform theme tokens, including syntax and status roles.');
  }
  for (const match of uncommented.matchAll(/var\((--[\w-]+)/g)) {
    if (!knownTokens.has(match[1])) add(match.index, `Unknown visual token: ${match[1]}.`);
  }
  return problems;
}

function checkProject() {
  const relative = name => path.relative(root, name).replaceAll('\\', '/');
  const lessonFiles = filesIn(path.join(root, 'courses'), '.tsx').filter(name => /[\\/]frontend[\\/]course[\\/]/.test(name));
  const learningFiles = filesIn(path.join(root, 'apps/web/src/features/learning'), '.tsx');
  const problems = [...lessonFiles, ...learningFiles].flatMap(name => checkLessonSource(fs.readFileSync(name, 'utf8'), relative(name)));
  const courseCss = filesIn(path.join(root, 'courses'), '.css').filter(name => /[\\/]frontend[\\/]/.test(name));
  const sharedFiles = [...filesIn(path.join(root, 'courses/shared/ui'), '.css'), ...filesIn(path.join(root, 'courses/shared/demo'), '.css')];
  for (const name of [...filesIn(path.join(root, 'apps/web/src'), '.css'), ...courseCss, ...sharedFiles]) problems.push(...checkCssOwnership(fs.readFileSync(name, 'utf8'), relative(name)));
  // Local geometry/state variables may live in CSS or inline style objects.
  // Semantic color values always come from the existing platform palettes.
  const palette = fs.readFileSync(path.join(root, 'apps/web/src/shared/theme/palettes.ts'), 'utf8');
  const knownTokens = new Set([...palette.matchAll(/"([\w-]+)"\s*:/g)].map(match => `--${match[1]}`));
  const variableSources = [path.join(root, 'apps/web/src/app/styles/tokens.css'), ...sharedFiles, ...filesIn(path.join(root, 'courses/shared/ui'), '.tsx'), ...filesIn(path.join(root, 'courses/shared/demo'), '.tsx')];
  for (const name of variableSources) {
    for (const match of fs.readFileSync(name, 'utf8').matchAll(/(--[\w-]+)["']?\s*:/g)) knownTokens.add(match[1]);
  }
  for (const name of sharedFiles) problems.push(...checkPaletteUsage(fs.readFileSync(name, 'utf8'), relative(name), knownTokens));
  const apiPath = 'courses/shared/ui/index.ts';
  const api = ts.createSourceFile(apiPath, fs.readFileSync(path.join(root, apiPath), 'utf8'), ts.ScriptTarget.Latest, true);
  if (!api.statements.some(node => ts.isImportDeclaration(node) && node.moduleSpecifier.text === './components/styles/index.css')) {
    problems.push({ file: apiPath, line: 1, message: 'The shared API must load the canonical visual stylesheet.' });
  }
  const entry = fs.readFileSync(path.join(root, libraryStyles, 'index.css'), 'utf8');
  for (const name of fs.readdirSync(path.join(root, libraryStyles)).filter(name => name.endsWith('.css') && name !== 'index.css')) {
    if (!entry.includes(`"./${name}"`)) problems.push({ file: `${libraryStyles}index.css`, line: 1, message: `Missing shared stylesheet: ${name}.` });
  }
  for (const name of ['apps/web/src/features/learning/theory/lesson-theory.css', 'apps/web/src/features/learning/practice/lesson-interactions.css']) {
    const source = fs.readFileSync(path.join(root, name), 'utf8');
    if (/components\/styles\/(?!index\.css)[^"']+\.css/.test(source)) problems.push({ file: name, line: 1, message: 'Do not maintain a second list of component styles; use the canonical stylesheet.' });
  }
  return { lessons: lessonFiles.length, problems };
}

if (require.main === module) {
  const { lessons, problems } = checkProject();
  for (const problem of problems) console.error(`${problem.file}:${problem.line}: ${problem.message}`);
  if (problems.length) process.exitCode = 1;
  else console.log(`Lesson visual library: ${lessons} source modules checked; no retired APIs, duplicated list markup or external style overrides.`);
}

module.exports = { checkLessonSource, checkCssOwnership, checkPaletteUsage, checkProject };
