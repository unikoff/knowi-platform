import { CodeBlock, CodeSequence, Diagram, DiagramArrow, DiagramFlow, DiagramNode, Lead, LinkedNotes, RichHero, RichLesson, Section } from "../../shared";

const setup = [
  "Get-Location",
  "Test-Path .\\data\\demo",
  "New-Item -ItemType Directory -Path .\\data\\demo",
  "py -3.12 --version",
  "py -3.12 -m venv .venv",
  ".\\.venv\\Scripts\\python.exe --version",
  ".\\.venv\\Scripts\\python.exe -m pip install -r requirements-dev.txt",
].join("\n");

const showConfig = [
  "# notes/show_config.py",
  "import os",
  "",
  "print(os.environ.get('DATABASE_URL', 'адрес не задан'))",
].join("\n");

const initNotes = [
  "# notes/init_db.py",
  "import os",
  "import sqlite3",
  "from pathlib import Path",
  "",
  "raw_path = os.environ.get('NOTES_DB')",
  "if not raw_path:",
  "    raise SystemExit('Учебный путь не задан')",
  "path = Path(raw_path)",
  "if not path.parent.is_dir():",
  "    raise SystemExit('Каталог не найден')",
  "if path.exists():",
  "    raise SystemExit('Файл уже существует')",
  "db = sqlite3.connect(path)",
  "db.execute('CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT NOT NULL, done INTEGER NOT NULL)')",
  "db.commit()",
  "db.close()",
  "print('Таблица создана')",
].join("\n");

const schemaDemo = [
  "# notes/schema_demo.py",
  "import sqlite3",
  "from pathlib import Path",
  "",
  "path = Path('data/demo/schema.sqlite3')",
  "if path.exists():",
  "    raise SystemExit('Файл уже существует')",
  "db = sqlite3.connect(path)",
  "try:",
  "    db.execute('CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT)')",
  "    db.execute('CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, body TEXT, color TEXT)')",
  "    print('body:', db.execute('SELECT body FROM notes').fetchall())",
  "    try:",
  "        db.execute('SELECT color FROM notes')",
  "    except sqlite3.OperationalError as error:",
  "        print('Нет столбца color:', error)",
  "    db.commit()",
  "finally:",
  "    db.close()",
].join("\n");

const sqlProbe = [
  "# notes/sql_probe.py",
  "import sqlite3",
  "",
  "db = sqlite3.connect(':memory:')",
  "db.execute('CREATE TABLE samples (id INTEGER, label TEXT)')",
  "db.execute('INSERT INTO samples VALUES (?, ?)', (7, 'чай'))",
  "row = db.execute('SELECT id, label FROM samples WHERE id = ?', (7,)).fetchone()",
  "print('Найдено:', row)",
  "missing = db.execute('SELECT id, label FROM samples WHERE id = ?', (99,)).fetchone()",
  "print('Нет записи:', missing)",
  "db.close()",
].join("\n");

const jsonProbe = [
  "# notes/json_probe.py",
  "import json",
  "",
  "tags = ['home', 'study']",
  "encoded = json.dumps(tags)",
  "print('Текст:', encoded)",
  "print('Список:', json.loads(encoded))",
].join("\n");

const demoApi = [
  "# notes/api.py",
  "import os",
  "import sqlite3",
  "from pathlib import Path",
  "from fastapi import FastAPI",
  "from pydantic import BaseModel, Field",
  "",
  "path = os.environ.get('NOTES_DB')",
  "if not path or not Path(path).is_file():",
  "    raise SystemExit('Учебная база не найдена')",
  "",
  "app = FastAPI()",
  "",
  "class NoteInput(BaseModel):",
  "    body: str = Field(min_length=1)",
  "",
  "@app.post('/notes', status_code=201)",
  "def add_note(note: NoteInput):",
  "    db = sqlite3.connect(path)",
  "    try:",
  "        cursor = db.execute('INSERT INTO notes (body, done) VALUES (?, ?)', (note.body, 0))",
  "        db.commit()",
  "        return {'id': cursor.lastrowid, 'body': note.body, 'done': False}",
  "    finally:",
  "        db.close()",
  "",
  "@app.get('/notes/{note_id}')",
  "def get_note(note_id: int):",
  "    db = sqlite3.connect(path)",
  "    try:",
  "        row = db.execute('SELECT id, body, done FROM notes WHERE id = ?', (note_id,)).fetchone()",
  "        return {'id': row[0], 'body': row[1], 'done': bool(row[2])} if row else None",
  "    finally:",
  "        db.close()",
].join("\n");

const demoCli = [
  "# notes/finish.py",
  "import os",
  "import sqlite3",
  "from pathlib import Path",
  "",
  "path = os.environ.get('NOTES_DB')",
  "if not path or not Path(path).is_file():",
  "    raise SystemExit('Учебная база не найдена')",
  "note_id = int(input('ID заметки: '))",
  "db = sqlite3.connect(path)",
  "try:",
  "    row = db.execute('SELECT id, body, done FROM notes WHERE id = ?', (note_id,)).fetchone()",
  "    if row is None:",
  "        print('Заметка не найдена')",
  "    else:",
  "        print('Найдена:', row[0], row[1], bool(row[2]))",
  "        cursor = db.execute('UPDATE notes SET done = ? WHERE id = ?', (1, note_id))",
  "        db.commit()",
  "        print('Изменено строк:', cursor.rowcount)",
  "finally:",
  "    db.close()",
].join("\n");

const demoTags = [
  "# notes/tags_demo.py",
  "import json",
  "import sqlite3",
  "from pathlib import Path",
  "",
  "path = Path('data/demo/labels.sqlite3')",
  "if path.exists():",
  "    raise SystemExit('Файл уже существует')",
  "db = sqlite3.connect(path)",
  "try:",
  "    db.execute('CREATE TABLE labels (id INTEGER PRIMARY KEY, name TEXT, tags TEXT)')",
  "    db.execute('INSERT INTO labels VALUES (?, ?, ?)', (1, 'Книга', '[]'))",
  "    db.execute('INSERT INTO labels VALUES (?, ?, ?)', (2, 'Карандаш', '[]'))",
  "    db.commit()",
  "    print('До:', db.execute('SELECT id, name, tags FROM labels ORDER BY id').fetchall())",
  "    cursor = db.execute('UPDATE labels SET tags = ? WHERE id = ?', (json.dumps(['дом']), 1))",
  "    print('Изменено:', cursor.rowcount)",
  "    db.commit()",
  "    rows = db.execute('SELECT id, name, tags FROM labels ORDER BY id').fetchall()",
  "    for row in rows:",
  "        print(row[0], row[1], json.loads(row[2]))",
  "finally:",
  "    db.close()",
].join("\n");

export function Lesson81({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero chip={module ?? "StudyHub · SQL Planner"} title="Проверяем SQL Planner" intro="Выберем учебную базу и выясним, действительно ли API и CLI читают одно сохранённое состояние." />
      <Section number="00" title="От переданного проекта к наблюдаемому результату">
        <Lead>У нас есть принятый снимок Planner. Проверим, что происходит в нашей отдельной учебной копии.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "В проекте предусмотрены SQL-хранилище, API и CLI. Готовность нашей среды ещё нужно проверить." },
          { title: "Новый вопрос", description: "Как узнать, что разные процессы обращаются к одному файлу и сохраняют данные?" },
          { title: "Результат", description: "Мы подготовим отдельную базу, пройдём сценарий и сравним записи после нового запуска." },
        ]} />
        <p>Код показывает задуманный договор. Адрес базы и наблюдения в нашей копии покажут, выполняется ли он здесь.</p>
      </Section>
      <Section number="01" title="Отдельный путь и один интерпретатор">
        <Lead>Сначала отделим учебные данные от рабочих. Нам не нужен доступ к единственной базе с реальными задачами.</Lead>
        <p>Работаем в изолированной копии проекта, в каталоге <code>project/</code>. В PowerShell команда <code>Get-Location</code> показывает текущую папку, а <code>Test-Path</code> отвечает, существует ли путь. Создадим каталог только после ответа False и убедимся, что выбранного файла внутри ещё нет. Если он уже существует или его происхождение неизвестно, остановимся и выберем другое новое имя.</p>
        <p>Для независимого примера подготовим пустой мини-проект <code>demo_notes/</code>. В его корне команда <code>New-Item -ItemType Directory -Path .\notes</code> создаёт каталог пакета, а <code>New-Item -ItemType File -Path .\notes\__init__.py</code> создаёт пустой файл. В <code>requirements-dev.txt</code> разместим строки <code>fastapi==0.162.1</code>, <code>uvicorn==0.54.0</code>, <code>pytest==9.2.0</code> и <code>httpx2==2.13.1</code>, каждую с новой строки. Из корня мини-проекта выполним команды ниже. Если каталог <code>data</code> отсутствует, сначала создадим его такой же командой <code>New-Item -ItemType Directory -Path .\data</code>.</p>
        <CodeBlock caption="PowerShell в demo_notes/" code={setup} />
        <p><code>New-Item -ItemType Directory</code> создаёт каталог. <code>py -3.12</code> выбирает Python 3.12, а <code>-m venv .venv</code> создаёт отдельное окружение. Путь к его Python позволяет использовать его без активации. <code>-m pip install -r</code> устанавливает зависимости из указанного файла; дождёмся успешного завершения. Если Python или пакеты недоступны, восстановим среду до дальнейших шагов.</p>
        <p>Например, <code>Get-Location</code> показывает полный путь, заканчивающийся на <code>demo_notes</code>, а <code>Test-Path .\data\demo</code> до создания показывает <code>False</code>. Это означает, что проверяемого каталога пока нет, а не то, что отсутствует весь проект. После создания та же проверка вернёт <code>True</code>. Для занятого имени создание повторять не будем: выберем другое имя и ещё раз проверим путь.</p>
        <p>Проверим себя: если <code>Test-Path .\data\demo</code> вернул True, создавать каталог поверх существующего нельзя. Выберем другое свободное имя, не удаляя найденные данные. В PowerShell условие <code>if (Test-Path .\data\demo) {"{ throw 'Путь занят' }"}</code> прерывает выполнение сообщением, если путь существует. Обратная форма <code>if (-not (Test-Path .\.venv)) {"{ py -3.12 -m venv .venv }"}</code> создаёт среду, когда её ещё нет.</p>
      </Section>
      <Section number="02" title="Настройка принадлежит процессу">
        <Lead>Одинаковая папка ещё не означает, что оба запуска читают один файл.</Lead>
        <p>Переменная окружения передаёт настройку запущенной программе. В PowerShell зададим адрес:</p>
        <CodeBlock caption="Настройка в PowerShell" code={"$env:DATABASE_URL = 'sqlite:///data/demo/notes.sqlite3'"} />
        <p>Присваивание относится к текущему терминалу и процессам, которые из него запускаются. Во втором терминале значение нужно установить и проверить отдельно.</p>
        <p>Сохраним этот полный модуль в <code>notes/show_config.py</code> мини-проекта:</p>
        <CodeBlock caption="Читаем настройку" code={showConfig} />
        <p>Из <code>demo_notes/</code> выполним команду:</p>
        <CodeBlock caption="Запускаем модуль настройки" code={'.\\.venv\\Scripts\\python.exe -m notes.show_config'} />
        <p><code>-m</code> запускает модуль пакета; точка отделяет пакет от имени файла. Первый терминал покажет заданный URL. Если во втором установить другой URL, он покажет другое значение, хотя терминалы открыты в одной папке. При отсутствии переменной пример выведет «адрес не задан».</p>
        <p>В Planner настройку читает <code>app/core/config.py</code>. Явная переменная имеет приоритет над <code>.env</code>; без неё может использоваться адрес по умолчанию. Перед каждой записью распечатаем разрешённый адрес и сравним с выбранным учебным путём:</p>
        <CodeBlock caption="PowerShell из project/" code={'.\\.venv\\Scripts\\python.exe -c "from app.core.config import load_settings; print(load_settings().database_url)"'} />
        <p>Здесь <code>-c</code> выполняет короткую команду Python. Например, при учебном расположении проекта в <code>C:/study/project</code> относительный адрес из первой строки разрешается к файлу из второй:</p>
        <CodeBlock caption="Относительный адрес и полный путь к файлу" code={'sqlite:///data/demo/notes.sqlite3\nC:/study/project/data/demo/notes.sqlite3'} />
        <p>Выведенный URL содержит этот полный путь после префикса SQLite. Сравниваем именно расположение файла; соседний файл с другим именем или каталогом не подходит. Если адрес неизвестен или не совпадает с учебным, не продолжим запись.</p>
        <p>Посмотрим на один успешный путь. Стрелки показывают запись в файл и последующее чтение из него.</p>
        <Diagram caption="Два процесса и одна база" orientation="vertical">
          <DiagramFlow label="От записи API к чтению CLI" orientation="vertical">
            <DiagramNode title="Процесс API">Получил адрес A</DiagramNode>
            <DiagramArrow label="сохраняет запись" />
            <DiagramNode title="Учебный файл A" shape="database">Хранит запись после завершения запроса</DiagramNode>
            <DiagramArrow label="читает запись из A" />
            <DiagramNode title="Процесс CLI">Тоже получил адрес A</DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>Это один файл, а не память, общая для двух процессов. Если CLI получил адрес B, последняя стрелка к файлу A уже не описывает его работу.</p>
        <p>Если второй процесс получил адрес B, он обращается к другому файлу. Если настройка в нём не задана, он может использовать значение из <code>.env</code> или адрес по умолчанию. Поэтому проверка обоих адресов предшествует записи; даже совпадающий адрес ещё не доказывает результат чтения.</p>
      </Section>
      <Section number="03" title="Что создаёт начальная схема">
        <Lead>Новый файл ещё не содержит таблиц. Подготовим их отдельной командой.</Lead>
        <p>В мини-проекте зададим в каждом нужном терминале <code>$env:NOTES_DB = 'data/demo/notes.sqlite3'</code>. Это отдельный учебный путь для примера. Сохраним файл ниже, затем из <code>demo_notes/</code> запустим <code>.\.venv\Scripts\python.exe -m notes.init_db</code>.</p>
        <CodeBlock caption="notes/init_db.py" code={initNotes} />
        <p><code>sqlite3.connect</code> открывает SQLite-файл и может создать отсутствующий файл. Поэтому мы заранее проверяем, что учебный путь задан, каталог существует, а выбранный для создания файл ещё не занят. <code>Path</code> представляет путь; <code>parent.is_dir()</code> проверяет каталог, <code>exists()</code> наличие файла. <code>CREATE TABLE</code> задаёт таблицу, <code>commit()</code> подтверждает изменения, <code>close()</code> закрывает соединение. Первый запуск печатает «Таблица создана»; повторный и запуск без пути останавливаются до открытия файла.</p>
        <p>В существующей таблице команда <code>CREATE TABLE IF NOT EXISTS</code> не добавляет новые столбцы. Сохраним самостоятельный пример в <code>notes/schema_demo.py</code>, проверим, что <code>data/demo/schema.sqlite3</code> отсутствует, и выполним <code>.\.venv\Scripts\python.exe -m notes.schema_demo</code> из <code>demo_notes/</code>.</p>
        <CodeBlock caption="Повторное объявление не меняет таблицу" code={schemaDemo} />
        <p>Первое объявление создаёт таблицу с id и body. Второе добавляет color только в текст команды, но существующая таблица остаётся прежней. Выборка body возвращает пустой список, а выборка color сообщает об отсутствии столбца. Попробуем на другом новом файле добавить во второе объявление поле size и предскажем результат SELECT для size до запуска: поле также не появится. Для Planner штатная команда <code>.\.venv\Scripts\python.exe -m app.DBase.init_db</code> создаёт отсутствующую исходную схему, но не обновляет существующую и не импортирует JSON.</p>
      </Section>
      <Section number="04" title="Два интерфейса и новое чтение">
        <Lead>Теперь проследим независимый пример: один процесс создаёт заметку, другой меняет её, а новое чтение показывает сохранённый результат.</Lead>
        <p>HTTP-запрос содержит метод, адрес и иногда JSON-тело. POST создаёт запись, GET читает её, а статус ответа сообщает результат запроса. Например, тело <code>{'{"body":"купить чай"}'}</code> передаёт один текст. Ответ <code>201</code> говорит о создании, а <code>422</code> в этом примере означает, что пустой текст не прошёл проверку. Это разные наблюдения: после отказа список данных нужно прочитать отдельно.</p>
        <p>Ниже минимальный API заметок, работающий с файлом из предыдущего примера. Первые строки проверяют адрес и готовят приложение. Функция POST сохраняет текст и возвращает назначенный ID; функция GET по этому ID читает строку заново. Сохраним весь пример в <code>notes/api.py</code>.</p>
        <CodeBlock caption="Независимый API заметок" code={demoApi} />
        <p>Сначала пример проверяет путь: <code>Path(path).is_file()</code> подтверждает наличие выбранного файла. Если путь не задан или файл отсутствует, программа останавливается до открытия базы. <code>BaseModel</code> описывает вход запроса, а <code>Field(min_length=1)</code> требует непустой текст.</p>
        <p>Декоратор <code>@app.post</code> связывает функцию с адресом и методом POST. Внутри функция вставляет текст и отметку 0, затем <code>commit()</code> сохраняет запись. Знак <code>?</code> обозначает место для значения из кортежа; <code>lastrowid</code> сообщает ID новой строки. В ответе статус 201 и данные этой заметки, включая <code>done: false</code>.</p>
        <p>При GET функция ищет переданный ID: <code>fetchone()</code> возвращает одну строку либо <code>None</code>. Для найденной записи <code>bool</code> переводит сохранённые 0 и 1 в False и True. Блок <code>finally</code> закрывает соединение и при ошибке. Это отдельный учебный пример, который поможет проследить состояние между процессами Planner.</p>
        <p>В первом терминале мини-проекта с заданным <code>NOTES_DB</code> запустим <code>.\.venv\Scripts\python.exe -m uvicorn notes.api:app --host 127.0.0.1 --port 8000</code>. Здесь указаны модуль и объект приложения, локальный адрес и порт. Если порт занят, выберем свободный и используем его в браузере. Откроем <code>http://127.0.0.1:8000/docs</code>, раскроем POST /notes, нажмём Try it out («попробовать»), введём <code>{'{"body":"купить чай"}'}</code> и нажмём Execute («выполнить»). Ответ 201 содержит id, текст и done: false. Запрос с пустым body получает 422.</p>
        <p>Во втором терминале зададим тот же <code>NOTES_DB</code> и сохраним следующий модуль. Он получает ID, сначала читает и показывает найденную заметку, а затем меняет отметку. Если запись отсутствует, <code>fetchone()</code> возвращает <code>None</code>: мы увидим сообщение и не будем выполнять изменение.</p>
        <CodeBlock caption="notes/finish.py" code={demoCli} />
        <p>Запустим <code>.\.venv\Scripts\python.exe -m notes.finish</code> и введём ID из ответа. До изменения увидим ID, текст «купить чай» и <code>False</code>; затем появится «Изменено строк: 1». При несуществующем ID увидим «Заметка не найдена», изменение не выполняется. Остановим сервер сочетанием Ctrl+C и запустим снова с тем же адресом. В GET /notes/{'{note_id}'} введём ID: ответ покажет тот же текст и <code>done: true</code>. Новое чтение показывает сохранённое изменение, а не только состояние объекта старого процесса.</p>
        <p>Попробуем другую заметку и предскажем, что увидит процесс с тем же адресом и с адресом отсутствующего файла. Во втором случае пример остановится до открытия: это не доказательство состояния первой базы. Проверим вывод и объясним, почему общая папка не гарантирует общее состояние. Маршрут <code>/health</code> у Planner сообщает, что HTTP-процесс отвечает. Он не читает задачи и сам по себе не доказывает общий файл. В меню Planner команда <code>find</code> запрашивает ID и показывает найденную задачу, <code>done</code> запрашивает ID и отмечает её выполненной, <code>exit</code> завершает CLI. Команды вводятся в меню, а не в PowerShell. При несовпадении результатов сначала сравним настройки обоих терминалов.</p>
        <CodeSequence
          title="Проследим сохранённую заметку"
          prompt="В примере оба процесса используют файл A. Расположим наблюдения так, чтобы последнее подтвердило сохранение изменения после перезапуска. Лишнее наблюдение оставим в стороне."
          pieces={[
            { id: "read", code: "CLI находит заметку в файле A и меняет её отметку" },
            { id: "health", code: "Маршрут health отвечает, не читая заметку" },
            { id: "create", code: "API сохраняет новую заметку в файле A" },
            { id: "again", code: "Новый процесс читает из A изменённую отметку" },
          ]}
          correctOrder={["create", "read", "again"]}
          explanation="Сначала запись появляется, затем другой процесс изменяет её, а новое чтение показывает сохранённое состояние. Health не проверяет содержимое базы."
          incorrectExplanation="Сравним, существует ли заметка до её изменения и какое чтение происходит после перезапуска. Ответ health не подтверждает данные."
        />
      </Section>
      <Section number="05" title="Проверяем внутренние данные через SQL">
        <Lead>Публичный ответ Planner не показывает всё, что хранится в таблице.</Lead>
        <p>API возвращает id, title, priority и is_done. Внутренние tags и title_normalized проверяются в хранилище. Сначала разберём выбор одной строки отдельно от работы с файлом. Сохраним этот пример в <code>notes/sql_probe.py</code> и запустим из <code>demo_notes/</code> командой <code>.\.venv\Scripts\python.exe -m notes.sql_probe</code>.</p>
        <CodeBlock caption="Поиск строки на маленьком примере" code={sqlProbe} />
        <p><code>:memory:</code> создаёт временную учебную базу в памяти одного соединения. В ней одна строка: ID 7 и текст «чай». Первый запрос подставляет 7 вместо <code>?</code> и возвращает <code>(7, 'чай')</code>: первое значение кортежа это ID, второе текст. Для ID 99 строка не найдена, поэтому <code>fetchone()</code> возвращает <code>None</code>. Закрытие соединения завершает этот опыт, файл Planner в нём не участвует. Попробуем ID 7 и 99 поменять местами: результат найденного и отсутствующего случая тоже поменяется.</p>
        <p>Список меток Python сам по себе не является текстом JSON. Сохраним второй независимый пример в <code>notes/json_probe.py</code> и выполним <code>.\.venv\Scripts\python.exe -m notes.json_probe</code> из того же каталога.</p>
        <CodeBlock caption="Список и его JSON-представление" code={jsonProbe} />
        <p>Первая строка показывает JSON-текст <code>["home", "study"]</code>, а вторая восстанавливает список Python <code>['home', 'study']</code>. Кавычки отличаются, потому что это два разных представления одних значений. Изменим одну метку и убедимся, что при обратном преобразовании возвращается изменённый список.</p>
        <p>Теперь соединим поиск и JSON на отдельной таблице меток. Создадим новый файл <code>data/demo/labels.sqlite3</code>; если он уже существует, пример остановится и не перезапишет его. Сохраним код в <code>notes/tags_demo.py</code> и запустим из <code>demo_notes/</code>: <code>.\.venv\Scripts\python.exe -m notes.tags_demo</code>.</p>
        <CodeBlock caption="Две независимые строки и одно изменение" code={demoTags} />
        <p><code>SELECT</code> читает строки, <code>UPDATE</code> меняет их, а <code>WHERE id = ?</code> ограничивает выбор одним ID. <code>ORDER BY id</code> упорядочивает результат по ID, <code>fetchall()</code> получает все выбранные строки, а <code>cursor.rowcount</code> показывает число строк, затронутых UPDATE. В выводе «Изменено: 1» единица относится только к строке с ID 1. После неё строка <code>1 Книга ['дом']</code> показывает ID, название и восстановленный список меток. Строка <code>2 Карандаш []</code> показывает, что соседняя запись не изменилась. На другом новом файле попробуем отсутствующий ID: число изменённых строк будет 0, обе исходные строки останутся прежними.</p>
        <p>В учебном Planner перед SQL-подготовкой остановим записывающие процессы, проверим полный путь и наличие файла. URL из настройки и путь для <code>sqlite3.connect</code> имеют разную форму. Из <code>project/</code> откроем Python командой <code>.\.venv\Scripts\python.exe</code>; появится приглашение <code>&gt;&gt;&gt;</code>, в котором можно вводить показанные строки Python. Команда <code>exit()</code> возвращает в PowerShell. Для чтения используем <code>SELECT id, title, priority, is_done, tags, title_normalized FROM tasks ORDER BY id</code>, а для записи меток по конкретному ID форму <code>UPDATE tasks SET tags = ? WHERE id = ?</code>. До и после сравним обе строки; значение ID берём из ответа API.</p>
      </Section>
      <Section number="06" title="Тесты и наблюдения отвечают на разные вопросы">
        <Lead>Существующие тесты помогают проверить известные случаи. Сквозной запуск отдельно показывает состояние выбранной нами базы.</Lead>
        <p>В мини-проекте сохраним файл <code>test_sum.py</code> с кодом ниже. Из корня запустим <code>.\.venv\Scripts\python.exe -m pytest -q</code>. <code>-m pytest</code> запускает тесты этим Python, а <code>-q</code> сокращает вывод.</p>
        <CodeBlock caption="test_sum.py" code={"def test_sum():\n    assert 2 + 3 == 5"} />
        <p><code>assert</code> проверяет следующее за ним условие и сообщает об ошибке, если оно ложно. При верном ожидании увидим один прошедший тест. Попробуем заменить 5 на 6: тест покажет ошибку сравнения. Затем вернём 5 и повторим запуск. Это различие не говорит, какой файл используют API и CLI. Для этого нужен отдельный сценарий записи и чтения.</p>
        <p>В Planner запустим существующий набор той же командой из <code>project/</code>. Запишем фактический итог, адрес, количество строк и все шесть полей. Ошибку оставим видимой и разберём до передачи данных дальше.</p>
      </Section>
      <Section number="07" title="Что мы будем делать в практике">
        <Lead>На отдельной учебной базе проверим существующий Planner, сохранив его код и интерфейсы.</Lead>
        <LinkedNotes items={[
          { title: "Подготовим отдельный файл", description: "Выберем новый путь, проверим адрес каждого процесса и получим исходную схему." },
          { title: "Проверим API", description: "Создадим две задачи и убедимся, что неверный запрос не добавил третью." },
          { title: "Запишем исходное состояние", description: "На учебных данных зададим внутренние tags и сохраним опись всех полей." },
          { title: "Сравним процессы", description: "Найдём и завершим одну задачу через CLI, затем прочитаем обе новым процессом." },
          { title: "Передадим результат", description: "Сравним поля с описью и запишем фактический итог существующих тестов." },
        ]} />
        <p>Каждый результат станет входом следующего действия. Опись поможет отличить сохранённые данные от одного успешного ответа. При затруднении вернёмся к примеру о настройке, SQL или тестах.</p>
      </Section>
    </RichLesson>
  );
}
