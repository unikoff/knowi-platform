import { FileText, Wrench } from "lucide-react";
import { Callout, CodeBlock, CodeSequence, FillBlank, KeyTakeaways, Lead, MatchPairs, ContentList, PracticeCta, PredictOutput, QuizCard, RecallCard, RichHero, RichLesson, Section, TerminalDemo, TypeCard, TypeCards } from "../../shared";
import { TheoryBridge } from "../../shared";
const BLOCK_TITLE = "Блок 22 · PostgreSQL и перенос StudyHub";

export function Lesson124({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title={"Установка, psql и первая база"}
        intro={"Запустим PostgreSQL как service, подключимся через psql, создадим первую database и научимся проверять соединение и структуру без FastAPI и ORM."}
        tags={[
          { icon: <Wrench size={14} />, label: "service и pg_isready" },
          { icon: <FileText size={14} />, label: "psql · meta-commands" },
        ]}
      />
      <TheoryBridge link={"Прошлый урок дал карту server, database, schema и role. Теперь каждый элемент появится в реальном терминальном сценарии."} boundary={"Установка PostgreSQL и установка Python-package — разные действия. psycopg не запускает server, а psql не является самой database."} />

      <Section number={"01"} title={"Сначала сервер, потом приложение"}>
        <Lead>
          {"До подключения StudyHub нужно доказать, что PostgreSQL установлен, service запущен и port принимает connections. Это отдельная инфраструктурная проверка без FastAPI."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «Сначала сервер, потом приложение»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>


        <TerminalDemo
          title={"минимальная проверка server"}
          lines={[
            { cmd: "psql --version" },
            { out: "psql (PostgreSQL)" },
            { cmd: "pg_isready -h localhost -p 5432" },
            { out: "localhost:5432 - accepting connections" },
          ]}
        />

        <Callout tone="success">
          {"Команда запуска service зависит от операционной системы. Универсальная часть урока — проверить, что server принимает connections по ожидаемому адресу."}
        </Callout>

      </Section>

      <Section number={"02"} title={"pg_isready проверяет доступность server"}>
        <Lead>
          {"pg_isready выполняет узкую проверку: отвечает ли PostgreSQL на host и port. Он не подтверждает существование таблиц и не заменяет login конкретной role."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «pg_isready проверяет доступность server»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>

        <TypeCards>
          <TypeCard badge={"accepting"} title={"Server отвечает"} code={"localhost:5432 - accepting connections"}>
            {"Можно переходить к проверке credentials и database."}
          </TypeCard>
          <TypeCard badge={"no response"} badgeTone="float" title={"Server недоступен"} code={"localhost:5432 - no response"}>
            {"Проверяются service, host, port и firewall."}
          </TypeCard>
          <TypeCard badge={"rejecting"} badgeTone="str" title={"Server запускается"} code={"rejecting connections"}>
            {"Процесс найден, но ещё не готов принимать обычные clients."}
          </TypeCard>
        </TypeCards>

        <QuizCard
          question={<>{"Успешный pg_isready доказывает, что таблица tasks существует."}</>}
          options={["Правда", "Ложь"]} correctIndex={1}
          explanation={"Команда проверяет доступность server, но не schema state конкретной database."}
        />

        <Callout>
          {"Используйте маленькие проверки: сначала доступность процесса, затем authentication, затем database, затем tables."}
        </Callout>

      </Section>

      <Section number={"03"} title={"Первое подключение через psql"}>
        <Lead>
          {"psql — интерактивный client PostgreSQL. Параметры команды делают connection contract видимым и не зависят от догадок о текущем пользователе ОС."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «Первое подключение через psql»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>

        <CodeBlock
          caption={"явное подключение"}
          code={"psql -h localhost -p 5432 -U postgres -d postgres"}
        />

        <ContentList
          items={[
            { title: <>{"-h localhost"}</>, description: "host server" },
            { title: <>{"-p 5432"}</>, description: "port процесса" },
            { title: <>{"-U postgres"}</>, description: "role подключения" },
            { title: <>{"-d postgres"}</>, description: "database для текущей session" },
            { title: <>{"\\q"}</>, description: "завершить psql session" },
          ]}
        />

        <FillBlank
          prompt={"Добавьте параметр выбора database."}
          before={"psql -h localhost -U postgres "}
          after={" studyhub_dev"}
          options={["-d", "-p", "-U"]}
          answer={"-d"}
          explanation={"Флаг -d выбирает database, к которой подключается psql."}
        />

        <Callout>
          {"Пароль вводится интерактивно или передаётся безопасным способом окружения. Не добавляйте реальный secret в историю shell и README."}
        </Callout>

      </Section>

      <Section number={"04"} title={"Meta-commands показывают контекст psql"}>
        <Lead>
          {"Команды, начинающиеся с обратного slash, обрабатывает psql client. Это не SQL statements и обычно завершаются без точки с запятой."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «Meta-commands показывают контекст psql»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>

        <ContentList
          items={[
            { title: <>{"\\conninfo"}</>, description: "показать текущее connection" },
            { title: <>{"\\l"}</>, description: "список databases" },
            { title: <>{"\\c studyhub_dev"}</>, description: "переключиться на database новым connection" },
            { title: <>{"\\dn"}</>, description: "список schemas" },
            { title: <>{"\\dt"}</>, description: "список tables в search path" },
            { title: <>{"\\d tasks"}</>, description: "описать columns и constraints table" },
          ]}
        />

        <MatchPairs
          prompt={"Соедините meta-command и наблюдаемый результат."}
          pairs={[
            { left: "\\l", right: "databases" },
            { left: "\\c", right: "новое connection к database" },
            { left: "\\dt", right: "tables" },
            { left: "\\d tasks", right: "структура table tasks" },
          ]}
          explanation={"Каждая пара связывает термин с его конкретной ролью в текущей модели."}
        />

        <QuizCard orientation="vertical"
          description={<CodeBlock code={"SELECT \\dt;"} />}
          question={"Почему эта строка ошибочна?"}
          options={["\\dt является psql meta-command, а не SQL", "SELECT нельзя писать в PostgreSQL", "Таблицы доступны только FastAPI"]}
          correctIndex={0}
          explanation={"Meta-command вводится отдельно без SELECT и semicolon."}
          answer={<CodeBlock caption="Исправленный вариант" code={"\\dt"} />}
        />

      </Section>

      <Section number={"05"} title={"Создаём studyhub_dev осознанно"}>
        <Lead>
          {"Database создаётся административной role, после чего к ней устанавливается отдельное connection. CREATE DATABASE нельзя выполнить внутри transaction block."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «Создаём studyhub_dev осознанно»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>

        <CodeBlock
          caption={"создание первой database"}
          code={"CREATE DATABASE studyhub_dev;"}
        />

        <TerminalDemo
          title={"создание и переключение"}
          lines={[
            { cmd: "psql -h localhost -U postgres -d postgres" },
            { out: "postgres=#" },
            { cmd: "CREATE DATABASE studyhub_dev;" },
            { out: "CREATE DATABASE" },
            { cmd: "\\c studyhub_dev" },
            { out: "You are now connected to database \"studyhub_dev\"." },
          ]}
        />

        <PredictOutput
          code={"SELECT current_database();"}
          output={"studyhub_dev"}
          hint={"После \\c все следующие SQL statements выполняются в новом connection."}
        />

        <Callout>
          {"На следующем занятии database будет создаваться сразу с owner studyhub_app. Здесь цель — освоить connection и database context."}
        </Callout>

      </Section>

      <Section number={"06"} title={"Тестовая table как инструмент наблюдения"}>
        <Lead>
          {"До Alembic полезно один раз вручную создать маленькую disposable table. Она показывает, что SQL выполняется в выбранной database и schema."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «Тестовая table как инструмент наблюдения»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>

        <CodeBlock
          caption={"временная лабораторная table"}
          code={"CREATE TABLE connection_check (\n    id integer PRIMARY KEY,\n    note text NOT NULL\n);\n\nINSERT INTO connection_check (id, note)\nVALUES (1, 'psql works');"}
        />

        <TerminalDemo
          title={"проверка структуры и строки"}
          lines={[
            { cmd: "\\dt" },
            { out: "public | connection_check | table" },
            { cmd: "\\d connection_check" },
            { out: "id integer not null primary key\nnote text not null" },
            { cmd: "SELECT * FROM connection_check;" },
            { out: "1 | psql works" },
          ]}
        />

        <RecallCard
          question={"Зачем создавать временную table до подключения StudyHub?"}
          hint={"Она проверяет цепочку server → database → schema → SQL."}
          answer={
            <p>{"Лабораторная table изолирует инфраструктурный сценарий: мы подтверждаем connection, выбранную database, schema public и выполнение SQL без влияния ORM и FastAPI."}</p>
          }
        />

        <Callout tone="success">
          {"После эксперимента удалите connection_check. Реальная схема StudyHub будет восстановлена только Alembic migrations."}
        </Callout>

      </Section>

      <Section number={"07"} title={"Connection refused: диагностируем сверху вниз"}>
        <Lead>
          {"Сообщение connection refused означает, что client не установил TCP connection. Проверка password или table на этом этапе преждевременна."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «Connection refused: диагностируем сверху вниз»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>

        <CodeSequence
          title={"Соберите порядок диагностики"}
          prompt={"Расположите проверки от инфраструктуры к данным."}
          pieces={[
            { id: "ready", code: "pg_isready -h localhost -p 5432" },
            { id: "address", code: "сверить host и port" },
            { id: "login", code: "psql с явными -U и -d" },
            { id: "context", code: "\\conninfo" },
            { id: "tables", code: "\\dt" },
            { id: "orm", code: "переписывать ORM-модели", note: "не относится к connection refused" },
          ]}
          correctOrder={["ready", "address", "login", "context", "tables"]}
          explanation={"Каждая следующая проверка имеет смысл только после успеха предыдущего слоя."}
        />

        <TerminalDemo
          title={"ошибка и локализация"}
          lines={[
            { cmd: "psql -h localhost -p 5433 -U postgres -d postgres" },
            { out: "connection to server at \"localhost\", port 5433 failed: Connection refused" },
            { cmd: "pg_isready -h localhost -p 5432" },
            { out: "localhost:5432 - accepting connections" },
          ]}
        />

        <Callout>
          {"Не меняйте сразу несколько параметров. Исправьте port, повторите ровно ту же команду и зафиксируйте, какой слой стал успешным."}
        </Callout>

      </Section>

      <Section number={"08"} title={"Контрольная точка: самостоятельный psql flow"}>
        <Lead>
          {"Ученик должен с нуля проверить server, подключиться, создать database, переключить context, создать и удалить лабораторную table, а затем объяснить каждую команду."}
        </Lead>

        <div className="lesson-check-group">
          <QuizCard
            question={"Что проверяет pg_isready?"}
            options={["доступность PostgreSQL server", "наличие всех migrations", "валидность Pydantic schema"]}
            correctIndex={0}
            explanation={"Это проверка server по host и port."}
          />
          <QuizCard
            question={"Как выбрать database в psql command?"}
            options={["-d studyhub_dev", "-p studyhub_dev", "-U studyhub_dev"]}
            correctIndex={0}
            explanation={"Флаг -d задаёт database."}
          />
          <QuizCard
            question={"Что делает \\dt?"}
            options={["показывает tables", "удаляет transaction", "меняет password"]}
            correctIndex={0}
            explanation={"Это psql meta-command списка tables."}
          />
          <QuizCard
            question={"Что проверять первым при connection refused?"}
            options={["service, host и port", "response_model", "foreign key tasks"]}
            correctIndex={0}
            explanation={"Connection ещё не дошёл до database objects."}
          />
        </div>

        <KeyTakeaways
          points={[
            <>{"PostgreSQL installation создаёт server tools и service."}</>,
            <>{"pg_isready проверяет network readiness server."}</>,
            <>{"psql является client и подключается с явными host, role и database."}</>,
            <>{"Meta-commands помогают увидеть connection и objects."}</>,
            <>{"Database context меняется через новое connection."}</>,
            <>{"Disposable table полезна как изолированный infrastructure test."}</>,
            <>{"Connection refused диагностируется до credentials и schema."}</>,
          ]}
        />

        <PracticeCta text={"Создайте studyhub_dev, подключитесь через psql, выполните \\conninfo, \\dn и \\dt, создайте временную connection_check, прочитайте её через SELECT и удалите. В README добавьте runbook подключения без реального password."} />

      </Section>

    </RichLesson>
  );
}
