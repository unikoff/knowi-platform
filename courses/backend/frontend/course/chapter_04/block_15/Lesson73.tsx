import {
  CodeBlock,
  Diagram,
  DiagramArrow,
  DiagramFlow,
  DiagramNode,
  InteractiveTable,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const EQUIPMENT_ROWS = [
  { id: 10, title: "Лампа", closed: true },
  { id: 9, title: "Крепёж AX", closed: false },
  { id: 11, title: "Крепёж 'X'", closed: false },
  { id: 8, title: "КРЕПЁЖ_%", closed: true },
];

const EQUIPMENT_SETUP = `from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

class Base(DeclarativeBase):
    pass

class EquipmentRequest(Base):
    __tablename__ = "equipment_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    caption_lower: Mapped[str]
    is_closed: Mapped[bool]

engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
session = Session(engine)
rows = [
${EQUIPMENT_ROWS.map(({ id, title, closed }) =>
  `    (${id}, ${JSON.stringify(title)}, ${closed ? "True" : "False"}),`).join("\n")}
]
session.add_all(
    EquipmentRequest(id=number, caption_lower=title.lower(), is_closed=closed)
    for number, title, closed in rows
)
session.commit()`;

const FILTER_EXAMPLE = `from sqlalchemy import select

all_ids = select(EquipmentRequest.id)
open_ids = all_ids.where(EquipmentRequest.is_closed == False)

print(sorted(session.scalars(all_ids).all()))
print(sorted(session.scalars(open_ids).all()))`;

const ORDER_EXAMPLE = `newest_first = open_ids.order_by(EquipmentRequest.id.desc())

print(session.scalars(newest_first).all())
print(session.scalars(newest_first.limit(1)).all())`;

const INPUT_EXAMPLE = `for note in ("  ЛАМПА  ", "   ", "  КРЕПЁЖ_%  "):
    print(repr(note.strip().lower()))`;

const POSITION_EXAMPLE = `from sqlalchemy import func

position = func.instr(EquipmentRequest.caption_lower, "'x'")
found = position > 0
diagnosis = select(EquipmentRequest.id, position, found).order_by(EquipmentRequest.id)
print(session.execute(diagnosis).all())`;

export function Lesson73({ module }: { module?: string } = {}) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Полный SQL-путь Planner"}
        title="Отбираем нужные строки в SQL"
        intro="Когда список задач растёт, можно попросить SQLite вернуть только нужные записи. Посмотрим, как задать это условие и какие строки вернутся."
      />

      <Section number="00" title="Вступление">
        <Lead>В SQL-репозитории Planner уже можно прочитать задачи. Теперь представим два запроса: показать первые открытые задачи и найти названия с определённым фрагментом.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "SQLite возвращает строки, а репозиторий превращает их в объекты Task." },
          { title: "Новый вопрос", description: "Как объяснить базе, какие строки подходят и какие из них должны идти первыми?" },
          { title: "Результат", description: "Получим нужные задачи по статусу и найдём буквальный фрагмент в их названиях." },
        ]} />
        <p>Проследим оба запроса на реестре заявок на оборудование. Строки останутся теми же, а условие будет меняться.</p>
      </Section>

      <Section number="01" title="Как условие оставляет нужные строки">
        <Lead>Фильтр отвечает на вопрос о каждой строке. Если нам нужны открытые заявки, SQLite оставит строки, где отметка о закрытии равна <code>False</code>.</Lead>
        <p>Представим четыре карточки на стойке склада. Когда просят показать открытые заявки, сотрудник смотрит на отметку каждой карточки.</p>
        <InteractiveTable
          title="Реестр заявок на оборудование"
          columns={["id", "Название", "Закрыта"]}
          rows={EQUIPMENT_ROWS.map(({ id, title, closed }) => ({
            id: String(id),
            label: <code>{id}</code>,
            values: [title, closed ? "Да" : "Нет"],
          }))}
        />
        <p>
          Для примеров соберём эти четыре строки в SQLite в памяти. <code>EquipmentRequest</code> описывает
          номер, название в нижнем регистре и отметку о закрытии. После подготовки <code>session</code> может
          читать эти строки. Фрагменты ниже выполняем в той же консоли Python. Пустая строка после каждого класса
          завершает его определение в консоли.
        </p>
        <CodeBlock code={EQUIPMENT_SETUP} caption="Готовим четыре заявки для примеров" />
        <p>
          <code>select</code> указывает, что читаем. Здесь это только номера заявок. <code>where</code> добавляет
          условие для SQLite. Сравнение ORM-колонки через <code>==</code> описывает вопрос к строке. Вызов
          {" "}<code>where</code> возвращает новое описание запроса, поэтому сохраняем результат отдельно.
        </p>
        <CodeBlock code={FILTER_EXAMPLE} caption="Одна таблица, два запроса: все номера и номера открытых заявок" />
        <p>
          Первый вывод <code>[8, 9, 10, 11]</code> содержит все номера. Второй вывод <code>[9, 11]</code> содержит
          только номера карточек без отметки. Запрос выполняется в <code>session.scalars(...)</code>, а
          {" "}<code>.all()</code> даёт список. Здесь <code>sorted</code> нужен для удобства сравнения напечатанных
          номеров. Порядок внутри запроса зададим отдельно.
        </p>
        <p>
          Если сотруднику сказали «только открытые», значение <code>False</code> передаёт точное пожелание. Если
          про статус вообще не спрашивали, аргумент будет <code>None</code>. Для <code>False</code> проверка
          {" "}<code>if value</code> тоже не сработает. Чтобы отличить эти случаи, сравниваем само значение с
          {" "}<code>None</code>.
        </p>
        <QuizCard
          question={<>Какие номера получим, если в прежнем условии поставить <code>True</code>?</>}
          description="Сверьте отметки в реестре и выберите список номеров."
          options={["[9, 11]", "[8, 10]", "[8, 9, 10, 11]"]}
          correctIndex={1}
          explanation="Пройдут 8 и 10. У обеих заявок есть отметка о закрытии, поэтому сравнение с True выполняется для этих строк."
        />
        <p>
          В Planner список собирается из ORM-строк. В имеющемся <code>list()</code> уже видны
          {" "}<code>select(TaskRow)</code> и преобразование <code>task_from_row</code>. Для настройки статуса пригодится
          тот же способ прочитать результат.
        </p>
      </Section>

      <Section number="02" title="Порядок строк и предел выдачи">
        <Lead>Сортировка решает, какая из подходящих строк стоит первой. Предел отвечает, сколько строк взять с начала упорядоченного списка.</Lead>
        <p>
          Сотрудник оставил карточки 9 и 11. Если просят показать сначала заявку с большим номером, он кладёт
          11 перед 9. Если просят одну карточку, он берёт 11.
        </p>
        <p>
          <code>order_by</code> задаёт ключ сортировки. Здесь ключом будет номер. <code>desc()</code> ставит
          большие номера первыми, <code>asc()</code> ставит меньшие. Вызов <code>limit(1)</code> оставляет в
          результате до одной строки. Оба метода создают новое описание запроса.
        </p>
        <CodeBlock code={ORDER_EXAMPLE} caption="Из открытых заявок берём одну с наибольшим номером" />
        <p>
          Получаем <code>[11, 9]</code> без предела и <code>[11]</code> с пределом. Смена направления
          меняет порядок только тех строк, которые уже подходят по статусу.
        </p>
        <QuizCard
          question={<>Что напечатается, если заменить <code>desc()</code> на <code>asc()</code> и оставить <code>limit(1)</code>?</>}
          description="Сначала отметьте подходящие заявки, затем расположите их в новом порядке."
          options={["[8]", "[9, 11]", "[9]"]}
          correctIndex={2}
          explanation="Из открытых подходят 9 и 11. При возрастании первой идёт 9, а предел оставляет только одну строку. Закрытая 8 отсеялась раньше."
        />
        <Diagram caption="Как из реестра получилась одна открытая заявка">
          <DiagramFlow label="От исходных заявок к первому номеру в выдаче">
            <DiagramNode title="Все заявки" shape="database"><p>10, 9, 11, 8</p></DiagramNode>
            <DiagramArrow label="Оставляем открытые" />
            <DiagramNode title="Подходят"><p>9 и 11</p></DiagramNode>
            <DiagramArrow label="По убыванию id" />
            <DiagramNode title="Порядок"><p>11, 9</p></DiagramNode>
            <DiagramArrow label="Берём одну" />
            <DiagramNode title="Ответ"><p>11</p></DiagramNode>
          </DiagramFlow>
        </Diagram>
        <p>
          Схема помогает прочитать смысл результата. В реальном следе SQL ищем <code>WHERE</code>,
          {" "}<code>ORDER BY</code> и, если задан предел, <code>LIMIT</code>. Верные номера могут получиться и
          после среза готового списка в Python. Предложение <code>LIMIT</code> в выполненном SQL показывает, что
          число строк ограничила база. SQLAlchemy может дописать рядом <code>OFFSET 0</code>, это чтение без пропуска строк.
        </p>
        <p>
          В репозитории отсутствие предела обозначается <code>None</code>. Обычный CLI-список запрашивает все
          задачи. HTTP по умолчанию просит 10 записей и принимает значения от 1 до 50. Эти границы уже проверяет
          HTTP-слой.
        </p>
      </Section>

      <Section number="03" title="От ввода к буквальному совпадению">
        <h3>Подготовка строки поиска</h3>
        <p>
          Подготовка ввода уже знакома по JSON-поиску. При поиске по записке складской
          сотрудник пропускает зазоры по краям и читает слово независимо от регистра букв. В Python
          {" "}<code>strip()</code> убирает пробельные символы по краям, а <code>lower()</code> переводит буквы в нижний регистр.
        </p>
        <CodeBlock code={INPUT_EXAMPLE} caption="Смотрим на записки отдельно, до запроса к таблице" />
        <p>
          <code>repr</code> показывает границы строки в кавычках. Получаем <code>'лампа'</code>, затем
          {" "}<code>''</code> и <code>'крепёж_%'</code>. Видно, что у пустой записки нет фрагмента, а знаки
          {" "}<code>_</code> и <code>%</code> остались на месте. Пробелы внутри слова или фразы тоже сохраняются.
        </p>
        <p>
          В JSON-поиске пустой фрагмент уже давал <code>[]</code> до чтения данных. Кладовщик поступит так же:
          для пустой записки он вообще не открывает реестр. В SQL-репозитории это видно по следу. Для непустой
          записки «молоток» также можно получить <code>[]</code>, но сначала SQLite выполнит <code>SELECT</code>.
        </p>

        <h3>Буквальный фрагмент в запросе</h3>
        <p>
          Буквальный поиск ищет подряд написанные символы. На карточке 11 можно обвести фрагмент
          {" "}<code>'X'</code> вместе с двумя кавычками. У карточки 9 тоже есть X, но кавычек рядом нет.
        </p>
        <p>
          В SQLite функция <code>instr(строка, фрагмент)</code> возвращает позицию первого совпадения.
          Позиции начинаются с 1. Если фрагмента нет, результат равен 0. Для пустого фрагмента SQLite возвращает 1, поэтому его отсеиваем раньше. SQLite получает заранее заполненное
          название в нижнем регистре из <code>caption_lower</code>.
        </p>
        <CodeBlock code={POSITION_EXAMPLE} caption="Смотрим позицию и ответ на вопрос, найден ли фрагмент" />
        <p>
          <code>func.instr</code> описывает вызов SQL-функции. <code>execute</code> сохраняет все три колонки
          запроса, поэтому для каждой строки видны номер, позиция и результат сравнения с нулём. У 8, 9 и 10
          позиция 0 и ответ <code>False</code>. У «крепёж 'x'» открывающая кавычка стоит на восьмой позиции,
          ответ <code>True</code>. Если оставить только совпадения, в выдаче будет одна заявка 11.
        </p>
        <p>
          SQLAlchemy отправляет строковый аргумент отдельно от текста SQL. В следе у выражения
          {" "}<code>instr(..., ?) &gt; ?</code> виден знак <code>?</code> на месте значения. Среди параметров
          увидим <code>"'x'"</code> и порог <code>0</code>. Так кавычки остаются частью
          искомого текста. В Planner для сравнения уже есть колонка <code>title_normalized</code>. Она хранит
          результат Python <code>title.lower()</code>, в том числе для кириллицы.
        </p>
        <p>
          Знак <code>%</code> тоже читается буквально. Для него <code>instr</code> даст положительную позицию
          в «крепёж_%» и ноль в «крепёж ax». Для сравнения, в шаблоне <code>LIKE '%'</code> тот же знак
          означает последовательность любой длины, а <code>_</code> в шаблоне означает один произвольный знак.
          У Planner ввод <code>%</code> или <code>_</code> ищет именно написанный символ.
        </p>
        <QuizCard
          question="Что увидим для двух записок « _ » и «   »?"
          description="Возьмём тот же реестр. Сначала определите подготовленный фрагмент, затем решите, выполнится ли запрос."
          options={[
            "Первая даст [8] после запроса, вторая даст [] без запроса",
            "Обе дадут [] без запроса",
            "Первая даст все номера, вторая даст [] после запроса",
          ]}
          orientation="vertical"
          correctIndex={0}
          explanation="После обработки первой записки останется знак _. Он есть в названии 8, поэтому позиция только там положительная. Из второй записки получится пустая строка. Поиск завершается до SELECT."
        />
        <p>
          Если результат выглядит одинаково, сравним след. Так узнаем, выполнялся ли <code>SELECT</code>.
        </p>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Отберём по статусу", description: "Сравним открытые задачи со всеми задачами при отсутствии фильтра." },
          { title: "Упорядочим и ограничим", description: "С пределом увидим первые id в каждом направлении, а в следе SQL найдём ORDER BY и LIMIT." },
          { title: "Проверим границу поиска", description: "Увидим два пустых результата. Только непустой запрос без совпадений дойдёт до SELECT." },
          { title: "Найдём буквальный фрагмент", description: "Из двух похожих названий останется то, где действительно напечатан нужный знак." },
        ]} />
        <p>В первой карточке подготовим один учебный набор Planner для всех четырёх шагов.</p>
      </Section>
    </RichLesson>
  );
}
