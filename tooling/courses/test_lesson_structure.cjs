'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { checkLesson } = require('./lesson_structure.cjs');
const notes = `<LinkedNotes items={[{title:'Точка старта', description:'Начало'}, {title:'Новый вопрос', description:'Вопрос'}, {title:'Результат', description:'Итог'}]}/>`;
const intro = `<Section number="00">${notes}</Section>`;
const practiceNotes = `<LinkedNotes items={[{title:'Проверим резерв', description:'Убедимся, что копия читается'}]}/>`;
const practiceSection = content => `<Section title="Что мы будем делать в практике">${content}</Section>`;
const end = practiceSection(practiceNotes);
const body = `<Section number="01"><Diagram/><QuizCard/></Section>`;
const lesson = (middle = body, start = intro, finish = end) => `export function Lesson() { return <>${start}${middle}${finish}</>; }`;
const statuses = source => checkLesson(source, 'Lesson').map(x => x.status);
test('valid lesson and local const items', () => {
  assert.deepEqual(statuses(lesson()), ['PASS','PASS','PASS','PASS','PASS']);
  const source = lesson().replace(`items={[{title:'Точка старта', description:'Начало'}, {title:'Новый вопрос', description:'Вопрос'}, {title:'Результат', description:'Итог'}]}`, 'items={items}').replace('return <>', `const items = [{title:'Точка старта', description:'Начало'}, {title:'Новый вопрос', description:'Вопрос'}, {title:'Результат', description:'Итог'}]; return <>`);
  assert.equal(statuses(source)[0], 'PASS');
});
test('missing intro and wrong title order', () => {
  assert.equal(statuses(lesson(body, ''))[0], 'FAIL');
  assert.equal(statuses(lesson().replace('Точка старта', 'ошибка'))[0], 'FAIL');
});
test('last section must be the practice section', () => {
  assert.equal(statuses(lesson(body, intro, ''))[1], 'FAIL');
  assert.equal(statuses(lesson(body, intro, end + '<Section title="Другое"/>'))[1], 'FAIL');
});
test('a plain list cannot replace LinkedNotes in the practice section', () => {
  for (const tag of ['ol', 'ul']) {
    const plainList = `<${tag}><li>Проверим резерв</li></${tag}>`;
    assert.deepEqual(statuses(lesson(body, intro, practiceSection(plainList))).slice(0, 2), ['PASS','FAIL']);
  }
  assert.equal(statuses(lesson(body, intro, practiceSection('')))[1], 'FAIL');
  assert.equal(statuses(lesson(body, intro, practiceSection('<LinkedNotes items={[]}/>')))[1], 'FAIL');
  assert.equal(statuses(lesson(body, intro, practiceSection('<LinkedNotes/>')))[1], 'FAIL');
  assert.equal(statuses(lesson(body, intro, practiceSection('<ContentList items={[{description:"Проверим резерв"}]}/>')))[1], 'FAIL');
});
test('practice notes can use their own headings and an array constant', () => {
  const source = lesson(body, intro, practiceSection('<LinkedNotes items={steps}/>'))
    .replace('return <>', "const steps = [{title:'Сделаем копию', description:'Откроем новый файл'}, {title:'Проверим данные', description:'Сверим записи'}]; return <>");
  assert.equal(statuses(source)[1], 'PASS');
  assert.equal(statuses(lesson(body, intro, end.replace('<LinkedNotes ', '<LinkedNotes variant="connected" ')))[1], 'FAIL');
});
test('dynamic practice notes do not establish a verified practice section', () => {
  assert.equal(statuses(lesson(body, intro, practiceSection('<LinkedNotes items={dynamic}/>')))[1], 'UNKNOWN');
  assert.equal(statuses(lesson(body, intro, practiceSection('{showSteps && ' + practiceNotes + '}')))[1], 'UNKNOWN');
});
test('code and decoys do not satisfy visual or interactive', () => {
  const source = `import { Diagram, QuizCard } from './shared'; // <Diagram/><QuizCard/>\nconst unused = <Diagram/>; function Helper(){ return <QuizCard/> }\n` + lesson('<Section><CodeBlock code="<Diagram/>"/><LinkedNotes/><Callout/><MethodGrid/></Section>');
  assert.deepEqual(statuses(source).slice(2,4), ['FAIL','FAIL']);
});
test('no interactive and interactive in intro', () => {
  assert.equal(statuses(lesson('<Section><svg/></Section>'))[3], 'FAIL');
  assert.equal(statuses(lesson(body, intro.replace('</Section>', '<QuizCard/></Section>')))[4], 'FAIL');
});
test('dynamic and custom visual are unknown', () => {
  const s = statuses(lesson('<Section><CustomVisual/>{condition && <QuizCard/>}</Section>'));
  assert.deepEqual(s.slice(2,4), ['UNKNOWN','UNKNOWN']);
  assert.equal(statuses(lesson(body, intro, '{sections.map(render)}' + end))[1], 'UNKNOWN');
});
test('StepThrough counts as visual only with nonempty vars', () => {
  assert.equal(statuses(lesson('<Section><StepThrough steps={[{vars:{x:"1"}}]}/></Section>'))[2], 'PASS');
  assert.equal(statuses(lesson('<Section><StepThrough steps={[{vars:{}}]}/></Section>'))[2], 'FAIL');
  assert.equal(statuses(lesson('<Section><StepThrough steps={unknown}/></Section>'))[2], 'UNKNOWN');
});
test('JSX documentation strings and lists are not rendered elements', () => {
  const s = lesson('<Section><CodeBlock code="<Diagram/>"/>{docs}</Section>').replace('return <>', 'const docs = ["<QuizCard/>"]; return <>');
  assert.notEqual(statuses(s)[2], 'PASS'); assert.notEqual(statuses(s)[3], 'PASS');
});
test('syntax diagnostics are reported', () => {
  const row = checkLesson('export function Lesson( {', 'Lesson')[0];
  assert.match(row.detail, /Syntax error/);
  assert.equal(row.status, 'FAIL');
});

test('empty exported function is a missing lesson, not evidence of a frame', () => {
  assert.deepEqual(statuses('export function Lesson() {}'), ['FAIL','FAIL','FAIL','FAIL','PASS']);
});

test('side effects before rendering cannot prove literal title order', () => {
  const source = lesson().replace('return <>', 'items.reverse(); return <>');
  assert.ok(statuses(source).every(status => status === 'UNKNOWN'));
});

test('unrendered children and hidden image cannot establish a visual', () => {
  assert.equal(statuses(lesson('<Section><CodeBlock><svg/></CodeBlock></Section>'))[2], 'FAIL');
  assert.equal(statuses(lesson('<Section><img hidden /></Section>'))[2], 'UNKNOWN');
});
test('custom wrappers do not prove that children are rendered', () => {
  const s = statuses(lesson('<Section><Custom><Diagram/><QuizCard/></Custom></Section>'));
  assert.deepEqual(s.slice(2,4), ['UNKNOWN','UNKNOWN']);
});

test('a custom element inside the body does not hide the known outer frame', () => {
  const s = statuses(lesson('<Section number="01"><CustomVisual/></Section>'));
  assert.deepEqual(s, ['PASS','PASS','UNKNOWN','UNKNOWN','PASS']);
});

test('a second intro list does not satisfy the single connected list contract', () => {
  const s = statuses(lesson(body, intro.replace('</Section>', notes + '</Section>')));
  assert.equal(s[0], 'FAIL');
});

test('intro notes enforce ordered items and reject variant', () => {
  assert.equal(statuses(lesson().replace("{title:'Точка старта', description:'Начало'}, {title:'Новый вопрос', description:'Вопрос'}", "{title:'Новый вопрос', description:'Вопрос'}, {title:'Точка старта', description:'Начало'}"))[0], 'FAIL');
  assert.equal(statuses(lesson().replace(", {title:'Результат', description:'Итог'}", ''))[0], 'FAIL');
  assert.equal(statuses(lesson().replace('<LinkedNotes ', '<LinkedNotes variant="connected" '))[0], 'FAIL');
  assert.equal(statuses(lesson().replace('<LinkedNotes ', '<LinkedNotes variant={dynamic} '))[0], 'FAIL');
  assert.equal(statuses(lesson().replace('<LinkedNotes ', '<LinkedNotes {...props} '))[0], 'UNKNOWN');
  assert.equal(statuses(lesson().replace("items={[{title:'Точка старта', description:'Начало'}, {title:'Новый вопрос', description:'Вопрос'}, {title:'Результат', description:'Итог'}]}", 'items={dynamic}'))[0], 'UNKNOWN');
});
