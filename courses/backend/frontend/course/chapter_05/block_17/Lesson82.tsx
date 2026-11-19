import { BranchExplorer, Callout, CodeBlock, ContentList, InteractiveTable, Lead, LinkedNotes, QuizCard, RichHero, RichLesson, Section } from "@course-ui";

const reserveDemo = `from pathlib import Path
from sqlalchemy.engine import URL

Path(".lesson-lab").mkdir(exist_ok=True)
demo_root = Path(".lesson-lab/baseline-demo")
demo_root.mkdir()
db_path = (demo_root / "books.sqlite3").resolve()
db_path.touch(exist_ok=False)
print(db_path)
print(URL.create("sqlite", database=db_path.as_posix()))`;

const bookRevision = `from alembic import op
import sqlalchemy as sa

revision = "b_demo"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table(
        "book_stock",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("copies", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("copies >= 0", name="ck_book_stock_copies"),
    )

def downgrade():
    raise RuntimeError("Removing the baseline is not supported")`;

const inspectBook = `from pathlib import Path
import sqlite3

db_path = Path("books.sqlite3").resolve()
if not db_path.is_file():
    raise FileNotFoundError(db_path)
db = sqlite3.connect(db_path)
try:
    print(db.execute("PRAGMA table_info(book_stock)").fetchall())
    print(db.execute(
        "SELECT sql FROM sqlite_schema WHERE type = 'table' AND name = ?",
        ("book_stock",),
    ).fetchone()[0])
    print(db.execute("PRAGMA index_list(book_stock)").fetchall())
    print(db.execute("PRAGMA foreign_key_list(book_stock)").fetchall())
    print(db.execute("SELECT version_num FROM alembic_version").fetchall())
finally:
    db.close()`;

const insertBooks = `from pathlib import Path
import sqlite3

db_path = Path("books.sqlite3").resolve()
if not db_path.is_file():
    raise FileNotFoundError(db_path)
db = sqlite3.connect(db_path)
try:
    db.execute("INSERT INTO book_stock (id) VALUES (?)", (7,))
    db.commit()
    db.execute("INSERT INTO book_stock (id, copies) VALUES (?, ?)", (8, 0))
    db.commit()
    try:
        db.execute("INSERT INTO book_stock (id, copies) VALUES (?, ?)", (9, -1))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        print("CHECK: отрицательный остаток отклонён")
    print(db.execute("SELECT id, copies FROM book_stock ORDER BY id").fetchall())
finally:
    db.close()`;

const defaultProbe = `import sqlite3

db = sqlite3.connect(":memory:")
try:
    db.execute("CREATE TABLE stock_probe (id INTEGER PRIMARY KEY, copies INTEGER NOT NULL)")
    try:
        db.execute("INSERT INTO stock_probe (id) VALUES (?)", (1,))
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        print("NOT NULL: copies не передан")
    db.execute("INSERT INTO stock_probe (id, copies) VALUES (?, ?)", (2, 4))
    db.commit()
    print(db.execute("SELECT id, copies FROM stock_probe").fetchall())
finally:
    db.close()`;

const readVersion = `from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

db_path = Path("books.sqlite3").resolve()
if not db_path.is_file():
    raise FileNotFoundError(db_path)
url = URL.create("sqlite", database=db_path.as_posix())
engine = create_engine(url)
try:
    with Session(engine) as session:
        versions = session.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        rows = session.execute(text("SELECT id, copies FROM book_stock ORDER BY id")).all()
        print(versions)
        print(rows)
finally:
    engine.dispose()`;

export function Lesson82({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero chip={module ?? "Миграции базы"} title="Первая миграция" intro="Запишем способ создания исходной схемы один раз и проверим его на независимых пустых базах. Сохранённые задачи останутся в своей восстановленной копии." />
      <Section number="00" title="От восстановимых данных к воспроизводимой схеме">
        <Lead>Сначала подтвердим восстановление: отдельная копия открывается, её задачи и структура совпадают с сохранённой описью. Если это ещё не проверено, вернёмся к восстановлению до изменений схемы.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Есть исходная SQLite, резерв, восстановленная заполненная копия и опись. Их адреса различимы и записаны." },
          { title: "Новый вопрос", description: "Как получить ту же исходную структуру в новом пустом файле, не копируя готовую базу?" },
          { title: "Результат", description: "Настроим Alembic и подготовимся самостоятельно записать исходную ревизию, воспроизводимую на двух новых SQLite." },
        ]} />
        <p>Резерв сохраняет существующее состояние. История схемы описывает действия создания структуры. Для первого опыта возьмём маленький книжный склад, а в практике перенесём способ на исходную Task-схему Planner.</p>
      </Section>
      <Section number="01" title="История, таблицы и метка отвечают на разные вопросы">
        <p>Файл миграции содержит команды изменения структуры. Такие SQL-команды, например CREATE TABLE, относятся к <strong>DDL</strong>, языку определения данных. Один шаг истории называется <strong>ревизией</strong> (revision). Его имя связывает файл с историей; это не номер строки и не имя SQLite-файла. Исходную ревизию будем называть <strong>baseline</strong>: она создаёт структуру, с которой начинается история.</p>
        <InteractiveTable title="Три независимых состояния" columns={["Объект", "Что содержит", "Что ещё нужно проверить"]} rows={[
          { id: "files", label: "Файлы истории", values: ["Ревизию b_demo и операции CREATE", "Исполнялась ли она на выбранной БД"] },
          { id: "schema", label: "Реальная SQLite", values: ["book_stock, ограничения и строки", "Совпадает ли структура с исходным шагом"] },
          { id: "mark", label: "alembic_version", values: ["Метка применённой ревизии", "Есть ли соответствующая ей структура"] },
        ]} />
        <p><code>upgrade</code> исполняет нужные ревизии и отмечает пройденный шаг. <code>stamp</code> меняет метку без исполнения этих операций. Надпись b_demo не доказывает, что таблица создана. Здесь используем upgrade; заполненную восстановленную копию пока не маркируем.</p>
        <p>Родитель ревизии записан в <code>down_revision</code>. У первой ревизии значение <code>None</code>, родителя нет. <strong>Head</strong> есть конец файловой истории; <strong>current</strong> есть шаг, записанный в выбранной базе. Пока у нас одна цепочка и один конец.</p>
        <Callout>Новая пустая цель отличается от существующего источника. Не очищаем восстановленную копию. Сравниваем новый путь с адресами исходника, резерва и копии, затем резервируем исключительно. Занятый путь означает остановку и выбор другого имени.</Callout>
        <p>В независимом опыте новый каталог тоже создаётся исключительно. Из корня ученического project откроем Python командой <code>python</code> в подготовленном venv. <code>mkdir()</code> без exist_ok=True откажет для занятого каталога; <code>touch(exist_ok=False)</code> откажет для занятого файла. <code>resolve()</code> получает абсолютный адрес, а <code>as_posix()</code> записывает разделители как косые черты. Импорт URL из SQLAlchemy собирает SQLite URL из выбранного имени файла.</p>
        <CodeBlock caption="Python из корня project: новый каталог и пустая цель" code={reserveDemo} />
        <p>Вывод показывает фактический полный путь books.sqlite3 и его URL. Сохраним обе строки. Пустой файл пока без таблиц и метки. При занятом каталоге опыта выберем свободное имя и последовательно подставим его дальше; прежний опыт не удаляем. Повтор touch для того же файла также должен отказать.</p>
        <p>На изменённом наборе подписанных адресов ещё раз выберем допустимую роль без создания дополнительного файла: свободная новая цель подходит, резерв и заполненная копия не подходят. Закроем Python через <code>exit()</code>, чтобы следующие команды исполнялись в терминале.</p>
      </Section>
      <Section number="02" title="Сначала окружение инструмента, затем его история">
        <Lead>Alembic управляет файлами миграций и их применением. Используем тот же venv, где работает проект, чтобы команды обращались к выбранному Python.</Lead>
        <p>В терминале из project проверим <code>python --version</code> и <code>python -m pip --version</code>. Форма python -m запускает модуль именно этим Python. Для выбранного Alembic нужен Python не ниже 3.10. Если окружение уже согласовано, не устанавливаем его повторно. Иначе используем версии этого материала:</p>
        <CodeBlock caption="Терминал: установка в активный venv" language="text" code={'python -m pip install "alembic==1.20.1" "SQLAlchemy==2.0.55"\npython -m alembic --version'} />
        <p>Кавычки сохраняют требование версии одним аргументом; == фиксирует версию. Проверка должна показать Alembic 1.20.1. В практике этот pin попадёт в существующий requirements без изменения остальных зависимостей.</p>
        <p>Перейдём в созданный каталог опыта. Команда init создаёт миграционное окружение с именем migrations, но ещё не таблицы:</p>
        <CodeBlock caption="Терминал из project" language="text" code={'cd .lesson-lab/baseline-demo\npython -m alembic init migrations'} />
        <ContentList items={[
          { title: "alembic.ini", description: "Настройки: где скрипты, какую БД открыть и как писать служебные сообщения." },
          { title: "migrations/env.py", description: "Python-код, связывающий конфигурацию, соединение и миграции." },
          { title: "migrations/script.py.mako", description: "Шаблон новых ревизий. Сейчас его не переписываем; отдельный язык шаблонов не нужен." },
          { title: "migrations/versions/ и README", description: "Каталог истории и сгенерированная справка. Versions пока пуст." },
        ]} />
        <p>В alembic.ini сохраним стандартные логирующие секции. В секции <code>[alembic]</code> укажем <code>script_location = %(here)s/migrations</code>: %(here)s обозначает каталог ini. В <code>sqlalchemy.url</code> вставим URL, напечатанный при резервировании. Шаблонный driver://user:pass@localhost/dbname не является нашей целью.</p>
        <p>На Windows URL имеет форму <code>sqlite:///C:/каталог/books.sqlite3</code>, на POSIX перед абсолютным путём четыре косые черты. Используем собственный синтетический путь без процента и URI-параметров. Prepend_sys_path относится к поиску Python-модулей; <code>path_separator = os</code> оставляет разделение списков путей операционной системе.</p>
        <p>После клонирования проекта <code>%(here)s</code> указывает на каталог нового ini, а абсолютный <code>sqlalchemy.url</code> сохраняет прежний адрес. Перед первой командой Alembic укажем URL выбранной учебной копии в своей среде и сверим его с ролью файла из описи.</p>
        <p>В env.py импорт <code>fileConfig</code> настраивает сообщения по ini. <code>engine_from_config</code> читает параметры с префиксом sqlalchemy. и создаёт Engine. <code>pool.NullPool</code> не удерживает возвращённые соединения в пуле. <code>context</code> из Alembic связывает конфигурацию, соединение и миграции.</p>
        <p>Online-ветвь открывает connect(), передаёт соединение в context.configure и вызывает run_migrations() внутри begin_transaction(). Offline-ветвь выводит SQL без подключения; здесь её не запускаем. Стандартный шаблон сохраняем, новую обвязку не пишем.</p>
        <p>Metadata есть описание таблиц в Python, связанное с ORM-моделью. Оставляем <code>target_metadata = None</code>: ревизию напишем явно, текущая модель для её исполнения не нужна. Приложение и init_db в env.py не импортируем. Стандартный шаблон не читает .env Planner: DATABASE_URL приложения не меняет цель Alembic. Перед подключением читаем sqlalchemy.url.</p>
        <CodeBlock caption="Терминал из каталога опыта" language="text" code="python -m alembic heads" />
        <p>Пока ревизий нет, список heads пуст. Инструмент настроен, но историю и структуру мы ещё не создали. Неверный script_location исправляем в ini, а не переименованием файлов приложения.</p>
      </Section>
      <Section number="03" title="Один файл превращает пустую цель в схему">
        <p>В каталоге опыта создадим ревизию. Флаг <code>--rev-id</code> задаёт стабильное имя шага, а <code>-m</code> задаёт описание файла:</p>
        <CodeBlock caption="Терминал из каталога опыта" language="text" code={'python -m alembic revision --rev-id b_demo -m "book stock baseline"'} />
        <p>Появится migrations/versions/b_demo_book_stock_baseline.py. Изменились только файлы истории, в SQLite ещё нет book_stock. Опишем склад с целым ключом id, обязательным числом copies, SQL-default 1 и запретом отрицательного остатка.</p>
        <p><code>from alembic import op</code> даёт операции текущей миграции. <code>import sqlalchemy as sa</code> даёт типы и описания структуры; sa есть короткое имя модуля. <code>op.create_table</code> принимает имя таблицы, колонки и ограничения. <code>primary_key=True</code> задаёт ключ, <code>nullable=False</code> запрещает NULL, а <code>server_default</code> добавляет SQL DEFAULT прямо в структуру. <code>sa.CheckConstraint</code> хранит SQL-условие и его имя.</p>
        <CodeBlock caption="Полный файл migrations/versions/b_demo_book_stock_baseline.py" code={bookRevision} />
        <p>В сгенерированном шаблоне могут быть аннотации и typing-импорты. Они описывают типы служебных значений, а не выполняют DDL; полный вариант выше обходится без них. <code>revision</code> называет шаг, <code>down_revision=None</code> делает его корневым. <code>branch_labels</code> и <code>depends_on</code> оставлены None: дополнительных ветвей и зависимостей нет.</p>
        <p><code>upgrade()</code> Alembic вызовет при применении. <code>downgrade()</code> в этой учебной baseline явно отказывает: <code>raise RuntimeError</code> останавливает операцию с сообщением. Команду удаления исходной схемы не исполняем; пустой pass в downgrade не означал бы выполненного отката.</p>
        <CodeBlock caption="Терминал: применяем и читаем историю" language="text" code={'python -m alembic upgrade head\npython -m alembic history\npython -m alembic heads\npython -m alembic current'} />
        <p>После успешного upgrade ожидаем book_stock и alembic_version. History показывает связь корня с b_demo, heads показывает конец файловой истории, current показывает b_demo в выбранной SQLite. Current не используем для проверки существования источника: команда открывает настроенную БД.</p>
        <p>Проверим прогноз до просмотра модели переходов. Представим новый пустой файл, которому выполнили только stamp b_demo. Что реально появится?</p>
        <QuizCard question="Что подтверждает метка после одного stamp на пустой цели?" orientation="vertical" options={[
          "Созданы book_stock, CHECK и метка b_demo",
          "Записана метка b_demo; создания book_stock эта команда не выполняла",
          "Скопированы строки книжного склада из другой БД",
        ]} correctIndex={1} explanation="Stamp не выполняет upgrade ревизии. Метка появляется отдельно от таблицы и данных; соответствие структуры ею не доказано." />
        <p>Переключим исходное состояние и действие. Это модель четырёх случаев, она не запускает команды на наших файлах. Перед переключением прогнозируем таблицу, строки и метку, затем сравниваем с результатом:</p>
        <BranchExplorer code={'upgrade b_demo\nstamp b_demo'} scenarios={[
          { label: "Пустая: upgrade", activeLine: 0, output: "book_stock создана, строк 0, метка b_demo. Выполнено CREATE из ревизии." },
          { label: "Пустая: stamp", activeLine: 1, output: "book_stock отсутствует, строк нет, метка b_demo. Stamp не выполняет DDL." },
          { label: "Готовая: upgrade", activeLine: 0, output: "Таблица уже есть, метки нет: CREATE конфликтует. Останавливаемся, результат не принят." },
          { label: "Готовая: stamp", activeLine: 1, output: "Таблица и прежние строки сохраняются, метка b_demo. Соответствие схемы ещё нужно доказать." },
        ]} />
        <p>Метка есть утверждение о состоянии, а не доказательство. Поэтому заполненную восстановленную копию не подключаем к этому CREATE и не исправляем через слепой stamp. Сейчас создаём новую структуру.</p>
        <p>Файл принятой baseline сохраняет исходный смысл и явные операции. Будущая модель не должна менять действие прошлого шага. Новые изменения потребуют новых ревизий, а не вызова изменяемой metadata из исходного файла. Кандидат до проверки ещё не является принятой baseline.</p>
      </Section>
      <Section number="04" title="Проверяем фактические правила, включая defaults">
        <Lead>Успешная команда ещё не показывает, какие правила записаны в файле. Прочитаем структуру с нового соединения, как при исходной описи.</Lead>
        <p>В каталоге опыта откроем новый Python и выполним полный фрагмент ниже. <code>is_file()</code> не даёт sqlite3 незаметно создать отсутствующий файл. Знак ? обозначает параметр, переданный отдельным кортежем, а не склеенный с SQL. <code>fetchall()</code> получает строки; <code>fetchone()[0]</code> берёт первое значение единственной найденной строки. Закрытие в finally выполняется и при ошибке.</p>
        <CodeBlock caption="Python из каталога опыта: структура и метка" code={inspectBook} />
        <p>В table_info строка copies имеет вид <code>{`(1, 'copies', 'INTEGER', 1, "'1'", 0)`}</code>. По порядку: номер колонки, имя, тип, NOT NULL, SQL-default и позиция в ключе. Четвёртое значение 1 подтверждает NOT NULL, пятое хранит SQL DEFAULT '1'. Строка id показывает PK=1 и явно заданный NOT NULL=1. В SQL таблицы отдельно найдём <code>CONSTRAINT ck_book_stock_copies CHECK (copies &gt;= 0)</code>: table_info это условие не показывает.</p>
        <p>Дополнительные списки индексов и FK пусты: мы их не добавляли. INTEGER PRIMARY KEY всё равно остаётся ключом; отсутствие отдельного индекса SQLite для такого ключа не означает отсутствие PK. Метка читается как <code>[('b_demo',)]</code>. SQL и PRAGMA сравниваем вместе, без требования одинаковых пробелов и регистра SQL.</p>
        <p>Проверим правило действием. Commit сохраняет успешную запись; rollback после ошибки отменяет неуспешную транзакцию. У ошибочной записи новый положительный id, поэтому отказ не путается с дубликатом ключа.</p>
        <CodeBlock caption="Python из каталога опыта: выполняем один раз после создания схемы" code={insertBooks} />
        <CodeBlock caption="Наблюдаемый вывод" language="text" code={'CHECK: отрицательный остаток отклонён\n[(7, 1), (8, 0)]'} />
        <p>У id=7 не передали copies, и SQLite подставила 1. У id=8 передали 0, он допустим. Copies=-1 у id=9 нарушает CHECK и не сохраняется. Повтор всего фрагмента столкнулся бы с занятыми id 7 и 8. Для собственной пробы выберем новые положительные id и сами запишем вставки для copies=4 и copies=-2. Сначала предскажем результат, затем сравним строки после отдельного rollback. Принятую ревизию и CHECK не меняем.</p>
        <p>SQL-default отличается от Python-default. <code>sa.Column("copies", sa.Integer(), default=1)</code> задаёт подстановку SQLAlchemy при его INSERT, но не добавляет DEFAULT в CREATE TABLE. Сырой SQL про такую Python-настройку не знает. Отсутствующий SQL-default также не заменяем явным DEFAULT NULL. Проверим другую короткую структуру в памяти, не меняя дисковый склад:</p>
        <CodeBlock caption="Отдельная SQLite в памяти: нет SQL DEFAULT" code={defaultProbe} />
        <CodeBlock caption="Вывод сравнения" language="text" code={'NOT NULL: copies не передан\n[(2, 4)]'} />
        <p>Пропуск copies отвергнут: NOT NULL есть, SQL-default нет. Явно переданное 4 сохраняется. :memory: есть отдельная временная SQLite этого соединения; после close она исчезает. Вся подготовка сравнения показана, второго приложения нет.</p>
        <p>На входе Planner имеет id, title, priority, is_done, tags, title_normalized. Сверим их с TaskRow и описью. Все колонки обязательны, id является ключом. Именованные ck_tasks_id_positive и ck_tasks_priority_range ограничивают id &gt; 0 и priority от 1 до 5. В ревизии String, Boolean и JSON записываются через уже понятное имя sa.</p>
        <InteractiveTable title="Что сохраняем при переносе на Task" columns={["Признак", "В модели на входе", "В исходном DDL"]} rows={[
          { id: "priority", label: "priority", values: ["Integer, default=2", "NOT NULL, SQL DEFAULT отсутствует"] },
          { id: "done", label: "is_done", values: ["Boolean, default=False", "NOT NULL, SQL DEFAULT отсутствует"] },
          { id: "tags", label: "tags", values: ["JSON, default=list", "NOT NULL, SQL DEFAULT отсутствует"] },
        ]} />
        <p>Default list создаёт новую коллекцию на стороне Python. Server_default для этих трёх полей в исходную историю не добавляем. Различаем Python-список <code>{'["book"]'}</code> и строку JSON <code>{`'["book"]'`}</code>: в параметр сырого SQLite INSERT передаём заданный текст JSON. Модель SQLAlchemy умеет работать с JSON-значением сама.</p>
        <p>У исходного TaskRow нет User, owner, description, UNIQUE, дополнительного Boolean CHECK или JSON CHECK. Их не добавляем. Проверяем колонки, PK/nullable, defaults, выражения и имена CHECK, индексы/FK и прочие объекты по описи, а не только имя tasks.</p>
        <p><strong>Autogenerate</strong> сравнивает metadata с БД и предлагает файл-кандидат. Для него в env.py подключают metadata, но файл всё равно читают и исправляют до принятия. В основном пути baseline пишем вручную, не подключая приложение. Проверим маленький независимый кандидат:</p>
        <CodeBlock caption="Кандидат книжной схемы: фрагмент для чтения" code={'op.create_table(\n    "book_stock",\n    sa.Column("id", sa.Integer(), primary_key=True, nullable=False),\n    sa.Column("copies", sa.Integer(), nullable=False, server_default="1"),\n)'} />
        <p>Колонки узнаваемы, но CHECK потерян. Сначала найдём расхождение, затем назовём наблюдение: отрицательный copies оказался бы допустимым. Даже две одинаковые БД из такого кандидата не докажут соответствие исходному правилу. Будущая mutable metadata не определяет смысл старой baseline.</p>
      </Section>
      <Section number="05" title="Повтор, новая Session и второй пустой файл">
        <p>Сначала запишем книжные строки и метку в наблюдениях, закроем соединения и Python. В каталоге опыта повторим применение к прежнему файлу:</p>
        <CodeBlock caption="Терминал: повтор на прежнем файле" language="text" code="python -m alembic upgrade head" />
        <p>При прежней истории current уже равен head. Alembic не должен повторить CREATE или добавить строки. Это не ремонт самовольно повреждённой таблицы: её структуру мы проверяли отдельно.</p>
        <p>Откроем новый процесс командой python и прочитаем метку новой SQLAlchemy Session. Импорт create_engine создаёт Engine по явному URL, Session даёт короткое обращение к БД. Text обозначает явный SQL, execute выполняет его. Scalars() оставляет значения первой колонки, all() собирает их в список. Для двух колонок используем all без scalars, чтобы не потерять copies.</p>
        <CodeBlock caption="Новый Python из каталога опыта" code={readVersion} />
        <p>Новая Session видит <code>['b_demo']</code>, прежние пары <code>(7, 1)</code>, <code>(8, 0)</code> и собственные успешно сохранённые строки пробы, если мы их добавили. Ошибочных вставок нет. With закрывает Session, dispose освобождает ресурсы Engine. Новый процесс подтверждает состояние файла, а не старый объект в памяти.</p>
        <p>В практике получим две новые SQLite одной Task-ревизией. Перед каждым применением читаем фактический URL ini. После первого файла закрываем соединения, явно меняем URL и применяем ту же историю ко второму без редактирования ревизии. Обе структуры сравниваем с описью, а не только друг с другом.</p>
        <p>Первая цель сохранит синтетическую строку для проверки повтора. Вторая останется без Task-строк и станет чистым образцом схемы с меткой. Если upgrade отказал, не объявляем всю DDL автоматически откаченной: читаем фактические таблицы/метку, отмечаем цель как неэталонную и останавливаем перенос. Stamp не исправляет такой результат.</p>
        <p>Прежний init_db остаётся для исходного runtime Planner. Новые БД доказывают работу истории без create_all при запуске приложения. Восстановленная заполненная копия, её данные и адрес приложения сохраняются отдельно.</p>
      </Section>
      <Section number="06" title="Что мы будем делать в практике">
        <Lead>Книжный пример остаётся опытом, а Task-ревизию напишем самостоятельно по реальной модели и сохранённой описи.</Lead>
        <LinkedNotes items={[
          { title: "Выбираем две новые цели", description: "Отделяем E1/E2 от исходника, резерва и восстановленной копии; занятый путь сохраняется." },
          { title: "Настраиваем и записываем историю", description: "Создаём конфигурацию и корневую p0001 с явным исходным Task-DDL, не вызывающим будущую metadata." },
          { title: "Применяем и различаем ошибки", description: "Сравниваем обе схемы с описью, проверяем CHECK и отсутствие SQL-default отдельными вставками и rollback." },
          { title: "Повторяем и передаём", description: "Новая Session читает единственную метку; E1 сохраняет контрольную строку, E2 остаётся пустым эталоном." },
        ]} />
        <p>При затруднении вернёмся к настройке, полной книжной ревизии, PRAGMA и сравнению defaults. Фактические URL и наблюдения сохраним в короткой секции README. Заполненную копию подключим к истории только после отдельного доказательства соответствия схеме, в следующей теме.</p>
      </Section>
    </RichLesson>
  );
}
