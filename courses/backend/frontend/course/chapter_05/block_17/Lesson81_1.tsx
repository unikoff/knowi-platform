import { Callout, CodeBlock, CodeSequence, Diagram, DiagramArrow, DiagramFlow, DiagramNode, Lead, LinkedNotes, RichHero, RichLesson, Section, TypeCard, TypeCards } from "../../shared";

const setup = `from pathlib import Path
import sqlite3

path = Path("books-demo.sqlite3").resolve()
if path.exists():
    raise SystemExit("Выберем новое имя для учебной базы")
connection = sqlite3.connect(path)
try:
    connection.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT NOT NULL, copies INTEGER NOT NULL DEFAULT 3, CONSTRAINT ck_copies CHECK (copies >= 0))")
    connection.execute("CREATE INDEX ix_books_title ON books (title)")
    connection.execute("INSERT INTO books (id, title) VALUES (2, 'Atlas')")
    connection.execute("INSERT INTO books (id, title, copies) VALUES (7, 'Notes', 1)")
    connection.commit()
finally:
    connection.close()
print("Учебная база создана:", path)`;

const read = `from pathlib import Path
from contextlib import closing
import sqlite3

path = Path("books-demo.sqlite3").resolve()
print("Проверяем путь:", path)
if not path.is_file():
    raise SystemExit("Учебный файл не найден")
if input("Это выбранный учебный файл? Введите да: ").strip().lower() != "да":
    raise SystemExit("Путь не подтверждён")
uri = path.as_uri() + "?mode=ro"
with closing(sqlite3.connect(uri, uri=True)) as connection:
    print("Таблицы:", connection.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' ORDER BY name"
    ).fetchall())
    print("Столбцы:", connection.execute("PRAGMA table_info('books')").fetchall())`;

const inspectObjects = `from pathlib import Path
from contextlib import closing
import sqlite3

path = Path("books-demo.sqlite3").resolve()
print("Проверяем путь:", path)
if not path.is_file():
    raise SystemExit("Учебный файл не найден")
if input("Это выбранный учебный файл? Введите да: ").strip().lower() != "да":
    raise SystemExit("Путь не подтверждён")
with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
    print("Объекты:", db.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"
    ).fetchall())
    print("Все столбцы:", db.execute("PRAGMA table_xinfo('books')").fetchall())
    indexes = db.execute("PRAGMA index_list('books')").fetchall()
    print("Индексы:", indexes)
    print("Внешние ключи:", db.execute("PRAGMA foreign_key_list('books')").fetchall())
    for index in indexes:
        name = index[1]
        print("Поля индекса:", name, db.execute(
            "SELECT * FROM pragma_index_info(?)", (name,)
        ).fetchall())
        print("Подробности индекса:", db.execute(
            "SELECT * FROM pragma_index_xinfo(?)", (name,)
        ).fetchall())`;

const inventory = `from pathlib import Path
from contextlib import closing
import sqlite3

path = Path("books-demo.sqlite3").resolve()
print("Проверяем путь:", path)
if not path.is_file():
    raise SystemExit("Учебный файл не найден")
if input("Это выбранный учебный файл? Введите да: ").strip().lower() != "да":
    raise SystemExit("Путь не подтверждён")
with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
    print("Количество:", db.execute("SELECT COUNT(*) FROM books").fetchall())
    print("Строки:", db.execute(
        "SELECT id, title, copies FROM books ORDER BY id"
    ).fetchall())`;

const mismatch = `from pathlib import Path
from contextlib import closing
import sqlite3

good = Path("books-good.sqlite3").resolve()
missing = Path("books-missing-check.sqlite3").resolve()
if good == missing or good.exists() or missing.exists():
    raise SystemExit("Нужны два новых разных пути")
for path, sql in [
    (good, "CREATE TABLE books (id INTEGER PRIMARY KEY, copies INTEGER NOT NULL, CONSTRAINT ck_copies CHECK (copies >= 0))"),
    (missing, "CREATE TABLE books (id INTEGER PRIMARY KEY, copies INTEGER NOT NULL)"),
]:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(sql)
        connection.commit()
for path in (good, missing):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        print(path.name, connection.execute("PRAGMA table_info('books')").fetchall())
        print(connection.execute("SELECT sql FROM sqlite_schema WHERE name='books'").fetchone())`;

const nullableProbe = `from pathlib import Path
from contextlib import closing
import sqlite3

required = Path("probe-required.sqlite3").resolve()
optional = Path("probe-nullable.sqlite3").resolve()
if required == optional or required.exists() or optional.exists():
    raise SystemExit("Выберем два новых разных пути")
for path, sql in [
    (required, "CREATE TABLE supplies (id INTEGER PRIMARY KEY, quantity INTEGER NOT NULL)"),
    (optional, "CREATE TABLE supplies (id INTEGER PRIMARY KEY, quantity INTEGER)"),
]:
    with closing(sqlite3.connect(path)) as db:
        db.execute(sql)
        db.commit()
for path in (required, optional):
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        print(path.name, db.execute("PRAGMA table_info('supplies')").fetchall())`;

const createAll = `from pathlib import Path
from contextlib import closing
import sqlite3
from sqlalchemy import CheckConstraint, Column, Integer, MetaData, Table, create_engine

path = Path("books-old.sqlite3").resolve()
if path.exists():
    raise SystemExit("Нужен новый путь")
with closing(sqlite3.connect(path)) as connection:
    connection.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, pages INTEGER NOT NULL)")
    connection.commit()
    print("До:", connection.execute("SELECT sql FROM sqlite_schema WHERE name='books'").fetchone()[0])
metadata = MetaData()
Table("books", metadata, Column("id", Integer, primary_key=True),
      Column("pages", Integer, nullable=False),
      CheckConstraint("pages > 0", name="ck_pages"))
engine = create_engine("sqlite:///" + path.as_posix())
try:
    metadata.create_all(engine)
finally:
    engine.dispose()
with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
    print("После:", connection.execute("SELECT sql FROM sqlite_schema WHERE name='books'").fetchone()[0])`;

const modelExample = `from sqlalchemy import CheckConstraint, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    pass

class PackageRow(Base):
    __tablename__ = "packages"
    __table_args__ = (CheckConstraint("weight > 0", name="ck_weight"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

print("Таблица:", PackageRow.__tablename__)
print("Ключ:", PackageRow.__table__.c.id.primary_key)
print("Обязательность:", PackageRow.__table__.c.weight.nullable)
print("Python default:", PackageRow.__table__.c.weight.default.arg)
print("Правило:", PackageRow.__table_args__[0].name)`;

const relationships = `from pathlib import Path
from contextlib import closing
import sqlite3

path = Path("library-links.sqlite3").resolve()
if path.exists():
    raise SystemExit("Выберем новый путь")
with closing(sqlite3.connect(path)) as db:
    db.execute("CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    db.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, author_id INTEGER REFERENCES authors(id))")
    db.execute("CREATE VIEW book_authors AS SELECT id, author_id FROM books")
    db.commit()
with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
    print("Связи:", db.execute("PRAGMA foreign_key_list('books')").fetchall())
    print("Представления:", db.execute("SELECT name FROM sqlite_schema WHERE type='view'").fetchall())`;

const defaults = `from pathlib import Path
from contextlib import closing
import sqlite3
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, insert

path = Path("defaults-demo.sqlite3").resolve()
if path.exists():
    raise SystemExit("Выберем новый путь")
metadata = MetaData()
items = Table("items", metadata, Column("id", Integer, primary_key=True),
              Column("amount", Integer, nullable=False, default=3))
engine = create_engine("sqlite:///" + path.as_posix())
try:
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(items).values(id=1))
finally:
    engine.dispose()
with closing(sqlite3.connect(path)) as db:
    try:
        db.execute("INSERT INTO items (id) VALUES (2)")
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        print("Прямой SQL без amount отклонён")
    print("Строки:", db.execute("SELECT id, amount FROM items").fetchall())
    print("Определение:", db.execute("PRAGMA table_info('items')").fetchall())`;

export function Lesson81_1() {
  return (
    <RichLesson>
      <RichHero chip="SQLite и Planner" title="Сверяем реальную схему" intro="Выясним, что находится в учебном файле базы, и сравним наблюдение с моделью до изменения схемы." />
      <Section number="00" title="От сохранённых задач к устройству базы">
        <Lead>В учебном Planner мы можем прочитать задачи после перезапуска. Теперь выясним, какие правила действительно записаны в файле SQLite.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "У нас есть адрес отдельной учебной базы и две синтетические задачи после проверки Planner." },
          { title: "Новый вопрос", description: "Совпадает ли устройство существующей таблицы с моделью приложения?" },
          { title: "Результат", description: "Составим опись схемы и значений и обнаружим расхождение на отдельном образце." },
        ]} />
        <p>Проверяемый адрес связывает результаты с нужным файлом. Затем сравниваем его структуру и содержимое с тем, что ожидает приложение.</p>
      </Section>
      <Section number="01" title="Модель, схема и строки">
        <Lead>Модель описывает ожидаемую таблицу в Python. Схема хранит фактическое описание таблиц в SQLite. Строки содержат значения задач.</Lead>
        <p>Метод <code>create_all</code> создаёт отсутствующую таблицу, но не добавляет ограничения в уже существующую. Проверим это на отдельном каталоге книг. Сохраним код в <code>create_books_old.py</code> внутри учебного <code>project/</code> и запустим <code>python create_books_old.py</code>. Нужен установленный в учебной среде SQLAlchemy.</p>
        <CodeBlock caption="Существующая таблица и новая metadata" code={createAll} />
        <p>Сначала SQL создаёт таблицу. Затем <code>Table</code> описывает ожидаемое правило, а <code>metadata.create_all(engine)</code> находит существующую таблицу. Строки «До:» и «После:» показывают одно и то же определение; <code>dispose()</code> освобождает подключения. Сопоставим ожидаемое правило с результатом чтения файла.</p>
        <Diagram caption="Сравниваем ожидание с существующей таблицей books" orientation="vertical">
          <DiagramFlow label="Ожидание, наблюдение и вывод" orientation="vertical">
            <DiagramNode title="Ожидаем по модели"><p>В таблице books должно быть ограничение <code>ck_pages</code>: pages &gt; 0.</p></DiagramNode>
            <DiagramArrow label="Сравниваем с CREATE в файле" />
            <DiagramNode title="Видим в SQLite" shape="database"><p>CREATE содержит поля id и pages, но ограничения <code>ck_pages</code> в нём нет.</p></DiagramNode>
            <DiagramArrow label="Делаем вывод из расхождения" />
            <DiagramNode title="Результат сравнения"><p>Ожидаемое правило отсутствует: успешный <code>create_all</code> не добавил его в существующую таблицу.</p></DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>Здесь мы прочитали определение таблицы, но не её строки. Для описи сохранённых значений понадобится отдельное чтение: наличие таблицы и её полей само по себе не подтверждает наличие CHECK.</p>
      </Section>
      <Section number="02" title="Выбираем файл и открываем его для чтения">
        <Lead>Перед наблюдением остановим учебные процессы и сверим путь с описью. Обычное подключение к ошибочному пути может создать новый пустой файл.</Lead>
        <p>Создадим отдельный пример в учебной копии. Сохраним код ниже как <code>make_books.py</code> и запустим из <code>project/</code> командой <code>python make_books.py</code>. Имя уже занято: останавливаемся и выбираем другое, не перезаписываем файл.</p>
        <CodeBlock caption="Подготовка синтетических книг" code={setup} />
        <p><code>Path.resolve()</code> показывает абсолютный путь, <code>exists()</code> проверяет занятость, <code>connect</code> создаёт новый файл. <code>execute</code> выполняет SQL, <code>commit</code> сохраняет строки, <code>close</code> закрывает соединение. Значение <code>3</code> для первой книги задаёт SQL DEFAULT. Учебный каталог принадлежит нам и во время опыта никто другой в нём не пишет.</p>
        <p>Теперь сохраним следующий код как <code>read_books.py</code> и выполним <code>python read_books.py</code> из того же каталога. Программа напечатает абсолютный путь и остановится в ожидании ответа. Сравним его с выбранным учебным файлом: только при совпадении введём <code>да</code>. Любой другой ответ завершит программу до подключения.</p>
        <CodeBlock caption="Открываем файл и читаем столбцы" code={read} />
        <p><code>is_file()</code> требует существующий файл. <code>input()</code> ждёт нашего ответа; <code>strip().lower()</code> убирает пробелы по краям и приводит буквы к нижнему регистру. Если путь не совпал с описью, не подтверждаем его. <code>as_uri()</code> преобразует абсолютный путь в файловый URI; <code>mode=ro</code> запрещает запись, а <code>uri=True</code> включает разбор URI. <code>closing</code> закрывает соединение и при ошибке. Если файла нет или ответ не подтверждает путь, код останавливается до подключения. В практике подставляем путь Planner из описи, а не предполагаем его по имени.</p>
        <p>После подтверждения увидим <code>Таблицы: [('books',)]</code>. Запрос к <code>sqlite_schema</code> выбирает имена объектов типа table, а <code>fetchall()</code> возвращает все найденные строки. Далее идут три кортежа с описанием столбцов. Разберём их по частям.</p>
      </Section>
      <Section number="03" title="Столбцы и значения по умолчанию">
        <Lead>Разберём вывод <code>table_info</code>. Каждая строка описывает один столбец, а не одну книгу.</Lead>
        <TypeCards>
          <TypeCard badge="Поле PRAGMA" title="name и type">Имя столбца и объявленный тип</TypeCard>
          <TypeCard badge="Поле PRAGMA" title="notnull">1 означает объявленный NOT NULL</TypeCard>
          <TypeCard badge="Поле PRAGMA" title="dflt_value">SQL DEFAULT из определения таблицы или None</TypeCard>
          <TypeCard badge="Поле PRAGMA" title="pk">Позиция в первичном ключе; 0 означает, что столбец не входит в ключ</TypeCard>
        </TypeCards>
        <p>Например, строка <code>(2, 'copies', 'INTEGER', 1, '3', 0)</code> описывает третий столбец: его имя copies, тип INTEGER, задан NOT NULL, SQL DEFAULT равен 3 и в ключ он не входит. Первое число это позиция в таблице, а не значение книги. В строке <code>(0, 'id', 'INTEGER', 0, None, 1)</code> последний 1 отмечает ключ. Особенность целочисленного ключа обсуждаем ниже.</p>
        <p>В примере у <code>copies</code> есть SQL DEFAULT 3. У <code>title</code> SQL-default отсутствует. В модели Planner <code>default=2</code> у priority задаётся Python-клиентом SQLAlchemy; без <code>server_default</code> это не SQL DEFAULT таблицы. Для первичного ключа SQLite интерпретируем <code>notnull</code> вместе с CREATE: значение 0 само по себе не описывает все особенности ключа. При скрытых или генерируемых полях используем <code>PRAGMA table_xinfo('books')</code>.</p>
        <p>Сохраним следующий независимый пример как <code>defaults_demo.py</code> и запустим <code>python defaults_demo.py</code> из учебного <code>project/</code>. В новом файле SQLAlchemy подставляет значение 3 при своей вставке. Прямой SQL не видит Python default и получает ошибку NOT NULL; после отката первая строка остаётся.</p>
        <CodeBlock caption="Клиентское значение и прямой SQL" code={defaults} />
        <p><code>insert(items).values(id=1)</code> передаёт вставку SQLAlchemy, <code>engine.begin()</code> подтверждает успешную транзакцию. При ошибке <code>IntegrityError</code> вызываем <code>rollback()</code>. В выводе остаётся <code>[(1, 3)]</code>, а у столбца amount в PRAGMA нет SQL DEFAULT.</p>
        <p>Посмотрим, как найти те же свойства в независимой модели посылки. Сохраним следующий код как <code>package_model.py</code> и выполним <code>python package_model.py</code> в учебной среде из <code>project/</code>. Этот пример только описывает модель и не открывает базу.</p>
        <CodeBlock caption="Читаем свойства модели посылки" code={modelExample} />
        <p>Вывод содержит таблицу packages, ключ True, nullable False, Python default 3 и имя правила ck_weight. <code>Mapped[int]</code> описывает тип атрибута, <code>mapped_column</code> задаёт столбец; <code>__table_args__</code> хранит ограничение. В Planner те же места находятся в <code>app/DBase/models.py</code>. Integer соответствует INTEGER, String представлен строковым типом SQLite, Boolean и JSON имеют свои объявления. Для своей базы сравним реальные результаты, а не будем предполагать их по классу.</p>
      </Section>
      <Section number="04" title="Ограничения и другие объекты">
        <Lead>Одинаковый список столбцов ещё не гарантирует одинаковые правила для строк.</Lead>
        <p>Сначала прочитаем определение объектов и индексы отдельно от списка столбцов. Сохраним этот полный код в <code>inspect_books_objects.py</code>, выполним <code>python inspect_books_objects.py</code> из <code>project/</code> и подтвердим путь после сверки.</p>
        <CodeBlock caption="Читаем ограничения и индексы" code={inspectObjects} />
        <p>В результате <code>sqlite_schema</code> показывает тип, имя, связанную таблицу и SQL объекта. В CREATE таблицы находится <code>ck_copies</code>, а <code>index_list</code> возвращает индекс <code>ix_books_title</code>. Вывод его полей <code>[(0, 1, 'title')]</code> означает: первое ключевое поле индекса находится на позиции 1 в таблице и называется title. <code>table_xinfo</code> дополняет обычный список скрытыми и генерируемыми полями.</p>
        <p>В <code>pragma_index_info(?)</code> вопросительный знак получает имя отдельно в кортеже <code>(name,)</code>; имя не склеивается с SQL. При сложном индексе дополнительно смотрим <code>pragma_index_xinfo(?)</code> и SQL индекса. <code>foreign_key_list</code> вернул пустой список: в этой таблице нет объявленных ссылок. Он не доказывает включённость проверки внешних ключей.</p>
        <p>В списке объектов могут встретиться представления и триггеры. SQL автоматического индекса может быть NULL: это не ошибка чтения. Если объект или его смысл неизвестен, не объявляем схемы равными до выяснения.</p>
        <p>Посмотрим на другую отдельную базу. Сохраним код как <code>library_links.py</code> и запустим <code>python library_links.py</code> из учебного <code>project/</code>.</p>
        <CodeBlock caption="Внешний ключ и представление" code={relationships} />
        <p><code>REFERENCES authors(id)</code> объявляет связь с ключом автора. В выводе связи увидим таблицу authors и поля author_id и id; представление <code>book_authors</code> появится в списке. Представление сохраняет запрос, а не отдельный набор строк. Эта проверка читает объявление, но не включает проверку FK на соединениях приложения.</p>
        <CodeSequence title="Как проверить ограничение" prompt="В учебной таблице книг перечисление столбцов совпало с ожидаемым. Соберём порядок проверки: что читаем и как делаем вывод о CHECK? Лишний вариант оставим." pieces={[
          { id: "expect", code: "Установить, какое ограничение ожидается в модели" },
          { id: "ddl", code: "Прочитать CREATE TABLE из sqlite_schema" },
          { id: "compare", code: "Сравнить выражение CHECK с ожидаемым" },
          { id: "guess", code: "Объявить совпадение только по имени таблицы" },
        ]} correctOrder={["expect", "ddl", "compare"]} explanation="Модель задаёт ожидание, CREATE показывает фактическое определение. Сравнение обнаруживает отсутствующий CHECK даже при одинаковых столбцах." incorrectExplanation="Одного имени таблицы недостаточно. Сначала определим ожидаемое правило, затем прочитаем CREATE и сравним конкретное выражение." />
      </Section>
      <Section number="05" title="Сравниваем два независимых образца">
        <Lead>Создадим два новых файла книг. У них одинаковые столбцы, но только первый содержит CHECK.</Lead>
        <p>Сохраним код как <code>compare_books.py</code> и выполним <code>python compare_books.py</code> из учебного <code>project/</code>. Если хотя бы одно имя занято, программа остановится до открытия обоих файлов.</p>
        <CodeBlock caption="Одинаковые столбцы, разные ограничения" code={mismatch} />
        <p>Вывод <code>table_info</code> для двух таблиц совпадает. В SQL первого файла есть <code>ck_copies</code>, во втором его нет. Значит, второй экземпляр неизвестен для исходного договора и не должен автоматически получать отметку соответствия. Текстовое отличие SQL требует анализа: похожая запись не является универсальным доказательством равного смысла.</p>
        <p>Теперь попробуем другое расхождение самостоятельно. Сохраним следующий код как <code>probe_nullable.py</code> в учебном <code>project/</code>. До запуска предскажем, что покажет <code>notnull</code> у quantity в двух файлах: в одном задано <code>NOT NULL</code>, в другом это ограничение отсутствует. Проверим, что оба имени свободны, и выполним <code>python probe_nullable.py</code>.</p>
        <CodeBlock caption="Проба обязательности на отдельных файлах" code={nullableProbe} />
        <p>Для quantity в строке PRAGMA четвёртое значение равно <code>1</code> в первом файле и <code>0</code> во втором. Имя и тип одинаковы, но допустимость NULL различается. Если получили другой результат, сравним CREATE и вернёмся к полю <code>notnull</code>. Занятый путь останавливает пробу до открытия любого из файлов; для повтора выбираем два новых имени.</p>
      </Section>
      <Section number="06" title="Считаем и сравниваем содержимое">
        <Lead>Схема описывает устройство, но не отвечает, сохранились ли нужные значения.</Lead>
        <p>Сохраним следующий код как <code>inventory_books.py</code> и запустим <code>python inventory_books.py</code> из <code>project/</code>. Программа снова ждёт подтверждения знакомого пути перед открытием.</p>
        <CodeBlock caption="Количество и полные строки книг" code={inventory} />
        <p>Количество равно <code>[(2,)]</code>, а строки: <code>[(2, 'Atlas', 3), (7, 'Notes', 1)]</code>. <code>COUNT(*)</code> считает строки, <code>ORDER BY id</code> задаёт порядок по ключу. Другой набор тоже может содержать две строки, но отличаться названием или количеством книг. Поэтому фиксируем число и все поля каждой строки. JSON в SQLite читаем как фактическое значение и сохраняем без потери содержимого.</p>
        <Callout tone="success">Перед работой с Planner убедимся, что учебный адрес подтверждён, процессы остановлены и есть опись синтетических задач. При неизвестном пути останавливаемся.</Callout>
      </Section>
      <Section number="07" title="Что мы будем делать в практике">
        <Lead>Сначала выпишем ожидания модели, затем откроем отдельный учебный Planner только для чтения и сравним его с этим описанием.</Lead>
        <LinkedNotes items={[
          { title: "Сверим схему", description: "Найдём поля, ключ, defaults, CHECK, индексы, связи и другие объекты." },
          { title: "Зафиксируем данные", description: "Сохраним количество, ID и все шесть значений каждой учебной задачи." },
          { title: "Проверим расхождение", description: "На новом отдельном файле самостоятельно создадим лишний столбец и обнаружим его." },
        ]} />
        <p>При совпадении получим проверенный кандидат исходной схемы и опись. Неизвестный объект или различие означает остановку; сама опись ещё не является резервной копией.</p>
      </Section>
    </RichLesson>
  );
}
