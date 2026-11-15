import { Callout, CodeBlock, CodeSequence, Diagram, DiagramArrow, DiagramFlow, DiagramNode, Lead, LinkedNotes, RichHero, RichLesson, Section, QuizCard } from "../../shared";

const pathsExample = `from pathlib import Path

folder = Path("demo_snapshot")
folder.mkdir()
occupied = folder / "occupied.bin"
new_file = folder / "new.bin"

with occupied.open("xb") as file:
    file.write(b"KEEP")
before = occupied.read_bytes()
try:
    with occupied.open("xb"):
        pass
except FileExistsError:
    print("Занятое имя: отказ")
print("Байты сохранены:", before == occupied.read_bytes())
with new_file.open("xb"):
    pass
print("Новое имя создано:", new_file.is_file())`;

const demo = `from pathlib import Path
import sqlite3
from contextlib import closing

folder = Path("demo_snapshot")
source = folder / "books.sqlite3"
snapshot = folder / "books-copy.sqlite3"

with source.open("xb"):
    pass
with closing(sqlite3.connect(source)) as db:
    db.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO books VALUES (1, 'Atlas')")
    db.execute("INSERT INTO books VALUES (2, 'River')")
    db.commit()

if not source.is_file() or source.resolve() == snapshot.resolve():
    raise ValueError("Проверьте источник и назначение")
with snapshot.open("xb"):
    pass
with closing(sqlite3.connect(source)) as src:
    with closing(sqlite3.connect(snapshot)) as dst:
        src.backup(dst)

with closing(sqlite3.connect(snapshot)) as copy:
    rows = copy.execute("SELECT id, name FROM books ORDER BY id").fetchall()
print("Снимок:", rows)
with closing(sqlite3.connect(source)) as db:
    original = db.execute("SELECT id, name FROM books ORDER BY id").fetchall()
print("Исходник:", original)`;

const errorExample = `from pathlib import Path
import sqlite3
from contextlib import closing

folder = Path("demo_error")
folder.mkdir()
source = folder / "damaged.bin"
destination = folder / "unfinished.sqlite3"
with source.open("xb") as file:
    file.write(b"NOT SQLITE")
with destination.open("xb"):
    pass
try:
    with closing(sqlite3.connect(source)) as src:
        with closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
except sqlite3.DatabaseError:
    print("Ошибка после резервирования: файл не передаём")
print("Новое имя осталось:", destination.is_file())`;

const checkPathsExample = `from pathlib import Path

folder = Path("demo_snapshot")
source = folder / "books.sqlite3"
missing = folder / "missing.sqlite3"
new_copy = folder / "another-copy.sqlite3"

def check_paths(source, destination):
    if not source.is_file():
        raise ValueError("Источник отсутствует")
    if source.resolve() == destination.resolve():
        raise ValueError("Пути совпадают")

for first, second in [(missing, new_copy), (source, source), (source, new_copy)]:
    try:
        check_paths(first, second)
    except ValueError as error:
        print("Отказ:", error)
    else:
        print("Пути подходят для следующего шага")
print("Новое имя ещё свободно:", not new_copy.exists())`;

const argumentsExample = `# Файл show_paths.py в project/
import sys

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Нужны два пути")
        raise SystemExit(1)
    print("Источник:", sys.argv[1])
    print("Назначение:", sys.argv[2])`;

const readExample = `# Файл read_snapshot_books.py в project/ после примера со снимком
from pathlib import Path
import sqlite3
from contextlib import closing

path = Path("demo_snapshot/books.sqlite3")
if not path.is_file():
    raise ValueError("Учебная база не найдена")
with closing(sqlite3.connect(path)) as db:
    rows = db.execute("SELECT id, name FROM books ORDER BY id").fetchall()
print("Количество:", len(rows))
print("Строки:", rows)`;

export function Lesson82({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero chip={module ?? "StudyHub · Резервирование"} title="Получаем согласованный снимок" intro="Получим отдельную копию учебной SQLite и проверим, что исходные данные и занятые файлы остались на месте." />
      <Section number="00" title="От описи к сохранённому состоянию">
        <Lead>Перед изменением базы нам нужна возможность вернуться к её прежнему состоянию. Сначала выясним, как получить отдельный снимок и не повредить то, что уже есть.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Для учебного Planner подготовлены отдельная база и опись её схемы и записей. Если описи или адреса нет, сначала проверим их." },
          { title: "Новый вопрос", description: "Как сохранить состояние базы, когда простое копирование файла может пропустить данные?" },
          { title: "Результат", description: "Создадим отдельный снимок, проверим открытие и защитим существующий файл от перезаписи." },
        ]} />
        <p>Опись помогает сравнить исходное состояние после операции. Открываемый снимок станет входом для отдельной проверки восстановления.</p>
      </Section>
      <Section number="01" title="Почему одного файла может быть недостаточно">
        <Lead>SQLite сохраняет данные согласованно, но состояние работающей базы нельзя всегда определить по одному основному файлу.</Lead>
        <p>Пока программа записывает данные, SQLite использует журнал отката или режим WAL. В WAL подтверждённые изменения могут ещё находиться в отдельном журнале. Если взять только основной файл активной базы, эти изменения могут не попасть в копию. Файл может открываться и при этом содержать не все ожидаемые записи.</p>
        <p>Для нашего опыта останавливаем API, CLI и другие известные процессы записи, закрываем соединения и работаем только с отдельной учебной базой без личных данных. SQLite Backup API создаёт согласованный снимок через соединение с базой. Поддержка активной базы у Backup API есть, но здесь мы намеренно используем остановленную учебную базу.</p>
        <p>Проследим путь от остановленной учебной базы к открытому снимку. Журнал на схеме относится к исходной базе; отдельно восстанавливать данные мы пока не будем.</p>
        <Diagram caption="От источника к проверке открытия" orientation="vertical">
          <DiagramFlow label="Путь учебного снимка" orientation="vertical">
            <DiagramNode title="До остановки записи" shape="database">Основной файл и журнал WAL, если он используется: подтверждённые изменения могут быть в журнале</DiagramNode>
            <DiagramArrow label="останавливаем учебные процессы и закрываем соединения" />
            <DiagramNode title="Учебный источник готов" shape="database">Путь к существующей базе подтверждён описью</DiagramNode>
            <DiagramArrow label="SQLite Backup API получает состояние" />
            <DiagramNode title="Новый снимок" shape="database">Отдельный файл в учебном каталоге</DiagramNode>
            <DiagramArrow label="открываем и читаем" />
            <DiagramNode title="Открытие проверено" shape="process">Полноту восстановленных данных ещё не проверяли</DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>Стрелки обозначают создание и отдельное чтение снимка. Остановка записи является условием нашего учебного опыта; схема не изображает уже выполненное восстановление.</p>
        <QuizCard question="Если файл снимка открылся, мы уже доказали, что восстановленные данные полны." options={["Правда", "Ложь"]} correctIndex={1} explanation="Открытие показывает доступность файла. Для проверки полноты нужно восстановить копию в новый путь и сравнить её содержимое с описью." />
        <p>Дальше нам нужно защитить имя назначения: снимок не должен заменить уже существующий файл.</p>
      </Section>
      <Section number="02" title="Как защитить адрес назначения">
        <Lead>Путь источника указывает на существующую базу, а путь назначения выбираем новым в отдельном учебном каталоге.</Lead>
        <p><code>Path</code> из <code>pathlib</code> представляет путь. Относительный путь отсчитывается от папки, где запущен Python. <code>is_file()</code> проверяет существующий обычный файл; <code>resolve()</code> помогает сравнить нормализованные пути. Отсутствующий источник нельзя сразу передавать обычному <code>sqlite3.connect</code>: вместо ошибки может появиться новая пустая база.</p>
        <p><code>path.open("xb")</code> создаёт новый файл для двоичных данных. Буква <code>x</code> запрещает открывать уже занятый путь и вызывает <code>FileExistsError</code>, а <code>b</code> обозначает байты. В отличие от него режим <code>w</code> может обнулить существующий файл. <code>read_bytes()</code> читает байты для сравнения. После <code>with</code> файловый объект закрывается.</p>
        <p>Проверим защиту имени отдельно, пока не подключая SQLite. В <code>project/</code> сохраним следующий файл как <code>demo_paths.py</code>. Каталога <code>demo_snapshot</code> заранее быть не должно; затем из <code>project/</code> выполним <code>python demo_paths.py</code>.</p>
        <CodeBlock caption="demo_paths.py: занятое и новое имя" code={pathsExample} />
        <p>Видим «Занятое имя: отказ», затем <code>Байты сохранены: True</code> и <code>Новое имя создано: True</code>. Первое открытие записало известные байты, второе с тем же именем вызвало ошибку до записи. Пустой новый файл уже существует, но ещё не является снимком базы.</p>
        <p>Для собственной пробы заменим оба имени файлов и имя каталога на новые. Запустим опыт и сравним результат. При повторном запуске с тем же каталогом <code>mkdir()</code> остановится: так мы не затрём результаты первой попытки.</p>
        <Callout tone="warn">Проверка пути не защищает от всех сторонних изменений и жёстких ссылок. Используем выделенный учебный каталог без параллельных изменений и не работаем с единственным оригиналом.</Callout>
      </Section>
      <Section number="03" title="Законченный опыт на другой базе">
        <Lead>Создадим базу книг, затем получим отдельный снимок по новому имени. Защиту занятого имени мы уже проверили в отдельном опыте. Все данные вымышленные.</Lead>
        <p>Сохраним код в <code>demo_books.py</code> внутри <code>project/</code>. Сначала выполним <code>demo_paths.py</code> из предыдущего раздела: он создаст каталог <code>demo_snapshot</code> и контрольный файл. В этом же каталоге ещё не должно быть файлов <code>books.sqlite3</code> и <code>books-copy.sqlite3</code>. Из <code>project/</code> выполним <code>python demo_books.py</code> подготовленным Python. Для повторения выберем другой новый каталог в обоих файлах, не удаляя существующие данные.</p>
        <CodeBlock caption="demo_books.py: исходная база и отдельный снимок" code={demo} />
        <p>Сначала появились две строки в исходной базе: <code>commit()</code> сохранил их, а <code>closing</code> закрыл соединение. Затем <code>is_file()</code> подтвердил существование источника, а <code>resolve()</code> позволил сравнить пути. Так мы не откроем отсутствующий источник как новую пустую базу и не используем один файл в обеих ролях.</p>
        <p>После проверки <code>open("xb")</code> резервирует свободное имя и закрывает пустой файл. <code>sqlite3.connect(path)</code> возвращает соединение с указанной базой. У нас их два: <code>src</code> для исходника и <code>dst</code> для назначения. Вызов <code>src.backup(dst)</code> выполняется у источника, а назначение передаётся аргументом. Сам метод не защищает занятое имя, поэтому резервирование было отдельным шагом.</p>
        <p><code>closing</code> из <code>contextlib</code> закрывает каждое соединение и при исключении, даже если второе подключение не удалось. Обычный контекстный менеджер соединения SQLite управляет транзакцией, но сам соединение не закрывает. После закрытия снова открываем снимок, выполняем <code>SELECT</code> и получаем строки через <code>fetchall()</code>.</p>
        <p>Ожидаем две строки: <code>Снимок: [(1, 'Atlas'), (2, 'River')]</code> и <code>Исходник: [(1, 'Atlas'), (2, 'River')]</code>. Первое чтение показывает, что снимок открылся; второе показывает, что эти исходные строки не изменились. Полная проверка восстановления остаётся отдельным действием.</p>
        <p>Рассмотрим проверки адресов отдельно от копирования. После опыта с книгами сохраним в <code>project/</code> файл <code>check_paths.py</code> и запустим <code>python check_paths.py</code>. В каталоге <code>demo_snapshot</code> уже есть <code>books.sqlite3</code>, а имена <code>missing.sqlite3</code> и <code>another-copy.sqlite3</code> должны быть свободны. Этот пример только сравнивает пути и не открывает базы.</p>
        <CodeBlock caption="check_paths.py: три пары путей" code={checkPathsExample} />
        <p>Сначала получаем «Отказ: Источник отсутствует», затем «Отказ: Пути совпадают», затем «Пути подходят для следующего шага» и <code>Новое имя ещё свободно: True</code>. <code>else</code> у <code>try</code> выполняется только без исключения. Подходящие пути ещё не создают снимок: после проверки потребуется резервирование и работа соединений.</p>
        <p>Теперь в другом новом учебном каталоге подготовим небольшую базу с другими значениями. Зарезервируем новое имя, запишем вызов на двух соединениях и прочитаем результат. Что произойдёт, если поменять местами источник и назначение? Пустая база станет источником, а база с книгами назначением. Такой вызов может перезаписать исходные данные и не создаст нужный снимок. Не используем путь Planner для этой пробы.</p>
      </Section>
      <Section number="04" title="Что делать при отказе">
        <Lead>У ошибки до создания назначения и ошибки после резервирования разные последствия.</Lead>
        <p>Если источник отсутствует, пути совпадают или имя занято, прекращаем операцию до создания назначения. Если ошибка возникла после создания нового файла, он может быть пустым или частичным. Закрываем все открытые ресурсы, сообщаем об ошибке и не называем этот файл успешным снимком. Выясняем причину и повторяем с другим новым именем.</p>
        <p>В примере <code>try</code> содержит опасную операцию, а <code>except FileExistsError</code> обрабатывает отказ для занятого пути. <code>closing</code> гарантирует закрытие уже открытого соединения даже тогда, когда открытие второго соединения или вызов <code>backup</code> вызовет исключение. Не перехватываем ошибку так, чтобы после неё печаталось сообщение об успехе.</p>
        <p>Отдельно увидим поздний отказ. Сохраним <code>demo_error.py</code> в <code>project/</code>, убедимся, что каталога <code>demo_error</code> нет, и выполним <code>python demo_error.py</code> из <code>project/</code>.</p>
        <CodeBlock caption="demo_error.py: ошибка после создания имени" code={errorExample} />
        <p>Вывод сообщает «Ошибка после резервирования: файл не передаём» и <code>Новое имя осталось: True</code>. Файл создан этой пробой до обращения к повреждённому источнику; он не подтверждён как снимок. Не предполагаем, что при любой ошибке его содержимое будет одинаковым. Для повторения после выяснения причины выбираем другое новое имя.</p>
        <CodeSequence title="Проследим состояние" prompt="Расположим события успешного опыта с каталогом книг. Какие состояния должны наступить до чтения снимка?" pieces={[
          { id: "open", code: "Открыли готовый снимок и прочитали строки" },
          { id: "reserve", code: "Зарезервировали свободное имя и закрыли файл" },
          { id: "copy", code: "Выполнили backup и закрыли соединения" },
          { id: "failed", code: "После ошибки передали частичный файл как снимок" },
        ]} correctOrder={["reserve", "copy", "open"]} explanation="Чтение имеет смысл после завершения backup и закрытия соединений. Проверка восстановления остаётся отдельным действием." incorrectExplanation="Вернёмся к двум соединениям: сначала нужно получить новое имя, затем завершить backup, и только потом читать снимок." />
        <p>Если резервирование не удалось из-за занятого пути, его байты не меняются. Если ошибка возникла позднее, оставшийся новый файл не передаём дальше как готовый результат.</p>
      </Section>
      <Section number="05" title="Пути в команде и чтение записей">
        <Lead>Обёртке нужно передать два пути. Сначала разберём их на маленькой программе, которая только показывает аргументы.</Lead>
        <CodeBlock caption="show_paths.py" code={argumentsExample} />
        <p><code>if __name__ == "__main__"</code> выполняет вложенный блок при прямом запуске файла или модуля, но не при его импорте. <code>sys.argv</code> содержит имя запускаемого файла и переданные аргументы. <code>len</code> считает элементы; если путей не два, <code>SystemExit(1)</code> завершает программу с ненулевым кодом. Из <code>project/</code> выполним <code>python show_paths.py demo_snapshot/books.sqlite3 "demo_snapshot/new copy.sqlite3"</code>. Увидим два подписанных пути. Кавычки удерживают путь с пробелом одним аргументом. Команда с одним путём выведет «Нужны два пути».</p>
        <p>Чтобы отдельно попробовать запуск модуля, создадим в учебной копии новую папку <code>demo_package</code>, пустой файл <code>demo_package/__init__.py</code> и сохраним тот же пример в <code>demo_package/paths.py</code>. Из <code>project/</code> команда <code>python -m demo_package.paths first second</code> показывает <code>Источник: first</code> и <code>Назначение: second</code>. При одном аргументе она сообщает «Нужны два пути». Папку выбираем новую, не заменяя существующие файлы.</p>
        <p>В проектной работе мы создадим модуль <code>app/DBase/backup_snapshot.py</code> и будем передавать ему пути командой <code>python -m app.DBase.backup_snapshot SOURCE DEST</code>. Флаг <code>-m</code> запускает модуль из корня <code>project/</code>; <code>SOURCE</code> и <code>DEST</code> заменяем реальными путями, а не вводим как буквальные имена. В PowerShell можно вместо <code>python</code> указать <code>./.venv/Scripts/python.exe</code> подготовленной среды.</p>
        <p>В PowerShell код завершения последней внешней программы доступен в <code>$LASTEXITCODE</code>. Выполним <code>python -c "raise SystemExit(3)"</code>, затем сразу <code>$LASTEXITCODE</code>: увидим <code>3</code>. Ноль обычно означает успешное завершение, ненулевое значение сообщает об ошибке. Прочитаем или сохраним значение сразу после нужной команды: следующий запуск Python его заменит. Так проверяем результат процесса модуля, а не только исключение, перехваченное внутри программы.</p>
        <p>Для сравнения состояния отдельно прочитаем строки книг. В PowerShell из <code>project/</code> сначала выполним <code>Test-Path -LiteralPath './read_snapshot_books.py'</code>. Значение <code>False</code> означает, что имя свободно: сохраним следующий код в <code>read_snapshot_books.py</code> и запустим командой <code>python read_snapshot_books.py</code>. Если получили <code>True</code>, остановимся и выберем другое свободное имя, используя его при сохранении и во всех следующих командах. Прежний <code>read_books.py</code> не изменяем.</p>
        <CodeBlock caption="Читаем существующую базу без изменения строк" code={readExample} />
        <p>Увидим количество <code>2</code> и те же два кортежа. <code>SELECT</code> выбирает названные столбцы, <code>ORDER BY id</code> задаёт порядок по идентификатору, а <code>len(rows)</code> считает полученные строки. Если путь неверный, проверка остановит программу до открытия новой пустой базы.</p>
        <p>Для быстрой проверки открытия другой существующей SQLite можно тем же способом выполнить <code>SELECT name FROM sqlite_schema LIMIT 1</code>: системная таблица описывает объекты схемы, а <code>LIMIT 1</code> ограничивает ответ одной строкой. Например, заменим запрос в <code>read_snapshot_books.py</code>, сохраним файл и снова выполним <code>python read_snapshot_books.py</code>. Получим <code>[('books',)]</code> для учебной базы книг. Это проверка чтения схемы, а не сравнение содержимого.</p>
        <p>В Planner используем тот же способ чтения с запросом <code>SELECT id, title, priority, is_done, tags, title_normalized FROM tasks ORDER BY id</code>. Сравним число строк и все значения с ранее составленной описью; внутренние <code>tags</code> не нужно добавлять в API или CLI.</p>
      </Section>
      <Section number="06" title="Готовимся к собственной обёртке">
        <Lead>Теперь перенесём знакомые приёмы на учебную базу Planner, не изменяя её записи.</Lead>
        <p>Сначала выясним действительный путь из описи и настроек, остановим процессы и проверим, что новый путь лежит в учебном каталоге. Затем создадим собственную функцию с двумя путями и модульным запуском. При занятом назначении она должна отказаться без перезаписи, а при отсутствии источника или совпадении путей остановиться до создания файла.</p>
        <p>После успешного вызова откроем снимок отдельно и перечитаем исходные записи по описи. Если какой-либо шаг завершился ошибкой, не передаём частичный файл дальше. Полноту и восстановление проверим отдельно.</p>
      </Section>
      <Section number="07" title="Что мы будем делать в практике">
        <Lead>Перенесём опробованный способ на учебный Planner: сначала подтвердим исходное состояние, затем защитим файлы и получим снимок.</Lead>
        <LinkedNotes items={[
          { title: "Проверим вход", description: "Установим адрес учебной базы и сравним её записи с описью после остановки процессов." },
          { title: "Создадим обёртку", description: "Напишем модуль с двумя путями, защитой занятого имени и закрытием соединений; проверим неверные аргументы." },
          { title: "Проверим отказ", description: "Попробуем занятое имя, сравним байты контрольного файла и перечитаем источник." },
          { title: "Получим снимок", description: "Вызовем модуль с новым именем, откроем снимок и снова сверим источник с описью." },
        ]} />
        <p>В каждой карточке есть своя проверка. Открытый снимок и опись передадим на проверку восстановления только после успешной операции.</p>
      </Section>
    </RichLesson>
  );
}
