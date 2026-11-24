import { CodeBlock, InteractiveTable, Lead, LinkedNotes, QuizCard, RichHero, RichLesson, Section } from "../../shared";

const SETUP = `from sqlalchemy import Boolean, Column, Integer, MetaData, String, Table
from sqlalchemy import case, create_engine, func, select, text
from sqlalchemy.orm import Session

engine = create_engine("sqlite://")
schema = MetaData()
requests = Table(
    "equipment_requests", schema,
    Column("id", Integer, primary_key=True),
    Column("is_done", Boolean, nullable=False),
    Column("quantity", Integer, nullable=False),
    Column("shelf_code", String),
)
schema.create_all(engine)

with engine.begin() as connection:
    connection.execute(requests.insert(), [
        {"id": 8, "is_done": True, "quantity": 1, "shelf_code": "R2"},
        {"id": 9, "is_done": False, "quantity": 3, "shelf_code": None},
        {"id": 10, "is_done": True, "quantity": 2, "shelf_code": "R5"},
    ])`;

const CASE_EXAMPLE = `cols = requests.c
ticket = case((cols.quantity >= 2, cols.id), else_=None)
statement = select(cols.id, ticket).order_by(cols.id)

with Session(engine) as session:
    for row in session.execute(statement):
        print(tuple(row))`;

const COUNT_EXAMPLE = `statement = text("""
    SELECT COUNT(CASE WHEN quantity >= 2 THEN id ELSE NULL END),
           COUNT(id),
           COUNT(CASE WHEN quantity >= 2 THEN id ELSE 0 END)
    FROM equipment_requests
""")

with Session(engine) as session:
    print(tuple(session.execute(statement).one()))`;

const NAMED_EXAMPLE = `statement = select(
    func.count(cols.shelf_code).label("with_shelf"),
    func.count(cols.id).label("registered"),
)

with Session(engine) as session:
    row = session.execute(statement).mappings().one()
    print(row["registered"], row["with_shelf"])`;

export function Lesson73_2() {
  return (
    <RichLesson>
      <RichHero
        chip="Полный SQL-путь Planner"
        title="Получаем сводку из одного SQL-запроса"
        intro="Проследим вклад одной задачи и узнаем, как SQLite из всех строк получает три именованных числа."
      />

      <Section number="00" title="Вступление">
        <Lead>Фильтр по статусу помогает найти открытые задачи. Для сводки нужны сразу все задачи и оба статуса.</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Запрос по статусу возвращает подходящие задачи." },
          { title: "Новый вопрос", description: "Как из одной таблицы сразу получить общий счёт и два счёта по статусам?" },
          { title: "Результат", description: "Один запрос возвращает строку с числами all, open и done. Сверим эти числа до обращения к API." },
        ]} />
        <p>Начнём с реестра заявок на оборудование. Посмотрим, какие номера попадают в каждый счётчик и почему.</p>
      </Section>

      <Section number="01" title="Как одна строка попадает в счётчик">
        <Lead>На стойке выдачи мы отмечаем заявки на два предмета и больше. Для каждой заявки есть ячейка: кладём в неё талон с номером или оставляем её пустой.</Lead>
        <p>
          В реестре три заявки: завершённая рулетка №8 на 1 штуку с полкой R2, открытые маркеры №9 на 3 штуки
          без полки и завершённые кабели №10 на 2 штуки с полкой R5. Для запуска примеров выполните подготовку один раз
          в Python окружения Planner, а следующие фрагменты запускайте по порядку в том же процессе.
          Подготовка создаёт учебную таблицу и добавляет эти три строки. Адрес <code>sqlite://</code> держит базу в памяти процесса.
        </p>
        <CodeBlock code={SETUP} caption="Подготовка одного реестра для всех примеров" />
        <p>
          <code>CASE</code> вычисляет значение для каждой строки по условию. В <code>case()</code> первая пара
          содержит условие и значение при совпадении. Через <code>else_</code> задаём значение для остальных строк.
          Вернём номер заявки, если в ней от двух предметов. Пустую ячейку SQLite показывает как <code>NULL</code>,
          а в Python мы получаем <code>None</code>. Запись <code>requests.c</code> даёт доступ к колонкам таблицы.
        </p>
        <CodeBlock code={CASE_EXAMPLE} caption="Один результат CASE рядом с номером каждой заявки" />
        <p>
          Получаем <code>(8, None), (9, 9), (10, 10)</code>. У заявки 8 один предмет, поэтому её условная ячейка пуста,
          но строка с id 8 присутствует. Знакомый фильтр <code>WHERE</code> убрал бы такую строку из результата.
        </p>
        <p>
          <code>COUNT(выражение)</code> считает заполненные ячейки. Положим в него условное выражение целиком:
          SQLite сначала определит, где лежит номер, а затем посчитает эти номера.
          В примере <code>text()</code> передаёт готовый SQL в <code>execute()</code>. Вызов <code>one()</code> читает одну строку итога.
        </p>
        <CodeBlock code={COUNT_EXAMPLE} caption="Считаем номера заявок с двумя предметами и больше" />
        <p>
          Получаем <code>(2, 3, 3)</code>. Первый счётчик видит номера 9 и 10, общий <code>COUNT(id)</code> видит все три.
          Третий счётчик тоже даёт 3: мы положили в пустую ячейку число 0, а число 0 считается заполненным значением.
        </p>
        <p>
          Теперь держим рядом две колонки, для открытых и для завершённых. В статусном фильтре <code>False</code> означает
          открытую задачу, <code>True</code> завершённую. Эти же условия можно использовать для условного значения.
          У открытых маркеров №9 CASE открытых возвращает 9, CASE завершённых даёт <code>NULL</code>.
          Общий счётчик учитывает заявку 9, потому что её id заполнен.
        </p>
        <p>Перед таблицей попробуйте так же проследить №10. Какие две ячейки получатся и к каким итогам эта строка добавит единицу?</p>
        <InteractiveTable
          title="Какие номера видит каждый счётчик"
          columns={["Заявка (общий id)", "CASE открытых", "CASE завершённых"]}
          rows={[
            { id: "8", label: "8 · Рулетка", values: ["NULL", 8] },
            { id: "9", label: "9 · Маркеры", values: [9, "NULL"] },
            { id: "10", label: "10 · Кабели", values: ["NULL", 10] },
          ]}
        />
        <p>У №10 номер попадает в общий счёт и в счёт завершённых. Итоги по трём колонкам: <code>all=3</code>, <code>open=1</code>, <code>done=2</code>. Каждое число отвечает на свой вопрос о том же наборе строк.</p>
      </Section>

      <Section number="02" title="Как прочитать итоги по именам">
        <Lead>На стойке можно сосчитать все заявки и заявки с назначенной полкой, а затем вписать оба числа в одну строку отчёта под своими заголовками.</Lead>
        <p>
          Подсчёт по набору строк называется агрегацией. Если в <code>SELECT</code> несколько счётчиков без группировки,
          SQLite вернёт одну строку с несколькими колонками. На пустом наборе эта строка тоже есть, а каждый <code>COUNT</code> равен 0.
          <code>func.count()</code> строит выражение SQLAlchemy для <code>COUNT</code>, а <code>.label()</code> подписывает колонку.
          В реестре подпишем число занятых ячеек «Полка» как <code>with_shelf</code>, а число заявок как <code>registered</code>.
        </p>
        <CodeBlock code={NAMED_EXAMPLE} caption="Два подписанных итога в одной строке результата" />
        <p>
          <code>execute()</code> выполняет запрос. <code>mappings()</code> позволяет обращаться к ячейкам по именам, как в словаре.
          <code>one()</code> ожидает ровно одну строку. Получаем <code>3 2</code>: id есть у всех, а полка назначена только R2 и R5.
          Вызов <code>print()</code> читает <code>registered</code> первым, хотя в запросе он второй.
          Если переставить колонки запроса, обе величины останутся под своими именами. При таком чтении доступны все колонки.
          Знакомый <code>scalars()</code> годится для <code>select(TaskRow)</code>, где нужен один элемент из каждой строки.
        </p>
        <QuizCard
          question="Что изменится, если снова открыть заявку №8?"
          description="Сначала решите, что вернут два CASE для №8 и что станет с тремя итогами. Затем выберите прогноз."
          orientation="vertical"
          options={[
            { title: "Открытые: 8, завершённые: NULL", description: "all=3, open=1, done=2" },
            { title: "Открытые: 8, завершённые: NULL", description: "all=3, open=2, done=1" },
            { title: "Открытые: 8, завершённые: 0", description: "all=3, open=2, done=2" },
          ]}
          correctIndex={1}
          explanation="Номер 8 переходит из колонки завершённых в колонку открытых. Первых заполненных ячеек становится 2, вторых 1. Общий id остался тем же, поэтому всего по-прежнему 3. Ноль был бы посчитан как значение."
        />
      </Section>

      <Section number="03" title="Что мы будем делать в практике">
        <Lead>Продолжим существующий Planner в редакторе с готовым набором из трёх задач в отдельной SQLite.</Lead>
        <LinkedNotes items={[
          { title: "Проследим одну строку", description: "Выберем задачу среди одноимённых, предскажем два условных значения и сравним их с пробой." },
          { title: "Сверим именованные числа до HTTP", description: "Напишем метод сводки SQL-репозитория и напрямую проверим все три ключа на смешанном наборе." },
        ]} />
        <p>Запишите результат первой карточки. Во второй он поможет проверить, с каким именем связать вклад открытой или завершённой задачи.</p>
      </Section>
    </RichLesson>
  );
}
