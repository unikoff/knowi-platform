import { useState } from "react";
import {
  CodeBlock,
  Diagram,
  DiagramArrow,
  DiagramFlow,
  DiagramNode,
  DiagramNote,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const BLOCK_TITLE = "Этап 4 · Блок 16 · Надёжность и приёмка Planner";
const wrapPath = { overflowWrap: "anywhere", whiteSpace: "normal" } as const;

const WAREHOUSE_DEMO = String.raw`import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen


def summary(db):
    total, closed = db.execute(
        "SELECT count(*), coalesce(sum(state = 'closed'), 0) FROM stock"
    ).fetchone()
    return {"total": total, "open": total - closed, "closed": closed}


def find(db, item_id):
    row = db.execute(
        "SELECT id, name, state FROM stock WHERE id = ?", (item_id,)
    ).fetchone()
    return dict(zip(("id", "name", "state"), row)) if row else None


def serve(database):
    class StockAPI(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, {"status": "ok"})
                return
            with closing(sqlite3.connect(database)) as db:
                if self.path == "/summary":
                    self.reply(200, summary(db))
                elif self.path.startswith("/stock/") and self.path[7:].isdigit():
                    row = find(db, int(self.path[7:]))
                    self.reply(200 if row else 404, row)
                else:
                    self.reply(404, None)

        def do_POST(self):
            if self.path != "/stock":
                self.reply(404, None)
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            with closing(sqlite3.connect(database)) as db:
                cursor = db.execute(
                    "INSERT INTO stock (name, state) VALUES (?, 'open')",
                    (payload["name"],),
                )
                db.commit()
                self.reply(201, find(db, cursor.lastrowid))

    with HTTPServer(("127.0.0.1", 0), StockAPI) as server:
        print(server.server_port, flush=True)
        server.serve_forever()


def console(database):
    for line in sys.stdin:
        action, *args = line.split()
        with closing(sqlite3.connect(database)) as db:
            if action == "lookup":
                row = find(db, int(args[0]))
                print("Найдено:", row or "записи нет")
            elif action == "close":
                db.execute("UPDATE stock SET state = 'closed' WHERE id = ?", (int(args[0]),))
                db.commit()
            elif action == "summary":
                print("Сводка CLI:", summary(db))


def child(mode, database):
    return [sys.executable, str(Path(__file__).resolve()), mode, str(database)]


@contextmanager
def started_api(database, name):
    with subprocess.Popen(child("api", database), stdout=subprocess.PIPE, text=True) as proc:
        try:
            port = int(proc.stdout.readline())
            print(f"API {name}: PID={proc.pid}, файл={database.name}")
            yield f"http://127.0.0.1:{port}"
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait()


def request(base, path, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(base + path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=3) as response:
        value = json.load(response)
        method = "POST" if payload is not None else "GET"
        print(f"{method} {path} → {response.status}", value)
        return value


def demo(use_other):
    with TemporaryDirectory(prefix="warehouse-") as directory:
        shared = Path(directory) / "warehouse.sqlite3"
        other = Path(directory) / "warehouse-other.sqlite3"
        for database in (shared, other):
            with closing(sqlite3.connect(database)) as db:
                db.execute("CREATE TABLE stock (id INTEGER PRIMARY KEY, name TEXT, state TEXT)")
                db.commit()

        with started_api(shared, "A") as base:
            request(base, "/health")
            request(base, "/summary")
            item_id = request(base, "/stock", {"name": "Набор"})["id"]
            cli_database = other if use_other else shared
            commands = f"lookup {item_id}\n"
            if not use_other:
                commands += f"close {item_id}\nsummary\n"
            cli = subprocess.run(
                child("cli", cli_database),
                input=commands.encode("utf-8"),
                stdout=subprocess.PIPE,
                env={**os.environ, "PYTHONUTF8": "1"},
                check=True,
                timeout=5,
            )
            print(f"CLI B: файл={cli_database.name}")
            print(cli.stdout.decode("utf-8"), end="")

        print("API A завершён")
        if not use_other:
            with started_api(shared, "C") as base:
                request(base, f"/stock/{item_id}")
                request(base, "/summary")


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["api", database]:
            serve(database)
        case ["cli", database]:
            console(database)
        case [] | ["--other"]:
            demo("--other" in sys.argv)
        case _:
            sys.exit("Команда: python warehouse_trace.py [--other]")
`;
const ACCEPTANCE_LAUNCHER = String.raw`import argparse
import ast
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen

from sqlalchemy.engine import URL


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = "tests/test_planner_acceptance_contract.py"
DEFAULT_ROLLBACK = "tests/test_write_recovery.py"
PROJECT_CHECKS = (
    ("отказ хранилища", "tests/test_storage_availability.py"),
    ("откат записи", DEFAULT_ROLLBACK),
    ("наблюдение SQL", "tests/test_sql_selection_observation.py"),
    ("договор выборки", "tests/test_sql_selection_guard.py"),
)


def environment(database):
    url = URL.create("sqlite", database=database.as_posix()).render_as_string(
        hide_password=False
    )
    return {**os.environ, "DATABASE_URL": url, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"}


def command(*args):
    return [sys.executable, "-m", *args]


def rollback_test_path(value):
    tests_dir = (ROOT / "tests").resolve()
    candidate = (ROOT / value).resolve()
    if (
        not tests_dir.is_relative_to(ROOT)
        or not candidate.is_relative_to(tests_dir)
        or candidate.suffix != ".py"
        or not candidate.name.startswith("test_")
    ):
        raise RuntimeError("Путь теста отката должен вести к test_*.py внутри tests/.")
    return candidate.relative_to(ROOT).as_posix()


def has_own_rollback_test(filename):
    try:
        tree = ast.parse((ROOT / filename).read_text(encoding="utf-8-sig"), filename=filename)
    except (OSError, UnicodeError, SyntaxError) as error:
        raise RuntimeError(f"Проверьте файл отката {filename}: {error}") from error
    return any(
        isinstance(node, ast.FunctionDef)
        and node.name.startswith("test_")
        and node.name != "test_post_commit_keeps_row"
        and any(arg.arg == "rollback_sessions" for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs))
        for node in tree.body
    )


def checked_paths(rollback):
    checks = [(name, rollback if path == DEFAULT_ROLLBACK else path) for name, path in PROJECT_CHECKS]
    missing = [f"{name}: {path}" for name, path in checks if not (ROOT / path).is_file()]
    if missing:
        raise RuntimeError("Восстановите свои проверки:\n" + "\n".join(missing))
    if not has_own_rollback_test(rollback):
        raise RuntimeError(f"Добавьте в {rollback} свой тест отката с фикстурой rollback_sessions.")
    return [path for _, path in checks]


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    print(f"API завершён: PID={process.pid}.")


def start(database, port, label, log):
    env = environment(database)
    origin = f"http://127.0.0.1:{port}"
    args = command("uvicorn", "app.api:app", "--host", "127.0.0.1", "--port", str(port))
    with log.open("wb") as output:
        process = subprocess.Popen(args, cwd=ROOT, env=env, stdout=output, stderr=output)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and process.poll() is None:
            # Ждём подтверждения от запущенного нами сервера, затем читаем health.
            if "Uvicorn running on" in log.read_text(encoding="utf-8", errors="replace"):
                try:
                    with urlopen(origin + "/health", timeout=0.4) as response:
                        if response.status == 200 and process.poll() is None:
                            print(f"API {label}: PID={process.pid}, GET /health → 200")
                            print("DATABASE_URL:", env["DATABASE_URL"])
                            print("Swagger:", origin + "/docs")
                            return process
                except (URLError, TimeoutError):
                    pass
            time.sleep(0.08)
        raise RuntimeError("API не поднялся. Журнал запуска:\n" + log.read_text(
            encoding="utf-8", errors="replace"
        ))
    except BaseException:
        stop(process)
        raise


def run_checks(database, project_checks, include_contract):
    targets = list(project_checks)
    if include_contract:
        if not (ROOT / CONTRACT).is_file():
            raise RuntimeError("Сохраните готовый файл " + CONTRACT)
        targets.append(CONTRACT)
    result = subprocess.run(
        command("pytest", "-q", "-p", "no:cacheprovider", *targets),
        cwd=ROOT,
        env=environment(database),
        check=False,
    )
    print("Проверки прошли." if result.returncode == 0 else "Есть сбой проверки.")


def run_cli(database, label):
    env = environment(database)
    print(f"CLI ({label}):", env["DATABASE_URL"], flush=True)
    subprocess.run(command("app.main"), cwd=ROOT, env=env, check=True)


MENU = (
    "\n1. Входные проверки\n"
    "2. Запустить API\n"
    "3. Открыть CLI\n"
    "4. Перезапустить API\n"
    "5. Открыть CLI с другим файлом\n"
    "6. Регрессия\n"
    "0. Завершить\n"
)


def main():
    if not (ROOT / "app" / "api.py").is_file() or not (ROOT / "tests").is_dir():
        raise RuntimeError("Сохраните файл в tools/ своего Planner и запустите из его корня.")
    parser = argparse.ArgumentParser(description="Сквозная проверка своего Planner")
    parser.add_argument("--rollback-test", default=DEFAULT_ROLLBACK, help="Ваш файл test_*.py внутри tests/")
    args = parser.parse_args()
    project_checks = checked_paths(rollback_test_path(args.rollback_test))
    with TemporaryDirectory(prefix="planner-acceptance-") as temp:
        directory = Path(temp)
        shared = directory / "planner.sqlite3"
        other = directory / "planner-other.sqlite3"
        log = directory / "api.log"
        for database in (shared, other):
            subprocess.run(
                command("app.DBase.init_db"), cwd=ROOT, env=environment(database), check=True
            )
        print("Подготовлены две пустые схемы.")
        print("Общий файл:", environment(shared)["DATABASE_URL"])
        print("Другой файл:", environment(other)["DATABASE_URL"])
        port = free_port()
        process = None
        try:
            while True:
                print(MENU)
                choice = input("Действие: ").strip()
                if choice == "0":
                    return
                try:
                    if choice in {"1", "6"}:
                        with TemporaryDirectory(dir=directory) as check_dir:
                            run_checks(Path(check_dir) / "checks.sqlite3", project_checks, choice == "6")
                    elif choice == "2" and process is None:
                        process = start(shared, port, "A", log)
                    elif choice == "3":
                        run_cli(shared, "общий файл")
                    elif choice == "4" and process is not None:
                        stop(process)
                        process = None
                        process = start(shared, port, "C", log)
                    elif choice == "5":
                        run_cli(other, "другой файл")
                    else:
                        print("Выберите действие из меню. Для перезапуска сначала запустите API.")
                except (RuntimeError, subprocess.CalledProcessError) as error:
                    print(error)
        finally:
            if process is not None:
                stop(process)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print("\nПроверка завершена.")
    except (RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
`;
const CONTRACT_REGRESSION = String.raw`from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL

from app.DBase.models import Base, TaskRow
from app.DBase.session import create_session_factory
from app.api import app
from app.core.engine import make_engine


@pytest.fixture
def planner_contract(tmp_path):
    database = tmp_path / "planner-contract.sqlite3"
    url = URL.create("sqlite", database=database.as_posix()).render_as_string(
        hide_password=False
    )
    engine = make_engine(url)
    previous_factory = app.state.session_factory
    try:
        Base.metadata.create_all(engine)
        factory = create_session_factory(engine)
        app.state.session_factory = factory
        with closing(TestClient(app)) as client:
            yield client, factory
    finally:
        app.state.session_factory = previous_factory
        engine.dispose()


def test_current_crud_contract_and_tags(planner_contract):
    client, factory = planner_contract
    created = client.post("/tasks", json={"title": "Проверка договора", "priority": 2})
    assert created.status_code == 201
    payload = created.json()
    assert set(payload) == {"id", "title", "priority", "is_done"}
    assert payload["is_done"] is False
    item = f"/tasks/{payload['id']}"
    assert client.get(item).json() == payload
    assert client.post("/tasks", json={"title": "", "priority": 2}).status_code == 422

    with factory.begin() as db:
        db.get(TaskRow, payload["id"]).tags = ["contract"]

    replaced = client.put(item, json={"title": "Другой заголовок", "priority": 4, "is_done": True})
    assert replaced.status_code == 200
    assert replaced.json() == {
        "id": payload["id"], "title": "Другой заголовок", "priority": 4, "is_done": True
    }
    patched = client.patch(item, json={"is_done": False, "priority": None})
    assert patched.status_code == 200
    assert patched.json() == {**replaced.json(), "is_done": False}
    with factory() as db:
        assert db.get(TaskRow, payload["id"]).tags == ["contract"]

    deleted = client.delete(item)
    assert deleted.status_code == 204
    assert deleted.content == b""
    assert client.get(item).status_code == 404


@pytest.mark.parametrize("headers, view", [({}, "compact"), ({"Cookie": "planner_view=detailed"}, "detailed")])
def test_health_and_view_contract(planner_contract, headers, view):
    client, _ = planner_contract
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    preferences = client.get("/preferences", headers=headers)
    assert preferences.status_code == 200
    assert preferences.json()["view"] == view
`;

const warehouseTrace = `GET /health → 200 {"status": "ok"}
GET /summary → 200 {"total": 0, "open": 0, "closed": 0}
POST /stock → 201 {"id": 1, "name": "Набор", "state": "open"}
CLI B, lookup 1 → {"id": 1, "name": "Набор", "state": "open"}
CLI B, close 1; summary → {"total": 1, "open": 0, "closed": 1}
API A завершён, запущен API C
GET /stock/1 → 200 {"id": 1, "name": "Набор", "state": "closed"}
GET /summary → 200 {"total": 1, "open": 0, "closed": 1}`;

const proofOptions = {
  complete: "Все переходы подтверждены",
  cli: "CLI читает запись, созданную API",
  restart: "Новый API читает изменение",
};

type Proof = keyof typeof proofOptions;
type ApiProcess = "new" | "old";

const evidenceStyles = {
  panel: {
    boxSizing: "border-box",
    width: "100%",
    minWidth: 0,
    margin: "1rem 0",
    padding: "0.5rem 1rem 1rem",
    borderRadius: "0.75rem",
    border: "1px solid var(--line-soft)",
    background: "var(--surface)",
  },
  legend: { padding: "0 0.4rem", fontWeight: 650 },
  group: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 15rem), 1fr))",
    gap: "0.85rem",
    marginBlock: "0.9rem",
  },
  label: { display: "grid", gap: "0.3rem", minWidth: 0 },
  labelText: { fontWeight: 600, fontSize: "0.9rem" },
  select: {
    width: "100%",
    minWidth: 0,
    padding: "0.5rem 0.65rem",
    border: "1px solid var(--line-soft)",
    borderRadius: "0.45rem",
    background: "var(--surface)",
    color: "var(--text)",
    fontSize: "0.95rem",
  },
  actions: { display: "flex", flexWrap: "wrap", gap: "0.55rem", marginTop: "0.85rem" },
  action: {
    padding: "0.45rem 0.7rem",
    border: "1px solid var(--line-soft)",
    borderRadius: "0.45rem",
    fontWeight: 600,
    background: "var(--surface)",
    color: "var(--text)",
  },
  feedback: {
    marginTop: "0.8rem",
    padding: "0.65rem 0.8rem",
    borderInlineStart: "3px solid var(--accent)",
    overflowWrap: "anywhere",
  },
} as const;

function WarehouseEvidence() {
  const [sameFile, setSameFile] = useState(true);
  const [apiProcess, setApiProcess] = useState<ApiProcess>("new");
  const [prediction, setPrediction] = useState("");
  const [revealed, setRevealed] = useState(false);
  const expected: Proof = !sameFile ? "cli" : apiProcess === "old" ? "restart" : "complete";

  function clearPrediction() {
    setPrediction("");
    setRevealed(false);
  }

  function reset() {
    setSameFile(true);
    setApiProcess("new");
    clearPrediction();
  }

  return (
    <fieldset style={evidenceStyles.panel}>
      <legend style={evidenceStyles.legend}>Где прервётся доказательство?</legend>
      <p>Модель склада. API A уже записал «Набор» под ID 1 в warehouse.sqlite3. Выберем адрес CLI и запуск API для финального чтения.</p>
      <div style={evidenceStyles.group}>
        <label style={evidenceStyles.label}>
          <span style={evidenceStyles.labelText}>Файл для CLI</span>
          <select
            style={evidenceStyles.select}
            value={sameFile ? "shared" : "other"}
            onChange={(event) => {
              setSameFile(event.target.value === "shared");
              clearPrediction();
            }}
          >
            <option value="shared">Общий файл</option>
            <option value="other">Другой пустой файл</option>
          </select>
        </label>
        <label style={evidenceStyles.label}>
          <span style={evidenceStyles.labelText}>API для последнего чтения</span>
          <select
            style={evidenceStyles.select}
            value={apiProcess}
            onChange={(event) => {
              setApiProcess(event.target.value === "new" ? "new" : "old");
              clearPrediction();
            }}
          >
            <option value="new">Новый процесс C</option>
            <option value="old">Старый процесс A</option>
          </select>
        </label>
      </div>
      <label style={evidenceStyles.label}>
        <span style={evidenceStyles.labelText}>Первое неподтверждённое наблюдение</span>
        <select
          style={evidenceStyles.select}
          value={prediction}
          onChange={(event) => {
            setPrediction(event.target.value);
            setRevealed(false);
          }}
        >
          <option value="">Выбрать прогноз</option>
          <option value="complete">{proofOptions.complete}</option>
          <option value="cli">{proofOptions.cli}</option>
          <option value="restart">{proofOptions.restart}</option>
        </select>
      </label>
      <div style={evidenceStyles.actions}>
        <button
          style={{ ...evidenceStyles.action, borderColor: "var(--accent)", opacity: prediction ? 1 : 0.55 }}
          type="button"
          disabled={!prediction}
          onClick={() => setRevealed(true)}
        >
          Проверить прогноз
        </button>
        <button
          style={evidenceStyles.action}
          type="button"
          onClick={reset}
        >
          Сбросить
        </button>
      </div>
      <div role="status" aria-live="polite">
        {revealed && (
          <div style={evidenceStyles.feedback}>
            <strong>{prediction === expected ? "Прогноз верен." : "Сверим с наблюдением."}</strong>
            <p>
              {expected === "cli"
                ? "Поиск ID 1 в CLI даёт «записи нет»: CLI открыл warehouse-other.sqlite3. На этом шаге история останавливается."
                : expected === "restart"
                  ? "CLI нашёл и закрыл запись. API A может прочитать closed, но в модели так и не появился новый процесс C."
                  : "CLI нашёл ID 1 и сохранил closed. Новый API C прочитал ID 1 и сводку (1, 0, 1) из общего файла."}
            </p>
          </div>
        )}
      </div>
    </fieldset>
  );
}

function ReadyPythonFile({ name, source }: { name: string; source: string }) {
  return (
    <div style={{ minWidth: 0, borderTop: "1px solid var(--line-soft)", paddingBlock: "0.65rem" }}>
      <a
        style={{ ...wrapPath, fontWeight: 600, textDecoration: "underline", textUnderlineOffset: "0.15em" }}
        href={`data:text/x-python;charset=utf-8,${encodeURIComponent(source)}`}
        download={name}
      >
        Скачать {name}
      </a>
      <details style={{ marginTop: "0.35rem" }}>
        <summary style={{ ...wrapPath, cursor: "pointer" }}>Показать код файла {name}</summary>
        <CodeBlock code={source} language="python" />
      </details>
    </div>
  );
}

export function Lesson79({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title="Проверяем одну историю API → CLI → новый процесс"
        intro="Создадим одну задачу, передадим её ID между интерфейсами и проверим сохранённое состояние после перезапуска."
      />

      <Section number="00" title="Одна задача в двух интерфейсах">
        <Lead>Запись, запрос и отказ хранилища мы уже проверяли по отдельности. Теперь проследим, как одна задача переходит между рабочими интерфейсами Planner.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Наш SQL Planner умеет читать и менять задачи в API и CLI. Для отказа, отката и выборки есть отдельные проверки." },
          { title: "Новый вопрос", description: "Как убедиться, что CLI нашёл запись API, а новый API-процесс увидел изменение CLI?" },
          { title: "Результат", description: "Увидим тот же ID до изменения, после него и после нового запуска API." },
        ]} />
        <p>ID связывает наблюдения. Когда интерфейс меняется, мы сверяем адрес файла, ID и состояние строки. При новом запуске к ним добавляется отметка процесса.</p>
      </Section>

      <Section number="01" title="Найти общую базу">
        <Lead>У API и CLI отдельные процессы. Их связывает файл SQLite.</Lead>
        <p>API выделяет короткую Session на запрос, а CLI выделяет её на команду с данными. Session задаёт границу работы с базой. После <code>commit</code> изменение можно прочитать из другой Session по тому же адресу. Даже в новом процессе строка останется в файле.</p>
        <p>Переменная <code>DATABASE_URL</code> задаёт адрес SQLite. Например, <code style={wrapPath}>sqlite:///C:/Temp/warehouse/warehouse.sqlite3</code> указывает полный путь к учебному файлу на Windows. Сверим адреса целиком: файлы с одинаковым именем могут лежать в разных каталогах. Инициализация создаёт в выбранном файле пустую схему.</p>
        <Diagram caption="Создание, изменение и новое чтение одной складской записи" orientation="vertical">
          <DiagramFlow label="Общий путь записи склада" orientation="vertical">
            <DiagramNode title="API A создаёт запись">
              <p>API возвращает ID 1 и состояние <code>open</code>.</p>
            </DiagramNode>
            <DiagramArrow label="Подтверждённая запись остаётся в файле" />
            <DiagramNode title="Общий файл warehouse.sqlite3" shape="database">
              <p>В файле есть запись «Набор» с ID 1.</p>
            </DiagramNode>
            <DiagramArrow label="CLI открывает этот файл по тому же адресу" />
            <DiagramNode title="Отдельный CLI B находит и закрывает запись">
              <p>CLI читает ID 1 и сохраняет <code>closed</code> в общий файл.</p>
            </DiagramNode>
            <DiagramArrow label="API A завершён, API C открывает тот же файл" />
            <DiagramNode title="Новый API C читает запись">
              <p>Новый процесс видит ID 1 и сохранённое <code>closed</code>.</p>
            </DiagramNode>
          </DiagramFlow>
          <DiagramNote title="Если адрес CLI изменился">
            В пустом <code>warehouse-other.sqlite3</code> есть таблица склада. Поиск ID 1 сообщает, что записи нет.
          </DiagramNote>
        </Diagram>
        <QuizCard
          question="У API и CLI файлы с одинаковым именем warehouse.sqlite3. Что проверим вначале?"
          options={["Полные адреса обоих файлов", "Совпадение имени файла", "Номер порта API"]}
          correctIndex={0}
          explanation="Полный адрес показывает, какой файл открыл каждый процесс. Затем чтение по ID подтверждает, что нужная строка действительно видна."
          orientation="vertical"
        />
      </Section>

      <Section number="02" title="Прочитать запись после остановки API">
        <Lead>Разберём весь маршрут на отдельной учебной базе склада.</Lead>
        <p>Подготовлены два пустых файла с таблицей записей. API A открывает основной <code>warehouse.sqlite3</code>. Запрос <code>GET /health</code> показывает, что процесс отвечает. Пустую таблицу подтверждает отдельный запрос <code>GET /summary</code>, который читает данные.</p>
        <p>Отправляем в A <code>POST /stock</code> с телом <code>{'{"name":"Набор"}'}</code>. API сохраняет строку и возвращает 201 с ID 1. В отдельном CLI B используем полученный ID. Команда <code>lookup 1</code> читает открытую запись. Затем <code style={wrapPath}>close 1</code> сохраняет <code>closed</code>, <code>summary</code> считает все, открытые и закрытые записи. Закрылась одна существующая строка, поэтому общее количество осталось прежним.</p>
        <p>Останавливаем A, запускаем C и оставляем существующий файл по прежнему адресу. Теперь C отвечает на <code>GET /stock/1</code> и <code>GET /summary</code>. Объекты в памяти A уже недоступны, а C получает состояние из файла.</p>
        <details className="my-3">
          <summary className="cursor-pointer font-medium">Показать сводный след обычного прогона</summary>
          <CodeBlock code={warehouseTrace} language="text" />
        </details>
        <p>Есть два полезных контраста. CLI на втором подготовленном файле останавливается уже на поиске ID 1. А повторное чтение через старый A может показать <code>closed</code> из новой Session, при этом проверка нового процесса ещё впереди.</p>
        <WarehouseEvidence />

        <details className="my-4">
          <summary className="cursor-pointer font-medium">Повторить опыт склада в терминале</summary>
          <p>Сохраним готовый файл в отдельном каталоге и выполним <code>python warehouse_trace.py</code>. Для контраста выполним <code>python warehouse_trace.py --other</code>. Скрипт сам создаёт учебные файлы и запускает процессы. Он печатает ответы и удаляет свой временный каталог после завершения.</p>
          <ReadyPythonFile name="warehouse_trace.py" source={WAREHOUSE_DEMO} />
        </details>
      </Section>

      <Section number="03" title="Подготовка к проверке">
        <Lead>Берём собственный Planner с рабочим SQL-хранилищем и результаты отдельных проверок. Для запуска понадобятся два готовых файла и Python из среды Planner.</Lead>
        <p>Откроем корень своего проекта, где рядом находятся <code>app/</code> и <code>tests/</code>. Сохраним первый файл как <code style={wrapPath}>tools/planner_acceptance.py</code>, при необходимости создадим <code>tools/</code>. Второй сохраним как <code style={wrapPath}>tests/test_planner_acceptance_contract.py</code>. Если файл с этим именем уже есть, сверим его и сохраним свои изменения отдельно. Можно скачать оба файла или раскрыть и скопировать полный код.</p>
        <ReadyPythonFile name="planner_acceptance.py" source={ACCEPTANCE_LAUNCHER} />
        <ReadyPythonFile name="test_planner_acceptance_contract.py" source={CONTRACT_REGRESSION} />
        <p>Из корня запускаем <code style={wrapPath}>python tools/planner_acceptance.py</code> своим интерпретатором проекта. Оснастка покажет два абсолютных адреса и меню. Она подготавливает временные схемы и передаёт общий адрес штатным API и CLI. Для режима с другим файлом адрес CLI меняется. При старте помощник сверяет наличие четырёх файлов с проверками. Если какого-то файла нет, запуск остановится и покажет список отсутствующих файлов. В файле отката должны быть готовый контраст после commit и собственный тест с <code>rollback_sessions</code>. Если упал тест, сначала восстанавливаем этот результат в своём Planner.</p>
        <p>Если при проверке отката вы выбрали другое имя, укажите свой путь после <code>--rollback-test</code>. Например, <code style={wrapPath}>python tools/planner_acceptance.py --rollback-test tests/test_my_rollback.py</code> выбирает ваш готовый файл в <code>tests/</code>.</p>
        <p>После запуска API оснастка напечатает отметку процесса, его PID (номер запущенного процесса) и адрес Swagger. В Swagger раскрываем нужный метод и путь, выбираем <strong>Try it out</strong> для ввода, затем <strong>Execute</strong> для запроса. В теле создания <code>title</code> означает название, <code>priority</code> принимает число от 1 до 5. В поле <code>task_id</code> подставляем фактический ID, полученный из ответа. Статус и JSON читаем после каждого запроса.</p>
        <p>В CLI команды <code>find</code> и <code>done</code> запрашивают ID. <code>find</code> показывает сохранённые поля и состояние, <code>done</code> сохраняет отметку выполнения, а <code>exit</code> возвращает меню. Команда перезапуска дожидается завершения первого API и поднимает новый на том же файле. Сверяем отметки запуска и снова отправляем запрос к задаче.</p>
        <p>Сводка Planner находится на <code>GET /stats</code>. В ней <code>total</code> означает все задачи, <code>open</code> открытые, <code>done</code> завершённые. Как на складе, выполнение одной существующей записи меняет открытый и завершённый счётчики. Поля <code>id</code>, <code>title</code>, <code>priority</code> и <code>is_done</code> читаем в HTTP-ответе. Внутренние <code>tags</code> проверяет отдельная регрессия.</p>
        <p>«Входные проверки» запускают тесты отказа, отката, наблюдения SQL и выборки из проекта. «Регрессия» повторяет их и добавляет готовую проверку прежнего договора Planner на отдельных данных. Итог <code>passed</code> означает успешные проверки, а при сбое pytest показывает имя теста. В контрасте со вторым файлом база доступна, просто нужной строки там нет. Известный отказ открытия базы и успешное восстановление проверяет отдельный автоматический сценарий.</p>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <Lead>В своём Planner проведём одну тестовую запись через обычные точки входа и сохраним результаты в локальном листе наблюдений.</Lead>
        <LinkedNotes items={[
          { title: "Проведём один ID через интерфейсы", description: "Получим ID из ответа API, найдём эту запись в отдельном CLI и увидим изменение состояния." },
          { title: "Перезапустим API", description: "В новом процессе прочитаем тот же ID, сохранённое состояние и согласованные счётчики." },
          { title: "Проверим расхождение и прежний договор", description: "Поймём, какой переход нарушает другой файл. Сверим итог готовой регрессии." },
        ]} />
        <p>Если наблюдение расходится с ожиданием, вернёмся к схеме и проверим по порядку: адрес файла, ID, состояние и отметку процесса. Эта запись пригодится, когда будем воспроизводить запуск по собственному README.</p>
      </Section>
    </RichLesson>
  );
}
