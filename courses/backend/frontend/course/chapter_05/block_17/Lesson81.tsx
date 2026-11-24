import { RichLesson, RichHero, Section, Lead, LinkedNotes, Callout, CodeBlock, QuizCard, Diagram, DiagramFlow, DiagramNode, DiagramArrow, DiagramNote } from "@course-ui";

const prepareBooks = String.raw`import sqlite3
from contextlib import closing
from pathlib import Path

def prepare_books() -> Path:
    source = Path("data/backup-work/books-source.sqlite3").resolve()
    with source.open("xb"):
        pass
    with closing(sqlite3.connect(source)) as connection:
        with connection:
            connection.execute("""
                CREATE TABLE books (
                    id INTEGER PRIMARY KEY NOT NULL CHECK(id > 0),
                    title TEXT NOT NULL,
                    shelf TEXT NOT NULL DEFAULT 'reading'
                )
            """)
            connection.execute(
                "INSERT INTO books (id, title) VALUES (?, ?)",
                (4, "Лес"),
            )
            connection.execute(
                "INSERT INTO books (id, title, shelf) VALUES (?, ?, ?)",
                (9, "Море", "archive"),
            )
    return source

if __name__ == "__main__":
    created = prepare_books()
    print(created.name)`;

const readBooks = String.raw`import sqlite3
from contextlib import closing
from pathlib import Path

source = Path("data/backup-work/books-source.sqlite3").resolve(strict=True)
if not source.is_file():
    raise ValueError("Источник должен быть файлом")
with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as connection:
    queries = [
        "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name",
        "PRAGMA table_info(books)",
        "PRAGMA index_list(books)",
        "PRAGMA foreign_key_list(books)",
        "SELECT id, title, shelf FROM books ORDER BY id",
    ]
    for query in queries:
        print(query)
        print(connection.execute(query).fetchall())`;

const backupBooks = String.raw`import sqlite3
from contextlib import closing
from pathlib import Path

source = Path("data/backup-work/books-source.sqlite3").resolve(strict=True)
destination = Path("data/backup-work/books-reserve.sqlite3").resolve()
if not source.is_file():
    raise ValueError("Источник должен быть файлом")
if source == destination:
    raise ValueError("Источник и назначение совпали")
if not destination.parent.is_dir():
    raise ValueError("Каталог назначения не существует")

with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as source_connection:
    source_connection.execute("SELECT name FROM sqlite_schema").fetchall()
    with destination.open("xb"):
        pass
    with closing(sqlite3.connect(destination)) as target_connection:
        source_connection.backup(target_connection)

with closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as check:
    print(check.execute("SELECT id, title, shelf FROM books ORDER BY id").fetchall())
print("Резерв открыт", destination.name)`;

const plannerFields = String.raw`def prepare_planner_fields(first_id: int, second_id: int) -> None:
    if first_id == second_id:
        raise ValueError("Нужны два разных ID")
    source = Path("data/backup-work/planner-source.sqlite3").resolve(strict=True)
    with closing(sqlite3.connect(source.as_uri() + "?mode=rw", uri=True)) as connection:
        with connection:
            first = connection.execute(
                "UPDATE tasks SET tags = ? WHERE id = ?",
                ('["учёба","резерв"]', first_id),
            )
            second = connection.execute(
                "UPDATE tasks SET tags = ?, is_done = ? WHERE id = ?",
                ('["перенос"]', 1, second_id),
            )
            if first.rowcount != 1 or second.rowcount != 1:
                raise ValueError("Нужны два существующих ID")`;

export function Lesson81() {
  return <RichLesson>
    <RichHero chip="Миграции базы" title="Резервная копия" intro="Проверим адрес SQL Planner, опишем исходное состояние и подготовим собственную операцию резервирования до первого изменения схемы." />
    <Section number="00" title="Сначала сохраняем возможность вернуться">
      <Lead>Работающая программа ещё не даёт способа вернуть данные после неудачного изменения. Начнём с понятного источника и отдельного места для резерва.</Lead>
      <LinkedNotes items={[
        { title: "Точка старта", description: "Есть SQL Planner с API и CLI. Сначала проверим его запуск и файл общей учебной SQLite." },
        { title: "Новый вопрос", description: "Как получить согласованный снимок, сохранив источник и уже существующие файлы?" },
        { title: "Результат", description: "Напишем свою операцию backup, сохраним открываемый резерв и исходную опись для восстановления." },
      ]} />
      <p>Адрес объясняет, что сохраняем. Опись фиксирует, что должно остаться. Защищённое новое назначение позволяет получить резерв без замены прежнего файла.</p>
    </Section>
    <Section number="01" title="Выбираем учебную базу и проверяем общий вход">
      <Lead>Сначала выясним, какой файл используют процессы. Успешный /health не читает задачи и сам по себе не доказывает адрес SQLite.</Lead>
      <p>Работаем из корня существующего Planner: папки project/ с app/ и tests/. Используем один Python из .venv для теста, API и CLI. Флаг -m запускает модуль по Python-имени. Команды PowerShell вводим в терминале; list и exit вводятся внутри меню Planner.</p>
      <p>Для короткой пробы сохраним в project/backup_lab.py строку <code>print("Книжный опыт запущен")</code>. Из project/ выполним <code>.venv/Scripts/python.exe backup_lab.py</code>. Получим эту строку. Предскажем эффект запуска из другой папки: относительное имя может не найтись. Исправляем место запуска, затем повторяем.</p>
      <p>В project/data создадим один новый свободный каталог backup-work. Если имя уже занято, выберем другое и одинаково заменим его во всех путях. Старый каталог не очищаем. planner-source.sqlite3 внутри нового каталога пока отсутствует; обычный data/planner.sqlite3 сохраняем.</p>
      <p>После главы 4 v2 запустим знакомый тест из занятия 76. API и CLI должны найти одну и ту же задачу во временной SQLite.</p>
      <CodeBlock caption="PowerShell из project/: проверка для главы 4 v2" code=".venv/Scripts/python.exe -m pytest -q tests/test_storage_availability.py::test_smoke_api_and_cli_read_the_same_task" />
      <details>
        <summary>Если у нас проект главы 4 v1</summary>
        <CodeBlock caption="PowerShell из project/: проверка для главы 4 v1" code=".venv/Scripts/python.exe -m pytest -q tests/test_sql_runtime.py::test_cli_main_uses_configured_sql_database_not_json" />
      </details>
      <p>Успешный тест подтверждает запуск с изолированной SQLite. Адрес для нашего источника зададим и проверим отдельно.</p>
      <CodeBlock caption="PowerShell из project/: явный URL и новая учебная схема" code={String.raw`$env:DATABASE_URL = "sqlite:///./data/backup-work/planner-source.sqlite3"
.venv/Scripts/python.exe -c "from app.core.config import load_settings; print(load_settings().database_url)"
.venv/Scripts/python.exe -m app.DBase.init_db`} />
      <p>Переменная $env:DATABASE_URL действует в этом терминале и наследуется его процессами. Во втором терминале задаём её ещё раз. Флаг -c выполняет переданный текст Python: импортируем load_settings, вызываем её, читаем database_url у результата Settings и печатаем значение.</p>
      <p>URL должен указывать на наш полный адрес planner-source.sqlite3. Настройки разрешают относительный путь от project/. sqlite:/// является URL SQLAlchemy; sqlite3 позже получит Path или file: URI. URL не превращаем в путь срезом строки. init_db здесь намеренно создаёт новую учебную схему. При backup источник уже должен существовать.</p>
      <Callout>Если файл теста не найден, сначала сверим версию Planner и путь к тесту. Если не запускается Python или отсутствует пакет, сверим окружение с README проекта. При несовпадении URL не резервируем неизвестный файл.</Callout>
      <CodeBlock caption="Каждая команда в своём терминале с одинаковым учебным URL" code={String.raw`.venv/Scripts/python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8000
.venv/Scripts/python.exe -m app.main`} />
      <p>Откроем http://127.0.0.1:8000/docs, раскроем POST /tasks, нажмём Try it out и зададим title/priority в теле. Создадим «Сверить резерв», priority 2, затем «Проверить перенос», priority 4. После Execute успешный ответ имеет статус 201 и выданный id. Сохраним оба разных ID в data/backup-work/observations.md; их не назначаем заново.</p>
      <p>В CLI введём list: видны те же две задачи. Через exit завершим процесс, запустим CLI ещё раз и повторим list. Новое чтение подтверждает сохранённые данные. При расхождении сверяем настройки обоих терминалов и исправляем причину до продолжения; /health=200 её не снимает.</p>
      <p>Завершим CLI через exit, API через Ctrl+C и закроем редактор БД, если он открывал файл. В observations.md запишем полный адрес, URL, ID и результат диагностики. Весь CRUD заново не пишем и полную регрессионную главу не повторяем.</p>
    </Section>
    <Section number="02" title="Открываем существующий файл и закрываем соединение">
      <Lead>Обычный sqlite3.connect может создать отсутствующую базу. Для сохранения существующих данных опечатка должна закончиться отказом.</Lead>
      <p>Path из pathlib описывает адрес. Оператор / соединяет части пути. resolve делает его абсолютным, разрешает .. и символические ссылки; strict=True требует существования. is_file отличает файл от каталога. Эти признаки ещё не доказывают наличие нужной таблицы.</p>
      <p>Сначала создадим независимый реестр книг в одном файле и с двумя строками. Заменим backup_lab.py полным кодом ниже и запустим <code>.venv/Scripts/python.exe backup_lab.py</code> из project/. Каталог data/backup-work уже существует, books-source.sqlite3 ещё нет.</p>
      <p>open("xb") исключительно создаёт новый двоичный файл. При занятом имени FileExistsError сохраняет прежний файл. with закрывает файловый дескриптор, pass не записывает байты. Затем SQLite заполняет выделенный файл. closing из contextlib вызывает close при выходе, включая исключение. Вложенный with connection фиксирует транзакцию при успехе или откатывает её при ошибке; сам соединение не закрывает.</p>
      <CodeBlock caption="backup_lab.py: один раз создаём книжный источник" code={prepareBooks} />
      <p>CREATE TABLE задаёт структуру. INTEGER и TEXT являются типами; PRIMARY KEY выделяет ключ; NOT NULL требует значение; CHECK ограничивает ID. В INSERT список колонок соответствует значениям. Знак ? получает значение из кортежа параметров, SQL вручную не склеиваем. Первая запись не передаёт shelf: SQL DEFAULT подставит reading. Явное archive второй записи сохраняется.</p>
      <p>def объявляет функцию. Аннотация -&gt; Path описывает возвращаемый адрес. return передаёт source после закрытия ресурсов, created.name берёт имя файла. Вывод: <code>books-source.sqlite3</code>. Проверка __name__ == "__main__" выполняет нижний вызов при запуске файла, а при импорте оставляет только определения функций. Так можно написать вызываемое средство, не запуская его случайно при импорте.</p>
      <p>Повтор подготовки должен отказать на занятом имени. Теперь заменим backup_lab.py блоком открытия ниже: в нём создания схемы уже нет.</p>
      <CodeBlock caption="Полный блок открытия существующего источника" code={String.raw`import sqlite3
from contextlib import closing
from pathlib import Path

source = Path("data/backup-work/books-source.sqlite3").resolve(strict=True)
if not source.is_file():
    raise ValueError("Источник должен быть файлом")
source_uri = source.as_uri() + "?mode=ro"
with closing(sqlite3.connect(source_uri, uri=True)) as connection:
    print("Открыт источник", source.name)`} />
      <p>as_uri кодирует абсолютный адрес как file: URI, включая пробелы. mode=ro означает read-only; uri=True включает разбор параметра SQLite. Вывод: <code>Открыт источник books-source.sqlite3</code>. После with соединение закрыто. Контекст with sqlite3.connect сам по себе его не закрывал бы: он управляет транзакцией.</p>
      <p>Сначала предскажем результат, затем заменим имя на books-missing.sqlite3 и запустим блок открытия. Ожидаем отказ и проверяем отсутствие нового файла. Теперь вместо источника выберем существующий data/backup-work: is_file остановит попытку. Вернём books-source. Подготовку книг повторно не вызываем.</p>
    </Section>
    <Section number="03" title="Снимаем опись схемы и всех значений">
      <Lead>Опись сохраняет состояние до операции. Позже сравним его с результатом, не восстанавливая исходные данные по памяти.</Lead>
      <p>sqlite_schema хранит описание таблиц, индексов, представлений и триггеров. execute выполняет SQL, fetchall забирает все строки результата как список кортежей. Порядок значений задают колонки SELECT; ORDER BY id делает порядок строк явным. PRAGMA читает свойства SQLite.</p>
      <CodeBlock caption="Полный reader в backup_lab.py, запуск тем же Python из project/" code={readBooks} />
      <p>Последний вывод: <code>[(4, 'Лес', 'reading'), (9, 'Море', 'archive')]</code>. Первую строку читаем по id/title/shelf. У книги 4 полка reading, хотя INSERT её не передавал: сработал SQL-default.</p>
      <p>В table_info строка title имеет вид <code>(1, 'title', 'TEXT', 1, None, 0)</code>. Значения: cid=1, имя title, тип TEXT, notnull=1, SQL-default=None, pk=0. Четвёртое значение подтверждает явный NOT NULL, None показывает отсутствие SQL-default, 0 отсутствие участия в ключе. У shelf default содержит 'reading'. У id pk=1, а явный NOT NULL в DDL даёт notnull=1.</p>
      <p>CHECK ищем в CREATE SQL из sqlite_schema, не в table_info. index_list и foreign_key_list здесь пусты; отдельный индекс для INTEGER PRIMARY KEY не нужен. Это не означает отсутствие книг. Неожиданный объект сначала выясняем, не исправляем файл вслепую.</p>
      <p>Попробуем сами расшифровать shelf: какое значение показывает default, а какое обязательность? Сравним с title. Это разные свойства: default не заменяет NOT NULL.</p>
      <p>У TaskRow из app/DBase/models.py шесть колонок: id, title, priority, is_done, tags, title_normalized. Все обязательны; id положителен, priority ограничен 1–5. Внутренние tags и title_normalized не видны в TaskRead/CLI, но входят в опись. Python-default=2/False/list подставляется SQLAlchemy при записи и не объявляет SQL DEFAULT в DDL. Поэтому dflt_value=None не является ошибкой само по себе.</p>
      <p>Для контрольных внутренних значений добавим в backup_lab.py функцию ниже с уже разобранными импортами sqlite3, closing и Path. Это готовая подготовка двух задач, а не решение backup. В нижнем main-вызове оставляем только <code>prepare_planner_fields(first_id, second_id)</code>, подставив два ID из API. prepare_books больше не вызываем.</p>
      <CodeBlock caption="Функция подготовки уже существующей учебной tasks" code={plannerFields} />
      <p>mode=rw разрешает запись только в существующий файл. Это явный режим подготовки до описи; backup читает ro. Каждый WHERE выбирает один сохранённый ID. rowcount показывает число затронутых строк: ждём 1 у каждого UPDATE. При расхождении исключение откатывает обе записи через with connection; при успехе записи фиксируются, closing закрывает соединение.</p>
      <p>tags получают показанные синтетические JSON-строки; is_done второй задачи становится 1, первой остаётся 0. title_normalized, созданный сервисом, не меняем. Теперь сами запишем чтение всех шести колонок tasks с ORDER BY id и запросы schema/PRAGMA по книжному образцу. Сохраним полный вывод, оба ID и каждое значение в observations.md. COUNT(*)=2 не доказывает сохранность всех полей.</p>
      <p>Это точка фиксации: подготовка завершена, соединения закрыты, исходная schema/data-опись сохранена. Дальше источник не записываем и опись не подгоняем под результат.</p>
    </Section>
    <Section number="04" title="Получаем согласованный снимок по новому адресу">
      <Lead>Согласованное состояние читаем средствами SQLite. Копирование только основного файла может оставить подтверждённые изменения вне копии.</Lead>
      <p>В режиме WAL подтверждённые страницы могут находиться в файле с окончанием -wal. SQLite учитывает основную БД и журнал при чтении. WAL вручную не удаляем. Перед описью и backup завершаем записи, останавливаем API/CLI/редактор БД и закрываем их соединения. Так получаем фиксированную точку сравнения. Наличие WAL у нашего файла не предполагаем автоматически.</p>
      <Diagram caption="Путь логического состояния SQLite в новый резерв" orientation="vertical">
        <DiagramFlow label="Два соединения и новое назначение" orientation="vertical">
          <DiagramNode title="books-source + возможный WAL" shape="database"><p>Две книги, писатели остановлены. Подтверждённые страницы доступны SQLite.</p></DiagramNode>
          <DiagramArrow label="ro-соединение читает основную БД и журнал" />
          <DiagramNode title="SQLite backup" shape="subprocess"><p>Соединение источника передаёт состояние соединению назначения.</p></DiagramNode>
          <DiagramArrow label="Пишем только в новое выделенное назначение" />
          <DiagramNode title="books-reserve" shape="database"><p>Имя выделено xb; файловый дескриптор закрыт до target-соединения. После backup закрываются оба соединения.</p></DiagramNode>
        </DiagramFlow>
        <DiagramNote title="Будущая рабочая копия пока отсутствует"><p>Её адрес отличается от source и reserve. Сейчас его не открываем. Копирование одного основного файла могло бы пропустить страницы WAL.</p></DiagramNote>
      </Diagram>
      <p>Backup API умеет работать с другими клиентами, но здесь online-режим не исследуем. Активный писатель нарушает наш учебный протокол, поэтому сначала завершаем остановку.</p>
      <p><code>source_connection.backup(target_connection)</code> вызывается на источнике и получает соединение назначения. Он переносит содержимое, но не возвращает Path. API способен заменить содержимое существующей цели. Поэтому до target-соединения отдельно защищаем имя. exists помогает показать состояние, но только xb отказывает, если файл появился между проверкой и созданием.</p>
      <CodeBlock caption="backup_lab.py: полный линейный опыт на уже существующих книгах" code={backupBooks} />
      <p>Перед запуском books-reserve отсутствует. parent.is_dir требует существующий каталог. Сначала читаем sqlite_schema источника: не-SQLite файл не должен оставить новую цель. После xb закрываем файловый дескриптор, открываем отдельное target-соединение и выполняем backup. После закрытия обоих открываем резерв заново ro.</p>
      <p>Вывод: две исходные книги, затем <code>Резерв открыт books-reserve.sqlite3</code>. Успешный backup и открытие отличаются от одного наличия файла. Полноту Planner позже докажем восстановлением и сравнением с описью. Между xb и SQLite-open в нашем локальном каталоге нет конкурентной подмены пути; универсальную защиту от другого процесса не обещаем.</p>
      <QuizCard question="Какую попытку можем выполнить по нашему протоколу?" description="books-source существует и описан; books-reserve свободен; books-occupied занят. Выбираем источник, назначение и состояние писателей." orientation="vertical" options={[
        { title: "books-source → books-occupied; писатели закрыты", description: "Назначение занято" },
        { title: "books-source → books-reserve; писатель открыт", description: "Остановка не завершена" },
        { title: "books-source → books-reserve; писатели закрыты", description: "Разные адреса, новое назначение" },
        { title: "books-missing → books-reserve; писатели закрыты", description: "Источник отсутствует" },
        { title: "books-source → ./books-source; писатели закрыты", description: "Один разрешённый адрес" },
      ]} correctIndex={2} explanation="Допустима третья попытка: читаем известный source, создаём новое reserve. Занятое имя сохраняем; отсутствующий source не создаём; равные разрешённые адреса останавливают действие. Открытый писатель нарушает наш учебный протокол, хотя backup API в общем случае работает и с другими клиентами. Реальных файлов этот выбор не меняет." />
      <p>До Planner выполним малый перенос: выберем свободное books-trial.sqlite3 вместо books-reserve, предскажем состояние до/после, запустим опыт и прочитаем две книги новым соединением. Повтор trial должен отказать, сохранив первый снимок. Новое имя выбираем явно, старое не удаляем.</p>
    </Section>
    <Section number="05" title="Отличаем успешный резерв от неполного результата">
      <Lead>Отказ до создания цели, прерывание после создания и успех оставляют разные состояния. Ошибка не всегда означает, что ничего не появилось.</Lead>
      <p>Один раз создадим контрольный файл полным блоком ниже в backup_lab.py. Префикс b задаёт bytes, \n внутри байтовой строки перевод строки. read_bytes читает все байты. Равенство bytes проверяет содержимое; одинаковый размер этого не доказывает.</p>
      <CodeBlock caption="Один раз создаём защищаемый файл" code={String.raw`from pathlib import Path

occupied = Path("data/backup-work/books-occupied.bin").resolve()
with occupied.open("xb") as file:
    file.write(b"KEEP BOOKS\n")
before = occupied.read_bytes()
print(before)`} />
      <p>Сохраним <code>b'KEEP BOOKS\n'</code> в observations.md. Вернём полный backup-блок, выберем books-occupied.bin как destination и запустим. Ожидаем FileExistsError. Отдельно прочитаем <code>Path("data/backup-work/books-occupied.bin").read_bytes()</code> в backup_lab.py с импортом Path и сравним с сохранёнными bytes. Через wb файл повторно не создаём.</p>
      <p>С тем же полным backup-блоком проверим отсутствующий books-missing и новое свободное назначение: отказ до xb, оба имени после попытки отсутствуют. Затем source и destination зададим как books-source напрямую и через ..: отказ должен сохраниться после разрешения адресов. Исходные книги остаются прежними.</p>
      <p>Для отдельного опыта выберем свободное books-interrupted.sqlite3. В полном backup-блоке вставим <code>raise RuntimeError("Учебная остановка")</code> после закрытия with destination.open("xb"), перед открытием target_connection. Строка остаётся внутри контекста source_connection. Это учебное исключение, не реальный отказ SQLite. closing закроет источник, созданный пустой target останется.</p>
      <p>try выделяет попытку, except обрабатывает исключение, raise передаёт ошибку дальше. Освобождение ресурсов выполняется и при ошибке; успешный вывод после прерывания не выполняется. Созданному interrupted запишем INCOMPLETE в observations.md. Отказ xb на занятом файле не даёт права назвать этот прежний файл нашим неполным резервом.</p>
      <p>Уберём учебную строку остановки до продолжения. Interrupted не удаляем, не используем повторно как новое назначение и не передаём для восстановления. Новый запуск получает другой свободный путь. Даже пустая SQLite способна открыться; отсутствие books отличает её от успешного книжного снимка.</p>
      <Callout>Источник сравниваем по исходной схеме и значениям; обычный контрольный файл по bytes. Не требуем побайтового равенства SQLite и резерва: логическое состояние и расположение страниц различаются.</Callout>
    </Section>
    <Section number="06" title="Что мы будем делать в практике">
      <Lead>Соединим разобранные средства в своё полезное средство существующего Planner.</Lead>
      <LinkedNotes items={[
        { title: "Подтвердим источник", description: "Две синтетические задачи, общий API/CLI URL и чтение новым процессом дадут известный вход." },
        { title: "Зафиксируем состояние", description: "Подготовим внутренние поля, сохраним schema/data до backup." },
        { title: "Напишем операцию", description: "В app/DBase/backup.py сами соберём функцию с Path, guard, двумя соединениями и возвратом успешного пути." },
        { title: "Проверим исходы", description: "Сохраним занятые bytes, отвергнем неверные адреса, отделим INCOMPLETE от SUCCESS." },
        { title: "Подготовим восстановление", description: "Передадим открываемый резерв, опись и три разных адреса. Будущая рабочая цель пока отсутствует." },
      ]} />
      <p>При затруднении возвращаемся к открытию ресурсов, расшифровке PRAGMA и книжному примеру. Готового wrapper Planner здесь нет. Следующим шагом станет восстановление в отдельный новый файл и доказательство сохранности всего содержимого.</p>
    </Section>
  </RichLesson>;
}
