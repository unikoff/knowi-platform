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

const INPUT_EXAMPLE = `import json

for body in ('{}', '{"is_done": null}', '{"is_done": false, "title": null}'):
    form = json.loads(body)
    status = form.get("is_done")
    if status is None:
        final_status = True
    else:
        final_status = status
    print("есть ключ:", "is_done" in form,
          "значение:", status, "итог:", final_status)`;

const QUEUE_EXAMPLE = `def send_to_storage(jobs):
    for field, value in jobs:
        print("Передано на запись:", field, value)

print("Пустой бланк")
send_to_storage([])

print("Бланк с отметкой «не завершена»")
send_to_storage([("статус", False)])`;

const DISPATCH_EXAMPLE = `import json
import sqlite3
from contextlib import closing

def dispatch(db, number, body):
    query = "SELECT * FROM requests WHERE id = ?"
    if db.execute(query, (number,)).fetchone() is None:
        print("Заявка не найдена:", number)
        return

    form = json.loads(body)
    jobs = []
    status = form.get("is_done")
    if status is not None:
        jobs.append(("UPDATE requests SET is_done = ? WHERE id = ?", status))
    priority = form.get("priority")
    if priority is not None:
        jobs.append(("UPDATE requests SET priority = ? WHERE id = ?", priority))

    updates = []
    db.set_trace_callback(
        lambda sql: updates.append(sql) if sql.startswith("UPDATE ") else None
    )
    try:
        for sql, value in jobs:
            db.execute(sql, (value, number))
        db.commit()
    finally:
        db.set_trace_callback(None)

    ident, title, rank, done, tags = db.execute(query, (number,)).fetchone()
    print(ident, title, rank, bool(done), tags, "| UPDATE:", len(updates))

with closing(sqlite3.connect(":memory:")) as db:
    db.execute("CREATE TABLE requests (id INTEGER, title TEXT, priority INTEGER,"
               " is_done INTEGER, tags TEXT)")
    db.executemany("INSERT INTO requests VALUES (?, ?, ?, ?, ?)", [
        (9, "Проектор", 4, True, "зал Б"),
        (12, "Кабель", 2, True, "техзал"),
    ])
    db.commit()
    dispatch(db, 9, '{"is_done": false, "title": null}')
    dispatch(db, 9, '{}')
    dispatch(db, 99, '{}')
    dispatch(db, 12, '{"priority": 3}')`;

const DIAGNOSTIC = `exec(r'''
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker

from app.api import app
from app.core.dependencies import get_db, get_planner
from app.core.engine import make_engine
from app.DBase.models import Base, TaskRow
from app.DBase.repositories import SqlTaskRepository
from app.main import build_service


def _patch_lab_snapshot(factory):
    with factory() as reader:
        rows = [reader.get(TaskRow, ident) for ident in (120, 121)]
        return [
            (row.id, row.title, row.priority, row.is_done,
             list(row.tags), row.title_normalized)
            for row in rows
        ]


def observe_write(method, body, *, task_id=120, start_done=True):
    with TemporaryDirectory() as directory:
        path = Path(directory) / "patch-lab.sqlite3"
        url = URL.create("sqlite", database=path.as_posix())
        engine = make_engine(url.render_as_string(hide_password=False))
        factory = sessionmaker(bind=engine)
        try:
            Base.metadata.create_all(engine)
            with factory.begin() as seed:
                seed.add_all([
                    TaskRow(id=120, title="Старый План", priority=4,
                            is_done=start_done, tags=["внутр"],
                            title_normalized="старый план"),
                    TaskRow(id=121, title="Сосед", priority=1,
                            is_done=False, tags=["рядом"],
                            title_normalized="сосед"),
                ])

            def provide_planner(db=Depends(get_db)):
                return build_service(
                    repository=SqlTaskRepository(db), write_scope=db.begin
                )

            calls = []
            statements = []
            actual_update = getattr(SqlTaskRepository, "update", None)

            def counted_update(repository, task):
                calls.append(task.id)
                return actual_update(repository, task)

            def observe_sql(conn, cursor, statement, parameters, context, executemany):
                if statement.lstrip().upper().startswith("UPDATE TASKS "):
                    statements.append(statement)

            counter = (
                patch.object(SqlTaskRepository, "update", counted_update)
                if actual_update is not None else nullcontext()
            )

            with patch.object(app.state, "session_factory", factory, create=True), \\
                 patch.dict(app.dependency_overrides, {get_planner: provide_planner}), \\
                 TestClient(app, raise_server_exceptions=False) as client:
                print("GET до:", client.get("/tasks/120").text)
                print("Строки до:", _patch_lab_snapshot(factory))
                event.listen(engine, "before_cursor_execute", observe_sql)
                try:
                    with counter:
                        response = client.request(method, f"/tasks/{task_id}", json=body)
                finally:
                    event.remove(engine, "before_cursor_execute", observe_sql)
                print("Ответ:", response.status_code, response.text)
                print("GET после:", client.get("/tasks/120").text)
                print("Строки после:", _patch_lab_snapshot(factory))
                print("Вызовы update:", len(calls), "| SQL UPDATE:", len(statements))
        finally:
            engine.dispose()
''')`;

const STATUS_INPUTS = [
  {
    label: "Убрать поле",
    body: "{}",
    correctIndex: 0,
    explanation: "В бланке нет графы статуса. Он остаётся True, а список поручений пуст. Получаем ноль UPDATE.",
  },
  {
    label: "Передать null",
    body: '{"is_done": null}',
    correctIndex: 0,
    explanation: "Графа есть, но None означает, что нового значения по договору нет. Статус остаётся True, UPDATE не нужен.",
  },
  {
    label: "Передать false",
    body: '{"is_done": false}',
    correctIndex: 1,
    explanation: "False указывает новый статус. Заявка открывается, а поручение даёт один UPDATE. Проверка истинности значения потеряла бы False.",
  },
] as const;

export function Lesson72({ module }: { module?: string } = {}) {
  const [inputIndex, setInputIndex] = useState(0);
  const input = STATUS_INPUTS[inputIndex];

  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Полный SQL-путь Planner"}
        title="Различаем пропуск, null и False в PATCH"
        intro="Задача уже закрыта, а клиент хочет открыть её снова. Разберём, как принять False и сохранить остальные поля."
      />
      <Section number="00" title="Вступление">
        <Lead>PUT принимает новое состояние публичных полей задачи. В PATCH клиент может указать только ту часть, которую хочет изменить.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "По номеру задачи мы уже умеем найти и заменить её в SQLite." },
          { title: "Новый вопрос", description: "Откуда взять значение поля, если в запросе его пропустили, передали null или явно указали false?" },
          { title: "Результат", description: "Явное false откроет задачу. Пропуск и null сохранят поле, а пустая правка вернёт задачу без UPDATE." },
        ]} />
        <p>Сначала прочитаем заявку на оборудование как бланк правки, затем посмотрим, какую команду диспетчер отправит в базу.</p>
      </Section>

      <Section number="01" title="Какие значения просит изменить PATCH">
        <Lead>Заявка 9 на проектор закрыта. У неё приоритет 4 и метка «зал Б». Диспетчер получает бланк, в котором может быть просьба открыть её снова.</Lead>
        <p>
          Частичная правка меняет только указанные поля. Если графы статуса в бланке нет, диспетчер сохраняет прежний статус.
          Если в графе указано <code>null</code>, она есть, но по договору Planner новое значение тоже не задано.
          Отметка <code>false</code> означает конкретное «не завершена». Для закрытой заявки это просьба открыть её снова.
        </p>
        <p>
          <code>json.loads</code> читает JSON и возвращает словарь Python. В нём <code>null</code> становится <code>None</code>,
          а <code>false</code> становится <code>False</code>. Проверка <code>"is_done" in form</code> говорит, была ли графа в бланке,
          а <code>form.get("is_done")</code> читает значение и даёт <code>None</code>, если ключа нет.
          Проверим три отдельных бланка на одном исходном статусе <code>True</code>.
        </p>
        <CodeBlock code={INPUT_EXAMPLE} caption="Одна графа и три способа её заполнить" />
        <Diagram caption="Как бланк правки меняет статус закрытой заявки" orientation="vertical">
          <DiagramLane title="Ключ отсутствует">
            <DiagramTrack label="Статус заявки" items={["{}", "Ключа нет, get даёт None", "Сохраняем True"]} />
          </DiagramLane>
          <DiagramLane title="В графе null">
            <DiagramTrack label="Статус заявки" items={['{"is_done": null}', "Ключ есть, значение None", "Сохраняем True"]} />
          </DiagramLane>
          <DiagramLane title="В графе false">
            <DiagramTrack label="Статус заявки" items={['{"is_done": false}', "Ключ есть, значение False", "Получаем False"]} />
          </DiagramLane>
        </Diagram>
        <p>
          В первых двух бланках <code>status is None</code> даёт <code>True</code>, поэтому остаётся исходный статус.
          В третьем бланке значение <code>False</code> попадает в <code>else</code>. Если выбрать через <code>status or True</code>,
          то и явный <code>False</code> превратится в <code>True</code>.
        </p>
        <p>
          В Planner маршрут уже отбирает явно присланные поля через <code>exclude_unset</code> и пропускает <code>None</code>
          через <code>exclude_none</code>. После двух фильтров смешанный бланк на схеме передаст сервису только <code>is_done=False</code>.
          Сравним чтение полей из такого словаря. Если <code>{'chosen = {"is_done": False}'}</code>, выражение
          {" "}<code>chosen.get("is_done", True)</code> вернёт <code>False</code>, а <code>chosen.get("priority", 4)</code> вернёт <code>4</code>.
          Запасное значение берётся только при отсутствии ключа.
        </p>
      </Section>

      <Section number="02" title="Когда запрос обходится без записи">
        <Lead>Диспетчер находит заявку по номеру и составляет список поручений для базы. Пустой список означает, что можно сразу показать её текущее состояние.</Lead>
        <p>
          Такое действие без изменения называют <em>no-op</em>. Пустой словарь <code>{"{}"}</code> и словарь
          <code>{" "}{'{"is_done": False}'}</code> различаются своей длиной. У второго есть одна графа с конкретным значением.
          Проверка истинности всего словаря отвечает на вопрос, есть ли в нём записи.
        </p>
        <p>
          Для заявки 9 сравним два готовых списка. В пустом бланке поручений нет. В бланке с отметкой «не завершена»
          есть поручение записать False. В этом коротком примере печать означает, что поручение передали на запись.
        </p>
        <CodeBlock code={QUEUE_EXAMPLE} caption="Что диспетчер передаёт на запись" />
        <p>
          Под строкой «Пустой бланк» ничего не печатается, потому что цикл не делает ни одного шага.
          У второго бланка появляется строка «Передано на запись: статус False». Значение False находится внутри одного поручения.
        </p>
        <p>
          Теперь сравним с фактической записью в SQLite. Первый бланк открывает проектор и даёт один UPDATE.
          Следом пустой бланк возвращает ту же строку и даёт ноль UPDATE.
        </p>
        <pre>{[
          "9 Проектор 4 False зал Б | UPDATE: 1",
          "9 Проектор 4 False зал Б | UPDATE: 0",
        ].join("\n")}</pre>
        <p>
          Чтение снимка нужно в обоих случаях. Поиск по несуществующему номеру 99 сообщает, что заявки нет.
          Перед пробой ниже подумайте, что изменит {" "}<code>{'{"priority": 3}'}</code>{" "}
          в заявке 12 с приоритетом 2 и статусом True.
        </p>
        <details>
          <summary>Повторить опыт в реестре оборудования</summary>
          <p>
            Здесь реестр создаётся в памяти. SELECT ищет заявку по номеру. После выполнения поручений тот же SELECT
            возвращает снимок. Знак <code>?</code> отмечает место данных, которые <code>execute</code> берёт из кортежа.
            Готовый счётчик записывает команды UPDATE. Нижний блок создаёт две учебные заявки и запускает четыре пробы.
          </p>
          <CodeBlock code={DISPATCH_EXAMPLE} caption="Полный опыт с реестром в SQLite" />
          <p>
            Для номера 99 выводится «Заявка не найдена: 99». Для последней пробы выводится
            «12 Кабель 3 True техзал | UPDATE: 1». Приоритет меняется, а графы статуса в этом бланке нет.
          </p>
        </details>

        <h3>Какой статус останется у кабеля?</h3>
        <p>Заявка 12 закрыта и имеет приоритет 2. Выберите, что придёт в графе статуса. Каждая попытка начинает с исходного <code>True</code>.</p>
        <p role="group" aria-label="Графа статуса в бланке">
          {STATUS_INPUTS.map(({ label }, index) => (
            <button
              key={label}
              type="button"
              className={index === inputIndex ? "interactive-primary" : "interactive-secondary"}
              aria-pressed={index === inputIndex}
              onClick={() => setInputIndex(index)}
            >
              {label}
            </button>
          ))}
        </p>
        <QuizCard
          key={input.label}
          question="Какой будет статус и сколько команд UPDATE выполнится?"
          description={<code>{input.body}</code>}
          options={["True, 0 UPDATE", "False, 1 UPDATE", "True, 1 UPDATE", "False, 0 UPDATE"]}
          correctIndex={input.correctIndex}
          explanation={input.explanation}
        />

        <h3>Как проверить это в Planner</h3>
        <p>
          При замене задачи репозиторий уже умеет сохранить полную <code>Task</code> по её номеру. Вспомним
          «Что сохраняется при замене» и «Одна Session для изменения и транзакции». Сервис открывает область до
          первого поиска, а репозиторий работает на той же Session. После выхода из неё свежий читатель увидит подтверждённую запись.
          Если номер отсутствует, существующий поиск и маршрут сообщают об отсутствии задачи даже с пустым телом.
        </p>
        <p>
          Для проверки сравним снимки до и после, а также два счётчика. «Вызовы update» показывают, отправил ли сервис
          поручение репозиторию. «SQL UPDATE» показывает, дошла ли команда до SQLite. Если отправить в репозиторий прежнее
          значение, SQLAlchemy может не выполнить UPDATE. Тогда снимки совпадут и SQL-счётчик будет нулевым, а счётчик вызовов покажет лишнее поручение.
        </p>
        <details>
          <summary>Диагностика для локального запуска</summary>
          <p>
            Из каталога <code>project/</code> в окружении своего Planner запустите <code>python</code>.
            Вставьте следующий блок в консоль от <code>exec</code> до последней скобки. Внутри блока подготовлены временная
            база, подмена репозитория для запроса и наблюдение за записью. Каждый вызов создаёт и удаляет собственную учебную копию.
          </p>
          <CodeBlock code={DIAGNOSTIC} caption="Команда подготовки наблюдений в Python-консоли" />
          <p>
            Проверьте исходную сборку командой ниже. В «Строки до» у задачи 120 будут старый заголовок,
            приоритет 4, статус True и метка «внутр». Ответ PUT даст 200, а в новом снимке увидите «Проба PUT» и «проба put»
            с прежним номером и меткой. Если не так, вернитесь к своей реализации замены и общей Session.
          </p>
          <CodeBlock code={'observe_write("PUT", {"title": "Проба PUT", "priority": 4, "is_done": True})'} caption="Входная проверка готового PUT" />
          <p>
            После правок откройте свежий Python и снова вставьте блок. В вызовах из практики второй аргумент задаёт тело запроса,
            а <code>start_done=False</code> начинает наблюдение с открытой задачи. В Python пишем <code>False</code> и <code>None</code>,
            клиент отправит их как JSON <code>false</code> и <code>null</code>. Снимки показывают id, название, приоритет, статус,
            метки и поисковую копию названия в таком порядке.
          </p>
        </details>
      </Section>

      <Section number="03" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Соберём новую Task", description: "В свежем чтении увидим, что явный False открыл задачу, а поля без нового значения сохранились." },
          { title: "Проверим пустую ветку", description: "Получим прежнюю задачу из пустого тела и тела с null. Оба счётчика покажут ноль." },
        ]} />
        <p>Оба результата пригодятся при завершении задачи по номеру. Первое действие меняет статус, а повтор можно распознать по уже сохранённому состоянию.</p>
      </Section>
    </RichLesson>
  );
}
