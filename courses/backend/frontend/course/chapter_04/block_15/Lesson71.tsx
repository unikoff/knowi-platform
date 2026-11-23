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

const SESSION_EXAMPLE = `from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

engine = create_engine("sqlite:///loans.sqlite3")
loan_sessions = sessionmaker(bind=engine)

with loan_sessions() as writer:
    def prepare_projector():
        loan = writer.get(LoanRow, 8)
        loan.note = "вернуть в зал"
        return loan.note

    print(run_change(prepare_projector, writer.begin))`;

const ID_EXAMPLE = `def register_loan(writer, item, days):
    loan = LoanRow(item=item, days=days, hours=days * 24, note="склад А")
    writer.add(loan)
    writer.flush()
    return {"number": loan.id, "equipment": loan.item}

with loan_sessions.begin() as writer:
    receipt = register_loan(writer, "Стойка", 3)

loan_id = receipt["number"]
print(receipt)`;

const READ_EXAMPLE = `with loan_sessions() as reader:
    row = reader.get(LoanRow, loan_id)
    if row is None:
        print("выдача не найдена")
    else:
        print(row.id, row.item, row.days, row.hours, row.note)`;

const UPDATE_EXAMPLE = `def extend_loan(writer, number, days):
    loan = writer.get(LoanRow, number)
    if loan is None:
        return None
    loan.days = days
    loan.hours = days * 24
    return loan.days

with loan_sessions.begin() as writer:
    print(extend_loan(writer, loan_id, 5))`;

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
        <p>Разберём механизм на журнале выдачи оборудования. В нём уже записан проектор под номером 8. Добавим стойку, запомним её номер и продлим срок выдачи.</p>
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
        <Lead>Подключим к примеру настоящую транзакцию. В учебном файле <code>loans.sqlite3</code> уже есть таблица выдач с проектором под номером 8. Модель <code>LoanRow</code> описывает её строки.</Lead>
        <CodeBlock code={SESSION_EXAMPLE} caption="В транзакции той же Session меняем примечание к проектору" />
        <p>
          <code>sessionmaker</code> создаёт фабрику на Engine с адресом этого файла. Внутри операции <code>writer.get()</code> читает
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
          Функция вставки принимает открытую Session, а вызывающий код владеет транзакцией.
          Здесь <code>loan_sessions.begin()</code> открывает новую Session и транзакцию на время блока.
        </p>
        <CodeBlock code={ID_EXAMPLE} caption="Добавляем выдачу оборудования" />
        <p>
          В ответе <code>number</code> равен 9, если до вставки в журнале был только проектор с id 8. У нового объекта
          {" "}<code>id</code> сначала равен <code>None</code>. <code>add()</code> ставит объект в очередь, а <code>flush()</code> отправляет INSERT.
          После него есть номер для квитанции, но транзакцию ещё можно откатить. В Planner роль квитанции играет <code>Task</code>.
          Получить её из строки помогает знакомая функция <code>task_from_row</code>.
        </p>
        <CodeBlock code={READ_EXAMPLE} caption="Новая Session на том же реестре" />
        <p>
          После выхода из блока записи этот код напечатает «9 Стойка 3 72 склад А». Каждый вызов фабрики создаёт новую Session
          с адресом того же файла. Если открыть отдельного читателя сразу после{" "}
          <code>flush()</code>, пока писатель ещё внутри блока, он напечатает «выдача не найдена». После отката вставки результат будет таким же.
          Так видно, что SQLite выдаёт номер раньше, чем строка становится доступна другим читателям.
          В Planner сервис завершит транзакцию до того, как POST вернёт этот номер клиенту.
        </p>
        <Diagram caption="Когда номер уже известен и когда запись увидит другой запрос">
          <DiagramLane title="Session, которая создаёт выдачу">
            <DiagramTrack label="Вставка" items={["add · id пока нет", "flush · id 9", "commit · запись подтверждена"]} />
          </DiagramLane>
          <DiagramLane title="Два свежих чтения">
            <DiagramTrack label="Поиск по id 9" items={["До commit · None", "После commit · Стойка"]} />
          </DiagramLane>
        </Diagram>
      </Section>

      <Section number="04" title="Что сохраняется при замене">
        <Lead>Продлим выдачу стойки до пяти дней. По её номеру найдём нужную строку, обновим срок и пересчитаем часы.</Lead>
        <CodeBlock code={UPDATE_EXAMPLE} caption="Находим выдачу по номеру и обновляем связанные поля" />
        <p>
          При повторном чтении по id 9 видим «9 Стойка 5 120 склад А». SQLAlchemy отслеживает присваивания найденному
          объекту и сохраняет их при подтверждении транзакции. Старые 72 часа были бы ошибкой даже при верных 5 днях.
          Примечание и номер остаются прежними. Если выдачи с таким номером нет, функция возвращает <code>None</code>.
          В Planner отсутствие задачи уже обрабатывают сервис и HTTP-маршрут.
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
          { title: "Добавим создание задачи", description: "Как при выдаче стойки, получим номер от SQLite и вернём его в результате создания." },
          { title: "Добавим замену по номеру", description: "Как при продлении выдачи, обновим нужные поля и пересчитаем зависимое значение. У задачи останутся её id и метки." },
        ]} />
      </Section>
    </RichLesson>
  );
}
