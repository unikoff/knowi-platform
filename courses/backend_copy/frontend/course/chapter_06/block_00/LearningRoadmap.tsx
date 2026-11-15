import { Database, Route } from "lucide-react";
import { BranchExplorer, Callout, CodeBlock, CodeSequence, FlipCards, KeyTakeaways, Lead, MatchPairs, ContentList, LinkedNotes, PracticeCta, QuizCard, RecallCard, RichHero, RichLesson, Section, TypeCard, TypeCards } from "../../shared";

export function LearningRoadmap() {
  return (
    <RichLesson>
      <RichHero
        chip={"ЭТАП 6 · карта обучения"}
        title={"План обучения: этап 6"}
        intro={"За 24 занятия Personal StudyHub API превратится в PostgreSQL StudyHub: ORM станет прозрачнее через SQL, проект переедет на server database, получит JOIN, aggregates, transactions, indexes, recovery и осознанную карту хранилищ."}
        tags={[
          {
            icon: <Route size={14} />,
            label: "занятия 117–140",
          },
          {
            icon: <Database size={14} />,
            label: "PostgreSQL StudyHub",
          },
        ]}
      />

      <Section number={"01"} title={"От Personal StudyHub к PostgreSQL StudyHub"}>
        <Lead>
          {"Этап 5 завершился персональным API с пользователями, ownership, сессиями, токенами и тестами. Теперь меняется не HTTP-контракт, а глубина работы с данными: ученик увидит SQL под ORM и перенесёт проект с локального SQLite-файла на сервер PostgreSQL."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"вход этапа"}</>, description: <>{"Personal StudyHub API на SQLite и SQLAlchemy"}</> },
            { title: <>{"основной проект"}</>, description: <>{"PostgreSQL StudyHub"}</> },
            { title: <>{"занятия"}</>, description: <>{"117–140"}</> },
            { title: <>{"блоки"}</>, description: <>{"21–24"}</> },
            { title: <>{"выход этапа"}</>, description: <>{"перенос, сложные запросы, измерения и выбор хранилища"}</> },
          ]}
        />

        <CodeBlock
          caption={"переход проекта"}
          code={"Personal StudyHub API\n→ SQL виден под ORM\n→ PostgreSQL server\n→ JOIN и statistics\n→ transactions\n→ indexes и EXPLAIN\n→ PostgreSQL StudyHub"}
        />

        <CodeSequence
          title={"Соберите безопасный маршрут этапа"}
          prompt={"Расположите шаги так, чтобы новая технология появлялась после понятной проблемы."}
          pieces={[
            {
              id: "baseline",
              code: "зафиксировать рабочий SQLite API",
            },
            {
              id: "sql",
              code: "прочитать и написать базовый SQL",
            },
            {
              id: "server",
              code: "подключить PostgreSQL server",
            },
            {
              id: "migrate",
              code: "применить migrations и regression tests",
            },
            {
              id: "queries",
              code: "добавить JOIN, aggregates и transaction",
            },
            {
              id: "measure",
              code: "измерить запрос и только затем добавить index",
            },
          ]}
          correctOrder={[
            "baseline",
            "sql",
            "server",
            "migrate",
            "queries",
            "measure",
          ]}
          explanation={"Каждый следующий слой отвечает на наблюдаемую проблему и проверяется до дальнейшего усложнения."}
        />

        <TypeCards>
          <TypeCard
            badge={"до"}
            title={"SQLite"}
            code={"sqlite:///studyhub.db"}
          >
            {"Простая локальная база уже доказала контракт database layer."}
          </TypeCard>
          <TypeCard
            badge={"переход"}
            badgeTone={"float"}
            title={"SQL и server"}
            code={"SELECT ... / PostgreSQL"}
          >
            {"Ученик видит язык запросов и отдельный процесс базы."}
          </TypeCard>
          <TypeCard
            badge={"после"}
            badgeTone={"str"}
            title={"PostgreSQL StudyHub"}
            code={"postgresql+...://"}
          >
            {"Проект сохраняет API, но получает server database и более сильные запросы."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Зафиксировать baseline"}</h3>
          <p>
            {"Запустить tests и основные requests до изменения database URL."}
          </p>

          <h3>{"Менять один слой"}</h3>
          <p>
            {"Не смешивать перенос PostgreSQL, новую auth-логику и новый endpoint."}
          </p>

          <h3>{"Доказывать результат"}</h3>
          <p>
            {"После каждого блока сохранять SQL, test, plan или runbook."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Этап не повторяет регистрацию и JWT. Эти механизмы используются как готовая бизнес-часть, поверх которой меняется работа с данными."}
        </Callout>

        <Callout tone={"warn"}>
          {"PostgreSQL не делает приложение автоматически быстрым. Сначала требуется корректный SQL, затем измерение и только потом оптимизация."}
        </Callout>

      </Section>

      <Section number={"02"} title={"Блок 21 · SQL как язык данных"}>
        <Lead>
          {"Первый блок снимает магию ORM. Ученик связывает ORM-класс с таблицей, создаёт учебную schema, выполняет параметризованный CRUD и переводит один запрос между raw SQL и SQLAlchemy 2.x."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"117"}</>, description: <>{"TaskModel → table → SQL log"}</> },
            { title: <>{"118"}</>, description: <>{"CREATE TABLE + constraints"}</> },
            { title: <>{"119"}</>, description: <>{"INSERT + parameters + RETURNING"}</> },
            { title: <>{"120"}</>, description: <>{"SELECT pipeline"}</> },
            { title: <>{"121"}</>, description: <>{"safe UPDATE/DELETE"}</> },
            { title: <>{"122"}</>, description: <>{"SQL and SQLAlchemy parity"}</> },
          ]}
        />

        <CodeBlock
          caption={"карта SQL-блока"}
          code={"ORM object\n→ SQL statement\n→ parameter values\n→ database constraint\n→ rows affected\n→ Python result"}
        />

        <MatchPairs
          prompt={"Соедините занятие и главный артефакт."}
          leftTitle={"Занятие"}
          rightTitle={"Артефакт"}
          pairs={[
            {
              left: "117",
              right: "подписанная схема ORM → table",
            },
            {
              left: "118",
              right: "DDL с ограничениями",
            },
            {
              left: "119",
              right: "parameterized INSERT",
            },
            {
              left: "120",
              right: "filter/sort/page SELECT",
            },
            {
              left: "121",
              right: "safe mutation checklist",
            },
            {
              left: "122",
              right: "таблица SQL ↔ SQLAlchemy",
            },
          ]}
          explanation={"Каждый урок оставляет проверяемый результат, а не только набор терминов."}
        />

        <TypeCards>
          <TypeCard
            badge={"DDL"}
            title={"Структура"}
            code={"CREATE TABLE"}
          >
            {"Определяет форму таблицы и database guarantees."}
          </TypeCard>
          <TypeCard
            badge={"DML"}
            badgeTone={"float"}
            title={"Изменения"}
            code={"INSERT / UPDATE / DELETE"}
          >
            {"Работает со строками и всегда проверяет затронутый набор."}
          </TypeCard>
          <TypeCard
            badge={"query"}
            badgeTone={"str"}
            title={"Чтение"}
            code={"SELECT ... WHERE"}
          >
            {"Формирует требуемый набор строк, порядок и границы страницы."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Сначала прочитать"}</h3>
          <p>
            {"До запуска проговорить, какие rows должен затронуть statement."}
          </p>

          <h3>{"Затем выполнить"}</h3>
          <p>
            {"Использовать небольшую отдельную учебную таблицу или transaction."}
          </p>

          <h3>{"После сравнить"}</h3>
          <p>
            {"Найти тот же смысл в SQLAlchemy statement и API response."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Raw SQL используется как учебный инструмент и точный язык базы. Основной CRUD проекта не переписывается строками SQL без причины."}
        </Callout>

        <Callout tone={"warn"}>
          {"Склеивание пользовательских значений в SQL через f-string не является упрощением. Значения передаются parameters отдельно от statement."}
        </Callout>

      </Section>

      <Section number={"03"} title={"Блок 22 · PostgreSQL и перенос StudyHub"}>
        <Lead>
          {"После понимания SQL ученик видит, что PostgreSQL — отдельный server process. SQLite-файл заменяется связкой client → driver → server → database → schema → table, но внешний API обязан остаться прежним."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"server"}</>, description: <>{"долгоживущий процесс PostgreSQL"}</> },
            { title: <>{"database"}</>, description: <>{"логическое пространство данных проекта"}</> },
            { title: <>{"schema"}</>, description: <>{"namespace таблиц, например public"}</> },
            { title: <>{"role"}</>, description: <>{"identity и permissions подключения"}</> },
            { title: <>{"driver"}</>, description: <>{"Python-клиент протокола PostgreSQL"}</> },
            { title: <>{"engine"}</>, description: <>{"SQLAlchemy entry point и pool connections"}</> },
          ]}
        />

        <CodeBlock
          caption={"путь подключения"}
          code={"FastAPI\n→ SQLAlchemy Engine\n→ PostgreSQL driver\n→ host:port\n→ database\n→ schema.public\n→ tables"}
        />

        <BranchExplorer
          code={"DATABASE_URL\n  driver://role:password@host:port/database\n\nalembic upgrade head\npytest"}
          scenarios={[
            {
              label: "server не запущен",
              activeLine: 1,
              output: "connection refused: проверить process и port",
            },
            {
              label: "роль неверна",
              activeLine: 1,
              output: "authentication failed: проверить role/password",
            },
            {
              label: "schema пустая",
              activeLine: 3,
              output: "применить migrations до запуска API",
            },
            {
              label: "tests зелёные",
              activeLine: 4,
              output: "HTTP contract после переноса сохранён",
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"client"}
            title={"psql / приложение"}
            code={"psql -h ..."}
          >
            {"Инициирует connection и отправляет SQL."}
          </TypeCard>
          <TypeCard
            badge={"server"}
            badgeTone={"float"}
            title={"PostgreSQL process"}
            code={"host:5432"}
          >
            {"Принимает connections и управляет несколькими databases."}
          </TypeCard>
          <TypeCard
            badge={"project"}
            badgeTone={"str"}
            title={"studyhub_dev"}
            code={"public.tasks"}
          >
            {"Хранит schema и данные конкретного окружения."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Поднять пустую database"}</h3>
          <p>
            {"Не начинать перенос поверх случайной старой schema."}
          </p>

          <h3>{"Применить migrations"}</h3>
          <p>
            {"Структура восстанавливается из repository, а не ручным create_all."}
          </p>

          <h3>{"Проверить contract"}</h3>
          <p>
            {"Запустить API tests и Postman collection после смены DATABASE_URL."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Отдельная app-role уменьшает последствия ошибки. StudyHub не должен подключаться PostgreSQL-superuser."}
        </Callout>

        <Callout tone={"warn"}>
          {"Миграции восстанавливают schema, но не заменяют backup пользовательских данных."}
        </Callout>

      </Section>

      <Section number={"04"} title={"Блок 23 · JOIN, aggregates и transactions"}>
        <Lead>
          {"Перенос завершён, но простой CRUD не отвечает на вопросы по связанным данным. В этом блоке SQL объединяет users, tasks, categories и tags, строит statistics и выполняет несколько изменений как одну атомарную операцию."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"INNER JOIN"}</>, description: <>{"оставляет совпавшие пары"}</> },
            { title: <>{"LEFT JOIN"}</>, description: <>{"сохраняет все строки слева"}</> },
            { title: <>{"GROUP BY"}</>, description: <>{"собирает строки в группы"}</> },
            { title: <>{"HAVING"}</>, description: <>{"фильтрует уже сформированные группы"}</> },
            { title: <>{"EXISTS"}</>, description: <>{"проверяет наличие строки"}</> },
            { title: <>{"transaction"}</>, description: <>{"задаёт атомарную границу операции"}</> },
          ]}
        />

        <CodeBlock
          caption={"единый запрос данных"}
          code={"users\n  JOIN tasks ON tasks.owner_id = users.id\n  LEFT JOIN categories ON categories.id = tasks.category_id\n  GROUP BY users.id\n  HAVING COUNT(tasks.id) >= 3"}
        />

        <QuizCard
          question={"Какой вариант сохраняет пользователя без задач?"}
          options={[{ title: "INNER JOIN", code: "users JOIN tasks ON ...", description: "Пользователь без совпавшей task исчезнет." }, { title: "LEFT JOIN", code: "users LEFT JOIN tasks ON ...", description: "Пользователь останется, columns task будут NULL." }]}
          
          correctIndex={1}
          explanation={"Тип JOIN выбирается по требуемому набору строк, а не по привычке."}
        />

        <TypeCards>
          <TypeCard
            badge={"связь"}
            title={"JOIN"}
            code={"ON foreign_key = primary_key"}
          >
            {"Соединяет rows разных tables по явному правилу."}
          </TypeCard>
          <TypeCard
            badge={"отчёт"}
            badgeTone={"float"}
            title={"Aggregate"}
            code={"COUNT(*) GROUP BY"}
          >
            {"Сворачивает набор строк в показатели."}
          </TypeCard>
          <TypeCard
            badge={"гарантия"}
            badgeTone={"str"}
            title={"Transaction"}
            code={"BEGIN → COMMIT/ROLLBACK"}
          >
            {"Не допускает частично завершённую бизнес-операцию."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Нарисовать rows"}</h3>
          <p>
            {"До SQL показать, какие строки должны попасть в результат."}
          </p>

          <h3>{"Собрать statement"}</h3>
          <p>
            {"Добавлять JOIN, GROUP BY и filter по одному слою."}
          </p>

          <h3>{"Сломать второй шаг"}</h3>
          <p>
            {"Проверить, что transaction откатывает оба изменения."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Async не требуется для JOIN и transaction. В этапе 6 используется знакомый синхронный SQLAlchemy и обычная Session."}
        </Callout>

        <Callout tone={"warn"}>
          {"GROUP BY не является способом убрать случайные дубликаты. Он используется вместе с понятным aggregate-result."}
        </Callout>

      </Section>

      <Section number={"05"} title={"Блок 24 · Индексы, EXPLAIN и выбор хранилища"}>
        <Lead>
          {"Финальный блок начинается не с индекса, а с медленного воспроизводимого запроса. После baseline ученик создаёт обоснованный index, читает простой execution plan, проверяет backup/restore и сравнивает PostgreSQL с document и key-value моделями."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"baseline"}</>, description: <>{"измерение до изменения"}</> },
            { title: <>{"index"}</>, description: <>{"дополнительная структура чтения"}</> },
            { title: <>{"EXPLAIN"}</>, description: <>{"объяснение выбранного plan"}</> },
            { title: <>{"backup"}</>, description: <>{"копия schema и data"}</> },
            { title: <>{"MongoDB"}</>, description: <>{"document-oriented storage"}</> },
            { title: <>{"Redis"}</>, description: <>{"in-memory key/value with TTL"}</> },
          ]}
        />

        <CodeBlock
          caption={"матрица хранения"}
          code={"PostgreSQL → relations, constraints, transactions, source of truth\nMongoDB → whole document, flexible nested shape\nRedis → temporary key/value, TTL, cache or service state"}
        />

        <MatchPairs
          prompt={"Соедините задачу и наиболее естественную модель хранения."}
          leftTitle={"Сценарий"}
          rightTitle={"Хранилище"}
          pairs={[
            {
              left: "users, tasks и ownership",
              right: "PostgreSQL",
            },
            {
              left: "самодостаточный импортируемый snapshot",
              right: "MongoDB document experiment",
            },
            {
              left: "verification code на 10 минут",
              right: "Redis with TTL",
            },
            {
              left: "атомарное изменение нескольких rows",
              right: "PostgreSQL transaction",
            },
            {
              left: "повторно читаемая временная statistics",
              right: "Redis cache after measurement",
            },
          ]}
          explanation={"Выбор следует из гарантий, lifetime и формы данных, а не из популярности технологии."}
        />

        <TypeCards>
          <TypeCard
            badge={"truth"}
            title={"PostgreSQL"}
            code={"constraints + transaction"}
          >
            {"Основное постоянное состояние StudyHub."}
          </TypeCard>
          <TypeCard
            badge={"document"}
            badgeTone={"float"}
            title={"MongoDB"}
            code={"{ course: { modules: [...] } }"}
          >
            {"Учебный эксперимент с целым вложенным документом."}
          </TypeCard>
          <TypeCard
            badge={"temporary"}
            badgeTone={"str"}
            title={"Redis"}
            code={"SETEX key ttl value"}
          >
            {"Быстрое временное значение, которое можно восстановить из source of truth."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Измерить"}</h3>
          <p>
            {"Создать baseline на достаточном объёме данных."}
          </p>

          <h3>{"Объяснить"}</h3>
          <p>
            {"Прочитать plan и назвать, почему index выбран или не выбран."}
          </p>

          <h3>{"Проверить восстановление"}</h3>
          <p>
            {"Backup считается полезным только после успешного restore test."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"MongoDB и Redis не становятся обязательными постоянными базами основного проекта. Они изучаются через ограниченные эксперименты и сравнение требований."}
        </Callout>

        <Callout tone={"warn"}>
          {"Index ускоряет некоторые reads ценой места и дополнительной работы при writes. Индекс на каждую колонку — не стратегия."}
        </Callout>

      </Section>

      <Section number={"06"} title={"Как работать с каждым занятием"}>
        <Lead>
          {"Низкий порог входа сохраняется через один повторяемый ритм: сначала назвать проблему, затем увидеть таблицу или rows, предсказать результат statement, выполнить маленький experiment и только после этого встроить изменение в StudyHub."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"таблица до кода"}</>, description: <>{"нарисовать исходные rows"}</> },
            { title: <>{"prediction"}</>, description: <>{"записать ожидаемый result"}</> },
            { title: <>{"terminal"}</>, description: <>{"выполнить statement вручную"}</> },
            { title: <>{"project"}</>, description: <>{"повторить через StudyHub layer"}</> },
            { title: <>{"negative path"}</>, description: <>{"нарушить constraint или connection"}</> },
            { title: <>{"evidence"}</>, description: <>{"сохранить test, plan, benchmark или runbook"}</> },
          ]}
        />

        <CodeBlock
          caption={"ритм одного занятия"}
          code={"problem\n→ data picture\n→ SQL prediction\n→ manual run\n→ one change\n→ error path\n→ StudyHub integration\n→ evidence"}
        />

        <FlipCards
          cards={[
            {
              front: <>{"До запуска"}</>,
              back: <>{"Назвать expected rows и возможную ошибку."}</>,
            },
            {
              front: <>{"После запуска"}</>,
              back: <>{"Сравнить реальный result с prediction."}</>,
            },
            {
              front: <>{"После интеграции"}</>,
              back: <>{"Запустить tests и проверить API contract."}</>,
            },
            {
              front: <>{"Перед коммитом"}</>,
              back: <>{"Объяснить изменение без чтения готового ответа."}</>,
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"модель"}
            title={"Одна новая идея"}
            code={"JOIN type / constraint / index"}
          >
            {"Сцена не смешивает несколько неизвестных механизмов."}
          </TypeCard>
          <TypeCard
            badge={"ошибка"}
            badgeTone={"float"}
            title={"Контролируемый сбой"}
            code={"constraint / connection / rollback"}
          >
            {"Ошибка изучается как часть контракта, а не как случайная поломка."}
          </TypeCard>
          <TypeCard
            badge={"артефакт"}
            badgeTone={"str"}
            title={"Проверяемый результат"}
            code={"test / plan / runbook"}
          >
            {"Каждое занятие оставляет доказательство понимания."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Не читать пассивно"}</h3>
          <p>
            {"Остановиться перед output и сформулировать prediction."}
          </p>

          <h3>{"Не копировать вслепую"}</h3>
          <p>
            {"Изменить один параметр и объяснить новый result."}
          </p>

          <h3>{"Не скрывать ошибки"}</h3>
          <p>
            {"Сохранить сообщение PostgreSQL и назвать нарушенное ожидание."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Главная визуальная единица этапа — конкретные строки таблицы и их преобразование. Абстрактные определения всегда связываются с данными StudyHub."}
        </Callout>

        <Callout tone={"warn"}>
          {"Большой SQL statement нельзя вводить одним экраном. Он собирается из FROM, JOIN, WHERE, GROUP BY и HAVING по шагам."}
        </Callout>

      </Section>

      <Section number={"07"} title={"Артефакты и критерии готовности блоков"}>
        <Lead>
          {"Переход между блоками определяется не номером занятия, а готовым артефактом. Это защищает курс от движения по верхам и позволяет наставнику проверять конкретное инженерное действие."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"sql-lab"}</>, description: <>{"ручные parameterized statements"}</> },
            { title: <>{"migration proof"}</>, description: <>{"alembic upgrade head on clean database"}</> },
            { title: <>{"regression suite"}</>, description: <>{"unchanged HTTP contract"}</> },
            { title: <>{"statistics"}</>, description: <>{"JOIN + GROUP BY endpoint"}</> },
            { title: <>{"transaction test"}</>, description: <>{"both changes or none"}</> },
            { title: <>{"performance note"}</>, description: <>{"baseline, plan and measured result"}</> },
          ]}
        />

        <CodeBlock
          caption={"контрольная матрица"}
          code={"Block 21 → SQL readable and reproducible\nBlock 22 → PostgreSQL migration reproducible\nBlock 23 → related data and transaction correct\nBlock 24 → measured optimization and storage decision"}
        />

        <QuizCard
          question={
            <>
              {"Этап завершён, если API работает на PostgreSQL, даже когда migrations, tests и restore не проверены."}
            </>
          }
          options={["Правда", "Ложь"]} correctIndex={1}
          explanation={"Рабочий случай на одной машине не доказывает воспроизводимость и сохранность данных."}
        />

        <TypeCards>
          <TypeCard
            badge={"code"}
            title={"Репозиторий"}
            code={"app + migrations + sql-lab"}
          >
            {"Содержит код и историю schema."}
          </TypeCard>
          <TypeCard
            badge={"proof"}
            badgeTone={"float"}
            title={"Проверка"}
            code={"pytest + smoke + restore"}
          >
            {"Подтверждает обычный и аварийный сценарии."}
          </TypeCard>
          <TypeCard
            badge={"explain"}
            badgeTone={"str"}
            title={"Защита"}
            code={"request → SQL → PostgreSQL"}
          >
            {"Ученик объясняет систему без заученной формулировки."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Проверить чистый старт"}</h3>
          <p>
            {"Создать пустую database и применить migrations."}
          </p>

          <h3>{"Проверить данные"}</h3>
          <p>
            {"Запустить seed или import и сверить ключевые counts."}
          </p>

          <h3>{"Проверить отказ"}</h3>
          <p>
            {"Нарушить constraint, сломать второй transaction-step или остановить server."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Четвёртая неделя этапа должна содержать интеграцию, диагностику и защиту, а не только новые команды."}
        </Callout>

        <Callout tone={"warn"}>
          {"Скриншот successful query не заменяет reproducible script, migration или automated test."}
        </Callout>

      </Section>

      <Section number={"08"} title={"Контрольная точка этапа 6"}>
        <Lead>
          {"Финальная проверка связывает SQL, PostgreSQL, relationships, transactions, plans и storage choices. Ученик должен не только показать endpoints, но и объяснить, какие guarantees даёт database и где заканчивается роль каждой технологии."}
        </Lead>


        <LinkedNotes
          items={[
            { title: <>{"117–122"}</>, description: <>{"SQL grammar and ORM bridge"}</> },
            { title: <>{"123–128"}</>, description: <>{"PostgreSQL migration"}</> },
            { title: <>{"129–134"}</>, description: <>{"JOIN, aggregates and transaction"}</> },
            { title: <>{"135–140"}</>, description: <>{"measurement, recovery and storage models"}</> },
            { title: <>{"project"}</>, description: <>{"PostgreSQL StudyHub"}</> },
            { title: <>{"next"}</>, description: <>{"asyncio only after stable synchronous database layer"}</> },
          ]}
        />

        <CodeBlock
          caption={"финальная демонстрация"}
          code={"1. clean database\n2. alembic upgrade head\n3. seed demo data\n4. run API tests\n5. show JOIN statistics\n6. trigger rollback\n7. show EXPLAIN\n8. restore backup\n9. defend storage matrix"}
        />

        <RecallCard
          question={"Почему следующий этап начинается с asyncio вне FastAPI, а не с немедленной замены всех def на async def?"}
          hint={"Сначала должен быть устойчивый синхронный baseline и точная модель I/O."}
          answer={
            <p>
              {"Async меняет способ ожидания I/O, но не исправляет неверный SQL, transaction или schema. Сначала завершается и измеряется синхронный PostgreSQL StudyHub."}
            </p>
          }
        />

        <TypeCards>
          <TypeCard
            badge={"data"}
            title={"Целостность"}
            code={"constraints + transactions"}
          >
            {"Database защищает правила и атомарные операции."}
          </TypeCard>
          <TypeCard
            badge={"speed"}
            badgeTone={"float"}
            title={"Измерение"}
            code={"baseline + EXPLAIN"}
          >
            {"Оптимизация опирается на наблюдаемый query pattern."}
          </TypeCard>
          <TypeCard
            badge={"choice"}
            badgeTone={"str"}
            title={"Архитектура"}
            code={"truth / document / temporary"}
          >
            {"Каждое хранилище получает ограниченную понятную роль."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Демонстрация"}</h3>
          <p>
            {"Пройти девять шагов финального сценария на чистой database."}
          </p>

          <h3>{"Диагностика"}</h3>
          <p>
            {"Объяснить connection, constraint и transaction errors."}
          </p>

          <h3>{"Защита"}</h3>
          <p>
            {"Ответить, почему выбран PostgreSQL и какие темы отложены."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"После этапа ученик готов изучать event loop и AsyncSession, потому что уже понимает, какой database I/O будет ожидать."}
        </Callout>

        <Callout tone={"warn"}>
          {"Redis и MongoDB не должны появиться в основном проекте без отдельного требования, lifecycle и source-of-truth решения."}
        </Callout>

        <div className="lesson-check-group">
          <QuizCard
            question={"Что остаётся неизменным при переносе SQLite → PostgreSQL?"}
            options={[
              "HTTP contract",
              "connection URL",
              "server process",
            ]}
            correctIndex={0}
            explanation={"Клиент не должен зависеть от замены database implementation."}
          />

          <QuizCard
            question={"Когда добавлять index?"}
            options={[
              "После baseline и query pattern",
              "На каждую column сразу",
              "До появления данных",
            ]}
            correctIndex={0}
            explanation={"Index проектируется под измеренный способ чтения."}
          />

          <QuizCard
            question={"Что гарантирует transaction?"}
            options={[
              "Все изменения или ни одного",
              "Автоматический retry",
              "Отсутствие всех ошибок",
            ]}
            correctIndex={0}
            explanation={"Atomic boundary не допускает частично записанную операцию."}
          />

          <QuizCard
            question={"Основная роль Redis в этапе 6?"}
            options={[
              "Временное key/value с TTL",
              "Главная реляционная база",
              "Замена Alembic",
            ]}
            correctIndex={0}
            explanation={"PostgreSQL остаётся source of truth."}
          />

        </div>

        <KeyTakeaways
          points={[
            <>{"ORM отображает Python-модель на таблицу, но не отменяет SQL."}</>,
            <>{"PostgreSQL состоит из server, databases, schemas, roles и connections."}</>,
            <>{"Миграции должны восстановить schema на чистой database."}</>,
            <>{"JOIN выбирается по требуемому набору rows."}</>,
            <>{"GROUP BY и HAVING решают разные этапы aggregation pipeline."}</>,
            <>{"Transaction защищает атомарность бизнес-операции."}</>,
            <>{"Index появляется после baseline и чтения plan."}</>,
            <>{"PostgreSQL остаётся source of truth StudyHub."}</>,
          ]}
        />

        <PracticeCta
          text={"Подготовьте контрольный репозиторий PostgreSQL StudyHub: clean setup, migrations, seed, regression tests, JOIN statistics, transaction failure test, EXPLAIN note, backup/restore runbook и storage decision matrix."}
        />

      </Section>

    </RichLesson>
  );
}
