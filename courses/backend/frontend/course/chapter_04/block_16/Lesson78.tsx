import { useState } from "react";
import { Database, ListFilter, ShieldCheck } from "lucide-react";
import { Callout, CodeBlock, Diagram, Lead, LinkedNotes, QuizCard, RichHero, RichLesson, Section } from "../../shared";

const BLOCK_TITLE = "Этап 4 · Блок 16 · Надёжность, запросы и передача";

const inventoryExample = `import sqlite3
from contextlib import closing

queries = {
    "sql": (
        "SELECT id, label, state FROM inventory "
        "WHERE state = ? ORDER BY id ASC LIMIT ?",
        ("open", 1),
    ),
    "early": (
        "SELECT id, label, state FROM inventory ORDER BY id ASC LIMIT ?",
        (1,),
    ),
    "python": ("SELECT id, label, state FROM inventory ORDER BY id ASC", ()),
}


def observe(db, variant):
    sql, params = queries[variant]
    rows = db.execute(sql, params).fetchall()
    cards = [row for row in rows if row[2] == "open"][:1] if variant != "sql" else rows
    return [row[0] for row in rows], [card[0] for card in cards], [sql]


def verify_inventory(selected, selects):
    assert selected == [2], "Возвращённый id"
    assert len(selects) == 1, "Число SELECT"
    sql = " ".join(selects[0].lower().split())
    assert "where state =" in sql, "Отбор по состоянию"
    assert "order by id asc" in sql, "Порядок id"
    assert "limit ?" in sql, "Лимит внутри SQL"


with closing(sqlite3.connect(":memory:")) as db:
    db.execute("CREATE TABLE inventory(id INTEGER PRIMARY KEY, label TEXT, state TEXT)")
    db.executemany(
        "INSERT INTO inventory VALUES (?, ?, ?)",
        [(1, "Север", "closed"), (2, "Смена", "open"), (3, "Запас", "open")],
    )
    for variant in queries:
        returned, selected, selects = observe(db, variant)
        try:
            verify_inventory(selected, selects)
        except AssertionError as error:
            verdict = f"FAIL: {error}"
        else:
            verdict = "PASS"
        print(variant, returned, selected, verdict)`;

const selectionStarter = `import logging
from contextlib import closing
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import URL, create_engine

from app.api import app
from app.DBase.models import Base, TaskRow
from app.DBase.session import create_session_factory


@pytest.fixture
def selection_lab(tmp_path):
    log_name = f"selection_guard_{uuid4().hex}"
    engine = create_engine(
        URL.create("sqlite", database=(tmp_path / "selection.sqlite3").as_posix()),
        logging_name=log_name,
        hide_parameters=True,
        echo=False,
    )
    logger_name = f"sqlalchemy.engine.Engine.{log_name}"
    logger = logging.getLogger(logger_name)
    previous_level = logger.level
    try:
        logger.setLevel(logging.WARNING)
        Base.metadata.create_all(engine)
        session_factory = create_session_factory(engine)
        previous_factory = app.state.session_factory
        app.state.session_factory = session_factory
        try:
            with closing(TestClient(app)) as client:
                yield SimpleNamespace(
                    client=client,
                    logger_name=logger_name,
                    session_factory=session_factory,
                )
        finally:
            app.state.session_factory = previous_factory
    finally:
        logger.setLevel(previous_level)
        engine.dispose()


@pytest.fixture
def seed_selection(selection_lab):
    def seed(done_flags):
        ids = []
        with selection_lab.session_factory() as session:
            with session.begin():
                for position, is_done in enumerate(done_flags, start=1):
                    row = TaskRow(
                        title=f"Guard sample {position}",
                        title_normalized=f"guard sample {position}",
                        priority=2,
                        is_done=is_done,
                        tags=["synthetic"],
                    )
                    session.add(row)
                    session.flush()
                    ids.append(row.id)
        return ids

    return seed


@pytest.fixture
def read_selection(selection_lab, caplog):
    def read(is_done):
        caplog.clear()
        try:
            with caplog.at_level(logging.INFO, logger=selection_lab.logger_name):
                response = selection_lab.client.get(
                    "/tasks",
                    params={
                        "is_done": "true" if is_done else "false",
                        "sort_desc": "false",
                        "limit": 1,
                    },
                )
            selects = []
            for record in caplog.records:
                if record.name == selection_lab.logger_name:
                    sql = " ".join(record.getMessage().lower().split())
                    if sql.startswith("select "):
                        selects.append(sql)
        finally:
            caplog.clear()

        assert response.status_code == 200, "GET /tasks должен вернуть HTTP 200"
        rows = response.json()
        assert isinstance(rows, list), "Ожидается список задач"
        assert all(
            set(row) == {"id", "title", "priority", "is_done"} for row in rows
        ), "Проверьте публичные поля списка"
        return [row["id"] for row in rows], selects

    return read`;

type Variant = "sql" | "early" | "python";

const variants: { value: Variant; title: string; clauses: string; explanation: string }[] = [
  {
    value: "sql",
    title: "Отбор в SQL",
    clauses: "WHERE · ORDER BY · LIMIT",
    explanation: "SQLite отбирает открытые карточки, упорядочивает их и передаёт первую.",
  },
  {
    value: "early",
    title: "Ранний лимит",
    clauses: "ORDER BY · LIMIT",
    explanation: "SQLite передаёт первую карточку по id. Только после этого Python проверяет её статус.",
  },
  {
    value: "python",
    title: "Отбор в Python",
    clauses: "ORDER BY",
    explanation: "SQLite передаёт все три карточки. Python оставляет первую открытую.",
  },
];

function inventoryRows(firstIsOpen: boolean, variant: Variant) {
  const firstOpen = firstIsOpen ? 1 : 2;
  return {
    fetched: variant === "sql" ? [firstOpen] : variant === "early" ? [1] : [1, 2, 3],
    answer: variant === "early" && !firstIsOpen ? [] : [firstOpen],
  };
}

function showIds(ids: number[]) {
  return "[" + ids.join(", ") + "]";
}

function InventoryComparison() {
  return (
    <div
      role="group"
      aria-label="Сравнение трёх вариантов на наборе с закрытой первой карточкой"
      style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 12rem), 1fr))", gap: "0.75rem" }}
    >
      {variants.map(({ value, title, clauses }) => {
        const { fetched, answer } = inventoryRows(false, value);
        return (
          <div key={value} style={{ padding: "0.875rem", border: "1px solid var(--line)", background: "var(--card)", borderRadius: "0.75rem", minWidth: 0 }}>
            <h3 style={{ marginTop: 0 }}>{title}</h3>
            <p><strong>В SQL</strong><br /><code>{clauses}</code></p>
            <p><strong>Из SQLite</strong> <code>{showIds(fetched)}</code><br /><strong>Итог</strong> <code>{showIds(answer)}</code></p>
            <p style={{ marginBottom: 0 }}>Верный ответ: {answer.length > 0 ? "да" : "нет"}.<br />Фильтр и лимит в SQLite: {value === "sql" ? "да" : "нет"}.</p>
          </div>
        );
      })}
    </div>
  );
}

function InventoryPredictor() {
  const [firstIsOpen, setFirstIsOpen] = useState(true);
  const [variant, setVariant] = useState<Variant>("sql");
  const [prediction, setPrediction] = useState("");
  const [checked, setChecked] = useState(false);
  const result = inventoryRows(firstIsOpen, variant);
  const answer = showIds(result.answer);
  const selectedVariant = variants.find((item) => item.value === variant) ?? variants[0];

  function resetPrediction() {
    setPrediction("");
    setChecked(false);
  }

  return (
    <section className="quiz-card" aria-label="Прогноз выборки витрины">
      <h3>Какой id вернётся?</h3>
      <p>Меняем статус первой карточки и способ отбора. Карточки 2 и 3 в обоих наборах открыты.</p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 12rem), 1fr))", gap: "0.75rem" }}>
        <label style={{ display: "grid", gap: "0.3rem" }}>
          Первая карточка
          <select value={firstIsOpen ? "open" : "closed"} onChange={(event) => {
            setFirstIsOpen(event.target.value === "open");
            resetPrediction();
          }}>
            <option value="open">Открыта</option>
            <option value="closed">Закрыта</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: "0.3rem" }}>
          Как выбираем
          <select value={variant} onChange={(event) => {
            const choice = variants.find((item) => item.value === event.target.value);
            if (choice) setVariant(choice.value);
            resetPrediction();
          }}>
            {variants.map(({ value, title }) => <option key={value} value={value}>{title}</option>)}
          </select>
        </label>
        <label style={{ display: "grid", gap: "0.3rem" }}>
          Наш прогноз
          <select value={prediction} onChange={(event) => { setPrediction(event.target.value); setChecked(false); }}>
            <option value="" disabled>Выбрать результат</option>
            <option value="[]">[]</option>
            <option value="[1]">[1]</option>
            <option value="[2]">[2]</option>
          </select>
        </label>
      </div>
      <div className="recall-controls">
        <button className="recall-toggle recall-answer-toggle" type="button" disabled={!prediction} onClick={() => setChecked(true)}>
          Проверить прогноз
        </button>
      </div>
      <div aria-live="polite" aria-atomic="true">
        {checked && (
          <>
            <p><strong>{prediction === answer ? "Прогноз совпал." : "Сверим прогноз с результатом."}</strong> SQLite вернула <code>{showIds(result.fetched)}</code>, итоговый список <code>{answer}</code>.</p>
            <p><code>{selectedVariant.clauses}</code>. {selectedVariant.explanation} {firstIsOpen && "При открытой первой карточке верный id совпадает во всех трёх вариантах."}</p>
          </>
        )}
      </div>
    </section>
  );
}

export function Lesson78({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title="Защищаем фильтрацию и limit в одном SQL-запросе"
        intro="Напишем один тест для выдачи Planner. Он проверит, какую задачу вернул API и какую работу выполнила SQLite."
        tags={[
          { icon: <ListFilter size={14} />, label: "Отбор до лимита" },
          { icon: <Database size={14} />, label: "Один SELECT" },
          { icon: <ShieldCheck size={14} />, label: "Регрессия" },
        ]}
      />

      <Section number="00" title="Вступление">
        <Lead>В журнале Planner мы уже видели SELECT одной выборки и сверяли его с id в ответе. Теперь превратим это наблюдение в проверку, которую можно повторить после правок.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "У нас есть обезличенный SQL одного чтения и id задачи, которую вернул API." },
          { title: "Новый вопрос", description: "Как заметить ранний лимит или загрузку всех строк, если на простом наборе ответ выглядит правильно?" },
          { title: "Результат", description: "Один тест проверяет подходящий id и отбор с ограничением внутри одного SELECT." },
        ]} />
        <p>Начнём с небольшой витрины. На ней видно, в какой момент каждый вариант отбрасывает строки и почему двух наблюдений достаточно для нашей проверки.</p>
      </Section>

      <Section number="01" title="Когда одного ответа мало">
        <p>На витрине три карточки по порядку id. Первая, «Север», закрыта. «Смена» с id 2 и «Запас» с id 3 открыты. Нам нужна одна карточка: первая среди открытых по возрастанию id.</p>
        <p><code>WHERE</code> определяет подходящие строки, <code>ORDER BY id ASC</code> задаёт порядок, а <code>LIMIT</code> ограничивает уже отобранный результат. В SQL ниже знаки <code>?</code> оставляют места для двух отдельных аргументов: <code>"open"</code> и <code>1</code>. Это смысл выборки. Способ чтения строк SQLite выбирает сама.</p>
        <CodeBlock language="text" caption="Сначала фильтр, затем порядок и лимит" code={"SELECT id, label, state\nFROM inventory\nWHERE state = ?\nORDER BY id ASC\nLIMIT ?"} />
        <Diagram caption="Где отбираются строки и какой id получает вызывающий код">
          <InventoryComparison />
        </Diagram>
        <p>Ранний лимит даёт пустой список: первая карточка уже закрыта, а до второй чтение не дошло. При отборе в Python ответ совпадает с правильным, хотя SQLite передала все строки. Счётчик в обоих случаях тоже может показывать один SELECT. Форма команды помогает увидеть, где был отбор.</p>
        <InventoryPredictor />
        <p>Попробуем закрыть первую карточку и сравнить ранний лимит с отбором в SQL. Две открытые карточки после первой дают ещё одну проверку: без ограничения подходящих результатов было бы два.</p>
      </Section>

      <Section number="02" title="Как сделать наблюдение проверкой">
        <p>Возьмём два значения из одного чтения: список полученных id и список его команд SELECT. Создание таблицы и добавление строк займут отдельную часть перед чтением. Сообщения о транзакции и параметры в счёт SELECT не входят.</p>
        <p>В готовом файле ниже <code>observe</code> выполняет один SELECT в SQLite в памяти и отдаёт текст команды, которую передала в <code>execute</code>. Выборка из API Planner использует знакомое окно SQL-журнала. В витрине мы можем посмотреть обе стороны: какие строки вернулись из SQLite и какие id остались на выходе.</p>
        <details>
          <summary>Полный пример витрины · inventory_selection.py</summary>
          <p>Сохраним блок одним файлом в любой временной папке и выполним <code>python inventory_selection.py</code>. Достаточно стандартной библиотеки Python.</p>
          <CodeBlock code={inventoryExample} />
        </details>
        <CodeBlock language="text" caption="Вывод опыта" code={"sql [2] [2] PASS\nearly [1] [] FAIL: Возвращённый id\npython [1, 2, 3] [2] FAIL: Отбор по состоянию"} />
        <p>Сначала <code>assert selected == [2]</code> проверяет содержимое списка. <code>len(selects) == 1</code> проверяет число SELECT перед обращением к <code>selects[0]</code>. Затем <code>in</code> ищет нужный фрагмент. Нижний регистр и одинарные пробелы позволяют сравнивать смысловые части, даже если SQL записан в несколько строк. Сравнение формы не восстанавливает значения за <code>?</code>.</p>
        <Callout tone="success">
          Если упало сравнение id, сначала сверим входные данные, порядок и результат. Если id совпал, а проверка формы упала, посмотрим, какие операции остались в SELECT. Тест даёт разные причины для этих двух случаев.
        </Callout>
      </Section>

      <Section number="03" title="Запрос, полученная Task и план SQLite">
        <p>SQL выше описывает, что хотим получить. SQLite возвращает строку <code>(2, "Смена", "open")</code>. Для примера из неё уже собрали обычную <code>Task</code>: имя попало в <code>title</code>, статус <code>open</code> стал <code>is_done=False</code>. Приоритет и пустой список меток заданы в примере. Когда объект с готовыми значениями создан, результат материализован в Python.</p>
        <CodeBlock language="text" caption="Три карточки одной выборки" code={"A · SELECT id, label, state FROM inventory\n    WHERE state = ? ORDER BY id ASC LIMIT ?\n\nB · Task(id=2, title=\"Смена\", priority=2,\n         is_done=False, tags=[])\n\nC · SCAN inventory"} />
        <p>Карточка C содержит готовую строку <code>EXPLAIN QUERY PLAN</code>. <code>SCAN inventory</code> показывает обход таблицы. Для этой схемы с первичным ключом по id отдельный шаг сортировки в плане не указан. Даже при <code>SCAN</code> SQLite может отбирать строки по <code>WHERE</code> и остановиться после нужного числа совпадений. Из карточки нельзя вывести число фактически просмотренных строк или длительность запроса.</p>
        <QuizCard
          question="В каком порядке стоят карточки для вопросов «что получили», «что попросили» и «как SQLite ищет строки»?"
          options={["B, A, C", "A, C, B", "C, B, A"]}
          correctIndex={0}
          explanation="B содержит уже полученную Task, A задаёт фильтр, порядок и лимит, C описывает способ доступа к таблице."
        />
        <p>Для нашей регрессии достаточно фактического ответа и формы одного SELECT. План SQLite разобрали на готовом примере.</p>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <p>Откроем свою рабочую копию Planner и заметку о наблюдении выборки. В той же операции <code>GET /tasks</code> зафиксируем порядок по возрастанию id и <code>limit=1</code>, а данные подберём сами.</p>
        <LinkedNotes items={[
          { title: "Подберём различающий набор", description: "Заранее определим, какой id должен вернуться, когда первая строка не проходит фильтр." },
          { title: "Защитим один SQL-отбор", description: "Одним тестом проверим нужный id и то, что фильтр, порядок и лимит остались внутри одного SELECT." },
          { title: "Обоснуем решение", description: "По месту падения найдём причину, а успешный тест сохраним для повторного прогона." },
        ]} />
        <p>При затруднении вернёмся к примеру витрины: он показывает разницу между неверным id и правильным id, полученным после лишней загрузки. Для запуска из папки <code>project/</code> используем <code style={{ overflowWrap: "anywhere", whiteSpace: "normal" }}>python -m pytest tests/test_sql_selection_guard.py -q</code>. Pytest находит функцию с именем <code>test_...</code>, а <code>-q</code> сокращает вывод.</p>
        <h3>Готовые материалы</h3>
        <p>Раскроем блок «Оснастка для теста» и скопируем код в <code style={{ overflowWrap: "anywhere", whiteSpace: "normal" }}>project/tests/test_sql_selection_guard.py</code>. Если файл уже начали, сохраним свои изменения. В нём будут средства подготовки и чтения. Пока нет собственной функции <code>test_...</code>, pytest сообщает, что тесты не собраны.</p>
        <p>В параметры своей функции передадим имена <code>seed_selection</code> и <code>read_selection</code>. По этим именам pytest подставляет готовые функции. <code>seed_selection</code> принимает список флагов: <code>True</code> означает завершённую задачу, <code>False</code> открытую. В ответ получим id в порядке добавления. <code>read_selection</code> принимает нужный статус. Результат вызова распакуем в два имени: первый список содержит id из ответа, второй содержит строки SQL в нижнем регистре с одинарными пробелами.</p>
        <p>Внутри оснастки <code>caplog.at_level</code> включает записи одного Engine на время одного GET. <code>selection_lab.logger_name</code> указывает логгер, выход из <code>with</code> восстанавливает исходный уровень. Схема и данные готовятся заранее в отдельной SQLite. Список SELECT собирается отдельно для каждого чтения.</p>
        <details>
          <summary>Оснастка для теста · tests/test_sql_selection_guard.py</summary>
          <CodeBlock code={selectionStarter} />
        </details>
      </Section>
    </RichLesson>
  );
}
