import { useState } from "react";
import {
  CodeBlock, Diagram, DiagramLane, DiagramTrack, Lead, LinkedNotes,
  QuizCard, RichHero, RichLesson, Section,
} from "../../shared";

const LOAN_DEMO = `from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class Loan(Base):
    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(primary_key=True)
    item: Mapped[str]


class ReceiptError(RuntimeError):
    pass


def add_loan(engine, item, stop):
    with Session(engine) as writer:
        with writer.begin():
            loan = Loan(item=item)
            writer.add(loan)
            writer.flush()
            ticket = loan.id
            print(f"flush: {item}, №{ticket}")
            if stop == "before":
                raise ReceiptError("Печать квитанции прервалась.")
        if stop == "after":
            raise ReceiptError("Печать квитанции прервалась.")
    return ticket


def read_journal(engine):
    with Session(engine) as reader:
        rows = reader.execute(select(Loan.id, Loan.item).order_by(Loan.id))
        return dict(rows.all())


with TemporaryDirectory() as folder:
    database = Path(folder) / "loans.sqlite3"
    engine = create_engine(URL.create("sqlite", database=database.as_posix()))
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as seed, seed.begin():
            seed.add(Loan(id=8, item="Проектор"))

        for item, stop in [("Штатив", "before"), ("Стойка", "after")]:
            try:
                add_loan(engine, item, stop)
            except ReceiptError as error:
                print(error)
            print("В журнале:", read_journal(engine))

        cable_ticket = add_loan(engine, "Кабель", "normal")
        journal = read_journal(engine)
        print(f"Квитанция №{cable_ticket}: {journal[cable_ticket]}")
    finally:
        engine.dispose()`;

const RECEIPT_DEMO = `import pytest


class ReceiptError(RuntimeError):
    pass


def print_receipt():
    raise ReceiptError("Печать квитанции прервалась.")


with pytest.raises(ReceiptError):
    print_receipt()

print("Сбой квитанции отмечен. Можно читать журнал.")`;

const SNAPSHOT_DEMO = `before = {8: "Проектор", 12: "Кабель"}
after = {8: "Проектор", 12: "Удлинитель"}

print(len(before), len(after))
print(before == after)
print(after[12])`;

const TEMPORARY_ADDRESS = `file = tmp_path / "planner.sqlite3"
engine = make_engine(f"sqlite:///{file.as_posix()}")
Base.metadata.create_all(engine)
sessions = sessionmaker(bind=engine, class_=WatchedSession)`;

const CLOSE_TRACE = `class WatchedSession(Session):
    numbers = count(1)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.number = next(self.numbers)
        self.close_calls = 0

    def close(self):
        super().close()
        self.close_calls += 1
        print(f"Session №{self.number}: close={self.close_calls}")`;

const BEFORE_COMMIT = [
  "flush · получен номер",
  "Сбой квитанции внутри области",
  "rollback · запись отменена",
  "Session записи закрыта",
  "Свежий журнал · проектор",
];

const AFTER_COMMIT = [
  "flush · получен номер",
  "commit · запись подтверждена",
  "Сбой квитанции после commit",
  "Session записи закрыта",
  "Свежий журнал · проектор и стойка",
];

export function Lesson76_1({ module }: { module?: string }) {
  const [stop, setStop] = useState<"before" | "after">("before");
  const stopsBeforeCommit = stop === "before";

  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Надёжность Planner"}
        title="Проверяем, что осталось после сбоя записи"
        intro="Прервём запись до и после подтверждения. Откроем SQLite заново и выясним, что сохранилось."
      />

      <Section number="00" title="Вступление">
        <Lead>Возьмём рабочую SQLite и задачу, которую можно изменить. Два раза вызовем одинаковую операцию и прервём её в разных местах.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Мы умеем распознать отказ хранилища и сбой запроса." },
          { title: "Новый вопрос", description: "Могла ли задача сохраниться, если клиент получил ошибку?" },
          { title: "Результат", description: "Прочитаем данные после каждого сбоя и проверим, что новая запись тоже сохраняется." },
        ]} />
        <p>Сначала проследим за выдачей в журнале оборудования. На ней видно, в какой момент запись становится доступна следующему читателю.</p>
      </Section>

      <Section number="01" title="Где прерывается запись">
        <Lead>Транзакция задаёт границу, внутри которой SQLite может подтвердить или отменить изменение.</Lead>
        <p>В журнале выдачи уже есть проектор №8. Сотрудник добавляет новую выдачу, получает номер, подтверждает запись и печатает квитанцию. Печать может сорваться даже после подтверждения.</p>
        <p>
          Изменение выполняется в <strong>транзакции</strong>, одной области, в которой SQLite подтверждает или отменяет запись.
          Вызов <code>flush()</code> отправляет вставку в SQLite, и появляется номер.
          Успешный выход из <code>with writer.begin()</code> делает <code>commit</code>, то есть подтверждает изменение.
          Исключение, вышедшее из этой области до подтверждения, приводит к <code>rollback</code>: SQLite отменяет изменение.
        </p>
        <p>
          Сохраним пример в <code>loans_commit_demo.py</code> в каталоге <code>project/</code> и запустим там
          {" "}<code>./.venv/Scripts/python.exe loans_commit_demo.py</code>. Это отдельный учебный журнал. Готовая подготовка
          создаёт файл в новом временном каталоге, добавляет проектор и закрывает Engine в конце.
          В <code>add_loan</code> посмотрим на поля записи и на отступы двух <code>raise</code>.
        </p>
        <CodeBlock code={LOAN_DEMO} caption="Одна вставка, два положения сбоя и следующая выдача" />
        <p>
          После первой вставки видим <code>flush: Штатив, №9</code> и сообщение об ошибке. Свежий читатель видит
          {" "}<code>{"{8: 'Проектор'}"}</code>. Транзакция успела отправить INSERT и откатилась при выходе с исключением.
          Если бы исключение возникло раньше, строки про <code>flush</code> в выводе бы не было.
        </p>
        <p>
          «Стойка» тоже получает номер 9. Её транзакция успела завершиться, поэтому после того же сообщения
          читатель видит <code>{"{8: 'Проектор', 9: 'Стойка'}"}</code>. Номер первой попытки SQLite смогла выдать повторно.
          В Planner будем менять существующую задачу и увидим, что новое значение дошло до SQLite, ещё до независимого чтения.
        </p>
      </Section>

      <Section number="02" title="Как увидеть точку сбоя">
        <Lead>Обе попытки прервала одна и та же ошибка квитанции. Отметим ожидаемое исключение так, чтобы сразу после него можно было прочитать журнал.</Lead>
        <p>
          В примере с журналом <code>except ReceiptError</code> ловит отказ печати, и цикл идёт дальше.
          В тесте используем <code>pytest.raises</code>. Передаём туда класс ошибки, а под <code>with</code> помещаем
          вызов, от которого её ждём. После ожидаемой ошибки выполнение продолжится за блоком.
        </p>
        <p>Сохраним отдельный пример в <code>receipt_demo.py</code> рядом с журналом и запустим в <code>project/</code> командой <code>./.venv/Scripts/python.exe receipt_demo.py</code>.</p>
        <CodeBlock code={RECEIPT_DEMO} caption="Отмечаем только ожидаемый сбой печати" />
        <p>
          На экране появится «Сбой квитанции отмечен. Можно читать журнал.». Если печать завершится без исключения,
          pytest выдаст <code>DID NOT RAISE</code>. Если вместо отказа печати придёт, например, <code>ValueError</code>,
          исключение выйдет наружу как ошибка. В Planner ожидаем <code>ControlledWriteError</code>, а снимок снимаем
          отдельно, после выхода из области ожидания.
        </p>
        <Diagram caption="Два положения одного сбоя относительно commit" orientation="vertical">
          <DiagramLane title="Сбой до подтверждения">
            <DiagramTrack label="Ход записи" items={BEFORE_COMMIT} />
          </DiagramLane>
          <DiagramLane title="Сбой после подтверждения">
            <DiagramTrack label="Ход записи" items={AFTER_COMMIT} />
          </DiagramLane>
        </Diagram>
        <p>
          В предыдущей теме <code>503</code> обозначал недоступное хранилище. Безопасный <code>500</code> сообщает,
          что запрос завершился внутренней ошибкой. Один статус <code>500</code> совместим с обеими дорожками:
          за ответом о сбое может стоять как отменённая, так и сохранённая запись.
        </p>
      </Section>

      <Section number="03" title="Что сравнивает независимый читатель">
        <Lead>Пусть второй сотрудник открывает журнал после ухода первого. В коде он создаёт новую Session на том же файле после закрытия писателя.</Lead>
        <p>
          <code>read_journal</code> открывает читателя заново и возвращает <strong>снимок</strong>, обычный словарь
          с номерами и прочитанными значениями. Снимок остаётся доступен после закрытия Session.
          Две распечатки могут содержать те же номера, хотя выданный предмет изменился:
        </p>
        <CodeBlock code={SNAPSHOT_DEMO} caption="Сравниваем значения в двух распечатках журнала" />
        <p>
          Получим <code>2 2</code>, затем <code>False</code> и «Удлинитель». Количество записей и номера совпали,
          а по номеру 12 читается другое значение. В проверке Planner ключом тоже будет id. Под ним будут шесть значений
          по порядку: <code>id</code>, <code>title</code>, <code>priority</code>, <code>is_done</code>,
          {" "}<code>tags</code>, <code>title_normalized</code>. Метки и поисковую копию заголовка читаем прямо из
          SQLite. Обычный HTTP-ответ эти поля не показывает.
        </p>
        <p>
          Для отката сравним всё прочитанное с исходным состоянием. Для подтверждённой замены по номеру отделим
          ожидаемые новые поля от прежних значений. Так заметим и неверную запись, и потерю соседней строки.
        </p>

        <h3>Какой срок прочитаем после ошибки?</h3>
        <p>
          Удлинитель №17 выдан на 2 дня. Писатель меняет срок на 6 дней и выполняет <code>flush()</code>.
          Выберем положение сбоя и предскажем срок, который увидит новая Session после закрытия писателя.
        </p>
        <p role="group" aria-label="Положение сбоя">
          <button
            type="button"
            className={stopsBeforeCommit ? "interactive-primary" : "interactive-secondary"}
            aria-pressed={stopsBeforeCommit}
            onClick={() => setStop("before")}
          >До commit</button>
          {" "}
          <button
            type="button"
            className={stopsBeforeCommit ? "interactive-secondary" : "interactive-primary"}
            aria-pressed={!stopsBeforeCommit}
            onClick={() => setStop("after")}
          >После commit</button>
        </p>
        <QuizCard
          key={stop}
          question="Какой срок записан в журнале?"
          options={["2 дня", "6 дней"]}
          correctIndex={stopsBeforeCommit ? 0 : 1}
          explanation={stopsBeforeCommit
            ? "Останется 2 дня. Ошибка вышла из области до commit. Откат отменил изменение, хотя flush уже прошёл."
            : "Сохранятся 6 дней. Транзакция подтвердилась до ошибки. Свежий читатель получит новый срок."}
        />
      </Section>

      <Section number="04" title="Проверяем, что работа продолжилась">
        <Lead>После сбоя доступен журнал, и можно попытаться сделать новую запись. Проверим обе части: новый вызов вернул номер, новый читатель нашёл по нему сохранённый результат.</Lead>
        <p>
          В журнале штатный вызов выдаёт квитанцию на «Кабель» под номером 10. Мы получили номер от <code>add_loan</code>
          {" "}после выхода из транзакции и нашли предмет по этому номеру в новом чтении. В Planner привяжем проверку новой
          записи к <code>Task.id</code>, который вернёт <code>add_task</code>. Число заранее выбирать не нужно.
        </p>

        <h3>Открываем исправную учебную базу</h3>
        <p>
          В своём <code>project/</code> создадим <code>tests/test_commit_boundary.py</code>. Обе наши тестовые функции
          получат параметр <code>tmp_path</code>. Pytest сам передаст каждой новый временный каталог. Внутри него соберём
          SQLite тем же способом, что и в теме записи задачи, и создадим таблицу через существующий <code>Base</code>.
        </p>
        <CodeBlock code={TEMPORARY_ADDRESS} caption="Адрес и схема временного Planner" />
        <p>
          Здесь <code>make_engine</code> из <code>app.core.engine</code> получает URL файла, а <code>Base</code> и
          {" "}<code>TaskRow</code> берём из <code>app.DBase.models</code>. В <code>with sessions.begin() as seed</code>
          добавим через <code>seed.add_all</code> две строки <code>TaskRow</code> из условий. Выход из блока сохранит
          обе записи. Эти шаги можно собрать в одну свою функцию подготовки и вызывать в обоих тестах.
          После опыта освободим Engine через <code>engine.dispose()</code> в <code>finally</code>, как в журнале выдачи.
        </p>
        <p>
          Проверить закрытие помогает небольшой счётчик. Добавим <code>from itertools import count</code> и
          {" "}<code>from sqlalchemy.orm import Session, sessionmaker</code>. <code>count(1)</code> последовательно
          выдаёт номера новым Session. В фабрике выше <code>class_=WatchedSession</code> включает этот журнал.
        </p>
        <CodeBlock code={CLOSE_TRACE} caption="Готовый счётчик закрытий Session" />
        <p>
          В строке <code>Session №3: close=1</code> число 3 обозначает экземпляр Session, а 1 означает один вызов
          {" "}<code>close()</code>. При следующем чтении увидим другой номер. Ожидаем одну строку закрытия от каждого
          использованного ресурса.
        </p>

        <h3>Ставим точку сбоя в проверке</h3>
        <p>
          Как и при сборке Planner для SQLite, откроем <code>with sessions() as writer</code> и передадим в
          {" "}<code>build_service</code> аргументы <code>repository=SqlTaskRepository(writer)</code> и
          {" "}<code>write_scope=...</code>. Репозиторий возьмём из <code>app.DBase.repositories</code>, сборку из
          {" "}<code>app.main</code>. Оба аргумента должны работать с одной и той же пишущей Session.
        </p>
        <p>
          В тесте объявим свой <code>ControlledWriteError</code>, наследник <code>RuntimeError</code>,
          и временную функцию для <code>write_scope</code> с декоратором <code>@contextmanager</code> из
          {" "}<code>contextlib</code>. Внутри неё откроем <code>with writer.begin()</code> и поставим <code>yield</code>.
          Здесь управление уйдёт в сервис: <code>replace_task(task_id, *, title, priority, is_done)</code>
          найдёт строку и изменит поля. После <code>yield</code> управление вернётся в нашу функцию.
        </p>
        <p>
          При обычной замене SQLAlchemy может отправить изменения только при <code>commit</code>.
          Для опыта вызовем <code>writer.flush()</code> сразу после <code>yield</code>, внутри <code>begin()</code>.
          SQLite выполнит <code>UPDATE</code> в открытой транзакции. Так в обеих попытках мы сможем увидеть новый
          заголовок ещё до подтверждения.
        </p>
        <p>
          Импортируем <code>select</code> из SQLAlchemy и передадим <code>select(TaskRow.title).where(TaskRow.id == ...)</code>
          в <code>writer.scalar()</code>. Обернём чтение в <code>with writer.no_autoflush</code>, чтобы оно само
          не отправляло ожидающие изменения. Сверим новый заголовок и выведем отметку <code>flush</code>.
          Если пропустить явный flush, в сборке, где замена ждёт commit, здесь будет прежний заголовок.
        </p>
        <p>
          Выберем место для одного и того же <code>raise ControlledWriteError(...)</code>. Если поднять ошибку
          внутри <code>begin()</code> после контрольного чтения, SQLite откатит уже выполненный <code>UPDATE</code>.
          Если поднять её сразу за блоком <code>begin()</code>, изменение уже подтверждено. Во второй ветке
          выведем <code>commit</code> сразу за выходом. В штатной операции передадим <code>writer.begin</code>
          напрямую, без скобок. Временная функция остаётся в тестах.
        </p>

        <h3>Читаем через новую Session</h3>
        <p>
          Для снимка откроем <code>with sessions() as reader</code>, запросим <code>select(TaskRow)</code> через
          {" "}<code>reader.scalars()</code> и соберём словарь по id. Для каждой строки заберём шесть значений в указанном
          выше порядке. <code>tuple(row.tags)</code> сохранит метки как обычные значения. Снимок можно вынести из
          {" "}<code>with</code> и сравнить после закрытия читателя. Первый снимок сразу покажет, подготовлены ли обе задачи.
        </p>
        <p>
          В <code>project/</code> запускаем <code>./.venv/Scripts/python.exe -m pytest -q -s</code> с путём к файлу
          и <code>::имя_функции</code> из карточки. <code>-q</code> сокращает отчёт pytest, <code>-s</code> показывает
          строки журнала. Именно новый снимок после завершения записи подскажет, что увидит следующий читатель.
        </p>
      </Section>

      <Section number="05" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Доказываем откат по полному снимку", description: "Сверим обе строки и все поля после сбоя внутри транзакции." },
          { title: "Подтверждаем запись при сбое после commit", description: "Проверим новую версию цели и прежние значения соседа." },
          { title: "Проверяем новую успешную операцию", description: "Найдём новую задачу по её id свежим чтением после отката." },
        ]} />
        <p>Сохраним проверки у себя в проекте. Для следующего опыта пригодится этот же способ открывать и читать отдельную исправную SQLite.</p>
      </Section>
    </RichLesson>
  );
}
