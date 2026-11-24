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

const RECEIPT_ORDERS = `for receipts in ([4], [4, 12]):
    ascending = sorted(receipts)
    descending = sorted(receipts, reverse=True)
    print(ascending, descending)`;

const CATALOGUE_QUERIES = `from sqlalchemy import create_engine, text

engine = create_engine("sqlite://", echo=False, hide_parameters=True)

with engine.begin() as connection:
    connection.execute(text("CREATE TABLE equipment (id INTEGER, category TEXT)"))
    insert_row = text("INSERT INTO equipment VALUES (:id, :category)")
    connection.execute(insert_row, [
        {"id": 31, "category": "фонари"},
        {"id": 14, "category": "штативы"},
    ])

category = "фонари"
base = "SELECT id, category FROM equipment"
with engine.connect() as connection:
    filtered = connection.execute(
        text(base + " WHERE category = :category"), {"category": category}
    ).all()
    all_rows = connection.execute(text(base + " ORDER BY id")).all()

print("SQLite отобрала:", [row.id for row in filtered])
print("Из SQLite пришли все:", [row.id for row in all_rows])
print("Python отобрал:", [row.id for row in all_rows if row.category == category])
engine.dispose()`;

const NORMALIZE_SQL = `line = "  SELECT id\\nFROM equipment\\nWHERE category = ?  "
normalized = " ".join(line.lower().split())
print(normalized)
print("where category" in normalized)`;

const CATALOGUE_SESSIONS = `import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def catalogue_sessions(tmp_path):
    url = URL.create("sqlite", database=(tmp_path / "catalogue.sqlite3").as_posix())
    engine = create_engine(url)
    try:
        yield sessionmaker(bind=engine)
    finally:
        engine.dispose()`;

export function Lesson78({ module }: { module?: string } = {}) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? "Надёжность и запросы Planner"}
        title="Защищаем фильтрацию и limit в одном SQL-запросе"
        intro="Подберём данные, на которых видна ошибка выборки, и проверим, какую работу действительно выполнила SQLite."
      />

      <Section number="00" title="Вступление">
        <Lead>Откроем список задач. Номера совпали с ожиданием. Могли ли те же данные скрыть ошибку?</Lead>
        <LinkedNotes items={[
          { title: "Точка старта", description: "У одного GET мы умеем прочитать ответ и увидеть SQL в окне наблюдения." },
          { title: "Новый вопрос", description: "Какие строки заставят ошибочное правило дать другой ответ?" },
          { title: "Результат", description: "Подберём свои строки и защитим результат выборки и форму SELECT двумя проверками." },
        ]} />
        <p>Начнём с данных. Затем посмотрим, как два запроса могут привести к одинаковому списку.</p>
      </Section>

      <Section number="01" title="Данные, на которых видно ошибку">
        <Lead>Различающий набор даёт разные результаты для двух правил. Сначала сравним правила на одном входе, затем выберем данные для проверки.</Lead>
        <p>
          Представим стойку выдачи квитанций. По правилу меньший номер должен лежать первым.
          Если на стойке только квитанция 4, сотрудник положит её первой при любом направлении.
          Добавим квитанцию 12 и посмотрим, как изменится порядок.
        </p>
        <CodeBlock code={RECEIPT_ORDERS} caption="Сравниваем порядок одних и тех же квитанций" />
        <p>
          Вызов <code>sorted</code> возвращает новый список. По умолчанию номера идут от меньшего к большему.
          Аргумент <code>reverse=True</code> меняет направление. Напечатаем две версии ряда на стойке.
        </p>
        <InteractiveTable
          title="Где становится видно направление"
          columns={["Номера на стойке", "От меньшего к большему", "От большего к меньшему"]}
          rows={[
            { id: "one", label: "4", values: [<code>[4]</code>, <code>[4]</code>] },
            { id: "two", label: "4 и 12", values: [<code>[4, 12]</code>, <code>[12, 4]</code>] },
          ]}
        />
        <p>
          На одном номере обе проверки ожидали бы <code>[4]</code>. С двумя номерами первый элемент уже
          выдаёт направление. Даже если положить квитанцию 12 на стойку раньше, сортировка всё равно
          сравнит номера.
        </p>
        <QuizCard
          question="На стойке одна квитанция 8. Какое изменение поможет проверить направление?"
          options={["Записать номер 8 ещё раз", "Добавить квитанцию 3", "Убрать квитанцию 8"]}
          correctIndex={1}
          explanation="Квитанции 3 и 8 могут поменяться местами. Повтор одного номера и пустой ряд выглядят одинаково при обоих направлениях."
        />
        <QuizCard
          question="На стойку сначала положили квитанцию 19, затем 6. Что напечатает пример для этого ряда?"
          options={["[19, 6] и [19, 6]", "[19, 6] и [6, 19]", "[6, 19] и [19, 6]"]}
          correctIndex={2}
          explanation="У sorted обычный порядок начинается с 6. С reverse=True первым идёт 19."
        />
        <p>
          В Planner спор будет о границе выдачи. По привычному правилу сначала остаются задачи нужного статуса,
          затем их упорядочивают и берут первые строки. Чтобы проверить перестановку стадий, запишем
          промежуточные ряды для обоих вариантов и найдём данные, на которых итог расходится.
        </p>
      </Section>

      <Section number="02" title="Одинаковый ответ, разные SQL-следы">
        <Lead>Форма SQL показывает, какие операции передали SQLite. Она пригодится, когда по ответу невозможно узнать, где прошёл отбор.</Lead>
        <p>
          Попросим кладовщика выдать ведомость фонарей. Он может сам оставить нужные строки.
          Он также может выдать всю ведомость и поручить нам отбор. В обоих случаях мы увидим один и тот же список фонарей.
        </p>
        <p>
          Выполним оба запроса на одной SQLite в памяти. В консоли Python из окружения Planner вставляйте
          пример по блокам. Пустая строка после блока <code>with</code> завершает его. Первый <code>with</code>
          готовит две строки и подтверждает запись. Затем откроем соединение для чтения.
        </p>
        <CodeBlock code={CATALOGUE_QUERIES} caption="Фонари и штативы на одной ведомости" />
        <InteractiveTable
          title="Что пришло из SQLite"
          columns={["Ведомость", "Строки от SQLite", "Итоговые номера"]}
          rows={[
            { id: "sql-filter", label: "Отобрана в SQLite", values: [<code>[31]</code>, <code>[31]</code>] },
            { id: "python-filter", label: "Отобрана после чтения", values: [<code>[14, 31]</code>, <code>[31]</code>] },
          ]}
        />
        <p>
          В первом запросе <code>text(...)</code> оформляет команду, а <code>:category</code> оставляет
          место для значения из словаря. SQLite сравнивает категорию и отдаёт строку 31. Во втором запросе
          SQLite упорядочивает обе строки по <code>id</code>. Условие <code>row.category == category</code>
          Python проверяет уже после чтения. Поменяйте категорию на «батарейки» в обоих путях.
          Итог пуст в обоих случаях, но полное чтение всё ещё приносит две строки.
        </p>
        <p>
          Для наблюдения одного GET возьмём знакомый <code>capture_sql(engine)</code>. Он записывает команды
          только на время <code>with</code>. Внутри окна выполняем запрос, а снаружи читаем <code>.statement</code>
          у записи. Служебную команду можно узнать по её назначению. Для ответа со списком задач нужна команда
          {" "}<code>SELECT</code> из <code>tasks</code>. Имя колонки в начале SELECT ещё не говорит,
          что по ней отбирают строки. Для этого смотрим условие <code>WHERE</code>.
        </p>
        <CodeBlock code={NORMALIZE_SQL} caption="Читаем форму SQL без привязки к регистру и переносам" />
        <p>
          <code>lower()</code> выравнивает регистр, <code>split()</code> делит по пробельным символам,
          а <code>join()</code> соединяет части одним пробелом. Печатается
          {" "}<code>select id from equipment where category = ?</code>, затем <code>True</code>.
          В практике признак нужен для каждой операции в её части SQL. Плейсхолдеры оставляем как есть.
        </p>
        <QuizCard
          question="Два пути вернули батарейки с номерами 22 и 38. Какой след показывает, что категорию проверила SQLite?"
          description="На складе ещё есть фонарь 10 и штатив 45. Сравните две команды."
          orientation="vertical"
          options={[
            { title: "След А", code: "SELECT id, category FROM equipment ORDER BY id" },
            { title: "След Б", code: "SELECT id, category FROM equipment WHERE category = ? ORDER BY id" },
          ]}
          correctIndex={1}
          explanation="В следе Б SQLite применяет предикат категории и передаёт две строки. В следе А база отдаёт четыре строки, а подходящие остаются после отбора в приложении."
        />
      </Section>

      <Section number="03" title="Как перепроверить, какой сбой пойман">
        <Lead>Регрессионная проверка сохраняет ожидание на будущее. Один тест сверит порядок номеров в ответе, другой проверит работу SQLite по фактической команде.</Lead>
        <p>
          В примере со стойкой ожидание <code>[4, 12]</code> можно сравнить с результатом обычного
          {" "}<code>sorted</code> через <code>assert</code>. На время одной попытки возьмём наблюдение из
          обратной сортировки. Ожидание остаётся прежним, поэтому <code>[12, 4]</code> вызовет
          {" "}<code>AssertionError</code>. Вернём фактическое наблюдение и повторим проверку. Так можно
          проверить, что утверждение замечает нужное различие.
        </p>

        <h3>Готовим временную SQLite для своих тестов</h3>
        <p>
          Фикстура готовит ресурс перед проверкой и освобождает его после. На ведомости она может
          создать Engine для временной базы и передать фабрику сессий. <code>@pytest.fixture</code> сообщает
          pytest, что параметр с именем <code>catalogue_sessions</code> надо взять из этой функции.
          {" "}<code>tmp_path</code> даёт новый каталог каждому тесту.
        </p>
        <CodeBlock code={CATALOGUE_SESSIONS} caption="Временная фабрика сессий на примере ведомости" />
        <p>
          <code>URL.create</code> собирает адрес SQLite из пути к файлу. <code>sessionmaker(bind=engine)</code>
          связывает фабрику с Engine, поэтому все созданные ею сессии читают одну ведомость.
          {" "}<code>yield</code> передаёт фабрику тесту. После проверки <code>finally</code> освобождает Engine
          даже при ошибке.
        </p>
        <p>
          В каталоге <code>project/</code> со своим <code>app/</code> и <code>tests/</code> выберите свободный
          путь <code>tests/test_task_selection_contract.py</code>. Соберите там свою фикстуру
          {" "}<code>selection_lab</code> из знакомых частей работы с короткими сессиями и наблюдением SQL.
        </p>
        <ol>
          <li>
            Возьмите свежий путь из <code>tmp_path</code> и соберите для него <code>url</code>, как в примере
            с ведомостью. Создайте учебный Engine с <code>echo=False</code> и <code>hide_parameters=True</code>.
            Создайте схему через <code>Base.metadata.create_all(engine)</code> у <code>Base</code> из
            {" "}<code>app.DBase.models</code> и привяжите к Engine свою фабрику сессий.
          </li>
          <li>
            До открытия клиента укажите приложению тот же временный адрес SQLite. В сборке, где запуск
            читает <code>app.state.settings.database_url</code>, передайте <code>monkeypatch.setattr</code>
            объект <code>app.state.settings</code>, имя поля <code>"database_url"</code> и значение
            {" "}<code>str(url)</code>. Добавьте <code>monkeypatch</code> как параметр фикстуры рядом с
            {" "}<code>tmp_path</code>. Pytest восстановит прежнее значение настройки после теста.
          </li>
          <li>
            Войдите в <code>with TestClient(app) as client</code>. Здесь FastAPI выполняет запуск
            приложения, который может создать свою фабрику. Сохраните текущую <code>app.state.session_factory</code>
            уже внутри блока и подставьте учебную фабрику. Передайте тесту клиент, учебную фабрику и её Engine.
            Их можно передать кортежем и распаковать в начале теста.
          </li>
          <li>
            В <code>finally</code> внутри блока клиента верните сохранённую фабрику. После выхода
            из <code>TestClient</code> освободите учебный Engine через <code>engine.dispose()</code>
            во внешнем <code>finally</code>. Так ресурсы освобождаются и при падении теста.
          </li>
        </ol>
        <p>
          Если <code>lifespan</code> создаёт фабрику при запуске, присваивание до входа в клиент будет
          перезаписано. Два Engine могут читать один SQLite-файл и возвращать те же id. Однако
          наблюдатель записывает команды только своего Engine. Перед первым GET откройте короткую Session
          из текущей <code>app.state.session_factory</code>: её <code>session.get_bind() is engine</code>
          должно дать <code>True</code> для учебного Engine. Закройте эту Session до захвата.
        </p>
        <p>
          Для своих строк импортируйте <code>TaskRow</code> из <code>app.DBase.models</code>.
          У каждой строки задайте выбранные <code>id</code> и <code>is_done</code>, синтетический
          {" "}<code>title</code>, <code>priority=2</code>, <code>tags=[]</code> и <code>title_normalized=title.lower()</code>.
          Добавьте строки через <code>session.add</code> в <code>with session_factory.begin() as session</code>.
          При нормальном выходе запись подтверждена, и новая Session запроса увидит те же строки.
          Сохраните пары номера и статуса в одном месте, чтобы оба теста наполняли свои пустые базы одинаково.
        </p>
        <p>
          У <code>client.get</code> первый аргумент задаёт путь, а <code>params</code> передаёт параметры из словаря.
          {" "}<code>response.status_code</code> показывает, что запрос завершился успешно, а <code>response.json()</code>
          для списка задач даёт список словарей. Выражение вида <code>[item["id"] for item in payload]</code>
          берёт номера в порядке выдачи.
        </p>

        <h3>Проверяем границы окна</h3>
        <p>
          Импортируйте <code>capture_sql</code> из <code>app.DBase.sql_trace</code>. Если файл не сохранили,
          восстановите помощник по открытым фрагментам темы «Наблюдаем SQL одной выборки». В её разделе
          «Окно наблюдения за одним действием» есть проба «Доступная проба сокрытия значения и снятия подписки».
        </p>
        <p>
          Повторите её на Engine своей временной фабрики отдельно от окна GET. В одном окне выполните
          параметризованный <code>SELECT ?</code>, после закрытия повторите его на том же Engine.
          При запуске с <code>-s</code> видны <code>SELECT ?</code>, <code>False</code> для появления
          маркера и <code>1 1</code> для размера следа до и после повторного чтения. Уберите вывод пробы
          из регрессии после сверки.
        </p>
        <p>
          Команда <code>python -m pytest -q tests/test_task_selection_contract.py</code> запустит тесты
          из выбранного файла. Если среда ещё не активирована, в PowerShell используйте
          {" "}<code>./.venv/Scripts/python.exe</code> вместо <code>python</code>. Чтобы повторить один, допишите к пути <code>::имя_теста</code>.
          При отказе найдите имя теста и сравните ожидаемое с фактическим. Если копия Planner
          сразу проходит проверку, оставьте регрессию и сохраните рабочий запрос.
        </p>
      </Section>

      <Section number="04" title="Что мы будем делать в практике">
        <LinkedNotes items={[
          { title: "Подберём свой набор", description: "Сравним два правила и получим различающиеся списки id." },
          { title: "Проверим фактический SELECT", description: "Найдём в следе отбор, порядок и предел." },
          { title: "Перепроверим Planner", description: "Запустим оба теста и сохраним команду регрессии." },
        ]} />
      </Section>
    </RichLesson>
  );
}
