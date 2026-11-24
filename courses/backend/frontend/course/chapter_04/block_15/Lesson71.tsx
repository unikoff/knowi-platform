import { useState } from "react";
import { CodeBlock, Diagram, DiagramLane, DiagramTrack, Lead, LinkedNotes, QuizCard, RichHero, RichLesson, Section } from "../../shared";

const SCOPE_EXAMPLE = `from contextlib import contextmanager, nullcontext

@contextmanager
def trace_scope():
    print("вход")
    try:
        yield
    finally:
        print("выход")

def run_change(action, open_write=nullcontext):
    with open_write():
        return action()

def prepare_return():
    print("готовим выдачу к возврату")
    return "готово"

print(run_change(prepare_return, trace_scope))`;

const DATABASE_SETUP = `from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import URL, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

class Base(DeclarativeBase):
    pass

class LoanRow(Base):
    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(primary_key=True)
    item: Mapped[str]
    days: Mapped[int]
    hours: Mapped[int]
    note: Mapped[str]

demo_dir = TemporaryDirectory()
db_path = Path(demo_dir.name) / "loans.sqlite3"
engine = create_engine(URL.create("sqlite", database=str(db_path)))
Base.metadata.create_all(engine)
loan_sessions = sessionmaker(bind=engine)

with loan_sessions.begin() as seed:
    seed.add(LoanRow(id=8, item="Проектор", days=2, hours=48, note="склад"))`;

const SESSION_EXAMPLE = `with loan_sessions() as writer:
    def prepare_projector():
        loan = writer.get(LoanRow, 8)
        loan.note = "вернуть в зал"
        return loan.note

    print(run_change(prepare_projector, writer.begin))`;

const ID_EXAMPLE = `def register_loan(writer, item, days):
    loan = LoanRow(item=item, days=days, hours=days * 24, note="склад А")
    writer.add(loan)
    writer.flush()
    return {"number": loan.id, "equipment": loan.item}`;

const READ_EXAMPLE = `def read_loan(number):
    with loan_sessions() as reader:
        row = reader.get(LoanRow, number)
        if row is None:
            print("выдача не найдена")
        else:
            print(row.id, row.item, row.days, row.hours, row.note)`;

const RECEIPT_EXAMPLE = `def issue_stand(use_transaction):
    with loan_sessions() as writer:
        def add_stand():
            return register_loan(writer, "Стойка", 3)

        open_write = writer.begin if use_transaction else nullcontext
        receipt = run_change(add_stand, open_write)
        print("Квитанция:", receipt)

    number = receipt["number"]
    read_loan(number)
    return number

issue_stand(False)
loan_id = issue_stand(True)`;

const UPDATE_EXAMPLE = `def extend_loan(writer, number, days):
    loan = writer.get(LoanRow, number)
    if loan is None:
        return None
    loan.days = days
    loan.hours = days * 24
    return loan.days

with loan_sessions.begin() as writer:
    extend_loan(writer, loan_id, 5)

read_loan(loan_id)
engine.dispose()
demo_dir.cleanup()`;

export function Lesson71({ module }: { module?: string }) {
  const [outcome, setOutcome] = useState<"commit" | "rollback">("commit");
  const commits = outcome === "commit";
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Полный SQL-путь Planner"}
        title="Создаём и заменяем задачу в SQLite"
        intro="Planner вернул новую задачу. Как убедиться, что она сохранилась и следующий запрос изменит именно её?"
      />
      <Section number="00" title="Вступление">
        <Lead>Ваш Planner уже умеет читать из SQLite. Добавим два действия: POST для создания задачи и PUT для замены её публичных полей.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "В обычном режиме Planner хранит задачи в JSON. В учебной сборке SQL-репозиторий уже умеет находить задачу по номеру." },
          { title: "Новый вопрос", description: "В базе уже есть задача с таким же названием. Как изменить именно ту, которую мы только что добавили?" },
          { title: "Результат", description: "По номеру из POST найдём новую задачу в базе. После PUT увидим у неё новые значения, а у соседней задачи останутся прежние." },
        ]} />
        <p>Разберём механизм на журнале выдачи оборудования. Начнём с проектора под номером 8, добавим стойку и продлим её выдачу по полученному номеру.</p>
      </Section>

      <Section number="01" title="Когда можно сказать «сохранено»">
        <Lead>Чтобы продлить выдачу, нужно найти запись и изменить срок. Для пользователя это одно действие. Сгруппируем работу с базой так же. При успехе подтвердим все изменения вместе. Если возникнет ошибка, отменим их.</Lead>
        <p>
          Такая группа называется транзакцией. Подтверждение называют <code>commit</code>, откат при ошибке называют <code>rollback</code>.
          Сначала посмотрим только на порядок событий в Python. Декоратор <code>@contextmanager</code> позволяет
          использовать функцию в <code>with</code>. На <code>yield</code> она передаёт управление действию, а при выходе выполняет блок <code>finally</code>.
        </p>
        <CodeBlock code={SCOPE_EXAMPLE} caption="Журнал выдачи · выход из области до возврата результата" />
        <p>
          Получаем «вход», «готовим выдачу к возврату», «выход», «готово». Даже при <code>return</code> внутри блока Python сначала
          выходит из <code>with</code> и только потом отдаёт результат. Здесь выход лишь печатает сообщение. Когда в этом месте
          стоит контекст транзакции SQLAlchemy, успешный выход подтверждает запись. Если из блока вышло исключение, изменения откатываются.
        </p>
        <p>
          В Planner границу знает сервис: при замене он сначала читает задачу, затем передаёт изменения репозиторию.
          Поэтому в первой карточке вы поместите оба действия в одну область, начиная с поиска.
          Сервису можно передать функцию, которая откроет область при вызове. У <code>run_change(prepare_return)</code> такой
          функцией по умолчанию будет <code>nullcontext</code>. Она позволяет выполнить тело <code>with</code> без дополнительной работы
          на входе и выходе. Для JSON этого достаточно.
        </p>
      </Section>

      <Section number="02" title="Одна Session для изменения и транзакции">
        <Lead>Подключим настоящую транзакцию. Подготовим отдельную SQLite с одной записью о проекторе.</Lead>
        <p>
          Для запуска нужен Python с SQLAlchemy 2.x. Выполняйте блоки примера из разделов 01–04 по порядку в одном
          сеансе Python. Подготовка создаёт по модели <code>LoanRow</code> таблицу в файле <code>loans.sqlite3</code> во
          временной папке и сохраняет проектор под номером 8.
        </p>
        <CodeBlock code={DATABASE_SETUP} caption="Подготовка журнала выдач · временный файл и запись №8" />
        <CodeBlock code={SESSION_EXAMPLE} caption="В транзакции той же Session меняем примечание к проектору" />
        <p>
          <code>loan_sessions</code> открывает Session на подготовленном файле. Внутри операции <code>writer.get()</code> читает
          проектор, а <code>writer.begin</code> передаёт в <code>run_change</code> метод той же Session. Скобки появляются внутри
          {" "}<code>run_change</code>: сначала открывается транзакция, затем ищется строка. После подтверждения на экране появится «вернуть в зал».
        </p>
        <p>
          Так же соедините сервис и репозиторий в первом задании. Если поиск сделать раньше, SQLAlchemy может уже начать
          транзакцию автоматически, и последующий <code>begin()</code> попытается открыть её повторно. Если передать сервису
          метод другой Session, подтверждение произойдёт там, где изменения задачи не делались.
        </p>
      </Section>

      <Section number="03" title="Когда появляется id и что видит читатель">
        <Lead>При импорте id уже известен. При создании новой строки SQLite выдаёт его в момент INSERT.</Lead>
        <p>
          Добавим стойку на три дня с примечанием «склад А». В журнале есть и срок в часах, его получаем из числа дней.
          Функция вставки принимает открытую Session. Решение о подтверждении остаётся у вызывающего кода.
        </p>
        <CodeBlock code={ID_EXAMPLE} caption="Добавляем выдачу оборудования" />
        <p>
          У нового объекта <code>id</code> сначала равен <code>None</code>. <code>add()</code> ставит объект в очередь,
          а <code>flush()</code> отправляет INSERT. Теперь номер есть в квитанции. В Planner из строки после flush
          получается <code>Task</code> через знакомую функцию <code>task_from_row</code>.
        </p>
        <CodeBlock code={READ_EXAMPLE} caption="Читаем номер квитанции через новую Session" />
        <p>В обоих прогонах клиент получит квитанцию. Проверим её номер через <code>read_loan</code> после закрытия писателя.</p>
        <CodeBlock code={RECEIPT_EXAMPLE} caption="Две квитанции и два результата чтения" />
        <p>
          В первом прогоне <code>nullcontext</code> только пропускает действие. Клиент получает номер после flush.
          Когда <code>writer</code> закрывается без commit, незавершённая вставка откатывается. Новый читатель печатает
          «выдача не найдена». Во втором прогоне <code>run_change</code> получает <code>begin</code> этой же Session.
          Успешный выход подтверждает запись до выдачи квитанции. Новый читатель печатает «9 Стойка 3 72 склад А».
        </p>
        <Diagram caption="От квитанции к проверке сохранённой выдачи">
          <DiagramLane title="Сборка без области записи">
            <DiagramTrack label="Ход операции" items={["flush · выдана квитанция", "закрытие · откат", "свежее чтение · выдачи нет"]} />
          </DiagramLane>
          <DiagramLane title="Сборка с begin той же Session">
            <DiagramTrack label="Ход операции" items={["flush · появился номер", "commit · выдана квитанция", "свежее чтение · Стойка"]} />
          </DiagramLane>
        </Diagram>
        <p>
          Для отдельного вызова Planner эту связку собирает <code>sql_service_context</code>. В знакомой HTTP-подмене
          из темы «Ресурс на один запрос» владельцем Session остаётся <code>get_db</code>: он открывает и закрывает её
          на время запроса. Подмена <code>get_planner</code> получает уже открытую Session, отдаёт её репозиторию
          и передаёт сервису её <code>begin</code>. Сервис подтверждает изменение до ответа. Эту же подмену можно
          использовать при проверке PATCH.
        </p>
      </Section>

      <Section number="04" title="Что сохраняется при замене">
        <Lead>Продлим выдачу стойки до пяти дней. По её номеру найдём нужную строку, обновим срок и пересчитаем часы.</Lead>
        <CodeBlock code={UPDATE_EXAMPLE} caption="Находим выдачу по номеру и обновляем связанные поля" />
        <p>
          При повторном чтении по id 9 видим «9 Стойка 5 120 склад А». SQLAlchemy отслеживает присваивания найденному
          объекту и сохраняет их при подтверждении транзакции. Старые 72 часа были бы ошибкой даже при верных 5 днях.
          Примечание и номер остаются прежними. Если выдачи с таким номером нет, функция возвращает <code>None</code>.
          В Planner отсутствие задачи уже обрабатывают сервис и HTTP-маршрут. После проверки примера последние две строки
          освобождают соединения и удаляют временную папку.
        </p>
        <p>
          В Planner из тела PUT приходят <code>title</code>, <code>priority</code> и <code>is_done</code>. Номер в адресе
          указывает, какую запись изменить. У неё уже могут быть метки, которых в запросе нет. Есть ещё одно поле,{" "}
          <code>title_normalized</code>. При создании и замене туда попадает очищенный заголовок в нижнем регистре.
          Например, для «Кабель USB» вызов <code>"Кабель USB".lower()</code> даст «кабель usb». Если там останется старое
          «кабель», клиент всё равно может получить HTTP 200: поисковая копия в ответ не входит.
        </p>
        <Diagram caption="Что видит клиент и что хранится в SQLite">
          <DiagramLane title="Ответ клиенту">
            <DiagramTrack label="Видно в HTTP" items={["Прежний id", "Новые title, priority, is_done"]} />
          </DiagramLane>
          <DiagramLane title="Дополнительные колонки SQLite">
            <DiagramTrack label="Поля строки" items={["Прежние tags", "Новый title_normalized"]} />
          </DiagramLane>
        </Diagram>

        <h3>Какой срок увидят два читателя?</h3>
        <p>Теперь попробуйте предсказать результат для другой выдачи в этом журнале. Удлинитель под номером 12 выдан на 2 дня.
          Писатель меняет срок на 6 и делает flush. Один читатель запрашивает срок сейчас и закрывает свою Session.
          Выберите, чем закончится запись, и предскажите, что прочитает второй читатель в новой Session.</p>
        <p role="group" aria-label="Исход записи">
          <button type="button" className={commits ? "interactive-primary" : "interactive-secondary"} aria-pressed={commits}
            onClick={() => setOutcome("commit")}>Подтвердить запись</button>
          {" "}<button type="button" className={commits ? "interactive-secondary" : "interactive-primary"} aria-pressed={!commits}
            onClick={() => setOutcome("rollback")}>Откатить до commit</button>
        </p>
        <QuizCard key={outcome} question="Что вернут первый и второй читатели?"
          options={["2 дня → 6 дней", "2 дня → 2 дня", "6 дней → 6 дней"]}
          correctIndex={commits ? 0 : 1}
          explanation={commits
            ? "В первом чтении 2 дня, потому что flush ещё не подтвердил изменение. Во втором 6 дней, потому что commit сохранил версию."
            : "Оба читателя видят 2 дня. При первом чтении изменение ещё не подтверждено, а к моменту второго откат его отменил."}
          answer={<Diagram caption="Два чтения выдачи 12">
            <DiagramTrack label="Удлинитель · срок выдачи" items={["До окончания · 2 дня", commits ? "После commit · 6 дней" : "После rollback · 2 дня"]} />
          </Diagram>}
        />
      </Section>

      <Section number="05" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Подключим транзакцию", description: "Как в примере с проектором, сервис будет начинать операцию на той же Session, с которой работает репозиторий." },
          { title: "Добавим создание задачи", description: "Получим номер от SQLite и найдём эту же задачу в новой Session." },
          { title: "Добавим замену по номеру", description: "Изменим нужные поля и пересчитаем зависимое значение. По снимкам сверим id, метки и соседнюю строку." },
        ]} />
      </Section>
    </RichLesson>
  );
}
