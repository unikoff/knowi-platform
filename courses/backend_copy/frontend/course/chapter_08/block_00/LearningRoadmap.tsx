import { PackageCheck, Route } from "lucide-react";
import { BranchExplorer, Callout, CodeBlock, CodeSequence, FlipCards, KeyTakeaways, Lead, MatchPairs, ContentList, LinkedNotes, PracticeCta, QuizCard, RichHero, RichLesson, Section, StepThrough, TerminalDemo, TypeCard, TypeCards } from "../../shared";

export function LearningRoadmap() {
  return (
    <RichLesson>
      <RichHero
        chip={"ЭТАП 8 · карта обучения"}
        title={"План обучения: этап 8"}
        intro={"За 24 занятия Async StudyHub превратится в Deployable StudyHub: ученик освоит Linux-runtime, соберёт non-root Docker image, поднимет Compose stack, автоматизирует quality gates, развернёт exact version и проверит smoke test с rollback."}
        tags={[
          {
            icon: <Route size={14} />,
            label: "занятия 165–188",
          },
          {
            icon: <PackageCheck size={14} />,
            label: "Deployable StudyHub",
          },
        ]}
      />

      <Section number={"01"} title={"От Async StudyHub к воспроизводимому релизу"}>
        <Lead>
          {"Async StudyHub уже умеет работать с PostgreSQL, внешними HTTP-сервисами и конкурентным I/O. Этап 8 отвечает на другой вопрос: сможет ли новый разработчик или сервер воспроизвести тот же запуск без IDE, скрытых локальных настроек и ручных догадок?"}
        </Lead>


        <ContentList
          items={[
            { title: <>{"вход этапа"}</>, description: <>{"Async StudyHub на PostgreSQL"}</> },
            { title: <>{"основной проект"}</>, description: <>{"Deployable StudyHub"}</> },
            { title: <>{"занятия"}</>, description: <>{"165–188"}</> },
            { title: <>{"блоки"}</>, description: <>{"29–32"}</> },
            { title: <>{"ключевая проблема"}</>, description: <>{"локальный запуск зависит от машины автора"}</> },
            { title: <>{"доказательство"}</>, description: <>{"runbook, Compose, CI, image tag, smoke test"}</> },
            { title: <>{"граница"}</>, description: <>{"Kubernetes и сложная cloud-инфраструктура не требуются"}</> },
          ]}
        />

        <CodeBlock
          caption={"эволюция проекта"}
          code={"Async StudyHub\n→ Linux process and config\n→ logs + health/readiness\n→ Docker image\n→ Compose stack\n→ CI quality gates\n→ versioned deployment\n→ smoke test + rollback\n→ Deployable StudyHub"}
        />

        <CodeSequence
          title={"Соберите безопасный инфраструктурный маршрут"}
          prompt={"Расположите шаги так, чтобы каждый новый слой опирался на уже проверенный предыдущий."}
          pieces={[
            {
              id: "linux",
              code: "запустить StudyHub без IDE и записать runbook",
            },
            {
              id: "observe",
              code: "добавить logs, health и readiness",
            },
            {
              id: "image",
              code: "собрать и проверить Docker image",
            },
            {
              id: "compose",
              code: "соединить API, PostgreSQL и Redis",
            },
            {
              id: "ci",
              code: "автоматизировать quality gates и migrations",
            },
            {
              id: "publish",
              code: "создать image tag по commit SHA",
            },
            {
              id: "deploy",
              code: "развернуть, smoke test, rollback",
            },
          ]}
          correctOrder={[
            "linux",
            "observe",
            "image",
            "compose",
            "ci",
            "publish",
            "deploy",
          ]}
          explanation={"Deployment появляется последним: сначала проект должен быть понятен, наблюдаем, контейнеризован и автоматически проверен."}
        />

        <TypeCards>
          <TypeCard
            badge={"среда"}
            title={"Linux runtime"}
            code={"process + env + port"}
          >
            {"Показывает, где и с какой конфигурацией живёт сервер."}
          </TypeCard>
          <TypeCard
            badge={"артефакт"}
            badgeTone={"float"}
            title={"Docker image"}
            code={"immutable runtime template"}
          >
            {"Фиксирует код, Python и dependencies в одной версии."}
          </TypeCard>
          <TypeCard
            badge={"система"}
            badgeTone={"str"}
            title={"Compose stack"}
            code={"api + db + redis"}
          >
            {"Соединяет отдельные процессы общей сетью и конфигурацией."}
          </TypeCard>
          <TypeCard
            badge={"доставка"}
            title={"CI/CD flow"}
            code={"commit → checks → image → deploy"}
          >
            {"Не позволяет выпускать непроверенный commit как релиз."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Сначала воспроизвести вручную"}</h3>
          <p>
            {"Автоматизировать можно только понятный последовательный сценарий."}
          </p>

          <h3>{"Ломать контролируемо"}</h3>
          <p>
            {"Проверять занятый port, missing env, unavailable database и failing migration."}
          </p>

          <h3>{"Фиксировать команды"}</h3>
          <p>
            {"Каждый блок оставляет README, runbook или checklist."}
          </p>

          <h3>{"Отделять artifact от config"}</h3>
          <p>
            {"Image не хранит production secrets и не зависит от конкретного host."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Инфраструктура изучается как продолжение backend-кода: request должен дойти до процесса, database и обратно, а разработчик обязан найти место сбоя."}
        </Callout>

        <Callout tone={"warn"}>
          {"Работающий один раз deployment без smoke test и rollback остаётся экспериментом, а не воспроизводимым релизом."}
        </Callout>

      </Section>

      <Section number={"02"} title={"Блок 29 · Linux, процессы, окружения и логи"}>
        <Lead>
          {"Первый блок снимает зависимость от IDE. Ученик работает из Linux-подобного терминала, связывает команду запуска с process, PID и port, передаёт configuration через environment, читает structured logs и различает liveness, readiness и graceful shutdown."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"cwd"}</>, description: <>{"директория, относительно которой разрешаются paths"}</> },
            { title: <>{"PID"}</>, description: <>{"идентификатор конкретного процесса"}</> },
            { title: <>{"port"}</>, description: <>{"точка сетевого прослушивания процесса"}</> },
            { title: <>{"environment"}</>, description: <>{"конфигурация конкретного запуска"}</> },
            { title: <>{"stdout/stderr"}</>, description: <>{"каналы наблюдаемого вывода процесса"}</> },
            { title: <>{"health"}</>, description: <>{"процесс и приложение отвечают"}</> },
            { title: <>{"readiness"}</>, description: <>{"критические зависимости готовы"}</> },
          ]}
        />

        <CodeBlock
          caption={"маршрут Linux-запуска"}
          code={"shell\n→ current working directory\n→ environment variables\n→ uvicorn process\n→ PID + listening port\n→ stdout/stderr logs\n→ /health and /ready\n→ SIGTERM\n→ graceful shutdown"}
        />

        <TerminalDemo
          title={"минимальная диагностика запуска"}
          lines={[
            {
              cmd: "pwd",
            },
            {
              out: "/srv/studyhub",
            },
            {
              cmd: "APP_ENV=production uvicorn app.main:app --host 0.0.0.0 --port 8000",
            },
            {
              out: "INFO application_started app_env=production",
            },
            {
              cmd: "curl http://127.0.0.1:8000/health",
            },
            {
              out: "{\"status\":\"ok\"}",
            },
            {
              cmd: "curl http://127.0.0.1:8000/ready",
            },
            {
              out: "{\"status\":\"ready\",\"database\":\"ok\"}",
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"path"}
            title={"Filesystem"}
            code={"pwd / ls / cd"}
          >
            {"Определяет, какие файлы видит процесс и как разрешаются относительные пути."}
          </TypeCard>
          <TypeCard
            badge={"process"}
            badgeTone={"float"}
            title={"Server process"}
            code={"PID + port"}
          >
            {"Долгоживущий экземпляр программы, принимающий HTTP requests."}
          </TypeCard>
          <TypeCard
            badge={"config"}
            badgeTone={"str"}
            title={"Environment"}
            code={"DATABASE_URL=..."}
          >
            {"Меняет поведение запуска без изменения исходного кода."}
          </TypeCard>
          <TypeCard
            badge={"trace"}
            title={"Logs and probes"}
            code={"request_id + /ready"}
          >
            {"Показывают состояние приложения и путь конкретного request."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Запустить из двух директорий"}</h3>
          <p>
            {"Увидеть, почему относительный path зависит от cwd."}
          </p>

          <h3>{"Занять port"}</h3>
          <p>
            {"Запустить второй Uvicorn и прочитать address already in use."}
          </p>

          <h3>{"Сломать config"}</h3>
          <p>
            {"Удалить обязательную environment variable и получить раннюю понятную ошибку."}
          </p>

          <h3>{"Остановить корректно"}</h3>
          <p>
            {"Послать SIGTERM и убедиться, что shutdown завершает resources."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Container позднее будет запускать тот же Linux process. Поэтому path, environment, stdout и signals нужно понять до Docker."}
        </Callout>

        <Callout tone={"warn"}>
          {"Health endpoint не должен выполнять тяжёлый бизнес-сценарий. Он быстро отвечает на ограниченный вопрос о состоянии приложения."}
        </Callout>

      </Section>

      <Section number={"03"} title={"Блок 30 · Dockerfile и контейнер приложения"}>
        <Lead>
          {"После понятного Linux-process появляется Docker. Ученик различает image, container и process, собирает первый Dockerfile, наблюдает layers и cache, передаёт ports и environment, исключает secrets через .dockerignore и запускает API непривилегированным пользователем."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"Dockerfile"}</>, description: <>{"описание последовательности сборки image"}</> },
            { title: <>{"image"}</>, description: <>{"неизменяемый шаблон filesystem и metadata"}</> },
            { title: <>{"container"}</>, description: <>{"запущенный экземпляр image"}</> },
            { title: <>{"layer"}</>, description: <>{"результат отдельной build-инструкции"}</> },
            { title: <>{"build context"}</>, description: <>{"набор файлов, доступных команде COPY"}</> },
            { title: <>{"port mapping"}</>, description: <>{"host_port → container_port"}</> },
            { title: <>{"non-root"}</>, description: <>{"процесс с ограниченными правами"}</> },
          ]}
        />

        <CodeBlock
          caption={"Dockerfile как маршрут"}
          code={"FROM python:3.12-slim\nWORKDIR /app\nCOPY requirements.txt .\nRUN pip install --no-cache-dir -r requirements.txt\nCOPY app ./app\nRUN useradd --create-home appuser\nUSER appuser\nCMD [\"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]"}
        />

        <QuizCard
          question={"Какой порядок лучше сохраняет cache dependencies после изменения Python-кода?"}
          options={[{ title: "Копировать всё до install", code: "COPY . .\nRUN pip install -r requirements.txt", description: "Любое изменение source инвалидирует слой установки dependencies." }, { title: "Сначала dependency-файл", code: "COPY requirements.txt .\nRUN pip install -r requirements.txt\nCOPY app ./app", description: "Source меняется отдельно, а неизменные dependencies берутся из cache." }]}
          
          correctIndex={1}
          explanation={"Стабильные inputs размещаются раньше изменяемого source, чтобы дорогой слой переиспользовался."}
        />

        <TypeCards>
          <TypeCard
            badge={"build"}
            title={"Dockerfile"}
            code={"docker build -t studyhub:dev ."}
          >
            {"Превращает build context в версионируемый image."}
          </TypeCard>
          <TypeCard
            badge={"run"}
            badgeTone={"float"}
            title={"Container"}
            code={"docker run --rm -p 8000:8000"}
          >
            {"Запускает один process с отдельным filesystem и environment."}
          </TypeCard>
          <TypeCard
            badge={"security"}
            badgeTone={"str"}
            title={"Non-root user"}
            code={"USER appuser"}
          >
            {"Ограничивает последствия ошибки внутри процесса."}
          </TypeCard>
          <TypeCard
            badge={"debug"}
            title={"Lifecycle tools"}
            code={"ps / logs / inspect / exec"}
          >
            {"Помогают отделить build failure от runtime failure."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Собрать прозрачную версию"}</h3>
          <p>
            {"Сначала рабочий image без преждевременной оптимизации."}
          </p>

          <h3>{"Проверить cache"}</h3>
          <p>
            {"Изменить source и убедиться, что install layer переиспользуется."}
          </p>

          <h3>{"Проверить ephemeral state"}</h3>
          <p>
            {"Создать файл внутри container, удалить container и сравнить новый запуск."}
          </p>

          <h3>{"Сломать CMD"}</h3>
          <p>
            {"Диагностировать exit code и missing module через docker logs."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Container не является полной виртуальной машиной в учебной модели. Это изолированный process, использующий подготовленный image filesystem."}
        </Callout>

        <Callout tone={"warn"}>
          {"Файл .env, Git history, local caches и test artifacts не должны случайно попадать в build context и image layers."}
        </Callout>

      </Section>

      <Section number={"04"} title={"Блок 31 · Docker Compose: API, PostgreSQL и Redis"}>
        <Lead>
          {"Один container уже запускается. Теперь Compose описывает локальную систему из нескольких services: API, PostgreSQL, Redis и одноразовую migration-команду. Ученик понимает внутренний DNS, volumes, readiness и восстановление clean environment."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"service"}</>, description: <>{"описание одного типа process в Compose"}</> },
            { title: <>{"network"}</>, description: <>{"внутренняя связь services по DNS-именам"}</> },
            { title: <>{"hostname db"}</>, description: <>{"имя PostgreSQL service внутри сети"}</> },
            { title: <>{"named volume"}</>, description: <>{"постоянные database files вне container"}</> },
            { title: <>{"healthcheck"}</>, description: <>{"наблюдаемое состояние готовности service"}</> },
            { title: <>{"migration job"}</>, description: <>{"одноразовое обновление schema"}</> },
            { title: <>{"depends_on"}</>, description: <>{"порядок запуска с ограниченными гарантиями"}</> },
          ]}
        />

        <CodeBlock
          caption={"локальная система"}
          code={"browser / client\n→ host:8000\n→ api service\n→ internal network\n├── db:5432 + pgdata volume\n├── redis:6379\n└── migrate service → alembic upgrade head\n\nhealthchecks decide readiness"}
        />

        <QuizCard orientation="vertical"
          description={<CodeBlock code={"services:\n  api:\n    environment:\n      DATABASE_URL: postgresql+asyncpg://app:pass@localhost:5432/studyhub\n  db:\n    image: postgres:16"} />}
          question={"Почему API-container не подключается к PostgreSQL через localhost?"}
          options={[
            "localhost внутри API-container указывает на сам API-container",
            "PostgreSQL запрещён в Docker Compose",
            "DATABASE_URL не может быть environment variable",
          ]}
          correctIndex={0}
          explanation={"Services общаются по внутреннему DNS. PostgreSQL доступен по hostname db и container port 5432."}
          answer={<CodeBlock caption="Исправленный вариант" code={"DATABASE_URL=postgresql+asyncpg://app:pass@db:5432/studyhub"} />}
        />

        <TypeCards>
          <TypeCard
            badge={"api"}
            title={"Application service"}
            code={"build: ."}
          >
            {"Запускает тот же проверенный image и публикует HTTP port."}
          </TypeCard>
          <TypeCard
            badge={"db"}
            badgeTone={"float"}
            title={"PostgreSQL service"}
            code={"hostname: db"}
          >
            {"Хранит source of truth и использует named volume."}
          </TypeCard>
          <TypeCard
            badge={"cache"}
            badgeTone={"str"}
            title={"Redis service"}
            code={"redis:6379"}
          >
            {"Готовит временную инфраструктуру для будущего этапа, но не заменяет database."}
          </TypeCard>
          <TypeCard
            badge={"schema"}
            title={"Migration job"}
            code={"alembic upgrade head"}
          >
            {"Применяет schema отдельно от долгоживущего API-process."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Поднять сеть из двух services"}</h3>
          <p>
            {"Сначала API и PostgreSQL, затем добавить volume и Redis."}
          </p>

          <h3>{"Пересоздать API"}</h3>
          <p>
            {"Убедиться, что named volume сохраняет database data."}
          </p>

          <h3>{"Удалить volume сознательно"}</h3>
          <p>
            {"Различить restart, recreate и полную очистку persistent state."}
          </p>

          <h3>{"Проверить clean bootstrap"}</h3>
          <p>
            {"На пустой database выполнить migrations и smoke scenario."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Compose является описанием локальной системы, а не production orchestrator. Его ценность здесь — воспроизводимость и понятные связи processes."}
        </Callout>

        <Callout tone={"warn"}>
          {"depends_on сам по себе не гарантирует, что PostgreSQL уже принимает queries. Нужен healthcheck/readiness и корректная retry-политика старта."}
        </Callout>

      </Section>

      <Section number={"05"} title={"Блок 32 · GitHub Actions, CI/CD и первый деплой"}>
        <Lead>
          {"Локальная система воспроизводима, поэтому проверки можно перенести на независимый runner. Ученик строит quality gates, запускает PostgreSQL service и migrations в CI, собирает image по commit SHA, передаёт production secrets вне Git и завершает release smoke test и rollback."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"workflow"}</>, description: <>{"автоматический pipeline по событию GitHub"}</> },
            { title: <>{"job"}</>, description: <>{"набор steps на отдельном runner"}</> },
            { title: <>{"quality gate"}</>, description: <>{"условие, блокирующее дальнейший release"}</> },
            { title: <>{"service container"}</>, description: <>{"PostgreSQL для integration tests"}</> },
            { title: <>{"image tag"}</>, description: <>{"связь artifact с commit SHA или release"}</> },
            { title: <>{"secret"}</>, description: <>{"production value вне repository"}</> },
            { title: <>{"smoke test"}</>, description: <>{"короткая проверка критического пользовательского пути"}</> },
          ]}
        />

        <CodeBlock
          caption={"release pipeline"}
          code={"pull request\n→ format check\n→ lint\n→ unit tests\n→ PostgreSQL ready\n→ alembic upgrade head\n→ integration tests\n→ docker build\n→ image tag = commit SHA\n→ deploy exact tag\n→ smoke test\n→ success or rollback"}
        />

        <BranchExplorer
          code={"format\nlint\ntests\nmigrations\nbuild image\ndeploy\nsmoke test\nrollback previous tag"}
          scenarios={[
            {
              label: "format failed",
              activeLine: 0,
              output: "pipeline stops before tests and release",
            },
            {
              label: "migration failed",
              activeLine: 3,
              output: "image is not deployed",
            },
            {
              label: "all gates green",
              activeLine: 5,
              output: "exact image tag is deployed",
            },
            {
              label: "smoke test failed",
              activeLine: 6,
              output: "previous known-good tag is restored",
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"verify"}
            title={"Continuous Integration"}
            code={"PR → checks"}
          >
            {"Проверяет commit в независимом окружении до merge."}
          </TypeCard>
          <TypeCard
            badge={"artifact"}
            badgeTone={"float"}
            title={"Versioned image"}
            code={"studyhub:<sha>"}
          >
            {"Связывает запущенный runtime с конкретной историей Git."}
          </TypeCard>
          <TypeCard
            badge={"config"}
            badgeTone={"str"}
            title={"Production environment"}
            code={"secrets + DATABASE_URL"}
          >
            {"Передаётся при deployment и не встраивается в image."}
          </TypeCard>
          <TypeCard
            badge={"safety"}
            title={"Rollback"}
            code={"deploy previous tag"}
          >
            {"Возвращает известную рабочую версию после провала post-deploy проверки."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Нарисовать pipeline"}</h3>
          <p>
            {"До YAML назвать каждую проверку и причину остановки."}
          </p>

          <h3>{"Получить красный run"}</h3>
          <p>
            {"Намеренно сломать formatting или test и прочитать logs."}
          </p>

          <h3>{"Восстановить clean database"}</h3>
          <p>
            {"Применить migrations в CI перед integration tests."}
          </p>

          <h3>{"Провести учебный rollback"}</h3>
          <p>
            {"Выпустить проблемный tag, обнаружить smoke test и вернуть previous tag."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"CI/CD не заменяет понимание команд. Workflow должен повторять локально воспроизводимые format, lint, test, migration и build steps."}
        </Callout>

        <Callout tone={"warn"}>
          {"Тег latest не доказывает, какой commit развернут. Для расследования и rollback нужен неизменяемый traceable tag."}
        </Callout>

      </Section>

      <Section number={"06"} title={"Артефакты этапа: что должно появиться в репозитории"}>
        <Lead>
          {"Инфраструктурный этап легко превратить в набор команд без результата. Поэтому каждый блок оставляет проверяемый artifact: runbook, Dockerfile, compose.yaml, workflow, release checklist и историю версий."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"RUNBOOK.md"}</>, description: <>{"запуск и диагностика Linux-process"}</> },
            { title: <>{"Dockerfile"}</>, description: <>{"reproducible application image"}</> },
            { title: <>{".dockerignore"}</>, description: <>{"ограниченный build context"}</> },
            { title: <>{"compose.yaml"}</>, description: <>{"local multi-service stack"}</> },
            { title: <>{"ci.yml"}</>, description: <>{"automatic quality gates"}</> },
            { title: <>{"RELEASE.md"}</>, description: <>{"deploy, smoke test и rollback"}</> },
            { title: <>{".env.example"}</>, description: <>{"публичный контракт configuration без secrets"}</> },
          ]}
        />

        <CodeBlock
          caption={"дерево результата"}
          code={"studyhub/\n├── app/\n├── tests/\n├── alembic/\n├── Dockerfile\n├── .dockerignore\n├── compose.yaml\n├── .env.example\n├── .github/workflows/ci.yml\n├── RUNBOOK.md\n├── RELEASE.md\n└── README.md"}
        />

        <MatchPairs
          prompt={"Соедините artifact с вопросом, на который он обязан отвечать."}
          leftTitle={"Artifact"}
          rightTitle={"Проверяемый вопрос"}
          pairs={[
            {
              left: "RUNBOOK.md",
              right: "как запустить и диагностировать process",
            },
            {
              left: "Dockerfile",
              right: "как собрать одинаковый application runtime",
            },
            {
              left: "compose.yaml",
              right: "как соединить API, database и Redis",
            },
            {
              left: "ci.yml",
              right: "какие gates проходит каждый pull request",
            },
            {
              left: "RELEASE.md",
              right: "как проверить deployment и откатить версию",
            },
            {
              left: ".env.example",
              right: "какие config values обязательны",
            },
          ]}
          explanation={"Файл нужен не ради структуры репозитория, а ради конкретного воспроизводимого вопроса."}
        />

        <TypeCards>
          <TypeCard
            badge={"start"}
            title={"One-command start"}
            code={"docker compose up --build"}
          >
            {"Поднимает local stack без ручной настройки каждого process."}
          </TypeCard>
          <TypeCard
            badge={"proof"}
            badgeTone={"float"}
            title={"Green CI"}
            code={"format + lint + tests + migrations"}
          >
            {"Показывает, что commit проходит независимые проверки."}
          </TypeCard>
          <TypeCard
            badge={"version"}
            badgeTone={"str"}
            title={"Traceable release"}
            code={"image tag = SHA"}
          >
            {"Позволяет связать runtime с конкретным исходным кодом."}
          </TypeCard>
          <TypeCard
            badge={"recovery"}
            title={"Rollback plan"}
            code={"previous known-good tag"}
          >
            {"Даёт заранее подготовленное действие при post-deploy failure."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Проверить с чистого checkout"}</h3>
          <p>
            {"Не использовать уже созданные local files и скрытые caches."}
          </p>

          <h3>{"Передать инструкцию другому человеку"}</h3>
          <p>
            {"Записать все уточнения, которые пришлось сказать устно."}
          </p>

          <h3>{"Сверить config"}</h3>
          <p>
            {"Каждая используемая environment variable присутствует в .env.example."}
          </p>

          <h3>{"Сверить release"}</h3>
          <p>
            {"README называет точный image tag, health URL и smoke scenario."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Документация является частью инженерного результата, потому что инфраструктурный сценарий должен быть воспроизводим после паузы и другим разработчиком."}
        </Callout>

        <Callout tone={"warn"}>
          {"README, который говорит только «запустите Docker», не фиксирует environment, migrations, healthcheck и ожидаемые failures."}
        </Callout>

      </Section>

      <Section number={"07"} title={"Диагностическая карта: где искать сбой"}>
        <Lead>
          {"Ученик должен не просто выполнить happy path, а локализовать failure по слою. Одинаковый симптом «API не отвечает» может означать неверный cwd, missing environment, exited container, unavailable database, failing migration или failed deployment."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"docker build"}</>, description: <>{"проверить build context и layers"}</> },
            { title: <>{"docker ps -a"}</>, description: <>{"увидеть running и exited containers"}</> },
            { title: <>{"docker logs"}</>, description: <>{"прочитать stdout/stderr process"}</> },
            { title: <>{"docker inspect"}</>, description: <>{"проверить environment, ports и health state"}</> },
            { title: <>{"docker compose ps"}</>, description: <>{"увидеть состояние всех services"}</> },
            { title: <>{"CI logs"}</>, description: <>{"найти первый failing gate"}</> },
            { title: <>{"smoke test"}</>, description: <>{"проверить пользовательский flow после deployment"}</> },
          ]}
        />

        <CodeBlock
          caption={"алгоритм расследования"}
          code={"symptom\n→ reproduce\n→ identify layer\n→ collect evidence\n→ form one hypothesis\n→ change one thing\n→ rerun same check\n→ document root cause"}
        />

        <StepThrough
          code={"curl /health\ndocker compose ps\ndocker compose logs api\ndocker compose logs db\nalembic current\nrun smoke test"}
          steps={[
            {
              line: 0,
              note: "Сначала фиксируем внешний симптом.",
              vars: {
                "health": "connection refused",
              },
            },
            {
              line: 1,
              note: "Проверяем, существует ли running API-container.",
              vars: {
                "api": "exited",
              },
            },
            {
              line: 2,
              note: "Читаем первую runtime error.",
              vars: {
                "error": "missing SECRET_KEY",
              },
            },
            {
              line: 3,
              note: "Не меняем database, если evidence указывает на config API.",
              vars: {
                "db": "healthy",
              },
            },
            {
              line: 4,
              note: "После исправления config подтверждаем schema state.",
              vars: {
                "revision": "head",
              },
            },
            {
              line: 5,
              note: "Повторяем тот же внешний сценарий.",
              vars: {
                "smoke": "passed",
              },
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"build"}
            title={"Build failure"}
            code={"docker build"}
          >
            {"Container ещё не существует; исследуются context и Dockerfile layers."}
          </TypeCard>
          <TypeCard
            badge={"exit"}
            badgeTone={"float"}
            title={"Runtime failure"}
            code={"docker logs"}
          >
            {"Image собран, но process завершился или healthcheck стал unhealthy."}
          </TypeCard>
          <TypeCard
            badge={"dependency"}
            badgeTone={"str"}
            title={"Readiness failure"}
            code={"db not ready / migration missing"}
          >
            {"Process жив, но критическая dependency не готова."}
          </TypeCard>
          <TypeCard
            badge={"release"}
            title={"Behavior failure"}
            code={"smoke test failed"}
          >
            {"Infrastructure отвечает, но пользовательский контракт нарушен."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Не начинать с exec"}</h3>
          <p>
            {"Сначала посмотреть state и logs снаружи, не меняя container."}
          </p>

          <h3>{"Фиксировать первый error"}</h3>
          <p>
            {"Поздние сообщения могут быть следствием первичной причины."}
          </p>

          <h3>{"Повторять одинаковый check"}</h3>
          <p>
            {"Иначе невозможно доказать, что изменение исправило именно проблему."}
          </p>

          <h3>{"Записывать root cause"}</h3>
          <p>
            {"Runbook растёт из реальных расследований, а не из абстрактного списка команд."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Хороший infrastructure developer умеет назвать не только исправление, но и evidence, которое подтвердило слой и причину сбоя."}
        </Callout>

        <Callout tone={"warn"}>
          {"docker exec полезен для диагностики, но ручное исправление внутри running container не становится воспроизводимым изменением проекта."}
        </Callout>

      </Section>

      <Section number={"08"} title={"Контрольная точка этапа 8"}>
        <Lead>
          {"Этап завершён, когда ученик способен с чистого checkout поднять local stack, объяснить путь одного request через process и services, показать зелёный CI, развернуть точный image tag, выполнить smoke test и вернуть предыдущую версию при сбое."}
        </Lead>


        <LinkedNotes
          items={[
            { title: <>{"Linux"}</>, description: <>{"process, environment, logs и signals объяснимы"}</> },
            { title: <>{"Docker"}</>, description: <>{"image собирается и запускается non-root"}</> },
            { title: <>{"Compose"}</>, description: <>{"local stack поднимается одной командой"}</> },
            { title: <>{"Database"}</>, description: <>{"clean schema восстанавливается migrations"}</> },
            { title: <>{"CI"}</>, description: <>{"pull request проходит независимые quality gates"}</> },
            { title: <>{"CD"}</>, description: <>{"deploy использует точный versioned artifact"}</> },
            { title: <>{"Recovery"}</>, description: <>{"smoke failure приводит к понятному rollback"}</> },
          ]}
        />

        <CodeBlock
          caption={"финальный сценарий"}
          code={"git clone\n→ copy .env.example\n→ docker compose up --build\n→ migrations reach head\n→ /health and /ready are green\n→ run tests\n→ push branch\n→ CI green\n→ build image:<sha>\n→ deploy exact tag\n→ smoke test\n→ release or rollback"}
        />

        <FlipCards
          cards={[
            {
              front: <>{"Как доказать воспроизводимость?"}</>,
              back: <>{"Запустить clean checkout по README и получить тот же результат."}</>,
            },
            {
              front: <>{"Как доказать traceability?"}</>,
              back: <>{"Показать соответствие running image tag и commit SHA."}</>,
            },
            {
              front: <>{"Как доказать readiness?"}</>,
              back: <>{"Проверить критические dependencies отдельным быстрым endpoint."}</>,
            },
            {
              front: <>{"Как доказать безопасность release?"}</>,
              back: <>{"Выполнить smoke test и заранее проверенный rollback."}</>,
            },
            {
              front: <>{"Как доказать независимость config?"}</>,
              back: <>{"Запустить один image с разными environment values."}</>,
            },
            {
              front: <>{"Как доказать сохранность PostgreSQL?"}</>,
              back: <>{"Пересоздать container и проверить named volume."}</>,
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"local"}
            title={"Reproducible stack"}
            code={"compose up --build"}
          >
            {"Другой человек получает рабочую систему из repository."}
          </TypeCard>
          <TypeCard
            badge={"pipeline"}
            badgeTone={"float"}
            title={"Automated evidence"}
            code={"green CI"}
          >
            {"Commit проходит одинаковые gates на независимом runner."}
          </TypeCard>
          <TypeCard
            badge={"release"}
            badgeTone={"str"}
            title={"Versioned deployment"}
            code={"image:<sha>"}
          >
            {"Production runtime связан с конкретной версией source."}
          </TypeCard>
          <TypeCard
            badge={"recovery"}
            title={"Controlled rollback"}
            code={"previous tag"}
          >
            {"Ошибка после deployment не превращается в импровизацию."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Провести защиту без IDE"}</h3>
          <p>
            {"Использовать terminal, logs, HTTP client и документацию."}
          </p>

          <h3>{"Показать один controlled failure"}</h3>
          <p>
            {"Например, missing secret, unavailable database или broken migration."}
          </p>

          <h3>{"Объяснить границы"}</h3>
          <p>
            {"Назвать, почему Kubernetes, Terraform и multi-cluster delivery пока не нужны."}
          </p>

          <h3>{"Зафиксировать release"}</h3>
          <p>
            {"Создать notes, tag, checklist и incident note учебного rollback."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Deployable StudyHub — это совокупность кода, image, configuration contract, migrations, checks, documentation и recovery procedure."}
        </Callout>

        <Callout tone={"warn"}>
          {"Не добавляйте новую LMS-функциональность в неделю защиты этапа. Сначала стабилизируйте существующий release и устраните infrastructure debt."}
        </Callout>

        <div className="lesson-check-group">
          <QuizCard
            question={"Почему Docker изучается после Linux-process и environment?"}
            options={[
              "Container запускает тот же process и использует те же runtime-понятия",
              "Docker заменяет операционную систему полностью",
              "Linux нужен только для оформления README",
            ]}
            correctIndex={0}
            explanation={"Без модели process, path, environment, port и signal container выглядит магией и плохо диагностируется."}
          />

          <QuizCard
            question={"Что должно быть источником production secrets?"}
            options={[
              "Deployment environment или secret storage",
              "Dockerfile layer",
              "Публичный .env.example",
            ]}
            correctIndex={0}
            explanation={"Image остаётся общим artifact, а secrets передаются отдельно при запуске или deployment."}
          />

          <QuizCard
            question={"Зачем image тегировать по commit SHA?"}
            options={[
              "Чтобы связать runtime с точной версией source",
              "Чтобы ускорить каждый SQL-query",
              "Чтобы volume не удалялся",
            ]}
            correctIndex={0}
            explanation={"Traceable tag упрощает расследование, release notes и rollback."}
          />

          <QuizCard
            question={"Что делать при failed smoke test после deployment?"}
            options={[
              "Остановить release и вернуть known-good tag",
              "Удалить все migrations",
              "Скрыть ошибку в healthcheck",
            ]}
            correctIndex={0}
            explanation={"Smoke test является post-deploy gate, а rollback должен быть подготовлен до инцидента."}
          />

        </div>

        <KeyTakeaways
          points={[
            <>{"Linux объясняет среду backend-process до появления контейнера."}</>,
            <>{"Docker image фиксирует runtime, но не production secrets."}</>,
            <>{"Compose соединяет API, PostgreSQL, Redis и migrations в воспроизводимый local stack."}</>,
            <>{"Named volume хранит database data вне lifecycle отдельного container."}</>,
            <>{"Readiness отличается от факта существования живого process."}</>,
            <>{"CI проверяет commit до сборки release artifact."}</>,
            <>{"Image tag должен однозначно указывать на source version."}</>,
            <>{"Smoke test и rollback являются частью release, а не реакцией после аварии."}</>,
          ]}
        />

        <PracticeCta
          text={"Проведите полную репетицию этапа на clean checkout: настройте environment, поднимите Compose stack, проверьте migrations и probes, запустите tests, соберите image с traceable tag, выполните учебный deployment, smoke test и rollback. Все команды и ожидаемые результаты внесите в README, RUNBOOK.md и RELEASE.md."}
        />

      </Section>

    </RichLesson>
  );
}
