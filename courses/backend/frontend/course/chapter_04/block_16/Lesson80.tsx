import {
  CodeBlock,
  Diagram,
  DiagramArrow,
  DiagramFlow,
  DiagramLane,
  DiagramNode,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const INVENTORY = `needed = {"stock.py", "guide.txt", "settings.example"}
sent = {"stock.py", "guide.txt", "shift.db"}

print("Не хватает:", sorted(needed - sent))
print("Лишнее:", sorted(sent - needed))`;

const TERMINAL_LABEL = String.raw`# Только в первом терминале
$env:STOCK_LABEL = "shelf-7"

# Выполните в каждом терминале
py -3.12 -c "import sys, os; print(sys.executable); print(os.getenv('STOCK_LABEL'))"`;

const SETTINGS_PROBE = String.raw`.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
.\.venv\Scripts\python.exe -c "from app.core.config import load_settings; print(load_settings().database_url)"`;

const SHEET_STATES = `import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

ddl = "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?"

with TemporaryDirectory() as folder:
    file = Path(folder) / "loans.db"
    print("Файл уже есть:", file.exists())
    file.touch()
    with closing(sqlite3.connect(file)) as db:
        print("Таблица до разметки:", db.execute(ddl, ("loans",)).fetchone())
        db.execute("CREATE TABLE loans (id INTEGER PRIMARY KEY, item TEXT NOT NULL)")
        print("Таблица после разметки:", db.execute(ddl, ("loans",)).fetchone()[0])
        print("Строк:", db.execute("SELECT COUNT(*) FROM loans").fetchone()[0])`;

const SCHEMA_READER = `import sqlite3
from contextlib import closing
from pathlib import Path

file = Path(input("Путь к SQLite: ").strip()).resolve()
table = input("Имя таблицы: ").strip()

if not file.is_file():
    print("Файл отсутствует или указан каталог")
else:
    uri = file.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        ddl = db.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if ddl is None:
            print("Таблица отсутствует:", table)
        else:
            print(ddl[0])
            print("Колонки:", db.execute("SELECT * FROM pragma_table_info(?)", (table,)).fetchall())
            print("Индексы:", db.execute("SELECT * FROM pragma_index_list(?)", (table,)).fetchall())
            print("Связи:", db.execute("SELECT * FROM pragma_foreign_key_list(?)", (table,)).fetchall())
            quoted_table = '"' + table.replace('"', '""') + '"'
            print("Строк:", db.execute(f"SELECT COUNT(*) FROM {quoted_table}").fetchone()[0])`;

const EMPTY_MIRROR = `from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

with TemporaryDirectory() as folder:
    target = Path(folder) / "loans-a.db"
    for name in ("loans-a.db", "loans-b.db"):
        file = Path(folder) / name
        engine = create_engine(f"sqlite:///{file.as_posix()}")
        try:
            with engine.begin() as db:
                db.execute(text("CREATE TABLE loans (id INTEGER PRIMARY KEY)"))
            with Session(engine) as reader:
                print(reader.execute(text("SELECT id FROM loans")).all())
                for _, schema, opened in reader.execute(text("PRAGMA database_list")):
                    if schema == "main":
                        actual = Path(opened).resolve() if opened else None
                        print(name, actual == target.resolve())
        finally:
            engine.dispose()`;

const REPEATED_INIT = `import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

def prepare(file):
    with closing(sqlite3.connect(file)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS loans (id INTEGER PRIMARY KEY, item TEXT NOT NULL)")

def read_label(file, number):
    with closing(sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)) as db:
        return db.execute("SELECT id, item FROM loans WHERE id = ?", (number,)).fetchone()

with TemporaryDirectory() as folder:
    file = Path(folder).resolve() / "loans.db"
    prepare(file)
    with closing(sqlite3.connect(file)) as db, db:
        db.execute("INSERT INTO loans VALUES (?, ?)", (18, "Ключ 10"))

    before = read_label(file, 18)
    prepare(file)
    after = read_label(file, 18)
    print(before, after, before == after)`;

const REGRESSION = String.raw`.\.venv\Scripts\python.exe -m pytest -q tests/test_storage_availability.py::test_smoke_api_and_cli_read_the_same_task
.\.venv\Scripts\python.exe -m pytest -q`;

export function Lesson80() {
  return (
    <RichLesson>
      <RichHero
        title="Запускаем Planner по своему README из чистой копии"
        intro="Проверим, сможет ли другой человек запустить Planner с нуля и сохранить задачу после повторной инициализации."
      />

      <Section number="00" title="Вступление">
        <Lead>Из привычной папки Planner запускается без сюрпризов. У получателя пока есть только файлы, которые мы передали, и наш README.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "API и CLI уже читают одну задачу. Новый процесс видит её изменённый статус." },
          { title: "Новый вопрос", description: "Хватит ли комплекта и README человеку, у которого ещё нет среды и базы?" },
          { title: "Результат", description: "Чистая копия заработает на новой SQLite. Повторный init сохранит пробную задачу." },
        ]} />
        <p>Будем останавливаться после каждого шага. Первое расхождение подскажет, какое указание в README стоит проверить.</p>
      </Section>

      <Section number="01" title="Что действительно получает другой человек">
        <Lead>Комплект передачи состоит из файлов, с которыми получатель может выполнить инструкцию. Начнём с того, что окажется в его папке до установки.</Lead>
        <p>Передаём учёт инструментов другой смене. Инструкция ссылается на образец настроек. Заполненный журнал остаётся у прежней смены. Сравним ожидаемый и фактический списки как множества имён.</p>
        <CodeBlock caption="Опись передачи журнала выдачи" code={INVENTORY} />
        <p>Для опыта сохраните блок во временный файл и запустите его через <code>py -3.12</code>. В обеих описях по три имени. Первая разность обнаруживает пропущенный <code>settings.example</code>, вторая находит случайно переданный <code>shift.db</code>. Если инструкция дополнительно ссылается на <code>labels.csv</code>, найдём этот пробел, когда проверим ссылку, даже если остальная опись совпала.</p>
        <p>У Planner проверьте код, README, оба файла требований, тесты и безопасный <code>.env.example</code>. Откройте относительные ссылки README и проверьте файлы, которые нужны тестам и командам из README. Создайте отдельную папку со свободным именем и отберите файлы в неё. В PowerShell <code>Get-ChildItem -Force -Recurse -File</code> покажет также скрытые файлы внутри копии. Сверьте опись до установки. Рабочие <code>.env</code>, <code>.venv</code>, базы, журналы, архивы и секреты остаются в исходной папке.</p>
      </Section>

      <Section number="02" title="Откуда берутся Python, зависимости и адрес">
        <Lead>Чистая среда получает пакеты по инструкции. А новый процесс получает адрес базы из настроек своего запуска.</Lead>
        <p>У нового пункта выдачи свой стол и свой журнал. Если номер полки записан только на одном столе, второй пункт его не увидит. Проверим это на метке <code>STOCK_LABEL</code> в двух свежих окнах PowerShell. Сначала убедитесь, что <code>py -3.12 --version</code> находит Python.</p>
        <CodeBlock caption="Одна метка в двух терминалах" code={TERMINAL_LABEL} />
        <p>Флаг <code>-c</code> выполняет переданную строку Python. <code>sys.executable</code> выводит путь к интерпретатору. <code>os.getenv</code> читает переменную процесса. В первом окне дочерний Python видит <code>shelf-7</code>, во втором видим <code>None</code>. Предскажите результат, если установить ту же метку во втором окне и повторить чтение. Теперь оба процесса получат метку, потому что она задана в каждом окне.</p>
        <p>В Planner команды выполняем из папки, где лежат <code>app/</code> и <code>tests/</code>. <code>py -3.12 -m venv .venv</code> создаёт новую среду. <code>-m</code> запускает модуль. Установку <code>-m pip install -r requirements-dev.txt</code> запускаем Python из этой среды. Запись <code>-r requirements.txt</code> внутри файла подключает основной список пакетов. В выводе установки и <code>-m pip show</code> с именами пакетов можно проверить, что они доступны именно этому Python.</p>
        <p>Для установки прочтите в README точное имя файла требований. Для проверки через <code>pip show</code> можно взять пакеты <code>SQLAlchemy</code> и <code>pytest</code> из списка. Учебную SQLite выбираем в свободной папке копии. Для обычного локального пути форма <code>sqlite:///./data/trial/planner.sqlite3</code> означает путь от корня Planner. Имя <code>trial</code> можно заменить своим. Создайте родительскую папку до init и проверьте, что выбранного файла ещё нет, например через <code>Test-Path</code>. Задайте один и тот же <code>$env:DATABASE_URL</code> в обоих терминалах до запуска. Настройка из окружения имеет приоритет над локальным <code>.env</code>. Файл <code>.env.example</code> служит образцом и сам не загружается.</p>
        <CodeBlock caption="Проверяем выбранный Python и адрес" code={SETTINGS_PROBE} />
        <p>Вторая команда вызывает <code>load_settings()</code> и печатает <code>database_url</code> из полученных настроек. Сопоставьте полный путь в этом URL с выбранным файлом. Если первое окно верно, а второе показало обычный <code>data/planner.sqlite3</code>, проверьте переменную второго окна, прежде чем запускать init или записывать данные. Файловый путь для чтения SQLite берите из своего выбора, сверенного с URL.</p>
      </Section>

      <Section number="03" title="Пустой файл, пустая таблица и явный init">
        <Lead>Схема задаёт колонки и правила записи. У пустого бланка уже есть разметка, а у чистого листа её ещё предстоит сделать.</Lead>
        <p>Во временной папке проверим лист выдачи. Сначала файла нет. Создадим пустой файл для опыта и посмотрим в <code>sqlite_schema</code>, где SQLite хранит описание таблиц. <code>?</code> получает значение из кортежа параметров. <code>fetchone()</code> возвращает первую строку или <code>None</code>.</p>
        <CodeBlock caption="Три состояния журнала выдачи" code={SHEET_STATES} />
        <p>Получим <code>False</code>, затем <code>None</code>, а после создания таблицы определение <code>CREATE TABLE loans</code> и <code>Строк: 0</code>. Если вместо <code>loans</code> создать только <code>returns</code>, файл будет на месте, но поиск <code>loans</code> всё равно даст <code>None</code>. В Planner команда <code>.\.venv\Scripts\python.exe -m app.DBase.init_db</code> явно готовит отсутствующую схему по настроенному URL.</p>
        <p>Для сверки возьмите этот читатель. Введите путь к файлу, затем имя таблицы. <code>as_uri()</code> превращает путь в файловый URI, в том числе для имён с пробелами. <code>uri=True</code> включает разбор параметров, а <code>mode=ro</code> разрешает только чтение. Пропущенный файл обнаруживается до подключения. <code>closing</code> закроет соединение при выходе из <code>with</code>. Последняя команда считает строки только после обнаружения таблицы. В ней имя заключено в двойные кавычки, а кавычки внутри имени удвоены.</p>
        <CodeBlock caption="Читаем схему без создания файла" code={SCHEMA_READER} />
        <p>Сохраните этот блок в свой временный файл, например <code>schema_probe.py</code>, вне комплекта передачи. Из папки Planner запустите <code>.\.venv\Scripts\python.exe</code> с полным путём к этому файлу в кавычках. На двух запросах введите путь к SQLite и имя таблицы. Тот же файл читателя пригодится до и после init. Для <code>loans</code> проверка показывает колонки <code>id</code> и <code>item</code>. В свойствах колонки <code>pk</code> указывает на первичный ключ, <code>notnull</code> на обязательное значение, <code>dflt_value</code> на значение по умолчанию в SQLite. Два пустых списка означают, что нет дополнительных индексов и внешних ключей. Ограничение <code>CHECK</code> видно в тексте <code>CREATE TABLE</code>.</p>
        <p>В чистом Planner ожидаем исходную <code>tasks</code> с шестью обязательными полями <code>id</code>, <code>title</code>, <code>priority</code>, <code>is_done</code>, <code>tags</code>, <code>title_normalized</code>. У <code>id</code> тип INTEGER, первичный ключ и условие <code>id &gt; 0</code>. У приоритета границы от 1 до 5. У этой схемы нет SQL DEFAULT, внешних ключей и дополнительных индексов. Значения по умолчанию в Python могут подставляться при записи. До init зафиксируйте отсутствие таблицы, после init сравните структуру с исходной моделью и подтвердите ноль строк до запуска обоих интерфейсов.</p>
      </Section>

      <Section number="04" title="Как узнать, какой файл открыл каждый процесс">
        <Lead>Два пункта выдачи могут одинаково ответить «пусто», хотя каждый открыл свой журнал. Содержимое списка и адрес файла проверяем отдельно.</Lead>
        <p>Прочитаем две пустые картотеки через SQLAlchemy, который уже установлен в среде Planner. <code>PRAGMA database_list</code> сразу после SELECT показывает файлы той же Session. В строке <code>main</code> берём путь основного файла и через <code>Path.resolve()</code> приводим к полному адресу. <code>text()</code> передаёт строку как SQL-команду. Пустое значение пути не даёт файлового свидетельства.</p>
        <CodeBlock caption="Одинаковые пустые журналы, разные файлы" code={EMPTY_MIRROR} />
        <p>Оба чтения вернут <code>[]</code>. Для <code>loans-a.db</code> сравнение адресов даст <code>True</code>, для <code>loans-b.db</code> даст <code>False</code>.</p>
        <Diagram caption="Два обычных чтения сверяем с одной целью">
          <DiagramLane title="API">
            <DiagramFlow label="Файл, открытый API">
              <DiagramNode title="GET /tasks"><code>[]</code></DiagramNode>
              <DiagramArrow label="Session запроса" />
              <DiagramNode title="Путь main" shape="database">Сверяем с файлом, где выполнили init.</DiagramNode>
            </DiagramFlow>
          </DiagramLane>
          <DiagramLane title="CLI">
            <DiagramFlow label="Файл, открытый CLI">
              <DiagramNode title="list"><p>Список задач пуст</p></DiagramNode>
              <DiagramArrow label="Session команды" />
              <DiagramNode title="Путь main" shape="database">Сверяем с тем же выбранным файлом.</DiagramNode>
            </DiagramFlow>
          </DiagramLane>
        </Diagram>
        <QuizCard
          question="Обе стойки выдачи показывают пустой список. Как различить два пустых журнала?"
          description="Для опыта выбран loans-a.db. Предскажите, какое наблюдение обнаружит, что вторая стойка вместо него открыла loans-b.db."
          options={[
            "Сравнить ответы проверки доступности двух стоек",
            "Ещё раз сравнить списки инструментов",
            "Сравнить пути main двух чтений с выбранным файлом",
          ]}
          correctIndex={2}
          explanation="Пути покажут loans-a.db и loans-b.db. Совпадёт только первый. Два списка пусты, а доступность стойки ничего не говорит об открытом журнале."
          orientation="vertical"
        />
        <p>Представьте другой случай: второй журнал вообще не подготовили. Проверка доступности может ответить, а чтение списка завершится ошибкой. Сначала проверьте, какое соединение действительно выполнило чтение.</p>
        <p>Из README запустите <code>uvicorn app.api:app</code> локально через <code>-m uvicorn</code> и обычный CLI через <code>-m app.main</code> в другом терминале. На PowerShell команды идут в терминал, а <code>list</code> и <code>exit</code> вводятся внутри меню. При занятом порте выберите свободный и используйте его и при запуске, и в HTTP-запросах.</p>
        <p>В копии Planner найдите чтения <code>SqlTaskRepository.select</code> для HTTP и <code>.list</code> для CLI. Временно перенесите наблюдение из примера после обычного SELECT, на <code>self.session</code> каждого чтения. Так путь будет принадлежать процессу, который действительно прочитал список. Выведите его в терминал. Сравните оба пути с целью, затем уберите временный вывод и перезапустите процессы перед записью. У API ожидаем <code>200</code> и <code>[]</code>, у CLI «Список задач пуст.».</p>
      </Section>

      <Section number="05" title="Как доказать сохранность после повторного init">
        <Lead>Повторный init идемпотентен для нашей подготовленной схемы: после него в том же файле остаются те же поля и строки.</Lead>
        <p>По штатному правилу подготовим бланк выдачи. Запись №18 запишем и подтвердим один раз. Первый снимок берём отдельным чтением. После повторной подготовки снова читаем то же значение из файла.</p>
        <CodeBlock caption="Та же запись до и после повторной подготовки" code={REPEATED_INIT} />
        <p>Оба чтения дадут <code>(18, 'Ключ 10')</code> и сравнение выведет <code>True</code>. <code>CREATE TABLE IF NOT EXISTS</code> создаёт бланк, когда его ещё нет. При повторе таблица уже существует. Если номер 18 остался, а название стало «Ключ 12», сравнение полей покажет изменение, даже при прежнем номере.</p>
        <p>В Planner создайте одну синтетическую задачу штатным <code>POST /tasks</code> с <code>title</code> и <code>priority</code> от 1 до 5. По ответу <code>201</code> узнайте id. Отдельным <code>GET /tasks/{"{id}"}</code> сохраните четыре публичных поля. Возьмите также полный кортеж этой записи из SQLite. Перенесите из примера чтение по одному id с параметром <code>?</code>. <code>tags</code> и <code>title_normalized</code> видны здесь, а в публичной выдаче их нет. После <code>exit</code> в CLI и Ctrl+C для своего API повторите init и запустите обычные процессы заново. Сравните схему и строку с тем, что записали до повтора. В CLI <code>find</code> спрашивает фактический id следующей строкой.</p>
        <p>Готовый тест из практики проверки отказов работает на своей временной SQLite. Первая команда выбирает одну проверку по форме <code>файл::имя_теста</code>, вторая запускает весь набор. <code>-q</code> сокращает вывод. На строке <code>passed</code> видно, что проверка выполнилась. Если pytest не нашёл файл или имя, сначала проверьте рабочую папку и наличие своего теста. Его успех подтверждает штатное чтение в изолированном опыте. Состояние выбранной для передачи SQLite подтверждают ваши записи из этой попытки.</p>
        <CodeBlock caption="Контроль из чистой копии" code={REGRESSION} />
        <p>В передаваемый комплект войдут проверенный README, код, зависимости, безопасный пример и тесты. Пробный SQLite-файл и запись проверки остаются у вас. Получатель сможет выбрать новый файл, выполнить явный init и сам подтвердить его содержимое.</p>
      </Section>

      <Section number="06" title="Что мы будем делать в практике">
        <Lead>Возьмём рабочий Planner и проверим запуск в отдельной копии по своему README.</Lead>
        <LinkedNotes items={[
          { title: "Соберём комплект", description: "Получим опись файлов, доступных сразу после передачи." },
          { title: "Подготовим среду и адрес", description: "Увидим новый Python и один учебный адрес в двух терминалах." },
          { title: "Проверим исходную схему", description: "Получим пустую tasks с шестью исходными колонками." },
          { title: "Запустим оба входа", description: "Прочитаем пустой список и сверим фактически открытые файлы." },
          { title: "Повторим init", description: "Перечитаем ту же задачу после повторной инициализации и проверим регрессию." },
        ]} />
        <p>Если инструкция пропустила нужное действие, исправим её по первому расхождению и перепроверим этот участок с чистого состояния.</p>
      </Section>
    </RichLesson>
  );
}
