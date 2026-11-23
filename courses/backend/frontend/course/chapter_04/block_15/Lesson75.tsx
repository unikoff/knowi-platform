import { useState } from "react";
import { Archive, ArrowRightLeft, ShieldCheck } from "lucide-react";
import {
  Callout,
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

const BLOCK_TITLE = "Этап 4 · Блок 15 · Полный SQL-путь и единое переключение Planner";

const CATALOG_DEMO = String.raw`import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory


rehearsal = [
    [2, "Фонарь", 2, False, ["поход"]],
    [4, "Компас", 1, True, []],
    [7, "Аптечка", 4, False, ["поход", "склад"]],
]
late_item = [11, "Коврик", 3, False, ["зал"]]


def from_archive(row):
    item_id, title, priority, done, tags = row
    return item_id, title, priority, done, json.dumps(tags, ensure_ascii=False), title.lower()


def run(empty):
    with TemporaryDirectory() as directory:
        root = Path(directory)
        live = root / "catalog.json"
        archive = root / "catalog-snapshot.json"
        target = root / "catalog.sqlite3"
        # Клуб закрыл приём запросов после последней записи.
        live.write_text(json.dumps([] if empty else rehearsal + [late_item]), encoding="utf-8")
        archive.write_bytes(live.read_bytes())
        expected = [from_archive(row) for row in json.loads(archive.read_text(encoding="utf-8"))]
        with closing(sqlite3.connect(target)) as db, db:
            db.execute("""
                CREATE TABLE items (
                    id INTEGER PRIMARY KEY, title TEXT, priority INTEGER,
                    done INTEGER, tags TEXT, title_normalized TEXT
                )
            """)
            db.executemany("INSERT INTO items VALUES (?, ?, ?, ?, ?, ?)", expected)
        with closing(sqlite3.connect(target)) as db:
            actual = db.execute("SELECT * FROM items ORDER BY id").fetchall()
        print("Снимок:", [row[0] for row in expected])
        print("Точная сверка:", expected == actual)
        if expected != actual:
            return
        # Каждое действие клуба открывает свой ресурс по одному адресу.
        with closing(sqlite3.connect(target)) as web, web:
            record = web.execute(
                "INSERT INTO items (title, priority, done, tags, title_normalized) VALUES (?, ?, ?, ?, ?)",
                ("Карта", 2, False, "[]", "карта"),
            )
            item_id = record.lastrowid
        with closing(sqlite3.connect(target)) as terminal, terminal:
            title = terminal.execute("SELECT title FROM items WHERE id=?", (item_id,)).fetchone()[0]
            print(f"Терминал читает {item_id}: {title}")
            terminal.execute("UPDATE items SET done=1 WHERE id=?", (item_id,))
        with closing(sqlite3.connect(target)) as restarted_web:
            done = restarted_web.execute("SELECT done FROM items WHERE id=?", (item_id,)).fetchone()[0]
            print("Новое чтение:", bool(done))
        print("Архив не изменился:", archive.read_bytes() == live.read_bytes())


if __name__ == "__main__":
    run("--empty" in sys.argv)`;

const CHECK_CUTOVER = String.raw`import json
import os
import re
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.engine import URL

from app.DBase.models import TaskRow
from app.DBase.session import create_session_factory
from app.core.config import load_settings
from app.core.engine import make_engine


PROJECT = Path(__file__).resolve().parent
DATA = PROJECT / "data"
SEED = [
    {"id": 3, "title": "Сверить фонари", "priority": 2, "is_done": False, "tags": ["прокат"]},
    {"id": 8, "title": "Вернуть кабель", "priority": 1, "is_done": True, "tags": []},
]
FIELDS = ("title", "priority", "is_done", "tags", "title_normalized")


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def checked_path(path):
    resolved = path.resolve()
    require(resolved == path and resolved.is_relative_to(DATA),
            f"Выберите новый обычный каталог внутри {DATA}: {path}")
    return resolved


def digest(data):
    return sha256(data).hexdigest()


def sqlite_url(file):
    return URL.create("sqlite", database=file.as_posix()).render_as_string(hide_password=False)


def print_setting(name, value):
    escaped = str(value).replace("'", "''")
    print(f"$env:{name} = '{escaped}'")


def save_json(path, value):
    with path.open("x", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)


def run_paths(run):
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", run),
            "Имя прогона: 1–40 латинских букв, цифр, дефисов или подчёркиваний.")
    source = checked_path(DATA / "cutover-input" / run / "tasks.json")
    work = checked_path(DATA / "cutover" / run)
    return source, work


def seed(source):
    source.parent.mkdir(parents=True, exist_ok=False)
    save_json(source, SEED)
    checked_path(source.with_name("training.marker")).touch(exist_ok=False)
    print("Учебный JSON:", source)
    print_setting("JSON_PATH", source)


def confirm_source(source):
    actual = load_settings().json_path.resolve()
    print("В настройках:", actual)
    print("Ожидаемый учебный файл:", source)
    marker = checked_path(source.with_name("training.marker"))
    require(actual == source and marker.is_file() and source.is_file(),
            "Выберите в текущем терминале JSON_PATH из вывода seed.")


def db_rows(file):
    require(file.is_file(), f"Сначала подготовьте SQLite: {file}")
    engine = make_engine(sqlite_url(file))
    try:
        with create_session_factory(engine)() as session:
            rows = session.scalars(select(TaskRow).order_by(TaskRow.id)).all()
            return {row.id: {field: getattr(row, field) for field in FIELDS} for row in rows}
    finally:
        engine.dispose()


def prepare(source, work):
    confirm_source(source)
    data = source.read_bytes()
    records = json.loads(data.decode("utf-8"))
    require(isinstance(records, list), "В учебном JSON ожидается список задач.")
    work.mkdir(parents=True, exist_ok=False)
    snapshot = checked_path(work / "snapshot.json")
    with snapshot.open("xb") as file:
        file.write(data)
    stamp = digest(data)
    require(digest(source.read_bytes()) == digest(snapshot.read_bytes()) == stamp,
            "Файл изменился. Дождитесь завершения обоих учебных процессов.")
    target = checked_path(work / "planner.sqlite3")
    url = sqlite_url(target)
    print("Учебные задачи в снимке:", [(row["id"], row["title"]) for row in records])
    print("SHA256 источника и снимка:", stamp)
    print("Создание схемы:", "python -m app.DBase.init_db", flush=True)
    subprocess.run(
        [sys.executable, "-m", "app.DBase.init_db"],
        cwd=PROJECT, env={**os.environ, "DATABASE_URL": url}, check=True,
    )
    require(not db_rows(target), "Таблица tasks должна быть пустой.")
    save_json(checked_path(work / "manifest.json"), {"sha256": stamp, "url": url})
    print("Пустая tasks:", target)
    print(f'python -m app.DBase.import_tasks --source "{snapshot}" --database "{target}"')
    print_setting("DATABASE_URL", url)


def compare(source, work):
    confirm_source(source)
    manifest = json.loads(checked_path(work / "manifest.json").read_text(encoding="utf-8"))
    snapshot = checked_path(work / "snapshot.json")
    archive = snapshot.read_bytes()
    require(digest(source.read_bytes()) == digest(archive) == manifest["sha256"],
            "Байты исходника или архива изменились после prepare.")
    expected = {}
    for row in json.loads(archive.decode("utf-8")):
        expected[row["id"]] = {
            "title": row["title"], "priority": row["priority"], "is_done": row["is_done"],
            "tags": row.get("tags", []), "title_normalized": row["title"].lower(),
        }
    target = checked_path(work / "planner.sqlite3")
    require(manifest["url"] == sqlite_url(target), "Адрес цели изменился.")
    actual = db_rows(target)
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    print(f"Снимок: {len(expected)}; SQLite: {len(actual)}.")
    print("Пропущенные id:", missing, "| лишние id:", extra)
    differences = []
    for task_id in sorted(expected.keys() & actual.keys()):
        for field in FIELDS:
            old = expected[task_id][field]
            new = actual[task_id][field]
            if old != new:
                differences.append((task_id, field, old, new))
    for task_id, field, old, new in differences:
        print(f"id={task_id}, {field}: ожидалось {old!r}, получено {new!r}")
    require(not (missing or extra or differences), "Данные расходятся. Входы пока не переключайте.")
    for task_id, fields in sorted(actual.items()):
        print(f"id={task_id}:", fields)
    print("Данные и отпечатки совпали:", manifest["sha256"])
    return manifest, actual


@contextmanager
def local_api(env):
    with tempfile.TemporaryDirectory() as directory:
        trace = Path(directory) / "uvicorn.log"
        with trace.open("wb") as log:
            with subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "app.api:app",
                 "--host", "127.0.0.1", "--port", "0"],
                cwd=PROJECT, env=env, stdout=log, stderr=subprocess.STDOUT,
            ) as process:
                try:
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        message = trace.read_text(encoding="utf-8", errors="replace")
                        match = re.search(r"Uvicorn running on (http://127\.0\.0\.1:\d+)", message)
                        if process.poll() is not None:
                            raise RuntimeError("Учебный API завершился:\n" + message)
                        if match:
                            yield match.group(1)
                            return
                        time.sleep(0.1)
                    raise RuntimeError("Учебный API не запустился за 15 секунд:\n" + message)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()


def http(base, route, expected=200, payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(base + route, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=5) as response:
            status, body = response.status, response.read()
    except HTTPError as error:
        status, body = error.code, error.read()
    require(status == expected, f"{route}: ожидался HTTP {expected}, получен {status}.")
    return json.loads(body)


def cli(env, *commands):
    result = subprocess.run(
        [sys.executable, "-m", "app.main"], input="\n".join((*commands, "exit", "")),
        text=True, encoding="utf-8", capture_output=True, cwd=PROJECT, env=env, timeout=15,
    )
    require(result.returncode == 0, "CLI завершился с ошибкой:\n" + result.stderr)
    return result.stdout


def smoke(source, work):
    manifest, rows = compare(source, work)
    require(load_settings().database_url == manifest["url"],
            "Задайте DATABASE_URL из вывода prepare в этом терминале.")
    probe = checked_path(work / "legacy-probe.json")
    probe_id = next(task_id for task_id in range(901, 901 + len(rows) + 1) if task_id not in rows)
    probe_rows = [{"id": probe_id, "title": "Контрольный JSON", "priority": 1, "is_done": False}]
    if probe.exists():
        require(json.loads(probe.read_text(encoding="utf-8")) == probe_rows,
                "Изменился контрольный JSON. Сохраните отладочный прогон.")
    else:
        save_json(probe, probe_rows)
    snapshot = checked_path(work / "snapshot.json")
    originals = {path: digest(path.read_bytes()) for path in (source, snapshot, probe)}
    env = {**os.environ, "DATABASE_URL": manifest["url"], "JSON_PATH": str(probe),
           "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        with local_api(env) as base:
            print("Штатный API должен вернуть 404 для задачи из контрольного JSON.", flush=True)
            http(base, f"/tasks/{probe_id}", expected=404)
            cli_probe = cli(env, "find", str(probe_id))
            require("Задача не найдена." in cli_probe, "CLI читает контрольный JSON или не работает.")
            if rows:
                task_id = min(rows)
                title = rows[task_id]["title"]
                require(http(base, f"/tasks/{task_id}")["title"] == title,
                        "API не видит импортированную задачу.")
                listing = cli(env, "find", str(task_id))
                require(f"[{task_id}] {title} | приоритет: {rows[task_id]['priority']}" in listing,
                        "CLI не видит импортированную задачу.")
            else:
                require(http(base, "/tasks") == [], "API должен читать пустую таблицу.")
                require("Список задач пуст." in cli(env, "list"), "CLI должен читать пустую таблицу.")

            start = http(base, "/stats")
            title = "Проверка общего источника"
            print("Начинаются новые записи в учебную SQLite.", flush=True)
            created = http(base, "/tasks", expected=201, payload={"title": title, "priority": 3})
            task_id = created["id"]
            require(type(task_id) is int and task_id > 0 and task_id not in rows,
                    f"Неподходящий новый id: {task_id!r}.")
            require(created["is_done"] is False, "Новая задача должна быть открытой.")
            print(f"API сохранил открытую задачу id={task_id}.")
            listing = cli(env, "find", str(task_id))
            require(f"[{task_id}] {title} | приоритет: 3" in listing,
                    "CLI не прочитал задачу после API POST.")
            print("CLI прочитал:", listing.split(f"[{task_id}]", 1)[1].splitlines()[0].strip())
            cli(env, "done", str(task_id))
            require(http(base, f"/tasks/{task_id}")["is_done"] is True,
                    "API не видит результат CLI done.")
            print("Новый HTTP GET:", {"id": task_id, "is_done": True})
            expected = {"total": start["total"] + 1, "open": start["open"], "done": start["done"] + 1}
            require(http(base, "/stats") == expected, "Сводка не отражает одно добавление и завершение.")
            print("HTTP /stats:", expected)
        with local_api(env) as base:
            require(http(base, f"/tasks/{task_id}")["is_done"] is True,
                    "Новый процесс API не видит завершённую задачу.")
        print(f"После перезапуска API id={task_id}, is_done=True. Общий URL:", manifest["url"])
    finally:
        for path, before in originals.items():
            require(digest(path.read_bytes()) == before, f"Изменился JSON: {path}")
            print(f"JSON не изменился: {path} · SHA256 {before}")


def main():
    require(len(sys.argv) == 3, "Запуск: python check_cutover.py seed|source|prepare|compare|smoke run-a")
    action, run = sys.argv[1:]
    source, work = run_paths(run)
    if action == "seed":
        seed(source)
    elif action == "source":
        confirm_source(source)
    elif action == "prepare":
        prepare(source, work)
    elif action == "compare":
        compare(source, work)
    elif action == "smoke":
        smoke(source, work)
    else:
        raise RuntimeError("Выберите seed, source, prepare, compare или smoke.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        sys.exit(f"Проверка остановлена: {error}")`;

const CUTOVER_WIRING = String.raw`from app.DBase.repositories import SqlTaskRepository
from app.DBase.session import create_session_factory
from app.core.engine import make_engine
from app.service import PlannerService


def make_session_factory(database_url):
    return create_session_factory(make_engine(database_url))


def make_http_planner(session):
    return PlannerService(
        repository=SqlTaskRepository(session), write_scope=session.begin
    )


def make_cli_context(session_factory, service_context):
    def open_context():
        return service_context(session_factory)

    return open_context`;

const DEMO_OUTPUT = "Снимок: [2, 4, 7, 11]\nТочная сверка: True\nТерминал читает 12: Карта\nНовое чтение: True\nАрхив не изменился: True";

const BINDINGS = [
  { id: "json", label: "Оба на JSON", api: "JSON", cli: "JSON" },
  { id: "api", label: "Только API на SQLite", api: "SQLite", cli: "JSON" },
  { id: "cli", label: "Только CLI на SQLite", api: "JSON", cli: "SQLite" },
  { id: "sqlite", label: "Оба на SQLite", api: "SQLite", cli: "SQLite" },
] as const;

function CutoverTrial() {
  const [snapshot, setSnapshot] = useState("old");
  const [binding, setBinding] = useState("api");
  const [checked, setChecked] = useState(false);
  const selected = BINDINGS.find((item) => item.id === binding) ?? BINDINGS[1];
  const fresh = snapshot === "fresh";
  const common = selected.api === selected.cli;

  function reset() {
    setSnapshot("old");
    setBinding("api");
    setChecked(false);
  }

  return (
    <form
      className="quiz-card"
      style={{ width: "100%", maxWidth: "100%", boxSizing: "border-box" }}
      aria-label="Проба переключения"
      onSubmit={(event) => {
        event.preventDefault();
        setChecked(true);
      }}
    >
      <h3>Куда попадёт следующая запись?</h3>
      <p>В репетиции было три вещи. Перед остановкой терминал добавил коврик с id 11. Выберите снимок и настройку двух входов. Предскажите, попадёт ли коврик в перенос и увидит ли терминал новую запись сайта.</p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.75rem" }}>
        <label style={{ display: "grid", gap: "0.35rem", minWidth: 0 }}>
          Снимок
          <select value={snapshot} onChange={(event) => {
            setSnapshot(event.currentTarget.value);
            setChecked(false);
          }}>
            <option value="old">Ранний, до коврика</option>
            <option value="fresh">Свежий, после остановки</option>
          </select>
        </label>
        <label style={{ display: "grid", gap: "0.35rem", minWidth: 0 }}>
          Штатные входы
          <select value={binding} onChange={(event) => {
            setBinding(event.currentTarget.value);
            setChecked(false);
          }}>
            {BINDINGS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
          </select>
        </label>
      </div>
      <div className="interactive-actions">
        <button className="interactive-primary" type="submit">Проверить прогноз</button>
        <button className="interactive-secondary" type="button" onClick={reset}>Сбросить</button>
      </div>
      <div role="status" aria-live="polite" aria-atomic="true">
        {checked && (
          fresh ? (
            <>
              <p><strong>Сверка прошла:</strong> в снимке есть id 11.</p>
              <ul>
                <li>JSON: {selected.api === "JSON" ? "сайт запишет сюда id 12" : "сохранены четыре исходные записи"}.</li>
                <li>SQLite: {selected.api === "SQLite" ? "сайт запишет сюда id 12" : "сохранены четыре импортированные записи"}.</li>
                <li>Терминал: {common ? "увидит id 12" : "не увидит id 12"}, потому что читает {selected.cli}.</li>
              </ul>
              <p>{common && selected.api === "SQLite"
                ? "Новый запрос к сайту прочитает и смену статуса из терминала: оба действия смотрят в один SQLite-файл."
                : common
                  ? "Обмен работает через прежний JSON. Подготовленная SQLite ещё не стала штатной."
                  : "Интерфейсы смотрят в разные файлы. Успешная запись в одном не появится в другом."}</p>
            </>
          ) : (
            <p><strong>Остановка на сверке.</strong> В архиве нет id 11. Пока актуальные данные не перенесены, обычные входы не переключаем и новой записи в модели нет.</p>
          )
        )}
      </div>
    </form>
  );
}

function CopyableFile({ caption, code }: { caption: string; code: string }) {
  const [feedback, setFeedback] = useState("");

  async function copy() {
    try {
      await navigator.clipboard.writeText(code + "\n");
      setFeedback("Скопировано.");
    } catch {
      setFeedback("Выделите код в блоке и скопируйте вручную.");
    }
  }

  return (
    <details>
      <summary>{caption}</summary>
      <div className="interactive-actions">
        <button
          type="button"
          className="interactive-secondary"
          onClick={copy}
          aria-label={"Копировать " + caption}
        >
          Копировать
        </button>
        <span role="status">{feedback}</span>
      </div>
      <CodeBlock caption={caption} code={code} />
    </details>
  );
}

export function Lesson75({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title="Переводим API и CLI на одну SQLite"
        intro="Сохраним последние задачи и сменим хранилище так, чтобы оба обычных входа продолжили работу с одним набором данных."
        tags={[
          { icon: <Archive size={14} />, label: "Актуальный снимок" },
          { icon: <ShieldCheck size={14} />, label: "Полная сверка" },
          { icon: <ArrowRightLeft size={14} />, label: "Одна SQLite" },
        ]}
      />

      <Section number="00" title="Вступление">
        <Lead>Репетиция показала, что импорт и временные SQL-сборки работают. Пока мы проверяли тестовый снимок, задачи в обычном учебном Planner могли измениться.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Тестовый перенос и чтение в обоих направлениях прошли. Штатные API и CLI всё ещё пишут в JSON." },
          { title: "Новый вопрос", description: "Когда можно вместе перевести оба интерфейса и сохранить все последние задачи?" },
          { title: "Результат", description: "Сверим свежий снимок, включим общую SQLite и прочитаем новую задачу после перезапуска." },
        ]} />
        <p>Сначала фиксируем момент, на котором JSON перестал меняться. Тогда можно проверить, что SQLite содержит ровно этот набор, и открыть оба входа на одном адресе.</p>
      </Section>

      <Section number="01" title="Как получить актуальный снимок">
        <p>Представим учебный каталог клуба. В репетиции было три вещи с id 2, 4 и 7. Позже терминал добавил коврик с id 11. Если взять старую копию, импорт может пройти успешно, а коврик так и не окажется в новой базе.</p>
        <p>Свежий снимок делаем после остановки всех записей. Дожидаемся завершения учебных запросов и команд. В своём терминале закрываем CLI через <code>exit</code>, останавливаем учебный API через <code>Ctrl+C</code> и убеждаемся, что оба терминала вернулись к приглашению. С этого момента прежний файл остаётся неизменным до сверки.</p>
        <p>В работе используем отдельную локальную учебную копию Planner с вымышленными задачами. Готовая команда создаст новый JSON и напечатает строку наподобие <code>{"$env:JSON_PATH = 'C:/Temp/club-demo/catalog.json'"}</code>. Она задаёт путь для процессов, запущенных из этого PowerShell. Перед запуском API и CLI строку вводим в обоих учебных терминалах.</p>
        <p>Перед остановкой добавляем одну вымышленную задачу обычной командой <code>add</code> и читаем её в API по выданному id. Этот id ищем в напечатанном списке свежего архива. Когда пути различаются, помощник останавливается до чтения другого файла.</p>
        <Diagram caption="Пауза в записях и первая запись в SQLite" orientation="vertical">
          <DiagramFlow label="От последней файловой записи до первой новой записи в SQLite" orientation="vertical">
            <DiagramNode title="В JSON добавлен коврик" shape="terminator"><p>Учебный API прочитал id 11.</p></DiagramNode>
            <DiagramArrow label="остановили оба входа" />
            <DiagramNode title="Пауза в записях"><p>Сохраняем снимок и сверяем импорт до запуска.</p></DiagramNode>
            <DiagramArrow label="полное совпадение" />
            <DiagramNode title="Оба входа на общей SQLite" shape="database"><p>Запускаем их с одним адресом.</p></DiagramNode>
            <DiagramArrow label="новая запись сохранена" />
            <DiagramNode title="JSON остаётся архивом" shape="terminator"><p>Новая задача есть в SQLite.</p></DiagramNode>
          </DiagramFlow>
        </Diagram>
      </Section>

      <Section number="02" title="Почему нужно сверять все поля">
        <p>Init создаёт пустую таблицу по <code>DATABASE_URL</code>. Импортёру передаём два файловых пути: <code>--source</code> к архиву JSON и <code>--database</code> к созданному SQLite-файлу. Кавычки сохраняют путь с пробелами одним аргументом. Импортёр проверяет данные, переносит их одной транзакцией и отклоняет непустую цель.</p>
        <CodeBlock
          language="text"
          caption="Форма команды импорта из project/ после prepare"
          code={'python -m app.DBase.import_tasks --source "data/cutover/run-a/snapshot.json" --database "data/cutover/run-a/planner.sqlite3"'}
        />
        <p>В своём запуске возьмите полные пути из вывода подготовки. Отдельный пример клуба ниже использует свою маленькую таблицу и запускается одной командой.</p>
        <p>Хеш помогает сверить байты исходника и архива. Для SQLite нужен другой способ: заново открыть базу, сравнить все id, а у каждого id проверить название, приоритет, статус и теги. Старой записи без <code>tags</code> соответствует пустой список. Служебное имя для поиска сравниваем с <code>title.lower()</code>.</p>
        <CodeBlock language="text" caption="Четыре строки с обеих сторон. Можно ли продолжать?" code={"Снимок: 4; SQLite: 4.\nПропущенные id: [] | лишние id: []\nid=11, tags: ожидалось ['зал'], получено []"} />
        <p>Тег коврика потерялся. Оставляем обе сборки на прежнем источнике и смотрим, какой файл и какая строка участвовали в сравнении. Для повторного переноса выбираем новую пустую цель. Совпадение числа строк этого решения не меняет.</p>
        <h3>Один запускаемый пример</h3>
        <p>Сохраните готовый блок как <code>catalog_demo.py</code> в свободной учебной папке и выполните <code>python catalog_demo.py</code>. Он сам создаёт временные файлы с данными клуба. Старый набор из трёх вещей остаётся в примере для сравнения. В архив попадают четыре вещи, включая коврик.</p>
        <p>Пример открывает реальную SQLite через стандартный <code>sqlite3</code>. Разные открытия файла условно представляют сайт, терминал и новый процесс. Каждый блок записи завершает транзакцию при выходе из <code>with</code>. В Planner ту же границу обеспечивают уже знакомые Session и область записи сервиса.</p>
        <CopyableFile caption="catalog_demo.py" code={CATALOG_DEMO} />
        <CodeBlock language="text" caption="Вывод python catalog_demo.py" code={DEMO_OUTPUT} />
        <p>Сайт добавил карту с id 12. Терминал увидел эту строку из другого соединения и завершил её. Свежий запрос снова открыл файл и прочитал <code>True</code>. Для отдельного пустого случая запустите <code>python catalog_demo.py --empty</code>. Сверка пустых наборов проходит, первая новая вещь получает id 1.</p>
      </Section>

      <Section number="03" title="Как открыть одну базу из двух штатных входов">
        <p>В старой обычной сборке каталог клуба берёт файловый сервис. При переходе сайт отдаёт запросу SQL-сервис, а терминал открывает такой сервис для каждой распознанной команды. Каждая сборка делает собственную фабрику, которая умеет открыть новый ресурс к одному SQLite-файлу. Сама Session живёт только на время запроса или команды.</p>
        <p>Если сменить только сайт, сайт запишет новую карту в SQLite, а терминал будет искать её в JSON. Если оба входа смотрят в одну SQLite, терминал прочитает карту и результат его записи вернётся в новом HTTP-запросе.</p>
        <CutoverTrial />
        <p>До первой новой SQL-записи можно остановиться, сохранить архив и восстановить оба прежних запуска на учебном JSON. После новой SQL-записи архив уже отстал. Остановите учебные входы и сохраните SQLite, если нужна отдельная процедура восстановления. Возврат к старому файлу оставил бы новые данные за пределами рабочего источника.</p>
        <h3>Где менять источник в Planner</h3>
        <ul>
          <li><code>app/api.py</code> собирает приложение. В нём храним фабрику ресурса на <code>app.state</code>. Следующему HTTP-запросу нужна своя Session.</li>
          <li><code>app/core/dependencies.py</code> связывает маршруты с <code>get_planner</code>. <code>get_db</code> открывает и закрывает Session запроса. Зависимость вида <code>Depends(имя_поставщика)</code> получает результат вызова указанного поставщика во время запроса. Имя поставщика должно быть определено к моменту объявления зависимости.</li>
          <li><code>app/main.py</code> запускает знакомый CLI. Параметр <code>context_factory</code> получает функцию без аргументов. При распознанной команде цикл вызывает её и входит в выданный контекст. Так один запуск CLI пользуется отдельной Session на каждую команду.</li>
        </ul>
        <p>Обе сборки берут адрес из <code>load_settings().database_url</code>. Скопируйте напечатанную строку вида <code>{"$env:DATABASE_URL = 'sqlite:///C:/Temp/club-demo/catalog.sqlite3'"}</code> перед проверкой. Все дочерние процессы получат это значение. Дальше задавайте тот же URL перед обычным запуском в новом терминале.</p>
        <p>Оснастка для переключения готовит фабрику по адресу, сервис запроса по Session и функцию для командного контекста. У командной обвязки второй аргумент <code>service_context</code> принимает имя существующей функции <code>sql_service_context</code>. Самостоятельно решите, где вызывать эти помощники в обычных входах.</p>
        <h3 id="lesson75-materials">Готовые материалы</h3>
        <p>Откройте блоки и сохраните их в своей отдельной учебной копии. Из корня её <code>project/</code> работают все команды ниже. Если имя файла занято, сохраните оснастку под свободным именем с суффиксом и используйте это имя в командах или импорте.</p>
        <CopyableFile caption="project/check_cutover.py" code={CHECK_CUTOVER} />
        <CopyableFile caption="project/app/cutover_wiring.py" code={CUTOVER_WIRING} />
        <ol>
          <li><code>python check_cutover.py seed run-a</code> создаёт новый <code>data/cutover-input/run-a/tasks.json</code> с фонарями и кабелем. Выберите одно свободное имя из латинских букв, цифр, дефисов или подчёркиваний. Используйте то же имя во всех командах.</li>
          <li>Скопируйте строку <code>JSON_PATH</code> из вывода в оба учебных PowerShell. В каждом выполните <code>python check_cutover.py source run-a</code>. При совпавших путях запустите в одном терминале <code>python -m uvicorn app.api:app --host 127.0.0.1 --port 0</code>, в другом <code>python -m app.main</code>. Порт 0 просит операционную систему выбрать свободный порт. Возьмите напечатанный адрес API.</li>
          <li>В CLI введите <code>add</code>, «Сверить карту склада» и приоритет 3. Запишите id. Откройте <code>/tasks/</code> с этим id на напечатанном адресе API и проверьте название. Завершите CLI и API как в разделе о снимке.</li>
          <li><code>python check_cutover.py prepare run-a</code> после остановки показывает записи архива, отпечаток JSON и готовую команду импорта. Убедитесь, что контрольный id есть. Выполните напечатанную команду импорта и <code>python check_cutover.py compare run-a</code>. Продолжайте только после полного совпадения.</li>
          <li>Сохраните прежний вариант изменяемых строк в черновике редактора. Переключите обе обычные сборки и установите напечатанный <code>DATABASE_URL</code>. Затем <code>python check_cutover.py smoke run-a</code> откроет реальные обычные входы на выбранной базе и проверит запись, повторное чтение и перезапуск.</li>
        </ol>
        <p>Smoke даёт своим процессам защитный путь к отдельному JSON с контрольной строкой. Если вход всё ещё читает JSON, это видно до новых записей. Когда SQLite работает, отчёт показывает фактический id новой задачи, чтение из CLI, итоговый статус из API, сводку и SHA256 всех трёх JSON. После успешного прогона сохраните <code>planner.sqlite3</code> и наблюдения в <code>data/cutover/run-a/result.md</code>.</p>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <Lead>Продолжим в своём учебном Planner. Повторим знакомые подготовку и сверку, затем сами соединим штатные входы с одной базой.</Lead>
        <LinkedNotes items={[
          { title: "Проверим свежий архив", description: "После остановки увидим в снимке id последней учебной задачи и совпавший отпечаток исходника." },
          { title: "Переведём оба входа", description: "После полной сверки направим API и CLI на один абсолютный URL." },
          { title: "Прочитаем после перезапуска", description: "Найдём фактический id и статус из нового API-процесса и подтвердим неизменность JSON." },
        ]} />
        <p>Если расходятся поля, вернитесь к сравнению записей с одинаковым id. Если пропала запись между интерфейсами, проследите, какой ресурс получает штатный запрос и какой контекст открывает обычная команда.</p>
      </Section>
    </RichLesson>
  );
}
