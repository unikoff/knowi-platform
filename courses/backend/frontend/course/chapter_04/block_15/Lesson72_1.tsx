import { useState } from "react";
import { CodeBlock, Diagram, DiagramLane, DiagramTrack, Lead, LinkedNotes, RichHero, RichLesson, Section } from "@course-ui";

const JOURNAL_SETUP = `from sqlalchemy import Boolean, JSON, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

class JournalBase(DeclarativeBase):
    pass

class RequestRow(JournalBase):
    __tablename__ = "requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    item: Mapped[str]
    quantity: Mapped[int]
    closed: Mapped[bool] = mapped_column(Boolean)
    labels: Mapped[list[str]] = mapped_column(JSON)
    search_name: Mapped[str]

engine = create_engine("sqlite://")
JournalBase.metadata.create_all(engine)
sessions = sessionmaker(engine)
with sessions.begin() as writer:
    writer.add_all([
        RequestRow(id=8, item="Кабель", quantity=1, closed=False,
                   labels=["зал"], search_name="кабель"),
        RequestRow(id=9, item="СТОЙКА", quantity=3, closed=False,
                   labels=["сцена", "выдача"], search_name="стойка"),
    ])

updates = []

@event.listens_for(engine, "before_cursor_execute")
def count_updates(connection, cursor, sql, parameters, context, many):
    if sql.lstrip().upper().startswith("UPDATE REQUESTS "):
        updates.append(sql)`;

const FIND_EXAMPLE = `with sessions() as reader:
    stand = reader.get(RequestRow, 9)
    print(stand.item, stand.closed)
    print(reader.get(RequestRow, 99))`;

const SCOPE_EXAMPLE = `with sessions.begin() as writer:
    stand = writer.get(RequestRow, 9)
    print(stand.id, stand.quantity)`;

const DONE_EXAMPLE = `for attempt in ("первый вызов", "повтор"):
    updates.clear()
    with sessions.begin() as writer:
        request = writer.get(RequestRow, 9)
        if not request.closed:
            request.closed = True
    with sessions() as reader:
        closed = reader.get(RequestRow, 9).closed
    print(attempt, closed, len(updates))`;

const STATUS_EXAMPLE = `def journal_label(closed):
    if closed:
        return "заявка уже закрыта"
    return "заявка ещё открыта"

print(journal_label(True))`;

const DELETE_EXAMPLE = `with sessions.begin() as writer:
    stand = writer.get(RequestRow, 9)
    receipt = (stand.id, stand.item, stand.quantity, list(stand.labels))
    writer.delete(stand)

print("Квитанция:", receipt)

with sessions() as reader:
    print("Заявка 9:", reader.get(RequestRow, 9))
    cable = reader.get(RequestRow, 8)
    print("Заявка 8:", cable.id, cable.item, cable.quantity,
          cable.closed, cable.labels, cable.search_name)`;

const RECEIPT_EXAMPLE = `def show_receipt(receipt):
    if receipt is None:
        return "Квитанции нет"
    return receipt[1]

print(show_receipt(None))
print(show_receipt((9, "СТОЙКА", 3, ["сцена", "выдача"])))`;

const LAB_CELL = `exec('''
import atexit
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import URL, create_engine, event
from sqlalchemy.orm import sessionmaker

from app.DBase.models import Base, TaskRow
from app.DBase.repositories import SqlTaskRepository
from app.main import build_service

lab_dir = engine = None

def close_lab():
    global lab_dir, engine
    if engine is not None:
        engine.dispose()
        engine = None
    if lab_dir is not None:
        lab_dir.cleanup()
        lab_dir = None

def start_lab(closed=False):
    global lab_dir, engine, sessions
    close_lab()
    lab_dir = TemporaryDirectory(prefix="planner-done-delete-")
    atexit.unregister(close_lab)
    atexit.register(close_lab)
    path = Path(lab_dir.name) / "planner.sqlite3"
    engine = create_engine(URL.create("sqlite", database=str(path)))
    sessions = sessionmaker(engine)
    Base.metadata.create_all(engine)
    with sessions.begin() as db:
        db.add_all([
            TaskRow(id=17, title="Проверить АРХИВ", priority=2, is_done=True,
                    tags=["бумаги"], title_normalized="проверить архив"),
            TaskRow(id=42, title="Сдать КЛЮЧИ", priority=4, is_done=closed,
                    tags=["срок", "офис"], title_normalized="сдать ключи"),
        ])
    @event.listens_for(engine, "before_cursor_execute")
    def watch(connection, cursor, sql, parameters, context, many):
        words = sql.lstrip().lower().split()
        if words[:2] == ["update", "tasks"]:
            trace["UPDATE"] += 1
        if words[:3] == ["delete", "from", "tasks"]:
            trace["DELETE"] += 1

    reset_counts()
    print(path)
    print(read_rows(17, 42))

def read_rows(first_id, second_id):
    with sessions() as db:
        result = {}
        for key in (first_id, second_id):
            r = db.get(TaskRow, key)
            result[key] = None if r is None else (
                r.id, r.title, r.priority, r.is_done, list(r.tags), r.title_normalized,
            )
        return result

def reset_counts():
    trace.clear()
    trace.update(update=0, UPDATE=0, DELETE=0, read_in_scope=None)

def counts():
    return trace.copy()

class ObservedRepository(SqlTaskRepository):
    def get(self, key):
        trace["read_in_scope"] = self.session.in_transaction()
        return super().get(key)

    def update(self, task):
        trace["update"] += 1
        return super().update(task)

trace = {}

@contextmanager
def service_context():
    with sessions() as db:
        yield build_service(repository=ObservedRepository(db), write_scope=db.begin)

def done(key):
    with service_context() as service:
        return service.mark_done(key)

def delete(key):
    with service_context() as service:
        return service.delete_task(key)

def repo_delete(key):
    with sessions.begin() as db:
        return SqlTaskRepository(db).delete(key)
''')`;

const REGRESSION_CELL = `exec('''
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cli import handle_delete, handle_done
from app.core.dependencies import get_planner
from app.routers.tasks.router import router

def regression():
    with TemporaryDirectory(prefix="planner-compat-") as folder:
        factory_engine = create_engine(URL.create(
            "sqlite", database=str(Path(folder) / "compat.sqlite3"),
        ))
        try:
            factory = sessionmaker(factory_engine)
            Base.metadata.create_all(factory_engine)
            with factory.begin() as db:
                db.add_all([
                    TaskRow(id=17, title="Доска", title_normalized="доска"),
                    TaskRow(id=42, title="Папка", title_normalized="папка"),
                ])

            def provider():
                with factory() as db:
                    yield build_service(
                        repository=SqlTaskRepository(db), write_scope=db.begin,
                    )

            application = FastAPI()
            application.include_router(router)
            application.dependency_overrides[get_planner] = provider
            with TestClient(application) as client:
                first = client.delete("/tasks/17")
                assert first.status_code == 204 and first.content == b""
                assert client.get("/tasks/17").status_code == 404
                assert client.delete("/tasks/17").status_code == 404
                assert client.get("/tasks/42").status_code == 200

            messages = []
            for handler in (handle_done, handle_done, handle_delete, handle_delete):
                with contextmanager(provider)() as service:
                    handler(service, read=lambda prompt: "42", write=messages.append)

            assert messages == [
                "Задача выполнена.", "Задача выполнена.",
                "Задача удалена.", "Задача не найдена.",
            ], messages
            print("HTTP: 204 без тела, GET и повтор DELETE 404. CLI: ответы сохранены.")
        finally:
            factory_engine.dispose()
''')`;

type RequestStatus = "open" | "closed" | "missing";
type Command = "done" | "delete";
type WriteEffect = "UPDATE" | "DELETE" | "none";

const STATUS_LABEL: Record<RequestStatus, string> = {
  open: "Открыта",
  closed: "Закрыта",
  missing: "Строки нет",
};

const WRITE_LABEL: Record<WriteEffect, string> = {
  UPDATE: "UPDATE",
  DELETE: "DELETE",
  none: "Без записи",
};

function requestOutcome(status: RequestStatus, command: Command): {
  status: RequestStatus;
  write: WriteEffect;
  explanation: string;
} {
  if (status === "missing") {
    return { status, write: "none", explanation: "Поиск сразу показал, что такой строки нет. Команда сообщает о промахе. Свежий поиск снова пуст." };
  }
  if (command === "delete") {
    return { status: "missing", write: "DELETE", explanation: "Строка найдена и удалена по её номеру. Новый поиск по этому id пуст. Номер соседа в команду не передавался." };
  }
  if (status === "closed") {
    return { status, write: "none", explanation: "У найденной заявки уже стоит отметка о завершении. Команда подтверждает закрытие, а записывать новое состояние не нужно." };
  }
  return { status: "closed", write: "UPDATE", explanation: "У найденной открытой заявки изменился флаг. После подтверждения записи новый читатель видит закрытую заявку." };
}

function RequestPrediction() {
  const [target, setTarget] = useState("8");
  const [status, setStatus] = useState<RequestStatus>("closed");
  const [command, setCommand] = useState<Command>("delete");
  const [writeGuess, setWriteGuess] = useState<WriteEffect | "">("");
  const [statusGuess, setStatusGuess] = useState<RequestStatus | "">("");
  const [checked, setChecked] = useState(false);
  const result = requestOutcome(status, command);
  const targetName = target === "8" ? "8 · Кабель" : "9 · СТОЙКА";
  const neighborName = target === "8" ? "9 · СТОЙКА" : "8 · Кабель";

  function reset() {
    setTarget("8");
    setStatus("closed");
    setCommand("delete");
    setWriteGuess("");
    setStatusGuess("");
    setChecked(false);
  }

  return (
    <section aria-labelledby="request-prediction">
      <h3 id="request-prediction">Что увидит новый читатель?</h3>
      <p>В модели сначала выбрана закрытая заявка 8. Предскажем след записи и результат свежего поиска. Затем поменяем один вход и сравним. Соседняя заявка открыта в каждом сценарии.</p>
      <fieldset>
        <legend>Вход команды</legend>
        <p>
          <label>Номер заявки{" "}
            <select value={target} onChange={(event) => { setTarget(event.target.value); setChecked(false); }}>
              <option value="8">8 · Кабель</option>
              <option value="9">9 · СТОЙКА</option>
            </select>
          </label>
          {" "}<label>Состояние до команды{" "}
            <select value={status} onChange={(event) => { setStatus(event.target.value as RequestStatus); setChecked(false); }}>
              <option value="open">Открыта</option>
              <option value="closed">Закрыта</option>
              <option value="missing">Строки нет</option>
            </select>
          </label>
          {" "}<label>Команда{" "}
            <select value={command} onChange={(event) => { setCommand(event.target.value as Command); setChecked(false); }}>
              <option value="done">Завершить</option>
              <option value="delete">Удалить</option>
            </select>
          </label>
        </p>
      </fieldset>
      <fieldset>
        <legend>Наш прогноз</legend>
        <p>
          <label>След записи{" "}
            <select value={writeGuess} onChange={(event) => { setWriteGuess(event.target.value as WriteEffect | ""); setChecked(false); }}>
              <option value="">Выбрать</option>
              <option value="UPDATE">UPDATE</option>
              <option value="DELETE">DELETE</option>
              <option value="none">Без записи</option>
            </select>
          </label>
          {" "}<label>Что вернёт свежий поиск{" "}
            <select value={statusGuess} onChange={(event) => { setStatusGuess(event.target.value as RequestStatus | ""); setChecked(false); }}>
              <option value="">Выбрать</option>
              <option value="open">Открыта</option>
              <option value="closed">Закрыта</option>
              <option value="missing">Строки нет</option>
            </select>
          </label>
        </p>
        <button className="interactive-primary" type="button" disabled={!writeGuess || !statusGuess} onClick={() => setChecked(true)}>Сверить прогноз</button>
        {" "}<button className="interactive-secondary" type="button" onClick={reset}>Сбросить</button>
      </fieldset>
      <div aria-live="polite">
        {checked && (
          <>
            <p><strong>{writeGuess === result.write && statusGuess === result.status ? "Прогноз совпал." : "Сравним прогноз с результатом."}</strong> {result.explanation}</p>
            <ul>
              <li>След записи · {WRITE_LABEL[result.write]}</li>
              <li>{targetName} · {STATUS_LABEL[status]} → {STATUS_LABEL[result.status]}</li>
              <li>{neighborName} · Открыта → Открыта, все поля прежние</li>
            </ul>
          </>
        )}
      </div>
    </section>
  );
}

export function Lesson72_1({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Полный SQL-путь Planner"}
        title="Завершаем и удаляем задачу по id"
        intro="Что останется в базе после завершения, его повтора и удаления по одному номеру?"
      />
      <Section number="00" title="Вступление">
        <Lead>Частичное обновление уже умеет менять статус задачи. Посмотрим, что происходит, когда команду со знакомым номером отправляют снова или когда строки уже нет.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Находим задачу по id и умеем сохранить новый статус в SQLite." },
          { title: "Новый вопрос", description: "Что вернёт команда и что останется в таблице после завершения, повтора и удаления?" },
          { title: "Результат", description: "Закроем одну строку, подтвердим повтор без записи и докажем, что удаление затронуло только выбранный id." },
        ]} />
        <p>Ответ команды увидим сразу. Состояние базы проверим отдельно, когда работу сможет продолжить новый читатель.</p>
      </Section>

      <Section number="01" title="Как выбрать одну запись и проверить результат после записи">
        <Lead>В журнале оборудования заявка 8 на кабель и заявка 9 на три стойки открыты. Номер заявки ведёт к одной карточке, даже если несколько заявок описывают похожие вещи.</Lead>
        <p>
          Номер передаём вторым аргументом <code>Session.get()</code>. Знакомая ORM-модель <code>RequestRow</code> описывает строки журнала.
          {" "}<code>get</code> найдёт строку 9. Для номера 99 в этом журнале вернётся <code>None</code>.
        </p>
        <CodeBlock code={FIND_EXAMPLE} caption="По номеру 9 находим СТОЙКА False, по номеру 99 получаем None" />
        <details>
          <summary>Подготовка для самостоятельного запуска примеров</summary>
          <p>На странице уже есть исходные данные и выводы. Чтобы повторить опыт, один раз выполним подготовку в Python с SQLAlchemy. База существует в памяти. Остальные фрагменты можно выполнить в том же сеансе по порядку.</p>
          <CodeBlock code={JOURNAL_SETUP} caption="Две заявки и готовый счётчик UPDATE для таблицы requests" />
        </details>
        <p>Когда карточку собираемся менять, поиск входит в ту же область записи. Представим, что журнал открыт на время одного поручения. Сначала открываем его, потом находим карточку и при необходимости меняем её. При успешном выходе из блока запись подтверждается.</p>
        <CodeBlock code={SCOPE_EXAMPLE} caption="Сначала открываем область, затем читаем номер и количество · 9 3" />
        <p>
          {" "}<code>sessions.begin()</code> открывает Session с транзакцией. В Planner знакомый <code>write_scope</code> открывает транзакцию у той же Session, которую получил репозиторий.
          При первом чтении SQLAlchemy может открыть транзакцию сама. Поэтому при редактировании метода важно заметить, где он впервые читает запись.
          Новый вызов фабрики после выхода из области читает тот же журнал уже с подтверждёнными изменениями.
        </p>
      </Section>

      <Section number="02" title="Когда завершение требует записи">
        <Lead>Завершить заявку значит поставить отметку, что работа выполнена. Сначала смотрим прежнюю отметку. Открытую заявку закрываем, а уже закрытую можем подтвердить ещё раз без новой записи.</Lead>
        <p>
          Переход состояния происходит при изменении флага. Возьмём открытую заявку 9.
          {" "}<code>if not request.closed</code> пропускает внутрь только открытую карточку.
          {" "}<code>request.closed = True</code> меняет её отметку. В примере это сама ORM-строка, поэтому SQLAlchemy увидит изменение при выходе из области.
        </p>
        <CodeBlock code={DONE_EXAMPLE} caption="Два завершения заявки 9 · счётчик сбрасываем перед каждым" />
        <p>
          Первый вывод <code>первый вызов True 1</code> показывает новое состояние и один UPDATE. При повторе получается
          {" "}<code>повтор True 0</code>. Поиск нашёл существующую строку с <code>True</code>, условие не пустило к присваиванию.
          Если взять номер 99, сначала получим <code>None</code>. У отсутствующей строки нет флага, который можно проверить. В Planner знакомый поиск превращает такой промах в предметную ошибку.
        </p>
        <p>
          В Planner сервис получает обычную Task, а общий <code>update</code> переносит изменение в SQLite. Здесь есть два наблюдения: вызвали ли репозиторий и выполнила ли база UPDATE.
          Если передать репозиторию прежние значения, SQLAlchemy может не отправить UPDATE. Пара <code>1 / 0</code> подскажет, что ветка сохранения всё-таки сработала.
          По выводу <code>True</code> определяем результат команды, по свежему чтению и счётчикам проверяем сохранение.
        </p>
        <p>Когда отдельная функция уже знает ответ по прежней отметке, она может вернуть его сразу. В этом примере <code>return</code> отдаёт подпись закрытой карточки и завершает вызов. До второй подписи выполнение не доходит.</p>
        <CodeBlock code={STATUS_EXAMPLE} caption="Ранний возврат · заявка уже закрыта" />
        <p>Перед переходом к удалению предскажем результат для заявки 8, если она уже закрыта. Какими будут флаг и число UPDATE?</p>
        <details>
          <summary>Сверить прогноз для закрытой заявки</summary>
          <p>Свежий читатель увидит <code>True</code>, а UPDATE будет 0. Заявка существует и уже находится в требуемом состоянии.</p>
        </details>
      </Section>

      <Section number="03" title="Что остаётся после удаления">
        <Lead>Удалить заявку значит убрать карточку из действующего журнала. Если данные нужны для квитанции, заполняем её по найденной карточке и затем убираем строку.</Lead>
        <p>
          Материализовать результат значит сразу собрать обычное значение из полей строки. В примере это кортеж с данными стойки.
          {" "}<code>list(stand.labels)</code> делает отдельный список меток для квитанции.
          {" "}<code>writer.delete(stand)</code> принимает найденный ORM-объект и отмечает его к удалению. Успешный выход из области подтверждает удаление.
        </p>
        <p>До удаления свежая заявка 8 выглядит так: <code>8 Кабель 1 False ['зал'] кабель</code>. Запомним все шесть значений, чтобы потом сравнить.</p>
        <CodeBlock code={DELETE_EXAMPLE} caption="Удаляем заявку 9 и читаем квитанцию после закрытия Session" />
        <p>
          Квитанция содержит <code>(9, "СТОЙКА", 3, ["сцена", "выдача"])</code>. Она доступна после выхода из блока.
          Новый поиск печатает <code>Заявка 9: None</code>, а строка 8 содержит те же шесть значений.
          Это показывает адресность удаления: исчез именно переданный id, а сосед сохранил и публичные, и служебные поля.
          Уменьшение общего числа строк само по себе не скажет, какую карточку убрали.
        </p>
        <p>
          При промахе по номеру 99 нет ни строки для удаления, ни полей для квитанции. Получатель может проверить <code>is None</code> и отдельно обработать отсутствие данных.
        </p>
        <CodeBlock code={RECEIPT_EXAMPLE} caption="Просмотр квитанции · сначала «Квитанции нет», затем «СТОЙКА»" />
        <p>
          У SQL-репозитория Planner остаётся знакомый договор <code>Task | None</code>. Обычную Task уже умеет собирать <code>task_from_row</code>.
          Сервис отличает промах по <code>is None</code> и сообщает прежнюю предметную ошибку. У вызывающей команды свои результаты: например, HTTP сообщает об успешном удалении пустым ответом.
        </p>
        <Diagram caption="Заявка 9 в журнале · новые чтения после команд">
          <DiagramLane title="Два изменения одной строки">
            <DiagramTrack label="ID 9" items={["Открыта · False", "done · UPDATE → закрыта", "delete · DELETE → строки нет"]} />
          </DiagramLane>
          <DiagramLane title="Повтор на закрытой строке">
            <DiagramTrack label="ID 9" items={["Закрыта · True", "done · 0 UPDATE", "Закрыта · True"]} />
          </DiagramLane>
          <DiagramLane title="Соседняя строка">
            <DiagramTrack label="ID 8" items={["До команд · Кабель", "После команд · тот же полный снимок"]} />
          </DiagramLane>
        </Diagram>
        <RequestPrediction />
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Завершим и повторим", description: "Сохраним статус, прочитаем его в новой Session и подтвердим, что повтор оставляет оба счётчика на нуле." },
          { title: "Удалим и проверим оба id", description: "Получим данные удалённой задачи, подтвердим отсутствие выбранного id и сравним полный снимок соседа." },
        ]} />
        <p>Сначала проверим переход статуса и повтор. Затем применим тот же поиск по ключу к удалению и отдельно сравним оба id после подтверждения.</p>
        <details>
          <summary>Диагностика Planner для локального запуска</summary>
          <p>Из корня <code>project/</code> в рабочем окружении запускаем <code>python</code> и вставляем ячейку в его приглашение. Затем вызываем <code>start_lab()</code> для карточек завершения или <code>start_lab(closed=True)</code> для карточек удаления. Появятся временный путь и исходные снимки.</p>
          <p><code>read_rows</code> читает два id в новой Session. <code>counts</code> показывает обращения к update и прошедшие SQL UPDATE или DELETE. После правки Planner открываем свежий сеанс Python, чтобы подхватить изменения. <code>exit()</code> завершает сеанс и закрывает временную базу.</p>
          <CodeBlock code={LAB_CELL} caption="Ячейка диагностики · вызывает методы из вашего Planner" />
        </details>
        <details>
          <summary>Готовый прогон HTTP и CLI</summary>
          <p>После проверки обоих id вставляем эту ячейку в тот же сеанс Python и вызываем <code>regression()</code>. Она проверяет прежние сообщения и HTTP-коды на другой временной SQLite.</p>
          <CodeBlock code={REGRESSION_CELL} caption="Ячейка совместимости · отдельные данные и тестовые маршруты" />
        </details>
      </Section>
    </RichLesson>
  );
}
