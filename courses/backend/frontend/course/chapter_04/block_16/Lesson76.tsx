import {
  CodeBlock,
  Diagram,
  DiagramArrow,
  DiagramFlow,
  DiagramNode,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const CODE_BADGE = String.raw`from types import SimpleNamespace

receipt = SimpleNamespace(number=0x030E)
parcel = SimpleNamespace(receipt=receipt)
number = getattr(parcel.receipt, "number", None)

print("Номер квитанции:", number)
print("Категория:", number & 0xFF)

other_receipt = SimpleNamespace(number=127)
print("Другая категория:", other_receipt.number & 0xFF)

blank_receipt = SimpleNamespace()
missing = getattr(blank_receipt, "number", None)
print("Номер пустой квитанции:", missing)
print("Можно выделить байт:", isinstance(missing, int))`;

const DRIVER_WRAPPER = String.raw`import sqlite3
from sqlalchemy.exc import DBAPIError

driver = sqlite3.OperationalError("Учебная квитанция")
driver.sqlite_errorcode = 517

record = DBAPIError(None, None, driver)
print(getattr(record.orig, "sqlite_errorcode", None))

blank = DBAPIError(None, None, sqlite3.OperationalError("Без номера"))
print(getattr(blank.orig, "sqlite_errorcode", None))`;

const COUNTER_FILES = String.raw`import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

lab = TemporaryDirectory()
folder = lab.name
registry = Path(folder) / "loans.sqlite3"
inventory = Path(folder) / "inventory.sqlite3"
with closing(sqlite3.connect(registry)) as db, db:
    db.execute("CREATE TABLE loans (number INTEGER, item TEXT)")
    db.execute("INSERT INTO loans VALUES (8, 'Штатив')")
with closing(sqlite3.connect(inventory)) as db, db:
    db.execute("CREATE TABLE equipment (item TEXT)")`;

const COUNTER_ROUTE = String.raw`from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, text

class CounterClosed(Exception):
    pass

files = {"registry": registry, "inventory": inventory}
counter = FastAPI()

@counter.exception_handler(CounterClosed)
async def closed(_request: Request, _error: CounterClosed):
    return JSONResponse({"detail": "Пункт закрыт"}, status_code=503)

@counter.get("/loans/{number}")
def loan(number: int, source: str = "registry"):
    if source == "closed":
        raise CounterClosed()
    engine = create_engine(f"sqlite:///{files[source].as_posix()}")
    try:
        with engine.connect() as connection:
            query = text("SELECT item FROM loans WHERE number = :number")
            return {"item": connection.execute(query, {"number": number}).scalar_one()}
    finally:
        engine.dispose()`;

const COUNTER_RESPONSES = String.raw`from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError

with TestClient(counter, raise_server_exceptions=False) as client:
    for suffix in ("", "?source=closed", "?source=inventory"):
        response = client.get("/loans/8" + suffix)
        print(response.status_code, response.text)

with TestClient(counter) as client:
    try:
        client.get("/loans/8?source=inventory")
    except DBAPIError as error:
        print(type(error).__name__, error.orig.sqlite_errorcode, str(error.orig))
lab.cleanup()`;

const PREPARE = String.raw`$py = '.\.venv\Scripts\python.exe'
$beforeUrl = [Environment]::GetEnvironmentVariable('DATABASE_URL')
$workingUrl = Read-Host 'Исправный DATABASE_URL из заметки'
$healthyId = [int](Read-Host 'Id известной задачи в рабочей SQLite')
$env:DATABASE_URL = $workingUrl

$lab = Join-Path (Get-Location).Path ('data\storage-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fffffff'))
New-Item -ItemType Directory -Path $lab -ErrorAction Stop | Out-Null
$blockedFile = Join-Path $lab 'missing-parent\planner.sqlite3'
$emptyFile = Join-Path $lab 'without-tasks.sqlite3'

@'
import sqlite3
import sys
from contextlib import closing

with closing(sqlite3.connect(sys.argv[1])) as db, db:
    db.execute("PRAGMA user_version = 1")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    table = db.execute("SELECT name FROM sqlite_schema WHERE name = 'tasks'").fetchone()
    print("Версия:", version, "Таблица tasks:", table)
'@ | & $py -X utf8 - $emptyFile`;

const START_PROBE = String.raw`$probe = @'
import sys
from fastapi.testclient import TestClient
from sqlalchemy.exc import DBAPIError
from app.api import app
from app.DBase.repositories import SqlTaskRepository

mode, task_id = sys.argv[1], int(sys.argv[2])
with TestClient(app, raise_server_exceptions=False) as client:
    if mode == "http":
        response = client.get(f"/tasks/{task_id}")
        print(response.status_code)
        print(response.text)
    elif mode == "reason":
        with app.state.session_factory() as session:
            engine = session.get_bind()
            try:
                SqlTaskRepository(session).get(task_id)
            except DBAPIError as error:
                print(getattr(error.orig, "sqlite_errorcode", None), str(error.orig))
        engine.dispose()
'@

$probe | & $py -X utf8 - http $healthyId`;

const FIRST_FAULT = String.raw`$caseId = [int](Read-Host 'Id контрольной задачи в архиве')
$archive = Read-Host 'Путь к прежнему архиву в учебной копии'
@((Get-Content -LiteralPath $archive -Raw | ConvertFrom-Json)) |
    Where-Object id -eq $caseId | Select-Object id, title
Test-Path -LiteralPath (Split-Path -Parent $blockedFile)
$env:DATABASE_URL = "sqlite:///$($blockedFile.Replace('\', '/'))"
$probe | & $py -X utf8 - reason $caseId
$probe | & $py -X utf8 - http $caseId
& $py -m app.main`;

const SECOND_FAULT = String.raw`$env:DATABASE_URL = "sqlite:///$($emptyFile.Replace('\', '/'))"
$probe | & $py -X utf8 - http $caseId
$probe | & $py -X utf8 - reason $caseId

$env:DATABASE_URL = $workingUrl
$probe | & $py -X utf8 - http $healthyId
$env:DATABASE_URL = $beforeUrl`;

const SECOND_COUNTER = String.raw`import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

reader = """
import os
import sys
from pathlib import Path

number = input().strip()
path = Path(os.environ["COUNTER_JOURNAL"])
print(number, path.read_text(encoding="utf-8").strip())
"""

with TemporaryDirectory() as folder:
    journal = Path(folder) / "journal.txt"
    copy = Path(folder) / "copy.txt"
    journal.write_text("out", encoding="utf-8")
    copy.write_text("out", encoding="utf-8")
    journal.write_text("returned", encoding="utf-8")

    child_env = {**os.environ, "COUNTER_JOURNAL": str(journal)}
    receipt = subprocess.run(
        [sys.executable, "-c", reader],
        input="8\n",
        env=child_env,
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    actual = receipt.stdout.strip()
    print(actual)
    assert actual == "8 returned"`;

export function Lesson76() {
  return (
    <RichLesson>
      <RichHero
        title="Распознаём недоступное хранилище"
        intro="Различим две ошибки SQLite, выберем безопасные ответы и снова прочитаем задачу из рабочего Planner."
      />

      <Section number="00" title="Вступление">
        <Lead>В исправной учебной сборке Planner читает задачи из SQLite. Когда она недоступна, приложение отказывает даже при читаемом старом архиве. Теперь разберёмся, что именно привело к отказу.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Обычные API и CLI читают одну учебную SQLite. Адрес и контрольный id сохранены." },
          { title: "Новый вопрос", description: "Как отличить отказ открытия файла от ошибки уже открытой базы?" },
          { title: "Результат", description: "По коду причины различим HTTP 503 и 500, а затем подтвердим новое успешное чтение." },
        ]} />
        <p>Сначала прочитаем то, что сообщил драйвер SQLite. Затем посмотрим, как эта причина меняет ответ API и поведение CLI.</p>
      </Section>

      <Section number="01" title="Как прочитать код причины">
        <Lead>Когда место сбоя важно, удобно иметь номер причины. Из него можно узнать категорию ошибки и отдельно сохранить подробность.</Lead>
        <p>Представим пункт выдачи оборудования. К посылке прикреплена квитанция: последние две клетки номера отведены под категорию, а клетки слева уточняют причину. SQLite хранит общую категорию в последних восьми битах числового кода. Эти восемь бит называются <strong>младшим байтом</strong>.</p>
        <p>Префикс <code>0x</code> обозначает шестнадцатеричную запись. Каждая её цифра занимает четыре бита, поэтому младший байт записи <code>0x030E</code> занимает две позиции <code>0E</code>. Маска <code>0xFF</code> заполнена единицами ровно в этих восьми позициях. Операция <code>&amp;</code> оставляет биты, которые есть в обоих числах. Так <code>number &amp; 0xFF</code> оставляет код категории.</p>
        <p>Сохраним пример как <code>counter_receipt.py</code> в свободном месте учебного <code>project/</code> и запустим <code>.venv/Scripts/python.exe counter_receipt.py</code> из этой папки. <code>SimpleNamespace</code> здесь работает как маленький объект с именованными полями: сначала создаём квитанцию, потом кладём её в посылку.</p>
        <CodeBlock code={CODE_BADGE} caption="Номер и категория одной квитанции" />
        <p>Сначала получаем <code>782</code>, затем <code>14</code>. Код 127 уже помещается в один байт и остаётся 127. У пустой квитанции <code>getattr(объект, имя, запасное_значение)</code> возвращает переданное <code>None</code>, а <code>isinstance(missing, int)</code> даёт <code>False</code>. Это наблюдение говорит, что числовое преобразование сейчас неприменимо.</p>

        <p>В Planner наружное исключение SQLAlchemy называется <code>DBAPIError</code>. Его поле <code>orig</code> хранит исходное исключение, которое вернул драйвер. При ошибке SQLite числовую причину ищем в <code>orig.sqlite_errorcode</code>. Python добавляет это поле ошибкам, пришедшим из библиотеки SQLite, начиная с версии 3.11. У созданного вручную исключения поле может отсутствовать.</p>
        <Diagram caption="От исключения до категории причины">
          <DiagramFlow label="Как прочитать категорию SQLite">
            <DiagramNode title="Обёртка SQLAlchemy"><p><code>DBAPIError</code></p></DiagramNode>
            <DiagramArrow label="orig" />
            <DiagramNode title="Ошибка драйвера"><p><code>sqlite_errorcode</code>: 782</p></DiagramNode>
            <DiagramArrow label="Маска 0xFF" />
            <DiagramNode title="Основной код"><p><code>0x0E</code>: 14</p></DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>Для ручной пробы можно создать исключение без базы. Возьмём другую учебную причину: число 517 и обёртку без номера. В <code>DBAPIError(None, None, driver)</code> первые два места отведены под SQL и параметры. В пробе они пусты. Запустим этот фрагмент в <code>.venv/Scripts/python.exe</code> из <code>project/</code>.</p>
        <CodeBlock code={DRIVER_WRAPPER} caption="Две учебные обёртки для ручной пробы" />
        <p>Видим <code>517</code> и <code>None</code>. SQLite называет категорию 14 <code>SQLITE_CANTOPEN</code>: файл открыть не удалось. Имена классов вроде <code>OperationalError</code> могут совпадать у разных причин. Для вывода нужна именно числовая категория.</p>
        <QuizCard
          question="Что получим у трёх новых квитанций?"
          description="Слева направо: 0x050E, 0x0205 и квитанция без номера. Определим категорию и решим, подтверждён ли отказ открытия."
          options={[
            { title: "1294, 517, None", description: "Сравним полные номера." },
            { title: "14, 5, None", description: "Отказ открытия подтверждён у первой квитанции." },
            { title: "14, 14, 0", description: "У обоих расширенных номеров одна категория, пустой номер считаем нулём." },
          ]}
          correctIndex={1}
          orientation="vertical"
          explanation="У 0x050E справа 0E, это 14. У 0x0205 справа 05, это 5. Старшие цифры не определяют категорию, а для отсутствующего номера маску вычислять нельзя."
        />
      </Section>

      <Section number="02" title="Где причина становится ответом">
        <Lead>Причина и сообщение для клиента нужны разным людям. На стойке выдачи посетителю достаточно узнать, что пункт закрыт. По внутренней причине специалист узнает, какой журнал или раздел проверять.</Lead>
        <p><strong>Граница обработки</strong> работает в месте, где одна часть программы решает, что получит вызывающая часть. API превращает распознанную причину в HTTP-ответ. CLI может показать короткую фразу и снова принять команду.</p>
        <p>В опыте одна стойка открыта, другая подаёт известный сигнал <code>CounterClosed</code>. Есть и третий вариант: мы открываем ведомость оборудования вместо журнала выдач. Оба файла читаются, но таблица <code>loans</code> есть только в журнале выдач. Посмотрим опыт по частям. Первая часть создаёт два файла во временной папке. Если хотите повторить наблюдение, выполняйте фрагменты по порядку в одной учебной сессии Python.</p>
        <CodeBlock code={COUNTER_FILES} caption="Два открываемых файла стойки" />
        <p>Следующая часть опыта выбирает файл по <code>source</code> и читает одну выдачу. В запросе <code>:number</code> обозначает место для номера, который передаём отдельным словарём. <code>scalar_one()</code> забирает одно значение, когда запрос вернул ровно одну строку. Декоратор <code>exception_handler</code> связывает тип сигнала стойки с фиксированным ответом. <code>finally</code> освобождает Engine даже при ошибке запроса.</p>
        <CodeBlock code={COUNTER_ROUTE} caption="Запрос к журналу и ответ закрытой стойки" />
        <p>Теперь отправим один и тот же запрос в трёх состояниях. Сначала сравним внешние ответы, затем повторим запрос к ведомости и посмотрим исходное исключение локально.</p>
        <CodeBlock code={COUNTER_RESPONSES} caption="Три ответа и одна локальная причина" />
        <p>Первая строка даёт <code>200</code> и «Штатив». Во второй видим <code>503</code> и «Пункт закрыт». Третья показывает <code>500 Internal Server Error</code>. У внутреннего прогона видна причина <code>OperationalError 1 no such table: loans</code>: соединение открылось, а сбой произошёл на запросе к таблице.</p>
        <p><code>TestClient</code> проводит запрос через приложение без запуска сервера. Вход в <code>with</code> запускает жизненный цикл приложения. С <code>raise_server_exceptions=False</code> клиент показывает публичный 500. В обычном режиме необработанная ошибка поднимается в тестовом коде, и мы можем прочитать <code>orig</code> локально.</p>
        <p>Внутри <code>except</code> запись <code>raise</code> повторно поднимает то же исключение. Запись <code>raise НоваяОшибка(...) from error</code> поднимает новый тип и сохраняет связь с исходной причиной. Публичный обработчик может выбрать постоянный текст по новому типу. Путь, SQL и параметры ему для этого не нужны.</p>

        <p><strong>Где искать это в Planner.</strong> При <code>GET /tasks/&#123;id&#125;</code> функция <code>get_db(request)</code> отдаёт Session через <code>yield</code>. Уже после этого репозиторий выполняет запрос. При <code>find</code> внутри CLI такую же роль играет <code>sql_service_context(session_factory)</code>, пока <code>run</code> выполняет команду. Ошибка возвращается в действующий контекст после начала работы. Одна только сборка Engine или Session, как и <code>GET /health</code>, ещё не выполняет запроса к файлу.</p>

      </Section>

      <Section number="03" title="Как два запуска подтверждают общее хранилище">
        <Lead>Две стойки могут показать одинаковый старый талон из двух копий. Свежая отметка, сделанная только в одном файле, помогает различить их.</Lead>
        <p>В учебном журнале выдача 8 сначала была открыта. Мы отметим возврат в одном файле, а отдельному читателю передадим его адрес и номер. В прежней копии останется <code>out</code>. Сохраним код как <code>counter_reader.py</code> в свободном месте <code>project/</code> и выполним <code>.venv/Scripts/python.exe counter_reader.py</code>.</p>
        <p><code>subprocess.run</code> запускает новый Python через <code>sys.executable</code>. С <code>-c</code> он исполняет строку <code>reader</code>; <code>input</code> передаёт номер отдельной строкой сразу в этот процесс. <code>env</code> получает отдельный словарь с адресом журнала. Результат читаем из <code>stdout</code>, который собирается с <code>capture_output=True</code>. <code>check=True</code> сообщает о ненулевом коде выхода. Сами слова, найденные читателем, проверяем отдельно.</p>
        <CodeBlock code={SECOND_COUNTER} caption="Второй процесс видит новую отметку" />
        <p>Получаем <code>8 returned</code>. Если заменить только <code>str(journal)</code> на <code>str(copy)</code>, процесс штатно завершится с <code>8 out</code>. <code>assert</code> сравнивает полученную строку с ожидаемой и остановит проверку. Поэтому один успешный запуск ещё не доказывает, что читатель смотрел в нужный файл.</p>
        <p>В проектном тесте принцип тот же. Pytest передаёт <code>tmp_path</code>, отдельный каталог для одного прогона. Из него берём путь к новой SQLite. Тот же словарь дочернего окружения используем для явного <code>-m app.DBase.init_db</code>, для короткой HTTP-пробы и для обычного CLI. Адрес задан ещё до импорта <code>app.api</code> или старта <code>TestClient</code>. Когда фабрика создаётся при старте приложения, она сразу читает временный адрес. Настройки родительского процесса остаются прежними.</p>
        <p>Короткую пробу HTTP запустим через <code>-c</code>, как читателя отметки. Внутри обычного <code>TestClient</code> смотрим <code>status_code</code> и читаем поля записи через <code>response.json()</code>. Когда нужно вернуть id и название в pytest, дочерний процесс печатает их одной JSON-строкой через <code>json.dumps</code>, а родитель читает её через <code>json.loads</code>. Статус POST 201 показывает успешное создание, а GET 200 и поля ответа подтверждают чтение именно созданной записи. Для обычного CLI запускаем Python с аргументами <code>-m app.main</code> из <code>project/</code>. На вход передаём три строки: <code>find</code>, полученный id и <code>exit</code>. В дочерний <code>env</code> кладём тот же <code>DATABASE_URL</code> и <code>PYTHONIOENCODING=utf-8</code>, чтобы команды и вывод читались согласованно. Для вывода одной задачи формат CLI начинается с <code>[номер] название</code>. Выберем проверку, которая отличит эту строку от меню и сообщений об ошибке.</p>
        <p>Pytest умеет запускать одну функцию по адресу <code>путь_к_файлу::имя_функции</code>. Добавим свой тест в проект. Его полный адрес приведён в карточке, чтобы тот же опыт можно было повторить позже.</p>
      </Section>

      <Section number="04" title="Подготавливаем два учебных отказа">
        <p>Следующие команды нужны в практике. Выполняем их в одной вкладке PowerShell из <code>project/</code> в изолированной учебной копии. Понадобится установленный по README <code>requirements-dev.txt</code> с TestClient. Первая группа сохраняет текущее значение <code>DATABASE_URL</code>, просит исправный адрес и id из заметки и создаёт новый каталог опыта. Для отказа открытия оставляем родителя <code>missing-parent</code> отсутствующим. Отдельный открываемый файл проверяем по версии схемы и отсутствию <code>tasks</code>.</p>
        <CodeBlock code={PREPARE} language="powershell" caption="PowerShell · три адреса для наблюдений" />
        <p><code>New-Item</code> остановит подготовку, если каталог уже занят. Текст между <code>@'</code> и <code>'@</code> передаётся Python через <code>|</code>. Аргумент <code>-</code> читает этот код из терминала, <code>-X utf8</code> выбирает UTF-8, а следующий аргумент доступен через <code>sys.argv[1]</code>. Ждём <code>Версия: 1 Таблица tasks: None</code>. Файл действительно открывается, но таблица отсутствует. Явный <code>init_db</code> здесь изменил бы условия опыта.</p>
        <p>Теперь один раз сохраним команду чтения в переменную <code>$probe</code>. Режим <code>http</code> печатает внешний ответ штатного GET. Режим <code>reason</code> выполняет чтение того же id через SQL-репозиторий и печатает число и короткую причину SQLite локально. Это позволяет увидеть исходное исключение до HTTP-обработчика. Новый запуск Python получает выбранный адрес до импорта <code>app.api</code>. Оба режима пользуются фабрикой из обычного приложения.</p>
        <CodeBlock code={START_PROBE} language="powershell" caption="PowerShell · готовая команда и первый GET по исправному адресу" />
        <p>Сначала ожидаем <code>200</code> и знакомые id и название. Если контроль не совпал, проверяем заметку, URL и синтетические данные. В отдельной учебной копии проверим, что по прежнему адресу читается архив с задачей 901 и заметным названием, например <code>ARCHIVE_CONTROL_901</code>. Оставляем его доступным. В команду вводим id-маркер из своей заметки. <code>Get-Content</code> читает архив, <code>ConvertFrom-Json</code> разбирает его, а <code>Where-Object</code> выбирает задачу по id. Перед вызовами GET запишем найденные id и название. Проверка <code>Test-Path</code> для отсутствующего родителя должна вывести <code>False</code>.</p>
        <CodeBlock code={FIRST_FAULT} language="powershell" caption="PowerShell · отсутствующий родитель SQLite и обычный CLI" />
        <p>Внутри меню CLI вводим <code>find</code>, id-маркер и затем <code>exit</code> после возвращения меню. У отказного HTTP-ответа и сообщения CLI сверяем отсутствие архивной задачи. Читаемый маркер нужен как положительный контроль: если приложение вернётся к JSON, в результате появится именно эта запись.</p>
        <CodeBlock code={SECOND_FAULT} language="powershell" caption="PowerShell · открываемый файл без tasks и повторный GET" />
        <p>Сначала HTTP показывает общий ответ, а отдельная локальная проба сообщает имя отсутствующей таблицы. Они используют один открываемый файл без <code>tasks</code>. Затем новый процесс получает сохранённый исправный адрес. Последняя строка восстанавливает настройку терминала, даже если до работы значение переменной было пустым.</p>
      </Section>

      <Section number="05" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Классифицируем по коду", description: "Базовый и расширенный коды открытия дадут один результат. Неизвестная и отсутствующая причина останутся внутренними." },
          { title: "Проверим публичный ответ", description: "Штатный GET вернёт безопасный 503, а CLI покажет отказ без архивной записи." },
          { title: "Проверим внутреннюю ошибку и новое чтение", description: "Файл без таблицы даст безопасный 500. После возврата адреса получим 200 и сохраним тест общего чтения." },
        ]} />
        <p>Сохраните исправный адрес и id. По ним можно вернуться к здоровой сборке перед проверкой сбоя записи.</p>
      </Section>
    </RichLesson>
  );
}
