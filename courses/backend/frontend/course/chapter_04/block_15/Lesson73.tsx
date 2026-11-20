import { useState } from "react";
import {
  CodeBlock,
  Diagram,
  DiagramArrow,
  DiagramFlow,
  DiagramNode,
  Lead,
  LinkedNotes,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const BLOCK_TITLE = "Этап 4 · Блок 15 · Полный SQL-путь и единое переключение";

const TICKET_EXAMPLE = String.raw`from sqlalchemy import create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class TicketRow(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    closed: Mapped[bool]


engine = create_engine("sqlite://")
try:
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            TicketRow(id=4, closed=False),
            TicketRow(id=1, closed=True),
            TicketRow(id=2, closed=False),
        ])
        session.commit()

        tickets = select(TicketRow)
        opened = tickets.where(TicketRow.closed == False)
        ascending = opened.order_by(TicketRow.id.asc())
        descending = opened.order_by(TicketRow.id.desc())

        examples = [
            ("Открытые, ASC, 1", ascending.limit(1)),
            ("Открытые, DESC, 1", descending.limit(1)),
            ("Все, ASC, 1", tickets.order_by(TicketRow.id.asc()).limit(1)),
            ("Открытые, ASC, без лимита", ascending),
            ("Билет 99", tickets.where(TicketRow.id == 99)),
        ]
        for label, statement in examples:
            rows = session.scalars(statement).all()
            print(f"{label}: {[row.id for row in rows]}")
finally:
    engine.dispose()`;

const SELECTION_CHECK = String.raw`import re

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import URL

from app.DBase.models import Base
from app.DBase.repositories import SqlTaskRepository
from app.DBase.session import create_session_factory
from app.cli import execute_command, format_task
from app.core.dependencies import get_db, get_planner
from app.core.engine import make_engine
from app.main import build_service, sql_service_context
from app.models import Task
from app.routers.tasks.router import router
from app.storage import JsonStorage


@pytest.fixture
def sql_database(tmp_path):
    url = URL.create("sqlite", database=(tmp_path / "selection.sqlite3").as_posix())
    engine = make_engine(url.render_as_string(hide_password=False))
    try:
        Base.metadata.create_all(engine)
        yield engine, create_session_factory(engine)
    finally:
        engine.dispose()


def seed_selection(factory):
    with sql_service_context(factory) as planner:
        a = planner.add_task("Закрыть счёт", 3, tags=["архив"])
        b = planner.add_task("Забрать заказ", 5, tags=["город"])
        c = planner.add_task("Выбрать обложку", 1, tags=["макет"])
        planner.mark_done(a.id)
    return (
        Task(a.id, a.title, priority=3, is_done=True, tags=["архив"]),
        Task(b.id, b.title, priority=5, tags=["город"]),
        Task(c.id, c.title, priority=1, tags=["макет"]),
    )


def public_task(task):
    return {
        "id": task.id,
        "title": task.title,
        "priority": task.priority,
        "is_done": task.is_done,
    }


@pytest.fixture
def sql_client(sql_database, tmp_path):
    _, factory = sql_database
    app = FastAPI()
    app.include_router(router)
    original_planner = build_service(JsonStorage(tmp_path / "default.json"))
    app.state.planner = original_planner

    def sql_planner(db=Depends(get_db)):
        return build_service(repository=SqlTaskRepository(db))

    app.state.session_factory = factory
    app.dependency_overrides[get_planner] = sql_planner
    with TestClient(app) as client:
        try:
            yield client
        finally:
            app.dependency_overrides.pop(get_planner)
            del app.state.session_factory
            assert app.state.planner is original_planner
            assert client.get("/tasks").json() == []


def test_input_sql_selection(sql_database):
    _, factory = sql_database
    expected = seed_selection(factory)
    with sql_service_context(factory) as planner:
        rows = planner.list_tasks()
        first = planner.get_task(expected[0].id)
    print("Исходные строки (id, is_done, tags):")
    for row in rows:
        print(row.id, row.is_done, row.tags)
    assert rows == list(expected)
    assert first == expected[0]
    assert rows[0].id < rows[1].id < rows[2].id


def test_selection_service(sql_database):
    _, factory = sql_database
    with sql_service_context(factory) as planner:
        assert planner.select_tasks(is_done=False) == []

    a, b, c = seed_selection(factory)
    with sql_service_context(factory) as planner:
        opened = planner.select_tasks(is_done=False, limit=None)
        done = planner.select_tasks(is_done=True)
        all_rows = planner.select_tasks()
        desc = planner.select_tasks(is_done=False, sort_desc=True, limit=1)
        first = planner.select_tasks(limit=1)

    assert opened == [b, c]
    assert done == [a]
    assert all_rows == [a, b, c]
    assert desc == [c]
    assert first == [a]
    print("Открытые без лимита:", [task.id for task in opened])
    print("Завершённые:", [task.id for task in done])
    print("Все:", [task.id for task in all_rows])
    print("Открытые по убыванию, лимит 1:", [task.id for task in desc])


def test_selection_stays_in_sql(sql_database):
    engine, factory = sql_database
    a, b, c = seed_selection(factory)
    statements = []

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    def read_one_statement(order_label, **options):
        statements.clear()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            with sql_service_context(factory) as planner:
                result = planner.select_tasks(**options)
        finally:
            event.remove(engine, "before_cursor_execute", capture)

        print(order_label, [task.id for task in result])
        for statement in statements:
            print(statement)
        assert len(statements) == 1, "Ожидается одно обращение к SQLite"
        sql = " ".join(statements[0].upper().split())
        assert sql.startswith("SELECT ")
        assert re.search(r"\bORDER BY\b.*\bID\b", sql)
        return result, sql

    result, sql = read_one_statement("Открытые, ASC, 1:", is_done=False, limit=1)
    assert result == [b]
    assert re.search(r"\bWHERE\b.*\bIS_DONE\b.*\bORDER BY\b", sql)
    assert re.search(r"\bLIMIT\b", sql)

    result, sql = read_one_statement("Все, ASC, без лимита:", is_done=None, limit=None)
    assert result == [a, b, c]
    assert not re.search(r"\b(WHERE|LIMIT)\b", sql)


def test_selection_http_mixed(sql_database, sql_client):
    _, factory = sql_database
    _, b, _ = seed_selection(factory)
    response = sql_client.get("/tasks?is_done=false&sort_desc=false&limit=1")
    assert response.status_code == 200
    assert response.json() == [public_task(b)]
    print("GET открытых с лимитом 1:", [row["id"] for row in response.json()])


def test_selection_http_and_cli_limits(sql_database, sql_client):
    _, factory = sql_database
    with sql_service_context(factory) as planner:
        expected = [planner.add_task(f"Задача {index}", 2) for index in range(1, 13)]

    for params, expected_rows in [({}, expected[:10]), ({"limit": 1}, expected[:1]),
                                  ({"limit": 50}, expected)]:
        response = sql_client.get("/tasks", params=params)
        assert response.status_code == 200
        assert response.json() == [public_task(task) for task in expected_rows]
        print("HTTP limit:", params.get("limit", "по умолчанию"), "строк:", len(expected_rows))

    for limit in (0, 51):
        response = sql_client.get("/tasks", params={"limit": limit})
        assert response.status_code == 422
        print("HTTP limit:", limit, "статус:", response.status_code)

    with sql_service_context(factory) as planner:
        without_limit = planner.select_tasks(limit=None)
        lines = []
        execute_command(planner, "list", write=lines.append)
    assert without_limit == expected
    assert lines == [format_task(task) for task in expected]
    print("Строк через сервис без лимита:", len(without_limit))
    print("Строк CLI:", len(lines))`;

const tickets = [
  { id: 4, closed: false },
  { id: 1, closed: true },
  { id: 2, closed: false },
];

type Ticket = (typeof tickets)[number];
type Selection = {
  status: "open" | "closed" | "all";
  direction: "asc" | "desc";
  size: "one" | "all";
  earlyLimit: boolean;
};

const initialSelection: Selection = {
  status: "open",
  direction: "asc",
  size: "one",
  earlyLimit: false,
};

const controlStyle = {
  minHeight: 44,
  maxWidth: "100%",
  font: "inherit",
  colorScheme: "light dark",
} as const;

function ticketList(rows: Ticket[]) {
  return rows.length
    ? rows.map(({ id, closed }) => `${id} (${closed ? "закрыт" : "открыт"})`).join(", ")
    : "Нет строк";
}

function TicketSelection() {
  const [selection, setSelection] = useState(initialSelection);
  const [revealed, setRevealed] = useState(false);

  function change(patch: Partial<Selection>) {
    setSelection((current) => ({ ...current, ...patch }));
    setRevealed(false);
  }

  function filter(rows: Ticket[]) {
    return rows.filter(
      (ticket) => selection.status === "all" || ticket.closed === (selection.status === "closed"),
    );
  }

  function sort(rows: Ticket[]) {
    return [...rows].sort((a, b) => selection.direction === "asc" ? a.id - b.id : b.id - a.id);
  }

  function limit(rows: Ticket[]) {
    return selection.size === "one" ? rows.slice(0, 1) : rows;
  }

  const first = selection.earlyLimit ? sort(tickets) : filter(tickets);
  const second = selection.earlyLimit ? limit(first) : sort(first);
  const result = selection.earlyLimit ? filter(second) : limit(second);
  const stages = selection.earlyLimit
    ? ["После сортировки", "После лимита", "После отбора"]
    : ["После отбора", "После сортировки", "После лимита"];

  function explainResult() {
    if (selection.size === "all") {
      return "Без лимита ни одна строка не теряется при обрезке. Проверьте, что обычный порядок и режим раннего лимита дают те же id.";
    }
    if (selection.status === "all") {
      return "Подходят все билеты. Первый id определяется только направлением сортировки.";
    }
    if (!result.length) {
      return `Лимит оставил только билет ${second[0].id}. Он не прошёл фильтр, а подходящие билеты уже отброшены.`;
    }
    if (selection.earlyLimit) {
      return `На этом входе билет ${result[0].id} прошёл фильтр случайно: он оказался первым среди всех. Смените фильтр и проверьте снова.`;
    }
    return `Билет ${result[0].id} стоит первым среди подходящих. Только после отбора и сортировки лимит оставляет этот билет.`;
  }

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 12, padding: "1rem", minWidth: 0 }}>
      <h3>Какие билеты вернутся?</h3>
      <p>Билеты: {ticketList(tickets)}. Выберите параметры, назовите ожидаемые id и откройте шаги. Это схема выборки на примере, её можно переключать без запуска базы.</p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 11rem), 1fr))", gap: "0.75rem" }}>
        <label style={{ display: "grid", gap: "0.25rem" }}>
          Статус
          <select
            value={selection.status}
            onChange={(event) => change({ status: event.target.value as Selection["status"] })}
            style={controlStyle}
          >
            <option value="all">Все</option>
            <option value="open">Открытые</option>
            <option value="closed">Закрытые</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: "0.25rem" }}>
          Порядок по id
          <select
            value={selection.direction}
            onChange={(event) => change({ direction: event.target.value as Selection["direction"] })}
            style={controlStyle}
          >
            <option value="asc">По возрастанию</option>
            <option value="desc">По убыванию</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: "0.25rem" }}>
          Лимит
          <select
            value={selection.size}
            onChange={(event) => change({ size: event.target.value as Selection["size"] })}
            style={controlStyle}
          >
            <option value="one">Одна строка</option>
            <option value="all">Без лимита</option>
          </select>
        </label>
      </div>

      <label style={{ display: "flex", alignItems: "center", gap: "0.5rem", minHeight: 44 }}>
        <input
          type="checkbox"
          checked={selection.earlyLimit}
          onChange={(event) => change({ earlyLimit: event.target.checked })}
        />
        Ограничить раньше фильтра
      </label>

      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
        <button type="button" aria-expanded={revealed} onClick={() => setRevealed(!revealed)} style={controlStyle}>
          {revealed ? "Скрыть шаги" : "Показать шаги"}
        </button>
        <button type="button" onClick={() => change(initialSelection)} style={controlStyle}>Сбросить</button>
      </div>
      <div aria-live="polite" aria-atomic="true">
        {revealed && (
          <>
            <dl>
              {stages.map((stage, index) => (
                <div key={stage}>
                  <dt><strong>{stage}</strong></dt>
                  <dd>{ticketList([first, second, result][index])}</dd>
                </div>
              ))}
            </dl>
            <p><strong>Результат: [{result.map((ticket) => ticket.id).join(", ")}].</strong> {explainResult()}</p>
          </>
        )}
      </div>
    </div>
  );
}

export function Lesson73({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title="Фильтруем, сортируем и ограничиваем список в SQL"
        intro="Попросим SQLite вернуть только нужные задачи и проверим, что старые команды Planner получают ожидаемый список."
      />

      <Section number="00" title="Вступление">
        <Lead>
          Допустим, Planner должен показать одну открытую задачу. Первая задача по id уже завершена.
          Какую строку нужно вернуть?
        </Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "SQLite уже хранит задачи Planner. Прежняя выборка умеет отбирать задачи по статусу, упорядочивать их и ограничивать размер ответа." },
          { title: "Новый вопрос", description: "Как попросить базу вернуть одну открытую задачу, если первая по id уже завершена?" },
          { title: "Результат", description: "При лимите в одну строку запрос вернёт первую подходящую задачу в заданном порядке." },
        ]} />
        <p>Проследим, какие строки проходят условие, какая из них становится первой и на каком шаге берём нужное количество.</p>
      </Section>

      <Section number="01" title="Прежняя выборка как договор">
        <Lead>У выборки три настройки. Первая задаёт состав списка, вторая задаёт порядок, третья задаёт длину ответа.</Lead>
        <dl>
          <dt><code>is_done</code></dt>
          <dd><code>None</code> оставляет оба статуса, <code>False</code> выбирает открытые задачи, <code>True</code> выбирает завершённые.</dd>
          <dt><code>sort_desc</code></dt>
          <dd><code>False</code> ставит меньшие id первыми, <code>True</code> ставит большие id первыми.</dd>
          <dt><code>limit</code></dt>
          <dd>Число задаёт, сколько строк взять с начала списка. Вызов сервиса с <code>None</code> снимает ограничение.</dd>
        </dl>
        <p>
          Возьмём билеты: первый закрыт, второй и четвёртый открыты. При запросе «все» подходят id 1, 2 и 4.
          Для запроса «только открытые» подходят 2 и 4. Для запроса «только закрытые» подходит 1.
          Если написать <code>if status</code>, ветка пропустит значение <code>False</code> и запрос открытых билетов потеряет фильтр.
        </p>
        <p>
          В Planner запрос <code>GET /tasks</code> передаёт настройки через <code>get_selected_tasks</code> в <code>PlannerService.select_tasks</code>.
          Сервис вызывает <code>repository.select</code>. В репозитории и будет строиться запрос к SQLite.
        </p>
      </Section>

      <Section number="02" title="Собираем запрос до выполнения">
        <Lead>Сначала составляем описание выборки. База получит его, когда мы попросим Session выполнить запрос.</Lead>
        <p>
          <code>select(TicketRow)</code> описывает чтение строк таблицы билетов. <code>WHERE</code> задаёт условие отбора.
          В SQLAlchemy метод <code>where()</code> добавляет условие к выражению.
          Запись <code>TicketRow.closed == status</code> сравнивает колонку каждой строки с переданным значением.
          SQLAlchemy сам передаёт значение базе в подходящей форме.
        </p>
        <p>Пусть <code>statement</code> уже хранит <code>select(TicketRow)</code>, а <code>status</code> задаёт фильтр.</p>
        <CodeBlock
          caption="Фрагмент для билетов"
          code={"if status is not None:\n    statement = statement.where(TicketRow.closed == status)"}
        />
        <p>
          Методы сборки возвращают новое выражение. Переприсваивание <code>statement</code> сохраняет версию с условием для следующего шага.
          При <code>status=False</code> ветка выполняется и подходят билеты 2 и 4.
          При <code>status=None</code> ветка пропускается и билет 1 тоже подходит.
          Какой из открытых билетов взять первым, определим отдельно.
        </p>
      </Section>

      <Section number="03" title="Определяем порядок и только затем берём первые строки">
        <Lead>Чтобы взять «первый» билет, сначала определим, среди каких строк и по какому порядку его ищем.</Lead>
        <p>
          В запросе <code>ORDER BY</code> задаёт порядок выдачи. У <code>order_by()</code> указываем колонку и направление:
          <code> TicketRow.id.asc()</code> ставит меньшие id первыми, <code>TicketRow.id.desc()</code> ставит большие первыми.
          <code> LIMIT</code> оставляет первые строки из упорядоченного результата. В SQLAlchemy <code>limit(1)</code> берёт до одной строки.
          Когда ограничение отсутствует, шаг <code>limit()</code> пропускаем.
        </p>

        <Diagram caption="Открытые билеты, меньшие id первыми, одна строка">
          <DiagramFlow label="Как получается результат">
            <DiagramNode title="Данные">4 открыт, 1 закрыт, 2 открыт</DiagramNode>
            <DiagramArrow label="WHERE" />
            <DiagramNode title="Отбор">4 и 2</DiagramNode>
            <DiagramArrow label="ORDER BY" />
            <DiagramNode title="Порядок">2, затем 4</DiagramNode>
            <DiagramArrow label="LIMIT" />
            <DiagramNode title="Лимит">Только 2</DiagramNode>
          </DiagramFlow>
        </Diagram>

        <p>
          Если сначала отсортировать все билеты и оставить только первый, останется закрытый билет 1.
          После фильтра получим пустой список. В SQL этот порядок «отбор, сортировка, лимит» описывает результат запроса.
          Внутренние шаги SQLite может выполнять по-разному.
        </p>

        <h3>Пример с билетами</h3>
        <p>
          При открытом фильтре и лимите 1 направление решает, вернётся билет 2 или 4. Ниже можно проверить оба результата в SQLite.
          Готовая часть файла создаёт таблицу в памяти, добавляет три билета и закрывает ресурсы.
          Запросы начинаются со строки <code>tickets = select(TicketRow)</code>.
        </p>
        <p>
          Чтобы повторить пример, скопируйте весь блок в <code>planner_tickets_select_demo.py</code> в корне Planner.
          Запустите <code>python planner_tickets_select_demo.py</code> тем же Python, которым запускаете проект.
          Если имя уже занято, сохраните пример под свободным именем и укажите этот файл в команде.
        </p>
        <CodeBlock caption="planner_tickets_select_demo.py" code={TICKET_EXAMPLE} />
        <CodeBlock caption="Ожидаемый вывод" language="text" code={"Открытые, ASC, 1: [2]\nОткрытые, DESC, 1: [4]\nВсе, ASC, 1: [1]\nОткрытые, ASC, без лимита: [2, 4]\nБилет 99: []"} />
        <p>
          Вызов <code>session.scalars(statement).all()</code> выполняет подготовленное выражение и возвращает ORM-объекты,
          то есть строки в виде экземпляров <code>TicketRow</code>. Из их полей <code>id</code> получаем списки в выводе.
          При снятии фильтра снова подходит билет 1. Если совпадений нет, как у id 99, список пуст.
        </p>

        <p>Попробуйте предсказать результат для открытых билетов с лимитом 1 по убыванию. Затем включите ранний лимит и сравните запрос открытых билетов по возрастанию.</p>
        <TicketSelection />
      </Section>

      <Section number="04" title="Возвращаем задачи и проверяем прежние интерфейсы">
        <Lead>Запрос определяет, какие строки придут из SQLite. Репозиторий переводит эти строки в привычные для Planner задачи.</Lead>
        <p>
          В существующем <code>SqlTaskRepository.list</code> чтение <code>select(TaskRow)</code> даёт объекты <code>TaskRow</code>.
          <code> task_from_row(row)</code> превращает каждую полученную строку в <code>Task</code> с id, названием, приоритетом,
          статусом и тегами. Преобразование идёт внутри открытой Session, поэтому наружу можно вернуть готовый список задач.
          Session закрывает её владелец. Для чтения <code>commit</code> не нужен.
        </p>
        <p>
          Если в базе 12 задач, <code>GET /tasks</code> без параметров покажет первые 10 по id.
          HTTP задаёт этот размер по умолчанию и принимает явный <code>limit</code> от 1 до 50.
          Значение за границей диапазона даёт ответ 422. Прямой <code>select_tasks(limit=None)</code> вернёт все 12,
          а команда CLI <code>list</code> по-прежнему получает полный список через <code>list_tasks</code>.
        </p>

        <details>
          <summary><strong>Готовая проверка выборки</strong></summary>
          <p>
            Скопируйте весь блок в <code>tests/test_lesson_sql_selection.py</code> своего Planner.
            Если имя занято, выберите свободное имя вида <code>test_*.py</code> в той же папке и подставьте его в обе команды.
            Код использует зависимости для тестов из <code>requirements-dev.txt</code> этого проекта.
          </p>
          <p>
            Из корня Planner запустите входную проверку. <code>-q</code> сокращает служебный вывод, <code>-s</code> показывает напечатанные строки,
            а <code>-k</code> оставляет тест с указанным именем.
          </p>
          <CodeBlock language="text" code={"python -m pytest -q -s tests/test_lesson_sql_selection.py -k test_input_sql_selection"} />
          <p>
            Тест создаёт отдельную временную SQLite, заполняет её через существующий сервис и показывает id, статус и теги в новом чтении.
            Он работает до добавления <code>select</code>. После реализации запустите тот же файл без <code>-k</code>.
          </p>
          <CodeBlock language="text" code={"python -m pytest -q -s tests/test_lesson_sql_selection.py"} />
          <p>
            Итоговая проверка временно подключает SQL-сервис к существующему GET и выводит id. Для различающего вызова печатается
            один <code>SELECT</code>: после <code>WHERE</code> ищите условие статуса, после <code>ORDER BY</code> порядок по id,
            после <code>LIMIT</code> ограничение. Знаки <code>?</code> обозначают места параметров.
            SQLAlchemy также может показать <code>OFFSET ?</code> с нулевым смещением. В самой выборке достаточно задать лимит.
          </p>
          <CodeBlock caption="tests/test_lesson_sql_selection.py" code={SELECTION_CHECK} />
        </details>
      </Section>

      <Section number="05" title="Что мы будем делать в практике">
        <Lead>Продолжим свой Planner с рабочим SQL CRUD и реализуем одну операцию в существующем репозитории.</Lead>
        <LinkedNotes items={[
          { title: "Сверим исходные задачи", description: "Входная проверка покажет одну завершённую и две открытые задачи. У завершённой самый маленький id." },
          { title: "Запросим нужные строки", description: "Самостоятельно напишем выборку. Запрос одной открытой задачи вернёт первую подходящую, а в выводе будет виден один SELECT." },
          { title: "Сверим прежние интерфейсы", description: "Проверим id и поля HTTP-ответа, граничные значения limit и полный список CLI." },
        ]} />
        <p>
          Откройте «Готовую проверку выборки», чтобы получить тестовый файл. Если результат отличается от прогноза,
          вернитесь к шагам с билетами и сравните фильтр, порядок и лимит.
        </p>
      </Section>
    </RichLesson>
  );
}
