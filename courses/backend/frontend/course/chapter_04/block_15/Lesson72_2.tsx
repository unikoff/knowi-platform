import { useState } from "react";
import {
  CodeBlock,
  Diagram,
  DiagramArrow,
  DiagramFlow,
  DiagramNode,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "@course-ui";

const bookingDemo = `from sqlalchemy import create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class BookingRow(Base):
    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    note: Mapped[str]


engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
Sessions = sessionmaker(bind=engine)

with Sessions.begin() as session:
    session.add_all([
        BookingRow(id=11, name="Проектор", note="вечер"),
        BookingRow(id=12, name="Зал", note="утро"),
    ])

with Sessions.begin() as session:
    row = session.get(BookingRow, 11)
    assert row is not None
    card = (row.id, row.name, row.note)
    session.delete(row)

print("Карточка:", card)
with Sessions() as fresh:
    print("После commit:", list(fresh.scalars(select(BookingRow.id))))

with Sessions.begin() as session:
    print("При повторе:", session.get(BookingRow, 11))

with Sessions() as fresh:
    print("После повтора:", list(fresh.scalars(select(BookingRow.id))))

engine.dispose()`;

const serviceStarter = `    def delete_task(self, task_id):
        with self.write_scope():
            valid_task_id = validate_task_id(task_id)
            deleted = self.repository.delete(valid_task_id)
            if deleted is None:
                self._raise_task_not_found(valid_task_id)`;

const checkModule = `from contextlib import contextmanager

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.api import app
from app.cli import handle_delete
from app.core.dependencies import get_db, get_planner
from app.core.engine import make_engine
from app.DBase.models import Base, TaskRow, task_from_row
from app.DBase.repositories import SqlTaskRepository
from app.DBase.session import create_session_factory
from app.main import build_service, sql_service_context
from app.models import Task


DONE_TASK = {}
CONTROL_TASK = {}


def input_tasks():
    records = (DONE_TASK, CONTROL_TASK)
    fields = {"id", "title", "priority", "is_done", "tags"}
    for record in records:
        assert isinstance(record, dict) and set(record) == fields, (
            "Скопируйте целиком присваивания DONE_TASK и CONTROL_TASK."
        )
        assert Task(**record).to_dict() == record
    assert DONE_TASK["id"] != CONTROL_TASK["id"], "Нужны два разных id."
    assert DONE_TASK["is_done"] is True, "Цель должна быть завершена."
    return records


@pytest.fixture
def sessions(tmp_path):
    url = URL.create("sqlite", database=(tmp_path / "delete.sqlite3").as_posix())
    engine = make_engine(url.render_as_string(hide_password=False))
    Base.metadata.create_all(engine)
    try:
        yield create_session_factory(engine)
    finally:
        engine.dispose()


def read_tasks(sessions):
    with sessions() as fresh:
        rows = fresh.scalars(select(TaskRow).order_by(TaskRow.id))
        return {
            row.id: (task_from_row(row).to_dict(), row.title_normalized)
            for row in rows
        }


@pytest.fixture
def initial(sessions):
    tasks = input_tasks()
    with sessions.begin() as session:
        for record in tasks:
            session.add(TaskRow(
                **record, title_normalized=record["title"].lower()
            ))
    return {
        record["id"]: (record, record["title"].lower())
        for record in tasks
    }


def test_ready_restores_tasks(sessions, initial):
    assert read_tasks(sessions) == initial


def test_ready_cli_context_commits(sessions):
    with sql_service_context(sessions) as planner:
        created = planner.add_task("Проверка области", 2, tags=["проверка"])
    assert read_tasks(sessions).get(created.id) == (
        created.to_dict(), "проверка области"
    ), "Проверьте готовый sql_service_context из темы создания задачи."


@pytest.mark.parametrize("index", [0, 1])
def test_repository_returns_task_and_deletes_selected_row(sessions, initial, index):
    target = list(initial)[index]
    with sessions.begin() as session:
        deleted = SqlTaskRepository(session).delete(target)
    assert isinstance(deleted, Task), "Результат должен быть обычной Task."
    assert deleted.to_dict() == initial[target][0]
    assert read_tasks(sessions) == {
        task_id: state for task_id, state in initial.items() if task_id != target
    }


def test_repository_missing_id_returns_none(sessions, initial):
    missing_id = next(task_id for task_id in (1, 2, 3) if task_id not in initial)
    with sessions.begin() as session:
        deleted = SqlTaskRepository(session).delete(missing_id)
    assert deleted is None
    assert read_tasks(sessions) == initial


@contextmanager
def fail_before_commit(session):
    with session.begin():
        yield
        session.flush()
        raise RuntimeError("Учебный сбой до commit")


@contextmanager
def sql_client(sessions, *, fail=False):
    def provide_planner(session: Session = Depends(get_db)):
        scope = (lambda: fail_before_commit(session)) if fail else session.begin
        return build_service(
            repository=SqlTaskRepository(session), write_scope=scope
        )

    saved_overrides = app.dependency_overrides.copy()
    had_factory = hasattr(app.state, "session_factory")
    saved_factory = getattr(app.state, "session_factory", None)
    app.state.session_factory = sessions
    app.dependency_overrides[get_planner] = provide_planner
    try:
        with TestClient(app, raise_server_exceptions=not fail) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved_overrides)
        if had_factory:
            app.state.session_factory = saved_factory
        else:
            del app.state.session_factory


def test_interfaces_delete_and_repeat(sessions, initial):
    target = DONE_TASK["id"]
    neighbor = CONTROL_TASK["id"]
    path = f"/tasks/{target}"
    with sql_client(sessions) as client:
        response = client.delete(path)
        assert response.status_code == 204
        assert response.content == b""
        assert read_tasks(sessions) == {neighbor: initial[neighbor]}
        assert client.get(path).status_code == 404
        assert client.delete(path).status_code == 404

    messages = []
    with sql_service_context(sessions) as planner:
        handle_delete(planner, read=lambda _: str(target), write=messages.append)
    assert messages == ["Задача не найдена."]
    assert read_tasks(sessions) == {neighbor: initial[neighbor]}


def test_interfaces_failure_before_commit(sessions, initial):
    with sql_client(sessions, fail=True) as client:
        response = client.delete(f'/tasks/{DONE_TASK["id"]}')
    assert response.status_code == 500
    assert read_tasks(sessions) == initial`;

const bookings = [
  { id: 12, title: "Зал", note: "утро" },
  { id: 16, title: "Экран", note: "вечер" },
  { id: 20, title: "Стойка", note: "день" },
];

type Removal = { id: number; card: (typeof bookings)[number] | undefined };

function BookingRemoval() {
  const [rows, setRows] = useState(bookings);
  const [id, setId] = useState(12);
  const [result, setResult] = useState<Removal | null>(null);
  const [fresh, setFresh] = useState(false);

  function removeBooking() {
    const card = rows.find((row) => row.id === id);
    setRows(rows.filter((row) => row.id !== id));
    setResult({ id, card });
    setFresh(false);
  }

  function reset() {
    setRows(bookings);
    setId(12);
    setResult(null);
    setFresh(false);
  }

  return (
    <div className="quiz-card" role="group" aria-label="Проба удаления брони">
      <h3>Отменяем бронь по id</h3>
      <p>В начале в каталоге есть три брони. Действия в макете показывают, что увидят хранилище и обработчик.</p>
      <ul>
        <li>12, Зал (утро)</li>
        <li>16, Экран (вечер)</li>
        <li>20, Стойка (день)</li>
      </ul>
      <label>
        Ключ брони{" "}
        <select value={id} onChange={(event) => setId(Number(event.target.value))}>
          <option value={12}>12</option>
          <option value={99}>99</option>
        </select>
      </label>
      <p>Выберем id и заранее назовём карточку или None, ожидаемый HTTP-статус и оставшиеся id.</p>
      <button className="interactive-secondary" type="button" onClick={removeBooking}>Подтвердить удаление</button>
      <div className="choice-feedback" role="status">
        {result && (
          <p>
            {result.card
              ? "Бронь " + result.id + ": карточка «" + result.card.title + "» (" + result.card.note + "). После commit получаем 204."
              : "Бронь " + result.id + ": поиск дал None. Получаем 404, каталог не изменился."}
          </p>
        )}
      </div>
      <div className="answer-options" style={{ display: "flex", flexWrap: "wrap" }}>
        <button className="interactive-secondary" type="button" disabled={!result} onClick={() => setFresh(true)}>Открыть свежий просмотр</button>
        <button className="interactive-secondary" type="button" onClick={reset}>Начать заново</button>
      </div>
      <div role="status">
        {fresh && <p>Свежий просмотр. Остались id <strong>{rows.map((row) => row.id).join(", ")}</strong>.</p>}
      </div>
    </div>
  );
}

function pythonFile(code: string) {
  return "data:text/x-python;charset=utf-8," + encodeURIComponent(code + "\n");
}

export function Lesson72_2({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Planner и SQLite"}
        title="Удаляем одну задачу по id"
        intro="Уберём выбранную задачу из базы и выясним, когда можно подтвердить удаление."
      />

      <Section number="00" title="Когда завершённую задачу пора убрать">
        <Lead>Мы только что научились завершать задачу по id. Иногда завершённая запись больше не нужна в списке.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Можем найти задачу, изменить её статус и перечитать запись из базы." },
          { title: "Новый вопрос", description: "Как удалить выбранную строку и ответить на запрос, если такого id уже нет?" },
          { title: "Результат", description: "DELETE даст пустой ответ 204, задача исчезнет, сосед останется. Повторное удаление получит 404." },
        ]} />
        <p>Начнём с того же поиска по id. Затем проследим, как выбранная строка исчезает из базы и когда ответ становится успешным.</p>
      </Section>

      <Section number="01" title="Как отметить одну строку к удалению">
        <Lead>Представим каталог оборудования. Нужно отменить бронь проектора и сохранить бронь зала.</Lead>
        <p>
          Строке SQL соответствует объект Python. Это ORM-объект. В вызове <code>session.get(BookingRow, 11)</code> класс
          задаёт таблицу, а число задаёт первичный ключ, по которому база находит одну запись. При промахе <code>get</code> возвращает <code>None</code>.
          Проверка <code>is None</code> отличает отсутствие объекта от найденной записи.
        </p>
        <p>
          <code>session.delete(row)</code> принимает найденный ORM-объект в той же Session и помечает его для удаления.
          Сначала сохраним обычную карточку, например кортеж из id, названия и пометки. Карточка хранит данные независимо от ресурса.
        </p>
        <p>
          При <code>flush</code> Session отправляет SQL DELETE внутри транзакции. При нормальном выходе из <code>with</code> работа фиксируется
          (<code>commit</code>), а при ошибке до фиксации откатывается. Для проверки результата откроем новую Session после выхода из области.
        </p>

        <Diagram caption="Куда попадают данные при удалении">
          <DiagramFlow label="Найдена бронь с id 11">
            <DiagramNode title="Каталог" shape="database">
              <p>11, Проектор (вечер)<br />12, Зал (утро)</p>
            </DiagramNode>
            <DiagramArrow label="Поиск по id 11" />
            <DiagramNode title="Строка найдена">
              <p>Сохраняем карточку проектора и помечаем его строку к удалению.</p>
            </DiagramNode>
            <DiagramArrow label="После commit" />
            <DiagramNode title="Свежий просмотр" shape="database">
              <p>Осталась только бронь 12. Пометка «вечер» доступна в сохранённой карточке.</p>
            </DiagramNode>
          </DiagramFlow>
          <DiagramFlow label="При повторе поиск возвращает None">
            <DiagramNode title="Каталог после удаления" shape="database"><p>12, Зал (утро)</p></DiagramNode>
            <DiagramArrow label="Поиск по id 11" />
            <DiagramNode title="Получили None"><p>В Session нет объекта, который нужно удалить. Бронь 12 остаётся.</p></DiagramNode>
          </DiagramFlow>
        </Diagram>

        <h3>Полный пример с двумя бронями</h3>
        <p>
          В готовом скрипте модель описывает таблицу, а фабрика создаёт Session. Скрипт сам наполняет отдельную SQLite в памяти.
          Проследим блок, в котором поиск возвращает бронь 11, мы сохраняем её поля и помечаем строку к удалению.
        </p>
        <p>
          Пример можно прочитать на странице или запустить. Для запуска сохраним{" "}
          <a href={pythonFile(bookingDemo)} download="booking_delete_demo.py">booking_delete_demo.py</a>{" "}
          в отдельной учебной папке и выполним там <code>python booking_delete_demo.py</code> в окружении Planner с SQLAlchemy.
        </p>
        <details>
          <summary>Открыть код примера</summary>
          <CodeBlock caption="booking_delete_demo.py" code={bookingDemo} />
        </details>
        <CodeBlock caption="Вывод примера" language="text" code={"Карточка: (11, 'Проектор', 'вечер')\nПосле commit: [12]\nПри повторе: None\nПосле повтора: [12]"} />
        <p>
          Карточку можно напечатать после закрытия Session. Первый список из новой Session показывает, что из таблицы исчезла только строка 11.
          После повторного поиска строка 11 отсутствует, а список остаётся прежним.
        </p>
      </Section>

      <Section number="02" title="Когда DELETE сообщает об успехе">
        <Lead>Путь удаления проходит через результат репозитория, фиксацию и HTTP-ответ.</Lead>
        <p>
          Если бронь найдена и изменение зафиксировано, ответом будет 204 с пустым телом. Сохранённая карточка нужна внутри приложения.
          Повторный поиск вернёт <code>None</code>. Сервис распознает отсутствие, обработчик ответит 404.
        </p>
        <BookingRemoval />
        <p>Попробуем сначала существующий id, затем повторим действие для него. В обоих случаях откроем отдельный свежий просмотр.</p>
        <QuizCard
          question="Что увидит новая Session, если DELETE уже отправлен при flush, а область записи откатилась?"
          options={["Исходную бронь на месте", "Каталог без выбранной брони"]}
          correctIndex={0}
          explanation="Flush выполнил удаление в текущей транзакции. Откат отменил её изменение, поэтому новое чтение снова находит исходную бронь, а обработчик не возвращает 204."
        />
        <p>
          В Planner <code>SqlTaskRepository</code> уже пользуется <code>self.session</code>. Знакомая функция <code>task_from_row</code> создаёт
          обычную <code>Task</code>, включая отдельный список <code>tags</code>. Аннотация <code>Task | None</code> обещает два исхода:
          найденную задачу или значение отсутствия.
        </p>
        <p>
          Сервис открывает область записи через <code>with self.write_scope()</code> до обращения к репозиторию. При штатном выходе изменение подтверждено,
          и обработчик <code>DELETE /tasks/&#123;task_id&#125;</code> возвращает пустой 204. Если результат репозитория равен <code>None</code>,
          проверка <code>deleted is None</code> ведёт к предметной ошибке. HTTP переводит её в 404, CLI сообщает «Задача не найдена.».
        </p>
      </Section>

      <Section number="03" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Восстановим две задачи", description: "Готовая проверка найдёт в учебной SQLite те же id, статус завершённой цели и теги обеих задач." },
          { title: "Реализуем удаление", description: "Напишем метод в своём SQL-репозитории. Готовая проверка увидит Task с её прежними полями или None для отсутствующего id." },
          { title: "Проверим ответ и соседа", description: "Первый DELETE даст пустой 204, повторный вернёт 404. Новая Session подтвердит поля соседа." },
        ]} />
        <h3>Подготовка к практике</h3>
        <p>
          Продолжаем в своём Planner. В <code>project/app/service.py</code> заменим метод <code>PlannerService.delete_task</code> готовым фрагментом.
          Он использует уже настроенную область записи и прежнюю проверку отсутствия.
        </p>
        <CodeBlock caption="Область записи для удаления" code={serviceStarter} />
        <p>
          Сохраним готовую{" "}
          <a href={pythonFile(checkModule)} download="test_lesson_task_delete.py">проверку удаления</a>{" "}
          в <code style={{ overflowWrap: "anywhere" }}>project/tests/test_lesson_task_delete.py</code>. Если имя уже занято, сохраним файл как
          {" "}<code>test_lesson_task_delete_local.py</code> и используем это имя в командах.
        </p>
        <details>
          <summary>Открыть полный файл проверки</summary>
          <CodeBlock caption="tests/test_lesson_task_delete.py" code={checkModule} />
        </details>
        <p>
          Найдём в выводе проверки завершения строки, начинающиеся с <code>DONE_TASK =</code> и <code>CONTROL_TASK =</code>.
          Заменим ими оба пустых присваивания в новом файле. Между фигурными скобками лежат именованные поля задачи, включая id,
          статус True или False и список тегов. Копируем обе строки целиком.
          Если вывод потерялся, из <code>project/</code> снова запустим <code>python check_done_practice.py</code>
          {" "}(либо <code>python check_done_practice_local.py</code>, если так назвали файл) и возьмём его результат.
        </p>
        <p>
          Проверка сама создаёт временную базу из этих двух записей. Запустим из <code>project/</code> в окружении Planner
          {" "}<code>python -m pytest -v tests/test_lesson_task_delete.py -k ready</code>. Команда запускает готовые проверки.
          Флаг <code>-k ready</code> выбирает входную группу, а <code>-v</code> выводит имена и результаты по отдельности.
          Например, <code>test_ready_restores_tasks PASSED</code> означает, что новая Session прочитала обе восстановленные записи.
        </p>
        <p>
          После входной проверки сами напишем метод в <code>project/app/DBase/repositories.py</code>.
          Если остановимся на выборе ветки, вернёмся к схеме «Куда попадают данные при удалении».
          Для разбора поведения ответа пригодится раздел «Когда DELETE сообщает об успехе».
        </p>
      </Section>
    </RichLesson>
  );
}
