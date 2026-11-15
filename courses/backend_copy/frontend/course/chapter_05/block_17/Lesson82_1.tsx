import { Callout, CodeBlock, CodeSequence, Diagram, DiagramArrow, DiagramFlow, DiagramNode, Lead, LinkedNotes, RichHero, RichLesson, Section, QuizCard } from "../../shared";

const prepareExample = `from pathlib import Path
import sqlite3

folder = Path("demo_restore")
folder.mkdir(exist_ok=True)
source = folder / "notes.sqlite3"
snapshot = folder / "notes-snapshot.sqlite3"
for path in (source, snapshot):
    with path.open("xb"):
        pass
original = sqlite3.connect(source)
try:
    original.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL CHECK (length(body) > 0))")
    original.execute("INSERT INTO notes VALUES (1, 'Карта'), (3, 'Ключ')")
    original.commit()
    target = sqlite3.connect(snapshot)
    try:
        original.backup(target)
    finally:
        target.close()
finally:
    original.close()
print("Снимок заметок создан")`;

const restoreExample = `from pathlib import Path
import sqlite3

source = Path("demo_restore/notes-snapshot.sqlite3").resolve()
destination = Path("demo_restore/notes-restored.sqlite3").resolve()
if not source.is_file() or source == destination:
    raise ValueError("Неверный источник или совпадают пути")
with destination.open("xb"):
    pass
source_db = sqlite3.connect(source)
try:
    destination_db = sqlite3.connect(destination)
    try:
        source_db.backup(destination_db)
    finally:
        destination_db.close()
finally:
    source_db.close()
print("Копирование завершилось без ошибки")`;

const occupiedExample = `from pathlib import Path

occupied = Path("demo_restore/occupied.bin")
with occupied.open("xb") as file:
    file.write(b"KEEP")
before = occupied.read_bytes()
try:
    with occupied.open("xb"):
        pass
except FileExistsError:
    print("Занято, байты сохранены:", before == occupied.read_bytes())`;

const schemaExample = `from pathlib import Path
import sqlite3

path = Path("demo_restore/notes-restored.sqlite3").resolve()
db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
try:
    objects = db.execute("SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name;").fetchall()
    columns = db.execute("PRAGMA table_info('notes');").fetchall()
    indexes = db.execute("PRAGMA index_list('notes');").fetchall()
    foreign_keys = db.execute("PRAGMA foreign_key_list('notes');").fetchall()
    print("Объекты:", objects)
    print("Столбцы:", columns)
    print("Индексы:", indexes)
    print("Внешние ключи:", foreign_keys)
finally:
    db.close()`;

const rowsExample = `from pathlib import Path
import sqlite3

path = Path("demo_restore/notes-restored.sqlite3").resolve()
db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
try:
    count = db.execute("SELECT COUNT(*) FROM notes;").fetchall()
    rows = db.execute("SELECT id, body FROM notes ORDER BY id;").fetchall()
    print("Количество:", count)
    print("Заметки:", rows)
finally:
    db.close()`;

const readerExample = `from pathlib import Path
import os
from sqlalchemy import create_engine, text

path = Path("demo_restore/notes-restored.sqlite3")
url = os.environ.get("DATABASE_URL")
if not path.is_file() or url != "sqlite:///./demo_restore/notes-restored.sqlite3":
    raise ValueError("Проверьте учебный файл и DATABASE_URL")
engine = create_engine(url)
try:
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id, body FROM notes ORDER BY id")).fetchall()
        print(rows)
finally:
    engine.dispose()`;

const indexExample = `from pathlib import Path
import sqlite3

folder = Path("demo_restore")
folder.mkdir(exist_ok=True)
path = folder / "index-example.sqlite3"
with path.open("xb"):
    pass
db = sqlite3.connect(path)
try:
    db.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT)")
    db.execute("CREATE INDEX idx_notes_body ON notes(body)")
    db.execute("CREATE TABLE labels (id INTEGER PRIMARY KEY, name TEXT)")
    db.commit()
    for table in ("notes", "labels"):
        indexes = db.execute(
            'SELECT name, "unique", origin, partial FROM pragma_index_list(?) ORDER BY name;', (table,)
        ).fetchall()
        print(table, indexes)
        for row in indexes:
            index_name = row[0]
            columns = db.execute(
                "SELECT name FROM pragma_index_info(?) ORDER BY seqno;", (index_name,)
            ).fetchall()
            print(index_name, columns)
finally:
    db.close()`;

export function Lesson82_1({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero chip={module ?? "SQLite и сохранность данных"} title="Проверяем восстановление" intro="Вернём снимок в отдельный файл и выясним, какие проверки подтверждают его пригодность." />
      <Section number="00" title="От снимка к восстановленной базе">
        <Lead>Открытый снимок ещё не отвечает на вопрос, вернутся ли все нужные данные. Для проверки нам понадобятся снимок и опись исходной учебной базы.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "У нас должен быть успешно созданный снимок и опись схемы и задач." },
          { title: "Новый вопрос", description: "Как получить отдельную базу и убедиться, что в ней сохранились схема и данные?" },
          { title: "Результат", description: "Мы восстановим файл, сравним его с описью и отклоним неполный образец." },
        ]} />
        <p>Опись задаёт ожидаемое состояние, а восстановление в новый путь позволяет сравнить его с результатом, сохраняя исходную базу и снимок.</p>
      </Section>
      <Section number="01" title="Три разных пути">
        <Lead>Исходная база, снимок и восстановленная копия выполняют разные роли.</Lead>
        <p>Исходная база содержит учебные задачи. Снимок сохраняет их состояние на момент резервирования. Восстановленный файл создаётся отдельно из снимка. До опыта остановим учебные API и CLI, закроем соединения и убедимся, что другие процессы не меняют файлы.</p>
        <Diagram caption="Три роли в учебном опыте" orientation="horizontal">
          <DiagramFlow label="Создание снимка и восстановление" orientation="horizontal">
            <DiagramNode title="Исходная база" shape="database">Учебные задачи</DiagramNode>
            <DiagramArrow label="создаём снимок" />
            <DiagramNode title="Снимок" shape="database">Резерв для восстановления</DiagramNode>
            <DiagramArrow label="восстанавливаем отдельно" />
            <DiagramNode title="Новая копия" shape="database">Сравниваем с описью</DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>Направления показывают происхождение файлов. При восстановлении мы читаем снимок и создаём третью базу; исходную базу не назначаем целью.</p>
        <p>Работаем из папки <code>project/</code> изолированной учебной копии. Назовём абсолютные пути всех трёх файлов. Исходник и снимок должны существовать, а путь восстановления должен быть новым и отличаться от первых двух. Если снимок остался после ошибки или его успешное создание не подтверждено, вернёмся к резервированию.</p>
        <p>Сначала разберём фрагмент для состояния, которое получим при подготовке примера: исходник заметок и их снимок существуют, а файл восстановления ещё не создан. В следующем разделе мы получим эти два файла.</p>
        <CodeBlock caption="Как сравнить пути в Python" code={'from pathlib import Path\nsource = Path("demo_restore/notes.sqlite3").resolve()\nsnapshot = Path("demo_restore/notes-snapshot.sqlite3").resolve()\nrestored = Path("demo_restore/notes-restored.sqlite3").resolve()\nprint(source.is_file(), snapshot.is_file(), restored.exists())\nprint(source != snapshot and snapshot != restored and source != restored)'} />
        <p><code>Path</code> описывает путь, <code>resolve()</code> делает его абсолютным; <code>is_file()</code> проверяет существующий файл, <code>exists()</code> обнаруживает занятое имя. На заранее подготовленных первых двух файлах и свободном третьем получим <code>True True False</code>, затем <code>True</code>. Эти предварительные проверки не защищают от чужого процесса, который меняет каталог одновременно, поэтому опыт изолирован.</p>
        <p>Если цель совпадает со снимком, сравнение путей даст <code>False</code>. Тогда мы останавливаемся до записи, даже если файл открывается.</p>
      </Section>
      <Section number="02" title="Восстановление как отдельное копирование SQLite">
        <Lead>Для восстановления соединение со снимком становится источником, а соединение с новым файлом принимает его данные.</Lead>
        <p><code>sqlite3.connect(path)</code> открывает базу и может создать отсутствующий файл. Поэтому сначала проверяем источник. Метод соединения с источником копирует базу в соединение назначения:</p>
        <CodeBlock caption="Копирование между соединениями" code="source_db.backup(destination_db)" />
        <p>Этот метод не защищает занятый путь и не возвращает код успеха: успешное завершение без исключения ещё нужно проверить чтением.</p>
        <p><code>Path.open("xb")</code> создаёт новый файл для записи байтов и сообщает <code>FileExistsError</code>, если имя уже занято. <code>with</code> закрывает файловый объект; SQLite-соединения закрываем явно через <code>close()</code> в <code>finally</code>. Если ошибка случилась после создания цели, считаем её частичной и не принимаем. Существующий чужой файл не удаляем и не перезаписываем.</p>
        <p>Начнём с маленькой базы заметок, чтобы видеть каждую сторону копирования. Сохраним следующий подготовительный код в <code>project/prepare_notes.py</code>. Он создаёт две заметки и отдельный снимок. Каталог <code>demo_restore</code> должен быть новым: если любое из имён занято, исключительное создание остановит программу без перезаписи. Не используем частичный результат после ошибки.</p>
        <CodeBlock caption="Подготовка двух заметок и снимка" code={prepareExample} />
        <p>Из <code>project/</code> выполним <code>.\.venv\Scripts\python.exe prepare_notes.py</code>. Сообщение <code>Снимок заметок создан</code> означает, что два соединения закрылись после вызова <code>backup</code>. <code>commit()</code> сохраняет две строки до копирования. Это подготовка входа, а не доказательство полноты снимка.</p>
        <p>Теперь источник операции изменится: будем читать именно снимок. Сохраним отдельный файл <code>project/restore_notes.py</code>. Он откажет при неизвестном источнике или совпадении адресов, зарезервирует новое имя и передаст соединение назначения методу источника.</p>
        <CodeBlock caption="Восстанавливаем снимок в новое имя" code={restoreExample} />
        <p>Из <code>project/</code> запустим <code>.\.venv\Scripts\python.exe restore_notes.py</code>. Сообщение <code>Копирование завершилось без ошибки</code> не подтверждает содержимое: чуть ниже отдельно откроем новый файл и прочитаем заметки. Если повторить команду с тем же назначением, получим <code>FileExistsError</code>, потому что имя уже занято. При ошибке после резервирования новый файл считаем частичным и не используем его как успешное восстановление.</p>
        <p>Отдельно проследим, почему занятое имя не перезаписывается. Проверим, что в <code>project/</code> свободно имя <code>check_restore_occupied.py</code>, и сохраним пример в этот новый файл. Прежний <code>check_occupied.py</code>, если мы его создавали, оставим без изменений. Если новое имя уже занято, выберем другое свободное имя и используем его в команде запуска. Файл <code>demo_restore/occupied.bin</code> до запуска тоже должен отсутствовать.</p>
        <CodeBlock caption="Проверка занятого имени" code={occupiedExample} />
        <p>Команда <code>.\.venv\Scripts\python.exe check_restore_occupied.py</code> выводит <code>Занято, байты сохранены: True</code>. Первый <code>open("xb")</code> создаёт контрольный файл, второй получает отказ. <code>read_bytes()</code> читает байты до и после попытки: совпадение подтверждает сохранность именно этого файла. Если имя уже было занято до опыта, первый вызов тоже остановится и не перезапишет его.</p>
        <p>В учебном Planner модуль резервирования создаётся при подготовке снимка. Его согласованная команда из <code>project/</code>: <code>.\.venv\Scripts\python.exe -m app.DBase.backup_snapshot SOURCE DEST</code>. <code>-m</code> запускает модуль, далее идут два пути, а не URL и не флаги. Для восстановления на место <code>SOURCE</code> ставим путь снимка, на место <code>DEST</code> свободное имя. Путь с пробелом заключаем в кавычки.</p>
        <p>Например, при наличии готового модуля выполним <code>.\.venv\Scripts\python.exe -m app.DBase.backup_snapshot demo_restore/notes-snapshot.sqlite3 demo_restore/notes-command-copy.sqlite3</code>. Откроем новую копию для чтения способом ниже и увидим те же две заметки. Если назначение уже занято, получим отказ и исходные байты занятого файла сохранятся. На другом синтетическом снимке попробуем выбрать свои два пути и объяснить, какой из них источник. Перед практикой обязательно проверим, что созданный модуль действительно поддерживает эту команду и защиту пути.</p>
      </Section>
      <Section number="03" title="Техническая целостность">
        <Lead>Даже открываемый файл нужно проверить как базу SQLite.</Lead>
        <p>Откроем отдельную восстановленную копию только для чтения. <code>as_uri()</code> превращает абсолютный путь в файловый URI, <code>?mode=ro</code> задаёт чтение без записи, а <code>uri=True</code> сообщает Python, как понимать этот адрес. Несуществующий файл при таком открытии не создаётся.</p>
        <p>Из <code>project/</code> запустим <code>.\.venv\Scripts\python.exe</code> и введём команды в приглашении Python. Они используют файл заметок, созданный примером выше.</p>
        <CodeBlock caption="Проверяем отдельную копию" code={'from pathlib import Path\nimport sqlite3\npath = Path("demo_restore/notes-restored.sqlite3")\ndb = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)\ntry:\n    print(db.execute("PRAGMA integrity_check;").fetchall())\n    print(db.execute("PRAGMA foreign_key_check;").fetchall())\n    print(db.execute("SELECT id, body FROM notes ORDER BY id;").fetchall())\nfinally:\n    db.close()'} />
        <p>В интерактивном Python после блока <code>finally</code> нужна пустая строка. Получим <code>[('ok',)]</code>, <code>[]</code> и <code>[(1, 'Карта'), (3, 'Ключ')]</code>. <code>execute</code> выполняет SQL, <code>fetchall()</code> забирает все строки ответа. Первое означает, что проверка структуры не нашла ошибок, второе показывает отсутствие обнаруженных нарушений внешних ключей. Третий результат содержит оба ID и тексты заметок, которые мы записали перед снимком. <code>integrity_check</code> сам внешние ключи не проверяет. Закончив, введём <code>exit()</code>.</p>
        <p>Файл с произвольным текстом может вызвать ошибку при запросе, даже если открытие соединения не сообщило её сразу. При ошибке открытия или чтения копию не принимаем. Если видим <code>ok</code>, данные ещё предстоит сравнить с описью.</p>
        <QuizCard question="Если integrity_check вернул ok, все ожидаемые строки обязательно на месте." options={["Правда", "Ложь"]} correctIndex={1} explanation="Проверка структуры не знает, какие строки мы ожидали. Корректная SQLite может содержать только часть данных." />
      </Section>
      <Section number="04" title="Сравниваем схему и ограничения">
        <Lead>Имя таблицы не гарантирует, что её поля и ограничения совпадают с исходной описью.</Lead>
        <p>Откроем уже восстановленные заметки для чтения. Сохраним этот самостоятельный пример в <code>project/read_notes_schema.py</code> и запустим из <code>project/</code> командой <code>.\.venv\Scripts\python.exe read_notes_schema.py</code>.</p>
        <CodeBlock caption="Читаем схему восстановленных заметок" code={schemaExample} />
        <p>В строке <code>Объекты</code> увидим запись таблицы <code>notes</code> и её SQL с условием <code>CHECK (length(body) &gt; 0)</code>. В строке <code>Столбцы</code> среди результатов будет <code>(1, 'body', 'TEXT', 1, None, 0)</code>: это второй столбец <code>body</code>, тип <code>TEXT</code>, значение <code>1</code> означает запрет NULL, SQL-default отсутствует, а последний <code>0</code> показывает, что столбец не входит в первичный ключ. Для индексов и внешних ключей получим пустые списки <code>[]</code>: в этом образце их нет.</p>
        <p><code>sqlite_schema</code> перечисляет таблицы, индексы и другие объекты вместе с их SQL. У автоматически созданного индекса текст SQL может быть <code>None</code>. В <code>table_info</code> видим имя, тип, обязательность, SQL-default и признак первичного ключа. Каждый запрос передаётся в кавычках в <code>execute</code>, а <code>fetchall()</code> возвращает его строки.</p>
        <p>В записи таблицы <code>notes</code> найдём <code>CHECK (length(body) &gt; 0)</code>. В другом образце с таким же именем таблицы, но без этого условия, таблица открывается, однако схема отличается. Имена индексов берём из результата и для каждого читаем его столбцы. Сравниваем также представления и триггеры, если они есть в описи. Значение по умолчанию в Python-модели и SQL-default не обязательно одно и то же.</p>
        <p>Проверим обход индексов на отдельном образце. Сохраним код в <code>project/index_example.py</code> и выполним <code>.\.venv\Scripts\python.exe index_example.py</code> из <code>project/</code>. Файл <code>demo_restore/index-example.sqlite3</code> должен отсутствовать: режим <code>xb</code> не перезапишет занятое имя.</p>
        <CodeBlock caption="Таблица с индексом и таблица без индексов" code={indexExample} />
        <p>Увидим <code>notes [('idx_notes_body', 0, 'c', 0)]</code>, затем <code>idx_notes_body [('body',)]</code> и <code>labels []</code>. В первом кортеже после имени идут признаки уникальности и частичного индекса: оба равны <code>0</code>, значит этот индекс не уникальный и охватывает всю таблицу. Буква <code>c</code> означает, что он создан командой <code>CREATE INDEX</code>. Вторая выборка возвращает названия столбцов по порядку. <code>pragma_index_list(?)</code> и <code>pragma_index_info(?)</code> позволяют передать имя параметром: знак вопроса получает значение из одноэлементного кортежа, например <code>(table,)</code>. <code>row[0]</code> берёт имя из строки результата; цикл по пустому списку не выполняется. <code>finally</code> закрывает соединение и при ошибке. На восстановленном Planner используем тот же приём для <code>tasks</code> и сравниваем каждый найденный индекс с описью.</p>
        <p>Для Planner сравним фактические поля, типы, ключи, nullable, defaults, CHECK и найденные объекты с описью. Буквальное сравнение текста SQL не является общим доказательством эквивалентности любых схем.</p>
      </Section>
      <Section number="05" title="Считаем и сравниваем строки">
        <Lead>Количество строк помогает обнаружить потерю, но одинаковое количество ещё не означает одинаковое содержимое.</Lead>
        <p>Наша опись заметок содержит две строки: ID 1 с текстом «Карта» и ID 3 с текстом «Ключ». Сравним с ней восстановленную копию: сохраним код в <code>project/read_notes.py</code> и запустим из <code>project/</code> командой <code>.\.venv\Scripts\python.exe read_notes.py</code>.</p>
        <CodeBlock caption="Число и содержимое заметок" code={rowsExample} />
        <p>Получим <code>Количество: [(2,)]</code> и <code>Заметки: [(1, 'Карта'), (3, 'Ключ')]</code>. Первый список содержит одну строку результата подсчёта: число 2. Во втором каждая пара означает ID и текст одной заметки. <code>COUNT(*)</code> считает строки, а <code>ORDER BY id</code> задаёт порядок сравнения. Обе пары совпадают с нашей описью.</p>
        <p>Если вместо «Ключ» сохранилось «Замок», счётчик останется равным двум. Сопоставление по ID обнаружит изменённый текст. Если одна строка потеряна, отличается и количество. Для учебного Planner сверяем каждую строку и все поля: <code>id</code>, <code>title</code>, <code>priority</code>, <code>is_done</code>, <code>tags</code>, <code>title_normalized</code>. Внутренние tags и нормализованное название нельзя проверить по публичному выводу CLI.</p>
        <p>Потренируемся на другой паре заметок. В описи два ID: 2 и 8. В копии тоже две записи, но их ID равны 2 и 9. Как обнаружить причину расхождения, если мы ещё не знаем, какие именно ID различаются?</p>
        <CodeSequence title="Исследуем совпадение количества" prompt="Соберём ход сравнения двух наборов, а затем проверим порядок." pieces={[
          { id: "count", code: "Сравнить число записей в описи и копии" },
          { id: "ids", code: "Сопоставить сами ID из обоих наборов" },
          { id: "result", code: "Назвать отсутствующий и лишний ID" },
        ]} correctOrder={["count", "ids", "result"]} explanation="Число 2 совпало, но после сравнения видно: ID 8 отсутствует, а ID 9 появился вместо него. Одного счётчика недостаточно." incorrectExplanation="Вернёмся к исходным данным: сначала выясняем число записей, затем сопоставляем ID и только после этого называем различие." />
      </Section>
      <Section number="06" title="Читаем копию обычным приложением">
        <Lead>SQL-сравнение проверяет данные, а отдельный запуск Planner показывает, что обычная сборка может их прочитать.</Lead>
        <p>Настройка <code>DATABASE_URL</code> задаёт приложению адрес. В этом проекте относительный путь SQLite разрешается относительно <code>project/</code>; переменная окружения имеет приоритет над <code>.env</code>. Перед запуском проверим, что адрес обозначает нужный существующий учебный файл, а не исходник и не снимок.</p>
        <p>Сначала рассмотрим отдельный маленький читатель заметок. После примера восстановления сохраним код в <code>project/demo_reader.py</code>. Он принимает только показанный учебный URL и открывает уже существующую копию. Проверка точной строки здесь нужна для ограниченного примера, не является общим правилом для всех адресов.</p>
        <CodeBlock caption="Независимое чтение заметок" code={readerExample} />
        <p><code>os.environ.get</code> читает настройку окружения; если файла нет или адрес другой, программа останавливается до подключения. <code>create_engine</code> подготавливает SQLAlchemy к работе с адресом, <code>connect()</code> открывает соединение, а <code>text()</code> обозначает SQL-запрос. <code>with</code> закрывает соединение, <code>dispose()</code> освобождает ресурсы engine.</p>
        <p>В PowerShell из <code>project/</code> зададим адрес и запустим читатель:</p>
        <CodeBlock caption="Запускаем читатель заметок" code={String.raw`$env:DATABASE_URL = 'sqlite:///./demo_restore/notes-restored.sqlite3'
.\.venv\Scripts\python.exe -m demo_reader`} />
        <p>Получим <code>[(1, 'Карта'), (3, 'Ключ')]</code>. Если вместо адреса восстановленной копии указать имя исходника, увидим ошибку проверки до подключения. После этого вернём правильный адрес; это проверка выбора пути на другом наборе данных, не готовое решение для Planner.</p>
        <p>Проверим интерпретатор:</p>
        <CodeBlock caption="Проверяем интерпретатор" code={String.raw`.\.venv\Scripts\python.exe --version`} />
        <p>Если среда отсутствует, сначала восстановим её по README проекта. URL заметок всё ещё действует в этом терминале. Для Planner выберем уже восстановленный учебный файл и подставим его путь вместо примера ниже; если такой копии пока нет, вернёмся к этому запуску после её восстановления. По описи убедимся, что выбранная копия отличается от исходника и снимка. В PowerShell из <code>project/</code> заменим URL и проверим, к какому существующему файлу он разрешается:</p>
        <CodeBlock caption="Переключаем адрес на восстановленный Planner" code={String.raw`$restored = (Resolve-Path -LiteralPath 'data/restored-check.sqlite3').Path
$env:DATABASE_URL = 'sqlite:///' + $restored.Replace('\', '/')
$env:DATABASE_URL
.\.venv\Scripts\python.exe -c "import os; from pathlib import Path; from sqlalchemy.engine import make_url; p = Path(make_url(os.environ['DATABASE_URL']).database).resolve(); print(p, p.is_file())"`} />
        <p>Напечатанный абсолютный путь должен совпасть с выбранной восстановленной копией, а проверка показать <code>True</code>. При несовпадении, отсутствии файла или ошибке остановимся до запуска. Теперь запустим приложение:</p>
        <CodeBlock caption="Запускаем Planner" code={String.raw`.\.venv\Scripts\python.exe -m app.main`} />
        <p>В меню введём <code>list</code>, затем <code>find</code> и известный ID, после проверки <code>exit</code>. Эти команды читают задачи. Новый процесс использует выбранный URL; видимые поля сопоставляем с описью, а внутренние поля проверяем SQL-запросом.</p>
        <p>После проверки закроем учебный терминал, чтобы настройка не перешла в следующий запуск. Если путь неизвестен или файл отсутствует, приложение не запускаем до исправления адреса.</p>
      </Section>
      <Section number="07" title="Когда восстановление можно принять">
        <Lead>Соединим свидетельства: техническую проверку, схему, содержимое и обычное чтение.</Lead>
        <p>Пусть опись заметок содержит ID 1 и 3. В первом образце есть обе строки; во втором только ID 1. Оба файла могут вернуть <code>ok</code>, но второй не совпадает с описью: количество и ID показывают пропажу. Ошибка открытия тоже означает отказ, а не совпадение.</p>
        <p>Если отдельный неполный учебный образец не передан, его можно подготовить самостоятельно. Сначала из <code>project/</code> создадим новую копию снимка заметок командой <code>.\.venv\Scripts\python.exe -m app.DBase.backup_snapshot demo_restore/notes-snapshot.sqlite3 demo_restore/notes-incomplete.sqlite3</code>. Имя назначения должно быть свободным; при отказе выбираем другое новое имя. Только затем откроем именно новую копию и удалим из неё одну известную строку:</p>
        <CodeBlock caption="Отдельный неполный образец заметок" code={'from pathlib import Path\nimport sqlite3\npath = Path("demo_restore/notes-incomplete.sqlite3").resolve()\nif not path.is_file():\n    raise ValueError("Нет отдельной копии")\ndb = sqlite3.connect(path)\ntry:\n    db.execute("DELETE FROM notes WHERE id = ?", (3,))\n    db.commit()\n    print(db.execute("PRAGMA integrity_check;").fetchall())\n    print(db.execute("SELECT id, body FROM notes ORDER BY id;").fetchall())\nfinally:\n    db.close()'} />
        <p>Эти команды вводим в Python из <code>project/</code> после успешного создания отдельной копии. Если выбрали другое свободное имя, подставим именно его в <code>path</code> до открытия: примерное имя не должно указывать на старый файл. <code>DELETE</code> удаляет строку по условию <code>WHERE</code>; знак <code>?</code> получает значение из кортежа <code>(3,)</code>, а запятая делает его кортежем из одного элемента. <code>commit()</code> сохраняет изменение именно в этой копии. Получим <code>[('ok',)]</code> и <code>[(1, 'Карта')]</code>: строка с ID 3 отсутствует. Исходную базу и снимок для изменения не открываем. При ошибке или занятом имени не выполняем удаление и сначала уточняем путь.</p>
        <p>Принимаем восстановление, когда файл открылся, техническая проверка прошла, схема и все данные совпали, а обычное приложение прочитало копию по явному адресу. Отсутствующее свидетельство означает, что проверка ещё не завершена.</p>
        <Callout tone="success">Снимок остаётся резервом. Сравнение и эксперименты выполняем на отдельной восстановленной копии.</Callout>
      </Section>
      <Section number="08" title="Что мы будем делать в практике">
        <Lead>Возьмём опись и успешно созданный учебный снимок. Каждая карточка даст отдельный результат, который понадобится следующей.</Lead>
        <LinkedNotes items={[
          { title: "Восстанавливаем", description: "Проверяем три пути и получаем новую копию, сохраняя исходник и снимок." },
          { title: "Сверяем схему", description: "Проверяем целостность, поля и ограничения по описи." },
          { title: "Сверяем данные", description: "Сопоставляем все поля задач и читаем выбранную запись через CLI." },
          { title: "Ищем потерю", description: "На неполном образце обнаруживаем несовпадение и объясняем отказ." },
        ]} />
        <p>Вместе эти наблюдения показывают, что копия открывается, соответствует описи и читается приложением. Если один шаг не проходит, возвращаемся к его объяснению и не принимаем файл по одному сообщению об успехе.</p>
      </Section>
    </RichLesson>
  );
}
