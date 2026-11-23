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
} from "../../shared";

const PREPARATION = String.raw`import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import select
from sqlalchemy.engine import URL

from app.DBase.models import TaskRow
from app.DBase.session import create_session_factory
from app.core.config import load_settings
from app.core.engine import make_engine


ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "data" / "rehearsal"
SNAPSHOT = "snapshot.json"
DATABASE = "planner-test.sqlite3"
BASELINE = "baseline.json"
LEGACY = "legacy-probe.json"
LEGACY_ROW = {
    "id": 99, "title": "Отметить рабочий список",
    "priority": 2, "is_done": False, "tags": [],
}


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def write_json(path, data):
    with path.open("x", encoding="utf-8") as destination:
        json.dump(data, destination, ensure_ascii=False, indent=2)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def fingerprint(path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def database_url(folder):
    return URL.create("sqlite", database=(folder / DATABASE).as_posix())


def prepare(folder, demo):
    from fastapi.testclient import TestClient

    work_json = load_settings().json_path.resolve()
    require(not work_json.is_relative_to(folder), "Выберите папку вне рабочего JSON.")
    folder.mkdir(parents=True, exist_ok=False)
    if demo:
        rows = [
            {"id": 2, "title": "Шлем", "priority": 2, "is_done": False, "tags": ["детский"]},
            {"id": 8, "title": "Фонарь", "priority": 4, "is_done": True},
        ]
    else:
        rows = [
            {"id": 17, "title": "Сверить склад 100%", "priority": 3,
             "is_done": False, "tags": ["склад"]},
            {"id": 24, "title": "Убрать чехол", "priority": 1, "is_done": True},
        ]
    write_json(folder / SNAPSHOT, rows)
    write_json(folder / LEGACY, [LEGACY_ROW])
    write_json(folder / BASELINE, {
        "source_hash": fingerprint(folder / SNAPSHOT),
        "work_path": str(work_json),
        "work_hash": fingerprint(work_json),
    })

    url = database_url(folder).render_as_string(hide_password=False)
    print("Явный init: python -m app.DBase.init_db", flush=True)
    subprocess.run(
        [sys.executable, "-m", "app.DBase.init_db"],
        cwd=ROOT, env={**os.environ, "DATABASE_URL": url}, check=True,
    )
    require((folder / DATABASE).is_file(), "Init не создал файл SQLite.")
    print("Снимок:", folder / SNAPSHOT)
    print("Файл SQLite:", folder / DATABASE)
    print("DATABASE_URL:", url)


def verify_bytes(folder):
    baseline = read_json(folder / BASELINE)
    require(fingerprint(folder / SNAPSHOT) == baseline["source_hash"], "Снимок изменился.")
    work_json = Path(baseline["work_path"])
    require(fingerprint(work_json) == baseline["work_hash"], "Рабочий JSON изменился.")
    print("Байты снимка совпали.")
    if baseline["work_hash"] is None:
        print("Рабочий JSON отсутствовал в начале и не появился.")
    else:
        print("Байты рабочего JSON совпали.")


def compare(folder, factory):
    verify_bytes(folder)
    expected = {}
    for row in read_json(folder / SNAPSHOT):
        expected[row["id"]] = {
            "title": row["title"], "priority": row["priority"], "is_done": row["is_done"],
            "tags": row.get("tags", []), "title_normalized": row["title"].lower(),
        }

    with factory() as session:
        actual = {
            row.id: {
                "title": row.title, "priority": row.priority, "is_done": row.is_done,
                "tags": row.tags, "title_normalized": row.title_normalized,
            }
            for row in session.scalars(select(TaskRow).order_by(TaskRow.id))
        }

    print(f"Записей в снимке: {len(expected)}, в SQLite: {len(actual)}")
    for task_id in sorted(expected.keys() | actual.keys()):
        print(f"id={task_id} | снимок: {expected.get(task_id)} | SQLite: {actual.get(task_id)}")
        if task_id not in actual:
            print(f"id={task_id}: запись отсутствует в SQLite")
        elif task_id not in expected:
            print(f"id={task_id}: лишняя запись в SQLite")
        else:
            for field, value in expected[task_id].items():
                if actual[task_id][field] != value:
                    print(f"id={task_id}, {field}: значения различаются")
    require(expected == actual, "Сверка не пройдена. Пробные записи остановлены.")
    print("Полная сверка пройдена.")
    return expected


def smoke(folder, factory, initial):
    from fastapi import Depends
    from fastapi.testclient import TestClient

    # Подмена действует внутри отдельного запуска помощника.
    os.environ["JSON_PATH"] = str(folder / LEGACY)
    from app.api import app
    from app.cli import run
    from app.core.dependencies import get_db, get_planner
    from app.main import sql_service_context

    def sql_planner(session=Depends(get_db)):
        from app.DBase.repositories import SqlTaskRepository
        from app.main import build_service

        return build_service(repository=SqlTaskRepository(session), write_scope=session.begin)

    def cli(*commands):
        transcript = []
        answers = iter([*commands, "exit"])
        run(
            read=lambda _prompt: next(answers),
            write=transcript.append,
            context_factory=lambda: sql_service_context(factory),
        )
        return transcript

    db_path = folder / DATABASE
    require(Path(factory.kw["bind"].url.database).resolve() == db_path, "Адрес SQL не совпал.")
    print("API и CLI используют:", db_path)
    try:
        with TestClient(app, base_url="https://testserver", follow_redirects=False) as client:
            default_rows = client.get("/tasks").json()
            require([row["id"] for row in default_rows] == [99], "Штатный API не прочитал учебный JSON.")
            default_cli = subprocess.run(
                [sys.executable, "-m", "app.main"],
                cwd=ROOT, input="list\nexit\n", capture_output=True, encoding="utf-8",
                env={**os.environ, "PYTHONIOENCODING": "utf-8"}, check=True,
            )
            require("[99] " + LEGACY_ROW["title"] in default_cli.stdout,
                    "Штатный CLI не прочитал учебный JSON.")
            print("Штатные API и CLI прочитали контрольный JSON.")

            app.state.session_factory = factory
            app.dependency_overrides[get_planner] = sql_planner
            require(client.get("/tasks/99").status_code == 404, "SQL API увидел задачу из JSON.")
            require("Задача не найдена." in cli("find", "99"), "SQL CLI увидел задачу из JSON.")

            if min(initial) == 2:
                demo_id = min(initial)
                require("Задача выполнена." in cli("done", str(demo_id)), "CLI done не сработал.")
                require(client.get(f"/tasks/{demo_id}").json()["is_done"] is True,
                        "HTTP не увидел запись CLI.")
                print(f"CLI done → HTTP GET: id={demo_id}, is_done=true.")

            title = "Насос" if min(initial) == 2 else "Проверить термос"
            created = client.post("/tasks", json={"title": title, "priority": 3})
            require(created.status_code == 201, "HTTP POST не вернул 201.")
            item = created.json()
            task_id = item["id"]
            require(task_id > 0 and task_id not in initial, "Созданная задача получила занятый id.")
            require(item == {"id": task_id, "title": title, "priority": 3, "is_done": False},
                    "Ответ POST отличается от публичного договора.")
            require(any(line.startswith(f"[{task_id}] {title} |") for line in cli("find", str(task_id))),
                    "CLI find не увидел задачу API.")
            print(f"HTTP POST 201 → CLI find: id={task_id}, {title}.")
            require("Задача выполнена." in cli("done", str(task_id)), "CLI done не сработал.")
            item["is_done"] = True
            require(client.get(f"/tasks/{task_id}").json() == item, "HTTP GET не увидел запись CLI.")
            print(f"CLI done → HTTP GET: id={task_id}, is_done=true.")
            done = sum(row["is_done"] for row in initial.values()) + 1 + (min(initial) == 2)
            total = len(initial) + 1
            stats = client.get("/stats").json()
            require(stats == {"total": total, "open": total - done, "done": done}, "Сводка API расходится.")
            print("HTTP /stats:", stats)

            filtered = client.get("/tasks", params={"is_done": "true", "sort_desc": "true", "limit": 1})
            require([row["id"] for row in filtered.json()] == [task_id], "Фильтр, порядок или limit изменились.")
            tagged_id = min(initial)
            tagged = initial[tagged_id]
            if "%" in tagged["title"]:
                matches = [line for line in cli("search", "%") if line.startswith("[")]
                require(len(matches) == 1 and matches[0].startswith(f"[{tagged_id}] "),
                        "Поиск не сохранил буквальный %.")
            require(any(line.startswith(f"[{task_id}] ") for line in cli("list")), "CLI list потерял задачу.")
            require(f"Всего: {total}" in cli("stats"), "Сводка CLI расходится.")

            target = f"/tasks/{tagged_id}"
            replaced = client.put(target, json={"title": "Проверить отметку", "priority": 2, "is_done": True})
            require(replaced.status_code == 200, "PUT не сохранил задачу.")
            patched = client.patch(target, json={"priority": None, "is_done": False})
            require(patched.status_code == 200 and patched.json()["is_done"] is False
                    and patched.json()["priority"] == 2, "PATCH изменил семантику null или false.")
            with factory() as session:
                row = session.get(TaskRow, tagged_id)
                require(row.tags == tagged["tags"] and row.title_normalized == "проверить отметку",
                        "Теги или служебное название потерялись.")

            deleted = client.delete(f"/tasks/{task_id}")
            require(deleted.status_code == 204 and not deleted.content, "DELETE должен вернуть пустой 204.")
            require(client.get(f"/tasks/{task_id}").status_code == 404, "Удалённая задача всё ещё находится.")
            require(client.post("/tasks", json={"title": "", "priority": 3}).status_code == 422,
                    "Невалидная задача не получила 422.")
            require(client.get("/health").json() == {"status": "ok"}, "Health изменился.")
            require(client.put("/preferences", json={"view": "detailed"}).status_code == 200
                    and client.get("/preferences").json()["view"] == "detailed", "Preferences изменились.")
            print("Прежние маршруты и команды прошли проверку.")
    finally:
        app.dependency_overrides.pop(get_planner, None)
        if hasattr(app.state, "session_factory"):
            del app.state.session_factory
        verify_bytes(folder)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("prepare", "compare", "smoke"))
    parser.add_argument("folder", type=Path)
    parser.add_argument("--demo", action="store_true", help="Данные примера про прокат")
    args = parser.parse_args()
    folder = (ROOT / args.folder).resolve()

    try:
        require(RUNS.resolve() == RUNS and folder.parent == RUNS,
                "Выберите одну папку внутри data/rehearsal.")
        require(not args.demo or args.mode == "prepare", "--demo применяется при подготовке.")
        if args.mode == "prepare":
            prepare(folder, args.demo)
            return
        for name in (DATABASE, SNAPSHOT, BASELINE, LEGACY):
            path = folder / name
            require(path.is_file() and not path.is_symlink(), f"Нет подготовленного файла: {name}")
        engine = make_engine(database_url(folder).render_as_string(hide_password=False))
        try:
            factory = create_session_factory(engine)
            initial = compare(folder, factory)
            if args.mode == "smoke":
                smoke(folder, factory, initial)
        finally:
            engine.dispose()
    except Exception as error:
        parser.exit(1, f"Проверка остановлена: {error}\n")


if __name__ == "__main__":
    main()
`;

const DEMO_COMMANDS = [
  'python rehearse_snapshot.py prepare "data/rehearsal/rental-demo" --demo',
  "python -m app.DBase.import_tasks --help",
  'python -m app.DBase.import_tasks --source "data/rehearsal/rental-demo/snapshot.json" --database "data/rehearsal/rental-demo/planner-test.sqlite3"',
  'python rehearse_snapshot.py compare "data/rehearsal/rental-demo"',
].join("\n");

const DEMO_OUTPUT = [
  "Импортировано задач: 2",
  "",
  "Записей в снимке: 2, в SQLite: 2",
  "Полная сверка пройдена.",
].join("\n");

type RehearsalCase = { sharedFile: boolean; missingTag: boolean };
const INITIAL_CASE: RehearsalCase = { sharedFile: true, missingTag: false };

function RehearsalInteractive() {
  const [choice, setChoice] = useState<RehearsalCase>(INITIAL_CASE);
  const [checked, setChecked] = useState<RehearsalCase | null>(null);

  return (
    <section className="lesson-infographic" aria-label="Проба на ведомости проката">
      <h3>Проба на ведомости проката</h3>
      <p>
        Меняем адрес CLI и тег у записи со шлемом. Перед проверкой решим, можно ли
        переходить к записям и увидит ли CLI новый «Насос». Модель работает на данных
        урока в браузере.
      </p>
      <fieldset style={{ border: 0, margin: 0, padding: 0, minWidth: 0, display: "grid", gap: "0.75rem" }}>
        <legend>Настройки пробного прогона</legend>
        <label style={{ display: "grid", gap: "0.25rem" }}>
          Файл CLI (API использует planner-test.sqlite3)
          <select
            value={choice.sharedFile ? "same" : "other"}
            onChange={(event) => {
              setChoice({ ...choice, sharedFile: event.target.value === "same" });
              setChecked(null);
            }}
          >
            <option value="same">planner-test.sqlite3</option>
            <option value="other">other.sqlite</option>
          </select>
        </label>
        <label>
          <input
            type="checkbox"
            checked={choice.missingTag}
            onChange={(event) => {
              setChoice({ ...choice, missingTag: event.target.checked });
              setChecked(null);
            }}
          />{" "}
          Убрать тег «детский» у записи id 2 в пробной SQLite
        </label>
      </fieldset>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem", marginBlock: "0.75rem" }}>
        <button type="button" onClick={() => setChecked(choice)}>Проверить</button>
        <button
          type="button"
          onClick={() => {
            setChoice(INITIAL_CASE);
            setChecked(null);
          }}
        >
          Сбросить
        </button>
      </div>
      <div aria-live="polite">
        {checked === null ? (
          <p>В снимке две записи. Отчёт появится после проверки.</p>
        ) : checked.missingTag ? (
          <p>
            <strong>Останавливаемся на сверке.</strong> В обоих наборах по две строки.
            У id 2 в поле <code>tags</code> снимок содержит [«детский»], а SQLite
            содержит []. До пробных записей нужно устранить расхождение.
          </p>
        ) : checked.sharedFile ? (
          <p>
            <strong>Снимок совпал. Оба направления работают.</strong> HTTP создал
            «Насос» с id 9, CLI нашёл его в общем файле. CLI завершил запись id 2,
            свежий HTTP GET увидел <code>is_done=true</code>.
          </p>
        ) : (
          <p>
            <strong>Снимок совпал. Адрес CLI отличается.</strong> HTTP сохранил
            «Насос» в <code>planner-test.sqlite3</code>. CLI открывает <code>other.sqlite</code>,
            поэтому новый id там отсутствует. Успех HTTP сам по себе подтверждает запись
            лишь в его файле.
          </p>
        )}
      </div>
    </section>
  );
}

function PracticePreparation() {
  const [copyMessage, setCopyMessage] = useState("");

  async function copyPreparation() {
    try {
      await navigator.clipboard.writeText(PREPARATION);
      setCopyMessage("Оснастка скопирована.");
    } catch {
      setCopyMessage("Можно выделить и скопировать код ниже.");
    }
  }

  return (
    <details>
      <summary>Подготовка к запуску: файл rehearse_snapshot.py</summary>
      <p>
        Сохраним весь блок под именем <code>rehearse_snapshot.py</code> в корне
        своего <code>project/</code>. Используем окружение, в котором запускали тесты
        Planner. Если помощник сообщает, что тестовый клиент отсутствует, из той же
        папки выполним <code>python -m pip install -r requirements-dev.txt</code>.
      </p>
      <button type="button" onClick={copyPreparation}>Копировать оснастку</button>
      <p aria-live="polite">{copyMessage}</p>
      <CodeBlock code={PREPARATION} caption="project/rehearse_snapshot.py" />
    </details>
  );
}

export function Lesson74_1({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "FastAPI, SQLite и SQLAlchemy"}
        title="Репетируем переход на тестовом снимке"
        intro="Проверим перенос учебных задач и убедимся, что API и команды терминала видят изменения друг друга в одной SQLite."
      />

      <Section number="00" title="Зачем репетировать переход">
        <Lead>
          Перед сменой хранилища проверим весь путь на отдельном снимке. Сначала
          посмотрим, что перенеслось, затем проверим работу обоих интерфейсов.
        </Lead>
        <LinkedNotes items={[
          {
            title: "Точка старта",
            description: "У Planner есть пробный SQL-путь для API и CLI. Обычные запуски используют JSON.",
          },
          {
            title: "Новый вопрос",
            description: "Как убедиться, что записи перенеслись целиком и оба интерфейса видят одну базу?",
          },
          {
            title: "Результат",
            description: "На пробной копии сверим данные и прочитаем изменения из обоих интерфейсов.",
          },
        ]} />
        <p>
          Сверим данные сразу после переноса. Тогда каждую строку можно сравнить с
          исходной записью до того, как пробные команды её изменят.
        </p>
      </Section>

      <Section number="01" title="Готовим снимок и отдельную SQLite">
        <Lead>
          Снимок фиксирует набор данных на один момент. Явный init создаёт таблицу
          в выбранной SQLite, а импорт один раз загружает в неё записи снимка.
        </Lead>
        <p>
          Для init и временных сборок нужен <code>DATABASE_URL</code>. Это адрес SQLite,
          например <code>sqlite:///C:/Temp/rental-demo/planner-test.sqlite3</code>.
          Для импортёра нужны два файловых пути. Посмотрим, где каждый из них используется.
        </p>
        <Diagram caption="Пробный и рабочий пути">
          <DiagramFlow label="Импорт снимка на тестовую цель">
            <DiagramNode title="Снимок" shape="database">snapshot.json</DiagramNode>
            <DiagramArrow label="--source" />
            <DiagramNode title="Импортёр">Загружает записи один раз</DiagramNode>
            <DiagramArrow label="--database" />
            <DiagramNode title="Пробная SQLite" shape="database">planner-test.sqlite3</DiagramNode>
          </DiagramFlow>
          <DiagramFlow label="Явное создание схемы">
            <DiagramNode title="Явный init">Создаёт пустую таблицу tasks</DiagramNode>
            <DiagramArrow label="DATABASE_URL" />
            <DiagramNode title="Та же пробная SQLite" shape="database">planner-test.sqlite3</DiagramNode>
          </DiagramFlow>
          <DiagramFlow label="Два временных интерфейса на одном файле">
            <DiagramNode title="Временный API">Session на запрос</DiagramNode>
            <DiagramArrow label="Один URL" direction="both" />
            <DiagramNode title="Та же пробная SQLite" shape="database">planner-test.sqlite3</DiagramNode>
            <DiagramArrow label="Один URL" direction="both" />
            <DiagramNode title="Временный CLI">Session на команду</DiagramNode>
          </DiagramFlow>
          <DiagramFlow label="Обычная работа со своим JSON">
            <DiagramNode title="Обычные API и CLI">Штатные настройки Planner</DiagramNode>
            <DiagramArrow label="Чтение и запись" direction="both" />
            <DiagramNode title="Рабочий JSON" shape="database">Путь из настроек проекта</DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>
          Указанный на схеме абсолютный адрес ведёт к одному файлу из разных папок.
          Импортёру передаём путь к этому файлу без части <code>sqlite:///</code>.
          Тот же файл после загрузки открывают временные API и CLI.
        </p>
        <h3>Запускаемый пример: ведомость проката</h3>
        <p>
          Возьмём ведомость с двумя записями в формате Planner. Шлем с id 2 ещё в
          работе, его тег «детский». Фонарь с id 8 уже отмечен выполненным, в старой
          записи поля <code>tags</code> нет.
        </p>
        <PracticePreparation />
        <p>
          Откроем терминал в <code>project/</code>. Первая команда создаёт свежую
          папку с синтетическим снимком и показывает, как явно запускает
          <code> python -m app.DBase.init_db</code> с временным URL. Если папка уже
          занята, выберем другое имя во всех командах примера.
        </p>
        <CodeBlock code={DEMO_COMMANDS} language="text" caption="Подготовка, импорт и первое чтение" />
        <p>
          <code>-m</code> запускает модуль проекта. <code>--help</code> показывает
          доступные флаги. <code>--source</code> выбирает снимок, а <code>--database</code>{" "}
          указывает на существующий файл с подготовленной таблицей. Кавычки сохраняют
          путь с пробелами как один аргумент. Модуль передаёт эти значения в{" "}
          <code>import_snapshot(source, database)</code> и печатает число записей.
        </p>
        <QuizCard
          question="В новой папке есть JSON. Что нужно сделать, чтобы импортёр смог открыть цель?"
          options={[
            "Указать будущий файл в --database. Импортёр сам создаст таблицу.",
            "Запустить явный init с URL этой базы, затем передать импортёру путь к её файлу.",
            "Подставить DATABASE_URL вместо пути в --source.",
          ]}
          correctIndex={1}
          explanation="Init отвечает за создание таблицы. После него --source указывает на JSON, а --database на существующую SQLite."
        />
      </Section>

      <Section number="02" title="Сверяем каждую запись до пробных изменений">
        <p>
          Число записей показывает размер набора. Чтобы заметить потерянное поле,
          сопоставим строки по <code>id</code> и прочитаем SQL-данные через новую Session.
          Порядок вывода строк может отличаться.
        </p>
        <ul>
          <li>Сравниваем все id и поля <code>title</code>, <code>priority</code>, <code>is_done</code> и <code>tags</code>.</li>
          <li>У старого фонаря ожидаем <code>tags=[]</code>. У шлема тег «детский» сохраняется.</li>
          <li>Служебное поле <code>title_normalized</code> получается из <code>title.lower()</code>. Для шлема это «шлем».</li>
        </ul>
        <CodeBlock code={DEMO_OUTPUT} language="text" caption="Ключевые строки реального вывода" />
        <p>
          Отчёт <code>compare</code> также показывает значения каждого id. Если в
          SQLite у шлема стоит <code>[]</code>, в обоих наборах всё ещё по две
          строки. В отчёте появится <strong>id 2, tags</strong>, и мы остановимся до
          пробных записей. Контрольная сумма отвечает на отдельный вопрос об
          исходном файле. Помощник запоминает отпечаток байтов до переноса и сравнивает
          его с новым после проверки.
        </p>
        <p>
          Теперь повторим именно команду импорта из примера. Импортёр откажется,
          потому что таблица <code>tasks</code> уже заполнена. Повторный{" "}
          <code>compare</code> снова подтвердит те же строки и неизменные байты. Это
          ожидаемый отказ, который сохраняет данные цели.
        </p>
      </Section>

      <Section number="03" title="Наблюдаем изменения из обоих интерфейсов">
        <Lead>
          После сверки пробуем записывать и читать через разные интерфейсы.
          API открывает Session на запрос, CLI открывает свою на команду.
        </Lead>
        <p>
          Запустим готовый <code>smoke</code>. Так назовём короткую проверку основных
          путей работы. Помощник ещё раз сверит исходные строки, затем проверит
          оба интерфейса на одном абсолютном пути.
        </p>
        <CodeBlock
          code={'python rehearse_snapshot.py smoke "data/rehearsal/rental-demo"'}
          language="text"
          caption="Общий прогон на ведомости проката"
        />
        <p>
          В прокате CLI завершает выдачу id 2, отдельный <code>GET /tasks/2</code>{" "}
          видит <code>is_done=true</code>. Затем <code>POST /tasks</code> создаёт
          «Насос» с приоритетом 3 и возвращает 201 с новым id. Команда{" "}
          <code>find</code> читает этот id и выводит «Насос». На исходных данных
          примера это id 9. Если ответы пришли из отдельных ресурсов, они
          подтверждают, что изменения сохранились в общем файле. Успех{" "}
          <code>GET /health</code> показывает лишь доступность API.
        </p>
        <RehearsalInteractive />
        <p>
          Из отчёта берём фактический адрес, выданный id и прочитанные значения.
          Проверка прежних маршрутов также показывает, что обычная сборка читает
          свой JSON. Для этого используется отдельная контрольная запись.
        </p>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <p>
          Оснастку берём из раздела «Подготовка к запуску» в примере. В своём{" "}
          <code>project/</code> используем свежее имя внутри <code>data/rehearsal/</code>.
          Для задания запускаем <code>prepare</code> без флага <code>--demo</code>.
          Он напечатает пути и оставит пустую таблицу и новый учебный снимок.
        </p>
        <LinkedNotes items={[
          {
            title: "Выберем источник и цель",
            description: "По напечатанным путям составим свою команду импорта и увидим число перенесённых задач.",
          },
          {
            title: "Сверим данные перед записью",
            description: "Посмотрим отчёты compare до и после ожидаемого отказа повторного импорта.",
          },
          {
            title: "Проверим общий адрес",
            description: "По следу smoke запишем, как два интерфейса прочитали изменения, и примем решение о готовности.",
          },
        ]} />
        <p>
          При ошибке пути возвращаемся к схеме. При отличии в данных ищем поле у
          того же id. Для нового полного прогона берём свежую папку. Итог и
          наблюдения сохраняем в <code>result.md</code> внутри папки прогона.
        </p>
      </Section>
    </RichLesson>
  );
}
