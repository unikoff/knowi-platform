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

const REGISTER_LOOKUP = `import os

tickets = {21: {"item": "штатив", "state": "закрыта"}}

def visible(state):
    return [number for number, ticket in tickets.items() if ticket["state"] == state]

view = os.environ.get("REGISTER_VIEW", "открыта")
print("Стойка отвечает")
print(view, visible(view))
print(tickets.get(21))
print(tickets.get(99))`;

const REGISTER_VIEW = String.raw`$env:REGISTER_VIEW = 'открыта'
python
# Вставьте пример, затем выполните exit() внутри Python.
$env:REGISTER_VIEW = 'закрыта'
python
# Снова вставьте тот же пример и выполните exit().
Remove-Item Env:REGISTER_VIEW`;

const TRACK_DATABASE = `for _, name, path in self.session.execute(text("PRAGMA database_list")):
    if name == "main":
        print("SQLite main:", path, flush=True)`;

const PREPARE_LAB = String.raw`$labPython = (Resolve-Path -LiteralPath '.venv/Scripts/python.exe').Path
$labRoot = Join-Path ([IO.Path]::GetTempPath()) ('planner-lab-' + [guid]::NewGuid())
$labPort = 8079
$labBase = "http://127.0.0.1:$labPort"

if (Get-NetTCPConnection -LocalPort $labPort -State Listen -ErrorAction SilentlyContinue) {
    throw 'Выберите другой свободный порт и повторите блок.'
}
New-Item -ItemType Directory -Path $labRoot | Out-Null

function Initialize-LabDatabase([string] $fileName) {
    $file = Join-Path $labRoot $fileName
    $env:DATABASE_URL = 'sqlite:///' + $file.Replace('\', '/')
    & $labPython -m app.DBase.init_db
    if ($LASTEXITCODE -ne 0) { throw "Не удалось подготовить $fileName" }
}

function Start-LabApi([string] $runName) {
    $stdout = Join-Path $labRoot "$runName.out"
    $stderr = Join-Path $labRoot "$runName.err"
    Start-Process -FilePath $labPython -ArgumentList '-u','-m','uvicorn','app.api:app','--host','127.0.0.1','--port',$labPort -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
}

$labApi = $null
foreach ($name in 'atlas.sqlite3', 'compass.sqlite3') {
    Initialize-LabDatabase $name
    if ($null -eq $labApi) { $labApi = Start-LabApi 'first' }
}
$labDocs = "$labBase/docs"
$labDocs`;

const START_CLI = String.raw`& $labPython -m app.main`;

const INSPECT_LAUNCH = String.raw`Get-Content -LiteralPath (Join-Path $labRoot 'first.out')
& $labPython -c 'from app.core.config import load_settings; print(load_settings().database_url)'`;

const READ_ONE = String.raw`$labFile = Read-Host 'Полный путь к исследуемой SQLite'
$labId = Read-Host 'Фактический id'
@'
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

file = Path(sys.argv[1]).resolve()
with closing(sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)) as db:
    sql = "SELECT id, title, priority, is_done FROM tasks WHERE id = ?"
    print(db.execute(sql, (int(sys.argv[2]),)).fetchone())
'@ | & $labPython -X utf8 - $labFile $labId`;

const STOP_API = String.raw`$labApi.Id
if (-not $labApi.HasExited) { Stop-Process -InputObject $labApi }
$labApi.WaitForExit()`;

const START_NEW_API = String.raw`$labApi = Start-LabApi 'second'
$labApi.Id`;

const REGISTER_COMMIT = `import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

with TemporaryDirectory() as folder:
    journal = Path(folder) / "tickets.sqlite3"
    with closing(sqlite3.connect(journal)) as writer:
        writer.execute("CREATE TABLE tickets (id INTEGER PRIMARY KEY, state TEXT)")
        writer.execute("INSERT INTO tickets VALUES (21, 'на выдаче')")
        writer.commit()
        writer.execute("UPDATE tickets SET state = ? WHERE id = ?", ("возврат", 21))
        print(writer.execute("SELECT state FROM tickets WHERE id = ?", (21,)).fetchone())
        writer.commit()

    with closing(sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True)) as reader:
        print(reader.execute("SELECT id, state FROM tickets WHERE id = ?", (21,)).fetchone())`;

const READ_SUMMARY = String.raw`$labFile = Read-Host 'Полный путь к проверенной SQLite'
@'
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

file = Path(sys.argv[1]).resolve()
with closing(sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)) as db:
    counts = dict(db.execute("SELECT is_done, COUNT(*) FROM tasks GROUP BY is_done"))
open_count = counts.get(0, 0)
done_count = counts.get(1, 0)
print({"total": open_count + done_count, "open": open_count, "done": done_count})
'@ | & $labPython -X utf8 - $labFile
Invoke-RestMethod "$labBase/stats"`;

const SHARED_SMOKE = String.raw`& $labPython -m pytest -q tests/test_storage_availability.py::test_smoke_api_and_cli_read_the_same_task`;

export function Lesson79() {
  return (
    <RichLesson>
      <RichHero
        title="Проверяем одну историю API → CLI → новый процесс"
        intro="Planner создал задачу через API. CLI запустился, но не нашёл её. Выясним, где расходятся показания, и проследим ту же запись после исправления и перезапуска."
      />

      <Section number="00" title="Вступление">
        <LinkedNotes items={[
          { title: "Точка старта", description: "После перехода на SQLite вы умеете находить выбранную задачу через штатные API и CLI." },
          { title: "Новый вопрос", description: "Как выбрать следующую проверку, если API создал задачу, а CLI сообщает, что её нет?" },
          { title: "Результат", description: "Вы найдёте причину расхождения и подтвердите изменённое состояние исходной записи в новом процессе API." },
        ]} />
        <p>Начнём с короткого примера. Он покажет, когда промах объясняется тем, что выводит список, и когда нужно искать дальше.</p>
      </Section>

      <Section number="01" title="Что именно доказала проверка">
        <Lead>Проверка доступности показывает, что процесс отвечает. Адресное чтение проверяет, видит ли он запись с конкретным номером.</Lead>
        <p>На стойке выдачи можно открыть список заявок или спросить про один штатив. Штатив №21 вернули, поэтому в списке открытых заявок его больше нет. Запрос по номеру всё ещё находит карточку и показывает её состояние.</p>
        <CodeBlock caption="Реестр выдачи. Пример можно вставить в Python" code={REGISTER_LOOKUP} />
        <p><code>visible</code> выбирает карточки со статусом из <code>view</code>. По умолчанию это «открыта»: список пуст. <code>tickets.get(21)</code> находит закрытую карточку, а запрос №99 возвращает <code>None</code>. Для №21 помогает смена фильтра. Промах по №99 произошёл при прямом чтении, где этот фильтр вообще не участвует.</p>
        <QuizCard
          question="Стойка отвечает, но в списке закрытых нет заявки №34. Что проверить следующим?"
          options={["Ещё раз проверить доступность стойки", "Прочитать заявку №34 по номеру", "Сразу переключить список на открытые"]}
          correctIndex={1}
          explanation="Прямое чтение показало заявку №34 со статусом «открыта». Это объясняет отсутствие в списке закрытых. Индикатор доступности не читает карточку, а смена списка до проверки не объясняет исходный результат."
        />
        <p>В Planner <code>GET /health</code> подтверждает ответ HTTP-процесса, а <code>GET /tasks</code> может отбирать задачи по параметрам. Чтобы сравнить показания об одной задаче, пригодятся фактический <code>id</code> из ответа на создание, <code>GET /tasks/&#123;id&#125;</code> и команда <code>find</code>. Последний подтверждённый факт и первое несовпадение запишите отдельно.</p>
      </Section>

      <Section number="02" title="Где процесс читает и откуда взял адрес">
        <Lead>Действующая настройка влияет на работу конкретного процесса. Если программе передать другое значение перед новым запуском, уже запущенный процесс продолжит работу со своей настройкой.</Lead>
        <p>У стойки выдачи смена выбирает, какой список открывать. В примере выше <code>os.environ.get</code> берёт это значение из окружения Python. Проверьте оба запуска из PowerShell. Каждый раз вставляйте тот же код реестра.</p>
        <CodeBlock caption="Два отдельных запуска реестра" code={REGISTER_VIEW} language="powershell" />
        <p>При «открыта» получаем <code>[]</code>, при «закрыта» получаем <code>[21]</code>. Прямое чтение №21 даёт одну и ту же карточку. В Planner переменная <code>DATABASE_URL</code> выбирает SQLite-файл. Её задают в PowerShell до запуска: новый дочерний Python унаследует значение. Настройка из <code>.env</code> используется, когда значение не задано в окружении.</p>

        <h3>Подготовьте учебный запуск</h3>
        <p>Откройте отдельный PowerShell из <code>project/</code> своего учебного Planner, где уже работают API и CLI на SQLite. Возьмите тот же Python из <code>.venv</code>. Используем только новые синтетические файлы. Подготовка не подключает обычную базу и не требует нового проекта или импорта.</p>
        <p>В <code>app/DBase/repositories.py</code> найдите чтение одной задачи в <code>SqlTaskRepository.get</code>. Добавьте <code>from sqlalchemy import text</code> к импортам, если <code>text</code> ещё не импортирован. Сразу после строки <code>row = self.session.get(TaskRow, task_id)</code>, с тем же отступом, добавьте этот временный вывод:</p>
        <CodeBlock caption="Путь из той же Session, которая прочитала задачу" code={TRACK_DATABASE} />
        <p><code>PRAGMA database_list</code> возвращает номер, имя и путь открытых баз. Строка <code>main</code> относится к основной базе. Вывод с <code>flush=True</code> сразу попадает в локальный терминал или файл вывода API. Он не меняет ответ и срабатывает в том числе при промахе по <code>id</code>.</p>
        <p>Вставьте следующий блок целиком. Первая функция создаёт схему на указанной временной цели через знакомый <code>init_db</code>. Вторая запускает API на loopback и возвращает объект именно этого процесса. Два файла и журналы останутся в новом временном каталоге. Запустите подготовку один раз. При неудаче остановите свой API, если он уже запущен, прежде чем повторять блок.</p>
        <CodeBlock caption="PowerShell из project/. Подготовка двух файлов и запуск API" code={PREPARE_LAB} language="powershell" />
        <p>Убедитесь, что <code>$labApi.HasExited</code> равно <code>False</code> и по адресу из <code>$labDocs</code> открылась документация Planner. Если процесс завершился, прочитайте <code>first.err</code> в <code>$labRoot</code>. В документации <code>GET /health</code> возвращает <code>200</code> и <code>&#123;"status":"ok"&#125;</code>. Для <code>POST /tasks</code> нужны название <code>title</code> и приоритет <code>priority</code> от 1 до 5. Ответ <code>201</code> содержит собственный <code>id</code> задачи, название, приоритет и <code>is_done</code>. При выполнении <code>GET /tasks/&#123;id&#125;</code> впишите номер в поле пути.</p>

        <h3>Способы наблюдения</h3>
        <p>Запускайте CLI из того же учебного PowerShell. Команды <code>find</code> и <code>done</code> вводятся в меню программы. После каждой программа спрашивает положительный <code>id</code> на следующей строке. <code>exit</code> возвращает в PowerShell.</p>
        <CodeBlock caption="Открыть CLI" code={START_CLI} language="powershell" />
        <p>После чтения задачи <code>SQLite main</code> виден прямо в CLI или в <code>first.out</code> после HTTP-запроса. Вторая команда ниже показывает адрес, который получит новый процесс из этого терминала. Она не заглядывает в уже запущенный API. Запишите, какой интерфейс дал каждое показание. По коду подготовки можно предположить причину, а проверяемый вывод требует реальных путей и чтения того же номера.</p>
        <CodeBlock caption="Прочитать журнал API и проверить настройку текущей оболочки" code={INSPECT_LAUNCH} language="powershell" />
        <p>Когда нужно отдельно подтвердить содержимое файла, возьмите полный путь из наблюдения и фактический <code>id</code>. Команда попросит оба значения. <code>mode=ro</code> открывает существующий файл для чтения и выдаёт ошибку, если файл отсутствует. Знак <code>?</code> получает значение из кортежа <code>(id,)</code>. Прочтите четыре поля или <code>None</code>, если строки нет. В SQLite статус записан числом: <code>0</code> соответствует <code>false</code>, <code>1</code> соответствует <code>true</code>.</p>
        <CodeBlock caption="Прочитать строку из выбранного файла" code={READ_ONE} language="powershell" />
        <p>Чтобы изменить настройку учебного запуска, задайте <code>$env:DATABASE_URL</code> до старта нужного процесса. На Windows к абсолютному пути с прямыми слешами, например <code>C:/Temp/example/loans.sqlite3</code>, добавляют <code>sqlite:///</code>. После диагностики удалите только временный вывод и добавленный для него импорт, если он больше не нужен. Уже загруженный API продолжит работать до остановки.</p>
      </Section>

      <Section number="03" title="Как проверить сохранение после перезапуска">
        <Lead>Фиксация изменения сохраняет его в базе. Новый читатель увидит итог, когда откроет тот же файл после фиксации.</Lead>
        <p>В журнале выдачи отметку о возврате штатива читает следующая смена. В примере первое соединение записывает возврат, а второе открывает тот же журнал заново. <code>closing</code> закрывает соединения, <code>TemporaryDirectory</code> убирает учебный каталог после опыта.</p>
        <CodeBlock caption="Штатив №21 до и после фиксации" code={REGISTER_COMMIT} />
        <p>Первый вывод содержит <code>('возврат',)</code>, второй показывает <code>(21, 'возврат')</code>. Подумайте, что изменится, если убрать только второй <code>writer.commit()</code>. После попытки можно повторно запустить пример с этой правкой.</p>
        <QuizCard
          question="Обновление выполнено, но второй commit пропущен. Что прочитает новое соединение?"
          options={["Возврат, как и писатель", "На выдаче, как до обновления", "Строку №21 уже нельзя прочитать"]}
          correctIndex={1}
          explanation="Первый commit сохранил исходную строку. Писатель увидел возврат внутри своей транзакции, но закрыл соединение без фиксации обновления. Следующий читатель снова видит «на выдаче»."
        />
        <p>В Planner вы используете отдельный API-процесс. При сравнении сохраните исходные поля и состояние после изменения, остановите именно свой API и запустите его заново. <code>Id</code> здесь означает номер процесса Windows. Номер задачи проверяется отдельно в ответе API.</p>
        <Diagram caption="От ответа стойки к сохранённой отметке" orientation="vertical">
          <DiagramFlow label="Четыре наблюдения о штативе №21" orientation="vertical">
            <DiagramNode title="Стойка отвечает">Известна доступность.</DiagramNode>
            <DiagramArrow label="Читаем карточку по номеру" />
            <DiagramNode title="№21 на выдаче">Известно исходное состояние.</DiagramNode>
            <DiagramArrow label="Отмечаем возврат и читаем с другой стойки" />
            <DiagramNode title="№21 возвращён">Другая стойка видит смену статуса.</DiagramNode>
            <DiagramArrow label="Открываем тот же журнал в следующую смену" />
            <DiagramNode title="№21 по-прежнему возвращён">Новый читатель подтверждает сохранённую отметку.</DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>Остановите старый процесс и дождитесь завершения. Для повторного запуска задайте проверенный адрес, как в разделе «Где процесс читает и откуда взял адрес», и используйте новое имя журнала. На существующем учебном файле повторять <code>init_db</code> для этой проверки не нужно. После проверки завершите CLI через <code>exit</code>, остановите свой последний API тем же способом и закройте учебный терминал.</p>
        <CodeBlock caption="Остановить именно предыдущий API" code={STOP_API} language="powershell" />
        <CodeBlock caption="Запустить новый API после проверки настройки" code={START_NEW_API} language="powershell" />

        <h3>Отдельно сверяем сводку</h3>
        <p>Ответ по №21 сообщает только об одной карточке. Чтобы проверить сводку, считайте все записи того же файла по двум группам. <code>GROUP BY is_done</code> даёт отдельные количества для <code>0</code> и <code>1</code>. <code>dict</code> позволяет взять счётчик по значению статуса, а <code>get</code> вернёт ноль для отсутствующей группы. В <code>/stats</code> внутренний счётчик <code>all</code> называется <code>total</code>.</p>
        <p>Запустите готовую сверку, когда новый API уже прочитал исходную задачу. Подставьте в запрос терминала проверенный путь к тому же существующему файлу. Сравните три поля по именам. Если открытых и завершённых поровну, перестановка счётчиков осталась бы незаметной. Поэтому для сводки возьмите две открытые задачи и одну завершённую.</p>
        <CodeBlock caption="Независимый подсчёт SQLite и показания GET /stats" code={READ_SUMMARY} language="powershell" />
        <p>Если числа разошлись, сначала проверьте адрес и строки выбранного файла. Затем сравните именованные группы с ответом <code>/stats</code>: сумма групп должна попасть в <code>total</code>.</p>

        <h3>Повторяем прежние проверки</h3>
        <p>После снятия временного вывода запустите адресные проверки из своей прошлой практики. Общий контроль ниже проверяет обычное чтение одной задачи через оба интерфейса на временной SQLite. Из своей заметки возьмите ещё проверки отказов и отката, а также сохранённую проверку ответа и места SQL-отбора. Например, если вы выбрали имя <code>tests/test_task_selection_contract.py</code>, передайте этот файл команде <code>python -m pytest -q</code>. Используйте тот же Python из venv и проверьте фактические имена в своём <code>tests/</code>.</p>
        <CodeBlock caption="Общий контроль для текущей версии Planner" code={SHARED_SMOKE} language="powershell" />
        <p>Если вы работаете на прежней сборке, контроль CLI называется <code>tests/test_sql_runtime.py::test_cli_main_uses_configured_sql_database_not_json</code>. Запустите его из <code>project/</code> тем же <code>python -m pytest -q</code>. Прежний тест общего пути дополняет наблюдения о своей записи.</p>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Найдём первое расхождение", description: "Запишем фактический номер задачи и выясним, какое чтение впервые показывает другой результат." },
          { title: "Подтвердим причину и исправим её", description: "Соберём улики о запусках и перечитаем исходную задачу через оба интерфейса." },
          { title: "Проверим тот же id в новом процессе", description: "Изменим статус через CLI, проверим запись после перезапуска API и внесём проверенные команды в README." },
        ]} />
        <p>Сохраните краткие наблюдения и инструкцию запуска. Они пригодятся, когда вы будете проверять свой README из чистой копии.</p>
      </Section>
    </RichLesson>
  );
}
