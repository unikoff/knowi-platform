import { useId, useState } from "react";
import {
  CodeBlock,
  Diagram,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const orderDemo = String.raw`from sqlalchemy import Boolean, Integer, case, create_engine, delete, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class OrderRow(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    is_closed: Mapped[bool] = mapped_column(Boolean, nullable=False)


with create_engine("sqlite://").connect() as connection:
    Base.metadata.create_all(connection)
    with Session(connection) as session:
        session.add_all([
            OrderRow(id=2, is_closed=False),
            OrderRow(id=6, is_closed=True),
            OrderRow(id=9, is_closed=False),
        ])

        statement = select(
            func.count(OrderRow.id),
            func.count(case((OrderRow.is_closed.is_(False), OrderRow.id))),
            func.count(case((OrderRow.is_closed.is_(True), OrderRow.id))),
        )

        ordered, waiting, closed = session.execute(statement).one()
        print(ordered, waiting, closed)

        session.get(OrderRow, 9).is_closed = True
        print(*session.execute(statement).one())

        session.delete(session.get(OrderRow, 6))
        print(*session.execute(statement).one())

        session.execute(delete(OrderRow))
        print(*session.execute(statement).one())`;

const statisticsCheck = String.raw`import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import URL

from app.DBase.models import Base, TaskRow
from app.DBase.repositories import SqlTaskRepository
from app.DBase.session import create_session_factory
from app.api import app
from app.cli import handle_stats
from app.core.dependencies import get_planner
from app.core.engine import make_engine
from app.main import sql_service_context


@pytest.fixture
def sql_database(tmp_path):
    db_path = tmp_path / "stats.sqlite3"
    url = URL.create("sqlite", database=db_path.as_posix())
    engine = make_engine(url.render_as_string(hide_password=False))

    try:
        Base.metadata.create_all(engine)
        yield engine, create_session_factory(engine)
    finally:
        engine.dispose()


def seed_mixed(factory):
    with factory.begin() as session:
        for task_id in (11, 14, 18, 21, 25):
            title = f"Задача {task_id}"
            session.add(TaskRow(
                id=task_id,
                title=title,
                title_normalized=title.lower(),
                priority=2,
                is_done=task_id == 14,
                tags=[],
            ))


@pytest.mark.parametrize(
    ("mixed", "counts"),
    [(False, (0, 0, 0)), (True, (5, 4, 1))],
    ids=["empty", "mixed"],
)
def test_repository_counts_in_one_select(sql_database, monkeypatch, mixed, counts):
    engine, factory = sql_database
    if mixed:
        seed_mixed(factory)

    def reject_list(_repository):
        pytest.fail("Сводка вызвала list() и запросила список задач.")

    monkeypatch.setattr(SqlTaskRepository, "list", reject_list)
    statements = []

    def record_sql(conn, cursor, statement, params, context, many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_sql)
    try:
        with factory() as session:
            actual = SqlTaskRepository(session).statistics()
    finally:
        event.remove(engine, "before_cursor_execute", record_sql)

    expected = dict(zip(("all", "open", "done"), counts))
    assert actual == expected, "Счётчики или ключи не совпали."
    assert all(type(value) is int for value in actual.values())
    assert len(statements) == 1, "Нужен ровно один SQL-запрос."
    sql = statements[0].lower().strip()
    assert re.match(r"select\b", sql), "Сводка должна читать данные."
    assert len(re.findall(r"\bcount\s*\(", sql)) == 3, "Нужны три COUNT."
    assert len(re.findall(r"\bcase\b", sql)) == 2, "Нужны два условных подсчёта."


def assert_interfaces(factory, client, counts):
    total, opened, done = counts
    with sql_service_context(factory) as planner:
        assert planner.get_statistics() == {
            "all": total, "open": opened, "done": done,
        }
        lines = []
        handle_stats(planner, write=lines.append)

    percent = done / total * 100 if total else 0.0
    assert lines == [
        f"Всего: {total}",
        f"Выполнено: {done}",
        f"Осталось: {opened}",
        f"Процент выполнения: {percent:.1f}%",
    ]
    response = client.get("/stats")
    assert response.status_code == 200
    assert response.json() == {"total": total, "open": opened, "done": done}


def test_interfaces_after_done_and_delete(sql_database, monkeypatch):
    _, factory = sql_database

    def sql_planner():
        with sql_service_context(factory) as planner:
            yield planner

    monkeypatch.setitem(app.dependency_overrides, get_planner, sql_planner)
    with TestClient(app) as client:
        assert_interfaces(factory, client, (0, 0, 0))
        seed_mixed(factory)
        assert_interfaces(factory, client, (5, 4, 1))

        with sql_service_context(factory) as planner:
            planner.mark_done(21)
        assert_interfaces(factory, client, (5, 3, 2))

        with sql_service_context(factory) as planner:
            planner.delete_task(14)
        assert_interfaces(factory, client, (4, 3, 1))`;

type Order = { id: number; closed: boolean };

const initialOrders: Order[] = [
  { id: 2, closed: false },
  { id: 6, closed: true },
  { id: 9, closed: false },
  { id: 12, closed: true },
];

const forecastSteps = [
  {
    action: "Закроем заказ 2",
    change: (orders: Order[]) =>
      orders.map(order => order.id === 2 ? { ...order, closed: true } : order),
    options: ["4 / 2 / 2", "4 / 1 / 3", "3 / 1 / 2"],
    explanation: "Заказ 2 теперь учитывается среди закрытых. Всего заказов по-прежнему четыре.",
  },
  {
    action: "Удалим заказ 9",
    change: (orders: Order[]) => orders.filter(order => order.id !== 9),
    options: ["4 / 0 / 4", "3 / 1 / 2", "3 / 0 / 3"],
    explanation: "Заказ 9 был ожидающим. Исчез его вклад в общий итог и в число ожидающих заказов.",
  },
  {
    action: "Рассмотрим пустую таблицу",
    change: () => [],
    options: ["Нет строк результата", "Одна строка: 0 / 0 / 0", "Три строки по одному нулю"],
    explanation: "Запрос возвращает одну строку итогов для всей таблицы. Каждому COUNT достаётся ноль значений.",
  },
];

function counts(orders: Order[]) {
  const closed = orders.filter(order => order.closed).length;
  return [orders.length, orders.length - closed, closed];
}

function OrderForecast() {
  const titleId = useId();
  const [orders, setOrders] = useState(initialOrders);
  const [stepIndex, setStepIndex] = useState(0);
  const [prediction, setPrediction] = useState("");
  const [feedback, setFeedback] = useState("");
  const [previousTally, setPreviousTally] = useState("");
  const step = forecastSteps[stepIndex];
  const canCheck = Boolean(prediction && !feedback);
  const canAdvance = Boolean(feedback) && stepIndex < forecastSteps.length - 1;

  function checkPrediction() {
    const updated = step.change(orders);
    const tally = counts(updated).join(" / ");
    const expected = stepIndex === 2 ? "Одна строка: " + tally : tally;
    const assessment = prediction === expected ? "Прогноз совпал. " : "Получилось " + expected.toLowerCase() + ". ";
    setPreviousTally(counts(orders).join(" / "));
    setOrders(updated);
    setFeedback(assessment + step.explanation);
  }

  function reset() {
    setOrders(initialOrders);
    setStepIndex(0);
    setPrediction("");
    setFeedback("");
  }

  return (
    <div className="quiz-card" role="group" aria-labelledby={titleId}>
      <div className="quiz-question"><strong id={titleId}>Что изменится в сводке заказов?</strong></div>
      <p style={{ margin: 0 }}>Модель показывает, как меняется вклад строк. Числа идут в порядке «все, ожидают, закрыты».</p>
      <div aria-live="polite" style={{ display: "grid", gap: 6 }}>
        <p style={{ margin: 0 }}>
          Ожидают: <strong>{orders.filter(order => !order.closed).map(order => order.id).join(", ") || "нет"}</strong>.
          {" "}Закрыты: <strong>{orders.filter(order => order.closed).map(order => order.id).join(", ") || "нет"}</strong>.
        </p>
        {feedback && <p style={{ margin: 0 }}>До действия: {previousTally}.</p>}
        <p style={{ margin: 0 }}>Строка итогов: <strong>{counts(orders).join(" / ")}</strong>.</p>
        {feedback && <p style={{ margin: 0 }}>{feedback}</p>}
      </div>
      <form
        style={{ display: "grid", gap: 10 }}
        onSubmit={event => {
          event.preventDefault();
          if (canCheck) checkPrediction();
        }}
      >
        <label>
          {step.action + ". Выберем ожидаемый результат."}
          <select
            value={prediction}
            disabled={Boolean(feedback)}
            onChange={event => setPrediction(event.target.value)}
            style={{
              display: "block", width: "100%", marginTop: 8, padding: 8,
              font: "inherit", color: "var(--text)", backgroundColor: "var(--surface-2)",
              border: "1px solid var(--border)", borderRadius: 8,
            }}
          >
            <option value="">Выбрать прогноз</option>
            {step.options.map(option => <option key={option}>{option}</option>)}
          </select>
        </label>
        <div className="interactive-actions">
          <button className={canCheck ? "interactive-primary" : "interactive-secondary"} type="submit" disabled={!canCheck}>Проверить</button>
          <button
            className={canAdvance ? "interactive-primary" : "interactive-secondary"}
            type="button"
            disabled={!canAdvance}
            onClick={() => {
              setStepIndex(stepIndex + 1);
              setPrediction("");
              setFeedback("");
            }}
          >
            Следующий случай
          </button>
          <button className="interactive-secondary" type="button" onClick={reset}>Сначала</button>
        </div>
      </form>
    </div>
  );
}

const matrixCell = {
  padding: "7px 5px",
  borderBlockEnd: "1px solid var(--border)",
};

export function Lesson73_2({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module}
        title="Считаем статистику в SQL"
        intro="Попросим SQLite посчитать задачи и прочитаем готовый итог из одной строки результата."
      />

      <Section number="00" title="Список задач и сводка">
        <Lead>Поиск Planner возвращает подходящие задачи. Сводка отвечает на другой вопрос: сколько задач сейчас в каждом состоянии.</Lead>
        <LinkedNotes
          items={[
            { title: "Точка старта", description: "Мы умеем выбирать строки из SQLite. Сводка уже показывает, сколько задач всего, сколько открыто и сколько завершено." },
            { title: "Новый вопрос", description: "Как попросить SQLite вернуть сразу три готовых числа одним запросом?" },
            { title: "Результат", description: "Получим одну строку с итогами и проверим её на пустой базе, после завершения и после удаления задачи." },
          ]}
        />
        <p>Для обоих вопросов служит <code>SELECT</code>. У списка в результате может быть много строк. Для сводки база подводит один общий итог.</p>
      </Section>

      <Section number="01" title="Как строки участвуют в подсчёте">
        <Lead>Начнём с заказов. Каждый заказ имеет свой <code>id</code> и статус: ожидает (<code>False</code>) или закрыт (<code>True</code>).</Lead>
        <p>Агрегат вычисляет итог по набору строк. Например, <code>COUNT(id)</code> считает все имеющиеся значения <code>id</code>. У каждого заказа есть идентификатор, поэтому это число равно количеству заказов.</p>
        <p>Для счётчика ожидающих передадим в <code>COUNT</code> выражение <code>CASE</code>. Оно проверяет статус каждой строки. Если заказ ожидает, выражение даёт его <code>id</code>. Для закрытого заказа выражение даёт SQL <code>NULL</code>, то есть отсутствие значения. <code>COUNT</code> пропускает <code>NULL</code>. Счётчик закрытых использует своё условие.</p>

        <Diagram caption="Какие значения каждый заказ передаёт трём счётчикам">
          <table aria-label="Вклад заказов в счётчики" style={{ width: "100%", borderCollapse: "collapse", textAlign: "center" }}>
            <thead>
              <tr>
                <th scope="col" style={matrixCell}>Заказ</th>
                <th scope="col" style={matrixCell}>Все</th>
                <th scope="col" style={matrixCell}>Ожидают</th>
                <th scope="col" style={matrixCell}>Закрыты</th>
              </tr>
            </thead>
            <tbody>
              <tr><th scope="row" style={matrixCell}>2 · ожидает</th><td style={matrixCell}>2</td><td style={matrixCell}>2</td><td style={matrixCell}><code>NULL</code></td></tr>
              <tr><th scope="row" style={matrixCell}>6 · закрыт</th><td style={matrixCell}>6</td><td style={matrixCell}><code>NULL</code></td><td style={matrixCell}>6</td></tr>
              <tr><th scope="row" style={matrixCell}>9 · ожидает</th><td style={matrixCell}>9</td><td style={matrixCell}>9</td><td style={matrixCell}><code>NULL</code></td></tr>
            </tbody>
            <tfoot>
              <tr><th scope="row" style={matrixCell}>Итог</th><td style={matrixCell}><strong>3</strong></td><td style={matrixCell}><strong>2</strong></td><td style={matrixCell}><strong>1</strong></td></tr>
            </tfoot>
          </table>
        </Diagram>

        <p>В строке заказа 6 его <code>id</code> попадает в общий счётчик и в счётчик закрытых. Во втором столбце счётчиков он даёт <code>NULL</code>. Если подставить туда <code>0</code>, счётчик тоже его учтёт: ноль является значением.</p>
        <QuizCard
          question="В наборе заказ 4 ожидает, а заказ 7 закрыт. Что получат два условных счётчика?"
          options={[
            "Ожидают: 4 и 0. Закрыты: 0 и 7.",
            "Ожидают: 4 и NULL. Закрыты: NULL и 7.",
            "Оба счётчика получат 4 и 7.",
          ]}
          correctIndex={1}
          orientation="vertical"
          explanation="У каждой строки есть вклад только в один счётчик по статусу. COUNT для ожидающих учтёт 4, COUNT для закрытых учтёт 7. Вместе с общим числом итог будет 2 / 1 / 1."
        />
        <p>В одном <code>SELECT</code> можно вычислить все три счётчика. Дальше прочитаем итоговые числа в Python.</p>
      </Section>

      <Section number="02" title="Как запрос отдаёт одну строку">
        <Lead>Запустим пример с заказами. В нём готова временная SQLite в памяти, поэтому его можно запустить отдельно от Planner.</Lead>
        <p>Из SQLAlchemy импортируем <code>select</code>, <code>func</code> и <code>case</code>. <code>func.count(...)</code> строит выражение SQL <code>COUNT</code>. Аргументы <code>select</code> задают столбцы результата в указанном порядке. Ссылки на поля <code>OrderRow</code> указывают, из какой таблицы взять строки.</p>
        <p>В <code>case</code> передаём пару <code>(условие, значение)</code>. Условие <code>OrderRow.is_closed.is_(False)</code> подходит ожидающим заказам. При совпадении берётся <code>OrderRow.id</code>. Когда условие не выполнено и <code>else_</code> опущен, SQL даёт <code>NULL</code>. Условие с <code>True</code> так же выделяет закрытые заказы.</p>
        <p>Запрос считает один итог по всей таблице, без группировки. Поэтому результат содержит одну строку даже при нуле входных строк. <code>session.execute(statement)</code> выполняет запрос, а <code>one()</code> проверяет, что вернулась ровно одна строка. Её значения можно распаковать в три переменные. Знакомый <code>scalars()</code> оставил бы только первый столбец.</p>
        <p>Чтобы повторить опыт, сохраним код в свободный файл <code>orders_summary_demo.py</code> и запустим <code>python orders_summary_demo.py</code> интерпретатором, где уже установлен SQLAlchemy. Первые строки готовят модель и данные. Строка <code>statement = select(…)</code> задаёт сводку, которую мы будем читать после каждого изменения.</p>
        <CodeBlock code={orderDemo} caption="orders_summary_demo.py" />
        <CodeBlock code={"3 2 1\n3 1 2\n2 1 1\n0 0 0"} caption="Вывод · все, ожидают, закрыты" language="text" />
        <p>Сначала ожидают заказы 2 и 9, закрыт заказ 6. Закрытие заказа 9 переносит его из одного счётчика в другой. Удаление заказа 6 уменьшает общий счётчик и счётчик закрытых. После удаления всех заказов по-прежнему видим одну строку. В ней три нуля, потому что считать нечего.</p>

        <OrderForecast />

        <p>В Planner статус хранится в <code>TaskRow.is_done</code>. <code>False</code> означает открытую задачу, <code>True</code> означает завершённую. Будущий метод <code>statistics(self) -&gt; dict[str, int]</code> использует выданную репозиторию <code>self.session</code> и возвращает словарь из трёх целых чисел.</p>
        <p><code>PlannerService.get_statistics()</code> отдаёт этот словарь с ключами <code>all</code>, <code>open</code> и <code>done</code>. В <code>GET /stats</code> существующий маршрут уже переводит <code>all</code> в <code>total</code>. CLI читает те же счётчики и вычисляет долю завершённых задач.</p>

        <h3>Готовый файл проверки SQL-статистики</h3>
        <p>Скопируем файл целиком в <code style={{ overflowWrap: "anywhere" }}>project/tests/test_sql_statistics.py</code> в нашем Planner. Если это имя уже занято своей работой, сначала сохраним её отдельно. Файл создаёт временную базу, подставляет SQL-сервис для <code>GET /stats</code> на время теста и вызывает готовый обработчик CLI. Штатные настройки Planner остаются прежними.</p>
        <p>Начнём с <code>python -m pytest -q tests/test_sql_statistics.py -k repository</code> из <code>project/</code>. <code>-q</code> сокращает вывод, <code>-k repository</code> выбирает проверки репозитория. Проверка сравнивает ожидаемые числа и считает запросы только во время вызова сводки. Подготовительные записи идут отдельно. После работы с методом запустим весь файл той же командой без <code>-k repository</code>.</p>
        <details>
          <summary style={{ cursor: "pointer" }}>Показать test_sql_statistics.py</summary>
          <CodeBlock code={statisticsCheck} caption="project/tests/test_sql_statistics.py" />
        </details>
      </Section>

      <Section number="03" title="Что мы будем делать в практике">
        <Lead>Дополним один SQL-метод в своём Planner и проверим его на временной базе.</Lead>
        <LinkedNotes
          items={[
            { title: "Получим сводку запросом", description: "Добавим statistics() в SqlTaskRepository. Проверка покажет три правильных счётчика, в том числе три нуля на пустой базе, и подтвердит один агрегатный SELECT." },
            { title: "Проверим статистику на изменениях", description: "Запустим готовый сценарий завершения и удаления задачи. Сверим новые числа в сервисе, поле total в HTTP и прежний вывод CLI." },
          ]}
        />
        <p>Первая проверка показывает результат метода на двух наборах данных. Полный прогон подтверждает, что прежние интерфейсы получают новый итог после изменений. Если расходятся счётчики, вернёмся к матрице вкладов и порядку столбцов в строке.</p>
      </Section>
    </RichLesson>
  );
}
