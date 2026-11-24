import {
  BranchExplorer, CodeBlock, Diagram, DiagramArrow, DiagramFlow, DiagramLane, DiagramNode,
  Lead, LinkedNotes, QuizCard, RichHero, RichLesson, Section,
} from "../../shared";

const schemaExample = `from contextlib import closing
from pathlib import Path
from sqlite3 import connect
from tempfile import TemporaryDirectory

with TemporaryDirectory() as folder:
    root = Path(folder)
    snapshot = root / "requests.json"
    snapshot.write_text('[4, 9]', encoding="utf-8")

    goal = root / "cards.sqlite3"
    with closing(connect(goal)) as db:
        db.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY)")
        db.commit()

    uri = goal.as_uri() + "?mode=ro"
    with closing(connect(uri, uri=True)) as db:
        table = db.execute(
            "SELECT name FROM sqlite_master WHERE name = 'requests'"
        ).fetchone()
        count = db.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
        print(snapshot.name, goal.name, table[0], count)`;

const targetExample = `if not has_table:
    print("Стоп: таблица отсутствует")
elif count != 0:
    print("Стоп: в цели есть строки")
else:
    print("Таблица есть, строк 0")`;

const compareExample = `register = {
    4: ("Проверить вход", "доступ", False),
    9: ("Заявка на СКЛАД", "склад", True),
}
copied = {
    9: ("Заявка на СКЛАД", "", True),
    4: ("Проверить вход", "доступ", False),
}

print(len(register), len(copied))
print(register.keys() == copied.keys())
if register.keys() == copied.keys():
    for number in sorted(register):
        for field, expected, actual in zip(
            ("название", "категория", "закрыта"),
            register[number], copied[number],
        ):
            if expected != actual:
                print(number, field, repr(expected), repr(actual))`;

const crossReadExample = `from contextlib import closing
from pathlib import Path
from sqlite3 import connect
from tempfile import TemporaryDirectory

def find_card(path, number):
    with closing(connect(path.as_uri() + "?mode=ro", uri=True)) as reader:
        return reader.execute(
            "SELECT id, title FROM requests WHERE id = ?", (number,)
        ).fetchone()

with TemporaryDirectory() as folder:
    chosen = Path(folder) / "chosen.sqlite3"
    copy = Path(folder) / "copy.sqlite3"
    for path in (chosen, copy):
        with closing(connect(path)) as db:
            db.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY, title TEXT)")
            db.executemany(
                "INSERT INTO requests VALUES (?, ?)",
                [(4, "Проверить вход"), (9, "Заявка на СКЛАД")],
            )
            db.commit()

    with closing(connect(chosen)) as dispatcher:
        saved = dispatcher.execute(
            "INSERT INTO requests (title) VALUES (?)", ("Заявка на СКЛАД",)
        )
        new_number = saved.lastrowid
        dispatcher.commit()

    print(find_card(chosen, new_number))
    print(find_card(copy, 9))
    print(find_card(copy, new_number))`;

const restoreExample = `register = {4: "Проверить вход", 9: "Заявка на СКЛАД"}
trial = {**register, 10: "Заявка на СКЛАД"}

def report(desk, terminal):
    for reader in (desk, terminal):
        print(9 in reader, 10 in reader)

report(trial, trial)
print("После возврата")
report(register, register)
print("Терминал остался в пробном режиме")
report(register, trial)`;

const inspectSchema = `$inspect = @'
from contextlib import closing
from pathlib import Path
from sqlite3 import connect
import sys

file = Path(sys.argv[1]).resolve(strict=True)
with closing(connect(file.as_uri() + "?mode=ro", uri=True)) as db:
    table = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
    ).fetchone()
    print(file)
    if table is None:
        print("tasks отсутствует")
    else:
        print("tasks", db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0])
'@
python -c $inspect "$target"`;

const compareSnapshots = `$compare = @'
from contextlib import closing
from pathlib import Path
from sqlite3 import connect
import json
import sys

from app.models import Task

source, target = (Path(value).resolve(strict=True) for value in sys.argv[1:3])
snapshot = json.loads(source.read_text(encoding="utf-8"))
expected = {}
for item in snapshot:
    task = Task.from_dict(item)
    expected[task.id] = (task.title, task.priority, task.is_done,
                         task.tags, task.title.lower())

with closing(connect(target.as_uri() + "?mode=ro", uri=True)) as db:
    sql = "SELECT id, title, priority, is_done, tags, title_normalized FROM tasks"
    actual = {i: (title, priority, bool(done), json.loads(tags), normalized)
              for i, title, priority, done, tags, normalized in db.execute(sql)}

print("JSON / SQLite", len(expected), len(actual))
print("id только в JSON", sorted(expected.keys() - actual.keys()))
print("id только в SQLite", sorted(actual.keys() - expected.keys()))
fields = ("title", "priority", "is_done", "tags", "title_normalized")
for number in sorted(expected.keys() & actual.keys()):
    if expected[number] == actual[number]:
        print("id", number, "совпадает по всем полям")
    for field, left, right in zip(fields, expected[number], actual[number]):
        if left != right:
            print("id", number, field, repr(left), "!=", repr(right))
'@
python -c $compare "$source" "$target"`;

const inspectJson = `$readJson = @'
from pathlib import Path
import json
import sys

file = Path(sys.argv[1])
number = int(sys.argv[2])
baseline = Path(sys.argv[3])
records = json.loads(file.read_bytes())
print("id присутствует:", any(row["id"] == number for row in records))
print("байты совпадают:", file.read_bytes() == baseline.read_bytes())
'@
python -c $readJson "$file" "$number" "$baseline"`;

export function Lesson74_1({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module}
        title="Репетируем перенос на одной тестовой базе"
        intro="Перенесём учебные задачи в одну SQLite, проверим обе точки входа и вернём обычный Planner к JSON."
      />

      <Section number="00" title="Вступление">
        <Lead>У API и CLI уже есть пробные SQL-сборки. Теперь важно выяснить, при каких условиях им можно доверить один снимок.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Обычные запуски читают JSON. В тесте API получает короткую Session на запрос, а CLI открывает свою на команду." },
          { title: "Новый вопрос", description: "Как проверить, что данные перенеслись целиком, обе точки видят одну базу, а после пробы снова читают JSON?" },
          { title: "Результат", description: "Отдельная пустая цель, решение по сверке, новая запись в пробном API и CLI, подтверждённый возврат к JSON." },
        ]} />
        <p>Сначала сравним результат импорта со снимком. Позже добавим одну задачу, и у нас появится номер, по которому можно различить два режима.</p>
      </Section>

      <Section number="01" title="Источник, адрес и пустая цель">
        <Lead>Снимок хранит данные в том виде, в каком их взяли для переноса. Цель принимает их в отдельную таблицу.</Lead>
        <p>Представьте ведомость с заявками 4 и 9. Для копий завели новую картотеку с подписанными полями. Ведомость заполнена, а в картотеке уже есть место для карточек. Самих карточек там пока ноль.</p>
        <CodeBlock caption="Два учебных файла и пустая таблица" code={schemaExample} />
        <p>Программа выводит <code>requests.json cards.sqlite3 requests 0</code>. Первые два имени указывают на разные файлы. Команда <code>CREATE TABLE</code> подготовила таблицу. Запрос к <code>sqlite_master</code> подтвердил её имя, а <code>COUNT(*)</code> посчитал строки. Соединение с <code>mode=ro</code> только читает уже существующий файл.</p>
        <p>В Planner таблицу создаёт изученный <code>python -m app.DBase.init_db</code>. Настройка <code>DATABASE_URL</code> содержит URL SQLite, например <code>sqlite:///C:/Lab/cards.sqlite3</code>. В <code>python -m app.DBase.import_tasks --source … --database …</code> оба флага получают обычные файловые пути. Первый ведёт к JSON, второй к SQLite. Путь с пробелами берём в кавычки.</p>
        <Diagram caption="Куда идут данные во время репетиции">
          <DiagramLane title="Пробный перенос">
            <DiagramFlow orientation="vertical">
              <DiagramNode title="Снимок JSON" shape="database">Заявки 4 и 9</DiagramNode>
              <DiagramArrow label="Перенос после проверки цели" />
              <DiagramNode title="Пробная SQLite" shape="database">До импорта таблица есть, строк 0</DiagramNode>
              <DiagramArrow label="Сверка перед первой новой записью" />
              <DiagramNode title="Два пробных читателя">Могут открыть одну проверенную базу</DiagramNode>
            </DiagramFlow>
          </DiagramLane>
          <DiagramLane title="Обычный режим">
            <DiagramFlow orientation="vertical">
              <DiagramNode title="Обычная ведомость JSON" shape="database">Контрольная заявка 9</DiagramNode>
              <DiagramArrow label="Читают после возврата" />
              <DiagramNode title="Два обычных читателя">Заявки из привычного источника</DiagramNode>
            </DiagramFlow>
          </DiagramLane>
        </Diagram>
        <p>Выберите состояние картотеки после init. Сравните пустую таблицу с заполненной, а затем с файлом без таблицы. Счётчик помогает принять решение только для уже найденной таблицы.</p>
        <BranchExplorer code={targetExample} scenarios={[
          { label: "Таблица есть, 0 строк", activeLine: 5, output: "Цель подготовлена. В ней есть место для заявок и ещё нет карточек." },
          { label: "Таблица есть, 2 строки", activeLine: 3, output: "Остановитесь: в картотеке уже есть чужие карточки." },
          { label: "Файл без нужной таблицы", activeLine: 1, output: "Остановитесь: проверка пустоты ещё невозможна без таблицы." },
        ]} />
        <p>Для практики работаем из корня Planner в PowerShell. В готовой проверке <code>$target</code> означает выбранный абсолютный путь к уже созданной SQLite. Сначала убедитесь, что место свободно, затем запустите init на этом учебном адресе. Команда ниже откроет SQLite для чтения и покажет путь, таблицу и число строк.</p>
        <CodeBlock caption="Проверка выбранной цели в PowerShell" code={inspectSchema} language="text" />
        <p>Если файла ещё нет, <code>resolve(strict=True)</code> остановит чтение. Если файл есть, а <code>tasks</code> отсутствует, структуру ещё предстоит проверить. Значение <code>tasks 0</code> подтверждает и наличие нужной таблицы, и её пустоту.</p>
      </Section>

      <Section number="02" title="Сверка по id перед первой записью">
        <Lead>Сверить перенос значит найти в копии каждую исходную запись и проверить её значения.</Lead>
        <p>Две картотеки содержат по две заявки. У копии заявки 9 потерялась наклейка «склад». Номер заявки остался прежним, а порядок карточек поменялся. Если сопоставить карточки по номеру, разница станет видна.</p>
        <CodeBlock caption="Сопоставление заявок по номеру и полям" code={compareExample} />
        <p>В первой строке видно <code>2 2</code>, затем <code>True</code>, потому что набор номеров совпал. В третьей строке видно <code>9 категория 'склад' ''</code>. <code>zip</code> сопоставляет название поля и оба значения из этой пары. Разное положение карточек не влияет на сравнение по номеру.</p>
        <p>В Planner у пары из JSON и SQLite сравниваем шесть полей. <code>id</code> помогает заметить пропажу, добавление или перенумерацию. <code>title</code>, <code>priority</code> и <code>is_done</code> сохраняют исходный текст, приоритет и состояние. <code>tags</code> проверяем целым списком, включая порядок. Для прежней записи без ключа ожидаем <code>[]</code>. Техническое <code>title_normalized</code> должно совпасть с <code>title.lower()</code>, поскольку именно эту форму использует поиск.</p>
        <QuizCard
          question="Можно ли после сверки начать пробную запись?"
          description="В другой поставке карточки 4 и 9 есть с обеих сторон, а у заявки 4 в копии изменился признак «закрыта»."
          options={["Можно, обе заявки на месте", "Сначала разобраться с расхождением у заявки 4", "Проверить только категории и продолжить"]}
          correctIndex={1}
          explanation="Совпавшие номера подтверждают состав. Поле «закрыта» изменилось, поэтому содержимое пока расходится. До исправления новая запись затруднит сверку."
          orientation="vertical"
        />
        <p>Готовый компаратор ниже получает <code>$source</code> и <code>$target</code> как файловые пути. Он использует знакомое преобразование <code>Task.from_dict</code>, чтобы учесть прежний формат JSON, и читает SQLite без изменений. В строке <code>JSON / SQLite</code> видны счётчики, затем отдельные списки потерянных или лишних номеров. Для каждой пары видны совпадение или имя поля с двумя значениями.</p>
        <CodeBlock caption="Отчёт по снимку и SQLite в PowerShell" code={compareSnapshots} language="text" />
        <p>Решение принимаем по всему отчёту до первой новой записи. Если отличается id или поле, сначала уточняем выбранные файлы и причину расхождения. Повтор импорта делаем на новой пустой цели. Так можно исследовать ошибку, сохранив первоначальные результаты.</p>
      </Section>

      <Section number="03" title="Одна база и новое чтение по id">
        <Lead>При перекрёстном чтении один интерфейс сохраняет запись, а другой открывает свой ресурс и ищет её по выданному номеру.</Lead>
        <p>Диспетчер зарегистрировал ещё одну заявку «Заявка на СКЛАД». У старой заявки уже такое название. Сотрудник получает от диспетчера новый номер и открывает свою картотеку. Если у него осталась вчерашняя копия, старая заявка находится, а новой там ещё нет.</p>
        <CodeBlock caption="Два независимых чтения после записи заявки" code={crossReadExample} />
        <p>В выбранной картотеке появится <code>(10, 'Заявка на СКЛАД')</code>. В отдельной копии видна прежняя заявка <code>9</code>, а поиск <code>10</code> возвращает <code>None</code>. <code>lastrowid</code> берёт номер из записи, <code>commit()</code> подтверждает изменение. Читатель открывает новое соединение по переданному пути и ищет ровно этот номер.</p>
        <p>В Planner вспомним подмену <code>get_planner</code> в тестовом API и <code>sql_service_context</code> для команды CLI. В двух отдельных Python-процессах задаём один выбранный <code>DATABASE_URL</code>. В первом используем прежнюю тестовую подмену и <code>TestClient</code>. Во втором передаём <code>run</code> фабрику контекстов пробного SQL. У каждого процесса своя фабрика Session и свой ресурс; обе фабрики смотрят в один и тот же файл.</p>
        <p>В ответе на <code>POST /tasks</code> код <code>201</code> показывает успешное создание. Число в поле <code>id</code> переносим в <code>find</code>. При промахе сначала сравниваем физические файлы, открытые через Engine API и CLI. В SQLite команда <code>PRAGMA database_list</code> показывает их пути; в результате нужна строка <code>main</code>. Если пути совпали, проверяем номер из ответа и наличие подтверждённой записи.</p>
      </Section>

      <Section number="04" title="Возврат к обычному JSON">
        <Lead>Вернуть обычный режим значит завершить процессы с пробными параметрами и запустить обе точки с исходными настройками.</Lead>
        <p>До пробы диспетчер и сотрудник пользовались ведомостью. Потом они получили временные картотеки. Когда проба завершена, оба снова открывают ведомость. Если один продолжит пользоваться временной картотекой, он по-прежнему найдёт новую заявку.</p>
        <CodeBlock caption="Два контрольных номера в каждом режиме" code={restoreExample} />
        <p>Первые две строки <code>True True</code> означают, что оба временных читателя находят старый и новый номера. Две строки <code>True False</code> после возврата подтверждают наличие прежней заявки и отсутствие пробной. В последнем случае результаты расходятся: терминал всё ещё находит <code>10</code>. Если пропала и старая заявка, следует проверить путь к обычной ведомости. Такой вывод возможен, например, на чужом пустом файле.</p>
        <p>Для Planner до пробы записываем путь обычного учебного JSON и наблюдение по старой задаче в обоих интерфейсах. Сохраняем контрольные копии JSON до изменения режима. После пробы заново запускаем HTTP-клиент без SQL-подмены и штатный CLI. Для старого id ждём чтение, для нового в API ждём <code>404</code>, а в CLI сообщение об отсутствии. У пробного номера заранее проверяем отсутствие в обоих исходных JSON. Если он совпал с прежним, берём новое пробное сохранение с различимым номером.</p>
        <p>Готовая проверка ниже принимает <code>$file</code>, фактический <code>$number</code> и <code>$baseline</code>, путь к контрольной копии именно этого файла. Её первый результат различает id, а второй сравнивает точные байты. Совпадение байтов подтверждает сохранность файла независимо от того, какой источник использовала запущенная точка.</p>
        <CodeBlock caption="Проверка номера и сохранности JSON в PowerShell" code={inspectJson} language="text" />
      </Section>

      <Section number="05" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Выберем снимок и цель", description: "Запишем исходный режим и два разных адреса, затем увидим пустую tasks." },
          { title: "Примем решение по переносу", description: "Сверим id и значения и объясним, когда отчёт разрешает перейти к пробной записи." },
          { title: "Прочитаем новую задачу из другой точки", description: "Возьмём id из ответа API, найдём ту же запись в CLI и проверим исходные JSON." },
          { title: "Вернём обычные запуски", description: "Обе обычные точки прочитают контрольный id. Пробный номер исчезнет из ответов, а копии подтвердят сохранность JSON." },
        ]} />
        <p>Работаем в одном учебном каталоге внутри своего Planner. В каждом шаге записываем результат, который понадобится дальше. От прогона останутся выбранные адреса, подтверждённая сверка и различающий id.</p>
      </Section>
    </RichLesson>
  );
}
