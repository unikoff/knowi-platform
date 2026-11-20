import { useState } from "react";
import type { CSSProperties } from "react";
import {
  CodeBlock,
  Diagram,
  DiagramBranch,
  DiagramBranches,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const BLOCK_TITLE = "Полный SQL-путь и единое переключение";

const orderDemo = `from contextlib import closing
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory


def setup(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, title TEXT, closed INTEGER)")
        db.executemany("INSERT INTO orders VALUES (?, ?, ?)", [
            (9, "Витрина", 0),
            (10, "Полка", 0),
        ])


def close_order(path, order_id):
    statements = []
    with closing(sqlite3.connect(path)) as db, db:
        db.set_trace_callback(statements.append)
        db.execute("BEGIN")
        order = db.execute(
            "SELECT id, title, closed FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if order is None:
            return "Заказ не найден", 0
        if order[2]:
            return "Заказ закрыт", 0

        db.execute("UPDATE orders SET closed = 1 WHERE id = ?", (order_id,))
        writes = sum(sql.startswith("UPDATE ") for sql in statements)
        return "Заказ закрыт", writes


def show_orders(path):
    with closing(sqlite3.connect(path)) as db:
        rows = db.execute("SELECT id, closed FROM orders ORDER BY id").fetchall()
        return [(order_id, bool(closed)) for order_id, closed in rows]


with TemporaryDirectory() as directory:
    path = Path(directory) / "orders.sqlite3"
    setup(path)
    print("До вызовов:", show_orders(path))
    for order_id in (9, 9, 99):
        message, writes = close_order(path, order_id)
        print(f"{order_id}: {message}; UPDATE: {writes}")
        print("Свежий просмотр:", show_orders(path))`;

const orderDemoOutput = `До вызовов: [(9, False), (10, False)]
9: Заказ закрыт; UPDATE: 1
Свежий просмотр: [(9, True), (10, False)]
9: Заказ закрыт; UPDATE: 0
Свежий просмотр: [(9, True), (10, False)]
99: Заказ не найден; UPDATE: 0
Свежий просмотр: [(9, True), (10, False)]`;

const doneStarter = `    def mark_done(self, task_id):
        with self.write_scope():
            task = self.get_task(task_id)
            raise NotImplementedError("Здесь будет ваше решение")`;

const doneCheck = `from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from sqlalchemy import URL, create_engine, event
from sqlalchemy.exc import SQLAlchemyError

from app.DBase.models import Base, TaskRow
from app.DBase.repositories import SqlTaskRepository
from app.DBase.session import create_session_factory
from app.cli import handle_done
from app.main import sql_service_context


TARGET = {
    "id": 120, "title": "Старый План", "priority": 4,
    "is_done": True, "tags": ["внутр"],
}
CONTROL = {
    "id": 121, "title": "Сосед", "priority": 1,
    "is_done": False, "tags": ["рядом"],
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def with_normalized(task):
    return {**task, "title_normalized": task["title"].lower()}


def read_pair(session_factory):
    with session_factory() as session:
        rows = [session.get(TaskRow, task_id) for task_id in (120, 121)]
        require(all(row is not None for row in rows), "Одна из двух строк пропала.")
        return [
            {field: getattr(row, field) for field in with_normalized(TARGET)}
            for row in rows
        ]


def verify_context(service, engine):
    repository = service.repository
    require(
        isinstance(repository, SqlTaskRepository)
        and repository.session.get_bind() is engine,
        "Нужен SQL-сервис, подключённый к этой учебной базе.",
    )
    require(
        service.write_scope == repository.session.begin,
        "Восстановите sql_service_context из темы создания задачи.",
    )


def check_done(path):
    engine = create_engine(URL.create("sqlite", database=str(path)))
    try:
        session_factory = create_session_factory(engine)
        Base.metadata.create_all(engine)
        with session_factory() as session, session.begin():
            session.add_all(
                [TaskRow(**with_normalized(task)) for task in (TARGET, CONTROL)]
            )

        sql_updates = 0

        @event.listens_for(engine, "before_cursor_execute")
        def count_update(conn, cursor, statement, params, context, many):
            compiled = context.compiled
            if compiled is not None and compiled.isupdate:
                if compiled.statement.table.name == TaskRow.__tablename__:
                    nonlocal sql_updates
                    sql_updates += 1

        open_target = {**TARGET, "is_done": False}
        expected_open = [with_normalized(open_target), with_normalized(CONTROL)]
        try:
            with sql_service_context(session_factory) as service:
                verify_context(service, engine)
                service.patch_task(120, is_done=False)
            require(read_pair(session_factory) == expected_open, "Флаг False не сохранился.")

            before = sql_updates
            with sql_service_context(session_factory) as service:
                verify_context(service, engine)
                service.patch_task(120)
            initial = read_pair(session_factory)
            require(initial == expected_open, "Пустой PATCH изменил данные.")
            require(sql_updates == before, "Пустой PATCH отправил UPDATE.")
        except (AttributeError, NotImplementedError, RuntimeError, SQLAlchemyError) as error:
            raise RuntimeError(f"Вход PATCH не готов: {error}") from error

        print("После PATCH, новая Session:", initial)
        expected_done = [with_normalized(TARGET), with_normalized(CONTROL)]
        cases = (
            ("Первый вызов", 120, "Задача выполнена.", 1),
            ("Повторный вызов", 120, "Задача выполнена.", 0),
            ("Отсутствующий id", 999, "Задача не найдена.", 0),
        )
        for label, task_id, expected_message, expected_writes in cases:
            messages = []
            before = sql_updates
            with sql_service_context(session_factory) as service:
                verify_context(service, engine)
                with patch.object(
                    service.repository, "update", wraps=service.repository.update
                ) as update:
                    handle_done(service, read=lambda prompt: str(task_id), write=messages.append)
                calls = update.call_count
            writes = sql_updates - before
            rows = read_pair(session_factory)
            print(f"{label}: {' | '.join(messages)} update: {calls}, SQL UPDATE: {writes}")
            print("Новая Session:", rows)
            require(messages == [expected_message], f"{label}: неожиданное сообщение.")
            require(calls == expected_writes, f"{label}: неверное число вызовов update.")
            require(writes == expected_writes, f"{label}: неверное число SQL UPDATE.")
            require(rows == expected_done, f"{label}: итоговые строки отличаются.")

        task, control = read_pair(session_factory)
        for record in (task, control):
            del record["title_normalized"]
        print("Проверка done прошла")
        print(f"DONE_TASK = {task!r}")
        print(f"CONTROL_TASK = {control!r}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        with TemporaryDirectory() as directory:
            check_done(Path(directory) / "done.sqlite3")
    except (AttributeError, NotImplementedError, RuntimeError, SQLAlchemyError) as error:
        raise SystemExit(f"Проверка done остановлена: {error}") from error`;

const controlStyle: CSSProperties = {
  background: "var(--surface-2)",
  color: "var(--reading)",
  border: "1px solid var(--line-soft)",
  borderRadius: 8,
  font: "inherit",
  padding: "8px 12px",
  maxWidth: "100%",
};

const controlsStyle: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  alignItems: "center",
  gap: 8,
};

function CopyPython({ caption, code }: { caption: string; code: string }) {
  const [feedback, setFeedback] = useState("");

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setFeedback("Скопировано");
    } catch {
      setFeedback("Выделите код и скопируйте его вручную.");
    }
  }

  return (
    <>
      <CodeBlock caption={caption} code={code} />
      <div style={controlsStyle}>
        <button type="button" style={controlStyle} onClick={copy}>
          Скопировать: {caption}
        </button>
        <span role="status">{feedback}</span>
      </div>
    </>
  );
}

function OrderClosingTryout() {
  const [initiallyClosed, setInitiallyClosed] = useState(false);
  const [prediction, setPrediction] = useState("");
  const [writes, setWrites] = useState<number[]>([]);
  const [showSaved, setShowSaved] = useState(false);
  const closed = initiallyClosed || writes.length > 0;
  const total = writes.reduce((sum, count) => sum + count, 0);

  function reset() {
    setPrediction("");
    setWrites([]);
    setShowSaved(false);
  }

  return (
    <section
      className="lesson-infographic"
      aria-label="Проба повторного закрытия"
      data-order-closing-tryout=""
      style={{
        background: "var(--surface-2)",
        border: "1px solid var(--line-soft)",
        borderRadius: 12,
        padding: 16,
        display: "grid",
        gap: 12,
      }}
    >
      <h3>Сколько раз запишется заказ?</h3>
      <p>В модели два заказа: «Витрина» и открытая «Полка». Выберите старт для «Витрины», спрогнозируйте число записей, затем закройте её дважды.</p>
      <div style={controlsStyle}>
        <label htmlFor="done-order-start">Исходный статус «Витрины»</label>
        <select
          id="done-order-start"
          style={controlStyle}
          value={initiallyClosed ? "closed" : "open"}
          onChange={(event) => {
            setInitiallyClosed(event.target.value === "closed");
            reset();
          }}
        >
          <option value="open">Открыта</option>
          <option value="closed">Закрыта</option>
        </select>
      </div>
      <div style={controlsStyle}>
        <label htmlFor="done-order-prediction">Всего UPDATE за два вызова</label>
        <select
          id="done-order-prediction"
          style={controlStyle}
          value={prediction}
          disabled={writes.length > 0}
          onChange={(event) => setPrediction(event.target.value)}
        >
          <option value="">Выбрать</option>
          <option value="0">0</option>
          <option value="1">1</option>
          <option value="2">2</option>
        </select>
      </div>
      <div style={controlsStyle}>
        <button
          type="button"
          style={controlStyle}
          disabled={prediction === "" || writes.length === 2}
          onClick={() => setWrites((previous) => [...previous, closed ? 0 : 1])}
        >
          Закрыть заказ
        </button>
        <button
          type="button"
          style={controlStyle}
          disabled={writes.length !== 2 || showSaved}
          onClick={() => setShowSaved(true)}
        >
          Свежий просмотр
        </button>
        <button type="button" style={controlStyle} onClick={reset}>Сбросить попытку</button>
      </div>
      <div aria-live="polite">
        <p>«Витрина» сейчас {closed ? "закрыта" : "открыта"}. Вызовов: {writes.length} из 2.</p>
        {writes.map((count, index) => (
          <p key={index}>
            Вызов {index + 1}: «Заказ закрыт», UPDATE: {count}.{" "}
            {count === 1 ? "Статус изменился." : "Заказ уже был закрыт."}
          </p>
        ))}
        {showSaved && (
          <p>
            <strong>{Number(prediction) === total ? "Прогноз совпал." : "Прогноз отличается."}</strong>{" "}
            Всего UPDATE: {total}. {initiallyClosed
              ? "«Витрина» с самого начала была закрыта."
              : "Запись понадобилась только при первом закрытии."}{" "}
            «Витрина» закрыта, «Полка» открыта.
          </p>
        )}
      </div>
      <small>Модель показывает результат команды. Она не подключается к вашей базе.</small>
    </section>
  );
}

export function Lesson72_1({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title="Завершаем задачу без повторной записи"
        intro="Одна команда, один id, два вызова. Посмотрим, почему запись нужна только при смене статуса."
      />

      <Section number="00" title="Вступление">
        <Lead>Представьте, что вы дважды подряд ввели done для одной задачи Planner.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "После частичного обновления Planner умеет найти задачу и сохранить изменённый флаг в SQLite." },
          { title: "Новый вопрос", description: "Что делать при повторной команде, если задача уже завершена?" },
          { title: "Результат", description: "Первый вызов сохранит True. Повтор вернёт завершённую задачу без ещё одной записи." },
        ]} />
        <p>Мы сравним флаг до команды, ответ на команду и число записей. Каждое наблюдение покажет свою часть результата.</p>
      </Section>

      <Section number="01" title="Когда команда меняет состояние">
        <Lead>В каталоге заказов команда «Закрыть» должна оставить выбранный заказ закрытым.</Lead>
        <p>Сначала находим карточку по id. У заказа есть флаг <code>closed</code>: он показывает, закрыт ли заказ сейчас. Если флаг уже истинный, нужное состояние достигнуто. Можно сразу вернуть успешный результат. Такой выход из функции называется ранним возвратом. При отсутствии карточки поиск заканчивается сообщением «Заказ не найден».</p>

        <Diagram caption="Что происходит после команды закрытия">
          <DiagramBranches label="Три результата поиска заказа по id">
            <DiagramBranch label="Найден открытый">
              <p>Открыт → Закрыт</p>
              <p>Успех, 1 UPDATE</p>
            </DiagramBranch>
            <DiagramBranch label="Найден закрытый">
              <p>Закрыт → Закрыт</p>
              <p>Успех, 0 UPDATE</p>
            </DiagramBranch>
            <DiagramBranch label="Не найден">
              <p>Карточки по этому id нет.</p>
              <p>Сообщение об отсутствии, 0 UPDATE</p>
            </DiagramBranch>
          </DiagramBranches>
        </Diagram>

        <h3>Закрываем заказ в отдельной SQLite</h3>
        <p>Заказы 9 «Витрина» и 10 «Полка» открыты. Закроем заказ 9 дважды, потом выберем отсутствующий 99. После каждого вызова заново откроем базу и посмотрим на обе записи.</p>
        <p>Сохраните общий пример как <code>close_order_demo.py</code> в своей учебной папке и выполните там <code>python close_order_demo.py</code>. Python сам создаст отдельную временную SQLite и очистит её после прогона. Для примера достаточно стандартной библиотеки.</p>
        <details>
          <summary>Открыть код примера с заказами</summary>
          <CopyPython caption="close_order_demo.py" code={orderDemo} />
        </details>
        <CodeBlock caption="Вывод примера" language="text" code={orderDemoOutput} />
        <ol>
          <li><code>close_order</code> открывает базу и в транзакции выбирает карточку командой SELECT по переданному id. <code>fetchone()</code> возвращает одну строку либо <code>None</code>.</li>
          <li>В строке <code>(id, title, closed)</code> значение <code>closed</code> стоит третьим, поэтому <code>if order[2]:</code> проверяет флаг. Первый вызов видит 0, отправляет UPDATE только для заказа 9 и возвращает успех. При выходе из <code>with</code> транзакция завершается.</li>
          <li>Повторный вызов видит уже сохранённую 1. Ранний <code>return</code> возвращает успех до строки с UPDATE. Для id 99 срабатывает отдельный ранний выход, потому что карточка отсутствует.</li>
        </ol>
        <p>Вывод «Свежий просмотр» получен из другого соединения после команды. В нём есть оба заказа. По статусу заказа 9 виден результат закрытия, а по заказу 10 видно, что запись была адресной.</p>
      </Section>

      <Section number="02" title="Как увидеть лишний вызов записи">
        <Lead>Два одинаковых сообщения «Заказ закрыт» совместимы с разным количеством записей.</Lead>
        <p>В примере число UPDATE показывает, сколько SQL-команд отправилось за один вызов. В Planner готовая проверка дополнительно считает обращения к общему <code>update</code>. Репозиторий может получить неизменённую задачу и пропустить отправку UPDATE. Поэтому ноль SQL-команд сам по себе ещё не подтверждает ранний возврат до вызова репозитория.</p>

        <OrderClosingTryout />

        <QuizCard
          question="При повторе счётчик вызовов update равен 1, а счётчик SQL UPDATE равен 0. Что показывает отчёт?"
          options={[
            "Сервис вернулся раньше вызова update.",
            "Сервис вызвал update, а хранилище не отправило команду UPDATE.",
          ]}
          correctIndex={1}
          explanation="Оба счётчика измеряют разные границы. Для раннего возврата до передачи в репозиторий оба должны остаться на нуле."
        />

        <h3>Те же наблюдения в Planner</h3>
        <p><code>with self.write_scope():</code> открывает готовую область записи перед поиском. В тестовом SQL-сервисе её предоставляет <code>sql_service_context</code> из темы создания задачи. Возврат из тела <code>with</code> сначала завершает область, затем отдаёт результат обработчику. Публичный <code>patch_task</code> сам открывает свою область. Для завершения есть общий путь записи на уровне репозитория.</p>
        <ul>
          <li><code>self.get_task(task_id)</code> возвращает обычную Task. Для отсутствующего id прежний сервис поднимает <code>TaskNotFoundError</code>. Обработчик превращает её в «Задача не найдена.».</li>
          <li><code>task.is_done</code> хранит статус. <code>task.mark_done()</code> меняет флаг этой Task в памяти и возвращает <code>None</code>. Изменённый объект остаётся доступен под именем <code>task</code>.</li>
          <li><code>self.repository.update(task)</code> принимает Task целиком и сохраняет её по собственному id. Он возвращает сохранённую Task либо <code>None</code>, если строки для записи нет. В прежнем сервисе отсутствие передаётся через <code>self._raise_task_not_found(task.id)</code>.</li>
        </ul>
        <p>Готовый прогон подаёт id в прежний <code>handle_done</code>. В него уже переданы тестовый SQL-сервис, ввод и сбор сообщений. После вызова отдельная Session покажет сохранённые данные обеих задач. Отчёт укажет сообщение и два счётчика за текущий вызов.</p>
      </Section>

      <Section number="03" title="Что мы будем делать в практике">
        <Lead>Продолжим свой Planner после частичного обновления. Самостоятельная правка нужна в одном методе сервиса.</Lead>
        <LinkedNotes items={[
          { title: "Завершим задачу", description: "Открытая задача после первой команды будет завершена в SQLite." },
          { title: "Повторим команду", description: "Сохраним успешное сообщение и увидим 0 вызовов update и 0 SQL UPDATE." },
          { title: "Проверим поиск по id", description: "Для отсутствующей задачи увидим прежнее сообщение. Строки цели и соседа останутся прежними." },
        ]} />
        <p>Сначала подготовьте два открытых фрагмента ниже. Область записи, поиск и вызов обработчика готовы. Правило перехода и возврат результата напишете сами.</p>

        <details>
          <summary>Старт для mark_done</summary>
          <p>В своём <code>project/app/service.py</code> найдите <code>PlannerService.mark_done</code> и замените только этот метод фрагментом ниже. Строка с <code>NotImplementedError</code> обозначает место для вашего кода. Пока она на месте, проверка остановится с понятным сообщением.</p>
          <CopyPython caption="PlannerService.mark_done" code={doneStarter} />
        </details>

        <details>
          <summary>Проверка done</summary>
          <p>Сохраните полный блок как <code>project/check_done_practice.py</code>. Если файл с таким именем уже есть, используйте <code>check_done_practice_local.py</code> и подставьте это имя в команду. Откройте терминал в <code>project</code> с окружением Planner. После своей правки выполните <code>python check_done_practice.py</code>.</p>
          <p>Проверка сама создаёт временную SQLite. В неё попадают задача 120 «Старый План» с приоритетом 4 и тегом «внутр» и задача 121 «Сосед» с приоритетом 1 и тегом «рядом». Сначала знакомый PATCH открывает задачу 120, затем пустой PATCH оставляет её открытой. Сообщение «Вход PATCH не готов» означает, что нужно восстановить выданный тестовый контекст или результат частичного обновления по причине в отчёте.</p>
          <CopyPython caption="check_done_practice.py" code={doneCheck} />
        </details>

        <p>Если все три случая совпали, отчёт напечатает «Проверка done прошла» и две строки <code>DONE_TASK =</code> и <code>CONTROL_TASK =</code>. Сохраните эти присваивания целиком: в них id, заголовок, приоритет, статус и теги обеих задач. Их можно перенести в следующую практику после очистки временной базы.</p>
      </Section>
    </RichLesson>
  );
}
