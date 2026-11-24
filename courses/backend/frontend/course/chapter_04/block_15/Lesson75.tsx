import { useState } from "react";
import {
  Callout,
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

const SNAPSHOT = `import json
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

with TemporaryDirectory() as folder:
    desk = Path(folder) / "loans.json"
    rehearsal = Path(folder) / "rehearsal.json"
    archive = Path(folder) / "archive.json"
    rows = [{"id": 8, "item": "Штатив", "days": 2},
            {"id": 11, "item": "Кабель", "days": 1}]
    rehearsal.write_text(json.dumps(rows), encoding="utf-8")

    rows[0]["days"] = 4
    desk.write_text(json.dumps(rows), encoding="utf-8")
    # С этого места оба пункта уже закрыты для записей.
    copyfile(desk, archive)
    for candidate in (rehearsal, archive):
        print(candidate.name, candidate.read_bytes() == desk.read_bytes())`;

const IMPORT_COMPARE = `expected = {
    8: {"item": "Штатив", "labels": ["выдача"], "key": "штатив"},
    11: {"item": "Кабель", "labels": [], "key": "кабель"},
}
# Эти строки прочитаны из нового журнала после переноса.
actual = {
    8: {"item": "Штатив", "labels": [], "key": "штатив"},
    11: {"item": "Кабель", "labels": [], "key": "кабель"},
}

print("Номера совпали:", expected.keys() == actual.keys())
for number in expected.keys() & actual.keys():
    for field, value in expected[number].items():
        observed = actual[number][field]
        if observed != value:
            print(number, field, value, "→", observed)`;

const OPENED_DATABASE = `import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

def read_loan(file, number):
    with closing(sqlite3.connect(file)) as connection:
        listing = connection.execute("PRAGMA database_list")
        opened = next((name for _, schema, name in listing
                       if schema == "main"), "")
        loan = connection.execute(
            "SELECT item FROM loans WHERE id = ?", (number,)
        ).fetchone()
        return Path(opened).resolve() if opened else None, loan

with TemporaryDirectory() as folder:
    selected = Path(folder) / "open.db"
    mirror = Path(folder) / "mirror.db"
    for path in (selected, mirror):
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE loans (id INTEGER, item TEXT)")
            db.execute("INSERT INTO loans VALUES (11, 'Кабель')")

    opened_a, row_a = read_loan(selected, 11)
    opened_b, row_b = read_loan(mirror, 11)
    print("Выдача:", row_a == row_b)
    print("Выбранный файл:", opened_a == selected.resolve(),
          opened_b == selected.resolve())`;

const SAVE_AND_READ = `import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

read_status = """
import sqlite3
import sys
from contextlib import closing

with closing(sqlite3.connect(sys.argv[1])) as db:
    row = db.execute(
        "SELECT returned FROM loans WHERE id = ?", (11,)
    ).fetchone()
    print("Выдача 11, возвращена:", bool(row[0]))
"""
with TemporaryDirectory() as folder:
    path = Path(folder) / "loans.db"
    with closing(sqlite3.connect(path)) as writer, writer:
        writer.execute("CREATE TABLE loans (id INTEGER, returned INTEGER)")
        writer.execute("INSERT INTO loans VALUES (11, 0)")
    subprocess.run([sys.executable, "-c", read_status, str(path)], check=True)

    with closing(sqlite3.connect(path)) as writer, writer:
        writer.execute("UPDATE loans SET returned = 1 WHERE id = 11")
    subprocess.run([sys.executable, "-c", read_status, str(path)], check=True)`;

const ARCHIVE_PROBE = `import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

def counter_view(file):
    try:
        with closing(sqlite3.connect(file)) as db:
            return db.execute("SELECT item FROM loans WHERE id = 11").fetchone()[0]
    except sqlite3.Error:
        return "Журнал недоступен"

def inspect_old_export(file):
    return file.read_text(encoding="utf-8")

def counter_with_old_sign(file, export):
    inspect_old_export(export)
    return counter_view(file)

with TemporaryDirectory() as folder:
    root = Path(folder)
    journal = root / "loans.db"
    archive = root / "paper-export.txt"
    with closing(sqlite3.connect(journal)) as db, db:
        db.execute("CREATE TABLE loans (id INTEGER, item TEXT)")
        db.execute("INSERT INTO loans VALUES (11, 'Кабель')")

    try:
        counter_with_old_sign(journal, archive)
    except FileNotFoundError:
        print("Старое обращение помешало чтению")
    print("Без архива:", counter_view(journal))
    archive.write_text("АРХИВНАЯ ВЫДАЧА 90", encoding="utf-8")
    print("Старый читатель:", inspect_old_export(archive))
    print("Недоступная база:", counter_view(root))`;

const WORKING_FIXTURE = `[
  {"id": 34, "title": "Купить бумагу", "priority": 4, "is_done": false, "tags": ["офис"]},
  {"id": 52, "title": "Уточнить срок", "priority": 1, "is_done": true, "tags": ["звонок"]}
]`;

const RUN_SETTINGS = `$env:JSON_PATH = "data/tasks.json"
$env:DATABASE_URL = "sqlite:///data/cutover.sqlite3"

# Терминал API
python -m uvicorn app.api:app --host 127.0.0.1 --port 8765

# Другой терминал: те же переменные, затем CLI
python -m app.main`;

const SOURCE_OBSERVATION = `python -c "from app.core.config import load_settings; print(load_settings().json_path.resolve())"
python -c "from pathlib import Path; import sys; print(Path(sys.argv[1]).read_bytes() == Path(sys.argv[2]).read_bytes())" <источник> <архив>`;

const IMPORT_COMMANDS = `python -m app.DBase.init_db
python -m app.DBase.import_tasks --source "<подтверждённый архив>" --database "<путь SQLite>"`;

const COMPARE_ROWS = `import json
import sqlite3
from contextlib import closing
from pathlib import Path

def inspect_import(database, snapshot=None):
    location = Path(database).resolve().as_uri() + "?mode=ro"
    fields = ("id", "title", "priority", "is_done", "tags", "title_normalized")
    with closing(sqlite3.connect(location, uri=True)) as db:
        rows = db.execute("SELECT " + ", ".join(fields) + " FROM tasks")
        actual = {
            i: (i, title, priority, bool(done), json.loads(tags), key)
            for i, title, priority, done, tags, key in rows
        }
    print("Строк в tasks:", len(actual))
    if snapshot is None:
        return
    records = json.loads(Path(snapshot).read_text(encoding="utf-8"))
    expected = {
        r["id"]: (r["id"], r["title"], r["priority"], r["is_done"],
                  r.get("tags", []), r["title"].lower())
        for r in records
    }
    print("Только в архиве:", sorted(expected.keys() - actual.keys()))
    print("Только в SQLite:", sorted(actual.keys() - expected.keys()))
    differences = 0
    for i in sorted(expected.keys() & actual.keys()):
        for field, old, new in zip(fields, expected[i], actual[i]):
            if old != new:
                print(i, field, repr(old), "→", repr(new))
                differences += 1
    print("Различий в полях:", differences)`;

const OBSERVE_OPEN = `from pathlib import Path
from sqlalchemy import event
from sqlalchemy.orm import Session

@event.listens_for(Session, "after_begin")
def show_open_file(session, transaction, connection):
    for _, schema, filename in connection.exec_driver_sql("PRAGMA database_list"):
        if schema == "main":
            print("main =", Path(filename).resolve() if filename else "(без файла)")
`;

const PROBE_START = `# В оболочке Python в терминале API, после кода наблюдателя
from app.api import app
from uvicorn import run
run(app, host="127.0.0.1", port=8765)

# В отдельной оболочке Python для CLI, после того же наблюдателя
from app.main import main
main()`;

const cases = [
  {
    label: "Старый снимок",
    question: "Последняя заявка совпадает. Что ещё удерживает переход?",
    description: "В рабочем реестре №8 выдан на 4 дня, в архиве на 2. №11 совпадает.",
    correctIndex: 0,
    explanation: "Архив хранит старую версию №8. Полная сверка с остановленным источником заметит различие до импорта.",
    observation: "№8 · срок 4 дня → 2 дня",
  },
  {
    label: "Два файла",
    question: "Пункты видят заявку №11. Можно ли начать новую запись?",
    description: "Первый пункт открыл open.db, второй открыл mirror.db. Целью выбрали open.db.",
    correctIndex: 1,
    explanation: "Оба видят прежнюю заявку, потому что импорт совпал. Сверка открытых путей задерживает запись до исправления второго пункта.",
    observation: "Цель · open.db · второй пункт · mirror.db",
  },
  {
    label: "Недоступный журнал",
    question: "Какой результат подтверждает выход старого пути?",
    description: "SQLite указывает на каталог. Старый экспорт по привычному адресу читается и содержит выдачу №90.",
    correctIndex: 2,
    explanation: "Рабочая операция должна сообщить об отказе. Появление архивной выдачи №90 означало бы, что приложение перешло к старому экспорту.",
    observation: "Отказ операции · выдача №90 отсутствует",
  },
];

export function Lesson75() {
  const [caseIndex, setCaseIndex] = useState(0);
  const scenario = cases[caseIndex];

  return (
    <RichLesson>
      <RichHero
        title="Переводим Planner на одну SQLite"
        intro="Перенесём актуальные задачи, затем проверим, что API и CLI работают с одной базой и обходятся без старого хранилища."
      />
      <Section number="00" title="Вступление">
        <Lead>Во время репетиции отдельная SQLite приняла задачи из JSON. После репетиции обычный Planner снова читает JSON. За это время задачи могли измениться.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Пробный перенос проверен, обычные API и CLI снова используют JSON." },
          { title: "Новый вопрос", description: "Как перевести обе точки на одну SQLite и проверить их поведение при недоступной базе?" },
          { title: "Результат", description: "Свежий процесс увидит новое изменение. При недоступной SQLite обе точки сообщат об отказе, даже если старый файл читается." },
        ]} />
        <p>Начнём с журнала выдачи оборудования. Два пункта принимают заявки. Пока один заканчивает работу, другой ещё может поменять запись о штативе.</p>
      </Section>

      <Section number="01" title="Замораживаем источник и проверяем архив">
        <Lead>Заморозить источник значит остановить все процессы, которые могут в него писать. Тогда копия содержит данные одного известного момента.</Lead>
        <p>На репетиции штатив №8 был выдан на два дня. Второй пункт продлил его до четырёх дней уже после репетиции. Последняя запись о кабеле №11 осталась прежней.</p>
        <p>Возьмём текущий журнал после ухода обоих сотрудников. <code>copyfile</code> переносит содержимое в отдельный файл, а <code>read_bytes()</code> читает файл целиком, как последовательность байтов. В примере временный каталог сам готовит и убирает файлы.</p>
        <CodeBlock code={SNAPSHOT} caption="Журнал выдачи · старые и свежие байты" />
        <p>На экране <code>rehearsal.json False</code> и <code>archive.json True</code>. Пробный файл устарел из-за №8. Новый архив совпал байт в байт, включая форматирование. В Planner сначала выясним, какой JSON реально открывали обычные процессы. После их завершения выберем для архива свободное имя. Свежий снимок сохранится отдельно от пробного.</p>
      </Section>

      <Section number="02" title="Сверяем содержимое после импорта">
        <Lead>После копирования мы знаем, что архив точный. После импорта проверяем уже другое: сохранилось ли каждое значение в новом хранилище.</Lead>
        <p>Бумажную ведомость можно отсканировать без потерь, а при переносе в журнал пропустить пометку «выдача». Сначала сравним номера. Затем <code>&amp;</code> оставит общие ключи, и у каждого номера сравним значения по полям. При расхождении в номерах или значениях остановимся до переключения.</p>
        <CodeBlock code={IMPORT_COMPARE} caption="Журнал выдачи · сравнение двух представлений" />
        <p>Получаем <code>Номера совпали: True</code> и расхождение <code>8 labels ['выдача'] → []</code>. Значит, перенос ещё нельзя считать точным. Если пропал сам номер, это покажет уже первая строка.</p>
        <p>В Planner вернёмся к подготовленной в репетиции пустой <code>tasks</code> и явному импорту. Свежим чтением сравним <code>id</code>, <code>title</code>, <code>priority</code>, <code>is_done</code>, <code>tags</code> и вычисленное поле <code>title_normalized</code>. Для последнего импортёр берёт сохранённое название и вызывает <code>.lower()</code>.</p>
      </Section>

      <Section number="03" title="Подключаем оба входа и проверяем открытый файл">
        <Lead>Два пункта могут открыть два журнала с одинаковыми заявками. Для новой записи им нужен один и тот же физический файл.</Lead>
        <p>У выбранного адреса есть владелец. Точка запуска принимает адрес, открывает через него ресурс и передаёт ресурс операции. В примере каждого пункта <code>read_loan</code> получает выбранный файл и открывает его. <code>closing</code> закрывает соединение после запроса. Обе операции читают №11.</p>
        <p><code>PRAGMA database_list</code> спрашивает текущее соединение, какие базы оно открыло. В каждой строке есть порядковый номер, имя базы и имя файла. Нас интересует <code>main</code>, основная база. Пустое имя файла бывает у базы в памяти. <code>Path.resolve()</code> приводит найденное имя и выбранный путь к абсолютному виду.</p>
        <CodeBlock code={OPENED_DATABASE} caption="Два файла, одна и та же заявка №11" />
        <p><code>Выдача: True</code> сообщает, что строки совпали. <code>Выбранный файл: True False</code> показывает, что второй пункт открыл зеркало. Тот же id пригоден для проверки данных. Для допуска записи сравним оба открытых пути с одной заранее выбранной целью.</p>
        <p>В Planner адрес приходит из <code>DATABASE_URL</code>. Engine и фабрика Session обслуживают срок жизни запуска. Поставщик сервиса API берёт Session на запрос, а SQL-контекст CLI берёт Session на команду. Репозиторий и уже знакомая область записи получают одну Session. Проследим существующие связи от каждой точки входа, сохраняя её способ закрыть ресурс.</p>
        <Diagram caption="Какие свидетельства нужны до первой записи">
          <DiagramLane title="Подготовка данных">
            <DiagramTrack label="Источник → SQLite" items={["Оба писателя остановлены", "Архив совпал в байтах", "Импорт совпал по полям"]} />
          </DiagramLane>
          <DiagramLane title="Проверка открытых файлов">
            <DiagramTrack label="Штатные входы" items={["API main = цель", "CLI main = цель", "Можно записать"]} />
          </DiagramLane>
        </Diagram>
      </Section>

      <Section number="04" title="Проверяем новое состояние из другого процесса">
        <Lead>После допуска создадим наблюдение, которое невозможно получить из вчерашней копии. Поменяем поле у известной записи и прочитаем её заново.</Lead>
        <p>Первый пункт отмечает возврат кабеля №11. Второй приходит позже и самостоятельно открывает журнал. Сначала узнаем прежнее значение, затем проведём подтверждённое изменение и запустим читателя ещё раз.</p>
        <CodeBlock code={SAVE_AND_READ} caption="Два отдельных запуска читателя для выдачи №11" />
        <p>Первый запуск печатает <code>Выдача 11, возвращена: False</code>, второй печатает <code>True</code> после той же подписи. <code>with writer</code> подтверждает запись при успешном выходе. <code>subprocess.run</code> здесь служит только для запуска нового Python, которому передаём путь. В Planner этим отдельным запуском станет обычный клиент другого интерфейса.</p>
        <p>Запишите id и прежнее значение до изменения. Если новый процесс видит прежнее поле, проверьте факт подтверждения и адрес читателя. Старый архив останется снимком до первой новой записи.</p>
      </Section>

      <Section number="05" title="Убираем файловую сборку и проверяем два исхода">
        <Lead>Файловая зависимость остаётся, если обычный вход обращается к старому файлу напрямую или через другой объект. Проследим каждый вызов до данных.</Lead>
        <p>Представьте, что на стойке ещё проверяют старую папку, чтобы сверить номер уже снятой вывески. Если папки нет, работник не дойдёт до нового журнала. Уберём обращение к папке из обычного просмотра. Сотрудник, который явно переносит старые записи, по-прежнему может открыть экспорт.</p>
        <p>Проверим обе ситуации на журнале выдачи. В первой новый файл исправен, старый экспорт отсутствует. Во второй основной адрес ведёт в каталог, а экспорт появился по привычному адресу. Специальный читатель показывает, что экспорт живой. Обычный просмотр сообщает, что журнал недоступен.</p>
        <CodeBlock code={ARCHIVE_PROBE} caption="Обычный просмотр журнала и явное чтение экспорта" />
        <p>В <code>counter_with_old_sign</code> чтение экспорта прерывается ещё до запроса к журналу. В обычном <code>counter_view</code> этого обращения уже нет, поэтому получаем <code>Без архива: Кабель</code>. Явный читатель выдаёт <code>АРХИВНАЯ ВЫДАЧА 90</code>, а обычный просмотр завершает опыт сообщением <code>Недоступная база: Журнал недоступен</code>. Если бы просмотр вернул архивную выдачу, это был бы fallback: после сбоя он продолжил работу со старым источником.</p>
        <p>В Planner пройдите от запуска API и CLI по настройкам, сборке сервиса и репозиторию. Отдельно проследите явный запуск импортёра и способы превращения объекта Task в данные и обратно. Они принадлежат другим путям вызова. В опыте с недоступной SQLite проверяйте операцию чтения задачи. <code>/health</code> отвечает за живой процесс, через него файл задач не открывается.</p>
        <Diagram caption="Разные опыты после вывода файловой сборки">
          <DiagramLane title="Исправная SQLite">
            <DiagramTrack label="Архив отсутствует" items={["Открываем задачу", "Читаем из SQLite"]} />
          </DiagramLane>
          <DiagramLane title="Недоступная SQLite">
            <DiagramTrack label="Архив доступен" items={["Открываем задачу", "Получаем отказ без архивного значения"]} />
          </DiagramLane>
        </Diagram>
        <h3>Где остановится переход?</h3>
        <p>Выберите состояние журнала. Сначала решите, какое свидетельство потребует остановки. Затем сравните прогноз с результатом.</p>
        <div role="group" aria-label="Состояние журнала" className="answer-options">
          {cases.map((item, index) => (
            <button key={item.label} type="button" className="answer-option" aria-pressed={index === caseIndex}
              onClick={() => setCaseIndex(index)}>
              {item.label}
            </button>
          ))}
        </div>
        <QuizCard
          key={scenario.label}
          question={scenario.question}
          description={scenario.description}
          options={["Сверка полного архива", "Сверка открытых файлов", "Отказ операции без архивной записи"]}
          correctIndex={scenario.correctIndex}
          explanation={scenario.explanation}
          answer={<p>{scenario.observation}</p>}
          orientation="vertical"
        />
      </Section>

      <Section number="06" title="Что мы будем делать в практике">
        <Lead>Продолжим собственный Planner в отдельной учебной копии проекта. Из репетиции возьмём запуск, пустую схему и команды переноса.</Lead>
        <LinkedNotes items={[
          { title: "Сохраним и перенесём данные", description: "Получим точный архив остановленного JSON и отдельно подтвердим строки, которые приняла SQLite." },
          { title: "Переведём оба входа", description: "Сверим открытые файлы до записи и увидим новое значение у того же id из отдельного процесса." },
          { title: "Проверим независимость", description: "В одной копии увидим рабочее чтение без старого файла, в другой получим отказ при доступном архивном маркере." },
        ]} />
        <p>Записывайте наблюдения по ходу работы. Каждый полезный результат станет входом следующей карточки.</p>
        <details>
          <summary>Данные и команды учебного запуска</summary>
          <p>Откройте терминалы из корня отдельной копии <code>project/</code>. Убедитесь, что оба адреса ведут внутрь этой копии и что порт 8765 свободен. Поместите данные в <code>data/tasks.json</code>. Для старого кандидата используйте <code>data/rehearsal/tasks.json</code> с теми же строками и приоритетом 2 у №34. Эти пути относятся к учебной копии. Настройки задаются в каждом терминале.</p>
          <CodeBlock code={WORKING_FIXTURE} caption="Две синтетические задачи" language="text" />
          <CodeBlock code={RUN_SETTINGS} caption="PowerShell · адреса и обычные запуски" language="text" />
          <p>После запуска запрос <code>GET http://127.0.0.1:8765/tasks/52</code> и команда CLI <code>find</code> с номером <code>52</code> показывают контрольную задачу. Для паузы завершите CLI командой <code>exit</code>, а учебный сервер остановите через Ctrl+C. Запущенный сервер получает настройки на старте, поэтому диагностическую команду запускайте из того же окружения. Значения в угловых скобках заменяются своими путями в кавычках.</p>
          <CodeBlock code={SOURCE_OBSERVATION} caption="Эффективный JSON-путь и сравнение двух файлов" language="text" />
          <p>В командах схемы и импорта <code>DATABASE_URL</code> относится к init. У импортёра <code>--source</code> принимает путь архива, а <code>--database</code> принимает путь SQLite как файла.</p>
          <CodeBlock code={IMPORT_COMMANDS} caption="Из репетиции · подготовка SQLite и явный импорт" language="text" />
          <p>Для сверки откройте <code>python</code> и выполните блок ниже. Вызов <code>inspect_import("data/cutover.sqlite3")</code> печатает число строк. Второй аргумент с путём архива добавляет сравнение. Чтение идёт в режиме <code>mode=ro</code>, поэтому ошибочный адрес не создаст новый файл. Сравните оба списка отсутствующих id и число различий, а при расхождении прочитайте строку поля.</p>
          <CodeBlock code={COMPARE_ROWS} caption="Оснастка · полный отчёт по импорту" />
        </details>
        <details>
          <summary>Смотрим путь в штатной Session</summary>
          <p>После правок запуска откройте оболочку <code>python</code> в каждом из двух подготовленных терминалов. Выполните определение наблюдателя и завершите его пустой строкой. Декоратор подписывает функцию на начало транзакции. <code>connection</code> уже получено штатной Session для запроса или команды, в нём и читается <code>main</code>. Этот временный вызов остаётся в оболочке Python.</p>
          <CodeBlock code={OBSERVE_OPEN} caption="Оснастка · какой файл открыла штатная Session" />
          <CodeBlock code={PROBE_START} caption="Запустите по своему блоку в каждом терминале" />
          <p>Выполните обычные GET и CLI find. Сравните напечатанные абсолютные пути с <code>Path(путь_цели).resolve()</code>. Для проверки зеркала используйте отдельную копию сверенной SQLite, сделанную при закрытых процессах. В обоих запусках читайте №52. Перед новой записью закройте пробные процессы и верните обоим терминалам выбранную цель.</p>
        </details>
      </Section>
    </RichLesson>
  );
}
