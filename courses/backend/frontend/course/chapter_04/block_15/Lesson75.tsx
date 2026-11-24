import {
  CodeBlock,
  Diagram,
  DiagramLane,
  DiagramTrack,
  InteractiveTable,
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

const IMPORT_COMPARE = `import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

cards = [
    {"id": 8, "item": "Штатив", "labels": ["выдача"]},
    {"id": 11, "item": "Кабель"},
]
expected = {row["id"]: (row["item"], row.get("labels", []), row["item"].lower())
            for row in cards}

with TemporaryDirectory() as folder:
    file = Path(folder) / "loans.db"
    with closing(sqlite3.connect(file)) as db, db:
        db.execute("CREATE TABLE loans (id INTEGER, item TEXT, labels TEXT, key TEXT)")
        db.execute("INSERT INTO loans VALUES (8, 'Штатив', '[]', 'штатив')")
        db.execute("INSERT INTO loans VALUES (11, 'Кабель', '[]', 'кабель')")

    address = file.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(address, uri=True)) as reader:
        query = "SELECT id, item, labels, key FROM loans"
        actual = {i: (item, json.loads(labels), key)
                  for i, item, labels, key in reader.execute(query)}

print("Номера совпали:", expected.keys() == actual.keys())
for i in sorted(expected.keys() & actual.keys()):
    for field, old, new in zip(("item", "labels", "key"), expected[i], actual[i]):
        if new != old:
            print(i, field, old, "→", new)`;

const OPENED_DATABASE = `from pathlib import Path
from tempfile import TemporaryDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

with TemporaryDirectory() as folder:
    target = Path(folder) / "open.db"
    for name in ("open.db", "mirror.db"):
        file = Path(folder) / name
        engine = create_engine(f"sqlite:///{file.as_posix()}")
        try:
            with engine.begin() as db:
                db.execute(text("CREATE TABLE loans (id INTEGER, item TEXT)"))
                db.execute(text("INSERT INTO loans VALUES (11, 'Кабель')"))
            with Session(engine) as reader:
                loan = reader.execute(text("SELECT item FROM loans WHERE id = 11")).scalar_one()
                for _, schema, opened in reader.execute(text("PRAGMA database_list")):
                    if schema == "main":
                        actual = Path(opened).resolve() if opened else None
                        print(loan, actual == target.resolve())
        finally:
            engine.dispose()`;

const COUNTER_LIFETIME = `from contextlib import asynccontextmanager
from io import StringIO
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

@asynccontextmanager
async def counter_hours(app):
    with StringIO("Журнал выдачи") as journal:
        app.state.journal = journal
        print("Журнал открыт")
        yield
    print("Журнал закрыт:", journal.closed)

counter = FastAPI(lifespan=counter_hours)

@counter.get("/counter/{number}")
def counter_status(number: int, request: Request):
    with StringIO(f"Талон {number}") as ticket:
        print(f"Талон {number} открыт")
        journal = request.app.state.journal
        reply = f"{journal.getvalue()} · {ticket.getvalue()}"
    print(f"Талон {number} закрыт:", ticket.closed)
    return reply

with TestClient(counter) as client:
    for number in (1, 2):
        print(client.get(f"/counter/{number}").json())`;

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

const SHARED_TEST_RESOURCE = `import sqlite3
from contextlib import closing

import pytest


@pytest.fixture
def loan_book(tmp_path):
    path = tmp_path / "loans.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE loans (number INTEGER)")
    return path


def test_two_counters_share_book(loan_book):
    with closing(sqlite3.connect(loan_book)) as writer, writer:
        writer.execute("INSERT INTO loans VALUES (8)")
    with closing(sqlite3.connect(loan_book)) as reader:
        assert reader.execute("SELECT number FROM loans").fetchall() == [(8,)]`;

export function Lesson75() {
  return (
    <RichLesson>
      <RichHero
        title="Переводим Planner на одну SQLite"
        intro="Перенесём актуальные задачи, затем проверим, что API и CLI работают с одной базой и обходятся без старого хранилища."
      />
      <Section number="00" title="Вступление">
        <Lead>На этапе «Возврат к обычному JSON» вы восстановили оба запуска. В обычном учебном JSON они находят прежнюю задачу. Номер пробной записи остаётся в отдельной SQLite.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Пробная база осталась отдельной. В заметке сохранены параметры обычных API и CLI." },
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
        <p>На экране <code>rehearsal.json False</code> и <code>archive.json True</code>. Пробный файл устарел из-за №8. Новый архив совпал байт в байт, включая форматирование. В Planner возьмём действующий адрес из заметки о возврате к JSON. Прежнюю и свежую копии сравним способом из примера.</p>
      </Section>

      <Section number="02" title="Сверяем содержимое после импорта">
        <Lead>После копирования мы знаем, что архив точный. После импорта проверяем уже другое: сохранилось ли каждое значение в новом хранилище.</Lead>
        <p>Бумажную ведомость можно отсканировать без потерь, а при переносе в журнал пропустить пометку «выдача». Построим ожидаемые карточки из ведомости. В примере картотека уже заполнена во временном файле. <code>mode=ro</code> открывает этот файл только для чтения, <code>json.loads()</code> возвращает список меток. Затем <code>&amp;</code> оставляет общие номера, а <code>zip()</code> ставит рядом название поля и оба его значения.</p>
        <CodeBlock code={IMPORT_COMPARE} caption="Журнал выдачи · сравнение двух представлений" />
        <p>Получаем <code>Номера совпали: True</code> и расхождение <code>8 labels ['выдача'] → []</code>. Значит, перенос ещё нельзя считать точным. У старой карточки №11 нет ключа <code>labels</code>. Значение по умолчанию <code>[]</code> совпадает с пустым списком в SQLite. Если пропал сам номер, это покажет первая строка.</p>
        <p>В Planner так же сравниваем предметные поля через знакомый <code>Task.from_dict()</code>. Старая задача без <code>tags</code> и та же задача с <code>tags: []</code> имеют одинаковые метки. Побайтовое равенство отдельно подтверждает, что свежий архив точно копирует остановленный JSON и остаётся в исходном виде.</p>
        <p>В репетиции мы уже проверяли пустую <code>tasks</code> и сравнивали шесть полей по <code>id</code>. Применим свой способ сверки к новому файлу и свежему архиву. Для небольшого набора можно выписать строки по <code>id</code> и пройти каждое поле по тому же принципу. Теперь результат сверки определяет, можно ли открыть эту SQLite в обоих обычных запусках.</p>
      </Section>

      <Section number="03" title="Подключаем оба входа и проверяем открытый файл">
        <Lead>Два пункта могут открыть два журнала с одинаковыми заявками. Для новой записи им нужен один и тот же физический файл.</Lead>
        <p><code>PRAGMA database_list</code> показывает базы, которые открыла текущая Session. В строках идут порядковый номер, имя базы и путь файла. Для <code>main</code> сравниваем путь с выбранной целью. <code>Path.resolve()</code> приводит оба пути к абсолютному виду. Пустой путь означает, что файлового адреса для сравнения нет.</p>
        <p><code>text()</code> помечает строку как SQL-команду для <code>Session.execute()</code>. В примере запрос к таблице и проверка файла выполняются в одной открытой Session.</p>
        <CodeBlock code={OPENED_DATABASE} caption="Одна заявка в двух картотеках" />
        <QuizCard
          question="Что покажут два чтения о заявке и открытом файле?"
          options={[
            "Оба чтения найдут заявку, и оба откроют выбранный файл",
            "Оба чтения найдут заявку, и только первое откроет выбранный файл",
            "Только первое чтение найдёт заявку и откроет выбранный файл",
          ]}
          correctIndex={1}
          explanation="Программа печатает «Кабель True», затем «Кабель False». Обе Session находят заявку №11. В первом чтении PRAGMA возвращает путь open.db, совпадающий с целью. Во втором чтении открыт mirror.db, поэтому сравнение с целью даёт False."
          orientation="vertical"
        />
        <p>Временный вывод в Planner можно добавить у обычного чтения, пока его Session открыта. Уберите этот вывод до прикладной записи.</p>
        <p>У стойки есть часы работы, а каждое обращение посетителя занимает только несколько минут. Так же Engine работает весь срок запуска, а Session закрывается по окончании запроса или команды. У FastAPI границы работы приложения задаёт <code>lifespan</code>.</p>
        <p><code>StringIO</code> ведёт себя как файл в памяти. Приложение хранит общий журнал в <code>app.state</code>. Каждый запрос открывает свой талон и берёт текст из того же журнала.</p>
        <CodeBlock caption="Один журнал и два талона" code={COUNTER_LIFETIME} />
        <p><code>asynccontextmanager</code> позволяет открыть журнал до <code>yield</code> и держать его открытым, пока приложение принимает запросы. В выводе сначала идёт <code>Журнал открыт</code>. Затем открывается талон 1, закрывается с отметкой <code>True</code> и приходит ответ <code>Журнал выдачи · Талон 1</code>. Для талона 2 порядок такой же. Последней появляется строка <code>Журнал закрыт: True</code>, потому что оба запроса завершились до выхода из <code>with TestClient(...)</code>.</p>
        <p>В Planner Engine живёт как журнал. <code>dispose()</code> освобождает его ресурсы при завершении приложения. Session живёт как талон. Её закрывает поставщик запроса или контекст одной команды CLI.</p>
        <Diagram caption="Какие свидетельства нужны до первой записи">
          <DiagramLane title="Подготовка данных">
            <DiagramTrack label="Источник → SQLite" items={["Оба писателя остановлены", "Архив совпал в байтах", "Импорт совпал по полям"]} />
          </DiagramLane>
          <DiagramLane title="Проверка открытых файлов">
            <DiagramTrack label="Штатные входы" items={["Файл API совпал с целью", "Файл CLI совпал с целью", "Можно записать"]} />
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
        <InteractiveTable
          title="Что подтверждают два запуска"
          columns={["Учебная копия", "Условия", "Что увидим"]}
          rows={[
            { id: "normal", label: "Обычный запуск", values: ["SQLite исправна, старого файла нет", "Оба интерфейса возвращают актуальную запись"] },
            { id: "failure", label: "Отказ SQLite", values: ["Главная база недоступна, архив по старому пути читается", "Чтение завершается ошибкой, архивный маркер отсутствует"] },
          ]}
        />
        <QuizCard
          question="Что добавить к опыту, чтобы проверить поведение при сбое?"
          description="Картотека недоступна, старый экспорт тоже отсутствует. Обычное чтение сообщило об ошибке."
          options={["Оставить оба файла недоступными", "Вернуть читаемый экспорт с узнаваемой записью", "Повторить проверку на исправной картотеке без экспорта"]}
          correctIndex={1}
          explanation="Подложим старый экспорт по прежнему адресу, сохраняя сбой основной базы. Тогда успешное чтение узнаваемой записи выдаст переход к экспорту, а отказ покажет, что обычное чтение остановилось."
          orientation="vertical"
        />
        <h3>Как заменить ресурс в общей фикстуре</h3>
        <p>Фикстура готовит ресурс до теста. Она тоже собирает приложение или его часть. Если обычный API уже читает SQLite, а общая фикстура всё ещё передаёт файловое хранилище в старую сборку, ошибка возникнет ещё до запроса.</p>
        <p>Двум учебным стойкам дадим один пустой журнал для проверки. <code>tmp_path</code> от pytest выделяет каталог для отдельного теста. Фикстура готовит таблицу и передаёт один путь обоим обращениям.</p>
        <CodeBlock caption="Отдельный журнал для проверки двух стоек" code={SHARED_TEST_RESOURCE} />
        <p>Если сохранить пример как <code>test_loan_book.py</code> в учебном каталоге, <code>python -m pytest -q test_loan_book.py</code> покажет <code>1 passed</code>. Первая стойка записала выдачу №8. После закрытия её соединения вторая открыла свой ресурс на том же пути и увидела запись. Для проверки чтения старого экспорта нужен отдельный JSON и прямой вызов адаптера.</p>
        <ol>
          <li>В корне учебного Planner запустите <code>python -m pytest -q -x tests/test_api.py</code>. <code>-x</code> останавливает прогон на первом сбое. Если traceback ведёт к <code>tests/conftest.py</code>, сначала проверьте сборку фикстуры, которая должна открыть ресурс для запроса.</li>
          <li>Подготовьте отдельную SQLite со схемой для каждого теста. HTTP-клиент и обычный запуск CLI должны получить тот же учебный адрес, когда тест проверяет оба. Проверьте настройки до запуска и фактический адрес после <code>lifespan</code>. Закройте ресурсы и восстановите изменённые параметры после теста. Тесты чтения и сохранения старого JSON обращаются к своему адаптеру напрямую.</li>
          <li>Повторите первый прогон. Затем проверьте прежние HTTP-статусы и поля, ответы CLI и настройки, включая выбор <code>DATABASE_URL</code>. В конце запустите весь набор через <code>python -m pytest -q</code>.</li>
        </ol>
      </Section>

      <Section number="06" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Сохраним и перенесём данные", description: "Снимем архив остановленного JSON и проверим SQLite по свежему снимку." },
          { title: "Переведём оба входа", description: "Сверим открытые файлы до записи и прочитаем новое значение из другого процесса." },
          { title: "Проверим независимость", description: "Запустим SQLite без архива и проверим отказ при читаемом архиве." },
        ]} />
        <p>Записывайте путь и результат каждого шага, чтобы использовать их в следующей карточке.</p>
      </Section>
    </RichLesson>
  );
}
