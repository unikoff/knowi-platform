import { FileText, GitBranch } from "lucide-react";
import { BranchExplorer, Callout, CodeBlock, CodeSequence, KeyTakeaways, Lead, MatchPairs, ContentList, PracticeCta, QuizCard, RecallCard, RichHero, RichLesson, Section, TerminalDemo } from "../../shared";
import { TheoryBridge } from "../../shared";
const BLOCK_TITLE = "Блок 22 · PostgreSQL и перенос StudyHub";

export function Lesson127({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title={"Alembic на чистой PostgreSQL-базе"}
        intro={"Восстановим всю schema StudyHub на пустой PostgreSQL database только из migration history и проверим revision state через current, history, heads и alembic_version."}
        tags={[
          { icon: <GitBranch size={14} />, label: "revision chain → head" },
          { icon: <FileText size={14} />, label: "clean database reproducibility" },
        ]}
      />
      <TheoryBridge link={"Engine уже выполняет SELECT 1, но пустая database ещё не содержит tables. Теперь repository должен самостоятельно восстановить schema."} boundary={"Alembic migration history описывает изменение schema. create_all и ручное создание tables не должны становиться параллельными источниками истины."} />

      <Section number={"01"} title={"Пустая database — профессиональный тест проекта"}>
        <Lead>
          {"Работающий локальный database-файл может скрывать забытые ручные изменения. Чистая PostgreSQL database показывает, достаточно ли repository для воспроизведения schema."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «Пустая database — профессиональный тест проекта»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>


        <BranchExplorer
          code={"empty studyhub_test\n  ↓ alembic upgrade head\nrevision 1\n  ↓\nrevision 2\n  ↓\n...\n  ↓\nhead + alembic_version"}
          scenarios={[
            { label: "before migrations", activeLine: 0, output: "нет application tables" },
            { label: "migration chain", activeLine: 3, output: "upgrade functions идут по revision order" },
            { label: "after upgrade", activeLine: 8, output: "database отмечена current head" },
          ]}
        />

        <Callout tone="success">
          {"Reproducibility важнее того, что database автора «как-то работает»."}
        </Callout>

      </Section>

      <Section number={"02"} title={"History, heads и current отвечают на разные вопросы"}>
        <Lead>
          {"Alembic разделяет состояние repository и состояние конкретной database. Это позволяет диагностировать «есть migration file» и «migration уже применена»."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «History, heads и current отвечают на разные вопросы»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>

        <ContentList
          items={[
            { title: <>{"alembic history"}</>, description: "показать revision chain repository" },
            { title: <>{"alembic heads"}</>, description: "показать конечные revisions scripts" },
            { title: <>{"alembic current"}</>, description: "показать revision текущей database" },
            { title: <>{"alembic upgrade head"}</>, description: "применить недостающие upgrades до head" },
            { title: <>{"alembic_version"}</>, description: "служебная table с current revision" },
          ]}
        />

        <MatchPairs
          prompt={"Соедините вопрос и команду."}
          pairs={[
            { left: "Какие migrations существуют?", right: "alembic history" },
            { left: "Какой revision применён здесь?", right: "alembic current" },
            { left: "Куда должна прийти цепочка?", right: "alembic heads" },
            { left: "Как применить все недостающие?", right: "alembic upgrade head" },
          ]}
          explanation={"Каждая пара связывает термин с его конкретной ролью в текущей модели."}
        />

        <QuizCard
          question={<>{"alembic history читает только tables текущей database."}</>}
          options={["Правда", "Ложь"]} correctIndex={1}
          explanation={"History прежде всего описывает migration scripts repository; current связывает их с database state."}
        />

      </Section>

      <Section number={"03"} title={"env.py должен использовать те же Settings и metadata"}>
        <Lead>
          {"Migration environment подключается к той database, которая указана configuration, и сравнивает или применяет operations относительно metadata ORM-моделей."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «env.py должен использовать те же Settings и metadata»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>

        <CodeBlock
          caption={"фрагмент alembic/env.py"}
          code={"from alembic import context\n\nfrom app.config import settings\nfrom app.models.base import Base\n\nconfig = context.config\nconfig.set_main_option(\n    \"sqlalchemy.url\",\n    settings.database_url,\n)\n\ntarget_metadata = Base.metadata"}
        />

        <QuizCard
          question={"Какой источник URL безопаснее для проекта?"}
          options={[{ title: "Дублированный secret", code: "sqlalchemy.url = postgresql://real-password@localhost/studyhub_dev", description: "Secret живёт в alembic.ini и расходится с application config." }, { title: "Общие Settings", code: "config.set_main_option(\"sqlalchemy.url\", settings.database_url)", description: "Application и migrations используют одну validated configuration boundary." }]}
          
          correctIndex={1}
          explanation={"Один источник configuration снижает риск миграции не той database."}
        />

        <QuizCard orientation="vertical"
          description={<CodeBlock code={"target_metadata = None\n# autogenerate -- ORM models ignored"} />}
          question={"Что потеряет autogenerate?"}
          options={["Возможность сравнить ORM metadata со schema", "Connection к server", "psql meta-commands"]}
          correctIndex={0}
          explanation={"Без target_metadata Alembic не видит proposed ORM schema."}
          answer={<CodeBlock caption="Исправленный вариант" code={"target_metadata = Base.metadata"} />}
        />

      </Section>

      <Section number={"04"} title={"Восстанавливаем schema на чистой database"}>
        <Lead>
          {"Упражнение выполняется на studyhub_test или отдельной disposable database. Development data не удаляется ради проверки migrations."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «Восстанавливаем schema на чистой database»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>

        <TerminalDemo
          title={"clean database drill"}
          lines={[
            { cmd: "dropdb -h localhost -U postgres --if-exists studyhub_migration_check" },
            { cmd: "createdb -h localhost -U postgres -O studyhub_app studyhub_migration_check" },
            { cmd: "DATABASE_URL=postgresql+psycopg://studyhub_app:CHANGE_ME@localhost:5432/studyhub_migration_check alembic current" },
            { out: "Current revision(s):" },
            { cmd: "DATABASE_URL=.../studyhub_migration_check alembic upgrade head" },
            { out: "Running upgrade ... -> ..." },
          ]}
        />

        <CodeSequence
          title={"Соберите воспроизводимый migration flow"}
          prompt={"Расположите шаги безопасной проверки."}
          pieces={[
            { id: "create", code: "создать пустую disposable database" },
            { id: "current0", code: "alembic current" },
            { id: "upgrade", code: "alembic upgrade head" },
            { id: "current1", code: "alembic current" },
            { id: "inspect", code: "проверить tables и constraints" },
            { id: "seed", code: "сначала копировать старые tables вручную", note: "обходит migration history" },
          ]}
          correctOrder={["create", "current0", "upgrade", "current1", "inspect"]}
          explanation={"Сначала доказывается schema reproducibility, затем отдельно переносятся data."}
        />

        <Callout>
          {"Команды dropdb/createdb являются удобными clients. То же можно выполнить административным SQL; важно использовать disposable database."}
        </Callout>

      </Section>

      <Section number={"05"} title={"alembic_version фиксирует current revision"}>
        <Lead>
          {"После upgrade Alembic создаёт служебную table. Она не хранит весь журнал SQL; она связывает database с текущей точкой revision graph."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «alembic_version фиксирует current revision»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>

        <TerminalDemo
          title={"проверка результата"}
          lines={[
            { cmd: "psql -h localhost -U studyhub_app -d studyhub_migration_check" },
            { cmd: "\\dt" },
            { out: "public | alembic_version | table\npublic | users           | table\npublic | categories      | table\npublic | tasks           | table" },
            { cmd: "SELECT version_num FROM alembic_version;" },
            { out: "current_head_revision" },
          ]}
        />

        <RecallCard
          question={"Почему наличие application tables ещё не доказывает корректный Alembic state?"}
          hint={"Таблицы могли быть созданы вручную или create_all."}
          answer={
            <p>{"Нужно сверить current revision и migration history. Иначе schema может внешне выглядеть похожей, но Alembic не знает, какие upgrades уже применены."}</p>
          }
        />

        <Callout tone="success">
          {"Не редактируйте alembic_version вручную, чтобы скрыть ошибку. Сначала установите реальную причину расхождения."}
        </Callout>

      </Section>

      <Section number={"06"} title={"Migration failure должна оставлять понятный след"}>
        <Lead>
          {"Ошибка upgrade может возникнуть из-за существующего object, неверного SQL, missing dependency или неправильного порядка revisions. PostgreSQL обычно позволяет Alembic использовать transactional DDL, но диагностика всё равно начинается с failing revision."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «Migration failure должна оставлять понятный след»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>

        <BranchExplorer
          code={"alembic upgrade head\n├─ connection error\n├─ revision not found\n├─ SQL/constraint error\n└─ multiple heads"}
          scenarios={[
            { label: "connection", activeLine: 1, output: "проверить DATABASE_URL и server" },
            { label: "migration body", activeLine: 3, output: "прочитать revision и database error" },
            { label: "revision graph", activeLine: 4, output: "проверить heads и merge strategy" },
          ]}
        />

        <QuizCard orientation="vertical"
          description={<CodeBlock code={"def upgrade():\n    op.create_table(\"tasks\", ...)\n\n# tasks already created manually"} />}
          question={"Почему upgrade падает на чисто выглядящем проекте?"}
          options={["Schema имеет ручной object вне согласованной history", "FastAPI не поддерживает Alembic", "psycopg не выполняет DDL"]}
          correctIndex={0}
          explanation={"Ручное создание и migrations стали двумя источниками истины."}
          answer={<CodeBlock caption="Исправленный вариант" code={"# удалить disposable database и восстановить её только через\nalembic upgrade head"} />}
        />

        <Callout>
          {"На development database с ценными data удаление недопустимо. Сначала backup и отдельный recovery plan; destructive reset используется только для disposable среды."}
        </Callout>

      </Section>

      <Section number={"07"} title={"create_all больше не управляет schema"}>
        <Lead>
          {"Base.metadata.create_all полезен в раннем обучении, но после появления migration history он создаёт обходной путь и скрывает пропущенные revisions."}
        </Lead>

        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Назовите входные данные, границу ответственности и ожидаемый результат для модели «create_all больше не управляет schema»."}</p>
          <h3>{"Контролируемое изменение"}</h3>
          <p>{"Измените один параметр текущего примера, заранее запишите прогноз и только затем повторите проверку."}</p>
          <h3>{"Профессиональное объяснение"}</h3>
          <p>{"Определите уровень возможной ошибки: client, network, server, database, schema, migration или HTTP contract."}</p>
        </div>

        <QuizCard
          question={"Как запускать готовый StudyHub?"}
          options={[{ title: "Скрытое создание при import", code: "Base.metadata.create_all(bind=engine)\napp = FastAPI()", description: "Schema меняется вне reviewable migration chain." }, { title: "Явная migration command", code: "alembic upgrade head\nuvicorn app.main:app", description: "Schema version обновляется отдельным контролируемым шагом." }]}
          
          correctIndex={1}
          explanation={"Migration является versioned artifact repository и может быть проверена до запуска application."}
        />

        <ContentList
          items={[
            { title: <>{"create_all"}</>, description: "создаёт отсутствующие tables, но не ведёт migration history" },
            { title: <>{"revision file"}</>, description: "reviewable изменение schema" },
            { title: <>{"upgrade head"}</>, description: "применяет chain в правильном порядке" },
            { title: <>{"current"}</>, description: "показывает state конкретной database" },
          ]}
        />

        <Callout>
          {"Test fixtures тоже должны применять migrations или использовать schema, построенную из них, если цель теста — production-like reproducibility."}
        </Callout>

      </Section>

      <Section number={"08"} title={"Контрольная точка: schema из Git"}>
        <Lead>
          {"Ученик удаляет disposable database, создаёт её снова, выполняет upgrade head и доказывает совпадение tables, constraints и revision без create_all."}
        </Lead>

        <div className="lesson-check-group">
          <QuizCard
            question={"Что показывает alembic current?"}
            options={["revision текущей database", "только список Python packages", "HTTP routes"]}
            correctIndex={0}
            explanation={"Current связывает database state с revision graph."}
          />
          <QuizCard
            question={"Что делает upgrade head?"}
            options={["применяет недостающие migrations", "копирует production data", "создаёт PostgreSQL role"]}
            correctIndex={0}
            explanation={"Команда выполняет upgrade functions до head."}
          />
          <QuizCard
            question={"Зачем target_metadata?"}
            options={["для сравнения ORM schema при autogenerate", "для password authentication", "для CORS"]}
            correctIndex={0}
            explanation={"Alembic получает SQLAlchemy metadata models."}
          />
          <QuizCard
            question={"Почему create_all убирается?"}
            options={["чтобы migrations были единым источником schema history", "потому что PostgreSQL не создаёт tables", "чтобы отключить tests"]}
            correctIndex={0}
            explanation={"Два независимых способа изменения schema создают drift."}
          />
        </div>

        <KeyTakeaways
          points={[
            <>{"Чистая database проверяет воспроизводимость repository."}</>,
            <>{"history и heads описывают revision graph."}</>,
            <>{"current показывает revision конкретной database."}</>,
            <>{"upgrade head применяет недостающие upgrade functions."}</>,
            <>{"env.py использует Settings и Base.metadata."}</>,
            <>{"alembic_version связывает database с current revision."}</>,
            <>{"create_all не должен конкурировать с migration history."}</>,
          ]}
        />

        <PracticeCta text={"Создайте disposable studyhub_migration_check, примените alembic upgrade head и проверьте \\dt, ключевые constraints и alembic current. Удалите create_all из startup path, добавьте migration runbook и зафиксируйте коммитом chore: verify PostgreSQL migrations from clean database."} />

      </Section>

    </RichLesson>
  );
}
