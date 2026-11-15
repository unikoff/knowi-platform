'use strict';
const fs = require('node:fs');
const path = require('node:path');
let ts;
try { ts = require('../../apps/web/node_modules/typescript'); } catch (_) { /* reported to caller */ }
const result = (status, detail) => ({ status, detail });
const interactive = new Set(['QuizCard','TrueFalse','BugHunt','FillBlank','BranchExplorer','SliceExplorer','CodeSequence','MatchPairs','PredictOutput','StepThrough','RecallCard','CompareSolutions','TerminalDemo','FlipCards','MutationScene']);
const visual = new Set(['Diagram','BranchExplorer','SliceExplorer','MutationScene']);
const transparent = new Set(['RichLesson','RichHero','Section','Diagram','Lead','LinkedNotes','Callout','TypeCards','TypeCard','MethodGrid','PracticeCta','KeyTakeaways','TheoryBridge','CodeBlock','DiagramFlow','DiagramNode','DiagramArrow','DiagramGroup','DiagramLane','DiagramBranches','DiagramBranch','DiagramNote','DiagramLegend','DiagramExchange','DiagramTrack',...interactive]);

function checkLesson(source, componentName) {
  if (!ts) return [result('UNKNOWN', 'TypeScript compiler API is unavailable')];
  const file = ts.createSourceFile('lesson.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  if (file.parseDiagnostics.length) return file.parseDiagnostics.map(d => result('FAIL', `Syntax error at ${file.getLineAndCharacterOfPosition(d.start || 0).line + 1}: ${ts.flattenDiagnosticMessageText(d.messageText, ' ')}`));
  const constants = new Map();
  let component, exported = false;
  const hasExport = node => node.modifiers?.some(m => m.kind === ts.SyntaxKind.ExportKeyword);
  for (const statement of file.statements) {
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
  const notesState = opening => {
    const variant = attr(opening, 'variant'), items = attr(opening, 'items');
    if (variant.missing || items.missing) return 'FAIL';
    if (variant.unknown || items.unknown) return 'UNKNOWN';
    const v = literal(variant.node); if (v === undefined) return 'UNKNOWN'; if (v !== 'connected') return 'FAIL';
    const arr = resolve(items.node); if (!arr || !ts.isArrayLiteralExpression(arr) || arr.elements.some(ts.isSpreadElement)) return 'UNKNOWN';
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
      const name = opening.tagName.getText(file);
      let current = section;
      if (name === 'Section') { current = { opening, notes: [], visuals: [], interactions: [], unknown: false }; sections.push(current); }
      else if (name === 'LinkedNotes' && current) current.notes.push(notesState(opening));
      if (current && interactive.has(name)) current.interactions.push('PASS');
      if (current && visual.has(name)) current.visuals.push('PASS');
      if (current && (name === 'img' || name === 'svg')) {
        const hidden = attr(opening, 'hidden'), style = attr(opening, 'style');
        if (!hidden.missing || !style.missing) current.visuals.push('UNKNOWN');
        else current.visuals.push('PASS');
      }
      if (current && name === 'StepThrough') current.visuals.push(stepState(opening));
      const unknownWrapper = /^[A-Z]/.test(name) && !transparent.has(name);
      if (unknownWrapper) { if (current) current.unknown = true; else unknownGlobal = true; }
      if (!unknownWrapper && ts.isJsxElement(node) && name !== 'CodeBlock' && name !== 'LinkedNotes' && !interactive.has(name)) node.children.forEach(n => walk(n, current));
      return;
    }
    if (ts.isJsxText(node) || ts.isStringLiteral(node) || ts.isNumericLiteral(node) || node.kind === ts.SyntaxKind.NullKeyword || node.kind === ts.SyntaxKind.FalseKeyword || node.kind === ts.SyntaxKind.TrueKeyword) return;
    // Expressions, including arrays of JSX, conditionals, identifiers and maps, are not executed.
    if (section) section.unknown = true; else unknownGlobal = true;
  };
  walk(roots[0], null);
  const num = s => { const a = attr(s.opening, 'number'); return a.missing ? null : a.unknown ? undefined : literal(a.node); };
  const title = s => { const a = attr(s.opening, 'title'); return a.missing ? null : a.unknown ? undefined : literal(a.node); };
  const first = sections[0], last = sections.at(-1);
  let intro = !first ? (unknownGlobal ? 'UNKNOWN' : 'FAIL') : num(first) === undefined ? 'UNKNOWN' : num(first) !== '00' ? (unknownGlobal ? 'UNKNOWN' : 'FAIL') : first.notes.length > 1 ? 'FAIL' : first.unknown || first.notes.includes('UNKNOWN') ? 'UNKNOWN' : first.notes[0] === 'PASS' ? 'PASS' : 'FAIL';
  if (unknownGlobal && intro === 'PASS') intro = 'UNKNOWN';
  let ending = !last ? (unknownGlobal ? 'UNKNOWN' : 'FAIL') : title(last) === undefined ? 'UNKNOWN' : title(last) === 'Что мы будем делать в практике' ? (unknownGlobal ? 'UNKNOWN' : 'PASS') : (unknownGlobal ? 'UNKNOWN' : 'FAIL');
  const outside = sections.filter(s => num(s) !== '00');
  const evidence = key => outside.some(s => num(s) !== undefined && s[key].includes('PASS')) ? 'PASS' : unknownGlobal || outside.some(s => num(s) === undefined || s.unknown || s[key].includes('UNKNOWN')) ? 'UNKNOWN' : 'FAIL';
  const introInteractive = sections.some(s => num(s) === '00' && s.interactions.includes('PASS')) ? 'FAIL' : unknownGlobal || sections.some(s => num(s) === undefined || (num(s) === '00' && s.unknown)) ? 'UNKNOWN' : 'PASS';
  return [result(intro, 'First Section 00: connected LinkedNotes with three ordered titles'), result(ending, 'Last Section: Что мы будем делать в практике'), result(evidence('visuals'), 'Educational visual outside Section 00'), result(evidence('interactions'), 'Interactive outside Section 00'), result(introInteractive, 'No interactive in Section 00')];
}
module.exports = { checkLesson };
if (require.main === module) {
  let output;
  try { const filename = process.argv[2]; if (!filename) throw new Error('Expected TSX path'); output = checkLesson(fs.readFileSync(filename, 'utf8'), path.parse(filename).name); }
  catch (e) { output = [result('UNKNOWN', `Cannot read lesson: ${e.message}`)]; }
  process.stdout.write(JSON.stringify(output) + '\n');
}



