import { useState } from "react";
import {
  CodeBlock,
  Diagram,
  DiagramLane,
  DiagramTrack,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const BLOCK_TITLE = "Этап 4 · Блок 15 · Полный SQL-путь и единое переключение";

const DEMO = `from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import Integer, String, URL, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class StockRow(Base):
    __tablename__ = "stock"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer)


class StopWrite(Exception):
    pass


def card(row):
    if row is None:
        return None
    return row.id, row.name, row.quantity


with TemporaryDirectory() as folder:
    database = Path(folder) / "stock.sqlite3"
    engine = create_engine(URL.create("sqlite", database=database.as_posix()))
    sessions = sessionmaker(engine)
    try:
        Base.metadata.create_all(engine)
        with sessions.begin() as seed:
            seed.add(StockRow(id=4, name="Футляр", quantity=1))

        with sessions() as writer, writer.begin():
            item = StockRow(name="Чехол", quantity=2)
            writer.add(item)
            print("До flush:", item.id)
            writer.flush()
            print("После flush, внутри записи:", card(item))

            with sessions() as early_reader:
                print("Другая Session до commit:", card(early_reader.get(StockRow, item.id)))

        with sessions() as reader:
            print("Новая Session после commit:", card(reader.get(StockRow, 5)))
            print("Исходная строка:", card(reader.get(StockRow, 4)))

        try:
            with sessions() as writer, writer.begin():
                item = StockRow(name="Крепление", quantity=1)
                writer.add(item)
                writer.flush()
                canceled_id = item.id
                print("Номер перед отменой:", canceled_id)
                raise StopWrite()
        except StopWrite:
            with sessions() as reader:
                print("Новая Session после отмены:", card(reader.get(StockRow, canceled_id)))
                print("Сохранены:", card(reader.get(StockRow, 4)), card(reader.get(StockRow, 5)))
    finally:
        engine.dispose()`;

const SERVICE_IMPORTS = `from contextlib import nullcontext

from app.errors import TaskNotFoundError
from app.models import Task
from app.storage import JsonTaskRepository
from app.validators import validate_priority, validate_tags, validate_task_id, validate_title`;

const SERVICE_INIT = `    def __init__(self, storage=None, *, repository=None, write_scope=None):
        if repository is None:
            if storage is None:
                raise ValueError("Передайте storage или repository.")
            repository = JsonTaskRepository(storage)
        elif storage is not None:
            raise ValueError("Передайте storage или repository, но не оба.")
        self.repository = repository
        self.write_scope = nullcontext if write_scope is None else write_scope`;

const SERVICE_ADD = `    def add_task(self, title, priority, tags=None):
        clean_title = validate_title(title)
        checked_priority = validate_priority(priority)
        checked_tags = validate_tags([] if tags is None else tags)

        with self.write_scope():
            if tags is None:
                return self.repository.create(clean_title, checked_priority)
            return self.repository.create(clean_title, checked_priority, tags=checked_tags)`;

const ASSEMBLY = `def build_service(
    storage=None,
    settings: Settings | None = None,
    *,
    repository=None,
    write_scope=None,
) -> PlannerService:
    if repository is not None:
        if storage is not None or settings is not None:
            raise ValueError("Передайте repository отдельно от storage и settings.")
        return PlannerService(repository=repository, write_scope=write_scope)

    if storage is None:
        if settings is None:
            settings = load_settings()
        storage = JsonStorage(settings.json_path)
    return PlannerService(storage, write_scope=write_scope)


@contextmanager
def sql_service_context(
    session_factory: sessionmaker[Session],
) -> Iterator[PlannerService]:
    with session_factory() as session:
        yield build_service(
            repository=SqlTaskRepository(session),
            write_scope=session.begin,
        )`;

const JSON_CREATE = `    def create(self, title, priority, tags=None):
        tasks = self.storage.load()
        next_id = max((task.id for task in tasks), default=0) + 1
        task = Task(next_id, title, priority, tags=[] if tags is None else tags)
        tasks.append(task)
        self.storage.save(tasks)
        return task`;

const CHECK = `from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import URL

from app.DBase.models import Base, TaskRow
from app.DBase.repositories import SqlTaskRepository
from app.DBase.session import create_session_factory
from app.api import app
from app.cli import execute_command
from app.core.config import Settings
from app.core.dependencies import get_db, get_planner
from app.core.engine import make_engine
from app.main import build_service, sql_service_context


def sql_planner(session=Depends(get_db)):
    return build_service(repository=SqlTaskRepository(session), write_scope=session.begin)


def read_task(sessions, task_id):
    with sessions() as reader:
        task = SqlTaskRepository(reader).get(task_id)
        assert task is not None, f"Новая Session не нашла задачу {task_id}."
        row = reader.get(TaskRow, task_id)
        return task.to_dict() | {"title_normalized": row.title_normalized}


def read_all(sessions):
    with sessions() as reader:
        return [
            task.to_dict() | {"title_normalized": reader.get(TaskRow, task.id).title_normalized}
            for task in SqlTaskRepository(reader).list()
        ]


class CancelWrite(Exception):
    pass


@contextmanager
def cancel_after_write(session):
    with session.begin():
        yield
        raise CancelWrite()


def check_sql(client, sessions):
    neighbor = read_task(sessions, 40)
    print("Сосед до:", neighbor)

    response = client.post("/tasks", json={"title": "  Купить ЧАЙ  ", "priority": 3})
    print("POST:", response.status_code, response.json())
    assert response.status_code == 201, "Ожидали 201 на тестовой SQLite."
    public_task = response.json()
    task_id = public_task["id"]
    assert type(task_id) is int and task_id > 0 and task_id != 40
    assert public_task == {
        "id": task_id, "title": "Купить ЧАЙ", "priority": 3, "is_done": False,
    }
    stored = read_task(sessions, task_id)
    print("Новая Session после POST:", stored)
    assert stored == public_task | {"tags": [], "title_normalized": "купить чай"}

    with sql_service_context(sessions) as planner:
        tagged = planner.add_task("Оплатить СВЕТ", 5, tags=["дом", "счёт"])
    stored = read_task(sessions, tagged.id)
    print("Новая Session после прямого вызова:", stored)
    assert type(tagged.id) is int and tagged.id > 0 and tagged.id not in (40, task_id)
    assert stored == {
        "id": tagged.id, "title": "Оплатить СВЕТ", "priority": 5, "is_done": False,
        "tags": ["дом", "счёт"], "title_normalized": "оплатить свет",
    }
    assert tagged.to_dict() == {key: value for key, value in stored.items() if key != "title_normalized"}

    before_cancel = read_all(sessions)
    try:
        with sessions() as session:
            planner = build_service(
                repository=SqlTaskRepository(session),
                write_scope=lambda: cancel_after_write(session),
            )
            planner.add_task("Отменённая задача", 2)
    except CancelWrite:
        assert read_all(sessions) == before_cancel, "После отмены состав строк изменился."
        print("Отмена: новой строки нет, исходный снимок совпадает.")
    else:
        raise AssertionError("Ожидали контролируемую отмену до подтверждения.")

    invalid = client.post("/tasks", json={"title": "Без приоритета"})
    assert invalid.status_code == 422 and read_all(sessions) == before_cancel
    print("Без priority:", invalid.status_code, "SQL-снимок совпадает.")
    neighbor_after = read_task(sessions, 40)
    print("Сосед после:", neighbor_after)
    assert neighbor_after == neighbor


def check_json(client, settings):
    response = client.post("/tasks", json={"title": "Обычная задача", "priority": 2})
    assert response.status_code == 201 and settings.json_path.is_file()
    task_id = response.json()["id"]
    messages = []
    execute_command(
        build_service(settings=settings), "find", read=lambda prompt: str(task_id), write=messages.append,
    )
    assert any(f"[{task_id}] Обычная задача" in line for line in messages), messages
    print("JSON/CLI:", messages[0])


def main():
    if not callable(getattr(SqlTaskRepository, "create", None)):
        raise SystemExit("В SqlTaskRepository пока нет create. Добавим его и повторим запуск.")

    with TemporaryDirectory(prefix="planner-sql-create-") as folder:
        scratch = Path(folder)
        url = URL.create("sqlite", database=(scratch / "planner.sqlite3").as_posix())
        engine = make_engine(url.render_as_string(hide_password=False))
        sessions = create_session_factory(engine)
        settings = Settings(json_path=scratch / "tasks.json", app_name="Проверка Planner")
        old_overrides = app.dependency_overrides.copy()
        old_planner = app.state.planner
        missing = object()
        old_factory = getattr(app.state, "session_factory", missing)
        try:
            Base.metadata.create_all(engine)
            with sessions.begin() as seed:
                seed.add(TaskRow(
                    id=40, title="Купить ЧАЙ", priority=2, is_done=True,
                    tags=["архив"], title_normalized="купить чай",
                ))

            app.state.session_factory = sessions
            app.state.planner = build_service(settings=settings)
            app.dependency_overrides[get_planner] = sql_planner
            with TestClient(app) as client:
                check_sql(client, sessions)
                app.dependency_overrides.pop(get_planner)
                check_json(client, settings)
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(old_overrides)
            app.state.planner = old_planner
            if old_factory is missing:
                if hasattr(app.state, "session_factory"):
                    del app.state.session_factory
            else:
                app.state.session_factory = old_factory
            engine.dispose()

    print("Проверка SQL create завершена.")


if __name__ == "__main__":
    main()`;

function CopyableCode({ caption, code }: { caption: string; code: string }) {
  const [message, setMessage] = useState("");

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
      setMessage("Скопировано.");
    } catch {
      setMessage("Можно выделить код и скопировать его вручную.");
    }
  }

  return (
    <div>
      <CodeBlock code={code} caption={caption} />
      <button type="button" className="interactive-secondary" onClick={copy} aria-label={`Копировать ${caption}`}>
        Копировать код
      </button>
      {" "}<span role="status">{message}</span>
    </div>
  );
}

export function Lesson71({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title="Создаём задачу и получаем её id из SQLite"
        intro="Добавим SQL-создание в Planner и проверим, что новую задачу можно найти по номеру после записи."
      />

      <Section number="00" title="Вступление">
        <Lead>
          В тестовой SQLite мы уже можем найти задачу по номеру. Теперь добавим туда новую задачу и получим её номер.
        </Lead>
        <LinkedNotes
          items={[
            { title: "Точка старта", description: "В тестовой базе читаем задачи по id. Обычные API и CLI работают с одним JSON." },
            { title: "Новый вопрос", description: "Как получить от SQLite номер новой задачи и убедиться, что по нему можно прочитать запись?" },
            { title: "Результат", description: "SQLite назначает id. После успешной записи находим ту же задачу в новой Session." },
          ]}
        />
        <p>
          Посмотрим, когда номер появляется в объекте и когда строка становится видна при новом чтении.
          Затем сверим оба результата в Planner.
        </p>
      </Section>

      <Section number="01" title="Как новая строка получает id">
        <Lead>
          В тестовом Planner создадим задачу тем же запросом <code>POST /tasks</code>.
          Клиент передаёт <code>title</code> и <code>priority</code>. Ответ содержит <code>id</code>,
          <code>title</code>, <code>priority</code> и <code>is_done</code>.
        </Lead>
        <p>
          В SQLite <code>TaskRow</code> представляет строку таблицы. SQLAlchemy связывает этот объект с записью через
          <code>Session</code>. Приложение пользуется обычной <code>Task</code>, которую уже умеет собирать
          функция <code>task_from_row</code>.
        </p>
        <p>
          При импорте мы переносили существующий id, чтобы задача находилась по прежнему номеру. При добавлении новой
          строки оставляем её ключ SQLite. Столбец <code>INTEGER PRIMARY KEY</code> получает свободный номер, когда
          приложение вставляет строку без id. Так номер выбирается самой базой в момент вставки. Ручной расчёт
          <code>max(id) + 1</code> отдельным чтением номер не резервирует. От номера нам нужна уникальность среди имеющихся
          строк. После удаления SQLite может использовать номер снова.
        </p>
        <p>
          В примере со складом уже есть карточка «Футляр» с id <code>4</code> и остатком <code>1</code>. Готовим
          новую карточку «Чехол» с остатком <code>2</code>. Вызов <code>writer.add(item)</code> передаёт объект
          в работу Session. В этот момент <code>item.id</code> равен <code>None</code>. Вызов
          <code>writer.flush()</code> сразу отправляет вставку в SQLite. База выдаёт id, и SQLAlchemy заполняет
          <code>item.id</code>. У «Чехла» на этих данных появится номер <code>5</code>.
        </p>
        <p>
          В Planner готовый сервис проверяет данные до записи. Например, из <code>{"\"  Коробка  \""}</code> получается
          <code>{"\"Коробка\""}</code>. Этот заголовок видит пользователь, а <code>lower()</code> даёт
          <code>{"\"коробка\""}</code> для внутреннего поля <code>title_normalized</code>. Приоритет лежит в диапазоне
          от 1 до 5. Новая задача открыта, поэтому её <code>is_done</code> равно <code>False</code>.
        </p>
        <p>
          Во внутреннем вызове можно передать метки. Значение <code>tags=None</code> означает, что метки не передали.
          В этом случае строка получает свой пустой список. Проверка <code>tags is None</code> отличает отсутствие
          аргумента от переданного списка. Вызов <code>list(tags)</code> создаёт отдельный список. Например,
          из <code>{'["зал"]'}</code> получится копия, и добавление в неё метки «вход» оставит исходный список прежним.
          При чтении <code>task_from_row</code> тоже берёт копию. Конструктор ORM-строки получает значения по именам полей,
          как <code>name="Чехол"</code> в примере склада.
        </p>
        <p>
          С номером <code>5</code> мы уже можем собрать карточку в памяти. Теперь проверим, когда её увидит
          другой читатель.
        </p>
      </Section>

      <Section number="02" title="Когда другой читатель увидит запись">
        <Lead>
          <code>flush()</code> отправляет вставку в текущей транзакции. Транзакция здесь охватывает одну операцию
          записи. <code>commit</code> подтверждает изменение и делает его видимым новому читателю.
          Если до подтверждения возникает исключение, откат убирает вставку целиком.
        </Lead>

        <Diagram caption="Две Session и один файл склада">
          <DiagramLane title="Session, которая пишет">
            <DiagramTrack label="Карточка «Чехол»" items={["add · id ещё нет", "flush · id 5", "commit · строка сохранена"]} />
          </DiagramLane>
          <DiagramLane title="Свежая Session, которая читает">
            <DiagramTrack label="Чтение по id 5" items={["До commit · пусто", "После commit · Чехол, 2"]} />
          </DiagramLane>
          <p>Если после <code>flush</code> произошёл откат, новое чтение по выданному номеру ничего не найдёт.</p>
        </Diagram>

        <QuizCard
          question="Когда новая Session найдёт «Крышку»?"
          description="В другом файле склада сохранён «Ящик» с id 11 и остатком 3. Для «Крышки» с остатком 1 писатель уже получил id 12 при flush. Предскажем результат нового чтения по номеру 12."
          options={["Сразу после flush", "После успешного commit"]}
          correctIndex={1}
          explanation="Сразу после flush у писателя уже есть id 12, а другая Session получит None. После commit она увидит «Крышка, 1». При откате между этими моментами новое чтение по id 12 вернёт None."
        />

        <h3>Запустим пример склада</h3>
        <p>
          Сохраним код целиком в свободный файл <code>stock_insert_demo.py</code> в отдельной папке.
          В терминале из этой папки, в окружении Planner с SQLAlchemy, запустим
          <code>python stock_insert_demo.py</code>. Пример сам создаёт отдельную временную базу, сохраняет в ней «Футляр»
          и удаляет свои временные файлы по завершении.
        </p>
        <p>
          Сначала предскажем, что напечатают <strong>«До flush»</strong> и <strong>«Другая Session до commit»</strong>.
          Вызов <code>reader.get(StockRow, id)</code> ищет строку модели <code>StockRow</code> по номеру.
          Если её пока не видно, результатом будет <code>None</code>. Каждый читатель открывает свою Session
          к тому же файлу. Раннее чтение завершается до подтверждения записи.
        </p>
        <details>
          <summary>Код stock_insert_demo.py</summary>
          <CopyableCode caption="stock_insert_demo.py" code={DEMO} />
        </details>
        <CodeBlock
          language="text"
          caption="Ожидаемый вывод"
          code={"До flush: None\nПосле flush, внутри записи: (5, 'Чехол', 2)\nДругая Session до commit: None\nНовая Session после commit: (5, 'Чехол', 2)\nИсходная строка: (4, 'Футляр', 1)\nНомер перед отменой: 6\nНовая Session после отмены: None\nСохранены: (4, 'Футляр', 1) (5, 'Чехол', 2)"}
        />
        <p>
          При обычном выходе из <code>writer.begin()</code> транзакция подтверждается. На второй попытке мы
          специально поднимаем исключение <code>StopWrite</code> после получения номера <code>6</code>. Оно
          выходит из области транзакции, поэтому SQLite отменяет вставку. Перехват ошибки снаружи позволяет открыть
          свежую Session и сравнить результат. Сверим предсказание с обеими ветками.
        </p>

        <h3>Кто подтверждает запись в Planner</h3>
        <p>
          В Planner репозиторий отправляет вставку и собирает обычную <code>Task</code> с полученным id.
          За всю операцию отвечает <code>PlannerService</code>. Готовая сборка передаёт ему
          <code>session.begin</code> как <code>write_scope</code>. Без скобок это связанный метод. Он уже знает,
          к какой Session относится, и выполняется при вызове <code>self.write_scope()</code>. Обычный выход
          из <code>with</code> подтверждает запись. Исключение внутри него приводит к откату.
          Результат из <code>return</code> попадёт в обработчик только после успешного выхода из <code>with</code>.
        </p>
        <p>
          У тестового запроса Session закрывает его зависимость. Для прямого вызова Session открывает и закрывает
          готовый <code>sql_service_context</code>. И репозиторий, и область записи получают одну и ту же Session.
          Когда Planner работает через обычный JSON, <code>nullcontext</code> позволяет сервису выполнить тот же
          блок <code>with</code> без SQL-транзакции.
        </p>

        <h3 id="planner-create-setup">Подготовка Planner</h3>
        <p>
          Работаем в корне своего Planner, где уже есть папка <code>app</code>. Возьмём готовые фрагменты из
          четырёх пунктов и применим их к существующим файлам. Пути указаны от этого корня.
          В подготовке уже есть проверка входа, область записи и изолированный запуск.
          Тело SQL-метода <code>create</code> мы напишем в практике.
        </p>
        <details>
          <summary>app/service.py · проверка данных и область записи</summary>
          <p>
            Заменим импорты перед классом <code>PlannerService</code> и два метода внутри этого класса.
            <code>validate_title</code> очищает заголовок, <code>validate_priority</code> проверяет приоритет,
            <code>validate_tags</code> проверяет список меток.
          </p>
          <CopyableCode caption="Импорты перед PlannerService" code={SERVICE_IMPORTS} />
          <CopyableCode caption="Метод PlannerService.__init__" code={SERVICE_INIT} />
          <CopyableCode caption="Метод PlannerService.add_task" code={SERVICE_ADD} />
        </details>
        <details>
          <summary>app/main.py · сборка сервиса и SQL-контекст</summary>
          <p>
            Заменим функции <code>build_service</code> и <code>sql_service_context</code> вместе с декоратором
            перед второй функцией. <code>main()</code> остаётся точкой запуска JSON CLI. SQL-контекст мы
            вызываем отдельно в проверке.
          </p>
          <CopyableCode caption="Функции в app/main.py" code={ASSEMBLY} />
        </details>
        <details>
          <summary>app/storage.py · прежнее добавление через JSON</summary>
          <p>
            В <code>JsonTaskRepository</code> заменим только метод <code>create</code>. Он добавляет задачу в JSON
            и теперь принимает необязательные метки. Расчёт номера в этом фрагменте относится к JSON.
            Для строки в SQLite номер назначит база.
          </p>
          <CopyableCode caption="Метод JsonTaskRepository.create" code={JSON_CREATE} />
        </details>
        <details>
          <summary>check_sql_create.py · проверка на временной SQLite</summary>
          <p>
            Сохраним полный файл рядом с папкой <code>app</code> и запустим <code>python check_sql_create.py</code>
            из корня Planner после реализации метода. Если имя уже занято, сохраним файл как
            <code>check_sql_create_local.py</code> и подставим это имя в команду. Для запроса используется
            <code>TestClient</code>, который вызывает приложение без отдельного сервера.
            Если не хватает зависимостей проверки, установим их в окружение Planner командой
            <code>python -m pip install -r requirements-dev.txt</code>.
          </p>
          <p>
            В отчёте сравним id ответа POST и id из новой Session, затем проверим внутренние метки из прямого
            вызова, отмену и обычный путь через JSON. Снимок строки <code>40</code> показывает данные до и после
            записи. Если новое чтение пустое, отчёт остановится с номером отсутствующей задачи.
          </p>
          <CopyableCode caption="Файл check_sql_create.py" code={CHECK} />
        </details>
      </Section>

      <Section number="03" title="Что мы будем делать в практике">
        <Lead>Добавим одну операцию в существующий SQL-репозиторий и проверим её в своём Planner.</Lead>
        <LinkedNotes
          items={[
            { title: "Подключим область записи", description: "Скопируем подготовку. Тестовый сервис получает область записи от своей Session." },
            { title: "Создадим строку", description: "Сами напишем SQL create. SQLite выдаст номер новой задачи." },
            { title: "Перечитаем после записи", description: "Готовый запуск покажет ответ POST, сохранённые поля и метки, исходную соседнюю строку и результат отмены." },
          ]}
        />
        <p>
          Если номер появился, а новая Session не находит задачу, вернёмся к схеме между <code>flush</code>
          и <code>commit</code>. Проверенный <code>create</code> пригодится, когда будем готовить тестовую задачу
          для изменения через PUT.
        </p>
      </Section>
    </RichLesson>
  );
}
