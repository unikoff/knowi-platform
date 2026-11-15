import { Route, Trophy } from "lucide-react";
import { BranchExplorer, Callout, CodeBlock, CodeSequence, FlipCards, KeyTakeaways, Lead, MatchPairs, ContentList, LinkedNotes, PracticeCta, QuizCard, RichHero, RichLesson, Section, StepThrough, TerminalDemo, TypeCard, TypeCards } from "../../shared";

export function LearningRoadmap() {
  return (
    <RichLesson>
      <RichHero
        chip={"ЭТАП 9 · карта обучения"}
        title={"План обучения: этап 9"}
        intro={"За 24 занятия Deployable StudyHub превратится в StudyHub LMS Release: ученик спроектирует LMS-домен, реализует teacher/student flow, добавит обоснованный Redis, проведёт финальный аудит и защитит проект как портфельную backend-систему."}
        tags={[
          {
            icon: <Route size={14} />,
            label: "занятия 189–212",
          },
          {
            icon: <Trophy size={14} />,
            label: "StudyHub LMS Release",
          },
        ]}
      />

      <Section number={"01"} title={"От Deployable StudyHub к финальному LMS-релизу"}>
        <Lead>
          {"Deployable StudyHub уже воспроизводимо запускается, проходит CI и имеет понятный release flow. Финальный этап отвечает на вопрос, способен ли проект показать содержательную бизнес-логику, выдержать отрицательные сценарии и быть убедительно защищён как портфельная работа."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"вход этапа"}</>, description: <>{"Deployable StudyHub"}</> },
            { title: <>{"основной проект"}</>, description: <>{"StudyHub LMS Release"}</> },
            { title: <>{"занятия"}</>, description: <>{"189–212"}</> },
            { title: <>{"блоки"}</>, description: <>{"33–36"}</> },
            { title: <>{"центральный flow"}</>, description: <>{"teacher → course → student → enrollment → progress"}</> },
            { title: <>{"операционная задача"}</>, description: <>{"cache, invalidation и controlled background work"}</> },
            { title: <>{"финальное доказательство"}</>, description: <>{"release, demo, tests и защита"}</> },
          ]}
        />

        <CodeBlock
          caption={"эволюция проекта"}
          code={"Deployable StudyHub\n→ LMS requirements and ER model\n→ Course / Module / Lesson\n→ Enrollment and Progress\n→ permissions and vertical slices\n→ Redis cache + invalidation\n→ background operation\n→ final audit + portfolio\n→ StudyHub LMS Release"}
        />

        <CodeSequence
          title={"Соберите порядок финального этапа"}
          prompt={"Расположите шаги так, чтобы инфраструктура и новые технологии не опережали бизнес-требования."}
          pieces={[
            {
              id: "requirements",
              code: "зафиксировать MVP, roles и user stories",
            },
            {
              id: "model",
              code: "спроектировать ER-diagram и constraints",
            },
            {
              id: "vertical",
              code: "реализовать teacher/student vertical flow",
            },
            {
              id: "measure",
              code: "измерить повторное чтение каталога",
            },
            {
              id: "redis",
              code: "добавить cache, TTL и invalidation",
            },
            {
              id: "audit",
              code: "провести security/performance/test audit",
            },
            {
              id: "release",
              code: "собрать demo, release и защиту",
            },
          ]}
          correctOrder={[
            "requirements",
            "model",
            "vertical",
            "measure",
            "redis",
            "audit",
            "release",
          ]}
          explanation={"Redis и оптимизация появляются после рабочего LMS-flow и измеримой проблемы. Портфолио собирается после технического аудита."}
        />

        <TypeCards>
          <TypeCard
            badge={"домен"}
            title={"LMS Core"}
            code={"Course → Module → Lesson"}
          >
            {"Показывает связную предметную модель вместо очередного абстрактного CRUD."}
          </TypeCard>
          <TypeCard
            badge={"сценарий"}
            badgeTone={"float"}
            title={"Vertical slice"}
            code={"request → DB → response"}
          >
            {"Каждый шаг даёт законченную возможность конкретной роли."}
          </TypeCard>
          <TypeCard
            badge={"эксплуатация"}
            badgeTone={"str"}
            title={"Redis boundary"}
            code={"cache ≠ source of truth"}
          >
            {"Ускоряет повторные чтения, но не заменяет PostgreSQL."}
          </TypeCard>
          <TypeCard
            badge={"защита"}
            title={"Portfolio evidence"}
            code={"README + diagrams + demo"}
          >
            {"Делает проект понятным без устного сопровождения автора."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Назвать проблему до технологии"}</h3>
          <p>
            {"Каждый новый слой должен отвечать на наблюдаемую потребность проекта."}
          </p>

          <h3>{"Закрывать пользовательский путь"}</h3>
          <p>
            {"Не оставлять набор ORM-моделей без работающего endpoint и теста."}
          </p>

          <h3>{"Проверять запреты"}</h3>
          <p>
            {"Для permissions важны не только разрешённые, но и запрещённые действия."}
          </p>

          <h3>{"Фиксировать решения"}</h3>
          <p>
            {"Каждый блок обновляет ER-diаграмму, contract table, README или release checklist."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Финальный этап не пытается добавить максимальное число технологий. Его цель — показать зрелое владение уже изученным стеком на содержательном проекте."}
        </Callout>

        <Callout tone={"warn"}>
          {"Публичная демонстрация не компенсирует неясный домен, отсутствующие отрицательные тесты или миграции, которые работают только на базе автора."}
        </Callout>

      </Section>

      <Section number={"02"} title={"Блок 33 · Проектирование StudyHub LMS Core"}>
        <Lead>
          {"Первый блок не начинается с ORM-классов. Ученик сначала определяет роли, пользовательские истории, границу MVP, структуру контента, enrollment, progress, ownership и API contract. Это уменьшает риск переписать проект после первых endpoints."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"MVP"}</>, description: <>{"минимальный законченный продуктовый сценарий"}</> },
            { title: <>{"Course"}</>, description: <>{"верхняя сущность учебного контента"}</> },
            { title: <>{"Enrollment"}</>, description: <>{"связь student и course с собственным состоянием"}</> },
            { title: <>{"Completion"}</>, description: <>{"факт завершения конкретного lesson"}</> },
            { title: <>{"ownership"}</>, description: <>{"право менять конкретный resource"}</> },
            { title: <>{"permissions matrix"}</>, description: <>{"роль × действие × ресурс"}</> },
            { title: <>{"API contract"}</>, description: <>{"внешнее обещание до реализации"}</> },
          ]}
        />

        <CodeBlock
          caption={"минимальная ER-модель"}
          code={"User\n├── owns → Course\n│   └── Module\n│       └── Lesson\n└── Enrollment → Course\n    └── LessonCompletion → Lesson\n\nconstraints:\n- unique enrollment(student_id, course_id)\n- unique completion(enrollment_id, lesson_id)\n- position >= 1"}
        />

        <MatchPairs
          prompt={"Соедините сущность с правилом, которое она должна защищать."}
          leftTitle={"Сущность"}
          rightTitle={"Инвариант"}
          pairs={[
            {
              left: "Course",
              right: "имеет owner-teacher и publication state",
            },
            {
              left: "Module",
              right: "принадлежит одному course и имеет position",
            },
            {
              left: "Lesson",
              right: "принадлежит одному module и имеет position",
            },
            {
              left: "Enrollment",
              right: "один student не записывается на course дважды",
            },
            {
              left: "LessonCompletion",
              right: "один lesson не завершается повторно в том же enrollment",
            },
            {
              left: "Permissions matrix",
              right: "до реализации фиксирует разрешённые и запрещённые действия",
            },
          ]}
          explanation={"Сущность нужна не только для хранения полей: она задаёт устойчивое правило домена."}
        />

        <TypeCards>
          <TypeCard
            badge={"product"}
            title={"User story"}
            code={"teacher publishes course"}
          >
            {"Называет роль, действие и ценность до выбора endpoint."}
          </TypeCard>
          <TypeCard
            badge={"data"}
            badgeTone={"float"}
            title={"ER-diagram"}
            code={"entities + keys + relations"}
          >
            {"Показывает, где хранится факт и какие связи обязаны существовать."}
          </TypeCard>
          <TypeCard
            badge={"access"}
            badgeTone={"str"}
            title={"Permissions matrix"}
            code={"role × action × resource"}
          >
            {"Предотвращает случайные решения о правах внутри каждого endpoint."}
          </TypeCard>
          <TypeCard
            badge={"delivery"}
            title={"Migration plan"}
            code={"revision 1 → revision 2"}
          >
            {"Разбивает изменение schema на проверяемые шаги."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Сначала написать user stories"}</h3>
          <p>
            {"Сократить пожелания до одного обязательного teacher/student flow."}
          </p>

          <h3>{"Нарисовать связи"}</h3>
          <p>
            {"Проверить cardinality и место foreign keys без кода."}
          </p>

          <h3>{"Назвать constraints"}</h3>
          <p>
            {"Определить уникальность и допустимые состояния."}
          </p>

          <h3>{"Составить contract table"}</h3>
          <p>
            {"Для каждого endpoint записать role, input, output и expected errors."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Проектирование считается завершённым, когда есть решения, которые можно проверить: таблицы, ограничения, endpoints, permissions и sequence migrations."}
        </Callout>

        <Callout tone={"warn"}>
          {"Статус progress не стоит хранить произвольным процентом, если его можно надёжно вычислить из фактов завершения lesson."}
        </Callout>

      </Section>

      <Section number={"03"} title={"Блок 34 · Курсы, зачисление и прогресс"}>
        <Lead>
          {"После утверждённого contract реализация идёт вертикальными slices. Сначала teacher создаёт и структурирует course, затем публикует его. После этого student видит каталог, создаёт enrollment, завершает lesson и получает progress."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"vertical slice"}</>, description: <>{"endpoint, service, query, response и test одного сценария"}</> },
            { title: <>{"draft"}</>, description: <>{"course ещё не доступен обычному student"}</> },
            { title: <>{"published"}</>, description: <>{"course прошёл правила готовности и виден в каталоге"}</> },
            { title: <>{"idempotency"}</>, description: <>{"повтор действия не создаёт нежелательный duplicate"}</> },
            { title: <>{"completion"}</>, description: <>{"отдельный факт завершения lesson"}</> },
            { title: <>{"progress"}</>, description: <>{"completed published lessons / all published lessons"}</> },
            { title: <>{"object permission"}</>, description: <>{"проверка доступа к конкретному course"}</> },
          ]}
        />

        <CodeBlock
          caption={"основной LMS-flow"}
          code={"teacher POST /courses\n→ POST /courses/{course_id}/modules\n→ POST /modules/{module_id}/lessons\n→ POST /courses/{course_id}/publish\n\nstudent GET /catalog\n→ POST /courses/{course_id}/enrollments\n→ POST /lessons/{lesson_id}/complete\n→ GET /me/courses/{course_id}/progress"}
        />

        <BranchExplorer
          code={"create course\nadd modules\nadd lessons\npublish\nenroll\ncomplete lesson\ncalculate progress"}
          scenarios={[
            {
              label: "teacher owns draft",
              activeLine: 1,
              output: "может менять структуру course",
            },
            {
              label: "teacher публикует пустой course",
              activeLine: 3,
              output: "409: course не готов к публикации",
            },
            {
              label: "student enrolls twice",
              activeLine: 4,
              output: "возвращается существующий enrollment или согласованный 409",
            },
            {
              label: "student completes foreign lesson",
              activeLine: 5,
              output: "403/404 по принятому security contract",
            },
            {
              label: "valid flow",
              activeLine: 6,
              output: "progress вычисляется из completion rows",
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"teacher"}
            title={"Content management"}
            code={"owner_id == current_user.id"}
          >
            {"Teacher изменяет только собственный course."}
          </TypeCard>
          <TypeCard
            badge={"student"}
            badgeTone={"float"}
            title={"Enrollment"}
            code={"unique(student_id, course_id)"}
          >
            {"Связь создаётся один раз и открывает доступ к прохождению."}
          </TypeCard>
          <TypeCard
            badge={"state"}
            badgeTone={"str"}
            title={"Publication"}
            code={"draft → published"}
          >
            {"Переход выполняется только после проверки готовности структуры."}
          </TypeCard>
          <TypeCard
            badge={"metric"}
            title={"Progress"}
            code={"completed / total * 100"}
          >
            {"Вычисляется из фактов, а не редактируется клиентом."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Реализовать course slice"}</h3>
          <p>
            {"Модель, migration, schema, endpoint, service и tests."}
          </p>

          <h3>{"Добавить structure slice"}</h3>
          <p>
            {"Проверить positions, ownership и ordering."}
          </p>

          <h3>{"Закрыть student flow"}</h3>
          <p>
            {"Enrollment, completion и progress как связанный сценарий."}
          </p>

          <h3>{"Проверить отрицательные пути"}</h3>
          <p>
            {"Чужой course, draft catalog, duplicate enrollment и foreign lesson."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Вертикальный slice уменьшает незавершённость: после каждого шага существует работающая возможность, которую можно показать и протестировать."}
        </Callout>

        <Callout tone={"warn"}>
          {"Нельзя доверять student_id, owner_id или progress из request body. Identity берётся из authentication context, а вычисляемые значения формирует сервер."}
        </Callout>

      </Section>

      <Section number={"04"} title={"Блок 35 · Redis, кеш и фоновые операции"}>
        <Lead>
          {"Redis появляется только после рабочего LMS-flow. Ученик измеряет повторный каталог, формулирует cache key и TTL, сохраняет PostgreSQL как source of truth, реализует invalidation после изменения course и добавляет один небольшой rate limit и одну безопасную background operation."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"source of truth"}</>, description: <>{"авторитетное состояние в PostgreSQL"}</> },
            { title: <>{"cache hit"}</>, description: <>{"ответ найден в Redis"}</> },
            { title: <>{"cache miss"}</>, description: <>{"данные читаются из PostgreSQL и помещаются в cache"}</> },
            { title: <>{"TTL"}</>, description: <>{"автоматический срок жизни временного значения"}</> },
            { title: <>{"invalidation"}</>, description: <>{"удаление устаревшего cache после write"}</> },
            { title: <>{"rate limit"}</>, description: <>{"контролируемое число запросов за окно"}</> },
            { title: <>{"background task"}</>, description: <>{"небольшая работа после отправки response"}</> },
          ]}
        />

        <CodeBlock
          caption={"cache-aside flow"}
          code={"GET /catalog\n→ redis.get(\"catalog:v1\")\n├── hit  → deserialize → response\n└── miss → SELECT published courses\n         → serialize\n         → redis.setex(key, ttl, value)\n         → response\n\nUPDATE course\n→ PostgreSQL commit\n→ redis.delete(\"catalog:v1\")"}
        />

        <StepThrough
          code={"cached = redis.get(key)\nif cached:\n    return decode(cached)\ndata = load_catalog(db)\nredis.setex(key, ttl, encode(data))\nreturn data"}
          steps={[
            {
              line: 0,
              note: "Сначала проверяется временная копия.",
              vars: {
                "key": "catalog:v1",
              },
            },
            {
              line: 1,
              note: "При cache hit database query не выполняется.",
              vars: {
                "source": "Redis",
              },
            },
            {
              line: 3,
              note: "При miss авторитетные данные читаются из PostgreSQL.",
              vars: {
                "source": "PostgreSQL",
              },
            },
            {
              line: 4,
              note: "Сериализованная копия получает TTL.",
              vars: {
                "ttl": "60 seconds",
              },
            },
            {
              line: 5,
              note: "Клиент получает одинаковый response contract.",
              vars: {
                "contract": "stable",
              },
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"truth"}
            title={"PostgreSQL"}
            code={"authoritative state"}
          >
            {"Хранит courses, enrollments и completions независимо от cache."}
          </TypeCard>
          <TypeCard
            badge={"speed"}
            badgeTone={"float"}
            title={"Redis cache"}
            code={"key + value + TTL"}
          >
            {"Сокращает повторные чтения согласованного представления."}
          </TypeCard>
          <TypeCard
            badge={"freshness"}
            badgeTone={"str"}
            title={"Invalidation"}
            code={"write → delete cache key"}
          >
            {"Не позволяет долго отдавать устаревший catalog."}
          </TypeCard>
          <TypeCard
            badge={"delay"}
            title={"BackgroundTasks"}
            code={"response → non-critical work"}
          >
            {"Подходит для короткой необязательной операции, а не для гарантированной очереди."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Измерить baseline"}</h3>
          <p>
            {"Зафиксировать query count и latency до cache."}
          </p>

          <h3>{"Сделать hit/miss test"}</h3>
          <p>
            {"Проверить одинаковый response и различный источник данных."}
          </p>

          <h3>{"Сломать Redis"}</h3>
          <p>
            {"Убедиться, что catalog может деградировать к PostgreSQL по принятому contract."}
          </p>

          <h3>{"Проверить stale data"}</h3>
          <p>
            {"Изменить course и доказать, что invalidation обновляет следующий response."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Кеш является копией представления. Он полезен только при понятном key, TTL, invalidation и измеримом эффекте."}
        </Callout>

        <Callout tone={"warn"}>
          {"BackgroundTasks не гарантирует выполнение после аварийного завершения process. Критические платежи, migrations и обязательные письма требуют более устойчивой очереди."}
        </Callout>

      </Section>

      <Section number={"05"} title={"Блок 36 · Финальное качество, портфолио и интервью"}>
        <Lead>
          {"Последний блок не добавляет новый framework. Ученик превращает технически работающий проект в проверяемый release: выравнивает error contract, проводит security и query audit, усиливает tests, восстанавливает database с нуля, оформляет README и diagrams, репетирует demo и защищает решения в формате интервью."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"API contract"}</>, description: <>{"одинаковая форма success и error responses"}</> },
            { title: <>{"security checklist"}</>, description: <>{"контроль identities, permissions, secrets и input"}</> },
            { title: <>{"query audit"}</>, description: <>{"N+1, indexes, counts и cache behavior"}</> },
            { title: <>{"release test"}</>, description: <>{"чистая database, migrations, seed и integration flow"}</> },
            { title: <>{"portfolio README"}</>, description: <>{"проблема, архитектура, запуск, demo и limitations"}</> },
            { title: <>{"mock interview"}</>, description: <>{"объяснение и изменение кода под вопросами"}</> },
            { title: <>{"project defense"}</>, description: <>{"короткий доказательный рассказ с live-сценарием"}</> },
          ]}
        />

        <CodeBlock
          caption={"release evidence chain"}
          code={"git clone\n→ copy .env.example\n→ docker compose up --build\n→ alembic upgrade head\n→ seed demo data\n→ pytest\n→ open API docs\n→ teacher creates and publishes course\n→ student enrolls and completes lesson\n→ progress updated\n→ inspect logs and cache metrics\n→ show release tag"}
        />

        <FlipCards
          cards={[
            {
              front: <>{"README"}</>,
              back: <>{"доказывает воспроизводимый запуск и объясняет ценность проекта"}</>,
            },
            {
              front: <>{"ER-diagram"}</>,
              back: <>{"показывает данные, keys, cardinality и association entities"}</>,
            },
            {
              front: <>{"CI run"}</>,
              back: <>{"доказывает независимую проверку commit"}</>,
            },
            {
              front: <>{"test suite"}</>,
              back: <>{"защищает success, failure и security scenarios"}</>,
            },
            {
              front: <>{"release tag"}</>,
              back: <>{"фиксирует конкретную демонстрируемую версию"}</>,
            },
            {
              front: <>{"demo script"}</>,
              back: <>{"показывает главный flow без случайной импровизации"}</>,
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"quality"}
            title={"Audit"}
            code={"evidence before opinion"}
          >
            {"Каждое замечание связано с тестом, query plan, log или воспроизводимым сценарием."}
          </TypeCard>
          <TypeCard
            badge={"story"}
            badgeTone={"float"}
            title={"Portfolio narrative"}
            code={"problem → decision → result"}
          >
            {"Объясняет эволюцию StudyHub, а не перечисляет библиотеки."}
          </TypeCard>
          <TypeCard
            badge={"interview"}
            badgeTone={"str"}
            title={"Technical defense"}
            code={"explain + debug + change"}
          >
            {"Проверяет способность работать с кодом под уточняющими вопросами."}
          </TypeCard>
          <TypeCard
            badge={"release"}
            title={"Known version"}
            code={"git tag + image tag"}
          >
            {"Связывает документацию, demo и running artifact с одной версией."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Провести аудит по checklist"}</h3>
          <p>
            {"Не исправлять всё сразу: создать приоритетный список рисков."}
          </p>

          <h3>{"Восстановить чистый environment"}</h3>
          <p>
            {"Проверить README, migrations, seed и tests с нуля."}
          </p>

          <h3>{"Записать трёхминутный demo"}</h3>
          <p>
            {"Убрать лишние действия и оставить доказательства основного flow."}
          </p>

          <h3>{"Пройти mock interview"}</h3>
          <p>
            {"Объяснить architecture, исправить defect, написать SQL и тест."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Портфолио становится сильным, когда видно инженерное мышление: проблема, варианты, принятое решение, компромисс и проверка."}
        </Callout>

        <Callout tone={"warn"}>
          {"Фраза «всё готово» без clean bootstrap, отрицательных тестов и согласованного release artifact не является техническим доказательством."}
        </Callout>

      </Section>

      <Section number={"06"} title={"Артефакты этапа и состояние репозитория"}>
        <Lead>
          {"Финальный этап должен оставить не только новые endpoints. В репозитории появляются product brief, ER-diagram, permissions matrix, contract table, Redis policy, test evidence, demo seed, architecture overview и release checklist."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"PRODUCT.md"}</>, description: <>{"MVP, roles и user stories"}</> },
            { title: <>{"ERD.md"}</>, description: <>{"entities, keys, relations и constraints"}</> },
            { title: <>{"PERMISSIONS.md"}</>, description: <>{"role/object access contract"}</> },
            { title: <>{"CACHE.md"}</>, description: <>{"keys, TTL, invalidation и fallback"}</> },
            { title: <>{"ARCHITECTURE.md"}</>, description: <>{"request flow и boundaries"}</> },
            { title: <>{"DEMO.md"}</>, description: <>{"воспроизводимый сценарий показа"}</> },
            { title: <>{"RELEASE.md"}</>, description: <>{"known version, checks и limitations"}</> },
          ]}
        />

        <CodeBlock
          caption={"дерево финального результата"}
          code={"studyhub/\n├── app/\n│   ├── api/\n│   ├── models/\n│   ├── schemas/\n│   ├── services/\n│   └── core/\n├── tests/\n├── alembic/\n├── docs/\n│   ├── PRODUCT.md\n│   ├── ERD.md\n│   ├── PERMISSIONS.md\n│   ├── CACHE.md\n│   └── ARCHITECTURE.md\n├── scripts/seed_demo.py\n├── compose.yaml\n├── README.md\n├── DEMO.md\n└── RELEASE.md"}
        />

        <QuizCard
          question={"Какой репозиторий легче оценить работодателю или наставнику?"}
          options={[{ title: "Код без контекста", code: "app/ + many endpoints", description: "Непонятны домен, запуск, права, ограничения и доказательства качества." }, { title: "Проверяемый release", code: "code + tests + diagrams + demo + tag", description: "Можно воспроизвести запуск, увидеть решения и проверить главный flow." }]}
          
          correctIndex={1}
          explanation={"Документация не украшает проект, а снижает стоимость его понимания и проверки."}
        />

        <TypeCards>
          <TypeCard
            badge={"product"}
            title={"Product evidence"}
            code={"stories + MVP"}
          >
            {"Объясняет, какую проблему решает LMS и для кого."}
          </TypeCard>
          <TypeCard
            badge={"architecture"}
            badgeTone={"float"}
            title={"Technical evidence"}
            code={"ERD + request flow"}
          >
            {"Показывает границы и направление данных."}
          </TypeCard>
          <TypeCard
            badge={"quality"}
            badgeTone={"str"}
            title={"Verification evidence"}
            code={"tests + CI + audit"}
          >
            {"Доказывает, что важные договорённости воспроизводимы."}
          </TypeCard>
          <TypeCard
            badge={"delivery"}
            title={"Release evidence"}
            code={"tag + demo + limitations"}
          >
            {"Фиксирует конкретную версию и честные границы."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Проверить каждую ссылку"}</h3>
          <p>
            {"README и docs не должны вести к устаревшим paths или командам."}
          </p>

          <h3>{"Сопоставить diagram с migrations"}</h3>
          <p>
            {"Название таблиц и relations совпадает с реальной schema."}
          </p>

          <h3>{"Сопоставить demo с tests"}</h3>
          <p>
            {"Главный demo-flow защищён integration test."}
          </p>

          <h3>{"Сопоставить release с Git"}</h3>
          <p>
            {"Tag, image и release notes относятся к одному commit."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Сильный artifact отвечает на конкретный вопрос: что строим, как устроено, как запустить, как проверить и где ограничения."}
        </Callout>

        <Callout tone={"warn"}>
          {"Автоматически сгенерированная документация не заменяет объяснение предметной модели и принятых инженерных решений."}
        </Callout>

      </Section>

      <Section number={"07"} title={"Ритм обучения и критерии перехода"}>
        <Lead>
          {"Финальный этап сочетает проектирование, реализацию, эксплуатацию и защиту. Чтобы не потерять качество, ученик проходит каждый блок по циклу: contract, implementation, negative tests, integration, documentation и review."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"понимание"}</>, description: <>{"объяснить модель без чтения конспекта"}</> },
            { title: <>{"реализация"}</>, description: <>{"запустить happy path на clean data"}</> },
            { title: <>{"диагностика"}</>, description: <>{"объяснить один failure path"}</> },
            { title: <>{"security"}</>, description: <>{"проверить forbidden access"}</> },
            { title: <>{"tests"}</>, description: <>{"автоматизировать важную договорённость"}</> },
            { title: <>{"documentation"}</>, description: <>{"обновить diagram или contract"}</> },
            { title: <>{"Git"}</>, description: <>{"сделать узкий осмысленный commit"}</> },
          ]}
        />

        <CodeBlock
          caption={"цикл одного проектного изменения"}
          code={"requirement\n→ contract\n→ model/migration\n→ endpoint/service\n→ happy-path test\n→ forbidden/error test\n→ docs\n→ review\n→ commit\n→ short defense"}
        />

        <QuizCard
          question={
            <>
              {"На финальном этапе можно пропускать отрицательные тесты, потому что проект уже большой и happy path работает."}
            </>
          }
          options={["Правда", "Ложь"]} correctIndex={1}
          explanation={"Чем больше ролей и связей, тем важнее проверки чужого resource, duplicate actions, stale cache и migration failures."}
        />

        <TypeCards>
          <TypeCard
            badge={"model"}
            title={"Понять"}
            code={"explain contract"}
          >
            {"Назвать вход, правило, результат и границу."}
          </TypeCard>
          <TypeCard
            badge={"build"}
            badgeTone={"float"}
            title={"Реализовать"}
            code={"vertical slice"}
          >
            {"Довести один пользовательский сценарий до рабочего response."}
          </TypeCard>
          <TypeCard
            badge={"break"}
            badgeTone={"str"}
            title={"Нарушить"}
            code={"forbidden / duplicate / stale"}
          >
            {"Найти ожидаемую реакцию системы до случайного production-сбоя."}
          </TypeCard>
          <TypeCard
            badge={"prove"}
            title={"Доказать"}
            code={"test + docs + demo"}
          >
            {"Оставить воспроизводимое подтверждение результата."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Перед coding назвать Definition of Done"}</h3>
          <p>
            {"Указать endpoint, migration, tests и docs."}
          </p>

          <h3>{"После coding провести self-review"}</h3>
          <p>
            {"Удалить debug output, проверить naming и boundaries."}
          </p>

          <h3>{"Перед merge запустить clean scenario"}</h3>
          <p>
            {"Не полагаться на состояние старой локальной database."}
          </p>

          <h3>{"После merge обновить demo state"}</h3>
          <p>
            {"Проверить, что seed и документация соответствуют новой версии."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Последний этап учит завершать работу. Незакрытый edge case или устаревшая migration важнее ещё одного дополнительного endpoint."}
        </Callout>

        <Callout tone={"warn"}>
          {"Большой финальный commit затрудняет review и скрывает источник дефекта. Продолжайте делать небольшие связанные изменения."}
        </Callout>

      </Section>

      <Section number={"08"} title={"Контрольная точка и финальная защита курса"}>
        <Lead>
          {"Этап завершён не тогда, когда написана последняя строка, а когда ученик способен воспроизвести release, показать главный LMS-flow, объяснить данные и права, диагностировать ошибку и защитить компромиссы проекта."}
        </Lead>


        <LinkedNotes
          items={[
            { title: <>{"clean bootstrap"}</>, description: <>{"clone → config → migrations → seed → start"}</> },
            { title: <>{"business demo"}</>, description: <>{"teacher/student flow работает полностью"}</> },
            { title: <>{"security proof"}</>, description: <>{"чужой resource защищён"}</> },
            { title: <>{"cache proof"}</>, description: <>{"hit, miss и invalidation наблюдаемы"}</> },
            { title: <>{"quality proof"}</>, description: <>{"tests и CI относятся к release commit"}</> },
            { title: <>{"architecture defense"}</>, description: <>{"решения объясняются через требования"}</> },
            { title: <>{"honest limits"}</>, description: <>{"названы темы, намеренно не реализованные в MVP"}</> },
          ]}
        />

        <CodeBlock
          caption={"финальный маршрут защиты"}
          code={"1. problem and users\n2. architecture and ERD\n3. clean start\n4. teacher flow\n5. student flow\n6. forbidden scenario\n7. cache and logs\n8. tests and CI\n9. trade-offs and limitations\n10. next steps"}
        />

        <TerminalDemo
          title={"финальная воспроизводимая проверка"}
          lines={[
            {
              cmd: "docker compose up --build -d",
            },
            {
              out: "api, postgres and redis are healthy",
            },
            {
              cmd: "docker compose run --rm migrate alembic upgrade head",
            },
            {
              out: "migration completed",
            },
            {
              cmd: "docker compose exec api python scripts/seed_demo.py",
            },
            {
              out: "teacher, student and demo course created",
            },
            {
              cmd: "docker compose exec api pytest",
            },
            {
              out: "all tests passed",
            },
            {
              cmd: "curl http://localhost:8000/health",
            },
            {
              out: "{\"status\":\"ok\"}",
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"1"}
            title={"Reproducibility"}
            code={"clean bootstrap"}
          >
            {"Проект не зависит от скрытого состояния компьютера автора."}
          </TypeCard>
          <TypeCard
            badge={"2"}
            badgeTone={"float"}
            title={"Business completeness"}
            code={"teacher + student flow"}
          >
            {"Показана связная предметная логика, а не набор endpoints."}
          </TypeCard>
          <TypeCard
            badge={"3"}
            badgeTone={"str"}
            title={"Engineering quality"}
            code={"tests + CI + audit"}
          >
            {"Ключевые договорённости имеют автоматические доказательства."}
          </TypeCard>
          <TypeCard
            badge={"4"}
            title={"Communication"}
            code={"defense + limitations"}
          >
            {"Автор способен объяснить решение и честно назвать границы."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Подготовить 10-минутный сценарий"}</h3>
          <p>
            {"Убрать случайные действия и показать только load-bearing evidence."}
          </p>

          <h3>{"Провести техническую репетицию"}</h3>
          <p>
            {"Заранее проверить ports, seed, accounts и cache state."}
          </p>

          <h3>{"Подготовить вопросы к себе"}</h3>
          <p>
            {"Почему association model, почему computed progress, почему Redis, почему не microservices."}
          </p>

          <h3>{"После защиты записать backlog"}</h3>
          <p>
            {"Отделить дефекты от необязательных future improvements."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Хорошая защита следует пути данных и пользователя. Она не превращается в чтение списка технологий."}
        </Callout>

        <Callout tone={"warn"}>
          {"Перед финальной демонстрацией нельзя добавлять крупную новую функцию. Последние изменения должны снижать риск, а не расширять scope."}
        </Callout>

        <div className="lesson-check-group">
          <QuizCard
            question={"Почему Redis появляется после рабочего LMS-flow?"}
            options={[
              "Сначала нужна измеримая проблема и source of truth",
              "Redis заменяет ER-diagram",
              "Без Redis невозможно создать Course",
            ]}
            correctIndex={0}
            explanation={"Кеш отвечает на проблему повторного чтения, а не определяет предметную модель."}
          />

          <QuizCard
            question={"Что означает vertical slice?"}
            options={[
              "Законченный пользовательский сценарий через все нужные слои",
              "Только новая ORM-модель",
              "Отдельный frontend-компонент",
            ]}
            correctIndex={0}
            explanation={"Slice включает contract, storage, business rule, response и tests конкретной возможности."}
          />

          <QuizCard
            question={"Какой progress надёжнее?"}
            options={[
              "Вычисляемый из completion facts",
              "Произвольное число из request body",
              "Значение в Redis без PostgreSQL",
            ]}
            correctIndex={0}
            explanation={"Факты прохождения дают воспроизводимый источник вычисления."}
          />

          <QuizCard
            question={"Что считается финальным доказательством готовности?"}
            options={[
              "Clean bootstrap, demo flow, tests, CI и защита решений",
              "Большое число endpoints",
              "Только красивый README",
            ]}
            correctIndex={0}
            explanation={"Финальный release должен быть воспроизводимым, проверяемым и объяснимым."}
          />

        </div>

        <KeyTakeaways
          points={[
            <>{"Этап 9 начинается с требований и доменной модели, а не с новой библиотеки."}</>,
            <>{"Course, Module, Lesson, Enrollment и Completion защищают разные факты."}</>,
            <>{"Permissions проектируются до реализации endpoints."}</>,
            <>{"Вертикальные slices дают законченные пользовательские сценарии."}</>,
            <>{"PostgreSQL остаётся source of truth, Redis хранит временные копии и служебное состояние."}</>,
            <>{"Cache требует key, TTL, invalidation и измерения."}</>,
            <>{"Финальный аудит проверяет API, security, queries, tests, migrations и documentation."}</>,
            <>{"Портфолио ценится за воспроизводимость и объяснённые решения, а не за список технологий."}</>,
          ]}
        />

        <PracticeCta
          text={"Создайте release-plan этапа 9: перечислите 24 занятия, четыре блоковых артефакта, основной teacher/student flow, обязательные forbidden scenarios, cache experiment, clean-bootstrap checklist, demo script и 12 вопросов для финальной защиты. Для каждого пункта укажите наблюдаемое доказательство готовности."}
        />

      </Section>

    </RichLesson>
  );
}
