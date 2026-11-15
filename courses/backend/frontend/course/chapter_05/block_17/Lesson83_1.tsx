import { CodeBlock, Diagram, DiagramArrow, DiagramFlow, DiagramNode, Lead, LinkedNotes, QuizCard, RichHero, RichLesson, Section } from "../../shared";

const inlineCodeWrap = { whiteSpace: "normal", overflowWrap: "anywhere", wordBreak: "break-word" } as const;

const smallPath = `from pathlib import Path
from sqlalchemy.engine import make_url

url = make_url("sqlite:///demo.sqlite3")
actual = Path(url.database).resolve()
expected = Path("demo.sqlite3").resolve()
print(url.drivername, url.database)
print(actual == expected)`;

const smallSchema = `import sqlite3

db = sqlite3.connect(":memory:")
try:
    db.execute("CREATE TABLE counters (id INTEGER PRIMARY KEY, value INTEGER NOT NULL CHECK (value > 0))")
    for row in db.execute("PRAGMA table_info(counters)"):
        print(row[1], row[2], row[3], row[4], row[5])
    print("CHECK (value > 0)" in db.execute(
        "SELECT sql FROM sqlite_schema WHERE name = 'counters'"
    ).fetchone()[0])
finally:
    db.close()`;

const pathCheck = `from pathlib import Path
from sqlalchemy.engine import make_url

def checked_path(config):
    raw_url = config.get_main_option("sqlalchemy.url")
    expected = config.get_main_option("expected_database_path")
    if not raw_url or not expected:
        raise ValueError("Нужны URL и ожидаемый путь")
    url = make_url(raw_url)
    if (url.drivername not in {"sqlite", "sqlite+pysqlite"}
            or not url.database or url.database == ":memory:"
            or url.host or url.username or url.password or url.port or url.query):
        raise ValueError("Нужен локальный файл SQLite")
    expected_path = Path(expected)
    if not expected_path.is_absolute():
        raise ValueError("Ожидаемый путь должен быть абсолютным")
    actual = Path(url.database).resolve()
    expected_path = expected_path.resolve()
    print("Фактический:", actual)
    print("Ожидаемый:", expected_path)
    if actual != expected_path:
        raise ValueError("Пути не совпадают")
    return actual`;

const exampleModel = `from sqlalchemy import CheckConstraint, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class Book(Base):
    __tablename__ = "books"
    __table_args__ = (CheckConstraint("pages > 0", name="ck_books_pages"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    pages: Mapped[int] = mapped_column(Integer, nullable=False)`;

const envCode = `from logging.config import fileConfig
from alembic import context
from sqlalchemy import engine_from_config, pool
from book_model import Base
from address_check import checked_path

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata

def run_migrations_offline():
    checked_path(config)  # до configure
    context.configure(url=config.get_main_option("sqlalchemy.url"),
                      target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online():
    checked_path(config)  # до engine и соединения
    engine = engine_from_config(config.get_section(config.config_ini_section),
                                prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()`;

const checkSql = `import sqlite3

db = sqlite3.connect("catalog.sqlite3")
try:
    print(db.execute("SELECT version_num FROM alembic_version").fetchall())
    print(db.execute("PRAGMA table_info(books)").fetchall())
    print(db.execute("PRAGMA index_list(books)").fetchall())
    print(db.execute("PRAGMA foreign_key_list(books)").fetchall())
    print(db.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema "
                     "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall())
finally:
    db.close()`;

const constraints = `import sqlite3

db = sqlite3.connect("catalog.sqlite3")
try:
    db.execute("INSERT INTO books (id, title, pages) VALUES (?, ?, ?)",
               (1, "Atlas", 12))
    db.commit()
    try:
        db.execute("INSERT INTO books (id, title, pages) VALUES (?, ?, ?)",
                   (2, "Empty", 0))
        db.commit()
    except sqlite3.IntegrityError as error:
        print("Ограничение:", error)
        db.rollback()
    print(db.execute("SELECT id, title, pages FROM books").fetchall())
finally:
    db.close()`;

const defaultsExample = `from sqlalchemy import Column, Integer, MetaData, Table, create_engine
from sqlalchemy.exc import IntegrityError

engine = create_engine("sqlite:///:memory:")
metadata = MetaData()
client = Table("client", metadata,
    Column("id", Integer, primary_key=True),
    Column("count", Integer, nullable=False, default=3))
server = Table("server", metadata,
    Column("id", Integer, primary_key=True),
    Column("count", Integer, nullable=False, server_default="3"))
metadata.create_all(engine)
with engine.begin() as conn:
    conn.execute(client.insert().values(id=1))
    conn.exec_driver_sql("INSERT INTO server (id) VALUES (1)")
    print(conn.exec_driver_sql("SELECT count FROM client").all())
    print(conn.exec_driver_sql("SELECT count FROM server").all())
try:
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO client (id) VALUES (2)")
except IntegrityError:
    print("Нет SQL DEFAULT")
engine.dispose()`;

const testSample = `from pathlib import Path
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL
import sqlite3
import pytest

def config_for(path):
    path = path.resolve()
    cfg = Config("alembic.ini")
    url = URL.create("sqlite", database=path.as_posix()).render_as_string(hide_password=False)
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    cfg.set_main_option("expected_database_path", str(path).replace("%", "%%"))
    return cfg

def test_initial_schema(tmp_path):
    db_path = tmp_path / "first.sqlite3"
    db_path.open("xb").close()
    cfg = config_for(db_path)
    baseline = ScriptDirectory.from_config(cfg).get_base()
    command.upgrade(cfg, baseline)
    db = sqlite3.connect(db_path)
    try:
        names = [row[1] for row in db.execute("PRAGMA table_info(books)")]
        revision = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        db.close()
    assert names == ["id", "title", "pages"]
    assert revision == baseline

def test_current_schema(tmp_path):
    db_path = tmp_path / "current.sqlite3"
    db_path.open("xb").close()
    cfg = config_for(db_path)
    head = ScriptDirectory.from_config(cfg).get_current_head()
    command.upgrade(cfg, "head")
    db = sqlite3.connect(db_path)
    try:
        names = [row[1] for row in db.execute("PRAGMA table_info(books)")]
        revision = db.execute("SELECT version_num FROM alembic_version").fetchone()[0]
    finally:
        db.close()
    assert names == ["id", "title", "pages", "shelf"]
    assert revision == head

def test_wrong_address(tmp_path):
    allowed = tmp_path / "allowed.sqlite3"
    wrong = tmp_path / "wrong.sqlite3"
    allowed.open("xb").close()
    cfg = config_for(allowed)
    wrong_url = URL.create("sqlite", database=wrong.resolve().as_posix()).render_as_string(hide_password=False)
    cfg.set_main_option("sqlalchemy.url", wrong_url.replace("%", "%%"))
    with pytest.raises(ValueError):
        command.upgrade(cfg, "head")
    assert not wrong.exists()`;

const addShelf = `def upgrade():
    op.add_column("books", sa.Column("shelf", sa.String(), nullable=True))

def downgrade():
    op.drop_column("books", "shelf")`;

export function Lesson83_1() {
  return <RichLesson className="lesson-83-1">
    <style>{`.lesson-83-1 .rich-section p > code,
      .lesson-83-1 .rich-section li > code {
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: break-word;
      }`}</style>
    <RichHero chip="ИСТОРИЯ СХЕМЫ" title="Baseline новой базы" intro="Создадим первую ревизию и убедимся, что одна история воспроизводит исходную схему на новых учебных базах." />
    <Section number="00" title="От настроенной истории к первой схеме">
      <Lead>Настроенный инструмент знает адрес базы и модель. Но пустой журнал ещё не содержит инструкции, как построить таблицу.</Lead>
      <LinkedNotes items={[
        { title: "Точка старта", description: "У нас есть изолированная настройка Alembic и опись исходной схемы." },
        { title: "Новый вопрос", description: "Как создать ту же схему без старой заполненной базы?" },
        { title: "Результат", description: "Подготовим исходную ревизию и проверим её на двух пустых файлах." },
      ]} />
      <p>Исходная ревизия, или baseline, описывает состояние схемы в начале истории. Мы сравним её с описью и проверим настоящие ограничения, а не только наличие файла.</p>
    </Section>
    <Section number="01" title="Проверяем адрес до подключения">
      <Lead>SQLite может создать отсутствующий файл при подключении. Поэтому сначала выясним, куда ведёт адрес, и сравним его с разрешённым путём.</Lead>
      <p>Работаем из <code>project/</code> изолированной учебной копии, в подготовленной среде. Команды <code>python --version</code>, <code>python -m alembic --version</code> и <code>python -m pytest --version</code> показывают версии выбранного интерпретатора и пакетов. Если пакет не найден, вернёмся к настройке среды.</p>
      <p>В <code>alembic.ini</code> единственный адрес соединения хранится в <code>sqlalchemy.url</code>. Рядом задаём абсолютный <code>expected_database_path</code>: это контрольное значение, а не второй адрес подключения. При переключении учебного файла меняем оба значения. Снимок и оригинал не выбираем для этого опыта.</p>
      <p>Прежде чем читать проверку целиком, разберём один её шаг. В отдельном учебном каталоге сохраним <code>read_address.py</code> и запустим <code>python read_address.py</code>. Этот код только разбирает строку и сравнивает пути: файл базы не открывается.</p>
      <CodeBlock caption="Разбираем один адрес, read_address.py" code={smallPath} />
      <p>Первая строка вывода: <code>sqlite demo.sqlite3</code>. Это имя драйвера и путь из URL. Вторая строка <code>True</code> означает, что оба имени после разрешения указывают на один путь. Если заменить только имя в expected на <code>other.sqlite3</code>, увидим <code>False</code>. Теперь посмотрим, как проверка останавливает Alembic при таком различии.</p>
      <p>Рассмотрим отдельный пример в каталоге <code>sandbox</code>. Сохраним следующий файл как <code>address_check.py</code>. Объект Config передаёт настройки; <code>get_main_option</code> читает строку. <code>make_url</code> разбирает адрес, а <code>Path.resolve()</code> приводит путь к абсолютному. Проверка выполняется до создания engine и до offline-настройки.</p>
      <CodeBlock caption="Проверка адреса, address_check.py" code={pathCheck} />
      <p>Пустые значения, SQLite в памяти, параметры URI и другой драйвер здесь не подходят: опыт предназначен для обычного локального файла. Если пути различаются, возникает ошибка до подключения. Для относительного URL путь разрешается относительно текущего каталога запуска. Перед использованием выбираем новый файл: команда <code style={inlineCodeWrap}>python -c "from pathlib import Path; print(Path('catalog.sqlite3').exists())"</code> должна показать <code>False</code>.</p>
      <QuizCard question="Мы поменяли только URL, оставив прежний ожидаемый путь. Что должно произойти?" options={["Отказ до подключения, новый файл не появляется", "Инструмент сам меняет ожидаемый путь и открывает новую базу"]} correctIndex={0} explanation="Ожидаемый путь задаётся отдельно. Несовпадение останавливает работу до открытия SQLite." />
    </Section>
    <Section number="02" title="Независимый пример: каталог книг">
      <Lead>Сначала рассмотрим другую таблицу и полный переход от модели к кандидату ревизии. Исходный проект Planner при этом не нужен.</Lead>
      <p>Создадим отдельный пустой каталог <code>sandbox</code>, перейдём в него и выполним <code>python -m alembic init migrations</code>. В <code>book_model.py</code> запишем модель. Здесь <code>CheckConstraint</code> требует положительное число страниц, а <code>nullable=False</code> запрещает отсутствие значения.</p>
      <CodeBlock caption="Модель, book_model.py" code={exampleModel} />
      <p>В созданном <code>alembic.ini</code> в разделе <code>[alembic]</code> зададим <code>sqlalchemy.url = sqlite:///catalog.sqlite3</code> и <code>expected_database_path</code> равным абсолютному пути к этому новому файлу. Его можно узнать командой <code style={inlineCodeWrap}>python -c "from pathlib import Path; print(Path('catalog.sqlite3').resolve())"</code>. До подключения убедимся, что файл отсутствует. Сохраним показанную выше проверку как <code>address_check.py</code>.</p>
      <p>Заменим содержимое <code>migrations/env.py</code> следующим файлом. Импорт модели передаёт её metadata для сравнения. Проверка адреса стоит перед обоими режимами: онлайн открывает соединение, offline готовит SQL без него.</p>
      <CodeBlock caption="Окружение примера, migrations/env.py" code={envCode} />
      <p>Для проверки отказа изменим в ini только URL на отсутствующий <code>sqlite:///wrong.sqlite3</code>, сохранив прежний ожидаемый путь. Команда <code>python -m alembic current</code> должна вывести ошибку о несовпадении. Проверим отсутствие файла командой <code style={inlineCodeWrap}>python -c "from pathlib import Path; print(Path('wrong.sqlite3').exists())"</code>: ожидаем <code>False</code>. Вернём согласованный URL. Для нового второго файла меняем оба значения, снова сравниваем разрешённые пути и только затем запускаем команду.</p>
      <p>В каталоге <code>sandbox</code> выполним <code>python -m alembic revision --autogenerate -m "initial books"</code>. Параметр <code>--autogenerate</code> сравнивает модель с выбранной пустой базой, а <code>-m</code> задаёт подпись. Появится файл в <code>migrations/versions/</code>. Его имя и значение <code>revision</code> генерируются при запуске; у первой ревизии <code>down_revision = None</code>.</p>
      <p>В выводе строка <code>Detected added table 'books'</code> означает, что в модели найдена таблица, которой нет в выбранной базе. Строка <code>Generating</code> сообщает путь нового файла. Обе строки относятся к созданию кандидата: таблица в базе от этого ещё не появляется. Если инструмент сообщает о другом адресе или ошибке импорта, остановимся и вернёмся к настройке.</p>
      <p>Откроем файл и прочитаем <code>upgrade()</code>. Он должен создавать оба обязательных столбца и ключ, а также CHECK из модели. Если CHECK отсутствует, добавим в вызов <code>op.create_table</code> элемент <code>sa.CheckConstraint("pages &gt; 0", name="ck_books_pages")</code>. Генератор предлагает кандидат: его вывод всегда нужно просматривать.</p>
      <p>Проследим переход от проверенного файла к состоянию отдельной пустой базы.</p>
      <Diagram caption="От файла истории к созданной таблице" orientation="vertical">
        <DiagramFlow label="Создание исходной схемы" orientation="vertical">
          <DiagramNode title="Проверенная ревизия"><p>Файл содержит операцию создания таблицы</p></DiagramNode>
          <DiagramArrow label="upgrade применяет операцию к выбранной пустой базе" />
          <DiagramNode title="База после upgrade" shape="database"><p>Таблица создана, текущая версия записана</p></DiagramNode>
        </DiagramFlow>
      </Diagram>
      <p>До применения файл существует отдельно, а таблица в пустой базе ещё не создана. При дальнейшем изменении модели сохранённая ревизия остаётся прежним описанием.</p>
      <p>Затем выполним <code>python -m alembic upgrade head</code> и <code>python -m alembic current</code>. Первое применяет ревизию, второе показывает записанную версию. Команда <code>python -m alembic heads</code> показывает конец истории файлов. Файл ревизии сам по себе не создаёт таблицу, а <code>stamp</code> только записывает метку и не выполняет создание.</p>
      <p>В выводе upgrade переход от пустого начала к идентификатору сообщает, какая операция выполнена. После current тот же идентификатор рядом с пометкой head означает, что выбранная база достигла конца этой истории. Сами символы идентификатора у каждого запуска свои: берём их из созданного файла, а не из примера. Попробуем объяснить изменённый случай: если файл ревизии уже есть, но upgrade ещё не запускался, что увидим в самой базе? Таблицы книги там пока нет, потому что инструкция ещё не выполнялась.</p>
    </Section>
    <Section number="03" title="Схема в ревизии и значения по умолчанию">
      <Lead>История должна описывать прежнее состояние даже после того, как модель приложения изменится.</Lead>
      <p>В ревизии <code>op.create_table</code> получает имя таблицы, столбцы <code>sa.Column</code> и ограничения. У столбца задаём тип, <code>nullable</code> и при необходимости <code>primary_key</code>. Код ревизии хранит эти значения непосредственно: импортировать текущую <code>Base</code> и вызывать <code>create_all</code> внутри старой ревизии нельзя, иначе её результат изменится вместе с моделью.</p>
      <p>Параметр модели <code>default=3</code> применяется SQLAlchemy при формировании вставки. <code>server_default</code> записывает DEFAULT в DDL таблицы. При прямом SQL INSERT без обязательного значения первый вариант не подставит число на стороне SQLite; второй может это сделать. Поэтому не превращаем Python-default в SQL-default без основания в исходной схеме.</p>
      <p>Проверим различие отдельно от Planner. Сохраним <code>defaults_example.py</code> в учебном каталоге и выполним <code>python defaults_example.py</code>. Создаём две таблицы в памяти процесса. Первая вставка формируется SQLAlchemy, вторая отправляет SQL напрямую через <code>exec_driver_sql</code>; <code>all()</code> возвращает строки результата.</p>
      <CodeBlock caption="Клиентское и серверное значение" code={defaultsExample} />
      <p>В обеих таблицах читаем <code>[(3,)]</code>, но причины разные. Последний прямой INSERT в таблицу с клиентским default вызывает ошибку обязательного поля и печатает «Нет SQL DEFAULT». База в памяти исчезнет после завершения процесса.</p>
      <QuizCard question="В таблице нет SQL DEFAULT, столбец обязателен. Что произойдёт при прямом INSERT без него?" options={["SQLite отклонит вставку", "Python-default модели сработает внутри SQLite"]} correctIndex={0} explanation="Прямой SQL не вызывает клиентскую подстановку SQLAlchemy. Обязательное значение отсутствует." />
    </Section>
    <Section number="04" title="Смотрим на настоящую базу">
      <Lead>Записанная версия сообщает, какую ревизию инструмент считает применённой. Чтобы проверить схему, читаем саму SQLite.</Lead>
      <p>Сначала потренируем чтение на маленькой временной таблице. Сохраним <code>read_shape.py</code> в учебном каталоге и выполним <code>python read_shape.py</code>. База существует только в памяти этого процесса и не затрагивает файлы проекта.</p>
      <CodeBlock caption="Читаем два столбца, read_shape.py" code={smallSchema} />
      <p>Получим строки <code>id INTEGER 0 None 1</code>, <code>value INTEGER 1 None 0</code> и <code>True</code>. Число после типа показывает обязательность, <code>None</code> показывает отсутствие SQL DEFAULT, а последнее число обозначает первичный ключ. Для SQLite в этом примере первичный ключ id сообщает отдельный признак <code>pk=1</code>, хотя <code>notnull=0</code>. Последняя строка подтверждает, что SQL создания содержит CHECK. Изменим поиск на <code>CHECK (value &gt; 5)</code>: результат станет <code>False</code>, сама таблица не изменится.</p>
      <p>В каталоге <code>sandbox</code> сохраним код как <code>inspect_books.py</code> и выполним <code>python inspect_books.py</code>. Открываем только созданный учебный файл. <code>execute</code> отправляет запрос, <code>fetchall</code> возвращает все строки, а <code>finally</code> закрывает соединение даже при ошибке.</p>
      <CodeBlock caption="Читаем структуру и версию" code={checkSql} />
      <p>В <code>table_info</code> видны имя и тип столбца, признаки <code>notnull</code> и <code>pk</code>, SQL-значение по умолчанию <code>dflt_value</code>. <code>sqlite_schema</code> показывает DDL и пользовательские объекты; отдельные PRAGMA перечисляют индексы и внешние ключи. Пустой список индексов или внешних ключей может быть ожидаемым. Проверяем имена столбцов, обязательность и текст CHECK, а не только строку версии.</p>
      <p>Например, строка <code>(2, 'pages', 'INTEGER', 1, None, 0)</code> означает: третий столбец называется pages, хранит INTEGER, обязателен, не имеет SQL DEFAULT и не является ключом. Пустой список после запроса индексов означает, что в этом примере отдельных индексов нет. Если в SQL создания отсутствует <code>pages &gt; 0</code>, один правильный номер ревизии не подтверждает ограничение.</p>
      <QuizCard question="Две учебные базы показывают одну версию, но в одной отсутствует CHECK. Достаточно ли совпадения версии?" options={["Нет, проверяем DDL и поведение ограничения", "Да, версия сама восстанавливает CHECK"]} correctIndex={0} explanation="Метка не меняет таблицу. Фактический DDL и пробная вставка дают отдельные свидетельства." />
    </Section>
    <Section number="05" title="Проверяем ограничение на данных">
      <Lead>Проверка DDL полезна, но отдельно убедимся, что ограничение отклоняет неправильное значение.</Lead>
      <p>В том же каталоге сохраним <code>try_books.py</code> и запустим его на только что созданной учебной базе. Знаки <code>?</code> передают значения отдельно от SQL. <code>commit()</code> сохраняет успешную вставку, а после <code>IntegrityError</code> выполняем <code>rollback()</code>, чтобы отменить ошибочную транзакцию.</p>
      <CodeBlock caption="Допустимая и недопустимая строки" code={constraints} />
      <p>Увидим сообщение о CHECK и строку <code>[(1, 'Atlas', 12)]</code>. Отказ из-за отсутствующей таблицы или другого обязательного столбца не доказывает нужное ограничение. После повторного <code>python -m alembic upgrade head</code> откроем новое соединение и сравним строку и версию: повтор не должен создавать таблицу заново или менять данные.</p>
      <p>В строке результата число 1 является ключом книги, Atlas её названием, а 12 количеством страниц. Ошибочная книга отсутствует: её вставка не прошла CHECK. Для самостоятельной пробы поменяем только число страниц в отклонённой записи на отрицательное: ожидаем тот же отказ, тогда как уже сохранённая книга останется.</p>
    </Section>
    <Section number="06" title="Историческая и актуальная проверка">
      <Lead>Когда появится следующая ревизия, слово head будет обозначать новый конец истории. Проверке начальной схемы нужна конкретная исходная ревизия.</Lead>
      <p>В тесте <code>tmp_path</code> даёт отдельный временный каталог внутри проекта, если указать его через --basetemp. До первого upgrade зарезервируем разрешённый файл через <code>path.open("xb").close()</code>: режим xb не перезапишет занятый путь. При повторе upgrade резервировать тот же файл снова не нужно. Config только задаёт настройки: создадим его из того же ini и укажем оба значения для файла. <code>set_main_option</code> заменяет настройку в конкретном объекте; <code>env.py</code> читает эти значения и сверяет пути до соединения. Символ процента в значении ini удваиваем для ConfigParser. <code>ScriptDirectory.get_base()</code> находит первую ревизию в линейной истории примера.</p>
      <p>Для сравнения двух целей продолжим пример книг. После создания первой ревизии выполним <code>python -m alembic revision -m "add shelf"</code>. В новом файле сохраняем сгенерированные импорты <code>op</code> и <code>sa</code>, а тела функций заменяем следующим кодом. Колонку Planner пока не добавляем.</p>
      <CodeBlock caption="Вторая ревизия примера книг" code={addShelf} />
      <p>Сохраним пример как <code>test_books.py</code> в <code>sandbox</code> и из этого каталога в PowerShell выполним <code>python -m pytest test_books.py --basetemp ".pytest-runs/run-$([guid]::NewGuid().ToString('N'))"</code>. Каждый запуск получает новый выделенный каталог внутри sandbox; pytest может очистить переданный --basetemp, поэтому не указываем существующий каталог с нужными файлами. Первый тест строит базовую схему, второй применяет обе ревизии на другом временном файле. Третий сохраняет ожидаемый путь и меняет только URL. <code>pytest.raises</code> проверяет тип ошибки, а <code>exists()</code> показывает, появился ли файл. Ожидаем три успешных теста; второй обнаруживает дополнительный <code>shelf</code>, третий подтверждает отказ.</p>
      <CodeBlock caption="Тест исходной ревизии" code={testSample} />
      <p>Для своего проекта вместо динамического поиска сохраним в историческом тесте фактический ID принятой baseline из файла ревизии. Отдельный тест на другом временном пути выполняет <code>command.upgrade(cfg, "head")</code> и сравнивает текущую схему с актуальным ожиданием. На первом шаге обе цели совпадают; после расширения историческая проверка остаётся на старом ID. Для теста внутри <code>tests/</code> выражение <code style={inlineCodeWrap}>Path(__file__).resolve().parents[1]</code> находит каталог проекта, чтобы ini и история не зависели от места запуска.</p>
    </Section>
    <Section number="07" title="Что мы будем делать в практике">
      <Lead>Перенесём механизм на учебный Planner, сохраняя исходную базу и снимок.</Lead>
      <LinkedNotes items={[
        { title: "Выберем адреса", description: "Два новых пути отделены от исходных данных, а неверный URL останавливается до создания файла." },
        { title: "Подготовим ревизию", description: "Прочитаем и при необходимости исправим кандидат, затем получим его фактический ID." },
        { title: "Сверим результат", description: "Две пустые базы получат исходную схему; проверим ограничения и повтор без потери записи." },
        { title: "Сохраним проверки", description: "Исторический тест выберет конкретную ревизию, отдельный тест проверит текущий head." },
      ]} />
      <p>Каждый результат станет опорой следующего действия: адрес ограничивает место работы, ревизия создаёт схему, а тесты позволяют проверить её снова. Фактический ID и образец передадим для последующего сравнения с заполненной копией; её маркировка и расширение схемы сейчас не выполняются.</p>
    </Section>
  </RichLesson>;
}
