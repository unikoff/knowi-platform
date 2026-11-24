import {
  CodeBlock,
  InteractiveTable,
  Lead,
  LinkedNotes,
  QuizCard,
  RichHero,
  RichLesson,
  Section,
} from "../../shared";

const CATALOG = `from sqlalchemy import create_engine

engine = create_engine("sqlite://", hide_parameters=True)
with engine.begin() as connection:
    connection.exec_driver_sql(
        "CREATE TABLE equipment (id INTEGER PRIMARY KEY, category TEXT)"
    )
    connection.exec_driver_sql(
        "INSERT INTO equipment (id, category) VALUES (?, ?)",
        [(12, "stand"), (17, "cables"), (23, "case")],
    )`;

const CAPTURE = `from contextlib import contextmanager
from dataclasses import dataclass, field
from sqlalchemy import event


@dataclass(frozen=True)
class SqlRecord:
    statement: str
    _parameters: object = field(repr=False)


@contextmanager
def capture_sql(engine):
    trace = []
    event_name = "before_cursor_execute"

    def record(_connection, _cursor, statement, parameters, _context, _executemany):
        trace.append(SqlRecord(statement, parameters))

    event.listen(engine, event_name, record)
    try:
        yield trace
    finally:
        event.remove(engine, event_name, record)`;

const FIRST_READ = `cables_query = "SELECT id FROM equipment WHERE category = ? ORDER BY id"

with capture_sql(engine) as trace:
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA database_list").all()
        ids = connection.exec_driver_sql(cables_query, ("cables",)).scalars().all()

print(ids)
for entry in trace:
    print(entry.statement)`;

const CONTROL = `marker = "catalog-marker"

with capture_sql(engine) as probe:
    with engine.connect() as connection:
        connection.exec_driver_sql("SELECT ?", (marker,)).scalar_one()

before = len(probe)
with engine.connect() as connection:
    connection.exec_driver_sql("SELECT ?", (marker,)).scalar_one()

print(probe[0].statement)
print(marker in repr(probe))
print(before, len(probe))`;

const FILTER = `with capture_sql(engine) as sql_trace:
    with engine.connect() as connection:
        sql_ids = connection.exec_driver_sql(cables_query, ("cables",)).scalars().all()

full_query = "SELECT id, category FROM equipment ORDER BY id"
with capture_sql(engine) as python_trace:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(full_query).all()

python_ids = [row.id for row in rows if row.category == "cables"]
print(sql_ids, [row.id for row in rows])
print(sql_ids, python_ids)`;

const PLAN_METHOD = `    def explain(self, engine):
        with engine.connect() as connection:
            rows = connection.exec_driver_sql(
                "EXPLAIN QUERY PLAN " + self.statement, self._parameters
            ).all()
        return [row.detail for row in rows]`;

const PLAN_READ = `print(sql_trace[0].explain(engine))
print(python_trace[0].explain(engine))`;

const PLANNER_BIND = `from fastapi.testclient import TestClient

from app.api import app

with TestClient(app) as client:
    factory = app.state.session_factory
    with factory() as session:
        engine = session.get_bind()
    engine.hide_parameters = True

    # Здесь подготовим данные, затем откроем окно на один GET.`;

export function Lesson77() {
  return (
    <RichLesson>
      <RichHero
        chip="Надёжность Planner"
        title="Наблюдаем SQL одной выборки"
        intro="Свяжем ответ Planner с командой, которую получила SQLite, и прочитаем план этой команды."
      />

      <Section number="00" title="Вступление">
        <Lead>Planner вернул нужную заявку. Где из списка выбрали подходящие задачи?</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "Умеем сверять список задач после отбора по статусу, сортировки и ограничения выдачи." },
          { title: "Новый вопрос", description: "Как определить, где отобраны строки, если оба способа дают одинаковый HTTP-ответ?" },
          { title: "Результат", description: "Поймаем SQL одного GET и выясним, что видно из запроса и его плана." },
        ]} />
        <p>Начнём с каталога оборудования. Там ответ короткий, поэтому легко проследить, что меняется между командой и результатом.</p>
      </Section>

      <Section number="01" title="Окно наблюдения за одним действием">
        <Lead>На стойку каталога поступают поручения. Попросим наблюдателя записать только те, которые прошли через эту стойку, пока окно открыто.</Lead>
        <p>
          В программе роль стойки выполняет экземпляр <code>Engine</code>, через который идут команды к базе.
          Список команд за выбранный промежуток назовём <strong>SQL-следом</strong>.
          Подписка привязывает функцию к событию <code>before_cursor_execute</code> на этом Engine.
          SQLAlchemy вызывает её непосредственно перед передачей команды драйверу SQLite.
        </p>
        <p>
          Из каталога будем получать кабели. В учебном <code>sql_catalog.py</code> соберите фрагменты этого раздела сверху вниз.
          Из каталога <code>project/</code> запустите <code>./.venv/Scripts/python.exe sql_catalog.py</code> в окружении
          Planner. Сначала готовим SQLite в памяти и три карточки. Вторая команда передаёт три пары «номер, категория»
          отдельно от текста INSERT. Выход из <code>engine.begin()</code> сохраняет строки.
        </p>
        <CodeBlock code={CATALOG} caption="Каталог в SQLite, без файлов и HTTP-сервера" />
        <p>
          Теперь соберём окно. <code>event.listen()</code> получает Engine, имя события и функцию, которую нужно вызвать.
          При этом событии SQLAlchemy передаёт шесть аргументов. Берём <code>statement</code>, то есть готовый текст
          команды, и <code>parameters</code>, то есть её отдельные значения. Остальные аргументы здесь не используются.
          Например, в <code>WHERE category = ?</code> знак <code>?</code> оставляет место для категории в кортеже
          <code> ("cables",)</code>.
        </p>
        <p>
          <code>@dataclass</code> даёт короткую запись объекта из двух полей. <code>frozen=True</code> сохраняет запись
          после её создания, а <code>field(repr=False)</code> исключает параметры из обычного печатного представления.
          <code>hide_parameters=True</code> отдельно скрывает связанные значения в сообщениях самого SQLAlchemy.
          Включать <code>echo</code> для захвата не нужно.
        </p>
        <p>
          <code>@contextmanager</code> позволяет использовать функцию через <code>with</code>. Код до <code>yield</code>
          подписывает наблюдателя, а <code>yield trace</code> отдаёт список после <code>as</code>. При выходе из блока
          выполняется <code>finally</code> и снимает именно эту подписку, даже если внутри возникла ошибка.
        </p>
        <CodeBlock code={CAPTURE} caption="Подписка, список команд и снятие подписки" />
        <p>
          Сначала открываем окно, затем получаем соединение и выполняем две команды. <code>PRAGMA database_list</code>
          сообщает сведения об открытой базе. <code>SELECT</code> читает выдачу каталога. <code>scalars().all()</code>
          собирает значения первого столбца, в нашем случае номера.
        </p>
        <CodeBlock code={FIRST_READ} caption="Наблюдаем команды от открытия до закрытия окна" />
        <p>
          Получаем <code>[17]</code>, затем строки <code>PRAGMA database_list</code> и
          <code> SELECT id FROM equipment WHERE category = ? ORDER BY id</code>. Обе команды в следе, но только SELECT
          выдал карточку. Метки <code>BEGIN</code> и <code>ROLLBACK</code> могут встретиться в журнале Engine при
          открытии и завершении транзакции. В нашем списке событие записывает команды курсора.
        </p>
        <InteractiveTable
          title="Какие команды попадают в след"
          columns={["Действие", "Что увидит наблюдатель"]}
          rows={[
            { id: "prepare", label: "До подписки", values: ["Создание и заполнение уже завершены. В следе этих команд нет."] },
            { id: "observe", label: "Внутри окна", values: [<>В следе есть <code>PRAGMA</code> и <code>SELECT</code>. Карточки читает только SELECT.</>] },
            { id: "after", label: "После снятия подписки", values: ["Повторное чтение не пополняет старый след."] },
          ]}
        />
        <p>
          Если подписаться на другой Engine с тем же адресом или закрыть окно до вызова,
          чтение по-прежнему может вернуть нужную карточку, а список останется пуст. Проверяем оба места:
          куда подписались и успел ли вызов пройти внутри <code>with</code>.
        </p>
        <p>
          Для короткой проверки передадим условный маркер как отдельное значение <code>SELECT ?</code>.
          <code>scalar_one()</code> читает одно значение, а <code>repr(probe)</code> даёт печатное представление списка.
          Затем повторим запрос после выхода из окна и сравним длину списка.
        </p>
        <CodeBlock code={CONTROL} caption="Доступная проба сокрытия значения и снятия подписки" />
        <p>
          Вывод по строкам <code>SELECT ?</code>, <code>False</code>, <code>1 1</code>. Маркер обработан SQLite,
          а в представлении следа остался только заполнитель. Второй вызов прошёл по тому же Engine после снятия
          подписки, поэтому длина сохранилась. В практике повторим эту короткую пробу на временной базе Planner
          отдельно от окна GET.
        </p>
        <p>
          Для Planner сначала зададим <code>DATABASE_URL</code> новой учебной базы в терминале, до импорта
          <code> app.api</code>, и создадим схему. Когда входим в <code>with TestClient(app)</code>, FastAPI выполняет
          запуск приложения. Если фабрика создаётся при запуске, до входа в блок атрибута
          <code> app.state.session_factory</code> ещё нет. Читаем его уже внутри блока, когда API готов принимать вызовы.
        </p>
        <p>
          <code>factory()</code> открывает короткую Session, а <code>session.get_bind()</code> возвращает её Engine
          без выполнения SQL. Выход из внутреннего <code>with</code> закрывает Session. Engine остаётся общим для
          подготовки и GET, пока открыт клиент. Сначала запишем данные и сделаем отдельную пробу с маркером.
          Затем подпишемся на один GET. В штатном запуске подписки нет.
        </p>
        <CodeBlock code={PLANNER_BIND} caption="Фрагмент для выбора уже созданного Engine" />
      </Section>

      <Section number="02" title="Где выполнен отбор">
        <Lead>Стойка может выдать только кабели по талону. Или выдать весь каталог, а получатель сам оставит кабели. В руках у получателя в итоге одна и та же карточка.</Lead>
        <p>
          Условие, по которому строка подходит для выдачи, называется <strong>предикатом</strong>.
          В каталоге передадим это условие базе через <code>WHERE category = ?</code>. Во втором варианте запросим
          все строки, а условие выполним в Python. Добавьте следующий фрагмент в тот же файл.
        </p>
        <CodeBlock code={FILTER} caption="Два окна захвата для двух способов отбора" />
        <p>
          Первая строка вывода <code>[17] [12, 17, 23]</code> показывает номера, пришедшие из SQLite.
          Во второй строке оба результата <code>[17]</code>. При наличии <code>WHERE</code> SQLite передаёт
          одну строку. Без предиката Python получает три и оставляет одну.
        </p>
        <QuizCard
          question="Новое поручение: найти футляры, category = case. Что нужно увидеть, чтобы определить место отбора?"
          description="Номер футляра 23. Предскажите выдачу SQLite и выберите достаточное свидетельство."
          orientation="vertical"
          options={[
            "Достаточно ответа [23] в обоих вариантах.",
            "Нужен след SQL. С WHERE из SQLite придёт [23], без WHERE из неё придут [12, 17, 23].",
            "Нужен след SQL. Раз итог [23], из SQLite тоже придёт [23] в обоих случаях.",
          ]}
          correctIndex={1}
          explanation="По итогу видно, что осталось у клиента. Предикат категории в отправленном SELECT показывает, что SQLite получила это условие. Если SELECT читает весь каталог, до Python дойдут все три строки, хотя клиент получит только футляр."
        />
        <p>
          В знакомой выборке задач <code>WHERE</code> отбирает нужный статус, <code>ORDER BY</code> задаёт порядок
          номеров, а <code>LIMIT</code> ограничивает длину ответа. В SQL для SQLite булев статус может быть записан
          как <code>0</code> или <code>1</code>, а числа предела и сдвига могут прийти через <code>LIMIT ? OFFSET ?</code>.
          Чтобы сопоставить следы, прочитайте таблицу и смысл условия, порядка и предела. Переносы строк и регистр
          ключевых слов здесь роли не играют.
        </p>
        <p>
          По такому же признаку читали знакомую сводку. <code>COUNT(CASE ...)</code> в SELECT показывает, что число
          посчитала SQLite. В HTTP поле <code>total</code> получается из <code>all</code> при сборке ответа.
        </p>
      </Section>

      <Section number="03" title="Как SQLite добирается до строк">
        <Lead>Сотрудник стойки может пролистать весь каталог в поисках кабелей. Талон задаёт, что выдавать, а листание описывает способ поиска.</Lead>
        <p>
          <code>EXPLAIN QUERY PLAN</code> просит SQLite описать способ доступа для конкретной команды.
          Добавим этот метод к классу <code>SqlRecord</code>, сразу после поля <code>_parameters</code>.
          Метод берёт прежний текст и тот же кортеж значений, чтобы заполнители <code>?</code> получили нужные
          значения. В каждой строке плана <code>detail</code> содержит описание, которое будем читать.
        </p>
        <CodeBlock code={PLAN_METHOD} caption="Метод explain внутри класса SqlRecord" />
        <p>
          Допишите два вызова в конец учебного файла и запустите его снова. Используем записи, которые уже получили в двух окнах.
        </p>
        <CodeBlock code={PLAN_READ} caption="Читаем планы после снятия обеих подписок" />
        <p>
          Для каталога без индекса категории обе строки будут <code>['SCAN equipment']</code>. SQLite обходит
          таблицу при полном чтении и при чтении с условием. Во втором случае она отдаёт только подходящие строки.
          План описывает способ доступа. Условие выдачи остаётся в тексте <code>SELECT</code>.
        </p>
        <QuizCard
          question="Запись говорит SCAN equipment, а в SELECT стоит WHERE category = ?. Что произошло?"
          orientation="vertical"
          options={[
            "SQLite обошла таблицу и вернула только строки подходящей категории.",
            "SQLite отдала все строки, потому что SCAN означает отбор в Python.",
            "SQLite сразу нашла готовые подходящие строки по индексу категории.",
          ]}
          correctIndex={0}
          explanation="Здесь SCAN означает обход таблицы. Во время обхода SQLite применяет условие WHERE и возвращает только совпавшие строки. В примере для категории нет индекса."
        />
        <p>
          Если передать только SQL со знаком <code>?</code>, SQLite сообщит, что значения для заполнителей не переданы.
          Вызывайте <code>entry.explain(engine)</code> после закрытия окна захвата. В практике сделаем это внутри
          открытого <code>TestClient</code>, пока приложение ещё использует этот Engine.
          Внутри окна захвата <code>EXPLAIN QUERY PLAN</code> сам добавил бы команду в след.
        </p>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Поймаем один вызов", description: "Выберем GET для задачи оператора и свяжем id ответа с SELECT в коротком окне." },
          { title: "Выберем доказательство по следу", description: "Сравним две формы SQL и укажем, где выполняется отбор." },
          { title: "Прочитаем план", description: "Получим способ доступа SQLite для той же записи и проверим, что след после закрытия не пополняется." },
        ]} />
        <p>Общий захват пригодится при следующей проверке выборки на собственных данных.</p>
      </Section>
    </RichLesson>
  );
}
