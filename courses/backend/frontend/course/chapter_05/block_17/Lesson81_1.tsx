import {
  Callout, CodeBlock, Diagram, DiagramArrow, DiagramFlow, DiagramNode,
  DiagramNote, InteractiveTable, Lead, LinkedNotes, QuizCard, RichHero,
  RichLesson, Section,
} from "../../shared";

const BOOKS_DEMO = String.raw`from pathlib import Path
import sqlite3

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session


BOOK_SQL = "SELECT id, title, copies FROM books ORDER BY id"


def state(connection):
    return {
        "schema": connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall(),
        "columns": connection.execute(
            "PRAGMA table_info(books)"
        ).fetchall(),
        "rows": connection.execute(BOOK_SQL).fetchall(),
    }


def restore(snapshot, destination):
    snapshot = Path(snapshot).resolve()
    destination = Path(destination).resolve()
    if snapshot == destination:
        raise ValueError("Источник и назначение совпали")
    if not snapshot.is_file():
        raise FileNotFoundError(snapshot)
    source = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)
    try:
        with destination.open("xb"):
            pass
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return destination


def check_books(path, expected):
    connection = sqlite3.connect(
        Path(path).resolve().as_uri() + "?mode=ro", uri=True
    )
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        actual = state(connection)
    finally:
        connection.close()
    if integrity != [("ok",)]:
        raise ValueError("SQLite сообщила об ошибке целостности")
    if actual["schema"] != expected["schema"]:
        raise ValueError("Не совпала схема")
    if actual["columns"] != expected["columns"]:
        raise ValueError("Не совпали свойства столбцов")
    if actual["rows"] != expected["rows"]:
        raise ValueError("Не совпали все строки книг")
    return actual


def main():
    folder = Path("data/restore_books_demo").resolve()
    folder.mkdir(parents=True, exist_ok=False)
    snapshot = folder / "books_snapshot.sqlite3"
    restored = folder / "books_restored.sqlite3"
    bad = folder / "books_incomplete.sqlite3"
    rejected = folder / "books_rejected.sqlite3"

    memory = sqlite3.connect(":memory:")
    try:
        memory.execute(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, "
            "title TEXT NOT NULL, "
            "copies INTEGER NOT NULL CHECK(copies >= 0))"
        )
        memory.execute("INSERT INTO books VALUES (?, ?, ?)", (4, "Маяк", 2))
        memory.execute("INSERT INTO books VALUES (?, ?, ?)", (9, "Сад", 1))
        memory.commit()
        expected = state(memory)
        with snapshot.open("xb"):
            pass
        output = sqlite3.connect(snapshot)
        try:
            memory.backup(output)
        finally:
            output.close()
    finally:
        memory.close()

    original_bytes = snapshot.read_bytes()
    restore(snapshot, restored)
    actual = check_books(restored, expected)
    print("Копия:", restored.name)
    print("Книги:", actual["rows"])
    print("Схема и значения: совпали")

    restore(snapshot, bad)
    connection = sqlite3.connect(bad)
    try:
        connection.execute("DELETE FROM books WHERE id = ?", (9,))
        connection.commit()
    finally:
        connection.close()
    restore(bad, rejected)
    try:
        check_books(rejected, expected)
    except ValueError as error:
        print("Неполная копия: ОТКАЗ.", error)

    url = URL.create("sqlite", database=restored.as_posix())
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            print("Новая Session:", session.execute(text(BOOK_SQL)).all())
    finally:
        engine.dispose()
    occupied_bytes = restored.read_bytes()
    try:
        restore(snapshot, restored)
    except FileExistsError:
        print("Занятое назначение: ОТКАЗ")
    print("Занятый файл сохранён:", restored.read_bytes() == occupied_bytes)
    missing = folder / "missing.sqlite3"
    try:
        restore(missing, folder / "not_created.sqlite3")
    except FileNotFoundError:
        print("Отсутствующий источник: ОТКАЗ")
    print("Ложный источник появился:", missing.exists())
    print("Резерв не изменён:", snapshot.read_bytes() == original_bytes)


if __name__ == "__main__":
    main()
`;

const EXTRA_CHECKS = String.raw`occupied_bytes = restored.read_bytes()
try:
    restore(snapshot, restored)
except FileExistsError:
    print("Занятое назначение: ОТКАЗ")
print("Занятый файл сохранён:", restored.read_bytes() == occupied_bytes)

missing = folder / "missing.sqlite3"
try:
    restore(missing, folder / "not_created.sqlite3")
except FileNotFoundError:
    print("Отсутствующий источник: ОТКАЗ")
print("Ложный источник появился:", missing.exists())
`;

export function Lesson81_1({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Миграции базы"}
        title="Восстановление базы"
        intro="Получим отдельную копию данных, проверим её по исходной описи и вернём обычный Planner к сохранённым задачам."
      />
      <Section number="00" title="От снимка к рабочей копии">
        <Lead>Файл резерва появился. Но сможем ли мы продолжить по нему работу, сохранив каждую задачу?</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Для начала нужны успешный снимок, исходная опись схемы и задач, а также работающий SQL Planner. Если резервирование закончилось ошибкой, сначала возвращаемся к его проверке." },
          { title: "Новый вопрос", description: "Как отличить полноценное восстановление от файла, который открывается, но уже потерял часть данных?" },
          { title: "Результат", description: "Восстановим отдельную копию, сравним структуру и все значения, затем прочитаем её обычным Planner и новым процессом." },
        ]} />
        <p>Опись даёт нам независимый эталон. Новый путь сохраняет исходник и резерв, а несколько разных проверок не позволяют принять случайный успех за готовую рабочую базу.</p>
      </Section>

      <Section number="01" title="Три файла с разными ролями">
        <Lead>Резерв нужен для возвращения к сохранённому состоянию. Поэтому приложение получает отдельную восстановленную копию.</Lead>
        <p>Исходная база остаётся на своём месте. Из неё уже создан согласованный снимок. При восстановлении мы читаем снимок и переносим его содержимое в новый файл. Если дать Planner адрес самого резерва, будущая запись приложения изменит нашу точку возврата.</p>
        <Diagram caption="Направление переноса данных. Каждый узел означает отдельный файл." orientation="vertical">
          <DiagramFlow label="Источник, снимок и новая рабочая копия" orientation="vertical">
            <DiagramNode title="Исходная база" shape="database"><p>Синтетические задачи. Писатели остановлены, исходная опись сохранена.</p></DiagramNode>
            <DiagramArrow label="Успешное резервирование" />
            <DiagramNode title="Резерв" shape="database"><p>Сохраняем. При восстановлении открываем только для чтения.</p></DiagramNode>
            <DiagramArrow label="Восстановление в новый путь" />
            <DiagramNode title="Новая копия" shape="database"><p>Сначала кандидат. Проверяем структуру и содержимое до запуска Planner.</p></DiagramNode>
          </DiagramFlow>
          <DiagramNote title="Ещё один адрес остаётся свободным">Для будущего создания исходной истории выберем другой новый путь. Он не совпадает с заполненной восстановленной копией. Сейчас файл по этому адресу не создаём.</DiagramNote>
        </Diagram>
        <p>Проверка файла и проверка его содержимого тоже имеют разные роли. Сначала SQLite должна прочитать свою структуру без ошибки. Затем мы сравниваем фактическую схему и все строки с описью, снятой до резервирования. Сам снимок не может быть единственным эталоном своей полноты.</p>
        <p>Путь приводим к абсолютному через <code>Path.resolve()</code>. Это позволяет сравнить адреса, записанные разными относительными способами. Существующий снимок открываем через файловый URI: <code>as_uri()</code> строит адрес файла, <code>?mode=ro</code> задаёт чтение, <code>uri=True</code> включает разбор этих параметров в sqlite3.</p>
        <CodeBlock caption="Форма открытия существующего снимка, фрагмент" code={String.raw`snapshot = Path("data/books_snapshot.sqlite3").resolve()
source = sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)`} />
        <p>В этом фрагменте <code>Path</code> импортирован из pathlib, а sqlite3 является стандартным модулем Python. В полном примере ниже есть все импорты. Обычный <code>sqlite3.connect(path)</code> способен создать отсутствующий файл. Режим <code>ro</code> вместо этого откажет: несуществующий снимок не превратится в пустую базу.</p>
        <p>Новое назначение сначала резервируем через <code>Path.open("xb")</code>. Буква <code>x</code> требует создать новый файл и вызывает FileExistsError, если имя занято; <code>b</code> означает двоичный режим. После закрытия этого дескриптора SQLite откроет уже наш новый файл. Это учебный локальный опыт с остановленными писателями, без конкурентной подмены путей другим процессом.</p>
        <p>В книжном опыте контрольные байты помещаются в память программы. Для нескольких отдельных запусков удобнее записать в опись их короткий отпечаток SHA-256. Стандартный hashlib получает байты, sha256 вычисляет отпечаток, hexdigest возвращает его как текст. Повторяем эту команду для исходника и резерва до опыта и после него, подставляя фактический путь при запросе:</p>
        <CodeBlock caption="PowerShell: отпечаток существующего остановленного файла" code={String.raw`.\.venv\Scripts\python.exe -c "from pathlib import Path; import hashlib; p=Path(input('Путь файла: ')).resolve(); print(p); print(hashlib.sha256(p.read_bytes()).hexdigest())"`} />
        <p>Записываем адрес и полученный текст. Совпадение отпечатка подтверждает неизменность файла в нашем опыте, но не заменяет сравнение схемы и задач восстановленной копии. Рабочий источник во время сверки остаётся без писателей.</p>
        <QuizCard
          question="Есть исходная база, успешный снимок и существующий пустой файл candidate.sqlite3. Можно ли использовать candidate.sqlite3 как новое назначение?"
          options={["Да, потому что в нём ещё нет таблиц.", "Нет. Имя уже занято, нужен другой отсутствующий файл."]}
          correctIndex={1}
          explanation="Защита различает существующий и новый путь, а не полезный и пустой файл. Исключительное создание откажет и для файла нулевой длины."
        />
        <p>Теперь у нас есть безопасное направление: существующий резерв читаем, новую цель создаём. Посмотрим, какая операция переносит данные между соединениями.</p>
      </Section>

      <Section number="02" title="Восстановление меняет роли соединений">
        <Lead>Используем знакомый backup API, но источником теперь служит снимок, а назначением становится новая рабочая копия.</Lead>
        <CodeBlock caption="Смысл вызова между уже открытыми соединениями" code="source.backup(target)" />
        <p>Метод вызывается у читаемого источника. В аргументе передаётся соединение назначения. SQLite копирует содержимое источника в назначение и может заменить его прежнее содержимое. Поэтому защита нового пути выполняется до вызова, а не ожидается от самого <code>backup</code>.</p>
        <p>Вызов завершился, но оба соединения ещё нужно закрыть. <code>close()</code> освобождает соединение. Блок <code>finally</code> выполняется и при успехе, и при исключении, поэтому закрытие не теряется после ошибки. Контекст <code>with connection</code> у sqlite3 управляет commit/rollback, но не закрывает connection. В нашем опыте закрытие показано явно.</p>
        <Callout>
          <p><strong>До первого запуска.</strong> Работаем из корня прежнего <code>project/</code> с его готовой <code>.venv</code>. Создадим один файл <code>restore_books_demo.py</code>. Полная программа ниже создаст новый каталог <code>data/restore_books_demo/</code>, два основных SQLite-файла и ещё два файла отрицательного опыта. Повтор всей программы требует нового имени каталога: существующие результаты не перезаписываем.</p>
        </Callout>
        <p>Сначала отделим подготовку от нового механизма. Реестр книг живёт в памяти и имеет поля id, title и copies. Параметр <code>":memory:"</code> создаёт временную SQLite, исчезающую после закрытия соединения. Две строки содержат разные ID и количества. Значение <code>CHECK(copies &gt;= 0)</code> запрещает отрицательное количество, а NOT NULL требует название и количество.</p>
        <p>Подготовка выполняет два INSERT с параметрами: знаки <code>?</code> получают значения из переданного кортежа. <code>commit()</code> завершает запись перед снимком. Функция <code>state</code> читает схему, свойства колонок и строки; её результат сохраняется в <code>expected</code> ещё до backup. Поэтому эталон не получается из проверяемой восстановленной копии.</p>
        <p>В функции <code>restore</code> сначала проверяются разные адреса и существующий источник, затем создаётся новое назначение. Вложенные try/finally закрывают каждое открытое соединение. Частично созданный после сбоя файл остаётся негодным кандидатом: следующий запуск выбирает новое имя, а не выдаёт его за успешную копию.</p>
        <p>Полный пример связывает восстановление, проверки следующего раздела и новое чтение Session. Сначала проследим только участок <code>restore(snapshot, restored)</code>: входом служит готовый снимок, результатом является другой файл. К деталям проверок вернёмся сразу после запуска.</p>
        <CodeBlock caption="restore_books_demo.py: полный независимый опыт" code={BOOKS_DEMO} />
        <p>Перед первым запуском выберем другое свободное имя для значения <code>restored</code>, например <code>books_work.sqlite3</code>. Все последующие чтения уже используют эту переменную. Самостоятельно прогнозируем, какое имя появится в выводе, затем из корня <code>project/</code> выполняем:</p>
        <CodeBlock caption="PowerShell: запуск тем же Python" code={String.raw`.\.venv\Scripts\python.exe restore_books_demo.py`} />
        <CodeBlock caption="Ожидаемый вывод при имени books_work.sqlite3" code={"Копия: books_work.sqlite3\nКниги: [(4, 'Маяк', 2), (9, 'Сад', 1)]\nСхема и значения: совпали\nНеполная копия: ОТКАЗ. Не совпали все строки книг\nНовая Session: [(4, 'Маяк', 2), (9, 'Сад', 1)]\nЗанятое назначение: ОТКАЗ\nЗанятый файл сохранён: True\nОтсутствующий источник: ОТКАЗ\nЛожный источник появился: False\nРезерв не изменён: True"} />
        <p>Первая полезная проверка уже видна: мы выбрали новое назначение, и оно содержит обе книги. У строки <code>(9, 'Сад', 1)</code> три значения: ID 9, название «Сад», один экземпляр. Сам вызов backup ничего не печатает; вывод добавляет наш код после чтения копии.</p>
        <p>В конце main уже есть пробы отказов. Рассмотрим этот участок отдельно, не создавая второй демонстрационный файл. Он использует folder, snapshot и restored из полной программы:</p>
        <CodeBlock caption="Участок полного примера: отказы сохраняют прежние файлы" code={EXTRA_CHECKS} />
        <p>Попытка повторно занять restored даёт отказ; сравнение его байтов до и после показывает True. Отсутствующий источник тоже даёт отказ и не появляется как пустой файл: missing.exists() возвращает False. <code>read_bytes()</code> читает байты файла, <code>exists()</code> проверяет существование, а не исправность SQLite. Для ещё одной пробы выбираем новое назначение, а не запускаем подготовку на прежнем каталоге.</p>
        <p>Импорт файла не запускает подготовку: вызов main защищён условием <code>if __name__ == "__main__"</code>. При прямом запуске файла это условие истинно; при импорте доступны функции, а каталог и снимок заново не создаются. Таким способом можно отдельно вызвать restore с новым назначением и уже существующим снимком.</p>
        <p>В проектной практике мы добавим одну рабочую процедуру к существующему Planner. Из книжного примера переносим направление, защиту пути и закрытие ресурсов, а проверку шести полей Task напишем самостоятельно.</p>
      </Section>

      <Section number="03" title="Исправный файл может быть неполным">
        <Lead>Проверка целостности отвечает на вопрос о внутренней согласованности SQLite. Опись отвечает на другой вопрос: сохранили ли мы то, что ожидали?</Lead>
        <p>Команда <code>PRAGMA integrity_check</code> проверяет внутреннюю структуру базы. При отсутствии найденных ошибок SQLite возвращает одну строку с единственным значением <code>ok</code>. Метод <code>fetchall()</code> собирает строки результата в список кортежей, поэтому точная форма в Python: <code>[("ok",)]</code>. Запятая внутри круглых скобок обозначает кортеж из одного элемента.</p>
        <p>Список сообщений об ошибке не равен этому результату. Исключение при открытии или чтении тоже означает отказ. Но даже точное ok не знает, должны ли существовать книги с ID 4 и 9: удаление строки обычным SQL может оставить совершенно согласованную базу. Проверка внешних ключей является отдельной операцией; в нашем текущем книжном реестре и исходной Task-схеме таких связей ещё нет.</p>
        <p>Функция <code>state</code> в полном примере читает три группы. sqlite_schema хранит SQL-описания объектов; мы выбираем тип, имя, таблицу и SQL, исключая служебные имена sqlite_. <code>PRAGMA table_info(books)</code> возвращает свойства колонок. SELECT читает все книжные значения, а <code>ORDER BY id</code> задаёт один и тот же порядок для сравнения.</p>
        <InteractiveTable title="Читаем свойства столбца, а не перечень терминов" columns={["Часть строки table_info", "Пример для title", "Вывод"]} rows={[
          { id: "name", label: "name", values: ["title", "Сопоставляем одно и то же поле."] },
          { id: "nullable", label: "notnull", values: ["1", "В файле явно записано NOT NULL."] },
          { id: "default", label: "dflt_value", values: ["None", "SQL-default не задан. Это не значение поля в строке."] },
          { id: "primary", label: "pk", values: ["0", "title не является первичным ключом."] },
        ]} />
        <p>Полный порядок значений table_info: номер колонки, name, type, notnull, dflt_value, pk. Например, строка <code>(1, 'title', 'TEXT', 1, None, 0)</code> описывает вторую колонку title: тип TEXT, обязательная, без SQL-default, не PK. Особый случай INTEGER PRIMARY KEY читаем по полю pk, не делаем вывод о нём только из notnull.</p>
        <p>CHECK находится в SQL определения таблицы, а индексы перечисляет <code>PRAGMA index_list(books)</code>; <code>PRAGMA index_info</code> с именем индекса показывает его колонки. В этом реестре отдельные индексы не создавались, но правило CHECK видно в sqlite_schema. При восстановлении одной и той же схемы достаточно сравнить сохранённые описания и свойства напрямую; универсальный инструмент сравнения разных версий схемы нам пока не нужен.</p>
        <p>В программе <code>check_books</code> сначала читает файл новой connection и закрывает её, затем отдельно сравнивает целостность, схему, колонки и все строки. Оператор <code>!=</code> замечает различие значений или состава списка. Если строка потеряна, программа не делает из оставшейся строки новый эталон: выдаёт отказ.</p>
        <QuizCard
          question="Эталон: (4, 'Маяк', 2), (9, 'Сад', 1). У всех образцов одинаковая схема и integrity_check = ok. Какой вывод обоснован?"
          description="A: обе строки эталона. B: только (4, 'Маяк', 2). C: (4, 'Маяк', 2) и (9, 'Сад', 7). Сопоставим ID и каждое поле до выбора."
          options={[
            "Все три подходят: SQLite уже вернула ok.",
            "A и C подходят: у них по две строки и одинаковые ID.",
            "Только A: у B потерян ID 9, у C изменено copies для ID 9.",
          ]}
          correctIndex={2}
          orientation="vertical"
          explanation="У B отсутствует ожидаемая строка. У C множество ID совпало, но количество экземпляров изменилось с 1 на 7. Только A прошёл все показанные сверки; запуск приложения остаётся отдельным свидетельством."
          answer={<InteractiveTable title="Что установила каждая проверка" columns={["Образец", "Целостность и схема", "ID и значения"]} rows={[
            { id: "a", label: "A", values: ["Совпали", "Совпали"] },
            { id: "b", label: "B", values: ["Совпали", "Нет ID 9"] },
            { id: "c", label: "C", values: ["Совпали", "copies у ID 9 изменено"] },
          ]} />}
        />
        <p>В отрицательном опыте программа создаёт ещё одну копию снимка, удаляет ID 9 только в ней и выполняет commit. Кортеж <code>(9,)</code> в вызове DELETE передаёт один параметр для знака вопроса. Затем неполный снимок восстанавливается в новый books_rejected.sqlite3. Его структура исправна, но check_books отвергает неполный набор. Хороший снимок и принятая копия всё это время сохраняются.</p>
        <QuizCard
          question="В новом образце строки показаны в обратном порядке: (9, 'Сад!', 1), (4, 'Маяк', 2). Число строк, ID и copies совпали. Что осталось проверить?"
          options={["Только порядок отображения: данные уже доказаны.", "Название по ID: у ID 9 изменилось title, поэтому образец непригоден."]}
          correctIndex={1}
          explanation="Порядок показанных строк сам по себе не потеря данных: сопоставляем записи по ID. Но «Сад!» отличается от сохранённого «Сад», и это содержательное расхождение."
        />
        <p>У Planner сверка шире книжной: сохраняем id, title, priority, is_done, tags и title_normalized. API и CLI показывают только публичные поля, поэтому внутренние tags и title_normalized читаем SQL отдельно. В SQLite is_done представлен 0/1, в API false/true. Для tags сравниваем исходное сохранённое SQL-представление с таким же чтением после restore, включая порядок элементов; не пересчитываем title_normalized по придуманному правилу.</p>
        <p>В исходной Task-схеме проверяем и ограничения: положительный ID, priority от 1 до 5, PK, обязательность, реальные SQL-default и индексы из описи. Python-default модели не добавляет автоматически SQL-default в уже существующий файл. При расхождении возвращаемся к описи и резерву, а не вставляем потерянные задачи вручную.</p>
      </Section>

      <Section number="04" title="Новый адрес, новая Session, новый процесс">
        <Lead>Правильная SQL-копия ещё не гарантирует, что уже запущенное приложение читает именно её.</Lead>
        <p>В конце книжного примера <code>URL.create</code> строит SQLAlchemy URL. Имя драйвера sqlite задаёт тип базы, аргумент database получает абсолютный путь. <code>create_engine</code> создаёт объект доступа к этой базе. Новая <code>Session(engine)</code> читает данные через него, а <code>text(BOOK_SQL)</code> обозначает текстовый SQL-запрос. Метод all собирает все строки результата. Контекст Session закрывает её при выходе, затем <code>engine.dispose()</code> освобождает соединения engine.</p>
        <p>Вывод новой Session содержит обе исходные книги. Он получен после закрытия соединений восстановления. Однако весь книжный пример работал в одном Python-процессе. Для Planner отдельно проверим новый процесс: он заново загрузит настройки и создаст свои ресурсы, вместо использования старых объектов в памяти.</p>
        <p>В существующем Planner настройка <code>DATABASE_URL</code> читается при сборке engine. Переменная окружения имеет приоритет над .env. Если поменять окружение после запуска, старый engine не переедет в другой файл. Поэтому сначала завершаем прежние процессы, задаём URL восстановленной копии и проверяем эффективную настройку. Постоянный .env не меняем.</p>
        <CodeBlock caption="PowerShell из project/: сохраняем окружение и выбираем файл" code={String.raw`$HadDatabaseUrl = Test-Path Env:DATABASE_URL
$PreviousDatabaseUrl = $env:DATABASE_URL
$env:DATABASE_URL = Read-Host "Абсолютный SQLite URL принятой копии"
.\.venv\Scripts\python.exe -c "from app.core.config import load_settings; print(load_settings().database_url)"
.\.venv\Scripts\python.exe -m app.main`} />
        <p>Read-Host запрашивает значение в терминале. У URL Windows вид <code>sqlite:///C:/каталог/копия.sqlite3</code>; выбираем свой фактический путь. Это SQLAlchemy URL, не read-only URI с mode=ro. В меню Planner вводим <code>list</code>, сверяем ID и публичные значения, затем <code>exit</code>. Эти слова вводятся в меню приложения, а не как команды PowerShell.</p>
        <CodeBlock caption="После exit: API с тем же окружением" code={String.raw`.\.venv\Scripts\python.exe -m uvicorn app.api:app --host 127.0.0.1 --port 8000`} />
        <p>Открываем локальный <code>http://127.0.0.1:8000/docs</code>, находим GET /tasks, нажимаем Try it out, оставляем фильтры выключенными, задаём limit 50 и выполняем Execute. Ответ должен содержать обе синтетические задачи с ожидаемыми id, title, priority и is_done. Статус /health доказывает доступность HTTP, но не чтение задач. Если выбран другой свободный порт, меняем его и в команде, и в адресе браузера.</p>
        <p>Останавливаем API сочетанием Ctrl+C в его терминале, затем заново запускаем <code>python -m app.main</code> тем же Python и выполняем list/exit. Новый процесс должен показать тот же набор без add, done, delete и повторной инициализации. Для проверки достаточно последовательных запусков. Если используем второй терминал, задаём и проверяем URL в нём отдельно.</p>
        <CodeBlock caption="После всех чтений возвращаем прежнее окружение терминала" code={String.raw`if ($HadDatabaseUrl) {
    $env:DATABASE_URL = $PreviousDatabaseUrl
} else {
    Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
}`} />
        <p>Первая переменная запомнила, было ли значение до опыта, вторая хранит само значение. После опыта возвращаем его либо удаляем только добавленную переменную окружения. Базы и .env эта команда не удаляет. Если чтение пустое или таблица не найдена, сначала проверяем эффективный URL и принятую копию. Init_db не восстанавливает потерянные строки и не является исправлением такого результата.</p>
      </Section>

      <Section number="05" title="Что мы будем делать в практике">
        <Lead>Перенесём механизм на существующий Planner: получим проверенную заполненную копию, сохранив исходник и успешный резерв.</Lead>
        <LinkedNotes items={[
          { title: "Выберем независимые адреса", description: "Покажем резерв и исходную опись, назначим новое восстановление и отдельный свободный адрес будущей истории." },
          { title: "Напишем восстановление и проверку", description: "Самостоятельно применим guard и backup, сопоставим схему и все шесть Task-полей. Неполный отдельный образец получит отказ с причиной." },
          { title: "Вернём обычный Planner к данным", description: "Прочитаем принятую копию через API, CLI и новый CLI-процесс. Передадим резерв, опись, совместимый код, рабочую копию и другой отсутствующий путь." },
        ]} />
        <p>Книжный пример подготавливает операции и объясняет разные свидетельства. В практике остаются собственная процедура для Task, выбор реальных адресов и решение о пригодности каждого кандидата. За ролями путей возвращаемся к схеме, за сравнением к разбору целостности, за запуском к настройке нового процесса.</p>
      </Section>
    </RichLesson>
  );
}
