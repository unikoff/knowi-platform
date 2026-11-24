'use strict';
const fs = require('node:fs');
const path = require('node:path');
let ts;
try { ts = require('../../apps/web/node_modules/typescript'); } catch (_) { /* reported to caller */ }
const result = (status, detail) => ({ status, detail });
const practiceTitle = 'Что мы будем делать в практике';
const interactive = new Set(['QuizCard','TrueFalse','BugHunt','FillBlank','BranchExplorer','SliceExplorer','CodeSequence','MatchPairs','PredictOutput','StepThrough','RecallCard','CompareSolutions','TerminalDemo','FlipCards','MutationScene']);
const visual = new Set(['Diagram','BranchExplorer','SliceExplorer','MutationScene']);
const transparent = new Set(['RichLesson','RichHero','Section','Diagram','Lead','LinkedNotes','ContentList','Callout','TypeCards','TypeCard','MethodGrid','PracticeCta','KeyTakeaways','TheoryBridge','CodeBlock','DiagramFlow','DiagramNode','DiagramArrow','DiagramGroup','DiagramLane','DiagramBranches','DiagramBranch','DiagramNote','DiagramLegend','DiagramExchange','DiagramTrack','InteractiveTable',...interactive]);
const practiceBlocks = new Set(['CodeBlock', 'pre', 'details', 'Section', 'Checklist', 'KeyTakeaways', ...interactive]);

function checkLesson(source, componentName) {
  if (!ts) return [result('UNKNOWN', 'TypeScript compiler API is unavailable')];
  const file = ts.createSourceFile('lesson.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  if (file.parseDiagnostics.length) return file.parseDiagnostics.map(d => result('FAIL', `Syntax error at ${file.getLineAndCharacterOfPosition(d.start || 0).line + 1}: ${ts.flattenDiagnosticMessageText(d.messageText, ' ')}`));
  const constants = new Map(), componentAliases = new Map();
  let component, exported = false;
  const hasExport = node => node.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword);
  for (const statement of file.statements) {
    if (ts.isImportDeclaration(statement) && ts.isStringLiteral(statement.moduleSpecifier) && /(?:\/shared|^@course-ui|^@platform\/shared\/ui\/code-editor\/CodeEditor)$/.test(statement.moduleSpecifier.text)) {
      const bindings = statement.importClause?.namedBindings;
      if (bindings && ts.isNamedImports(bindings)) for (const item of bindings.elements) componentAliases.set(item.name.text, (item.propertyName || item.name).text);
    }
    if (ts.isFunctionDeclaration(statement) && statement.name?.text === componentName) { component = statement; exported ||= !!hasExport(statement); }
    if (ts.isVariableStatement(statement) && (statement.declarationList.flags & ts.NodeFlags.Const)) {
      for (const d of statement.declarationList.declarations) if (ts.isIdentifier(d.name)) {
        constants.set(d.name.text, d.initializer);
        if (d.name.text === componentName) { component = d.initializer; exported ||= !!hasExport(statement); }
      }
    }
    if (ts.isExportDeclaration(statement) && !statement.moduleSpecifier && statement.exportClause && ts.isNamedExports(statement.exportClause)) {
      exported ||= statement.exportClause.elements.some(e => (e.propertyName || e.name).text === componentName);
    }
    if (ts.isExportAssignment(statement) && ts.isIdentifier(statement.expression) && statement.expression.text === componentName) exported = true;
  }
  if (!component || !exported) return [result('UNKNOWN', `Exported component ${componentName} not found`)];
  const unwrap = n => { while (n && (ts.isParenthesizedExpression(n) || ts.isAsExpression(n) || ts.isSatisfiesExpression(n) || ts.isNonNullExpression(n))) n = n.expression; return n; };
  component = unwrap(component);
  if (!ts.isFunctionDeclaration(component) && !ts.isFunctionExpression(component) && !ts.isArrowFunction(component)) return [result('UNKNOWN', 'Component declaration is dynamic')];
  if (component.body && ts.isBlock(component.body)) for (const s of component.body.statements) if (ts.isVariableStatement(s) && (s.declarationList.flags & ts.NodeFlags.Const)) for (const d of s.declarationList.declarations) if (ts.isIdentifier(d.name)) constants.set(d.name.text, d.initializer);
  const resolve = node => { node = unwrap(node); const seen = new Set(); while (node && ts.isIdentifier(node) && constants.has(node.text) && !seen.has(node.text)) { seen.add(node.text); node = unwrap(constants.get(node.text)); } return node; };
  const literal = node => { node = unwrap(node); return node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) ? node.text : undefined; };
  const attr = (opening, name) => { const attrs = opening.attributes.properties; const a = attrs.find(p => ts.isJsxAttribute(p) && p.name.text === name); if (!a) return attrs.some(ts.isJsxSpreadAttribute) ? { unknown: true } : { missing: true }; if (!a.initializer) return { unknown: true }; const n = ts.isJsxExpression(a.initializer) ? a.initializer.expression : a.initializer; return { node: n }; };
  const property = (obj, name) => { if (!obj || !ts.isObjectLiteralExpression(obj)) return undefined; const p = obj.properties.find(x => ts.isPropertyAssignment(x) && (ts.isIdentifier(x.name) || ts.isStringLiteral(x.name)) && x.name.text === name); return p?.initializer; };
  const elementName = opening => {
    const tag = opening.tagName.getText(file);
    return componentAliases.get(tag) ?? tag;
  };
  const isText = node => ts.isJsxText(node) || ts.isStringLiteral(node) || ts.isNumericLiteral(node) || [ts.SyntaxKind.NullKeyword, ts.SyntaxKind.FalseKeyword, ts.SyntaxKind.TrueKeyword].includes(node.kind);
  const notesState = (opening, isIntro) => {
    const variant = attr(opening, 'variant'), items = attr(opening, 'items');
    if (opening.attributes.properties.some(p => ts.isJsxAttribute(p) && p.name.text === 'variant')) return 'FAIL';
    if (items.missing) return 'FAIL';
    if (variant.unknown || items.unknown) return 'UNKNOWN';
    const arr = resolve(items.node); if (!arr || !ts.isArrayLiteralExpression(arr) || arr.elements.some(ts.isSpreadElement)) return 'UNKNOWN';
    if (!isIntro) return arr.elements.some(item => !ts.isOmittedExpression(item)) ? 'PASS' : 'FAIL';
    if (arr.elements.length !== 3) return 'FAIL';
    const titles = arr.elements.map(x => literal(property(resolve(x), 'title')));
    if (titles.some(x => x === undefined)) return 'UNKNOWN';
    return titles.every((x, i) => x === ['Точка старта','Новый вопрос','Результат'][i]) ? 'PASS' : 'FAIL';
  };
  const stepState = opening => {
    const a = attr(opening, 'steps'); if (a.missing) return 'FAIL'; if (a.unknown) return 'UNKNOWN';
    const steps = resolve(a.node); if (!steps || !ts.isArrayLiteralExpression(steps) || steps.elements.some(ts.isSpreadElement)) return 'UNKNOWN';
    let unknown = false;
    for (const item of steps.elements) {
      const obj = resolve(item); if (!obj || !ts.isObjectLiteralExpression(obj)) { unknown = true; continue; }
      const vars = property(obj, 'vars'); if (!vars) { if (obj.properties.some(ts.isSpreadAssignment)) unknown = true; continue; }
      const val = resolve(vars); if (!val || !ts.isObjectLiteralExpression(val)) { unknown = true; continue; }
      if (val.properties.some(p => !ts.isSpreadAssignment(p))) return 'PASS';
      if (val.properties.some(ts.isSpreadAssignment)) unknown = true;
    }
    return unknown ? 'UNKNOWN' : 'FAIL';
  };
  const tableState = opening => {
    if (opening.attributes.properties.some(ts.isJsxSpreadAttribute)) return 'UNKNOWN';
    const length = name => {
      let value = resolve(attr(opening, name).node);
      // Mapping a known dense array with an inline callback preserves its length.
      if (value && ts.isCallExpression(value) && ts.isPropertyAccessExpression(value.expression) && value.expression.name.text === 'map') {
        const callback = value.arguments[0];
        if (callback && (ts.isArrowFunction(callback) || ts.isFunctionExpression(callback))) value = resolve(value.expression.expression);
      }
      if (value && ts.isArrayLiteralExpression(value) && !value.elements.some(element => ts.isSpreadElement(element) || ts.isOmittedExpression(element))) return value.elements.length;
      return undefined;
    };
    const columns = length('columns'), rows = length('rows');
    if (columns < 2 || rows === 0) return 'FAIL';
    return columns === undefined || rows === undefined ? 'UNKNOWN' : 'PASS';
  };

  const inspectPractice = section => {
    const seen = new Set(), notes = [];
    let problem, unknown = false;
    const visit = node => {
      node = resolve(node);
      if (!node || problem || seen.has(node) || isText(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isOmittedExpression(node)) return;
      seen.add(node);
      if (ts.isJsxExpression(node)) return visit(node.expression);
      if (ts.isJsxFragment(node)) return node.children.forEach(visit);
      if (ts.isArrayLiteralExpression(node)) return node.elements.forEach(visit);
      const conditional = ts.isConditionalExpression(node);
      const conjunction = ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.AmpersandAmpersandToken;
      if (conditional || conjunction) {
        const condition = resolve(conditional ? node.condition : node.left);
        if (condition?.kind === ts.SyntaxKind.TrueKeyword) return visit(conditional ? node.whenTrue : node.right);
        if (condition?.kind === ts.SyntaxKind.FalseKeyword) return visit(conditional ? node.whenFalse : undefined);
      }
      if (!ts.isJsxElement(node) && !ts.isJsxSelfClosingElement(node)) { unknown = true; return; }
      const opening = ts.isJsxElement(node) ? node.openingElement : node;
      const name = elementName(opening);
      const hasSpread = opening.attributes.properties.some(ts.isJsxSpreadAttribute);
      const role = attr(opening, 'role');
      const labelledSection = name === 'section' && ['aria-label', 'aria-labelledby'].some(label => literal(resolve(attr(opening, label).node))?.trim());
      if (practiceBlocks.has(name) || /^h[1-6]$/.test(name) || (!hasSpread && (labelledSection || literal(resolve(role.node)) === 'heading'))) {
        problem = `<${name}> at line ${file.getLineAndCharacterOfPosition(opening.getStart(file)).line + 1}`;
        return;
      }
      if (hasSpread || (!role.missing && literal(resolve(role.node)) === undefined) || !attr(opening, 'dangerouslySetInnerHTML').missing) unknown = true;
      if (name === 'LinkedNotes' || name === 'ContentList') {
        if (name === 'LinkedNotes') notes.push(notesState(opening, false));
        const items = attr(opening, 'items');
        const entries = resolve(items.node);
        if (items.missing) return;
        if (!entries || !ts.isArrayLiteralExpression(entries)) { unknown = true; return; }
        for (const entry of entries.elements) {
          const item = resolve(entry);
          if (!item || !ts.isObjectLiteralExpression(item) || item.properties.some(prop => !ts.isPropertyAssignment(prop) || ts.isComputedPropertyName(prop.name))) {
            unknown = true;
            continue;
          }
          visit(property(item, 'title'));
          visit(property(item, 'description'));
        }
        if (name === 'LinkedNotes') return;
      }
      if (['InteractiveTable', 'PracticeCta', 'TheoryBridge', 'VisualHeading', 'template'].includes(name)) return;
      if (!transparent.has(name) && name !== 'React.Fragment' && !/^[a-z][\w-]*$/.test(name)) { unknown = true; return; }
      if (ts.isJsxElement(node) && node.children.length) node.children.forEach(visit);
      else visit(attr(opening, 'children').node);
    };
    if (ts.isJsxElement(section) && section.children.length) section.children.forEach(visit);
    else visit(attr(ts.isJsxElement(section) ? section.openingElement : section, 'children').node);
    return { problem, unknown, notes };
  };
  let roots = [];
  if (!component.body) return [result('UNKNOWN', 'Component has no body')];
  if (ts.isBlock(component.body)) {
    let complex = false;
    for (const s of component.body.statements) {
      if (ts.isReturnStatement(s)) roots.push(s.expression);
      else if (!ts.isVariableStatement(s)) complex = true;
    }
    if (complex || roots.length > 1) return [result('UNKNOWN', 'Conditional or complex component return')];
  } else roots = [component.body];
  const sections = []; let unknownGlobal = false;
  const walk = (node, section) => {
    node = unwrap(node); if (!node) return;
    if (ts.isJsxExpression(node)) { if (node.expression) walk(node.expression, section); return; }
    if (ts.isJsxFragment(node)) { node.children.forEach(n => walk(n, section)); return; }
    if (ts.isJsxElement(node) || ts.isJsxSelfClosingElement(node)) {
      const opening = ts.isJsxElement(node) ? node.openingElement : node;
      const tag = opening.tagName.getText(file);
      const name = componentAliases.get(tag) === 'InteractiveTable' ? 'InteractiveTable' : tag;
      let current = section;
      if (name === 'Section') { current = { opening, node, parent: section, notes: [], visuals: [], interactions: [], unknown: false }; sections.push(current); }
      else if (name === 'LinkedNotes' && current) current.notes.push(opening);
      if (current && interactive.has(name)) current.interactions.push('PASS');
      if (current && visual.has(name)) current.visuals.push('PASS');
      if (current && (name === 'img' || name === 'svg')) {
        const hidden = attr(opening, 'hidden'), style = attr(opening, 'style');
        if (!hidden.missing || !style.missing) current.visuals.push('UNKNOWN');
        else current.visuals.push('PASS');
      }
      if (current && name === 'StepThrough') current.visuals.push(stepState(opening));
      if (current && name === 'InteractiveTable') current.visuals.push(tableState(opening));
      const unknownWrapper = /^[A-Z]/.test(name) && !transparent.has(name);
      if (unknownWrapper) { if (current) current.unknown = true; else unknownGlobal = true; }
      if (!unknownWrapper && ts.isJsxElement(node) && name !== 'CodeBlock' && name !== 'LinkedNotes' && name !== 'InteractiveTable' && !interactive.has(name)) node.children.forEach(n => walk(n, current));
      return;
    }
    if (isText(node)) return;
    // Expressions, including arrays of JSX, conditionals, identifiers and maps, are not executed.
    if (section) section.unknown = true; else unknownGlobal = true;
  };
  walk(roots[0], null);
  const num = s => { const a = attr(s.opening, 'number'); return a.missing ? null : a.unknown ? undefined : literal(a.node); };
  const title = s => { const a = attr(s.opening, 'title'); return a.missing ? null : a.unknown ? undefined : literal(a.node); };
  const first = sections[0], last = sections.filter(section => !section.parent).at(-1);
  const introNotes = first?.notes.map(opening => notesState(opening, true)) ?? [];
  let intro = !first ? (unknownGlobal ? 'UNKNOWN' : 'FAIL') : num(first) === undefined ? 'UNKNOWN' : num(first) !== '00' ? (unknownGlobal ? 'UNKNOWN' : 'FAIL') : introNotes.length > 1 ? 'FAIL' : first.unknown || introNotes.includes('UNKNOWN') ? 'UNKNOWN' : introNotes[0] === 'PASS' ? 'PASS' : 'FAIL';
  if (unknownGlobal && intro === 'PASS') intro = 'UNKNOWN';
  const endingDetail = `Last Section: ${practiceTitle} with non-empty LinkedNotes`;
  const ending = () => {
    if (unknownGlobal) return result('UNKNOWN', endingDetail);
    if (!last) return result('FAIL', endingDetail);
    const lastTitle = title(last);
    if (lastTitle === undefined) return result('UNKNOWN', endingDetail);
    if (lastTitle !== practiceTitle) return result('FAIL', endingDetail);
    if (last.opening.attributes.properties.some(ts.isJsxSpreadAttribute)) return result('UNKNOWN', endingDetail);
    const { problem, unknown, notes } = inspectPractice(last.node);
    if (problem) return result('FAIL', `${endingDetail}. Unexpected block in the practice plan: ${problem}`);
    if (unknown || notes.includes('UNKNOWN')) return result('UNKNOWN', `${endingDetail}. Some practice plan content is dynamic`);
    return result(notes.includes('PASS') ? 'PASS' : 'FAIL', endingDetail);
  };
  const outside = sections.filter(s => num(s) !== '00');
  const evidence = key => outside.some(s => num(s) !== undefined && s[key].includes('PASS')) ? 'PASS' : unknownGlobal || outside.some(s => num(s) === undefined || s.unknown || s[key].includes('UNKNOWN')) ? 'UNKNOWN' : 'FAIL';
  const introInteractive = sections.some(s => num(s) === '00' && s.interactions.includes('PASS')) ? 'FAIL' : unknownGlobal || sections.some(s => num(s) === undefined || (num(s) === '00' && s.unknown)) ? 'UNKNOWN' : 'PASS';
  return [result(intro, 'First Section 00: connected LinkedNotes with three ordered titles'), ending(), result(evidence('visuals'), 'Educational visual outside Section 00'), result(evidence('interactions'), 'Interactive outside Section 00'), result(introInteractive, 'No interactive in Section 00')];
}
module.exports = { checkLesson };
if (require.main === module) {
  let output;
  try { const filename = process.argv[2]; if (!filename) throw new Error('Expected TSX path'); output = checkLesson(fs.readFileSync(filename, 'utf8'), path.parse(filename).name); }
  catch (e) { output = [result('UNKNOWN', `Cannot read lesson: ${e.message}`)]; }
  process.stdout.write(JSON.stringify(output) + '\n');
}



