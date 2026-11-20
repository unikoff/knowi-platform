import { useId, useState } from "react";
import { CodeBlock, Diagram, DiagramArrow, DiagramFlow, DiagramNode, Lead, LinkedNotes, RichHero, RichLesson, Section } from "../../shared";

const BLOCK_TITLE = "Этап 4 · Блок 16 · Надёжность, запросы и передача работающего Planner";

const CLEAN_START_FILES = {
  "prepare_clean.py": `import argparse
import hashlib
from pathlib import Path, PurePosixPath
import re
import subprocess

PROJECT_FILES = {
    ".env.example", ".gitignore", ".gitkeep", "README.md", "requirements.txt",
    "requirements-dev.txt", "pyproject.toml", "pytest.ini", "setup.cfg",
    "check_sql_only.py", "check_sql_only_local.py", "gauge_experiment.py",
    "tools/planner_acceptance.py",
}
SOURCE_DIRS = {"app", "tests", "docs", "postman"}
SOURCE_EXTENSIONS = {".py", ".md", ".json", ".toml", ".ini", ".txt", ".yaml", ".yml"}
DATA_DIRS = {"archives", "logs", "__pycache__"}


def allowed(name):
    path = PurePosixPath(name)
    if (
        not path.parts or path.is_absolute() or "\\\\" in name or ":" in name
        or ".." in path.parts
    ):
        return False
    if name in PROJECT_FILES:
        return True
    return (
        path.parts[0] in SOURCE_DIRS
        and path.suffix.lower() in SOURCE_EXTENSIONS
        and not any(part.startswith(".") or part.lower() in DATA_DIRS for part in path.parts)
    )


def check_settings(data):
    values = {}
    for line in data.decode("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in {"DATABASE_URL", "COOKIE_SECURE", "APP_NAME"} or key in values:
            raise ValueError("Проверьте имена настроек в .env.example перед копированием.")
        values[key] = value.strip()

    url = values.get("DATABASE_URL", "")
    if not re.fullmatch(r"sqlite:///(?:[\\w-]+/)*[\\w-]+\\.(?:sqlite3?|db)", url):
        raise ValueError("В .env.example нужен локальный относительный SQLite-адрес.")
    if values.get("COOKIE_SECURE", "false").lower() not in {"true", "false"}:
        raise ValueError("Проверьте COOKIE_SECURE в .env.example.")
    if values.get("APP_NAME", "Planner") not in {"Planner", "StudyHub Planner"}:
        raise ValueError("Для образца выберите нейтральное APP_NAME или уберите это поле.")


def main():
    parser = argparse.ArgumentParser(description="Подготовить чистую учебную копию Planner.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--include", action="append", default=[], help="Дополнительный исходник относительно проекта")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    destination = args.destination.resolve()
    if not (source / "README.md").is_file() or not (source / "app").is_dir():
        parser.error("Выберите папку Planner с README.md и app/.")
    if destination.exists() or destination.is_relative_to(source):
        parser.error("Укажите свободный путь вне исходного проекта.")

    files = subprocess.run(
        ["git", "-C", str(source), "ls-files", "-z", "--", "."],
        capture_output=True,
        check=True,
    )
    chosen = {name for name in files.stdout.decode("utf-8").split("\\0") if allowed(name)}
    for name in args.include:
        if not allowed(name) or not (source / name).is_file():
            parser.error("--include принимает путь к существующему исходнику относительно проекта.")
        chosen.add(name)

    inputs = {}
    for name in sorted(chosen):
        path = source / name
        if not path.exists():
            continue
        if path.is_symlink() or path.resolve() != path or not path.is_file():
            parser.error(f"Проверьте обычный файл в проекте: {name}")
        inputs[name] = path.read_bytes()

    if not {"README.md", "requirements.txt"} <= inputs.keys() or not any(
        name.startswith("app/") for name in inputs
    ):
        parser.error("Проверьте опись: нужны README, requirements.txt и исходники app/.")

    print("Опись предлагаемой копии:")
    for name, data in inputs.items():
        print(f"{hashlib.sha256(data).hexdigest()}  {name}")
    if ".env.example" in inputs:
        check_settings(inputs[".env.example"])
    print("Проверьте, что образец, тестовые данные и дополнительные файлы безопасны.")
    if input("Скопировать перечисленные файлы? [y/N] ").strip().lower() != "y":
        return

    destination.mkdir()
    for name, data in inputs.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as file:
            file.write(data)
    print(f"Чистая копия: {destination}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error)) from None
`,
  "checks.md": `# Проверка чистого запуска

Сначала пройдите собственный README. Запишите первый пробел до обращения к справке.
Здесь отдельные команды наблюдения и знакомые точки входа. Они не заменяют README.

## До копирования

Прочитайте опись и убедитесь, что в ней есть всё, на что опираются README и тесты.
Если README вызывает check_sql_only.py или check_sql_only_local.py, найдите этот
файл в описи. Сверьте также tools/planner_acceptance.py из предыдущего занятия,
если сохранили его в своём Planner.
Опись состоит из отпечатка SHA256 и относительного пути. Она описывает байты
в рабочей папке, включая проверенные локальные правки. Для намеренного нового
исходника передайте --include и его путь от корня проекта, например
--include "tests/test_local_case.py". Другие файлы включайте только после сверки.
Просмотрите содержимое передаваемых настроек и фикстур. Фильтр архива работает
по именам и типам файлов. Он не умеет распознавать данные внутри всех файлов.

## Интерпретатор и окружение

Используйте Python новой среды. В PowerShell это проверяется так:
    .\\.venv\\Scripts\\python.exe -c "import sys; print(sys.executable)"

До выбора локального адреса проверьте наличие переопределений без вывода значений:
    Test-Path Env:DATABASE_URL
    Test-Path Env:APP_NAME
    Test-Path Env:COOKIE_SECURE
    Test-Path Env:PYTHONPATH
    Test-Path Env:PYTHONHOME

В каждом терминале для чистого запуска снимите только обнаруженные
переопределения, которые не предусмотрены README. Например:
    Remove-Item Env:DATABASE_URL

Эта команда меняет переменную только в данном PowerShell. Повторите проверку
в терминале API, если открыли его отдельно. Когда README предусматривает
настройку через переменную, назначьте в обоих окнах новый учебный адрес.
При настройке через файл оба запуска читают один новый .env из корня проекта.

## Новая SQLite и тесты

До запуска убедитесь, что путь выбранной SQLite ведёт в чистую копию, а сам файл
ещё отсутствует. Подставьте фактический локальный путь в Test-Path. Например:
    Test-Path .\\readme-probe.sqlite3

У локального URL sqlite:///readme-probe.sqlite3 часть после sqlite:/// обозначает
файл. В поставке Planner относительный путь разрешается от корня проекта.
Не используйте URL рабочей папки. В готовом образце допустим только учебный адрес.
Изолированные pytest-проверки запускаются из корня той же новой средой:
    .\\.venv\\Scripts\\python.exe -m pytest -q

## Живые интерфейсы

Используйте команды своего README. Если инструкция требует восстановления,
сверьте с кодом имя модуля CLI (app.main), init (app.DBase.init_db) и приложения
Uvicorn (app.api:app). Для API используйте 127.0.0.1 и свободный порт. Команда
Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue
поможет проверить, занят ли порт 8000. Если порт занят, выберите другой.
При остановке нажмите Ctrl+C в терминале именно своего Uvicorn и дождитесь
возврата приглашения. Запускайте API без --reload. Запишите PID из сообщения
о старте, чтобы наблюдать запуск нового процесса при повторе.

В браузере откройте /docs на своём локальном API. У нужного метода нажмите
Try it out, заполните поля, затем нажмите Execute. В POST /tasks title содержит
название, priority содержит целое число. В GET /tasks/{task_id} укажите
фактический id задачи. Читайте статус и JSON из ответа. GET /health подтверждает
ответ процесса, GET /tasks читает список. В CLI введите find или done, затем
введите id по приглашению. exit завершает только ваш CLI.

## Передача

Сохраните исходники, объявленные зависимости, проверки, README и безопасный
образец настроек. Сверьте опись с файлами, которые появились за время прогона.
Оставьте локально .venv, .env, файлы SQLite и их служебные файлы, архивы и журналы.
`,
  "run-notes.md": `# Запись чистого прогона Planner

- Исходная папка и опись проверенных файлов:
- Папка чистого запуска, интерпретатор и установленные зависимости:
- Учебный адрес SQLite и состояние до первого init:
- Команда init и первое чтение:
- Первая недостающая команда или неожиданный результат:
- Команда готовой регрессии и фактический итог:
- HTTP/CLI до повтора, поля задачи:
- HTTP/CLI после второго init и нового API-процесса, поля задачи:
- Подтверждение README либо точечная правка и результат перепроверки:
- Образец настроек и итоговый состав передачи:
`,
};

const STOCK_FILES = {
  "README.md": `# Склад инструментов: исходная инструкция

Откройте PowerShell в папке stock-demo:
    py -3.12 -m venv .venv
    .\\.venv\\Scripts\\python.exe --version
    .\\.venv\\Scripts\\python.exe -m pip install -r requirements-dev.txt
    Copy-Item .env.example .env
    .\\.venv\\Scripts\\python.exe stock.py list
    .\\.venv\\Scripts\\python.exe stock.py init

Для API используйте .\\.venv\\Scripts\\python.exe -m uvicorn stock_api:app --host 127.0.0.1 --port 8010.
Проходите готовые тесты через .\\.venv\\Scripts\\python.exe -m pytest -q.
`,
  ".env.example": "STOCK_DB=stock-demo.sqlite3\n",
  "requirements.txt": "fastapi\nuvicorn\n",
  "requirements-dev.txt": "-r requirements.txt\npytest\n",
  "stock.py": `import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3


ROOT = Path(__file__).resolve().parent
DEFAULT_DB = "stock-demo.sqlite3"


def chosen_path():
    env_file = ROOT / ".env"
    file_value = DEFAULT_DB
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("STOCK_DB="):
                file_value = line.split("=", 1)[1].strip()
    return ROOT / os.getenv("STOCK_DB", file_value)


def init(path):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")


def add(path, name):
    with closing(sqlite3.connect(path)) as db, db:
        cursor = db.execute("INSERT INTO items (name) VALUES (?)", (name,))
        return {"id": cursor.lastrowid, "name": name}


def items(path):
    with closing(sqlite3.connect(path)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT id, name FROM items ORDER BY id")]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["init", "list", "add"])
    parser.add_argument("name", nargs="?")
    args = parser.parse_args()
    path = chosen_path()
    if args.command == "init":
        init(path)
    elif args.command == "list":
        print(json.dumps(items(path), ensure_ascii=False))
    else:
        if not args.name:
            parser.error("Для add нужно название.")
        print(json.dumps(add(path, args.name), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except sqlite3.OperationalError as error:
        raise SystemExit(str(error)) from None
`,
  "stock_api.py": `from fastapi import FastAPI

from stock import chosen_path, items


app = FastAPI()


@app.get("/items")
def read_items():
    return items(chosen_path())
`,
  "tests/test_stock.py": `from stock import add, init, items


def test_init_preserves_items(tmp_path):
    file = tmp_path / "probe.sqlite3"
    init(file)
    assert items(file) == []
    row = add(file, "Ключ 10")
    init(file)
    assert items(file) == [row]
`,
};

type ArchiveEntries = Record<string, string>;

function crc32(bytes: Uint8Array): number {
  let checksum = 0xffffffff;
  for (const byte of bytes) {
    checksum ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      checksum = (checksum >>> 1) ^ ((checksum & 1) ? 0xedb88320 : 0);
    }
  }
  return (checksum ^ 0xffffffff) >>> 0;
}

function makeDownloadArchive(folder: string, entries: ArchiveEntries): Blob {
  const encoder = new TextEncoder();
  const localParts: BlobPart[] = [];
  const directory: BlobPart[] = [];
  const files = Object.entries(entries);
  let fileOffset = 0;
  let directorySize = 0;

  // Stored ZIP entries keep the original UTF-8 text bytes.
  for (const [file, content] of files) {
    const filename = encoder.encode(folder + "/" + file);
    const data = encoder.encode(content);
    const checksum = crc32(data);

    const header = new Uint8Array(30 + filename.length);
    const local = new DataView(header.buffer);
    local.setUint32(0, 0x04034b50, true);
    local.setUint16(4, 20, true);
    local.setUint16(6, 0x0800, true);
    local.setUint16(12, 33, true);
    local.setUint32(14, checksum, true);
    local.setUint32(18, data.length, true);
    local.setUint32(22, data.length, true);
    local.setUint16(26, filename.length, true);
    header.set(filename, 30);

    const entry = new Uint8Array(46 + filename.length);
    const central = new DataView(entry.buffer);
    central.setUint32(0, 0x02014b50, true);
    central.setUint16(4, 20, true);
    central.setUint16(6, 20, true);
    central.setUint16(8, 0x0800, true);
    central.setUint16(14, 33, true);
    central.setUint32(16, checksum, true);
    central.setUint32(20, data.length, true);
    central.setUint32(24, data.length, true);
    central.setUint16(28, filename.length, true);
    central.setUint32(42, fileOffset, true);
    entry.set(filename, 46);

    localParts.push(header, data);
    directory.push(entry);
    fileOffset += header.length + data.length;
    directorySize += entry.length;
  }

  const trailer = new Uint8Array(22);
  const end = new DataView(trailer.buffer);
  end.setUint32(0, 0x06054b50, true);
  end.setUint16(8, files.length, true);
  end.setUint16(10, files.length, true);
  end.setUint32(12, directorySize, true);
  end.setUint32(16, fileOffset, true);
  return new Blob([...localParts, ...directory, trailer], { type: "application/zip" });
}

function saveArchive(name: string, folder: string, files: ArchiveEntries) {
  const url = URL.createObjectURL(makeDownloadArchive(folder, files));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const STOCK_ACTIONS = [
  { id: "list", title: "Прочитать список" },
  { id: "address", title: "Задать путь к учебной SQLite" },
  { id: "init", title: "Явно создать таблицу" },
  { id: "deps", title: "Установить зависимости" },
  { id: "venv", title: "Создать новую venv" },
] as const;

type StockAction = typeof STOCK_ACTIONS[number]["id"];

function tryStockInstructions(steps: StockAction[]): { success: boolean; text: string } {
  const completed = new Set<StockAction>();
  for (const step of steps) {
    if (step !== "venv" && step !== "address" && !completed.has("venv")) {
      return {
        success: false,
        text: "Первый сбой: Python по пути .venv отсутствует. В рабочей папке этот путь мог остаться от прежнего запуска.",
      };
    }
    if (step === "list" || step === "init") {
      if (!completed.has("deps")) {
        return {
          success: false,
          text: "Python найден, но зависимости склада ещё не установлены. В старой среде пакеты могли остаться от прошлой установки.",
        };
      }
      if (!completed.has("address")) {
        return {
          success: false,
          text: "Учебный адрес пока не задан. Мы не можем подтвердить, что команда работает именно с выбранным файлом.",
        };
      }
      if (step === "list" && !completed.has("init")) {
        return {
          success: false,
          text: "no such table: items. В чистой копии таблицы ещё нет. В рабочей папке тот же порядок мог сработать с готовой SQLite.",
        };
      }
    }
    completed.add(step);
  }

  return completed.has("list")
    ? { success: true, text: "Первое чтение вернуло []. Явный init создал таблицу в выбранном файле, а строк в ней пока нет." }
    : { success: false, text: "Дойдите до первого чтения, чтобы увидеть, какую таблицу подготовила инструкция." };
}

function StockReadmeExercise() {
  const titleId = useId();
  const [steps, setSteps] = useState<StockAction[]>([]);
  const [checked, setChecked] = useState(false);
  const outcome = checked ? tryStockInstructions(steps) : null;

  return (
    <section className="sequence-card" aria-labelledby={titleId}>
      <h3 id={titleId}>Порядок команд для склада</h3>
      <p className="interactive-prompt">
        Добавляйте действия в том порядке, в каком будете читать README. Повторное нажатие убирает шаг.
        Предскажите, какая команда первой обнаружит пропуск, и проверьте прогноз.
      </p>
      <div className="sequence-workspace">
        <div className="sequence-panel">
          <div className="sequence-panel-heading">
            <span className="sequence-label">Действия</span>
          </div>
          <div className="sequence-pieces">
            {STOCK_ACTIONS.map(({ id, title }) => (
              <button
                key={id}
                type="button"
                className="sequence-piece answer-option"
                aria-pressed={steps.includes(id)}
                onClick={() => {
                  setSteps(current => current.includes(id) ? current.filter(step => step !== id) : [...current, id]);
                  setChecked(false);
                }}
              >
                <span className="sequence-text">{title}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="sequence-panel sequence-panel--target">
          <div className="sequence-panel-heading">
            <span className="sequence-label">Порядок в README</span>
          </div>
          <div aria-live="polite">
            {steps.length === 0 ? <p className="sequence-empty">Выберите первый шаг.</p> : (
              <ol>
                {steps.map(id => <li key={id}>{STOCK_ACTIONS.find(action => action.id === id)?.title}</li>)}
              </ol>
            )}
          </div>
        </div>
      </div>
      <div className="interactive-actions">
        <button className="interactive-secondary" type="button" onClick={() => {
          setSteps([]);
          setChecked(false);
        }} disabled={steps.length === 0}>Сбросить</button>
        <button className="interactive-primary" type="button" onClick={() => setChecked(true)} disabled={steps.length === 0}>
          Проверить порядок
        </button>
      </div>
      {outcome && (
        <p className={`interactive-feedback ${outcome.success ? "is-correct" : "is-wrong"}`} role="status">
          {outcome.text}
        </p>
      )}
    </section>
  );
}

const STOCK_START = String.raw`py -3.12 -m venv .venv
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
Test-Path .\stock-demo.sqlite3`;

const STOCK_READ = String.raw`.\.venv\Scripts\python.exe stock.py list
# no such table: items`;

const STOCK_FIXED = String.raw`.\.venv\Scripts\python.exe stock.py init
.\.venv\Scripts\python.exe stock.py list
# []`;

const STOCK_API = String.raw`.\.venv\Scripts\python.exe -m uvicorn stock_api:app --host 127.0.0.1 --port 8010`;

const STOCK_REPEAT = String.raw`.\.venv\Scripts\python.exe stock.py add "Ключ 10"
# {"id": 1, "name": "Ключ 10"}

# После остановки API повторяем init и чтение.
.\.venv\Scripts\python.exe stock.py init
.\.venv\Scripts\python.exe stock.py list
# [{"id": 1, "name": "Ключ 10"}]

.\.venv\Scripts\python.exe -m pytest -q
# 1 passed`;

const PREPARE_COPY =
  'py "../planner-clean-start/prepare_clean.py" --source "." --destination "../planner-clean-copy"';

export function Lesson80({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title="Запускаем Planner по своему README из чистой копии"
        intro="Представим, что другой разработчик получил исходники и вашу инструкцию. Проверим, сможет ли он с их помощью запустить оба интерфейса на новой SQLite."
      />

      <Section number="00" title="Можно ли повторить наш запуск">
        <Lead>
          В своей папке мы уже провели одну задачу через API, CLI и новый API-процесс. Теперь тот же проект
          запустит человек, у которого ещё нет нашего окружения и базы.
        </Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "API и CLI читают одну задачу из общей SQLite и видят её после перезапуска." },
          { title: "Новый вопрос", description: "Хватит ли README, чтобы установить пакеты, создать таблицу и открыть оба интерфейса с чистого места?" },
          { title: "Результат", description: "Повторим запуск на новой учебной SQLite и убедимся, что пробная задача пережила повторную инициализацию." },
        ]} />
        <p>Знакомое поведение даёт нам ориентир. Теперь проверим, какие условия для запуска описаны в самой инструкции.</p>
      </Section>

      <Section number="01" title="Что попадает в чистую копию">
        <Lead>
          Рабочая папка помнит предыдущий запуск. В ней уже могут быть установленные пакеты, настройки и созданная таблица.
          Чистая копия начинается с файлов, которые получит следующий разработчик.
        </Lead>
        <p>Посмотрим на две папки одного склада инструментов. Код в них одинаковый. Различается то, что успело появиться вокруг него.</p>
        <Diagram caption="Рабочая папка и чистая копия склада">
          <DiagramFlow label="Что передаём и что появится при запуске">
            <DiagramNode title="Рабочая папка">
              <span>В рабочей папке уже есть <code>.venv</code>, <code>.env</code> и база с таблицей.</span>
            </DiagramNode>
            <DiagramArrow label="Только файлы проекта" />
            <DiagramNode title="Чистая копия">
              <span>Исходники, README, зависимости, тесты и <code>.env.example</code>.</span>
            </DiagramNode>
            <DiagramArrow label="Команды README" />
            <DiagramNode title="Новый запуск">
              <span>Появятся новая <code>.venv</code>, локальный <code>.env</code> и SQLite с исходной таблицей.</span>
            </DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>
          В образце <code>.env.example</code> указываем имена настроек и безопасные учебные значения. Сам образец ещё
          не задаёт адрес для процесса. README объясняет, как из него получить локальный <code>.env</code> или
          передать настройки через переменные среды. Переменная процесса может иметь приоритет над файлом.
        </p>
        <p>
          Опись копии перечисляет фактически выбранные пути. Это отдельная проверка от <code>.gitignore</code>:
          ignore помогает исключать файлы при добавлении в Git, а содержимое передаваемой папки видно по описи.
          Рабочие настройки, базы, архивы и журналы с данными оставляем у себя.
        </p>
      </Section>

      <Section number="02" title="Первое чтение на складе">
        <Lead>
          Исходный README склада предлагает прочитать список инструментов до создания таблицы. На рабочем месте
          таблица уже есть, поэтому ошибка в порядке незаметна. Что произойдёт с тем же кодом в чистой папке?
        </Lead>
        <p>
          В папке <code>stock-demo</code> уже лежат CLI <code>stock.py</code>, API <code>stock_api.py</code>,
          две ведомости зависимостей, образец настроек и тест. Один и тот же файл SQLite хранит таблицу <code>items</code> с полями
          <code> id</code> и <code>name</code>. Посмотрим на команды и их результат. При желании можно скачать готовый
          пример и повторить опыт в свободной папке.
        </p>
        <button type="button" className="interactive-secondary" onClick={() => saveArchive("stock-demo.zip", "stock-demo", STOCK_FILES)}>
          Скачать пример склада
        </button>

        <h3>С какого Python начинается запуск</h3>
        <p>
          В PowerShell из <code>stock-demo</code> создаём отдельную <code>.venv</code>.
          Ключ <code>-m</code> запускает модуль тем Python, чей путь указан в команде.
          Используем его и для установки. <code>requirements-dev.txt</code> включает основной <code>requirements.txt</code>
          и добавляет pytest. В образце задан <code>STOCK_DB=stock-demo.sqlite3</code>. Копируем образец в <code>.env</code>
          и проверяем наличие файла SQLite. В чистой папке <code>Test-Path</code> вернёт <code>False</code>.
        </p>
        <CodeBlock code={STOCK_START} />

        <h3>Что обнаруживает чистая папка</h3>
        <p>Выполняем команду чтения из исходной инструкции.</p>
        <CodeBlock code={STOCK_READ} />
        <p>
          SQLite смогла открыть файл. При открытии файл мог появиться, но внутри ещё нет таблицы <code>items</code>.
          Поэтому чтение завершилось ошибкой. Пустой список означал бы, что таблица доступна и в ней пока нет строк.
        </p>
        <p>
          Уточняем в README склада порядок двух команд. Для чистой перепроверки берём новую распакованную копию
          и повторяем настройку с начала. Явный <code>init</code> теперь идёт перед чтением.
        </p>
        <CodeBlock code={STOCK_FIXED} />

        <h3>Что остаётся после повтора</h3>
        <p>
          Запускаем API в отдельном терминале из той же папки. GET <code>/items</code> пока возвращает <code>[]</code>.
          Через CLI добавляем «Ключ 10». Первый GET после записи возвращает список из одной строки с <code>id: 1</code>.
        </p>
        <CodeBlock code={STOCK_API} />
        <p>
          После <code>add</code> записываем id и название. Останавливаем свой API через Ctrl+C, повторяем init с тем
          же адресом и сравниваем список в CLI. Перезапускаем API той же командой и повторяем GET.
          Список снова содержит ту же строку. Init создаёт отсутствующую таблицу, а при повторе оставляет строки в
          уже совместимой исходной схеме. Тест запускается со своим отдельным файлом SQLite.
        </p>
        <CodeBlock code={STOCK_REPEAT} />
        <StockReadmeExercise />
      </Section>

      <Section number="03" title="По каким наблюдениям судить о README">
        <Lead>
          При прогоне ищем первый шаг, на котором действительность расходится с инструкцией. Это помогает исправить
          конкретную строку и повторить именно ту проверку, которая выявила пробел.
        </Lead>
        <p>
          Если тесты запустились только после установки пакета вручную, повторяем проверку установки в новой среде.
          Если пропуск init обнаружили на первом чтении, берём новую учебную SQLite и проверяем порядок заново.
          При ошибке чтения после успешного init сверяем адрес: два процесса могут открывать разные файлы с одинаковыми
          именами. Закрываем только свои проверочные процессы.
        </p>
        <p>
          В Planner адрес задаёт <code>DATABASE_URL</code>. Относительный SQLite-путь в этой сборке отсчитывается
          от корня проекта, где есть <code>app/</code> и <code>tests/</code>. Для прогона выбираем новый локальный
          файл в чистой копии. Например, в <code>sqlite:///readme-probe.sqlite3</code> часть после трёх слешей
          означает имя файла. До init проверяем местоположение и отсутствие файла.
        </p>
        <LinkedNotes items={[
          { title: "Интерпретатор", description: "Путь к исполняемому файлу Python показывает, какая среда установила пакеты и выполнила команды." },
          { title: "Ответ процесса", description: "Health подтверждает, что отвечает выбранный API. Первое чтение задач подтверждает доступ к подготовленной таблице." },
          { title: "Сохранённая запись", description: "Сравниваем один фактический id и поля до повтора и после запуска нового API. CLI и API должны читать из одного файла." },
        ]} />
        <p>
          Готовые тесты проверяют прежнее поведение на изолированных данных. Их результат записываем рядом с
          живым запуском. Когда все указания README уже приводят к этим наблюдениям, достаточно подтвердить их.
          Явный init рассчитан на исходную схему. Изменение уже существующей схемы потребует отдельного шага.
        </p>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <Lead>
          Берём свой уже проверенный Planner. На отдельной копии выясним, хватает ли README новому разработчику.
        </Lead>
        <LinkedNotes items={[
          { title: "Запустим свой README с чистого места", description: "Проверим, что в новой папке инструкция приводит к новому Python и первому чтению после явного init." },
          { title: "Проверим запуск и состав передачи", description: "Сверим пробную задачу после повторного init и нового процесса и проверим, что в описи есть безопасный образец настроек." },
        ]} />
        <p>
          Записывайте наблюдения по ходу работы. Они покажут, какой фрагмент инструкции нужно уточнить, или подтвердят,
          что она уже достаточна. Для продолжения сохраните учебный адрес и сведения о пробной записи у себя.
        </p>
        <h3>Проверка чистого запуска</h3>
        <p>
          Скачайте комплект и распакуйте папку <code>planner-clean-start</code> рядом со своим проектом.
          Откройте PowerShell в исходном Planner, в папке с <code>README.md</code> и <code>app/</code>.
          В примере ниже <code>--source "."</code> означает текущую папку, а <code>--destination</code> задаёт
          новый соседний каталог. Если имя уже занято, выберите свободное. Кавычки сохраняют путь с пробелами как один аргумент.
        </p>
        <button type="button" className="interactive-secondary" onClick={() => saveArchive(
          "planner-clean-start-kit.zip",
          "planner-clean-start",
          CLEAN_START_FILES,
        )}>
          Скачать комплект «Проверка чистого запуска»
        </button>
        <CodeBlock code={PREPARE_COPY} />
        <p>
          Помощник берёт исходные файлы из списка Git и читает их текущие байты. Для нужного нового исходника,
          которого ещё нет в списке, можно добавить <code>--include</code> и путь от корня проекта.
          Если README запускает <code>check_sql_only.py</code> или <code>check_sql_only_local.py</code>, найдите это имя в описи.
          Сверьте и <code>tools/planner_acceptance.py</code>, если сохранили его после прошлого занятия.
          Перед ответом <code>y</code> прочитайте опись и проверьте безопасность образца и синтетических фикстур.
          Если помощник остановился на образце, проверьте учебный адрес в <code>.env.example</code> и повторите подготовку.
          В новой папке откройте собственный README. Рядом в комплекте есть <code>checks.md</code> с командами проверки
          адреса, Python, HTTP и CLI и <code>run-notes.md</code> для ваших наблюдений.
        </p>
      </Section>
    </RichLesson>
  );
}
