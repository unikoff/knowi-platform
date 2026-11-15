import { RichLesson, RichHero, Section, Lead, LinkedNotes, CodeBlock, QuizCard, TypeCards, TypeCard, Diagram, DiagramFlow, DiagramNode, DiagramArrow, CodeSequence } from "../../shared";

const transactionExample = `import sqlite3
from pathlib import Path

path = Path("demo_transaction.sqlite3")
if path.exists():
    raise RuntimeError("Выберите новое имя файла")
db = sqlite3.connect(path)
try:
    db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    db.execute("INSERT INTO notes VALUES (1, 'черновик')")
    db.commit()
    try:
        db.execute("UPDATE notes SET body = 'готово' WHERE id = 1")
        raise RuntimeError("учебная ошибка")
    except RuntimeError as error:
        db.rollback()
        print(error)
finally:
    db.close()

check = sqlite3.connect(path)
try:
    print(check.execute("SELECT body FROM notes WHERE id = 1").fetchone())
finally:
    check.close()`;

const afterCommitExample = "import sqlite3\nfrom pathlib import Path\n\npath = Path(\"demo_after_commit.sqlite3\")\nif path.exists():\n    raise RuntimeError(\"Выберите новое имя файла\")\ndb = sqlite3.connect(path)\ntry:\n    db.execute(\"CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)\")\n    db.execute(\"INSERT INTO notes VALUES (1, 'черновик')\")\n    db.commit()\n    try:\n        db.execute(\"UPDATE notes SET body = 'готово' WHERE id = 1\")\n        db.commit()\n        raise RuntimeError(\"учебная ошибка\")\n    except RuntimeError as error:\n        db.rollback()\n        print(error)\nfinally:\n    db.close()\n\ncheck = sqlite3.connect(path)\ntry:\n    print(check.execute(\"SELECT body FROM notes WHERE id = 1\").fetchone())\nfinally:\n    check.close()";

const foreignKeysExample = "import sqlite3\nfrom pathlib import Path\n\npath = Path(\"demo_foreign_keys.sqlite3\")\nif path.exists():\n    raise RuntimeError(\"Выберите новое имя файла\")\ndb = sqlite3.connect(path)\ntry:\n    db.execute(\"PRAGMA foreign_keys = ON\")\n    print(\"До транзакции:\", db.execute(\"PRAGMA foreign_keys\").fetchone()[0])\n    db.execute(\"CREATE TABLE shelf (id INTEGER PRIMARY KEY)\")\n    db.execute(\"CREATE TABLE book (id INTEGER PRIMARY KEY, shelf_id INTEGER, \"\n               \"FOREIGN KEY (shelf_id) REFERENCES shelf(id), \"\n               \"CONSTRAINT ck_book_id CHECK (id > 0))\")\n    db.execute(\"CREATE INDEX ix_book_shelf ON book(shelf_id)\")\n    db.commit()\n    db.execute(\"BEGIN\")\n    db.execute(\"PRAGMA foreign_keys = OFF\")\n    print(\"В транзакции:\", db.execute(\"PRAGMA foreign_keys\").fetchone()[0])\n    try:\n        db.execute(\"INSERT INTO book VALUES (1, 99)\")\n    except sqlite3.IntegrityError:\n        print(\"Несуществующая полка отклонена\")\n        db.rollback()\n    print(\"Проверка связей:\", db.execute(\"PRAGMA foreign_key_check\").fetchall())\n    print(\"Индекс:\", db.execute(\"PRAGMA index_list(book)\").fetchall()[0][1])\n    print(\"CHECK сохранён:\", \"ck_book_id\" in db.execute(\n        \"SELECT sql FROM sqlite_schema WHERE name = 'book'\"\n    ).fetchone()[0])\nfinally:\n    db.close()\n";

const guardExample = `from pathlib import Path
from sqlalchemy.engine import make_url

def checked_url(config):
    raw = config.get_main_option("sqlalchemy.url")
    expected = config.get_main_option("expected_database_path")
    if not raw or not expected:
        raise ValueError("Нужны URL и контрольный путь")
    url = make_url(raw)
    if (url.drivername not in ("sqlite", "sqlite+pysqlite")
            or not url.database or url.database == ":memory:"
            or url.host or url.username or url.password or url.port
            or url.query):
        raise ValueError("Нужен URL файла SQLite")
    expected_path = Path(expected)
    if not expected_path.is_absolute():
        raise ValueError("Контрольный путь должен быть абсолютным")
    actual_path = Path(url.database).resolve()
    if actual_path != expected_path.resolve():
        raise ValueError("Адрес базы не совпадает с контрольным")
    return raw`;

const inspectionExample = `import sqlite3
from pathlib import Path
from contextlib import closing

path = Path("demo_simple/notes.sqlite3").resolve()
if not path.is_file():
    raise FileNotFoundError(path)
with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
    print("целостность:", db.execute("PRAGMA integrity_check").fetchall())
    print("столбцы:", db.execute("PRAGMA table_info(notes)").fetchall())
    print("индексы:", db.execute("PRAGMA index_list(notes)").fetchall())
    print("ссылки:", db.execute("PRAGMA foreign_key_list(notes)").fetchall())
    print("версия:", db.execute("SELECT version_num FROM alembic_version").fetchall())
    print("количество:", db.execute("SELECT COUNT(*) FROM notes").fetchall())
    print("схема:", db.execute(
        "SELECT type, name, sql FROM sqlite_schema ORDER BY type, name"
    ).fetchall())
    print("строки:", db.execute(
        "SELECT id, body FROM notes ORDER BY id"
    ).fetchall())`;

const demoSetup = "from pathlib import Path\n\nroot = Path(\"demo_simple\")\nroot.mkdir()\n(root / \"migrations\" / \"versions\").mkdir(parents=True)\nwith (root / \"notes.sqlite3\").open(\"xb\"):\n    pass\nprint(\"Контрольный путь:\", (root / \"notes.sqlite3\").resolve())";
const demoEnvironment = "from pathlib import Path\nfrom alembic import context\nfrom sqlalchemy import create_engine\nfrom sqlalchemy.engine import make_url\n\nconfig = context.config\nraw = config.get_main_option(\"sqlalchemy.url\")\nexpected = config.get_main_option(\"expected_database_path\")\nif not raw or not expected:\n    raise ValueError(\"Нужны URL и контрольный путь\")\nurl = make_url(raw)\nif (url.drivername != \"sqlite\" or not url.database or url.database == \":memory:\"\n        or url.host or url.username or url.password or url.port or url.query):\n    raise ValueError(\"Нужен URL файла SQLite\")\nexpected_path = Path(expected)\nif not expected_path.is_absolute() or Path(url.database).resolve() != expected_path.resolve():\n    raise ValueError(\"Адрес базы не совпадает с контрольным\")\nif context.is_offline_mode():\n    context.configure(url=raw, literal_binds=True)\n    with context.begin_transaction():\n        context.run_migrations()\nelse:\n    engine = create_engine(raw)\n    try:\n        with engine.connect() as connection:\n            context.configure(connection=connection)\n            with context.begin_transaction():\n                context.run_migrations()\n    finally:\n        engine.dispose()\n";
const demoBaseline = "from alembic import op\nimport sqlalchemy as sa\nrevision = \"demo_initial\"\ndown_revision = None\nbranch_labels = None\ndepends_on = None\ndef upgrade():\n    op.create_table(\"notes\", sa.Column(\"id\", sa.Integer, primary_key=True),\n                    sa.Column(\"body\", sa.String, nullable=False))\ndef downgrade():\n    op.drop_table(\"notes\")\n";
const demoFailure = "from alembic import op\nimport sqlalchemy as sa\nrevision = \"demo_failure\"\ndown_revision = \"demo_initial\"\nbranch_labels = None\ndepends_on = None\ndef upgrade():\n    op.add_column(\"notes\", sa.Column(\"memo\", sa.String, nullable=True))\n    raise RuntimeError(\"учебный отказ\")\ndef downgrade():\n    op.drop_column(\"notes\", \"memo\")\n";
const demoSeed = "import sqlite3\n\nconnection = sqlite3.connect(\"demo_simple/notes.sqlite3\")\ntry:\n    connection.executemany(\n        \"INSERT INTO notes (id, body) VALUES (?, ?)\",\n        [(1, \"черновик\"), (2, \"готово\")],\n    )\n    connection.commit()\n    print(connection.execute(\"SELECT id, body FROM notes ORDER BY id\").fetchall())\nfinally:\n    connection.close()";
const recoveryPrepare = "from pathlib import Path\nimport sqlite3\n\nroot = Path('demo_recovery')\nroot.mkdir()\nsource = root / 'source.sqlite3'\nsnapshot = root / 'snapshot.sqlite3'\nrestored = root / 'restored.sqlite3'\noccupied = root / 'occupied.bin'\nwith occupied.open('xb') as marker:\n    marker.write(b'KEEP')\nconnection = sqlite3.connect(source)\ntry:\n    connection.execute('CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)')\n    connection.executemany('INSERT INTO notes VALUES (?, ?)', [(1, 'чай'), (2, 'книга')])\n    connection.commit()\nfinally:\n    connection.close()\n\nprint(\"Источник и контрольный файл готовы\")\n";
const recoveryRun = "from pathlib import Path\nimport sqlite3\n\nroot = Path('demo_recovery')\nsource = root / 'source.sqlite3'\nsnapshot = root / 'snapshot.sqlite3'\nrestored = root / 'restored.sqlite3'\noccupied = root / 'occupied.bin'\n\ndef copy_database(source_path, target_path):\n    if not source_path.is_file() or source_path.resolve() == target_path.resolve():\n        raise ValueError('Проверьте источник и назначение')\n    with target_path.open('xb'):\n        pass\n    source_db = sqlite3.connect(source_path)\n    try:\n        target_db = sqlite3.connect(target_path)\n        try:\n            source_db.backup(target_db)\n        finally:\n            target_db.close()\n    finally:\n        source_db.close()\n\ncopy_database(source, snapshot)\ncopy_database(snapshot, restored)\ncheck = sqlite3.connect(restored)\ntry:\n    print('Строки:', check.execute('SELECT id, body FROM notes ORDER BY id').fetchall())\n    print('Целостность:', check.execute('PRAGMA integrity_check').fetchall())\nfinally:\n    check.close()\nbefore = occupied.read_bytes()\ntry:\n    copy_database(snapshot, occupied)\nexcept FileExistsError:\n    print('Занятый путь отклонён:', occupied.read_bytes() == before)\n";

export function Lesson85({ module }: { module?: string }) {
  return <RichLesson>
    <RichHero chip={module ?? "Изменение заполненной схемы"} title="Ошибка миграции и безопасное восстановление" intro="Разберём, что можно узнать после ошибки и как вернуть данные в отдельный файл, не угадывая состояние базы." />
    <Section number="00" title="Когда команда прервалась">
      <Lead>У нас есть история схемы, учебные данные и проверенный снимок. Теперь рассмотрим ошибку посреди изменения.</Lead>
      <LinkedNotes items={[
        { title: "Точка старта", description: "Расширенная учебная база и её проверенный снимок доступны отдельно от исходных данных." },
        { title: "Новый вопрос", description: "Как узнать, что осталось после ошибки, если сообщение не описывает содержимое файла?" },
        { title: "Результат", description: "Мы остановим опыт, исследуем схему, версию и строки, затем восстановим отдельную копию." },
      ]} />
      <p>Одного сообщения недостаточно: операция могла завершиться между двумя изменениями. Начнём с безопасных адресов и сравнения состояния.</p>
    </Section>
    <Section number="01" title="Четыре роли файлов">
      <p>Исходный резерв хранит состояние до расширения. Снимок после расширения хранит уже другую схему и значения. Его используем как источник для одноразовой базы с ошибкой и отдельного восстановленного файла. Ни один из этих файлов не должен совпадать с источником или уже занятым назначением.</p>
      <p>Команды выполняем из <code>project/</code> отдельной учебной копии. Останавливаем API и CLI и закрываем соединения. Если адрес неизвестен, файл занят или сохранность снимка не подтверждена, останавливаемся до записи. <code>Get-Location</code> показывает текущую папку, <code>Test-Path -LiteralPath имя</code> проверяет существование, а <code>Resolve-Path -LiteralPath имя</code> показывает абсолютный путь существующего файла. Обычный <code>sqlite3.connect</code> может создать отсутствующий файл, поэтому проверка адреса предшествует подключению.</p>
      <TypeCards>
        <TypeCard badge="РЕЗЕРВ" title="Исходный резерв">Состояние до расширения. Сохраняем для проверки старого перехода.</TypeCard>
        <TypeCard badge="СНИМОК" title="Снимок после расширения">Проверенный источник для этого опыта.</TypeCard>
        <TypeCard badge="ОПЫТ" title="Одноразовая копия">Только здесь намеренно вызываем ошибку.</TypeCard>
        <TypeCard badge="ВОССТАНОВЛЕНИЕ" title="Восстановленная копия">Новый путь, который проверим отдельно.</TypeCard>
      </TypeCards>
      <Diagram caption="Что делать после прерванной миграции" orientation="vertical">
        <DiagramFlow label="Путь от ошибки к проверенному восстановлению" orientation="vertical">
          <DiagramNode title="Отказ на одноразовой копии">Сохраняем сообщение и прекращаем повтор.</DiagramNode>
          <DiagramArrow label="исследуем" />
          <DiagramNode title="Фактическое состояние">Отдельно читаем схему, revision и все строки.</DiagramNode>
          <DiagramArrow label="восстанавливаем из снимка" />
          <DiagramNode title="Новый файл">Сравниваем его с описью и читаем обычным способом.</DiagramNode>
        </DiagramFlow>
      </Diagram>
      <p>На схеме новый файл появляется из проверенного снимка, а не из базы с неизвестным состоянием. Только сравнение подтверждает пригодность восстановления.</p>
      <QuizCard question="Если команда завершилась ошибкой, мы уже знаем, что ни одно изменение схемы не сохранилось." options={["Правда", "Ложь"]} correctIndex={1} explanation="Нужно отдельно прочитать фактические структуру, данные и версию. Поведение DDL зависит от окружения." />
    </Section>
    <Section number="02" title="Ошибка до и после commit">
      <Lead>Исключение прерывает выполнение. Оно не отменяет автоматически всё, что происходило раньше.</Lead>
      <p>Сохраним пример в <code>transaction_demo.py</code> внутри отдельного пустого учебного каталога и запустим <code>python transaction_demo.py</code> из этого каталога. Программа создаёт новую базу заметок. Повторный запуск с тем же именем остановится, чтобы не перезаписать файл.</p>
      <CodeBlock code={transactionExample} />
      <p>Сначала <code>commit()</code> сохраняет строку «черновик». Затем изменение находится в транзакции, а <code>raise</code> создаёт управляемую ошибку. Обработчик печатает её и вызывает <code>rollback()</code>. Новое соединение прочитает <code>('черновик',)</code>. <code>finally</code> закрывает соединение даже при ошибке. Если подтвердить изменение через <code>commit()</code> до ошибки, последующий rollback не отменит уже подтверждённую запись.</p>
      <p>Теперь проверим другую границу на отдельном файле. Сохраним следующий пример как <code>after_commit.py</code> в том же учебном каталоге и запустим <code>python after_commit.py</code>. У программы новое имя базы, поэтому она не перезаписывает результат первого опыта.</p>
      <CodeBlock code={afterCommitExample} />
      <p>Здесь <code>commit()</code> стоит после изменения и до исключения. Обработчик по-прежнему вызывает <code>rollback()</code>, но новое соединение читает <code>('готово',)</code>: подтверждённая запись уже сохранена. Сравним с первым файлом, где исключение возникло до подтверждения и прочитался «черновик».</p>
      <p>Чтобы другая проверка могла объяснить отличие результатов, запишем версии именно того окружения, которое выполняет опыт. Из <code>project/</code> в PowerShell воспользуемся подготовленным интерпретатором:</p>
      <CodeBlock code={String.raw`.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -m alembic --version
.\.venv\Scripts\python.exe -c "import sqlite3, sqlalchemy; print(sqlite3.sqlite_version, sqlalchemy.__version__)"`} />
      <p>Первая команда показывает версию Python. <code>-m</code> запускает установленный в этой среде модуль Alembic, <code>--version</code> просит только его версию, а <code>-c</code> исполняет короткую программу: она выводит версии SQLite и SQLAlchemy. Если интерпретатор или библиотека недоступны, сначала восстанавливаем подготовленную учебную среду, а не продолжаем с другим Python.</p>
      <p>Этот опыт касается изменения строки в показанной конфигурации. Для изменения схемы SQLite и драйвер могут вести себя иначе. Мы фиксируем версии Python, SQLite, SQLAlchemy и Alembic и наблюдаем результат своего опыта, не обещая универсальный откат DDL.</p>
    </Section>
    <Section number="03" title="Адрес и история миграций">
      <p>Ревизия описывает переход схемы. <code>current</code> читает отметку базы, а <code>head</code> обозначает конец выбранной истории. Совпадение имени файла конфигурации и каталога недостаточно: важно, куда действительно подключается <code>env.py</code>.</p>
      <p>В отдельной учебной конфигурации <code>sqlalchemy.url</code> содержит единственный URL подключения. <code>expected_database_path</code> содержит абсолютный контрольный путь к тому же файлу. Проверим оба значения до подключения и до настройки offline-режима. Для программного запуска оба значения передают через <code>Config.set_main_option</code>.</p>
      <p>Ниже функция для отдельного учебного <code>env.py</code>. Она принимает уже созданный Alembic <code>Config</code>; её вызов должен происходить до создания engine и до offline <code>configure</code>. Это фрагмент проверки, а не самостоятельная программа.</p>
      <CodeBlock code={guardExample} />
      <p><code>make_url</code> разбирает URL, <code>url.database</code> даёт путь файла, а <code>resolve()</code> приводит путь к абсолютной форме. Если изменить только URL, оставив прежний контрольный путь, функция откажет до подключения. Сначала повторим это на отдельной синтетической базе и убедимся, что ошибочный новый файл не появился.</p>
      <p>Для опыта в PowerShell из <code>project/</code> сначала убеждаемся, что каталог <code>failure_lab</code> отсутствует: первая команда должна вернуть <code>False</code>. Затем создаём каталог и копируем в него конфигурацию и историю:</p>
      <CodeBlock code={'Test-Path -LiteralPath failure_lab\nNew-Item -ItemType Directory -Path failure_lab\nCopy-Item -LiteralPath alembic.ini -Destination failure_lab/alembic.ini\nCopy-Item -LiteralPath migrations -Destination failure_lab/migrations -Recurse'} />
      <p><code>New-Item</code> создаёт каталог, <code>Copy-Item</code> копирует файлы, а <code>-Recurse</code> переносит содержимое дерева. Не используем <code>-Force</code> и не копируем поверх занятой цели. В ini выбираем расположение скопированной истории:</p>
      <CodeBlock code="script_location = %(here)s/migrations" />
      <p>Там же указываем URL одноразовой базы и соответствующий абсолютный контрольный путь. <code>%(here)s</code> указывает на каталог выбранного ini. Команда ниже выбирает этот файл и показывает текущую revision:</p>
      <CodeBlock code="python -m alembic --config failure_lab/alembic.ini current" />
      <p>Команда <code>revision</code> с описанием «failure probe» создаёт отдельную учебную ревизию, а <code>upgrade head</code> применяет выбранную историю. Перед каждой командой проверяем пару адресов.</p>
      <p>Пройдём независимый пример на заметках. Вместо одного генератора создадим несколько небольших файлов и после каждого шага увидим его назначение. Начнём в отдельном пустом учебном каталоге. Сохраним первый файл как <code>setup_demo.py</code> и выполним <code>python setup_demo.py</code> из этого каталога.</p>
      <CodeBlock code={demoSetup} />
      <p><code>mkdir</code> создаёт отдельную область, а <code>open("xb")</code> резервирует только свободное имя. На экране появляется абсолютный контрольный путь. Повтор с тем же каталогом откажет: прежний пример останется на месте.</p>
      <p>В созданной папке <code>demo_simple</code> сохраним файл <code>alembic.ini</code>. В последней строке вместо подписи вставим полный путь, который напечатал предыдущий шаг. Это контрольное значение, не второй URL.</p>
      <CodeBlock code={'[alembic]\nscript_location = %(here)s/migrations\nsqlalchemy.url = sqlite:///./demo_simple/notes.sqlite3\nexpected_database_path = ВСТАВИТЬ_ПОЛНЫЙ_ПУТЬ'} />
      <p>Теперь сохраним следующий файл по указанному пути. Он читает оба значения из одной конфигурации и сравнивает их до подключения и до offline configure.</p>
      <CodeBlock code="demo_simple/migrations/env.py" />
      <p><code>make_url</code> разбирает URL, <code>resolve</code> сравнивает абсолютные пути; при ошибке мы ещё не открыли базу.</p>
      <CodeBlock code={demoEnvironment} />
      <p>История состоит из двух отдельных файлов в этом каталоге:</p>
      <CodeBlock code="demo_simple/migrations/versions/" />
      <p>Первый, <code>a_initial.py</code>, создаёт таблицу. Его <code>revision</code> является именем этого перехода, а <code>down_revision = None</code> означает начало истории.</p>
      <CodeBlock code={demoBaseline} />
      <p>Второй файл, <code>b_error.py</code>, ссылается на первый через <code>down_revision</code>. Он добавляет необязательный столбец заметки, затем поднимает управляемое исключение.</p>
      <CodeBlock code={demoFailure} />
      <p>Из учебного каталога запускаем команду:</p>
      <CodeBlock code="python -m alembic --config demo_simple/alembic.ini upgrade demo_initial" />
      <p>Она применяет только первый переход; метка в базе становится <code>demo_initial</code>. Сохраним рядом <code>seed_demo.py</code> и запустим <code>python seed_demo.py</code>, чтобы добавить две учебные строки.</p>
      <CodeBlock code={demoSeed} />
      <p>Программа выводит две строки: первая содержит ID 1 и «черновик», вторая ID 2 и «готово». <code>commit</code> подтвердил обе записи, а выборка показала их по ID. Теперь выполним:</p>
      <CodeBlock code="python -m alembic --config demo_simple/alembic.ini upgrade head" />
      <p>Мы увидим <code>RuntimeError</code> с сообщением «учебный отказ» и ненулевой код процесса; в PowerShell его показывает <code>$LASTEXITCODE</code>. Сообщение не говорит, сохранился ли первый шаг.</p>
      <p>Далее в примере чтения мы увидим схему и обе строки новым соединением. На проверенных Python 3.12, SQLite 3.49.1 и Alembic 1.18.4 столбец <code>memo</code> остался, а revision осталась <code>demo_initial</code>. На другой конфигурации фиксируем реальное наблюдение. Для пробы изменим в ini только URL на другое новое имя, оставив контрольный путь прежним: команда должна остановиться до подключения, а новый файл не появится. Затем вернём URL.</p>
      <p>Вернёмся к одноразовой копии Planner. В PowerShell из <code>project/</code> после проверки свободного назначения создаём её командой:</p>
      <CodeBlock code="python -m app.DBase.backup_snapshot data/after-expand-snapshot.sqlite3 data/failure-lab.sqlite3" />
      <p>Первый путь указывает на проверенный снимок после расширения, второй на новый файл. Сообщение об ошибке прерывает дальнейшие шаги.</p>
      <p>После подготовки ini в <code>failure_lab</code> и проверки пары адресов выполняем по порядку:</p>
      <CodeBlock code={'python -m alembic --config failure_lab/alembic.ini current\npython -m alembic --config failure_lab/alembic.ini revision -m "failure probe"'} />
      <p>В файле скопированного каталога revisions сохраняем фактически созданные значения revision и down_revision. Форма добавления столбца и ошибки уже разобрана на заметках. Затем выполняем upgrade:</p>
      <CodeBlock code="python -m alembic --config failure_lab/alembic.ini upgrade head" />
      <p>Сразу записываем <code>$LASTEXITCODE</code>: ноль обозначает успешный выход процесса, ненулевое число вместе с RuntimeError показывает ожидаемый отказ. Это ещё не ответ о состоянии схемы.</p>
      <p>Путь одноразовой базы проверяем после создания через снимок: Resolve-Path -LiteralPath data/failure-lab.sqlite3 показывает абсолютный путь для expected_database_path. При копировании env.py сохраняем проверку обеих ветвей, а не добавляем второй адрес в исходный код. В отдельной ревизии после реально существующего head выполняем один подготовленный шаг, затем вызываем показанное выше исключение <code>RuntimeError</code> с сообщением «учебный отказ». После запуска сохраняем код процесса и traceback. Ошибка не показывает, успел ли первый шаг изменить таблицу. Не выполняем <code>stamp head</code>: метка не восстанавливает схему.</p>
    </Section>
    <Section number="04" title="Смотрим на фактическое состояние">
      <p>Проверим отдельно целостность, состав объектов, столбцы, версию и строки. <code>PRAGMA integrity_check</code> с ответом <code>ok</code> проверяет структуру файла, но не доказывает, что нужные записи присутствуют. <code>PRAGMA table_info</code> показывает столбцы, а <code>sqlite_schema</code> хранит определения таблиц, индексов и других объектов.</p>
      <p>Воспользуемся отдельной базой <code>demo_simple/notes.sqlite3</code>, которую создал предыдущий независимый пример с двумя строками <code>notes(id, body)</code>. Сохраним следующий код как <code>inspect_notes.py</code> и запустим из того же учебного каталога. <code>mode=ro</code> запрещает запись и создание файла; <code>closing</code> закрывает соединение после чтения.</p>
      <CodeBlock code={inspectionExample} />
      <p>В выводе <code>[(2,)]</code> после количества число 2 означает две записи, а <code>[('demo_initial',)]</code> показывает метку истории. Пустые списки индексов и ссылок означают, что в этой таблице они не объявлены. В Planner вместо таблицы notes запрашиваем tasks и все поля <code>id, title, priority, is_done, tags, title_normalized, description</code> в порядке ID. Сравниваем каждое значение с описью до опыта. И отсутствие столбца, и его наличие фиксируем как факт; по одному из этих исходов нельзя обобщать поведение других версий.</p>
      <QuizCard question="Проверка integrity_check вернула ok, значит все исходные задачи обязательно сохранились." options={["Правда", "Ложь"]} correctIndex={1} explanation="Файл может быть технически исправен и содержать меньше строк. Сравним количество, ID и значения каждого поля." />
    </Section>
    <Section number="04a" title="Связи и перестроение таблицы">
      <p>Иногда SQLite не позволяет выполнить нужное изменение одной командой. При batch-перестроении инструмент может создать новую таблицу, перенести строки и заменить старую. При таком переносе нужно проверить не только строки, но и ограничения CHECK, индексы и внешние ключи. Мы рассматриваем риск; перестраивать таблицу Planner для этого опыта не требуется.</p>
      <p>Внешний ключ проверяется настройкой <code>PRAGMA foreign_keys</code> конкретного соединения. Изменение этой настройки внутри открытой транзакции не действует. Увидим это на отдельной базе книг и полок. Сохраним код в <code>foreign_keys_demo.py</code> в новом учебном каталоге и запустим <code>python foreign_keys_demo.py</code>. Повтор с тем же именем файла остановится.</p>
      <CodeBlock code={foreignKeysExample} />
      <p>До транзакции мы включили проверку и получили <code>1</code>. После <code>BEGIN</code> попытка выключить её не меняет значение: снова видим <code>1</code>. Книгу с несуществующей полкой база отклоняет через <code>IntegrityError</code>; мы откатываем транзакцию. Пустой результат <code>foreign_key_check</code> означает, что проверка не обнаружила нарушений в этом файле. Затем отдельно читаем имя индекса и определение CHECK. Это разные признаки: успешное чтение строк не доказывает, что при перестроении сохранились все ограничения.</p>
      <p>Для другой попытки используем новый файл, а не отключаем проверку на рабочей базе. Новое соединение имеет собственную настройку: её нужно проверить именно там, где выполняется операция, до начала транзакции.</p>
      <CodeSequence title="Выбираем безопасную последовательность" prompt="Учебная миграция прервалась на отдельной базе. Расположим действия так, чтобы сначала узнать состояние и сохранить возможность восстановления." pieces={[
        {id:"observe",code:"Прочитать схему, revision и строки без записи"},
        {id:"restore",code:"Восстановить проверенный снимок в новый файл"},
        {id:"compare",code:"Сравнить восстановленный файл с описью"},
        {id:"stamp",code:"Поставить head на базу с неизвестной схемой"},
      ]} correctOrder={["observe", "restore", "compare"]} explanation="Сначала мы фиксируем следы, затем используем отдельный путь и проверяем содержимое. Метка head не исправляет структуру." incorrectExplanation="Вернёмся к схеме: действие с неизвестной базой не восстанавливает данные. Для проверки нужны наблюдение, отдельное восстановление и сравнение." />
    </Section>
    <Section number="05" title="Восстанавливаем отдельно">
      <p>Восстановление начинает с проверенного снимка после расширения и нового свободного пути. Учебная команда <code>python -m app.DBase.backup_snapshot SOURCE DEST</code> принимает сначала путь существующего снимка, затем путь нового файла. При занятой цели она должна отказать без перезаписи. Сам <code>backup</code> не выдаёт код успеха: сначала ждём завершения без исключения, затем открываем результат и сравниваем его.</p>
      <p>Покажем механизм на отдельной базе заметок. Сначала подготовим источник и контрольный файл. Сохраним код как <code>prepare_recovery.py</code> в новом учебном каталоге и выполним <code>python prepare_recovery.py</code> оттуда. Программа создаёт отдельный каталог <code>demo_recovery</code>; повтор с занятым каталогом останавливается.</p>
      <CodeBlock code={recoveryPrepare} />
      <p>Теперь у нас есть база с двумя заметками и контрольный файл с байтами KEEP. Сохраним следующий код рядом как <code>copy_recovery.py</code> и выполним <code>python copy_recovery.py</code>. Он создаст снимок, затем восстановит его в ещё одно новое имя.</p>
      <CodeBlock code={recoveryRun} />
      <p>Метод <code>backup</code> вызывается у соединения источника; аргументом служит соединение назначения. <code>open('xb')</code> резервирует только новое имя, поэтому занятому контрольному файлу сохраняются байты KEEP. После запуска видим две строки заметок, <code>[('ok',)]</code> и подтверждение неизменности занятого файла. При исключении после резервирования новый файл может быть неполным: закрываем соединения и не принимаем его за результат.</p>
      <p>В проекте команда <code>python -m app.DBase.backup_snapshot SOURCE DEST</code> работает из <code>project/</code>. В отдельном примере снимок заметок является SOURCE, а ещё не существующая копия DEST; пути с пробелами заключаем в кавычки. Для своей пробы меняем имена, открываем полученную копию и проверяем известные строки. До работы с Planner проверяем, что созданная ранее обёртка действительно поддерживает именно эти два аргумента и отказывает на занятом пути.</p>
      <p>Восстановленный файл проверяем отдельно: <code>integrity_check</code>, схема, версия, число записей и все значения. Особое внимание уделяем двум синтетическим строкам: у одной <code>description</code> равно NULL, у другой содержит непустой текст. Затем из project/ в PowerShell задаём $env:DATABASE_URL = 'sqlite:///./data/recovered-check.sqlite3', проверяем путь и запускаем .\.venv\Scripts\python.exe -m app.main. В меню выбираем list, затем find и существующий ID, после чего exit. Новый процесс читает восстановленный файл по явному URL. Эти внутренние значения не становятся полями API или CLI.</p>
      <p>Для корректного повтора создаём ещё одну новую копию data/retry-check.sqlite3 и отдельный retry_lab так же, как failure_lab, но копируем исходные alembic.ini и migrations, а не экспериментальную историю. В retry_lab/alembic.ini указываем script_location = %(here)s/migrations, sqlalchemy.url = sqlite:///./data/retry-check.sqlite3 и expected_database_path из Resolve-Path -LiteralPath data/retry-check.sqlite3. После проверки пары запускаем python -m alembic --config retry_lab/alembic.ini upgrade head. В retry_lab/migrations/env.py действует та же проверка online и offline до подключения. Если снимок уже на head, повторный upgrade показывает сохранность состояния на head, но не проверяет переход старой базы от baseline.</p>
    </Section>
    <Section number="06" title="Что мы будем делать в практике">
      <p>Мы будем работать в отдельной учебной копии. Сохранённый снимок даст нам возможность проверить восстановление, а опись покажет, с чем сравнивать результат.</p>
      <LinkedNotes items={[
        {title:"Подготовим вход",description:"Проверим пути и запишем схему, revision и значения двух задач."},
        {title:"Исследуем отказ",description:"В отдельной истории вызовем ошибку и прочитаем фактическое состояние без повторной записи."},
        {title:"Восстановим и повторим",description:"Сравним новый файл со снимком и выполним корректную историю на другой копии."},
      ]} />
      <p>Каждая проверка имеет свой результат: ненулевой код показывает ошибку процесса, запросы показывают фактическое состояние, а отдельное восстановление и чтение подтверждают пригодность копии. При неизвестном адресе или несовпадении останавливаемся и сохраняем наблюдения.</p>
    </Section>
  </RichLesson>;
}
