import { Callout, CodeBlock, CodeSequence, Diagram, DiagramArrow, DiagramFlow, DiagramNode, Lead, LinkedNotes, RichHero, RichLesson, Section, TypeCard, TypeCards } from "../../shared";

const compareExample = `import sqlite3
from pathlib import Path

folder = Path("demo")
folder.mkdir(exist_ok=True)
for name, constraint in [("reference.sqlite", "CHECK(score > 0)"),
                         ("different.sqlite", "")]:
    path = folder / name
    if path.exists():
        raise RuntimeError(f"Файл уже существует: {path}")
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE notes (id INTEGER PRIMARY KEY, score INTEGER {constraint})")
        db.execute("INSERT INTO notes VALUES (1, 3)")

for name in ("reference.sqlite", "different.sqlite"):
    path = (folder / name).resolve()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        print(name)
        print(db.execute("SELECT sql FROM sqlite_schema WHERE name='notes'").fetchone())
        print(db.execute("PRAGMA table_xinfo('notes')").fetchall())
        print(db.execute("SELECT id, score FROM notes ORDER BY id").fetchall())
    finally:
        db.close()`;

const guardExample = `from pathlib import Path
from alembic.config import Config
from sqlalchemy.engine import make_url

cfg = Config("demo/alembic.ini")
raw = cfg.get_main_option("sqlalchemy.url")
expected = cfg.get_main_option("expected_database_path")
if not raw or not expected:
    raise ValueError("Адрес не задан")
url = make_url(raw)
if (url.drivername not in ("sqlite", "sqlite+pysqlite") or not url.database
        or url.database == ":memory:" or url.query or url.host is not None
        or url.username is not None or url.password is not None or url.port is not None):
    raise ValueError("Нужен файловый SQLite URL")
target = Path(url.database).resolve()
allowed = Path(expected)
if not allowed.is_absolute():
    raise ValueError("Контрольный путь должен быть абсолютным")
allowed = allowed.resolve()
print("URL:", target)
print("Ожидаем:", allowed)
root = Path("demo").resolve()
if not target.is_relative_to(root):
    raise ValueError("Путь вне учебного каталога")
if target != allowed or not target.is_file():
    raise ValueError("Адрес не совпадает или файл отсутствует")
print("Адрес совпадает")`;

const inspectExample = `import sqlite3
from pathlib import Path

path = Path("demo/reference.sqlite").resolve()
db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
try:
    for sql in (
        "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name",
        "PRAGMA table_xinfo('notes')",
        "PRAGMA index_list('notes')",
        "PRAGMA foreign_key_list('notes')",
        "SELECT COUNT(*) FROM notes",
        "SELECT id, score FROM notes ORDER BY id",
    ):
        print(sql, db.execute(sql).fetchall())
finally:
    db.close()`;

const indexExample = `import sqlite3

db = sqlite3.connect(":memory:")
try:
    db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, score INTEGER)")
    db.execute("CREATE UNIQUE INDEX notes_score ON notes(score)")
    print(db.execute("PRAGMA index_list('notes')").fetchall())
    print(db.execute(
        "SELECT name, key FROM pragma_index_xinfo(?)", ("notes_score",)
    ).fetchall())
finally:
    db.close()`;

const baselineEnvExample = `from pathlib import Path
from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

config = context.config
raw = config.get_main_option("sqlalchemy.url")
expected = config.get_main_option("expected_database_path")
if not raw or not expected:
    raise ValueError("Адрес не задан")
url = make_url(raw)
if (url.drivername not in ("sqlite", "sqlite+pysqlite") or not url.database
        or url.database == ":memory:" or url.query or url.host is not None
        or url.username is not None or url.password is not None or url.port is not None):
    raise ValueError("Нужен файловый SQLite URL")
target = Path(url.database).resolve()
allowed = Path(expected)
if not allowed.is_absolute():
    raise ValueError("Контрольный путь должен быть абсолютным")
allowed = allowed.resolve()
print("Адрес:", target, "Ожидаем:", allowed)
if target != allowed or not target.is_relative_to(Path.cwd().resolve()) or not target.is_file():
    raise ValueError("Недопустимый адрес")

if context.is_offline_mode():
    context.configure(url=raw)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(raw)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()`;

const baselineRevisionExample = `from alembic import op
import sqlalchemy as sa

revision = "a1b2"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("notes",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("score", sa.Integer),
        sa.CheckConstraint("score > 0"))

def downgrade():
    op.drop_table("notes")`;

const baselineCopiesExample = `import sqlite3
from pathlib import Path

reference = Path("reference.sqlite").resolve()
names = ("notes.sqlite", "different.sqlite")
if any(Path(name).exists() for name in names):
    raise ValueError("Файл копии уже существует")
db = sqlite3.connect(reference.as_uri() + "?mode=ro", uri=True)
try:
    ddl = db.execute("SELECT sql FROM sqlite_schema WHERE type='table' AND name='notes'").fetchone()[0]
finally:
    db.close()
if "score > 0" not in ddl:
    raise ValueError("Неизвестное ограничение в образце")
for name, sql in ((names[0], ddl), (names[1], ddl.replace("score > 0", "score >= 0"))):
    with Path(name).open("xb"):
        pass
    db = sqlite3.connect(name)
    try:
        db.execute(sql)
        db.execute("INSERT INTO notes VALUES (1, 3)")
        db.commit()
    finally:
        db.close()
print("Созданы две заполненные учебные базы")`;

const baselineCompareExample = `import sqlite3
from pathlib import Path

def inspect(name):
    path = Path(name).resolve()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        objects = db.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE tbl_name != 'alembic_version' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY type, name"
        ).fetchall()
        columns = db.execute("PRAGMA table_xinfo('notes')").fetchall()
        indexes = db.execute("PRAGMA index_list('notes')").fetchall()
        details = [(row[1], db.execute(
            "SELECT * FROM pragma_index_xinfo(?)", (row[1],)
        ).fetchall()) for row in indexes]
        foreign_keys = db.execute("PRAGMA foreign_key_list('notes')").fetchall()
        rows = db.execute("SELECT id, score FROM notes ORDER BY id").fetchall()
        has_version = db.execute(
            "SELECT name FROM sqlite_schema WHERE name='alembic_version'"
        ).fetchall()
        version = db.execute("SELECT version_num FROM alembic_version").fetchall() if has_version else []
        return (objects, columns, indexes, details, foreign_keys), rows, version
    finally:
        db.close()

reference, _, _ = inspect("reference.sqlite")
for name in ("different.sqlite", "notes.sqlite"):
    schema, rows, version = inspect(name)
    print(name, "схема совпадает:", schema == reference)
    print("Строки:", rows, "Версия:", version)`;

export function Lesson84() {
  return <RichLesson>
    <RichHero chip="Изменение заполненной схемы" title="Baseline для данных" intro="Сверим восстановленную копию с исходной схемой и только затем запишем её версию." />
    <Section number="00" title="От образца к заполненной базе">
      <Lead>У нас есть образец схемы, построенный по истории, и отдельная восстановленная копия с задачами. Нам важно не потерять эти задачи при включении копии в историю.</Lead>
      <LinkedNotes items={[
        {title:"Точка старта",description:"Проверяем резерв, восстановленную копию, образец и исходную ревизию."},
        {title:"Новый вопрос",description:"Доказывает ли метка версии, что существующая схема действительно подходит?"},
        {title:"Результат",description:"Сравним схему и данные и отметим только подходящую копию."}
      ]} />
      <p>Ревизия описывает ожидаемую схему, но не проверяет файл за нас. Поэтому сначала рассматриваем реальное состояние, а отметку записываем в конце.</p>
    </Section>
    <Section number="01" title="Разделяем роли файлов">
      <p>Исходная база и резерв нужны для восстановления. На них мы не экспериментируем. Восстановленная копия содержит две разные синтетические задачи; отдельная пустая база после baseline служит образцом схемы. Ещё один отдельный файл понадобится для намеренной ошибки.</p>
      <TypeCards>
        <TypeCard badge="Роль файла" title="Резерв">Сохраняем без изменений и проверяем, что восстановление уже удалось.</TypeCard>
        <TypeCard badge="Роль файла" title="Образец">База, созданная применением исходной ревизии к пустому файлу.</TypeCard>
        <TypeCard badge="Роль файла" title="Восстановленная копия">Её будем сравнивать и при совпадении маркировать.</TypeCard>
        <TypeCard badge="Роль файла" title="Одноразовая копия">На ней обнаружим расхождение и остановимся.</TypeCard>
      </TypeCards>
      <Diagram caption="Как копия попадает в историю" orientation="vertical">
        <DiagramFlow label="Сравнение перед маркировкой" orientation="vertical">
          <DiagramNode title="Образец и заполненная копия" shape="database"><p>У каждого файла своя роль и проверенный адрес.</p></DiagramNode>
          <DiagramArrow label="сравниваем структуру и сохраняем опись" />
          <DiagramNode title="Схема совпадает?" shape="decision"><p>При отличии или неясном объекте останавливаемся.</p></DiagramNode>
          <DiagramArrow label="совпадает: отмечаем исходную revision" />
          <DiagramNode title="Проверяем результат"><p>Версия записана, строки и схема сохранились.</p></DiagramNode>
        </DiagramFlow>
      </Diagram>
      <p>Стрелка к отметке возможна только после сравнения. Если схема отличается, путь заканчивается на проверке, а резерв остаётся доступен для восстановления.</p>
      <Callout tone="warn">Работаем из project/ в изолированном учебном каталоге. Если путь неизвестен, файлы совпадают или восстановление не подтверждено, останавливаемся до записи.</Callout>
    </Section>
    <Section number="02" title="Что действительно хранит схема">
      <Lead>Схема описывает таблицы и их правила. Одинаковые имена столбцов ещё не означают одинаковые ограничения.</Lead>
      <p>В отдельном пустом каталоге сохраним пример как compare_notes.py и запустим <code>python compare_notes.py</code>. Он создаёт только новые файлы в demo и отказывается перезаписывать существующие. Затем открывает их для чтения. URI с <code>mode=ro</code> запрещает создание отсутствующей базы; <code>uri=True</code> включает разбор URI. После чтения соединение закрывается.</p>
      <CodeBlock code={compareExample} />
      <p>Обе базы вернут строку <code>[(1, 3)]</code> и одинаковые поля. Но в SQL таблицы reference есть <code>CHECK(score &gt; 0)</code>, а в different его нет. CHECK ограничивает допустимые значения. Содержимое совпало, правило нет. Если файлы уже существуют, сначала выбираем новый учебный каталог, а не удаляем их вслепую.</p>
      <p>Рассмотрим одну строку table_xinfo: <code>(1, 'score', 'INTEGER', 0, None, 0, 0)</code>. Это колонка номер 1 с именем score и типом INTEGER; первый ноль после типа означает отсутствие NOT NULL, None означает отсутствие SQL default, последние нули показывают, что это не первичный ключ и не скрытая колонка. Здесь таблица в обоих файлах даёт одинаковую строку, поэтому условие CHECK приходится найти в SQL создания отдельно.</p>
      <p>В SQLite <code>sqlite_schema</code> перечисляет объекты и исходный SQL. <code>PRAGMA table_xinfo('notes')</code> показывает имя, тип, обязательность, SQL default, первичный ключ и признак скрытого столбца. Для индексов используем <code>PRAGMA index_list('notes')</code>, затем <code>PRAGMA index_xinfo('имя_индекса')</code>; для связей <code>PRAGMA foreign_key_list('notes')</code>. Пустой список означает, что соответствующие объекты не найдены. CHECK, триггеры и представления отдельно читаем в sqlite_schema.</p>
      <p>Чтобы увидеть эти результаты средствами Python, сохраним inspect_notes.py рядом с compare_notes.py и запустим <code>python inspect_notes.py</code> из той же папки после создания образца. Метод execute отправляет SQL, fetchall возвращает все строки; цикл печатает каждый запрос вместе с ответом. Для notes в примере списки индексов и связей пусты, число строк равно 1. Если индекс есть, отдельным запросом с его именем читаем index_xinfo.</p>
      <CodeBlock code={inspectExample} />
      <p>Посмотрим на непустой результат отдельно. Сохраним index_notes.py в той же учебной папке и запустим <code>python index_notes.py</code>. База существует только в памяти, файлы предыдущего примера не изменяются.</p>
      <CodeBlock code={indexExample} />
      <p>Первый вывод <code>[(0, 'notes_score', 1, 'c', 0)]</code>: индекс называется notes_score, единица означает уникальность, c обозначает явно созданный индекс, последний ноль означает отсутствие частичного условия. Второй вывод <code>[('score', 1), (None, 0)]</code> показывает ключевую колонку score и служебную запись rowid. Знак вопроса передаёт имя отдельно от SQL, а кортеж с запятой содержит один аргумент. При сравнении реальной базы имя индекса берём из index_list; его SQL также читаем в sqlite_schema.</p>
      <p>Сравниваем таблицы, колонки, типы, PK, nullable, defaults, CHECK, индексы, внешние ключи, представления и триггеры. Python default в модели не обязательно является SQL default в базе. Непонятный объект или неоднозначное SQL-сравнение означает остановку: мы не доказываем эквивалентность любых возможных схем.</p>
    </Section>
    <Section number="03" title="Сравниваем сами записи">
      <p>Количество строк не покажет изменённый текст. Для примера выше <code>SELECT COUNT(*) FROM notes</code> вернёт 1 в обеих базах, а <code>SELECT id, score FROM notes ORDER BY id</code> даст отсортированные пары. Если score в одной копии изменить, количество останется прежним, но пары будут отличаться.</p>
      <p>В Planner сохраняем количество и полный список <code>id, title, priority, is_done, tags, title_normalized</code> по порядку id. После операции повторяем то же чтение в новом соединении и сравниваем значения каждой строки. Внутренние tags проверяем в базе: API и CLI не обязаны их показывать. Integrity check проверяет структуру файла, но не правильность содержимого.</p>
    </Section>
    <Section number="04" title="Метка и фактическое состояние">
      <p>Файл исходной ревизии содержит её <code>revision</code> и операцию создания схемы. <code>current</code> показывает записанную версию, а <code>history</code> доступную историю. Команда <code>stamp</code> записывает отметку, не выполняя операции миграции. Поэтому она не добавит пропущенный CHECK и не исправит данные. Автоматическое сравнение metadata тоже может пропускать ограничения.</p>
      <CodeSequence title="Когда можно поставить отметку" prompt="У отдельного учебного каталога книг есть заполненная копия и эталон. Расположим действия по порядку для случая, когда все проверки совпали." pieces={[
        {id:"compare",code:"Сравнить фактические объекты и правила с эталоном"},
        {id:"stamp",code:"Записать известную исходную ревизию"},
        {id:"check",code:"Повторно прочитать версию и данные"}
      ]} correctOrder={["compare","stamp","check"]} explanation="Сначала устанавливаем соответствие, затем записываем метку и независимо читаем результат. При отличии останавливаемся до метки." incorrectExplanation="Посмотрим, что даёт основание для метки и когда можно проверить её результат. При отличии схемы маркировки вообще не будет." />
      <p>Когда совпадение установлено, используем конкретное значение revision из файла в migrations/versions. Слово <code>head</code> обозначает вершину истории, которая может измениться позже. Не стираем неожиданную версию и не повторяем команду при неизвестном результате.</p>
      <p>Проведём отдельный опыт с notes. Как и при подготовке истории, сначала получим окружение Alembic и исходную ревизию. Разница в том, что затем сравним с образцом уже заполненную базу и запишем ей метку без создания таблицы заново. Работаем в новой пустой учебной папке notes_baseline с установленными Alembic и SQLAlchemy; все команды ниже выполняем из неё.</p>
      <p>Создадим окружение командой:</p>
      <CodeBlock code="python -m alembic init migrations" />
      <p>В папке появятся alembic.ini, migrations/env.py и migrations/versions. Команду init поверх существующего окружения не повторяем.</p>
      <p>В migrations/versions создадим файл <code>a1b2_initial.py</code>. Это самостоятельная история примера, поэтому здесь можно задать известный ID a1b2. В реальном проекте значение читаем из его исходного файла.</p>
      <CodeBlock caption="migrations/versions/a1b2_initial.py" code={baselineRevisionExample} />
      <p>Теперь заменим содержимое migrations/env.py. Как и в подготовленном окружении, он читает URL и контрольный путь из Config. Проверка выполняется перед обоими режимами: offline готовит SQL, online открывает соединение. Дополнительно в этом опыте мы требуем существующий файл внутри учебной папки.</p>
      <CodeBlock caption="migrations/env.py" code={baselineEnvExample} />
      <p>Создадим только новый пустой файл образца. Команда с режимом xb откажет, если имя уже занято:</p>
      <CodeBlock code={`python -c "from pathlib import Path; Path('reference.sqlite').open('xb').close()"`} />
      <p>Чтобы узнать его абсолютный путь для проверки, выполним следующую команду. Выбираем учебный путь без знака процента.</p>
      <CodeBlock code={`python -c "from pathlib import Path; print(Path('reference.sqlite').resolve())"`} />
      <p>В созданном alembic.ini в разделе [alembic] заменим значение sqlalchemy.url на URL ниже и добавим expected_database_path с только что выведенным абсолютным путём к reference.sqlite. В примере подставляем собственный путь; остальные настройки init сохраняем.</p>
      <CodeBlock caption="Пример настроек alembic.ini" code={`sqlalchemy.url = sqlite:///reference.sqlite
expected_database_path = C:/учебная_папка/notes_baseline/reference.sqlite`} />
      <p>Проверим совпадение путей в выводе и выполним следующую команду: ревизия создаст таблицу в пустом образце и запишет его версию.</p>
      <CodeBlock code="python -m alembic upgrade a1b2" />
      <p>Теперь создадим две заполненные базы. Сохраним следующий файл как <code>create_copies.py</code> рядом с alembic.ini и выполним <code>python create_copies.py</code>. Он читает SQL таблицы образца, создаёт notes.sqlite с тем же правилом, а в different.sqlite меняет только условие CHECK. В обе базы записывается одна и та же строка. Имена файлов должны быть свободны.</p>
      <CodeBlock caption="create_copies.py" code={baselineCopiesExample} />
      <p>Увидим «Созданы две заполненные учебные базы». Это две отдельные базы с одинаковыми значениями, а не копии файла версии Alembic. Теперь сохраним <code>compare_baseline.py</code> рядом с ними и выполним <code>python compare_baseline.py</code>. Код открывает каждую базу только для чтения и сравнивает пользовательские объекты и их SQL, свойства колонок, индексы и их состав, внешние ключи. Таблица alembic_version учитывается отдельно.</p>
      <CodeBlock caption="compare_baseline.py" code={baselineCompareExample} />
      <p>Для different.sqlite вывод содержит <code>схема совпадает: False</code>, для notes.sqlite <code>схема совпадает: True</code>. В обоих случаях строки равны <code>[(1, 3)]</code>, а версия пока <code>[]</code>. Различие CHECK видно в SQL из sqlite_schema, хотя значения одинаковы. Сравнение SQL намеренно строгое: при другом написании мы останавливаемся и не обещаем автоматическую проверку смысловой равнозначности.</p>
      <p>Different.sqlite мы не маркируем: схема отличается. Для notes.sqlite совпадение установлено, и в ней нет неожиданной версии. В alembic.ini изменим оба параметра на эту базу: URL <code>sqlite:///notes.sqlite</code>, а expected_database_path на её абсолютный путь, который получим той же командой Path.resolve, заменив имя файла. Сверим пути в выводе и последовательно выполним команды ниже. Идентификатор берём из нашей ревизии, а не подменяем словом head.</p>
      <CodeBlock caption="Проверяем, отмечаем и снова читаем версию" code={`python -m alembic current
python -m alembic stamp a1b2
python -m alembic current`} />
      <p>Первый current не показывает ID ревизии, stamp записывает a1b2, второй current показывает <code>a1b2 (head)</code>. Выполним <code>python compare_baseline.py</code> ещё раз: у notes.sqlite по-прежнему совпадает схема и остаётся <code>[(1, 3)]</code>, а версия стала <code>[('a1b2',)]</code>. У different.sqlite сохраняются False и пустая версия. Так мы видим отдельно решение не отмечать несовпадающую базу и результат маркировки подходящей.</p>
      <p>Представим ещё одну копию: её строки совпадают, но в схеме появился незнакомый триггер. Можно ли её отметить? Нет: даже сохранённые значения не объясняют действие триггера. Сначала выясняем назначение объекта, иначе останавливаемся. В практике мы самостоятельно примем такое решение для Planner.</p>
      <p>В этом опыте команда Alembic читает Config из alembic.ini. В тесте оба параметра можно заменить через set_main_option для своей временной базы; env.py получает именно эти переданные значения. Строка с процентом экранируется для Config как два знака процента. Конкретный проектный env.py и учебный каталог проверяем перед применением.</p>
    </Section>
    <Section number="05" title="Проверяем адрес до команды">
      <p>В alembic.ini единственный адрес подключения хранится в <code>sqlalchemy.url</code>. Значение <code>expected_database_path</code> в том же файле является отдельной проверкой ожидаемого абсолютного пути. В migrations/env.py нужно прочитать оба значения из Config и сравнить разрешённые пути до создания engine, подключения и offline configure. Неизвестный адрес или несовпадение останавливает операцию.</p>
      <p>На независимом примере Config читает значения секции alembic, make_url разбирает SQLite URL, а Path.resolve приводит путь к абсолютному виду. Сохраним следующий файл как check_address.py рядом с compare_notes.py. Создадим demo/alembic.ini с секцией [alembic], значением sqlalchemy.url в форме sqlite:/// с абсолютным путём к созданному demo/reference.sqlite и значением expected_database_path с тем же абсолютным путём без префикса sqlite. На Windows форма URL может быть такой:</p>
      <CodeBlock code="sqlite:///C:/учебная_папка/demo/reference.sqlite" />
      <p>Вместо примерного пути записываем свой. Запуск <code>python check_address.py</code> покажет два одинаковых пути и сообщение об успехе.</p>
      <CodeBlock code={guardExample} />
      <p>Если изменить только URL на другой файл, программа остановится до подключения. Разрешены драйверы sqlite и sqlite+pysqlite; адрес с host, портом, именем пользователя, паролем, query или базой в памяти отклоняется. Метод is_relative_to проверяет, что путь находится внутри demo. Пример показывает проверку, но её нужно встроить в env.py до обеих ветвей Alembic. Относительный SQLite URL разрешается относительно текущей папки; контрольный путь задаём абсолютным.</p>
      <p>Для каждой новой цели в ini синхронно меняем оба значения, выводим и сравниваем два абсолютных пути. В тестовом Config оба значения также задаются через <code>set_main_option</code>, а env.py читает именно переданные значения. Не направляем конфигурацию на исходную базу или резерв.</p>
      <p>Это ограничение сохраняется и при следующем изменении: Alembic принимает только существующий файл. Если заранее разрешена новая пустая учебная база, сначала проверяем, что её каталог существует и находится в разрешённой области, путь свободен, а URL и абсолютный контрольный путь указывают на выбранную цель. Затем отдельно создаём пустой файл через <code>Path(...).open('xb').close()</code>, как в опыте с образцом. Режим xb отказывает при занятом имени и не перезаписывает файл. Только после этого передаём согласованные значения в тот же Config и запускаем upgrade, который создаст схему. Заполненную копию и резерв заново таким способом не создаём; неизвестный путь означает остановку.</p>
      <p>Ранее сохранённые проверки истории тоже должны учитывать эту защиту. В <code>tests/test_db_schema.py</code> проверки исходной схемы и текущей истории получают отдельные файлы внутри временного каталога проекта и заранее резервируют их через xb. Проверка неверного адреса резервирует только ожидаемый файл, затем меняет URL: файл по ошибочному адресу должен остаться отсутствующим. Оба значения по-прежнему передаются в один Config. Если сохранённый тест ещё не подготовлен таким образом, сначала возвращаемся к его настройке; отказ guard сам по себе не доказывает ошибку миграции.</p>
      <p>Для адресной проверки из PowerShell в <code>project/</code> выберем новое свободное имя временного каталога. Параметр basetemp задаёт pytest место для tmp_path внутри проекта, разрешённого guard. Pytest может очистить указанный каталог, поэтому существующее имя использовать нельзя. Например, убедившись, что <code>.pytest-baseline-84</code> отсутствует, выполним:</p>
      <CodeBlock code="python -m pytest tests/test_db_schema.py --basetemp .pytest-baseline-84" />
      <p>Ожидаем успех проверок файла: отдельные базы получают исходную и текущую ревизии, неверный URL отклоняется до создания ошибочного файла. При неуспехе читаем причину и проверяем путь и подготовку файлов, не ослабляя guard. Это проверка текущей истории; дальнейшую адаптацию других тестов здесь ещё не считаем выполненной.</p>
      <p>Для одноразовой копии применяется подготовленная обёртка:</p>
      <CodeBlock code="python -m app.DBase.backup_snapshot SOURCE DEST" />
      <p>SOURCE это существующая восстановленная учебная база, DEST новый отдельный путь. При занятом DEST операция должна отказать без перезаписи. Само завершение без исключения не доказывает сохранность содержимого: открываем копию и сравниваем её записи.</p>
      <p>Например, после создания учебного файла notes используем существующий источник и новое назначение:</p>
      <CodeBlock code="python -m app.DBase.backup_snapshot demo/reference.sqlite demo/copy.sqlite" />
      <p>Затем открываем copy.sqlite только для чтения и сверяем SELECT с источником. Повтор той же команды должен отказать, потому что назначение занято, а содержимое copy.sqlite должно остаться прежним. Эта команда относится к подготовленному учебному проекту: прежде проверяем доступность его обёртки и тестируем её на собственных синтетических файлах.</p>
      <p>Чтобы создать контролируемое расхождение, используем отдельную копию. Для демонстрации сохраним следующий код в make_extra.py и вызовем его командой:</p>
      <CodeBlock code="python make_extra.py demo/copy.sqlite" />
      <p>В sys.argv[1] Python помещает первый аргумент после имени программы; Path разрешает его в полный путь. Файл должен существовать в demo; программа добавит таблицу extra_note и после commit закроет соединение. Перечень sqlite_schema покажет новый объект. Повтор откажет, если таблица уже есть.</p>
      <CodeBlock code={`import sqlite3
import sys
from pathlib import Path

path = Path(sys.argv[1]).resolve()
root = Path("demo").resolve()
if not path.is_relative_to(root) or not path.is_file():
    raise ValueError("Нужна существующая учебная копия")
db = sqlite3.connect(path)
try:
    db.execute("CREATE TABLE extra_note (id INTEGER)")
    db.commit()
finally:
    db.close()`} />
    </Section>
    <Section number="06" title="Что мы будем делать в практике">
      <Lead>Соединим проверку адреса, структуры и содержимого в один осторожный маршрут.</Lead>
      <LinkedNotes items={[
        {title:"Проверим вход",description:"Разделим резерв, образец и восстановленную копию и найдём исходную revision."},
        {title:"Сравним",description:"Снимем опись объектов и шести полей заполненной копии и сопоставим схему с образцом."},
        {title:"Проверим отличие",description:"На отдельной копии обнаружим изменение схемы и убедимся, что отметка не записана."},
        {title:"Отметим и проверим",description:"Для подходящей копии запишем конкретную исходную revision и повторим чтение."}
      ]} />
      <p>При расхождении или неизвестном объекте остановимся до маркировки. Следующему шагу передадим только копию с доказанным baseline, опись и отдельно сохранённый резерв.</p>
    </Section>
  </RichLesson>;
}
