import { useId, useState } from "react";
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

const bookExample = `from sqlalchemy import JSON, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class BookRow(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str]
    on_loan: Mapped[bool]
    shelf: Mapped[int]
    labels: Mapped[list[str]] = mapped_column(JSON)
    title_key: Mapped[str]


def read_book(book_id):
    with sessions() as db:
        book = db.get(BookRow, book_id)
        return book.id, book.title, book.on_loan, book.shelf, book.labels, book.title_key


class DemoAbort(Exception):
    pass


def put_book(book_id, title, on_loan, shelf, abort=False):
    if not title.strip() or shelf < 1:
        raise ValueError("Нужны название и положительный номер полки")

    with sessions() as db:
        with db.begin():
            book = db.get(BookRow, book_id)
            if book is None:
                return "книга не найдена"

            book.title, book.on_loan, book.shelf = title, on_loan, shelf
            book.title_key = title.lower()
            db.flush()
            if abort:
                raise DemoAbort
            public_book = (book.id, book.title, book.on_loan, book.shelf)
        return public_book


engine = create_engine("sqlite://")
sessions = sessionmaker(engine)
try:
    Base.metadata.create_all(engine)
    with sessions.begin() as db:
        db.add_all([
            BookRow(id=7, title="Север", on_loan=False, shelf=1, labels=["дар"], title_key="север"),
            BookRow(id=8, title="Маяк", on_loan=True, shelf=3, labels=["архив"], title_key="маяк"),
        ])

    print("Было:", read_book(7))
    print("Ответ:", put_book(7, "Карта", True, 2))
    print("Новое чтение:", read_book(7))
    print("Сосед:", read_book(8))
    print("Промах:", put_book(99, "Карта", True, 2))

    try:
        put_book(7, "Атлас", False, 4, abort=True)
    except DemoAbort:
        print("После отмены:", read_book(7))
finally:
    engine.dispose()`;

const bookOutput = `Было: (7, 'Север', False, 1, ['дар'], 'север')
Ответ: (7, 'Карта', True, 2)
Новое чтение: (7, 'Карта', True, 2, ['дар'], 'карта')
Сосед: (8, 'Маяк', True, 3, ['архив'], 'маяк')
Промах: книга не найдена
После отмены: (7, 'Карта', True, 2, ['дар'], 'карта')`;

function BookFieldProbe() {
  const id = useId();
  const [address, setAddress] = useState("11");
  const [labels, setLabels] = useState("");
  const [loan, setLoan] = useState("");
  const [checked, setChecked] = useState(false);

  const found = address === "11";
  const keepsLabel = labels === "keep";
  const takesLoanFromBody = loan === "body";
  const publicBook = { id: 11, title: "ЁЛКА", on_loan: !takesLoanFromBody, shelf: 6 };

  function reset() {
    setAddress("11");
    setLabels("");
    setLoan("");
    setChecked(false);
  }

  return (
    <form
      className="quiz-card"
      aria-labelledby={id}
      onSubmit={(event) => {
        event.preventDefault();
        setChecked(true);
      }}
    >
      <h3 id={id}>Из каких полей сложится новая карточка?</h3>
      <p>В каталоге одна книга. У книги 11 название «Сад», флаг выдачи true, полка 4 и метка «обмен».</p>
      <p>Новое тело запроса содержит название «ЁЛКА», флаг false и полку 6. Предскажем техническое имя и выберем значения для новой строки.</p>
      <div style={{ display: "grid", gap: "0.75rem" }}>
        <label>
          <span style={{ display: "block" }}>Адрес книги</span>
          <select value={address} onChange={(event) => { setAddress(event.target.value); setChecked(false); }}>
            <option value="11">11 (книга существует)</option>
            <option value="99">99 (проверка отсутствия)</option>
          </select>
        </label>
        {found && (
          <>
            <label>
              <span style={{ display: "block" }}>Внутренняя метка</span>
              <select value={labels} onChange={(event) => { setLabels(event.target.value); setChecked(false); }}>
                <option value="">Выбрать значение</option>
                <option value="keep">Сохранить «обмен»</option>
                <option value="clear">Оставить пустой список</option>
              </select>
            </label>
            <label>
              <span style={{ display: "block" }}>Флаг выдачи</span>
              <select value={loan} onChange={(event) => { setLoan(event.target.value); setChecked(false); }}>
                <option value="">Выбрать значение</option>
                <option value="keep">Взять прежнее true</option>
                <option value="body">Взять false из тела</option>
              </select>
            </label>
          </>
        )}
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          <button className="interactive-primary" type="submit" disabled={found && (!labels || !loan)}>Сверить</button>
          <button className="interactive-secondary" type="button" onClick={reset}>Сбросить пробу</button>
        </div>
      </div>
      <div role="status">
        {checked && (found ? (
          <>
            <p><strong>Публичная карточка</strong></p>
            <p>Книга {publicBook.id}, «{publicBook.title}», флаг {String(publicBook.on_loan)}, полка {publicBook.shelf}.</p>
            <p><strong>Чтение после подтверждения</strong></p>
            <p>Та же книга, метка {keepsLabel ? "«обмен»" : "утрачена"}, техническое имя «{publicBook.title.toLowerCase()}».</p>
            <p>{keepsLabel ? "Метка пришла из найденной строки." : "Из тела метку взять нельзя. Если её очистить, публичная карточка этого не покажет."}</p>
            <p>{takesLoanFromBody ? "Явное false из тела меняет флаг. Новое название даёт техническое имя «ёлка»." : "Старый флаг остался true, хотя в теле явно передано false. Берём флаг из тела."}</p>
          </>
        ) : (
          <p>Книга не найдена. В каталоге по-прежнему одна книга. Запрос не добавил строку с id 99.</p>
        ))}
      </div>
    </form>
  );
}

export function Lesson71_1({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Блок 15 · Полный SQL-путь и единое переключение"}
        title="Заменяем задачу через PUT и сохраняем теги"
        intro="Обновим задачу по её id и проверим, какие данные видит клиент, а какие остаются в SQLite."
      />

      <Section number="00" title="Открываем задачу, которую хотим изменить">
        <Lead>Мы уже можем создать задачу и потом открыть её по id. Теперь используем этот же id, чтобы заменить значения задачи.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "В тестовой базе есть задача с id, заголовком, приоритетом, статусом и внутренними тегами." },
          { title: "Новый вопрос", description: "Какие значения возьмём из запроса, а какие сохраним из найденной задачи?" },
          { title: "Результат", description: "После PUT по прежнему id читается новая версия задачи с прежними тегами." },
        ]} />
        <p>Сначала разберём источники полей на примере каталога книг. Потом проследим, что увидит новый ресурс после подтверждения записи.</p>
      </Section>

      <Section number="01" title="Откуда приходят поля новой версии">
        <Lead>В PUT клиент передаёт полный набор значений, которые разрешено редактировать. Адрес запроса выбирает существующую запись.</Lead>
        <p>В каталоге попросим заменить публичные поля книги 7. У неё было название «Север», флаг выдачи false, полка 1 и внутренняя метка «дар». В запросе приходят «Карта», true и полка 2. Метка остаётся у той же книги. Техническое имя нужно для поиска по названию без учёта регистра. Вызов <code>"Карта".lower()</code> даёт «карта».</p>
        <Diagram caption="Книга 7: какие данные переходят в новую версию" className="lesson-diagram--full-width">
          <DiagramFlow label="Путь полей от запроса к сохранённой книге">
            <DiagramNode title="Тело запроса">
              <p>«Карта», true, полка 2</p>
            </DiagramNode>
            <DiagramArrow label="Новые публичные значения" />
            <DiagramNode title="Строка из адреса: id 7">
              <p>Сохраняем id 7 и метку «дар».</p>
            </DiagramNode>
            <DiagramArrow label="Публичные поля + сохранённые данные" />
            <DiagramNode title="Итоговая строка" shape="database">
              <p>7, «Карта», true, полка 2, «дар».</p>
              <p>Пара для поиска: «Карта» → lower() → «карта».</p>
            </DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>В Planner адрес <code>{"PUT /tasks/{task_id}"}</code> выбирает задачу. Тело обязано задать <code>title</code>, <code>priority</code> и <code>is_done</code>. Явное <code>false</code> означает новый статус. Если обязательного поля нет, HTTP возвращает 422. После проверки тела сервис берёт id и копию списка <code>tags</code> из найденной задачи, затем собирает и проверяет новую <code>Task</code>.</p>
        <p>SQL-строка дополнительно хранит <code>title_normalized</code>. При смене публичного заголовка это поле получает новое значение по правилу Python <code>title.lower()</code>. Схема ответа <code>TaskRead</code> возвращает id и три публичных поля. Теги и технический заголовок увидим отдельным чтением строки.</p>
      </Section>

      <Section number="02" title="Сохраняем и перечитываем ту же строку">
        <p>В примере с книгами создадим таблицу в памяти и поместим в неё две строки. Первичный ключ, то есть id, отличает одну строку от другой. <code>db.get(BookRow, 7)</code> открывает строку с этим ключом, а <code>is None</code> помогает узнать, что записи нет.</p>
        <p>Когда книга найдена, присваиваем новые значения свойствам загруженного объекта. ORM, слой связи Python с таблицей, замечает эти изменения. Вызов <code>flush()</code> отправляет их в базу в текущей транзакции. При успешном выходе из <code>with db.begin()</code> транзакция подтверждается, и после этого возвращается обычная публичная карточка. При исключении до подтверждения изменения отменяются.</p>
        <CodeBlock code={bookOutput} caption="Наблюдение: ответ и состояние из нового чтения" />
        <p>В ответе видим четыре публичных значения. Новая Session читает также метку «дар» и техническое имя «карта». Книга 8 сохранила свои поля. Промах по id 99 даёт сообщение об отсутствии, а прерванная попытка записать «Атлас» оставляет последней сохранённой версию «Карта».</p>
        <details>
          <summary>Посмотреть и запустить полный пример на книгах</summary>
          <p>Можно скопировать код в свободный файл <code>book_put_demo.py</code> и запустить <code>python book_put_demo.py</code> из каталога файла тем же Python с SQLAlchemy, что и в Planner. Пример создаёт собственную базу в памяти.</p>
          <CodeBlock code={bookExample} caption="Полный код отдельного примера" />
        </details>
        <p>В Planner область записи даёт сервис. В готовой подготовке <code>with self.write_scope():</code> открывается до первого <code>self.get_task(task_id)</code>. Первый SQL-запрос сам может начать транзакцию Session. Если открыть область после чтения, попытка начать транзакцию поверх уже начатой может завершиться ошибкой.</p>
        <p>Репозиторий использует ту же Session и выполняет <code>flush()</code>. Подтверждение принадлежит области сервиса, ресурс закрывает тот, кто открыл Session. При промахе репозиторий возвращает <code>None</code>, а отсутствие задачи в сервисе переходит в HTTP 404. Аннотация <code>Task | None</code> описывает эти два возможных результата. Для поиска и преобразования строки пригодится существующий <code>get</code> с <code>task_from_row</code>, а вызовы <code>lower()</code> и <code>flush()</code> уже есть в <code>create</code>.</p>
        <QuizCard
          question="Как проверим, что книга «Карта» доступна после завершения записи?"
          options={["Посмотрим, что присвоено объекту до flush", "Откроем новую Session после выхода из области записи"]}
          correctIndex={1}
          explanation="Новое чтение видит состояние сохранённой строки. Значение в объекте внутри транзакции доступно раньше, чем транзакция подтверждена."
        />
      </Section>

      <Section number="03" title="Проверяем источники полей на другой книге">
        <p>В этой модели сравним, что увидит читатель карточки и что сохранится в строке. Затем сменим адрес и проверим, нужен ли новый ряд при промахе.</p>
        <BookFieldProbe />
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <Lead>Добавим адресную замену в SQL-репозиторий своего Planner. В карточке практики найдём готовую область записи и файл локальных проверок.</Lead>
        <LinkedNotes items={[
          { title: "Найдём задачу для проверки", description: "Запустим подготовку и увидим id, прежние теги и соседнюю запись." },
          { title: "Заменим публичные поля", description: "Напишем один метод replace. PUT вернёт 200 и новые публичные значения под прежним id." },
          { title: "Перечитаем результат", description: "Новое чтение покажет сохранённые теги и пересчитанный заголовок. Запрос по отсутствующему id вернёт 404." },
        ]} />
        <p>Если пропала метка или сохранился старый флаг, вернёмся к схеме источников полей. Если ответ и данные расходятся, проследим путь от первого чтения до подтверждения записи.</p>
      </Section>
    </RichLesson>
  );
}
