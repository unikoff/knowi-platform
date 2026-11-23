import { useId, useState } from "react";

import {
  Callout,
  CodeBlock,
  Diagram,
  DiagramArrow,
  DiagramFlow,
  DiagramNode,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const inventoryProbe = String.raw`from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


def catalog_engine(path):
    return create_engine(f"sqlite:///{path.as_posix()}")


def prepare_catalog(path):
    engine = catalog_engine(path)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE inventory (id INTEGER PRIMARY KEY, name TEXT)"
            ))
            connection.execute(text(
                "INSERT INTO inventory (id, name) VALUES (1, 'Лампа')"
            ))
    finally:
        engine.dispose()


def read_catalog(label, path):
    engine = catalog_engine(path)
    try:
        with Session(engine) as session:
            names = session.scalars(
                text("SELECT name FROM inventory ORDER BY id")
            ).all()
        print(f"{label}: {names}")
    except DBAPIError as error:
        code = getattr(error.orig, "sqlite_errorcode", None)
        print(f"{label}: {type(error).__name__}, код {code}")
    finally:
        engine.dispose()


with TemporaryDirectory() as directory:
    root = Path(directory)
    working = root / "open" / "catalog.sqlite3"
    working.parent.mkdir()
    prepare_catalog(working)

    missing = root / "missing-parent" / "catalog.sqlite3"
    empty = root / "empty" / "catalog.sqlite3"
    empty.parent.mkdir()
    empty.touch()

    read_catalog("Обычный каталог", working)
    read_catalog("Адрес недоступен", missing)
    read_catalog("Нет таблицы", empty)`;

const projectPatch = String.raw`diff --git a/project/app/DBase/session.py b/project/app/DBase/session.py
--- a/project/app/DBase/session.py
+++ b/project/app/DBase/session.py
@@ -1,5 +1,32 @@
+import logging
+import sqlite3
+from collections.abc import Iterator
+from contextlib import contextmanager
+
 from sqlalchemy.engine import Engine
+from sqlalchemy.exc import DBAPIError
 from sqlalchemy.orm import Session, sessionmaker
+
+from app.errors import StorageError
+
+
+logger = logging.getLogger(__name__)
+
+
+def is_sqlite_unavailable(error: DBAPIError) -> bool:
+    return False
+
+
+@contextmanager
+def available_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
+    try:
+        with session_factory() as session:
+            yield session
+    except DBAPIError as error:
+        if is_sqlite_unavailable(error):
+            logger.warning("SQLITE_CANTOPEN")
+            raise StorageError("Task storage is temporarily unavailable.") from None
+        raise
␠
␠
 def create_session_factory(engine: Engine) -> sessionmaker[Session]:
diff --git a/project/app/core/dependencies.py b/project/app/core/dependencies.py
--- a/project/app/core/dependencies.py
+++ b/project/app/core/dependencies.py
@@ -6,6 +6,7 @@
 from sqlalchemy.orm import Session
␠
 from app.DBase.repositories import SqlTaskRepository
+from app.DBase.session import available_session
 from app.core.config import Settings
 from app.main import build_service
 from app.models import Task
@@ -22,11 +23,8 @@
     if session_factory is None:
         raise RuntimeError("SQL session factory is not configured")
␠
-    session = session_factory()
-    try:
+    with available_session(session_factory) as session:
         yield session
-    finally:
-        session.close()
␠
␠
 def get_planner(db: Session = Depends(get_db)) -> PlannerService:
diff --git a/project/app/main.py b/project/app/main.py
--- a/project/app/main.py
+++ b/project/app/main.py
@@ -1,14 +1,14 @@
 from collections.abc import Iterator
 from contextlib import contextmanager
␠
+from sqlalchemy.exc import DBAPIError
 from sqlalchemy.orm import Session, sessionmaker
␠
 from app.DBase.repositories import SqlTaskRepository
-from app.DBase.session import create_session_factory
+from app.DBase.session import available_session, create_session_factory
 from app.cli import run
 from app.core.config import load_settings
 from app.core.engine import make_engine
-from app.errors import StorageError
 from app.service import PlannerService
␠
␠
@@ -24,14 +24,14 @@
 def sql_service_context(
     session_factory: sessionmaker[Session],
 ) -> Iterator[PlannerService]:
-    with session_factory() as session:
+    with available_session(session_factory) as session:
         yield build_service(
             SqlTaskRepository(session),
             write_scope=session.begin,
         )
␠
␠
-def main():
+def main() -> int:
     settings = load_settings()
     engine = make_engine(settings.database_url)
     session_factory = create_session_factory(engine)
@@ -40,14 +40,14 @@
             None,
             context_factory=lambda: sql_service_context(session_factory),
         )
-    except StorageError as error:
-        print(
-            "Не удалось выполнить операцию с задачами. "
-            f"Приложение остановлено: {error}"
-        )
+    except DBAPIError:
+        print("Внутренняя ошибка. Команда не выполнена.")
+        return 1
+    else:
+        return 0
     finally:
         engine.dispose()
␠
␠
 if __name__ == "__main__":
-    main()
+    raise SystemExit(main())
diff --git a/project/app/api.py b/project/app/api.py
--- a/project/app/api.py
+++ b/project/app/api.py
@@ -1,11 +1,14 @@
 from contextlib import asynccontextmanager
␠
-from fastapi import FastAPI
+from fastapi import FastAPI, Request
+from fastapi.responses import JSONResponse, PlainTextResponse
+from sqlalchemy.exc import DBAPIError
␠
 from app.DBase.session import create_session_factory
 from app.core.config import load_settings
 from app.core.dependencies import PlannerDep
 from app.core.engine import make_engine
+from app.errors import StorageError
 from app.routers.tasks.router import router as tasks_router
 from app.routers.preferences import router as preferences_router
␠
@@ -21,12 +24,25 @@
         engine.dispose()
␠
␠
-app = FastAPI(title=settings.app_name, lifespan=lifespan)
+app = FastAPI(title=settings.app_name, lifespan=lifespan, debug=False)
 app.state.settings = settings
 app.state.engine = engine
 app.state.session_factory = create_session_factory(engine)
 app.include_router(tasks_router)
 app.include_router(preferences_router)
+
+
+@app.exception_handler(StorageError)
+async def storage_unavailable(_request: Request, _error: StorageError):
+    return JSONResponse(
+        status_code=503,
+        content={"detail": "Task storage is temporarily unavailable."},
+    )
+
+
+@app.exception_handler(DBAPIError)
+async def database_failure(_request: Request, _error: DBAPIError):
+    return PlainTextResponse("Internal Server Error", status_code=500)
␠
␠
 @app.get("/health")
diff --git a/project/app/cli.py b/project/app/cli.py
--- a/project/app/cli.py
+++ b/project/app/cli.py
@@ -1,6 +1,6 @@
 from contextlib import nullcontext
␠
-from app.errors import TaskNotFoundError
+from app.errors import StorageError, TaskNotFoundError
 from app.service import PlannerService
 from app.validators import normalize_title
␠
@@ -202,12 +202,16 @@
         if normalized_command not in COMMAND_HANDLERS:
             keep_running = execute_command(None, command, read, write)
         else:
-            with context_factory() as command_service:
-                keep_running = execute_command(
-                    command_service,
-                    command,
-                    read,
-                    write,
-                )
+            try:
+                with context_factory() as command_service:
+                    keep_running = execute_command(
+                        command_service,
+                        command,
+                        read,
+                        write,
+                    )
+            except StorageError:
+                write("База данных временно недоступна. Команда не выполнена.")
+                keep_running = True
         if not keep_running:
             break
diff --git a/project/app/errors.py b/project/app/errors.py
--- a/project/app/errors.py
+++ b/project/app/errors.py
@@ -3,4 +3,4 @@
␠
␠
 class StorageError(Exception):
-    pass
+    """The SQL task storage could not be opened."""
diff --git a/project/tests/test_sql_runtime.py b/project/tests/test_sql_runtime.py
--- a/project/tests/test_sql_runtime.py
+++ b/project/tests/test_sql_runtime.py
@@ -1,10 +1,8 @@
 from pathlib import Path
␠
 from fastapi.testclient import TestClient
-import pytest
 from sqlalchemy import select
 from sqlalchemy.engine import URL
-from sqlalchemy.exc import SQLAlchemyError
 from sqlalchemy.orm import Session, sessionmaker
␠
 from app.DBase.models import Base, TaskRow
@@ -150,11 +148,11 @@
         with TestClient(app, raise_server_exceptions=False) as client:
             response = client.get("/tasks")
␠
-        assert response.status_code == 500
+        assert response.status_code in (500, 503)
         assert legacy_json.read_bytes() == legacy_bytes
␠
         monkeypatch.setattr("app.main.load_settings", lambda: settings)
-        answers = iter(["list"])
+        answers = iter(["list", "exit"])
         messages = []
␠
         def run_with_input(service=None, read=input, write=print, *, context_factory=None):
@@ -166,10 +164,12 @@
             )
␠
         monkeypatch.setattr("app.main.run", run_with_input)
-        with pytest.raises(SQLAlchemyError):
-            cli_main()
+        exit_code = cli_main()
␠
-        assert messages == ["Команды: " + ", ".join(COMMANDS)]
+        assert exit_code in (0, 1)
+        assert messages[0] == "Команды: " + ", ".join(COMMANDS)
+        if exit_code == 0:
+            assert "База данных временно недоступна. Команда не выполнена." in messages
         assert "Список задач пуст." not in messages
         assert legacy_json.read_bytes() == legacy_bytes
     finally:
diff --git a/project/tests/test_storage_availability.py b/project/tests/test_storage_availability.py
new file mode 100644
--- /dev/null
+++ b/project/tests/test_storage_availability.py
@@ -0,0 +1,182 @@
+import json
+import os
+from pathlib import Path
+import shutil
+import sqlite3
+import subprocess
+import sys
+
+import pytest
+from sqlalchemy import create_engine
+from sqlalchemy.exc import OperationalError
+from sqlalchemy.orm import Session
+
+from app.DBase.models import Base, TaskRow
+from app.DBase.session import is_sqlite_unavailable
+
+
+PROJECT = Path(__file__).resolve().parents[1]
+TITLE = "Проверить фонарь"
+JSON_TITLE = "Контрольная запись JSON"
+REQUEST_MARKER = "private-test-title"
+UNAVAILABLE_DETAIL = "Task storage is temporarily unavailable."
+UNAVAILABLE_CLI = "База данных временно недоступна. Команда не выполнена."
+INTERNAL_ERROR_CLI = "Внутренняя ошибка. Команда не выполнена."
+
+HTTP_PROBE = """
+import json
+import sys
+
+from fastapi.testclient import TestClient
+from app.api import app
+
+strict, encoded_requests = sys.argv[1:]
+requests = json.loads(encoded_requests)
+with TestClient(app, raise_server_exceptions=strict == "yes") as client:
+    responses = [client.request(method, path, **kwargs)
+                 for method, path, kwargs in requests]
+print(json.dumps([(response.status_code, response.text) for response in responses]))
+"""
+
+
+@pytest.fixture
+def workspace(tmp_path):
+    # Imports and relative or module-based paths stay in the temporary copy.
+    shutil.copytree(PROJECT / "app", tmp_path / "app", ignore=shutil.ignore_patterns(
+        "__pycache__", "*.pyc", ".env", "data",
+    ))
+    data = tmp_path / "data"
+    data.mkdir()
+    snapshot = data / "tasks.json"
+    snapshot.write_text(
+        json.dumps([{"id": 41, "title": JSON_TITLE, "priority": 2,
+                     "is_done": False, "tags": []}]),
+        encoding="utf-8",
+    )
+    return tmp_path, snapshot
+
+
+def run_project(folder, database, args, commands=None):
+    env = {
+        **os.environ,
+        "DATABASE_URL": f"sqlite:///{database.as_posix()}",
+        "JSON_PATH": str(folder / "data" / "tasks.json"),
+        "PYTHONPATH": str(folder),
+        "PYTHONIOENCODING": "utf-8",
+    }
+    return subprocess.run(
+        [sys.executable, "-B", *args],
+        cwd=folder, env=env, input=commands, capture_output=True,
+        encoding="utf-8", timeout=20, check=False,
+    )
+
+
+def request_tasks(folder, database, *requests, strict=True):
+    process = run_project(folder, database, [
+        "-c", HTTP_PROBE, "yes" if strict else "no", json.dumps(requests),
+    ])
+    assert process.returncode == 0, process.stderr
+    return json.loads(process.stdout), process.stderr
+
+
+def run_list(folder, database):
+    # The second list and exit exercise continuation after a known refusal.
+    return run_project(folder, database, ["-m", "app.main"], "list\nlist\nexit\n")
+
+
+def database_with_task(folder):
+    path = folder / "working.sqlite3"
+    engine = create_engine(f"sqlite:///{path.as_posix()}")
+    try:
+        Base.metadata.create_all(engine)
+        with Session(engine) as session, session.begin():
+            session.add(TaskRow(id=1, title=TITLE, priority=3, is_done=False,
+                                tags=[], title_normalized=TITLE.lower()))
+    finally:
+        engine.dispose()
+    return path
+
+
+def assert_safe(*outputs, folder):
+    for output in outputs:
+        assert str(folder) not in output
+        assert folder.as_posix() not in output
+        for fragment in ("traceback", "[sql:", "[parameters:", "select ",
+                         "insert into", REQUEST_MARKER, JSON_TITLE):
+            assert fragment.casefold() not in output.casefold()
+
+
+def test_smoke_api_and_cli_read_the_same_task(workspace):
+    folder, _snapshot = workspace
+    database = database_with_task(folder)
+    responses, _logs = request_tasks(
+        folder, database, ("GET", "/tasks", {}),
+        ("GET", "/tasks/999", {}), ("GET", "/tasks?limit=0", {}),
+    )
+    assert [status for status, _ in responses] == [200, 404, 422]
+    assert [task["title"] for task in json.loads(responses[0][1])] == [TITLE]
+    cli = run_list(folder, database)
+    assert cli.returncode == 0
+    assert cli.stdout.count(TITLE) == 2
+
+
+def test_cantopen_gives_503_and_cli_can_continue(workspace):
+    folder, snapshot = workspace
+    before = snapshot.read_bytes()
+    database = folder / "absent-parent" / "missing.sqlite3"
+    responses, logs = request_tasks(folder, database, ("GET", "/tasks", {}))
+    status, body = responses[0]
+    assert status == 503
+    assert json.loads(body) == {"detail": UNAVAILABLE_DETAIL}
+
+    cli = run_list(folder, database)
+    assert cli.returncode == 0
+    assert cli.stdout.count(UNAVAILABLE_CLI) == 2
+    assert "Список задач пуст." not in cli.stdout
+    assert "SQLITE_CANTOPEN" in logs and "SQLITE_CANTOPEN" in cli.stderr
+    assert_safe(body, logs, cli.stdout, cli.stderr, folder=folder)
+    assert before == snapshot.read_bytes()
+    assert not database.parent.exists()
+
+    working = database_with_task(folder)
+    responses, _logs = request_tasks(folder, working, ("GET", "/tasks", {}))
+    assert responses[0][0] == 200
+    assert TITLE in responses[0][1]
+
+
+def test_open_file_without_schema_stays_a_safe_internal_error(workspace):
+    folder, snapshot = workspace
+    before = snapshot.read_bytes()
+    database = folder / "empty.sqlite3"
+    database.touch()
+
+    responses, logs = request_tasks(
+        folder, database, ("GET", "/tasks", {}),
+        ("POST", "/tasks", {"json": {"title": REQUEST_MARKER, "priority": 3}}),
+        strict=False,
+    )
+    assert responses == [[500, "Internal Server Error"], [500, "Internal Server Error"]]
+    cli = run_list(folder, database)
+    assert cli.returncode != 0
+    assert INTERNAL_ERROR_CLI in cli.stdout
+    assert UNAVAILABLE_CLI not in cli.stdout
+    assert "SQLITE_CANTOPEN" not in logs + cli.stderr
+    assert_safe(*(text for _, text in responses), logs, cli.stdout, cli.stderr,
+                folder=folder)
+    assert before == snapshot.read_bytes()
+
+
+@pytest.mark.parametrize(("driver_code", "expected"), [
+    (sqlite3.SQLITE_CANTOPEN, True),
+    (sqlite3.SQLITE_CANTOPEN_ISDIR, True),
+    (sqlite3.SQLITE_ERROR, False),
+    (sqlite3.SQLITE_CONSTRAINT_UNIQUE, False),
+    (999, False),
+    (None, False),
+])
+def test_classifier_code_contract(driver_code, expected):
+    cause = sqlite3.OperationalError("synthetic driver error")
+    if driver_code is not None:
+        cause.sqlite_errorcode = driver_code
+    wrapped = OperationalError(None, None, cause)
+    assert is_sqlite_unavailable(wrapped) is expected
`.replace(/^␠$/gm, " ");

const catalogCases = [
  {
    label: "Файл открывается, таблица есть",
    database: "Открылась БД. SELECT прочитал одну строку.",
    driver: "Исключения нет.",
    http: "200",
    cli: "Лампа",
    cause: "Запрос выполнен. Классификатор ошибок не вызывается.",
  },
  {
    label: "Родительский каталог отсутствует",
    database: "SQLite не смогла открыть файл БД.",
    driver: "OperationalError, код 14 (SQLITE_CANTOPEN).",
    http: "503",
    cli: "Каталог временно недоступен.",
    cause: "Файл открыть не удалось. Обвязка передаёт известный отказ о недоступности.",
  },
  {
    label: "Файл открывается, таблицы нет",
    database: "БД открылась. SELECT не нашёл таблицу inventory.",
    driver: "OperationalError, код 1 (SQLITE_ERROR).",
    http: "500",
    cli: "Внутренняя ошибка каталога.",
    cause: "Соединение уже установлено. Сбой случился при обращении к отсутствующей таблице.",
  },
];

function CatalogProbe() {
  const heading = useId();
  const [caseIndex, setCaseIndex] = useState(0);
  const [prediction, setPrediction] = useState("");
  const [revealed, setRevealed] = useState(false);
  const selected = catalogCases[caseIndex];

  function reset() {
    setCaseIndex(0);
    setPrediction("");
    setRevealed(false);
  }

  return (
    <div role="group" aria-labelledby={heading} style={{ minWidth: 0 }}>
      <h3 id={heading}>Что ответит каталог?</h3>
      <p>Выберите состояние базы и предскажите HTTP-статус. Эта модель показывает путь результата в API и CLI.</p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 14rem), 1fr))", gap: "0.75rem" }}>
        <label style={{ display: "grid", gap: "0.25rem", minWidth: 0 }}>
          Состояние базы
          <select
            value={caseIndex}
            style={{ width: "100%", minHeight: "2.75rem", font: "inherit" }}
            onChange={(event) => {
              setCaseIndex(Number(event.target.value));
              setPrediction("");
              setRevealed(false);
            }}
          >
            {catalogCases.map((scenario, index) => (
              <option key={scenario.label} value={index}>{scenario.label}</option>
            ))}
          </select>
        </label>
        <label style={{ display: "grid", gap: "0.25rem", minWidth: 0 }}>
          Прогноз HTTP
          <select
            value={prediction}
            style={{ width: "100%", minHeight: "2.75rem", font: "inherit" }}
            onChange={(event) => {
              setPrediction(event.target.value);
              setRevealed(false);
            }}
          >
            <option value="">Выберите статус</option>
            <option value="200">200 · Чтение выполнено</option>
            <option value="503">503 · База недоступна</option>
            <option value="500">500 · Внутренний сбой</option>
          </select>
        </label>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem", marginBlock: "0.75rem" }}>
        <button type="button" disabled={!prediction} onClick={() => setRevealed(true)} style={{ minHeight: "2.75rem" }}>
          Выполнить SELECT
        </button>
        <button type="button" onClick={reset} style={{ minHeight: "2.75rem" }}>
          Сбросить
        </button>
      </div>
      <div aria-live="polite">
        {revealed && (
          <>
            <p><strong>{prediction === selected.http ? "Прогноз совпал." : "Прогноз отличается."}</strong> {selected.cause}</p>
            <ol>
              <li><strong>Операция.</strong> {selected.database}</li>
              <li><strong>Причина.</strong> {selected.driver}</li>
              <li><strong>Закрытие.</strong> Область Session завершилась.</li>
              <li><strong>Результат.</strong> HTTP {selected.http}. CLI: {selected.cli}</li>
            </ol>
          </>
        )}
      </div>
      <p>Сравните два случая с открываемым файлом. Что меняется, когда таблица исчезает?</p>
    </div>
  );
}

const portableInstall = String.raw`git apply --include=project/app/DBase/session.py --include=project/app/errors.py --include=project/tests/test_storage_availability.py planner-storage-wiring.patch`;

const dependencyScope = String.raw`    with available_session(session_factory) as session:
        yield session`;

const httpHandlers = String.raw`@app.exception_handler(StorageError)
async def storage_unavailable(_request: Request, _error: StorageError):
    return JSONResponse(
        status_code=503,
        content={"detail": "Task storage is temporarily unavailable."},
    )


@app.exception_handler(DBAPIError)
async def database_failure(_request: Request, _error: DBAPIError):
    return PlainTextResponse("Internal Server Error", status_code=500)`;

const cliDispatch = String.raw`    try:
        with context_factory() as command_service:
            handler(command_service, read=read, write=write)
    except StorageError:
        write("База данных временно недоступна. Команда не выполнена.")`;

const cliEntrypoint = String.raw`def main() -> int:
    settings = load_settings()
    engine = make_engine(settings.database_url)
    factory = create_session_factory(engine)
    try:
        run(context_factory=make_cli_context(factory, sql_service_context))
    except DBAPIError:
        print("Внутренняя ошибка. Команда не выполнена.")
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())`;

function ProjectPreparation() {
  const [copyMessage, setCopyMessage] = useState("");

  async function copyPatch() {
    try {
      await navigator.clipboard.writeText(projectPatch);
      setCopyMessage("Патч скопирован.");
    } catch {
      setCopyMessage("Выделите текст патча и скопируйте его вручную. В конце файла добавьте перевод строки.");
    }
  }

  return (
    <div style={{ minWidth: 0 }}>
      <h3>Подготовка проекта</h3>
      <p>
        Используйте свой Planner с <code>project/app</code> и <code>project/tests</code> внутри
        учебного репозитория. Проверьте, что API и CLI уже читают одну SQLite.
        Патч подключит к обоим интерфейсам готовую область Session.
      </p>
      <p>
        Откройте патч, скопируйте его целиком и сохраните как <code>planner-storage-wiring.patch</code> в
        папке рядом с <code>project</code> в UTF-8. В файле должен остаться перевод строки в конце.
        Из этой папки примените патч. Команда меняет рабочую копию без коммита.
      </p>
      <CodeBlock language="text" code="git apply planner-storage-wiring.patch" />
      <details style={{ minWidth: 0, marginBlock: "0.75rem" }}>
        <summary>Открыть готовый патч</summary>
        <button type="button" onClick={copyPatch} style={{ minHeight: "2.75rem", marginBlock: "0.5rem" }}>Скопировать патч</button>
        <p aria-live="polite">{copyMessage}</p>
        <CodeBlock language="text" caption="planner-storage-wiring.patch" code={projectPatch} />
      </details>
      <p>
        При несовпадении строк Git остановит применение без правок. Тогда выберите один из
        вариантов ниже.
      </p>
      <details style={{ minWidth: 0, marginBlock: "0.75rem" }}>
        <summary>Вариант без прежнего теста отказа</summary>
        <p>
          Если другая только версия <code>tests/test_sql_runtime.py</code> или этого файла нет,
          примените патч с опцией <code>--exclude</code>. Она пропускает правку старого теста.
          Готовая проверка <code>test_storage_availability.py</code> всё равно добавится.
        </p>
        <CodeBlock language="text" caption="Если старый тест уже обновлён или отсутствует" code={
          "git apply --exclude=project/tests/test_sql_runtime.py planner-storage-wiring.patch"
        } />
      </details>
      <details style={{ minWidth: 0, marginBlock: "0.75rem" }}>
        <summary>Вариант с диспетчером команды и обвязкой переключения</summary>
        <p>
          Этот вариант подходит, если <code>with context_factory()</code> находится в
          <code> execute_command</code>, а <code>get_planner</code> вызывает <code>make_http_planner</code>.
          В корне репозитория рядом с <code>project</code> примените только три файла.
          Опция <code>--include</code> выбирает указанные файлы из сохранённого патча.
          После команды в <code>app/DBase/session.py</code> появится стартер, а в <code>tests/</code> проверка.
        </p>
        <CodeBlock language="text" caption="Три общих файла из патча" code={portableInstall} />
        <ol>
          <li>
            <strong>HTTP Session.</strong> В <code>app/core/dependencies.py</code> добавьте
            <code> from app.DBase.session import available_session</code>. Внутри <code>get_db</code>
            замените блок от <code>session = session_factory()</code> до <code>session.close()</code>.
            Строки получения фабрики, проверку <code>None</code> и весь <code>get_planner</code> оставьте.
            <CodeBlock caption="Нижняя часть get_db" code={dependencyScope} />
          </li>
          <li>
            <strong>Ответ HTTP.</strong> В <code>app/api.py</code> добавьте
            <code> Request</code> к импорту FastAPI и три импорта ниже. В существующем вызове
            <code> FastAPI(...)</code> установите <code>debug=False</code>.
            Фабрику Session, <code>lifespan</code>, настройки и подключение роутеров оставьте.
            Вставьте оба обработчика после <code>app.include_router(...)</code>, перед первым маршрутом.
            <CodeBlock caption="Импорты для обработчиков" code={
              "from fastapi.responses import JSONResponse, PlainTextResponse\nfrom sqlalchemy.exc import DBAPIError\nfrom app.errors import StorageError"
            } />
            <CodeBlock caption="Обработчики в app/api.py" code={httpHandlers} />
          </li>
          <li>
            <strong>Отказ одной команды.</strong> В <code>app/cli.py</code> добавьте
            <code> StorageError</code> к импорту из <code>app.errors</code>.
            В <code>execute_command</code> замените только <code>with context_factory()</code>
            и вложенный вызов <code>handler</code> на этот блок. Выбор команды над блоком,
            <code> return True</code> под ним и функцию <code>run</code> оставьте.
            <CodeBlock caption="Область вызова handler" code={cliDispatch} />
          </li>
          <li>
            <strong>Запуск CLI.</strong> В <code>app/main.py</code> оставьте импорт
            <code> make_cli_context</code> из <code>app.cutover_wiring</code> и добавьте
            импорты ниже. Если в этом файле импортируется <code>StorageError</code> для прежнего
            отказа, уберите этот импорт. В <code>sql_service_context</code> замените
            <code> with session_factory() as session:</code> на
            <code> with available_session(session_factory) as session:</code>. Передачу
            <code> repository</code> и <code>write_scope=session.begin</code> оставьте.
            Затем замените участок от <code>def main()</code> до конца файла готовым блоком.
            <CodeBlock caption="Импорты для запуска CLI" code={
              "from sqlalchemy.exc import DBAPIError\nfrom app.DBase.session import available_session, create_session_factory\nfrom app.core.engine import make_engine"
            } />
            <CodeBlock caption="Хвост app/main.py" code={cliEntrypoint} />
          </li>
        </ol>
        <p>
          Теперь из <code>project/</code> проверьте <code>smoke</code> командой ниже.
          После вашей правки полный набор отдельно проверит два вызова <code>list</code> и
          <code> exit</code> на временном адресе. При известном отказе обе команды сообщат о
          недоступности, и CLI завершится штатно. Файл без схемы даст общий внутренний отказ.
        </p>
      </details>
      <p>
        Откройте терминал в <code>project/</code> и используйте интерпретатор своего окружения.
        <code> python -m pytest</code> запускает pytest этим интерпретатором,
        <code> -v</code> показывает имена проверок, а <code>-k smoke</code> оставляет только
        проверку обычного чтения. Smoke должен пройти уже со стартовым <code>False</code>.
      </p>
      <CodeBlock language="text" caption="Из project/" code={
        "python -m pytest -v tests/test_storage_availability.py -k smoke"
      } />
      <p>
        После правки тела функции запустите тот же файл целиком. Проверки сами создадут временные
        адреса и тестовые данные. В строках результата <code>PASSED</code> означает, что сценарий
        дал ожидаемый результат. Если smoke упал, сначала сверьте обычную сборку, зависимости
        из <code>requirements-dev.txt</code> и применение патча.
      </p>
      <CodeBlock language="text" caption="Из project/ после правки" code={
        "python -m pytest -v tests/test_storage_availability.py"
      } />
    </div>
  );
}

export function Lesson76({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Надёжность Planner"}
        title="Распознаём недоступное хранилище"
        intro="По коду SQLite различим сбой открытия и ошибку запроса. Затем проверим одну общую границу отказа для API и CLI."
      />

      <Section number="00" title="Почему запрос к базе не выполнился">
        <LinkedNotes items={[
          {
            title: "Точка старта",
            description: "API и CLI Planner работают с одной SQLite. Проверим сбой на временной базе.",
          },
          {
            title: "Новый вопрос",
            description: "Как отличить недоступный файл от ошибки запроса к базе, которая открылась?",
          },
          {
            title: "Результат",
            description: "Оба интерфейса сообщают о недоступности при сбое открытия. Ошибка запроса к открытой базе остаётся внутренней.",
          },
        ]} />
        <p>На обе ошибки Python может ответить одним именем класса. Посмотрим, где лежит различие и как оно влияет на ответ.</p>
      </Section>

      <Section number="01" title="Одна обёртка, две причины">
        <p>
          У каталога инвентаря есть таблица <code>inventory</code>. Команда читает из неё название.
          Если родительского каталога файла нет, SQLite не может открыть БД. Если файл открывается,
          но таблица ещё не создана, SQLite спотыкается уже при запросе. В обоих случаях SQLAlchemy
          может поднять <code>OperationalError</code>, разновидность <code>DBAPIError</code>.
        </p>
        <p>
          <code>DBAPIError</code> хранит исходное исключение драйвера в <code>orig</code>. Драйвер
          передаёт запрос SQLite и может записать число причины в <code>sqlite_errorcode</code>.
          Вызов <code>getattr(error.orig, "sqlite_errorcode", None)</code> вернёт это поле или
          <code> None</code>, когда его нет. Так мы сохраняем различие даже при одинаковом имени
          класса SQLAlchemy.
        </p>
        <p>
          Ниже готовый опыт для Python 3.12 и SQLAlchemy из окружения Planner. Сохраните его в
          свободной папке как <code>inventory_probe.py</code> и из той папки выполните
          <code> python inventory_probe.py</code>. Скрипт сам готовит три временных адреса,
          выполняет один SELECT на каждом и убирает файлы после закрытия ресурсов. Можно сразу
          прочитать результат и перейти к пробе в браузере.
        </p>
        <details style={{ minWidth: 0 }}>
          <summary>Открыть полный пример inventory_probe.py</summary>
          <CodeBlock caption="inventory_probe.py" code={inventoryProbe} />
        </details>
        <CodeBlock language="text" caption="Вывод с тремя адресами каталога" code={
          "Обычный каталог: ['Лампа']\nАдрес недоступен: OperationalError, код 14\nНет таблицы: OperationalError, код 1"
        } />
        <p>
          В первом случае SELECT вернул «Лампа». Код <code>14</code> обозначает семейство
          <code> SQLITE_CANTOPEN</code>: SQLite не смогла открыть файл. Третий адрес дал
          <code> SQLITE_ERROR</code> с кодом <code>1</code> при выборе из отсутствующей таблицы.
          Открытие прошло, запрос завершился сбоем. Сравните две последние строки: имя исключения
          совпадает, а число причины отличается.
        </p>
      </Section>

      <Section number="02" title="Как прочитать основной код">
        <p>
          SQLite иногда добавляет к основному коду подробность. Основной код остаётся в младших
          восьми битах. Маска <code>0xFF</code> выделяет эти восемь бит: оператор <code>&amp;</code>
          оставляет единицы, которые есть и в числе, и в маске. Сначала убедитесь, что поле содержит
          целое число. Например, <code>isinstance(code, int)</code> вернёт <code>False</code>
          для <code>None</code>, и к такому значению маску применять не нужно.
        </p>
        <p>
          Возьмём другой сбой каталога: попытку записать уже занятую уникальную метку. Код
          <code> SQLITE_CONSTRAINT_UNIQUE</code> равен <code>2067</code>, а основное семейство
          <code> SQLITE_CONSTRAINT</code> имеет код <code>19</code>.
        </p>
        <CodeBlock caption="Основной код семейства ограничений" code={
          "import sqlite3\n\ncode = 2067\nprimary = code & 0xFF\nprint(primary)\nprint(primary == sqlite3.SQLITE_CONSTRAINT)"
        } />
        <p>
          Выводит <code>19</code> и <code>True</code> на отдельных строках. Примените ту же маску
          к <code>19</code>: число останется прежним. Именованная константа показывает, какое
          семейство проверяет сравнение.
        </p>
        <QuizCard
          question="Какой основной код скрывается в SQLITE_CANTOPEN_ISDIR (526)?"
          description="Примените ту же маску, что в примере с уникальной меткой."
          options={["526", "14", "1"]}
          correctIndex={1}
          explanation="526 & 0xFF даёт 14. Так расширенный код попадает в семейство SQLITE_CANTOPEN. Если поле отсутствует и получился None, выделять младший байт не из чего."
        />
      </Section>

      <Section number="03" title="Закрываем Session и показываем результат">
        <p>
          Запрос или команда сначала проходят обычную работу с Session. Если операция меняла данные,
          прежняя область <code>Session.begin</code> завершает транзакцию до выхода из Session.
          После выхода готовая обвязка распознаёт выбранную причину и передаёт
          <code> StorageError</code> в интерфейсы. Другая ошибка драйвера доходит до внутренней
          границы отказа.
        </p>
        <Diagram caption="От SQLite к ответу" orientation="vertical">
          <DiagramFlow label="Путь сбоя по слоям" orientation="vertical">
            <DiagramNode title="Операция SQLite" shape="database">
              <p>Пытаемся открыть БД и выполнить запрос.</p>
            </DiagramNode>
            <DiagramArrow label="Исключение SQLAlchemy" />
            <DiagramNode title="Граница Session">
              <p>Ресурс закрыт. Дальше можно разобрать код причины.</p>
            </DiagramNode>
            <DiagramArrow label="Два исхода классификации" />
            <DiagramNode title="Результат для API и CLI">
              <p><strong>Код 14:</strong> StorageError, HTTP 503 и сообщение CLI о недоступности.</p>
              <p><strong>Код 1 или поле отсутствует:</strong> внутренняя ошибка, HTTP 500 и общий отказ CLI.</p>
            </DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>
          Прежние HTTP-ветки проходят отдельно: неверный вход даёт 422, корректный неизвестный
          id даёт 404. В этих случаях классификатор ошибки драйвера не нужен.
          Для безопасной диагностики достаточно фиксированного имени причины. Текст
          <code> str(error)</code> может содержать SQL, параметры или пути, поэтому его оставляем
          за границей публичного ответа, вывода CLI и журнала этого отказа.
        </p>
        <CatalogProbe />
        <p>
          В Planner вы будете писать только тело <code>is_sqlite_unavailable(error: DBAPIError) -&gt; bool</code>.
          <code> error</code> получает исключение SQLAlchemy. Аннотация <code>bool</code> означает
          логический результат. <code>True</code> включает штатный перевод в
          <code> StorageError</code>, <code>False</code> оставляет исходную ошибку для внутренней
          границы. Файлы API и CLI уже подключены через готовый патч.
        </p>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          {
            title: "Распознаем недоступность",
            description: "Напишем одно правило выбора по коду и увидим HTTP 503 и штатный отказ CLI на временном адресе.",
          },
          {
            title: "Проверим соседний отказ",
            description: "Файл без таблицы даст общий внутренний отказ. Исправная временная база вернёт задачу.",
          },
        ]} />
        <ProjectPreparation />
        <Callout tone="accent">
          Практика использует временные базы. На рабочем файле Planner ничего менять не нужно.
        </Callout>
      </Section>
    </RichLesson>
  );
}
