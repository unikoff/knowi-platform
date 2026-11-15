import { Route, Zap } from "lucide-react";
import { BranchExplorer, Callout, CodeBlock, CodeSequence, FlipCards, KeyTakeaways, Lead, MatchPairs, ContentList, LinkedNotes, PracticeCta, QuizCard, RichHero, RichLesson, Section, TypeCard, TypeCards } from "../../shared";

export function LearningRoadmap() {
  return (
    <RichLesson>
      <RichHero
        chip={"ЭТАП 7 · карта обучения"}
        title={"План обучения: этап 7"}
        intro={"За 24 занятия PostgreSQL StudyHub превратится в Async StudyHub: ученик разберёт event loop без фреймворка, научится управлять Task и timeout, подключит AsyncClient, переведёт database layer на AsyncSession и подтвердит результат измерениями."}
        tags={[
          {
            icon: <Route size={14} />,
            label: "занятия 141–164",
          },
          {
            icon: <Zap size={14} />,
            label: "Async StudyHub",
          },
        ]}
      />

      <Section number={"01"} title={"От PostgreSQL StudyHub к Async StudyHub"}>
        <Lead>
          {"Этап 6 завершился серверной PostgreSQL-базой, понятным SQL, транзакциями, индексами и измерениями. Этап 7 не переписывает проект ради нового синтаксиса: он решает конкретную проблему ожидания I/O и последовательно переводит внешние HTTP-вызовы и database layer на асинхронную модель."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"вход этапа"}</>, description: <>{"PostgreSQL StudyHub на синхронном FastAPI и Session"}</> },
            { title: <>{"основной проект"}</>, description: <>{"Async StudyHub"}</> },
            { title: <>{"занятия"}</>, description: <>{"141–164"}</> },
            { title: <>{"блоки"}</>, description: <>{"25–28"}</> },
            { title: <>{"ключевая проблема"}</>, description: <>{"несколько независимых I/O-ожиданий"}</> },
            { title: <>{"доказательство"}</>, description: <>{"tests, timing, request trace и сравнимый benchmark"}</> },
            { title: <>{"граница"}</>, description: <>{"async не является ускорителем CPU-bound вычислений"}</> },
          ]}
        />

        <CodeBlock
          caption={"эволюция проекта"}
          code={"PostgreSQL StudyHub\n→ blocking и waiting различены\n→ coroutine + event loop\n→ controlled concurrency\n→ async HTTP integration\n→ AsyncSession\n→ measurement and observability\n→ Async StudyHub"}
        />

        <CodeSequence
          title={"Соберите безопасный маршрут этапа"}
          prompt={"Расположите шаги так, чтобы механизм был понятен до изменения основного проекта."}
          pieces={[
            {
              id: "baseline",
              code: "зафиксировать sync tests и timing",
            },
            {
              id: "model",
              code: "различить CPU и I/O",
            },
            {
              id: "coroutine",
              code: "запустить coroutine через event loop",
            },
            {
              id: "control",
              code: "добавить Task, timeout и semaphore",
            },
            {
              id: "http",
              code: "подключить AsyncClient",
            },
            {
              id: "db",
              code: "перевести один endpoint на AsyncSession",
            },
            {
              id: "measure",
              code: "измерить и объяснить результат",
            },
          ]}
          correctOrder={[
            "baseline",
            "model",
            "coroutine",
            "control",
            "http",
            "db",
            "measure",
          ]}
          explanation={"Async database layer появляется только после того, как ученик понимает event loop и управляет жизненным циклом задач."}
        />

        <TypeCards>
          <TypeCard
            badge={"до"}
            title={"Sync StudyHub"}
            code={"def + Session"}
          >
            {"Рабочий и проверенный проект остаётся эталоном поведения."}
          </TypeCard>
          <TypeCard
            badge={"механизм"}
            badgeTone={"float"}
            title={"asyncio lab"}
            code={"coroutine → await → Task"}
          >
            {"Небольшая лаборатория показывает переключение без шума фреймворка."}
          </TypeCard>
          <TypeCard
            badge={"сеть"}
            badgeTone={"str"}
            title={"AsyncClient"}
            code={"await client.get(...)"}
          >
            {"Первое реальное неблокирующее сетевое ожидание."}
          </TypeCard>
          <TypeCard
            badge={"после"}
            title={"Async StudyHub"}
            code={"async def + AsyncSession"}
          >
            {"Асинхронный стек внедрён там, где действительно есть I/O."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Не переписывать всё сразу"}</h3>
          <p>
            {"Сначала сохранить sync-реализацию и перевести один изолированный vertical slice."}
          </p>

          <h3>{"Сначала предсказать timeline"}</h3>
          <p>
            {"До запуска отметить, какая coroutine выполняется, ждёт или готова продолжиться."}
          </p>

          <h3>{"Проверять сбой"}</h3>
          <p>
            {"Timeout, cancellation и database error являются обязательными сценариями."}
          </p>

          <h3>{"Измерять конкретный flow"}</h3>
          <p>
            {"Вывод об async делается по одному воспроизводимому I/O-сценарию, а не по ощущению."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Этап не требует превращать каждую функцию в async. Обычные чистые вычисления и часть endpoint могут оставаться синхронными."}
        </Callout>

        <Callout tone={"warn"}>
          {"Слово async в сигнатуре не создаёт конкурентность. Последовательные await могут выполняться один за другим."}
        </Callout>

      </Section>

      <Section number={"02"} title={"Блок 25 · Coroutine, event loop и async/await"}>
        <Lead>
          {"Первый блок строит модель асинхронного выполнения вне FastAPI. Ученик видит blocking call, создаёт coroutine object, запускает event loop, использует первый await и диагностирует две базовые ошибки: забытый await и блокировку loop."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"141"}</>, description: <>{"blocking execution, CPU-bound и I/O-bound"}</> },
            { title: <>{"142"}</>, description: <>{"coroutine function и coroutine object"}</> },
            { title: <>{"143"}</>, description: <>{"asyncio.run, event loop и await"}</> },
            { title: <>{"144"}</>, description: <>{"sequential await и timing"}</> },
            { title: <>{"145"}</>, description: <>{"forgotten await и blocked loop"}</> },
            { title: <>{"146"}</>, description: <>{"трассируемый async-loader"}</> },
            { title: <>{"артефакт"}</>, description: <>{"async-lab с README и timeline"}</> },
          ]}
        />

        <CodeBlock
          caption={"основная модель блока"}
          code={"async def fetch_data():\n    await io_operation()\n\ncoroutine = fetch_data()\nasyncio.run(main())\n\nevent loop: run → wait → resume → done"}
        />

        <MatchPairs
          prompt={"Соедините термин и наблюдаемое поведение."}
          leftTitle={"Термин"}
          rightTitle={"Что происходит"}
          pairs={[
            {
              left: "coroutine function",
              right: "объявлена через async def",
            },
            {
              left: "coroutine object",
              right: "создан вызовом, но ещё не выполнен",
            },
            {
              left: "event loop",
              right: "запускает и возобновляет готовые coroutine",
            },
            {
              left: "await",
              right: "приостанавливает текущую coroutine до результата",
            },
            {
              left: "blocking call",
              right: "не отдаёт управление event loop",
            },
            {
              left: "asyncio.run",
              right: "создаёт loop для entry coroutine и закрывает его",
            },
          ]}
          explanation={"Точный словарь терминов защищает от фразы «async просто работает быстрее»."}
        />

        <TypeCards>
          <TypeCard
            badge={"work"}
            title={"CPU-bound"}
            code={"calculate()"}
          >
            {"Процесс занят вычислением и не ускоряется от await сам по себе."}
          </TypeCard>
          <TypeCard
            badge={"wait"}
            badgeTone={"float"}
            title={"I/O-bound"}
            code={"await network_read()"}
          >
            {"Coroutine может отдать управление, пока внешний ресурс готовит ответ."}
          </TypeCard>
          <TypeCard
            badge={"object"}
            badgeTone={"str"}
            title={"Coroutine"}
            code={"job = fetch()"}
          >
            {"Описывает будущую работу и должна быть awaited или запущена loop."}
          </TypeCard>
          <TypeCard
            badge={"executor"}
            title={"Event loop"}
            code={"asyncio.run(main())"}
          >
            {"Управляет готовностью coroutine в одном потоке выполнения."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Нарисовать timeline"}</h3>
          <p>
            {"Отметить участки Python-работы и участки ожидания внешнего ресурса."}
          </p>

          <h3>{"Создать coroutine object"}</h3>
          <p>
            {"Вызвать async-функцию без await и изучить repr и warning."}
          </p>

          <h3>{"Запустить через main"}</h3>
          <p>
            {"Использовать asyncio.run ровно в точке входа лаборатории."}
          </p>

          <h3>{"Сломать и исправить"}</h3>
          <p>
            {"Заменить asyncio.sleep на time.sleep и наблюдать остановку loop."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Event loop не является отдельным сервером и не выполняет две строки Python буквально одновременно. Он переключается в точках, где coroutine отдаёт управление."}
        </Callout>

        <Callout tone={"warn"}>
          {"Coroutine object вместо ожидаемых данных — признак того, что вызов не был awaited или запланирован как Task."}
        </Callout>

      </Section>

      <Section number={"03"} title={"Блок 26 · Task, timeout, cancellation и semaphore"}>
        <Lead>
          {"После последовательного await появляется управляемая конкурентность. Ученик создаёт Task, собирает результаты через gather, ограничивает время ожидания, корректно обрабатывает cancellation и не запускает неограниченное количество операций."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"Task"}</>, description: <>{"coroutine, запланированная в event loop"}</> },
            { title: <>{"gather"}</>, description: <>{"ожидание группы результатов"}</> },
            { title: <>{"timeout"}</>, description: <>{"максимальное допустимое время ожидания"}</> },
            { title: <>{"cancellation"}</>, description: <>{"управляемое прекращение незавершённой работы"}</> },
            { title: <>{"finally"}</>, description: <>{"обязательная очистка"}</> },
            { title: <>{"semaphore"}</>, description: <>{"ограничение concurrent operations"}</> },
            { title: <>{"partial result"}</>, description: <>{"явный status каждой операции"}</> },
          ]}
        />

        <CodeBlock
          caption={"контролируемая concurrency"}
          code={"inputs\n→ create Task for each input\n→ semaphore allows N active operations\n→ timeout bounds each wait\n→ cancellation triggers cleanup\n→ gather structured results"}
        />

        <BranchExplorer
          code={"Task created\nTask waiting for I/O\nTask completed\nTask timed out\nTask cancelled\nfinally cleanup"}
          scenarios={[
            {
              label: "успешный ответ",
              activeLine: 2,
              output: "result status=success",
            },
            {
              label: "слишком медленно",
              activeLine: 3,
              output: "timeout status, ожидание прекращено",
            },
            {
              label: "внешняя отмена",
              activeLine: 4,
              output: "CancelledError проходит после cleanup",
            },
            {
              label: "освобождение ресурса",
              activeLine: 5,
              output: "finally выполняется для error и cancellation",
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"schedule"}
            title={"Task"}
            code={"asyncio.create_task(coro())"}
          >
            {"Позволяет loop начать coroutine до точки её окончательного await."}
          </TypeCard>
          <TypeCard
            badge={"collect"}
            badgeTone={"float"}
            title={"Gather"}
            code={"await asyncio.gather(...)"}
          >
            {"Собирает результаты в порядке входных awaitables."}
          </TypeCard>
          <TypeCard
            badge={"limit"}
            badgeTone={"str"}
            title={"Timeout"}
            code={"await wait_for(job, 2)"}
          >
            {"Не позволяет одному ожиданию удерживать flow бесконечно."}
          </TypeCard>
          <TypeCard
            badge={"capacity"}
            title={"Semaphore"}
            code={"async with semaphore"}
          >
            {"Защищает внешний сервис и собственные ресурсы от всплеска concurrency."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Сравнить время"}</h3>
          <p>
            {"Запустить одинаковые операции последовательно и через Task/gather."}
          </p>

          <h3>{"Добавить timeout"}</h3>
          <p>
            {"Сделать одну операцию медленной и получить контролируемый результат."}
          </p>

          <h3>{"Отменить вручную"}</h3>
          <p>
            {"Проверить, что finally выполняется, а ложного success нет."}
          </p>

          <h3>{"Изменить limit"}</h3>
          <p>
            {"Сравнить active count при semaphore 1, 3 и 10."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Concurrency — это управление несколькими ожидающими задачами. Parallelism и несколько CPU-core являются другой моделью."}
        </Callout>

        <Callout tone={"warn"}>
          {"gather для тысяч операций без limit может перегрузить connection pool, внешний API или память процесса."}
        </Callout>

      </Section>

      <Section number={"04"} title={"Блок 27 · Async FastAPI и внешний HTTP I/O"}>
        <Lead>
          {"Механизм asyncio уже понятен, поэтому следующий блок переносит его в FastAPI. Ученик выбирает между def и async def, использует httpx.AsyncClient, обрабатывает сетевые ошибки, выносит client в dependency и управляет его lifecycle через lifespan."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"def endpoint"}</>, description: <>{"подходит для blocking library, которую FastAPI выполняет в threadpool"}</> },
            { title: <>{"async endpoint"}</>, description: <>{"подходит для awaitable I/O library"}</> },
            { title: <>{"AsyncClient"}</>, description: <>{"асинхронный HTTP client и connection pool"}</> },
            { title: <>{"dependency"}</>, description: <>{"конфигурация и тестовая замена client"}</> },
            { title: <>{"lifespan"}</>, description: <>{"startup и shutdown ресурса приложения"}</> },
            { title: <>{"mock transport"}</>, description: <>{"детерминированный тест без сети"}</> },
            { title: <>{"error mapping"}</>, description: <>{"внешний failure → безопасный StudyHub response"}</> },
          ]}
        />

        <CodeBlock
          caption={"путь внешнего запроса"}
          code={"client request\n→ async FastAPI endpoint\n→ recommendation service\n→ shared AsyncClient\n→ local mock API\n→ timeout/error mapping\n→ Pydantic response"}
        />

        <QuizCard
          question={"Какой endpoint не блокирует event loop во время сетевого ожидания?"}
          options={[{ title: "Blocking client inside async def", code: "async def endpoint():\n    response = requests.get(url)", description: "requests не отдаёт управление event loop во время ожидания." }, { title: "Awaitable client", code: "async def endpoint():\n    response = await client.get(url)", description: "AsyncClient отдаёт управление, пока сеть готовит ответ." }]}
          
          correctIndex={1}
          explanation={"Форма endpoint выбирается вместе с возможностями используемой I/O-library."}
        />

        <TypeCards>
          <TypeCard
            badge={"route"}
            title={"Endpoint"}
            code={"async def overview(...)"}
          >
            {"Связывает HTTP-contract и application service, но не хранит всю интеграционную логику."}
          </TypeCard>
          <TypeCard
            badge={"service"}
            badgeTone={"float"}
            title={"Integration service"}
            code={"await load_recommendations()"}
          >
            {"Собирает внешние requests и применяет fallback policy."}
          </TypeCard>
          <TypeCard
            badge={"resource"}
            badgeTone={"str"}
            title={"AsyncClient"}
            code={"base_url + timeout"}
          >
            {"Переиспользует connection pool и закрывается в lifespan."}
          </TypeCard>
          <TypeCard
            badge={"test"}
            title={"Mock"}
            code={"dependency override / MockTransport"}
          >
            {"Позволяет проверить сеть без случайной внешней зависимости."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Классифицировать endpoint"}</h3>
          <p>
            {"Назвать blocking или awaitable libraries внутри каждого flow."}
          </p>

          <h3>{"Поднять local mock-service"}</h3>
          <p>
            {"Не использовать случайный публичный API как обязательную часть курса."}
          </p>

          <h3>{"Проверить матрицу ошибок"}</h3>
          <p>
            {"Success, external 404, timeout и connection failure."}
          </p>

          <h3>{"Переиспользовать client"}</h3>
          <p>
            {"Подтвердить один startup/shutdown lifecycle на серию requests."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Синхронные endpoint могут сосуществовать с асинхронными. Цель — корректная модель I/O, а не единый стиль ради единообразия."}
        </Callout>

        <Callout tone={"warn"}>
          {"Нельзя возвращать клиенту внутренний traceback внешней интеграции. Подробность остаётся в server log, API получает безопасный error contract."}
        </Callout>

      </Section>

      <Section number={"05"} title={"Блок 28 · Async SQLAlchemy и наблюдаемость"}>
        <Lead>
          {"Database layer переводится последним. Знакомые statements сохраняются, но I/O выполняется через AsyncEngine и AsyncSession. После CRUD ученик переносит transaction, исправляет N+1, наблюдает connection pool и сравнивает конкретный sync/async-сценарий."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"AsyncEngine"}</>, description: <>{"создаёт async connections через подходящий driver"}</> },
            { title: <>{"async_sessionmaker"}</>, description: <>{"фабрика AsyncSession"}</> },
            { title: <>{"AsyncSession"}</>, description: <>{"unit of database work на request"}</> },
            { title: <>{"await execute"}</>, description: <>{"асинхронное ожидание SQL-result"}</> },
            { title: <>{"session.begin"}</>, description: <>{"transaction boundary"}</> },
            { title: <>{"selectinload"}</>, description: <>{"явная загрузка relation без N+1"}</> },
            { title: <>{"pool"}</>, description: <>{"ограниченный набор reusable connections"}</> },
          ]}
        />

        <CodeBlock
          caption={"async database path"}
          code={"HTTP request\n→ async get_db dependency\n→ AsyncSession\n→ await session.execute(statement)\n→ async PostgreSQL driver\n→ connection pool\n→ PostgreSQL\n→ scalars / ORM objects\n→ response"}
        />

        <CodeSequence
          title={"Соберите поэтапный перенос database layer"}
          prompt={"Сохраните возможность сравнивать поведение и быстро локализовать ошибку."}
          pieces={[
            {
              id: "engine",
              code: "создать отдельный AsyncEngine",
            },
            {
              id: "ping",
              code: "выполнить SELECT 1",
            },
            {
              id: "session",
              code: "создать async_sessionmaker и get_db",
            },
            {
              id: "read",
              code: "перенести один read endpoint",
            },
            {
              id: "write",
              code: "перенести write и transaction",
            },
            {
              id: "relations",
              code: "проверить relation loading и N+1",
            },
            {
              id: "tests",
              code: "запустить regression и load scenario",
            },
          ]}
          correctOrder={[
            "engine",
            "ping",
            "session",
            "read",
            "write",
            "relations",
            "tests",
          ]}
          explanation={"Синхронная версия удаляется только после того, как async vertical slice доказал одинаковый контракт и корректные ошибки."}
        />

        <TypeCards>
          <TypeCard
            badge={"driver"}
            title={"Async PostgreSQL driver"}
            code={"postgresql+asyncpg://"}
          >
            {"Поддерживает неблокирующий протокол, который можно await."}
          </TypeCard>
          <TypeCard
            badge={"scope"}
            badgeTone={"float"}
            title={"Request Session"}
            code={"async with session_factory()"}
          >
            {"Каждый request получает свой database context."}
          </TypeCard>
          <TypeCard
            badge={"loading"}
            badgeTone={"str"}
            title={"Explicit relations"}
            code={"selectinload(Model.items)"}
          >
            {"Количество запросов остаётся видимым и тестируемым."}
          </TypeCard>
          <TypeCard
            badge={"evidence"}
            title={"Measurement"}
            code={"p50 / p95 / throughput"}
          >
            {"Вывод делается по конкретному повторяемому сценарию."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Оставить sync baseline"}</h3>
          <p>
            {"Не удалять рабочий database layer до завершения сравнения."}
          </p>

          <h3>{"Переносить statements, не архитектуру"}</h3>
          <p>
            {"Сначала заменить способ выполнения I/O, не добавляя generic repository."}
          </p>

          <h3>{"Считать SQL-запросы"}</h3>
          <p>
            {"Async не исправляет N+1 и не уменьшает количество round trips."}
          </p>

          <h3>{"Проверить pool pressure"}</h3>
          <p>
            {"Намеренно ограничить pool и увидеть очередь вместо хаотичных выводов."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Методы add обычно не требуют await, а execute, commit, refresh, delete и rollback связаны с I/O и ожидаются асинхронно."}
        </Callout>

        <Callout tone={"warn"}>
          {"Долгая transaction удерживает connection вне зависимости от sync или async. Async не отменяет правила короткой transaction boundary."}
        </Callout>

      </Section>

      <Section number={"06"} title={"Архитектура Async StudyHub после этапа"}>
        <Lead>
          {"Финальный проект сохраняет знакомые слои StudyHub, но делает I/O-resources явными. Endpoint не создаёт клиент и engine на каждый request, service знает use case, database session живёт в request scope, а lifespan управляет долгоживущими ресурсами."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"main.py"}</>, description: <>{"создание FastAPI и lifespan"}</> },
            { title: <>{"routers"}</>, description: <>{"HTTP contract и dependency inputs"}</> },
            { title: <>{"services"}</>, description: <>{"application use cases и concurrency policy"}</> },
            { title: <>{"integrations"}</>, description: <>{"AsyncClient и external error mapping"}</> },
            { title: <>{"database"}</>, description: <>{"AsyncEngine, sessionmaker и get_db"}</> },
            { title: <>{"repositories/crud"}</>, description: <>{"SQLAlchemy statements без HTTP"}</> },
            { title: <>{"observability"}</>, description: <>{"logging context и request id"}</> },
          ]}
        />

        <CodeBlock
          caption={"целевая структура"}
          code={"app/\n├── main.py\n├── core/settings.py\n├── core/logging.py\n├── database/engine.py\n├── database/session.py\n├── integrations/recommendations.py\n├── routers/\n├── services/\n└── models/\ntests/\n├── integration/\n└── load/"}
        />

        <BranchExplorer
          code={"request\nrouter\nservice\n├── AsyncSession query\n├── AsyncClient request\n└── combine result\nresponse"}
          scenarios={[
            {
              label: "только локальные данные",
              activeLine: 3,
              output: "await database result",
            },
            {
              label: "внешняя рекомендация",
              activeLine: 4,
              output: "await external I/O without blocking loop",
            },
            {
              label: "два независимых источника",
              activeLine: 3,
              output: "service планирует controlled concurrency",
            },
            {
              label: "один источник упал",
              activeLine: 5,
              output: "service применяет заранее описанную policy",
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"HTTP"}
            title={"Router"}
            code={"async def overview(...)"}
          >
            {"Описывает method/path/status и получает dependencies."}
          </TypeCard>
          <TypeCard
            badge={"use case"}
            badgeTone={"float"}
            title={"Service"}
            code={"await build_dashboard(...)"}
          >
            {"Решает, что можно выполнять независимо и как обработать частичный failure."}
          </TypeCard>
          <TypeCard
            badge={"I/O"}
            badgeTone={"str"}
            title={"Resources"}
            code={"AsyncSession + AsyncClient"}
          >
            {"Явно передаются и имеют контролируемый lifecycle."}
          </TypeCard>
          <TypeCard
            badge={"trace"}
            title={"Observability"}
            code={"request_id=..."}
          >
            {"Связывает события одного request от входа до response."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Проследить один request"}</h3>
          <p>
            {"Назвать каждый await и ресурс, который реально ожидается."}
          </p>

          <h3>{"Проверить lifecycle"}</h3>
          <p>
            {"Убедиться, что session закрывается на request, а AsyncClient — на shutdown."}
          </p>

          <h3>{"Найти скрытый blocking call"}</h3>
          <p>
            {"Проверить сторонние libraries внутри async path."}
          </p>

          <h3>{"Объяснить policy"}</h3>
          <p>
            {"Сформулировать, когда внешний failure отменяет response, а когда допустим fallback."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Async — инфраструктурная характеристика I/O-path. Доменные функции без ожидания могут оставаться обычными def и легче тестироваться."}
        </Callout>

        <Callout tone={"warn"}>
          {"Нельзя хранить одну глобальную AsyncSession на всё приложение. Session содержит изменяемое transaction state и ограничивается request/use case."}
        </Callout>

      </Section>

      <Section number={"07"} title={"Что ученик доказывает после каждого блока"}>
        <Lead>
          {"Этап оценивает не количество async def, а доказательства понимания. После каждого блока остаётся артефакт, который можно запустить, сломать, измерить и объяснить без скрытых ручных действий."}
        </Lead>


        <ContentList
          items={[
            { title: <>{"timeline"}</>, description: <>{"порядок run/wait/resume"}</> },
            { title: <>{"test"}</>, description: <>{"success, error, timeout и cancellation"}</> },
            { title: <>{"trace"}</>, description: <>{"request id во всех ключевых logs"}</> },
            { title: <>{"SQL count"}</>, description: <>{"доказательство отсутствия N+1 в выбранном endpoint"}</> },
            { title: <>{"benchmark"}</>, description: <>{"сценарий, input, concurrency и результаты"}</> },
            { title: <>{"decision record"}</>, description: <>{"почему выбран sync или async path"}</> },
            { title: <>{"runbook"}</>, description: <>{"как запустить mock-service и tests"}</> },
          ]}
        />

        <CodeBlock
          caption={"матрица доказательств"}
          code={"concept → prediction\nimplementation → reproducible run\nfailure → expected handling\nperformance claim → measurement\narchitecture choice → written boundary"}
        />

        <FlipCards
          cards={[
            {
              front: <>{"async def"}</>,
              back: <>{"Покажите awaitable I/O внутри и объясните, где loop переключается."}</>,
            },
            {
              front: <>{"быстрее"}</>,
              back: <>{"Покажите baseline, одинаковую нагрузку и измеренный результат."}</>,
            },
            {
              front: <>{"timeout"}</>,
              back: <>{"Покажите контролируемый response и server log причины."}</>,
            },
            {
              front: <>{"cancellation"}</>,
              back: <>{"Покажите cleanup и отсутствие ложного success."}</>,
            },
            {
              front: <>{"нет N+1"}</>,
              back: <>{"Покажите число SQL statements на список объектов."}</>,
            },
            {
              front: <>{"готово"}</>,
              back: <>{"Запустите проект и tests по README с чистого окружения."}</>,
            },
          ]}
        />

        <TypeCards>
          <TypeCard
            badge={"explain"}
            title={"Модель"}
            code={"run → wait → resume"}
          >
            {"Ученик объясняет поведение до чтения вывода."}
          </TypeCard>
          <TypeCard
            badge={"reproduce"}
            badgeTone={"float"}
            title={"Сценарий"}
            code={"command + expected result"}
          >
            {"Другой разработчик повторяет проверку по инструкции."}
          </TypeCard>
          <TypeCard
            badge={"fail"}
            badgeTone={"str"}
            title={"Ошибка"}
            code={"timeout / cancel / DB error"}
          >
            {"Failure является частью контракта, а не неожиданным traceback."}
          </TypeCard>
          <TypeCard
            badge={"measure"}
            title={"Evidence"}
            code={"p50 / p95 / SQL count"}
          >
            {"Заявление о качестве опирается на наблюдаемое число."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Сохранить команду запуска"}</h3>
          <p>
            {"Каждая лаборатория имеет короткий воспроизводимый entry point."}
          </p>

          <h3>{"Фиксировать ожидаемый output"}</h3>
          <p>
            {"До запуска записать порядок logs или status результата."}
          </p>

          <h3>{"Проверять альтернативу"}</h3>
          <p>
            {"Сравнить sync/async или unbounded/bounded вариант."}
          </p>

          <h3>{"Сформулировать границу"}</h3>
          <p>
            {"Назвать, где изученный подход не нужен или опасен."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Качественный async-код может содержать меньше async-функций, чем некачественный. Важна корректная граница I/O и lifecycle."}
        </Callout>

        <Callout tone={"warn"}>
          {"Нагрузочный тест без описания данных, concurrency, duration и окружения не позволяет сделать надёжный вывод."}
        </Callout>

      </Section>

      <Section number={"08"} title={"Рабочий ритм и контрольная точка этапа"}>
        <Lead>
          {"Асинхронность сложнее читать, потому что порядок событий не всегда совпадает с порядком создания задач. Поэтому каждый шаг курса опирается на короткий timeline, предсказание logs и один новый механизм за раз."}
        </Lead>


        <LinkedNotes
          items={[
            { title: <>{"понимание"}</>, description: <>{"coroutine, Task, event loop и await объясняются без метафорической магии"}</> },
            { title: <>{"управление"}</>, description: <>{"timeout, cancellation и semaphore имеют тестируемый contract"}</> },
            { title: <>{"интеграция"}</>, description: <>{"AsyncClient переиспользуется и заменяется mock"}</> },
            { title: <>{"база"}</>, description: <>{"AsyncSession ограничена request scope"}</> },
            { title: <>{"целостность"}</>, description: <>{"transaction commit/rollback сохранены"}</> },
            { title: <>{"производительность"}</>, description: <>{"N+1 и blocking call проверены отдельно"}</> },
            { title: <>{"наблюдаемость"}</>, description: <>{"request id и structured logs связывают flow"}</> },
          ]}
        />

        <CodeBlock
          caption={"контрольный маршрут"}
          code={"predict timeline\n→ run async-lab\n→ add controlled concurrency\n→ trigger timeout and cancellation\n→ call local mock API\n→ execute AsyncSession CRUD\n→ inspect SQL/log trace\n→ measure one scenario\n→ explain boundary"}
        />

        <QuizCard orientation="vertical"
          description={<CodeBlock code={"async def dashboard():\n    profile = await load_profile()\n    stats = await load_stats()\n    response = requests.get(RECOMMENDATION_URL)\n    return profile, stats, response.json()"} />}
          question={"Какие две проблемы мешают назвать этот flow конкурентным и неблокирующим?"}
          options={[
            "Два await идут последовательно, а requests блокирует event loop",
            "В async def нельзя использовать return",
            "JSON нельзя получать из HTTP response",
          ]}
          correctIndex={0}
          explanation={"Независимые coroutine нужно планировать вместе, а blocking HTTP-client заменить awaitable library или вынести из async path."}
          answer={<CodeBlock caption="Исправленный вариант" code={"async def dashboard(client):\n    profile_task = asyncio.create_task(load_profile())\n    stats_task = asyncio.create_task(load_stats())\n    profile, stats = await asyncio.gather(profile_task, stats_task)\n    response = await client.get(RECOMMENDATION_URL)\n    return profile, stats, response.json()"} />}
        />

        <TypeCards>
          <TypeCard
            badge={"1"}
            title={"Понять"}
            code={"кто выполняется сейчас?"}
          >
            {"Назвать active coroutine и ожидаемый I/O."}
          </TypeCard>
          <TypeCard
            badge={"2"}
            badgeTone={"float"}
            title={"Предсказать"}
            code={"start A → start B → ..."}
          >
            {"Записать порядок logs до запуска."}
          </TypeCard>
          <TypeCard
            badge={"3"}
            badgeTone={"str"}
            title={"Сломать"}
            code={"timeout / cancel / block"}
          >
            {"Проверить failure path отдельно от happy path."}
          </TypeCard>
          <TypeCard
            badge={"4"}
            title={"Доказать"}
            code={"test + trace + measurement"}
          >
            {"Закрепить вывод воспроизводимым артефактом."}
          </TypeCard>
        </TypeCards>

        <div className="lesson-practice-steps">
          <h3>{"Один механизм за сцену"}</h3>
          <p>
            {"Не вводить Task, timeout, semaphore и HTTP-client в одном первом примере."}
          </p>

          <h3>{"Одна переменная изменения"}</h3>
          <p>
            {"Менять delay, concurrency limit или timeout отдельно."}
          </p>

          <h3>{"Один failure path"}</h3>
          <p>
            {"Сначала добиться ожидаемой ошибки, затем добавить обработку."}
          </p>

          <h3>{"Одно объяснение"}</h3>
          <p>
            {"После практики сформулировать, что именно позволило loop выполнить другую работу."}
          </p>

        </div>

        <Callout tone={"success"}>
          {"Следующий этап упакует уже работающий Async StudyHub в Linux/Docker. Поэтому текущий этап заканчивается воспроизводимым runtime и ясными logs."}
        </Callout>

        <Callout tone={"warn"}>
          {"Не добавляйте WebSocket, Celery, Kafka или микросервисы ради демонстрации async. Они требуют отдельных моделей и не входят в контрольную точку этапа."}
        </Callout>

        <div className="lesson-check-group">
          <QuizCard
            question={"Почему asyncio изучается до Async FastAPI и AsyncSession?"}
            options={[
              "Чтобы понять event loop и await без шума фреймворка",
              "Потому что FastAPI не поддерживает async",
              "Чтобы отказаться от HTTP",
            ]}
            correctIndex={0}
            explanation={"Механизм легче увидеть на маленьких coroutine, а затем осознанно перенести в инфраструктуру."}
          />

          <QuizCard
            question={"Что доказывает пользу async-перехода?"}
            options={[
              "Измеренный I/O-сценарий и корректный failure contract",
              "Количество async def",
              "Отсутствие обычных функций",
            ]}
            correctIndex={0}
            explanation={"Async оценивается по конкретному ожиданию, поведению под concurrency и измерению."}
          />

          <QuizCard
            question={"Когда появляется AsyncSession?"}
            options={[
              "После coroutine, controlled concurrency и async HTTP integration",
              "В первом примере async def",
              "До изучения event loop",
            ]}
            correctIndex={0}
            explanation={"Database layer переводится после понимания общей модели и безопасного lifecycle."}
          />

          <QuizCard
            question={"Что остаётся источником истины в проекте?"}
            options={[
              "PostgreSQL",
              "Event loop",
              "AsyncClient",
            ]}
            correctIndex={0}
            explanation={"Async меняет способ ожидания I/O, но не роль PostgreSQL и database constraints."}
          />

        </div>

        <KeyTakeaways
          points={[
            <>{"Async решает ожидание I/O, а не ускоряет любое вычисление."}</>,
            <>{"Coroutine object создаётся раньше, чем начинает выполняться."}</>,
            <>{"Event loop переключается только в точках отдачи управления."}</>,
            <>{"Последовательные await не равны concurrency."}</>,
            <>{"Task, timeout, cancellation и semaphore управляют жизненным циклом работы."}</>,
            <>{"AsyncClient и AsyncSession имеют явный lifecycle."}</>,
            <>{"Async не исправляет N+1, длинную transaction или слабый SQL."}</>,
            <>{"Любое заявление о производительности подтверждается измерением."}</>,
          ]}
        />

        <PracticeCta
          text={"Перед занятием 141 запустите текущий PostgreSQL StudyHub, сохраните результаты tests и выберите один I/O-flow для будущего сравнения. Нарисуйте его как последовательность: Python work → external wait → Python work."}
        />

      </Section>

    </RichLesson>
  );
}
