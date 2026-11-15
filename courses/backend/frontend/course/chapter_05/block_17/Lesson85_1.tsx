import { Callout, CodeBlock, Diagram, DiagramFlow, DiagramNode, DiagramArrow, Lead, LinkedNotes, RichHero, RichLesson, Section, QuizCard } from "../../shared";

const addressExample = `from pathlib import Path
from sqlalchemy.engine import URL, make_url

folder = Path("demo_guard").resolve()
good = (folder / "good.sqlite3").resolve()
wrong = (folder / "wrong.sqlite3").resolve()

def check_address(raw_url, expected_path):
    url = make_url(raw_url)
    if url.drivername not in {"sqlite", "sqlite+pysqlite"}:
        raise ValueError("Нужна файловая SQLite")
    if (not url.database or url.database == ":memory:" or url.query
            or url.host is not None or url.port is not None
            or url.username is not None or url.password is not None):
        raise ValueError("Нужен понятный путь к файлу")
    expected = Path(expected_path)
    if not expected.is_absolute():
        raise ValueError("Ожидаемый путь должен быть абсолютным")
    actual = Path(url.database).resolve()
    expected = expected.resolve()
    print("Адреса:", actual, expected)
    if actual != expected:
        raise ValueError("Адреса не совпадают")
    return actual

good_url = URL.create("sqlite", database=good.as_posix()).render_as_string()
wrong_url = URL.create("sqlite", database=wrong.as_posix()).render_as_string()
print("Совпадает:", check_address(good_url, str(good)) == good)
try:
    check_address(wrong_url, str(good))
except ValueError as error:
    print("Остановка:", error)
print("Ошибочный файл создан:", wrong.exists())`;

const configExample = `from pathlib import Path
from alembic.config import Config
from alembic import command
from sqlalchemy.engine import URL

# В готовом тесте tmp_path выдаёт pytest, а ini и история относятся
# к одной учебной копии проекта.
target = (tmp_path / "test.sqlite3").resolve()
config = Config("alembic.ini")
url = URL.create("sqlite", database=target.as_posix()).render_as_string()
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
config.set_main_option("expected_database_path", str(target).replace("%", "%%"))
command.upgrade(config, "head")`;

const historyIni = `[alembic]
script_location = migrations
sqlalchemy.url = sqlite:///placeholder.sqlite3
expected_database_path = unused
`;

const historyEnv = `from pathlib import Path
from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

config = context.config
raw = config.get_main_option("sqlalchemy.url")
expected_text = config.get_main_option("expected_database_path")
if not raw or not expected_text or not Path(expected_text).is_absolute():
    raise ValueError("Нет адреса или абсолютного пути")
url = make_url(raw)
if (url.drivername not in {"sqlite", "sqlite+pysqlite"} or not url.database or url.database == ":memory:"
        or url.query or url.host is not None or url.port is not None
        or url.username is not None or url.password is not None):
    raise ValueError("Нужна файловая SQLite")
actual = Path(url.database).resolve()
expected = Path(expected_text).resolve()
print("Пути:", actual, expected)
if actual != expected:
    raise ValueError("Адреса не совпадают")
if context.is_offline_mode():
    context.configure(url=raw, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(raw)
    try:
        with engine.connect() as connection:
            context.configure(connection=connection)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()
`;

const historyBaseline = `from alembic import op
import sqlalchemy as sa
revision = "base_note"
down_revision = None
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("notes", sa.Column("id", sa.Integer, primary_key=True), sa.Column("body", sa.Text, nullable=False), sa.CheckConstraint("length(body) > 0", name="ck_body"))
def downgrade():
    op.drop_table("notes")
`;

const historyExpansion = `from alembic import op
import sqlalchemy as sa
revision = "add_memo"
down_revision = "base_note"
branch_labels = None
depends_on = None
def upgrade():
    op.add_column("notes", sa.Column("memo", sa.Text, nullable=True))
def downgrade():
    op.drop_column("notes", "memo")
`;

const migrationDemo = `from pathlib import Path
from contextlib import closing
import sqlite3
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import URL

root = Path('demo_history').resolve()
old = root / 'old.sqlite3'
new = root / 'new.sqlite3'
wrong = root / 'wrong.sqlite3'
if not (root / 'alembic.ini').is_file() or any(p.exists() for p in (old, new, wrong)):
    raise SystemExit('Проверьте историю и выберите свободные учебные пути')

def config_for(target):
    cfg = Config(str(root / 'alembic.ini'))
    cfg.set_main_option('script_location', str(root / 'migrations').replace('%', '%%'))
    url = URL.create('sqlite', database=target.resolve().as_posix()).render_as_string()
    cfg.set_main_option('sqlalchemy.url', url.replace('%', '%%'))
    cfg.set_main_option('expected_database_path', str(target.resolve()).replace('%', '%%'))
    return cfg

cfg = config_for(old)
command.upgrade(cfg, 'base_note')
with closing(sqlite3.connect(old)) as db:
    db.execute("INSERT INTO notes VALUES (1, 'карта'), (2, 'блокнот')")
    db.commit()
command.upgrade(cfg, 'head')
with closing(sqlite3.connect(old)) as db:
    db.execute('UPDATE notes SET memo = ? WHERE id = ?', ('важно', 1))
    db.commit()
command.upgrade(cfg, 'head')
with closing(sqlite3.connect(old)) as db:
    print('Строки:', db.execute('SELECT * FROM notes ORDER BY id').fetchall())

command.upgrade(config_for(new), 'head')
with closing(sqlite3.connect(new)) as db:
    print('Новая схема:', [row[1] for row in db.execute('PRAGMA table_info(notes)')])

bad = config_for(new)
wrong_url = URL.create('sqlite', database=wrong.as_posix()).render_as_string()
bad.set_main_option('sqlalchemy.url', wrong_url.replace('%', '%%'))
try:
    command.upgrade(bad, 'head')
except ValueError as exc:
    print('Отказ:', exc)
print('Ошибочный файл создан:', wrong.exists())
`;

const migrationTests = `from pathlib import Path
import sqlite3
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import URL

ROOT = Path('demo_history').resolve()

def config_for(target, expected):
    cfg = Config(str(ROOT / 'alembic.ini'))
    cfg.set_main_option('script_location', str(ROOT / 'migrations').replace('%', '%%'))
    url = URL.create('sqlite', database=target.resolve().as_posix()).render_as_string()
    cfg.set_main_option('sqlalchemy.url', url.replace('%', '%%'))
    cfg.set_main_option('expected_database_path', str(expected.resolve()).replace('%', '%%'))
    return cfg

def revisions(target):
    script = ScriptDirectory.from_config(config_for(target, target))
    bases, heads = script.get_bases(), script.get_heads()
    assert len(bases) == len(heads) == 1
    return bases[0], heads[0]

def test_baseline(tmp_path):
    path = tmp_path / 'initial.sqlite3'
    baseline, _ = revisions(path)
    command.upgrade(config_for(path, path), baseline)
    with sqlite3.connect(path) as db:
        assert [r[1] for r in db.execute('PRAGMA table_info(notes)')] == ['id', 'body']
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0] == baseline

def test_head(tmp_path):
    path = tmp_path / 'head.sqlite3'
    _, head = revisions(path)
    command.upgrade(config_for(path, path), 'head')
    with sqlite3.connect(path) as db:
        assert [r[1] for r in db.execute('PRAGMA table_info(notes)')] == ['id', 'body', 'memo']
        assert db.execute('SELECT version_num FROM alembic_version').fetchone()[0] == head

def test_mismatch(tmp_path):
    wrong = tmp_path / 'wrong.sqlite3'
    expected = tmp_path / 'expected.sqlite3'
    with pytest.raises(ValueError, match='Адреса не совпадают'):
        command.upgrade(config_for(wrong, expected), 'head')
    assert not wrong.exists()
`;

const clientDemo = `import subprocess
import sys
import httpx2 as httpx


def handle(request):
    if request.method == 'POST' and request.url.path == '/items':
        return httpx.Response(201, json={'id': 1, 'name': 'атлас'})
    return httpx.Response(404, json={'detail': 'не найдено'})


with httpx.Client(base_url='http://demo.local', transport=httpx.MockTransport(handle)) as client:
    response = client.post('/items', json={'name': 'атлас'})
    print('POST:', response.status_code, response.json())
    missing = client.get('/unknown')
    print('GET:', missing.status_code)

result = subprocess.run([sys.executable, '-X', 'utf8', '-c', "print('прочитано')"],
                        capture_output=True, text=True, encoding='utf-8', timeout=5)
print('Процесс:', result.returncode, result.stdout.strip())
process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(10)'])
try:
    print('Запущен:', process.poll() is None)
finally:
    process.terminate()
    process.wait(timeout=5)
print('Завершён:', process.poll() is not None)
`;

const engineDemo = `from pathlib import Path
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

ROOT = Path('demo_history').resolve()

def migrated_engine(path):
    path = path.resolve()
    cfg = Config(str(ROOT / 'alembic.ini'))
    cfg.set_main_option('script_location', str(ROOT / 'migrations').replace('%', '%%'))
    url = URL.create('sqlite', database=path.as_posix()).render_as_string()
    cfg.set_main_option('sqlalchemy.url', url.replace('%', '%%'))
    cfg.set_main_option('expected_database_path', str(path).replace('%', '%%'))
    command.upgrade(cfg, 'head')
    return create_engine(url)

target = ROOT / 'engine_example.sqlite3'
if target.exists():
    raise SystemExit('Выберите новое имя учебного файла')
engine = migrated_engine(target)
try:
    with engine.connect() as connection:
        print('Ревизия:', connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one())
        print('Колонки:', [row[1] for row in connection.execute(text('PRAGMA table_info(notes)'))])
finally:
    engine.dispose()
`;

const valuesDemo = `from contextlib import closing
import sqlite3

with closing(sqlite3.connect(':memory:')) as db:
    db.execute('CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, note TEXT)')
    db.execute('INSERT INTO books VALUES (1, ?, ?)', ('Атлас', None))
    db.execute('INSERT INTO books VALUES (2, ?, ?)', ('Роман', 'Синий'))
    db.commit()
    print('До:', db.execute('SELECT id, title, note FROM books ORDER BY id').fetchall())
    db.execute('UPDATE books SET title = ? WHERE id = ?', ('Новый роман', 2))
    db.commit()
    print('После:', db.execute('SELECT id, title, note FROM books ORDER BY id').fetchall())
`;

const metadataDemo = `from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine, text

engine = create_engine('sqlite:///:memory:')
try:
    original = MetaData()
    Table('books', original, Column('id', Integer, primary_key=True), Column('title', String))
    original.create_all(engine)
    changed = MetaData()
    Table('books', changed, Column('id', Integer, primary_key=True), Column('title', String), Column('note', String))
    changed.create_all(engine)
    with engine.connect() as connection:
        print([row[1] for row in connection.execute(text('PRAGMA table_info(books)'))])
finally:
    engine.dispose()
`;

const storageErrorDemo = `from contextlib import closing
from pathlib import Path
import sqlite3

folder = Path(__file__).resolve().parent
try:
    sqlite3.connect(folder)
except sqlite3.OperationalError as error:
    print('Путь недоступен:', error.sqlite_errorcode & 0xFF == sqlite3.SQLITE_CANTOPEN)

with closing(sqlite3.connect(':memory:')) as db:
    try:
        db.execute('SELECT * FROM missing_table')
    except sqlite3.OperationalError as error:
        print('Ошибка схемы это CANTOPEN:', error.sqlite_errorcode & 0xFF == sqlite3.SQLITE_CANTOPEN)
`;

const importDemo = `from contextlib import closing
import json
import sqlite3

records = json.loads('[{"id": 21, "name": "перо"}, {"id": 22, "name": "бумага"}]')
with closing(sqlite3.connect(':memory:')) as db:
    db.execute('CREATE TABLE supplies (id INTEGER PRIMARY KEY, name TEXT NOT NULL)')
    db.commit()
    try:
        with db:
            db.execute('INSERT INTO supplies VALUES (?, ?)', (records[0]['id'], records[0]['name']))
            raise ValueError('учебная ошибка до commit')
    except ValueError:
        print('После ошибки:', db.execute('SELECT * FROM supplies').fetchall())
    with db:
        for row in records:
            db.execute('INSERT INTO supplies VALUES (?, ?)', (row['id'], row['name']))
    print('После успеха:', db.execute('SELECT * FROM supplies ORDER BY id').fetchall())
`;

export function Lesson85_1({ module }: { module?: string }) {
  return <RichLesson>
    <RichHero chip={module ?? "История схемы и сохранность Planner"} title="Регрессия и передача истории" intro="Проверим оба пути миграции, прежние возможности Planner и инструкцию для следующего разработчика." />
    <Section number="00" title="От восстановления к проверенной передаче">
      <Lead>Мы получили восстановимую копию и историю изменения схемы. Теперь важно проверить, что эти части работают вместе.</Lead>
      <LinkedNotes items={[
        {title:"Точка старта",description:"Нам нужны подтверждённый исходный снимок, копия после расширения и принятая история."},
        {title:"Новый вопрос",description:"Как проверить и новую базу, и переход старых задач, не затронув оригинал?"},
        {title:"Результат",description:"Мы соберём проверяемую регрессию и инструкцию передачи."},
      ]}/>
      <p>Открытый файл говорит лишь о том, что его удалось прочитать. Мы проверим схему, данные и поведение обоих интерфейсов отдельно.</p>
    </Section>
    <Section number="01" title="Три уровня проверки">
      <Lead>Разные проверки отвечают на разные вопросы.</Lead>
      <p>Структурная проверка сравнивает столбцы, ограничения и revision. Контрактная проверяет ответ на известный запрос. Сквозная показывает, как несколько частей работают с одним состоянием, в том числе после запуска нового процесса.</p>
      <p>Представим таблицу товаров, в которой записан чай. Таблица может иметь верные столбцы, но функция выдаст кофе: проверка структуры пройдёт, а проверка ответа обнаружит ошибку. Даже верный ответ в старом процессе не доказывает, что запись прочитает новый процесс.</p>
      <QuizCard question="Успешный повтор upgrade доказывает, что API и CLI читают одни и те же задачи." options={["Правда", "Ложь"]} correctIndex={1} explanation="Повтор проверяет путь истории. Чтобы проверить интерфейсы, нужно создать и прочитать данные через оба, затем повторить чтение новым процессом." />
    </Section>
    <Section number="02" title="Сначала проверим адрес">
      <Lead>Перед миграцией мы должны знать, какой файл откроет инструмент.</Lead>
      <p>Работаем в изолированной копии из папки <code>project/</code>. Оригинал и два снимка не выбираем целями. В <code>alembic.ini</code> единственный URL соединения хранится в <code>sqlalchemy.url</code>. Поле <code>expected_database_path</code> содержит абсолютный путь для сравнения и не создаёт второе соединение.</p>
      <p>Для чистой среды в PowerShell сначала проверим выбранный Python командой <code>py -3.12 --version</code>, затем из <code>project/</code> создадим среду <code>py -3.12 -m venv .venv</code>. Команда <code>.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt</code> устанавливает объявленные зависимости в неё. <code>-m</code> запускает модуль этим интерпретатором, а <code>-r</code> указывает файл зависимостей. Если в принятом комплекте указана другая версия Python, используем её вместо примера.</p>
      <p>Адрес приложения задаётся отдельно в PowerShell:</p>
      <CodeBlock caption="Адрес учебной базы приложения" code={"$env:DATABASE_URL = 'sqlite:///./data/regression.sqlite3'"}/>
      <p>Для этого проекта относительный путь разрешается от <code>project/</code>. До использования убедимся, что он ведёт в изолированный учебный каталог и файл не совпадает со снимками. Следующая команда покажет фактически разрешённый URL:</p>
      <CodeBlock caption="Проверяем адрес приложения" code={String.raw`.\.venv\Scripts\python.exe -c "from app.core.config import load_settings; print(load_settings().database_url)"`}/>
      <p>Сравним результат с выбранной целью, а не с соседним именем файла.</p>
      <p>В <code>migrations/env.py</code> оба значения читаются из Alembic Config. До создания engine, соединения и offline configure разбираем URL, получаем путь, разрешаем оба пути и сравниваем. Если адрес пустой, непонятный или отличается от ожидаемого, останавливаемся до подключения. Перед каждой новой целью меняем оба значения и проверяем их снова.</p>
      <p>Независимый пример ниже использует SQLAlchemy. Сохраним его в <code>demo_address.py</code> и запустим выбранным интерпретатором из отдельной учебной папки. Он не создаёт базу: сначала показывает совпадение, затем отказ при другом URL. Абсолютные пути на нашем компьютере будут отличаться.</p>
      <CodeBlock caption="Сравниваем два адреса до соединения" code={addressExample}/>
      <p><code>make_url</code> разбирает строку, <code>url.database</code> даёт файловую часть, а <code>Path.resolve()</code> приводит путь к абсолютному виду. <code>URL.create</code> строит URL из пути. Если файл <code>wrong.sqlite3</code> уже существовал до опыта, выберем другое свободное имя. В выводе <code>Совпадает: True</code> означает равенство разрешённых путей. Затем строка <code>Остановка: Адреса не совпадают</code> показывает отказ до соединения, а <code>Ошибочный файл создан: False</code> подтверждает, что свободное имя осталось свободным. Попробуем поменять только URL и объясним причину отказа.</p>
      <Callout tone="success">Для API и CLI отдельно проверяем <code>DATABASE_URL</code>. Изменение переменной приложения само по себе не меняет Alembic Config.</Callout>
    </Section>
    <Section number="03" title="Новая и заполненная базы проходят разные начала">
      <Lead>Пустой файл получает схему из истории. Заполненная исходная копия уже содержит таблицу и требует сравнения.</Lead>
      <p>Для новой базы выбираем свободный путь. Проверка истории допускает только существующий файл, поэтому сначала явно резервируем выбранное имя в учебном каталоге. Например, в PowerShell из <code>project/</code> выполним:</p>
      <CodeBlock caption="Резервируем свободную учебную цель" code={String.raw`.\.venv\Scripts\python.exe -c "from pathlib import Path; Path('data/new.sqlite3').open('xb').close()"`}/>
      <p>Режим <code>xb</code> создаёт пустой файл и отказывает, если имя уже занято. Затем согласуем оба значения Config и выполняем <code>python -m alembic upgrade head</code>: история создаёт схему внутри пустого файла. Конфигурация сама файл не резервирует. Для старой копии сначала восстанавливаем исходный снимок в новый путь: <code>python -m app.DBase.backup_snapshot SOURCE DEST</code>. Первый аргумент обозначает снимок, второй ещё не существующий файл. Восстановленную базу повторно не резервируем; занятый файл не перезаписываем.</p>
      <p>Переключим оба значения Config на восстановленную копию и проверим абсолютные пути. Сверим исходную структуру с зафиксированной базовой схемой: столбцы, типы, PK, nullable, defaults, CHECK, индексы, внешние ключи и триггеры. Для этого читаем <code>PRAGMA table_info(tasks)</code>, <code>PRAGMA index_list(tasks)</code>, <code>PRAGMA foreign_key_list(tasks)</code> и определения из <code>sqlite_master</code>. Одного PRAGMA недостаточно для всех объектов.</p>
      <p>Только после совпадения берём фактический ID базовой ревизии из <code>migrations/versions/</code> и выполняем <code>python -m alembic stamp BASELINE_ID</code>, подставив этот ID. Затем проверяем <code>current</code> и выполняем <code>upgrade head</code>. Stamp записывает метку и не исправляет схему. При расхождении останавливаемся без маркировки. Снимок после расширения используется для восстановления состояния после отказа, не вместо исходного снимка.</p>
      <p>Проверим переход на отдельной истории заметок. В новой учебной папке выполним команду PowerShell, чтобы создать вложенные каталоги для следующих файлов:</p>
      <CodeBlock caption="Создаём каталоги учебной истории" code={"New-Item -ItemType Directory -Path demo_history/migrations/versions -Force"}/>
      <p>Параметр <code>-Force</code> позволяет создать недостающие родительские папки. Если имя уже занято, выберем другую пустую учебную папку. Нужны согласованные Alembic и SQLAlchemy.</p>
      <p>В файле <code>demo_history/alembic.ini</code> задаём секцию Alembic. Значения адресов ниже служат заполнителями: учебный скрипт перед каждым запуском явно заменит их на согласованную пару. Самостоятельно не запускаем миграцию по этим заполнителям.</p>
      <CodeBlock caption="demo_history/alembic.ini" code={historyIni}/>
      <p>Следующие три файла размещаем по этим путям внутри учебной папки:</p>
      <CodeBlock caption="Пути файлов истории" code={'demo_history/migrations/env.py\ndemo_history/migrations/versions/base_note.py\ndemo_history/migrations/versions/add_memo.py'}/>
      <p>Файл <code>env.py</code> читает пару из Config. Сначала он сравнивает пути, и лишь затем подключается или настраивает offline-режим. Несовпадение прекращает опыт до создания базы.</p>
      <CodeBlock caption="Настройка истории: env.py" code={historyEnv}/>
      <p>Первая ревизия создаёт таблицу заметок и ограничение непустого текста. Файл <code>base_note.py</code> содержит только исходную схему.</p>
      <CodeBlock caption="Исходная ревизия заметок" code={historyBaseline}/>
      <p>Вторая ревизия ссылается на первую и добавляет необязательную заметку. Её сохраняем отдельно в файле <code>add_memo.py</code> по указанному выше пути.</p>
      <CodeBlock caption="Расширение таблицы заметок" code={historyExpansion}/>
      <p>Теперь сохраним следующий файл как <code>parts_run.py</code> рядом с <code>demo_history</code> и запустим <code>python parts_run.py</code>. Он сначала создаёт базу на исходной ревизии и записывает две строки, затем применяет расширение, сохраняет текст у одной строки и повторяет upgrade. Другая пустая база проходит всю историю сразу. Импорт <code>closing</code> гарантирует закрытие соединения после блока; <code>commit</code> сохраняет изменения.</p>
      <CodeBlock caption="Проверяем два пути и ошибочный адрес" code={migrationDemo}/>
      <p>Мы видим две строки: у одной текст <code>важно</code>, у другой <code>None</code>, то есть SQL NULL. У новой базы три колонки. Затем меняется только URL, ожидаемый путь остаётся прежним: появляется сообщение об отказе и <code>Ошибочный файл создан: False</code>. Попробуем мысленно изменить ожидаемый путь и объясним, какая проверка остановит команду. Идентификаторы заметок относятся только к этому примеру.</p>
      <p>Этот пример создаёт базу через baseline. Для уже существующей заполненной базы перед <code>stamp</code> дополнительно сравниваем схему с базовой ревизией; сам stamp не создаёт таблицу. Не переносим идентификаторы заметок в историю Planner.</p>
      <p>Сравним число задач, ID, title, priority, is_done, tags и title_normalized до и после. Новая колонка description допускает NULL. На синтетических данных проверим отдельно NULL и непустой текст и сохраним оба значения после повторного upgrade.</p>
    </Section>
    <Section number="04" title="Тест должен пройти настоящую историю">
      <Lead>Таблица из metadata может совпадать с моделью, даже если ревизии не работают.</Lead>
      <p><code>create_all</code> создаёт отсутствующие таблицы, но не добавляет колонку в уже существующую. Поэтому контрактные и интеграционные тесты используют отдельную SQLite и настоящий upgrade. Быстрые unit-тесты могут использовать metadata, но не доказывают работу истории.</p>
      <p>Посмотрим на разницу модели и готовой таблицы без файлов Planner. Сохраним пример как <code>demo_metadata.py</code> в отдельной папке и запустим <code>python demo_metadata.py</code> в подготовленной среде SQLAlchemy.</p>
      <CodeBlock caption="Почему повтор create_all не добавляет колонку" code={metadataDemo}/>
      <p>Первая metadata описывает таблицу книг с ID и названием и создаёт её. Во второй metadata появляется поле заметки, но повторный <code>create_all</code> обнаруживает существующую таблицу и не перестраивает её. Вывод <code>['id', 'title']</code> не содержит <code>note</code>. Это кратковременная база в памяти, а не миграционная история. Попробуем предсказать результат для ещё одной новой колонки: без миграции существующая таблица не получит и её.</p>
      <p>В проекте проверка адреса разрешает только файл внутри <code>project/</code>. Обычный временный каталог pytest может оказаться вне него, поэтому для проектных тестов выделим новую папку внутри учебной копии. Параметр <code>--basetemp</code> задаёт место для <code>tmp_path</code>, а TEMP и TMP используются другими временными файлами. Pytest очищает выбранный basetemp: выбираем для каждого запуска новую подпапку, никогда не указываем каталог проекта, data или снимки.</p>
      <p>В PowerShell из <code>project/</code> сохраним прежние TEMP и TMP, выполним сначала адресные тесты, затем полный набор. Команда показывает созданный путь; после успеха или ошибки <code>finally</code> возвращает настройки окружения. Новая папка остаётся отдельной учебной областью.</p>
      <p><code>Join-Path</code> соединяет путь проекта с именем папки, а <code>guid</code> помогает получить новое имя для каждого запуска. Если оно уже занято, проверка останавливает выполнение. <code>New-Item</code> создаёт папку; <code>-ErrorAction Stop</code> прекращает подготовку при ошибке. После каждой команды pytest значение <code>$LASTEXITCODE</code> показывает код завершения: ненулевой код останавливает сценарий. Блок <code>try</code> выполняет проверки, а <code>finally</code> возвращает TEMP и TMP даже при ошибке внутри него.</p>
      <CodeBlock caption="Безопасный запуск проектных тестов" code={String.raw`$oldTemp = $env:TEMP
$oldTmp = $env:TMP
$runRoot = Join-Path (Get-Location) ('.pytest-run-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $runRoot) { throw 'Выбранное имя уже занято' }
New-Item -ItemType Directory -Path $runRoot -ErrorAction Stop | Out-Null
try {
    $env:TEMP = $runRoot
    $env:TMP = $runRoot
    Write-Host "Учебный временный каталог: $runRoot"
    .\.venv\Scripts\python.exe -m pytest -q --basetemp (Join-Path $runRoot 'smoke') tests/test_db_schema.py tests/test_task_migrations.py
    if ($LASTEXITCODE -ne 0) { throw 'Предварительная проверка не прошла' }
    .\.venv\Scripts\python.exe -m pytest -q --basetemp (Join-Path $runRoot 'full')
    if ($LASTEXITCODE -ne 0) { throw 'Полный набор не прошёл' }
} finally {
    $env:TEMP = $oldTemp
    $env:TMP = $oldTmp
}`}/>
      <p>В pytest <code>tmp_path</code> даёт отдельный временный каталог теста. Ниже фрагмент для фикстуры: <code>Config</code> читает тот же ini, <code>set_main_option</code> заменяет два значения для этого вызова, а <code>command.upgrade</code> запускает историю. В <code>env.py</code> нужно читать именно переданные значения. Здесь <code>tmp_path</code> получает тест от pytest, поэтому фрагмент помещаем внутрь теста или фикстуры.</p>
      <CodeBlock caption="Параметры миграции для одного теста" code={configExample}/>
      <p>Теперь проверим историю заметок отдельными тестами. После запуска примера сохраним следующий файл рядом с ним как <code>test_demo_history.py</code> и выполним <code>python -m pytest -q test_demo_history.py</code>. Флаг <code>-q</code> сокращает вывод pytest, а имя файла ограничивает запуск показанными тестами. Первый тест проверяет настоящую начальную revision и две колонки, второй проходит до head и видит три, третий меняет URL и проверяет отказ без создания ошибочного файла.</p>
      <CodeBlock caption="Три самостоятельные временные базы" code={migrationTests}/>
      <p>В тестах <code>tmp_path</code> выдаёт отдельный каталог каждому случаю. <code>ScriptDirectory.from_config</code> читает файлы указанной истории; <code>get_bases()</code> и <code>get_heads()</code> возвращают исходные и конечные revision. Мы отдельно убеждаемся, что в учебной истории они единственные, а не придумываем ID Planner. <code>pytest.raises</code> проверяет ожидаемое исключение, а <code>assert</code> сравнивает фактические значения с ожидаемыми. Успех означает три пройденных случая, но не проверяет проект Planner вместо этих заметок.</p>
      <p>Ранее мы отдельно установили конфигурацию и вызвали upgrade. В проектных тестах удобно собрать эти действия в функцию. Сохраним пример как <code>demo_engine.py</code> рядом с каталогом <code>demo_history</code> и запустим <code>python demo_engine.py</code> из той же папки. Имя <code>engine_example.sqlite3</code> должно быть свободным.</p>
      <CodeBlock caption="Сначала история, затем engine" code={engineDemo}/>
      <p>Функция создаёт Config для отдельного пути, задаёт URL и ожидаемый путь, применяет историю и только после этого возвращает engine. На экране видим ревизию <code>add_memo</code> и колонки <code>id</code>, <code>body</code>, <code>memo</code>. <code>engine.dispose()</code> освобождает ресурсы. Мы используем эту идею для каждого учебного файла, но не заменяем ею опыт на неподготовленной базе: для проверки отказа такой файл должен оставаться неподготовленным.</p>
      <p>Знак процента удваиваем при передаче в ConfigParser, затем проверяем прочитанное значение. Для отрицательного случая меняем только URL и оставляем ожидаемый путь прежним: миграция останавливается, ошибочный файл не появляется. Для проверки исходного договора отдельная база проходит конкретную базовую revision; другая база идёт до head и проверяет актуальную схему с description.</p>
    </Section>
    <Section number="05" title="Одно состояние в двух процессах">
      <Lead>Разные процессы и Session могут работать с одним файлом, если их адреса совпадают.</Lead>
      <p>Для практической проверки нам понадобятся HTTP-клиент и отдельные процессы. В принятой среде клиент называется <code>httpx2</code>. Следующий независимый пример не обращается к сети: <code>MockTransport</code> возвращает подготовленные ответы. Сохраним его как <code>demo_client.py</code> в отдельной учебной папке и запустим <code>python demo_client.py</code>.</p>
      <CodeBlock caption="HTTP-ответ и жизненный цикл процесса" code={clientDemo}/>
      <p>POST получает статус 201 и словарь товара, отсутствующий путь даёт 404. <code>subprocess.run</code> дожидается отдельного Python, а <code>sys.executable</code> выбирает тот же интерпретатор. <code>-X utf8</code> задаёт кодировку дочернего Python, <code>capture_output</code> сохраняет вывод, <code>timeout</code> ограничивает ожидание. <code>Popen</code> запускает процесс, <code>poll</code> проверяет его состояние, а <code>terminate</code> и <code>wait</code> завершают именно созданный нами процесс. В примере мы не запускаем API и не считаем подставной ответ проверкой Planner.</p>
      <p>Для настоящей проверки используем <code>httpx2.Client</code> с локальным адресом, <code>json</code> для тела, <code>status_code</code>, <code>json()</code> и <code>content</code> для результата. <code>sqlite3.connect</code> позволяет сравнить внутренние поля в уже проверенном файле; <code>socket</code> помогает выбрать свободный локальный порт. Во всех случаях используем только отдельную учебную копию.</p>
      <Diagram caption="Как одна задача проходит через разные процессы" orientation="vertical">
        <DiagramFlow label="Сохранение и повторное чтение" orientation="vertical">
          <DiagramNode title="API создаёт задачу"><p>Ответ содержит ID новой записи.</p></DiagramNode>
          <DiagramArrow label="записывает в общий файл" />
          <DiagramNode title="Учебная SQLite" shape="database"><p>Хранит задачу после завершения запроса.</p></DiagramNode>
          <DiagramArrow label="CLI находит по ID и отмечает выполненной" />
          <DiagramNode title="Изменённое состояние"><p>В той же базе у задачи изменён признак выполнения.</p></DiagramNode>
          <DiagramArrow label="новый процесс читает заново" />
          <DiagramNode title="API после перезапуска"><p>Тот же ID и признак выполнения подтверждают сквозной путь.</p></DiagramNode>
        </DiagramFlow>
      </Diagram>
      <p>Стрелки показывают не передачу объекта из памяти одного процесса в другой, а обращения к одному проверенному файлу. Если адреса разные, каждый интерфейс может работать отдельно, но этот путь не подтвердится.</p>
      <p>Создадим тестовую задачу через API и запомним ID. В отдельном CLI найдём её и отметим выполненной. Закроем процессы, снова запустим API с тем же учебным адресом и прочитаем задачу и статистику. Новое чтение показывает сохранённое состояние, а не старый объект в памяти.</p>
      <p>Проверим поиск в CLI, а в API порядок фильтра, сортировки и limit. Полная замена PUT и частичный PATCH должны сохранять внутренние данные. DELETE возвращает 204 без тела; отдельный GET удалённого ID даёт 404. Некорректные данные дают 422 и не меняют соседние строки.</p>
      <p>Почему публичного ответа недостаточно для проверки внутреннего поля? Попробуем на отдельной таблице книг. Сохраним код в <code>demo_values.py</code> и запустим <code>python demo_values.py</code>. Здесь SQLite живёт только в памяти этого примера; миграции Planner по-прежнему применяются к отдельному файлу.</p>
      <CodeBlock caption="Меняем название, сохраняя внутренние заметки" code={valuesDemo}/>
      <p>До изменения мы видим <code>(1, 'Атлас', None)</code> и <code>(2, 'Роман', 'Синий')</code>. После обновления меняется только название второй книги; <code>None</code> и текст заметки остаются прежними. В запросе <code>WHERE id = ?</code> выбирает одну строку, а значения передаются отдельно. Если убрать WHERE, изменятся обе строки. В практике мы сравним поля задач по их собственным ID до и после разрешённых операций.</p>
      <p>API и CLI не принимают и не показывают tags и description как поддерживаемые поля. Проверим их напрямую в изолированной SQLite до и после разрешённых операций. Публичное отсутствие поля не доказывает сохранности значения в таблице.</p>
    </Section>
    <Section number="06" title="Импорт, отказы и инструкция">
      <Lead>Явный импорт и обычный запуск выполняют разные задачи.</Lead>
      <p>Для импорта выбираем отдельную мигрированную пустую базу и учебный JSON, затем вызываем <code>python -m app.DBase.import_tasks --source SOURCE --database DATABASE</code>. Повтор на непустой цели должен остановиться без очистки. Сравним строки и байты JSON до и после; при ошибке транзакции проверим rollback. Приложение не подменяет недоступную SQLite файлом JSON.</p>
      <p>Сначала разберём отказ до сохранения на маленьком примере без файлов Planner. Сохраним код как <code>demo_import.py</code> и выполним <code>python demo_import.py</code>. Две записи здесь находятся в строке JSON, а таблица создаётся только в памяти этого процесса.</p>
      <CodeBlock caption="Ошибка до commit и повтор на чистой таблице" code={importDemo}/>
      <p><code>json.loads</code> преобразует строку в список словарей. Внутри первого блока <code>with db</code> запись добавляется, но затем возникает ошибка. Выход из блока отменяет незавершённую транзакцию: после ошибки видим пустой список. Второй блок добавляет обе записи и успешно завершает транзакцию: теперь результат содержит ID 21 и 22. Внешний <code>closing</code> закрывает соединение. Это демонстрация rollback, а не готовый импортёр Planner.</p>
      <p>Не всякая ошибка SQLite означает, что файл недоступен. Сохраним следующий пример в отдельной учебной папке как <code>demo_storage_error.py</code> и запустим <code>python demo_storage_error.py</code>. Он пытается открыть сам каталог как файл базы, а затем обращается к отсутствующей таблице в базе памяти. Существующие базы при этом не открываются.</p>
      <CodeBlock caption="Различаем отказ открытия и ошибку схемы" code={storageErrorDemo}/>
      <p>Первая строка вывода: <code>Путь недоступен: True</code>; вторая: <code>Ошибка схемы это CANTOPEN: False</code>. У исключения SQLite есть числовой <code>sqlite_errorcode</code>. Выражение <code>code &amp; 0xFF</code> выделяет основной код из возможного расширенного, а <code>SQLITE_CANTOPEN</code> обозначает отказ открытия. Отсутствующая таблица тоже вызывает <code>OperationalError</code>, но её код другой. Поэтому нельзя считать любую такую ошибку недоступностью файла.</p>
      <p>В Planner существующий слой работы с Session распознаёт именно отказ открытия и передаёт <code>StorageError</code>; обработчик API отвечает HTTP 503, то есть сообщает о временной недоступности хранилища. Для проверки выбираем отдельную цель, которая не может быть файлом базы, и сравниваем ответ с ошибкой отсутствующей таблицы: её нельзя ошибочно назвать CANTOPEN. Отдельно проверяем закрытие каждой Session после запроса и неизменность учебного JSON: 503 само по себе не доказывает ни закрытие Session, ни отсутствие запасного чтения JSON.</p>
      <p>Проверим <code>/health</code> и прежние GET, PUT, DELETE <code>/preferences</code>, заголовки и cookie <code>planner_view</code> с путём <code>/preferences</code>. Cookie хранит предпочтение вида, а не подтверждает личность. Новые обязательные Origin или CSRF для этого анонимного маршрута не добавляем.</p>
      <p>При первом GET без cookie значение вида по умолчанию равно <code>compact</code>. Для изменения отправляем PUT с JSON <code>{'{"view":"detailed"}'}</code>; ответ устанавливает <code>planner_view</code>. Следующий GET с этой cookie возвращает <code>detailed</code>, а DELETE возвращает 204 и удаляет cookie. Заголовок <code>X-Client-Version</code> мы передаём в запросе, <code>X-Planner-Version</code> получаем в ответе. Эти действия не требуют новой проверки личности.</p>
      <p>В README запишем версии, команды, обе настройки Alembic, фактическую revision, правила сравнения исходной копии, отдельные снимки и результаты тестов. Старую команду <code>app.DBase.init_db</code> согласуем с явной историей или понятным отказом. Обычный запуск API и CLI не должен скрыто менять схему.</p>
    </Section>
    <Section number="07" title="Что мы будем делать в практике">
      <Lead>Соберём отдельные проверки в маршрут передачи, который другой человек сможет повторить на учебной копии.</Lead>
      <LinkedNotes items={[
        {title:"Подготовим вход",description:"Выберем изолированные файлы и убедимся, что URL и ожидаемый путь совпадают до соединения."},
        {title:"Пройдём историю",description:"Проверим новую базу и восстановленную исходную копию, а затем согласуем тестовые фикстуры с миграциями."},
        {title:"Подтвердим поведение",description:"Сверим API, CLI, внутренние значения, импорт и отказы на отдельных учебных данных."},
        {title:"Передадим инструкцию",description:"Запишем версии, команды, ограничения и проверенные результаты в README."},
      ]}/>
      <p>Результат каждого шага становится входом следующего: совпадающие адреса позволяют безопасно проверить схему, а схема и данные нужны для проверки приложения. Если одно свидетельство отсутствует, вернёмся к соответствующему шагу до передачи.</p>
    </Section>
  </RichLesson>;
}
