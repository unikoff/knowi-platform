import { KeyRound, Trophy } from "lucide-react";
import { Callout, CodeBlock, FlipCards, KeyTakeaways, Lead, ContentList, PracticeCta, QuizCard, RecallCard, RichHero, RichLesson, Section, StepThrough, TerminalDemo, TypeCard, TypeCards } from "../../shared";
const BLOCK_TITLE = "Блок 24 · Индексы, планы запросов и модели хранения";

export function Lesson140({ module }: { module?: string }) {
  return (
    <RichLesson>
      <RichHero
        chip={module ?? BLOCK_TITLE}
        title={"Redis, TTL и итоговый аудит хранилищ"}
        intro={"Завершим этап key-value моделью Redis: пройдём cache hit/miss, TTL и истечение ключа, затем защитим карту данных StudyHub с явным источником истины для каждого сценария."}
        tags={[
          {
            icon: <KeyRound size={14} />,
            label: "key/value и TTL",
          },
          {
            icon: <Trophy size={14} />,
            label: "аудит хранилищ",
          },
        ]}
      />
      <Callout tone="success">
        <strong>{"Связь с курсом."}</strong>
        {" "}
        {"PostgreSQL хранит связанные долговечные данные, MongoDB показала документ как единицу хранения. Redis добавляет быстрые временные значения, которые допустимо потерять или восстановить из источника истины."}
        {" "}
        <strong>{"Важно не перепутать:"}</strong>
        {" "}
        {"Cache не становится источником истины только потому, что отвечает быстрее. Важное состояние должно иметь надёжное основное хранение или явную стратегию потери."}
      </Callout>
      <Section number={"01"} title={"Проблема и маршрут занятия"}>
        <Lead>
          {"Завершим этап key-value моделью Redis: пройдём cache hit/miss, TTL и истечение ключа, затем защитим карту данных StudyHub с явным источником истины для каждого сценария."}
        </Lead>
        <TypeCards>
          <TypeCard
            badge={"key"}
            title={"Адрес значения"}
            code={"stats:user:42:v1"}
          >
            {"Строка вроде stats:user:42:v1."}
          </TypeCard>
          <TypeCard
            badge={"value"}
            badgeTone={"float"}
            title={"Сериализованные данные"}
            code={"{\"open\": 7}"}
          >
            {"Небольшое значение, которое быстро читается."}
          </TypeCard>
          <TypeCard
            badge={"TTL"}
            badgeTone={"str"}
            title={"Срок жизни"}
            code={"EX 60"}
          >
            {"После истечения Redis удаляет ключ или считает его отсутствующим."}
          </TypeCard>
        </TypeCards>
        <Callout tone="success">
          {"Сначала сформулируйте наблюдаемую проблему и критерий успеха. Инструмент появляется только после этого."}
        </Callout>
      </Section>
      <Section number={"02"} title={"Главная модель и термины"}>
        <Lead>
          {"PostgreSQL хранит связанные долговечные данные, MongoDB показала документ как единицу хранения. Redis добавляет быстрые временные значения, которые допустимо потерять или восстановить из источника истины."}
        </Lead>
        <ContentList
          items={[
            { title: <>{"cache hit"}</>, description: "ключ найден, значение возвращается без запроса к source of truth" },
            { title: <>{"cache miss"}</>, description: "ключа нет; приложение читает PostgreSQL и заново заполняет cache" },
            { title: <>{"TTL"}</>, description: "время до автоматического истечения ключа" },
            { title: <>{"source of truth"}</>, description: "основное долговечное хранилище факта" },
            { title: <>{"invalidation"}</>, description: "удаление или обновление устаревшего cache после изменения данных" },
          ]}
        />
        <CodeBlock
          caption={"cache-aside маршрут"}
          code={[
          "cached = redis.get(\"stats:user:42:v1\")",
          "if cached is not None:",
          "    return json.loads(cached)",
          "",
          "stats = load_stats_from_postgresql(user_id=42)",
          "redis.setex(",
          "    \"stats:user:42:v1\",",
          "    60,",
          "    json.dumps(stats),",
          ")",
          "return stats",
        ] .join(String.fromCharCode(10))}
        />
        <RecallCard
          question={"Объясните главную модель этого раздела без терминов из документации."}
          hint={"Назовите вход, выполняемую работу и наблюдаемый результат."}
          answer={
            <p>{"Cache не становится источником истины только потому, что отвечает быстрее. Важное состояние должно иметь надёжное основное хранение или явную стратегию потери."}</p>
          }
        />
      </Section>
      <Section number={"03"} title={"Жизненный цикл ключа с TTL"}>
        <Lead>
          {"Ключ создаётся на ограниченное время. Пока TTL положителен, чтение может быть hit. После истечения приложение обязано корректно обработать miss и восстановить значение из PostgreSQL."}
        </Lead>
        <StepThrough
          code={[
          "SETEX stats:user:42:v1 60 '{\"open\":7}'",
          "TTL stats:user:42:v1",
          "GET stats:user:42:v1",
          "# ... прошло 60 секунд ...",
          "GET stats:user:42:v1",
        ] .join(String.fromCharCode(10))}
          steps={[
            {
              line: 0,
              note: "SETEX создаёт значение и TTL одним атомарным действием.",
              vars: {"ttl": "60"},
            },
            {
              line: 1,
              note: "TTL показывает оставшееся время жизни.",
              vars: {"remaining": "59...0"},
            },
            {
              line: 2,
              note: "До истечения чтение возвращает значение.",
              vars: {"result": "cache hit"},
            },
            {
              line: 3,
              note: "Время проходит без изменения PostgreSQL.",
              vars: {"redis": "expires"},
            },
            {
              line: 4,
              note: "После истечения GET возвращает nil.",
              vars: {"result": "cache miss"},
            },
          ]}
        />
        <Callout tone="success">
          {"Пошаговый разбор нужен не для запоминания вывода, а для объяснения причин каждого перехода."}
        </Callout>
        <QuizCard
          question={<>{"Cache не становится источником истины только потому, что отвечает быстрее. Важное состояние должно иметь надёжное основное хранение или явную стратегию потери."}</>}
          options={["Правда", "Ложь"]} correctIndex={0}
          explanation={"Это ключевая граница урока: без неё инструмент легко применить механически."}
        />
      </Section>
      <Section number={"04"} title={"Переверните карточки хранения"}>
        <Lead>
          {"Сейчас нужно не копировать готовую команду, а выбрать ветку или структуру по требованиям конкретного сценария."}
        </Lead>
        <FlipCards
          cards={[
            {
              front: <strong>{"PostgreSQL"}</strong>,
              back: <span>{"Связанные долговечные данные, constraints, transactions и source of truth."}</span>,
            },
            {
              front: <strong>{"MongoDB"}</strong>,
              back: <span>{"Документ как единица чтения; изолированный snapshot-эксперимент."}</span>,
            },
            {
              front: <strong>{"Redis"}</strong>,
              back: <span>{"Быстрые временные значения, TTL, cache и служебное состояние."}</span>,
            },
            {
              front: <strong>{"README/runbook"}</strong>,
              back: <span>{"Не хранилище данных, а инструкция по воспроизводимой эксплуатации."}</span>,
            },
          ]}
        />
        <Callout>
          {"После выбора проговорите, какое условие изменило решение и какой альтернативный результат был бы возможен."}
        </Callout>
      </Section>
      <Section number={"05"} title={"Сравнение решений и цена выбора"}>
        <Lead>
          {"Два варианта могут быть синтаксически корректны, но только один соответствует измеряемому query pattern, модели данных или эксплуатационной процедуре."}
        </Lead>
        <QuizCard
          question={"Где хранить единственный факт оплаты или право доступа пользователя?"}
          options={[{ title: "Только Redis с TTL", code: "SETEX access:user:42 3600 granted", description: "После истечения или потери Redis право исчезнет без надёжной истории и восстановления." }, { title: "PostgreSQL как источник истины", code: [
          "user_permissions / subscriptions tables",
          "Redis — только ускоряющий cache",
        ] .join(String.fromCharCode(10)), description: "Долговечный факт хранится реляционно, кеш можно удалить и построить заново." }]}
          
          correctIndex={1}
          explanation={"Критичное состояние нельзя оставлять только в временном cache без отдельного source of truth."}
        />
        <QuizCard
          question={<>{"Корректный синтаксис сам по себе ещё не доказывает, что решение подходит проектному сценарию."}</>}
          options={["Правда", "Ложь"]} correctIndex={0}
          explanation={"Решение оценивается по query pattern, модели данных, измерению и эксплуатационной цене."}
        />
        <div className="lesson-practice-steps">
          <h3>{"Вопрос перед изменением"}</h3>
          <p>{"Какую конкретную работу перестанет выполнять система или какую гарантию добавит выбранный вариант?"}</p>
          <h3>{"Вопрос после изменения"}</h3>
          <p>{"Каким измерением, plan, smoke test или наблюдаемым сценарием подтверждается результат?"}</p>
          <h3>{"Граница"}</h3>
          <p>{"Cache не становится источником истины только потому, что отвечает быстрее. Важное состояние должно иметь надёжное основное хранение или явную стратегию потери."}</p>
        </div>
      </Section>
      <Section number={"06"} title={"Cached stats с явным miss"}>
        <Lead>
          {"Итоговый эксперимент кеширует вычисленную статистику пользователя на 60 секунд. При обновлении задачи ключ удаляется, а при потере Redis значение снова вычисляется из PostgreSQL."}
        </Lead>
        <CodeBlock
          caption={"services/stats.py"}
          code={[
          "import json",
          "",
          "",
          "def get_user_stats(redis, session, user_id: int) -> dict:",
          "    key = f\"stats:user:{user_id}:v1\"",
          "    cached = redis.get(key)",
          "    if cached is not None:",
          "        return json.loads(cached)",
          "",
          "    stats = calculate_stats(session, user_id)",
          "    redis.setex(key, 60, json.dumps(stats))",
          "    return stats",
          "",
          "",
          "def invalidate_user_stats(redis, user_id: int) -> None:",
          "    redis.delete(f\"stats:user:{user_id}:v1\")",
        ] .join(String.fromCharCode(10))}
        />
        <TerminalDemo
          title={"проверка в терминале"}
          lines={[
            { cmd: "redis-cli SETEX verify:user:42 30 843921" },
            { out: "OK" },
            { cmd: "redis-cli TTL verify:user:42" },
            { out: "27" },
            { cmd: "redis-cli GET verify:user:42" },
            { out: "\"843921\"" },
            { cmd: "redis-cli DEL verify:user:42" },
            { out: "(integer) 1" },
          ]}
        />
        <div className="lesson-practice-steps">
          <h3>{"До запуска"}</h3>
          <p>{"Зафиксируйте входные данные, версию команды и ожидаемый наблюдаемый результат."}</p>
          <h3>{"После запуска"}</h3>
          <p>{"Сохраните фактический вывод, сравните его с ожиданием и объясните расхождение."}</p>
          <h3>{"Перед коммитом"}</h3>
          <p>{"Повторите успешный и ошибочный сценарий из чистого состояния."}</p>
        </div>
        <Callout tone="success">
          {"Проектный артефакт должен запускаться повторно: SQL-файл, migration, script, test или runbook сохраняется в репозитории."}
        </Callout>
      </Section>
      <Section number={"07"} title={"Диагностика ошибки и объяснение результата"}>
        <Lead>
          {"Профессиональный сценарий включает не только успех. Найдите ошибочное предположение, исправьте минимальную часть и повторите прежнюю проверку."}
        </Lead>
        <ContentList
          items={[
            { title: <>{"наблюдение"}</>, description: "записать точный вывод, plan, row count или cache result" },
            { title: <>{"ожидание"}</>, description: "назвать результат, который считался правильным" },
            { title: <>{"расхождение"}</>, description: "найти первое место, где факт перестал совпадать с ожиданием" },
            { title: <>{"минимальное исправление"}</>, description: "изменить одну причину и повторить прежнюю проверку" },
          ]}
        />
        <QuizCard orientation="vertical"
          description={<CodeBlock code={[
          "def complete_task(task_id):",
          "    update_task_in_postgresql(task_id)",
          "    return {\"status\": \"done\"}",
          "",
          "# stats:user:42:v1 остаётся прежним до TTL",
        ] .join(String.fromCharCode(10))} />}
          question={"Почему пользователь может увидеть устаревшую статистику?"}
          options={[
            "После изменения source of truth cache не инвалидирован",
            "PostgreSQL не поддерживает UPDATE",
            "TTL всегда равен нулю",
          ]}
          correctIndex={0}
          explanation={"Cache-aside требует удалить или обновить зависимый ключ после изменения данных."}
          answer={<CodeBlock caption="Исправленный вариант" code={[
          "def complete_task(redis, task_id, user_id):",
          "    update_task_in_postgresql(task_id)",
          "    redis.delete(f\"stats:user:{user_id}:v1\")",
          "    return {\"status\": \"done\"}",
        ] .join(String.fromCharCode(10))} />}
        />
        <RecallCard
          question={"Что должно произойти при полной потере Redis?"}
          answer={
            <p>{"Основные данные StudyHub остаются в PostgreSQL. Первый запрос получает cache miss, пересчитывает значение и заново заполняет Redis."}</p>
          }
        />
        <Callout>
          {"Не скрывайте ошибку новой технологией. Сначала назовите нарушенное ожидание, затем покажите проверяемое исправление."}
        </Callout>
      </Section>
      <Section number={"08"} title={"Контрольная точка и практика"}>
        <Lead>
          {"Урок завершён, когда вы можете воспроизвести сценарий, объяснить выбранный инструмент и показать отрицательный путь без подсказки."}
        </Lead>
        <TypeCards>
          <TypeCard
            badge={"артефакт"}
            title={"Что предъявить"}
            code={"reproducible artifact"}
          >
            {"SQL, migration, script, plan, backup report или cache lab сохранены в репозитории."}
          </TypeCard>
          <TypeCard
            badge={"проверка"}
            badgeTone={"float"}
            title={"Что доказать"}
            code={"success + failure"}
          >
            {"Успешный и ошибочный сценарии дают ожидаемый наблюдаемый результат."}
          </TypeCard>
          <TypeCard
            badge={"защита"}
            badgeTone={"str"}
            title={"Что объяснить"}
            code={"decision + trade-off"}
          >
            {"Выбор связан с требованиями проекта и имеет названную цену."}
          </TypeCard>
        </TypeCards>
        <div className="lesson-check-group">
          <QuizCard
            question={"Что делает TTL?"}
            options={[
              "Ограничивает время жизни ключа",
              "Создаёт SQL JOIN",
              "Шифрует value",
            ]}
            correctIndex={0}
            explanation={"После истечения ключ становится отсутствующим."}
          />
          <QuizCard
            question={"Что такое cache miss?"}
            options={[
              "Ключ не найден и данные нужно получить из source of truth",
              "Ошибка синтаксиса Python",
              "Успешный hit",
            ]}
            correctIndex={0}
            explanation={"Miss является обычной веткой cache-aside."}
          />
          <QuizCard
            question={"Где хранить критичное долговечное состояние StudyHub?"}
            options={[
              "В PostgreSQL",
              "Только в Redis",
              "Только в памяти процесса",
            ]}
            correctIndex={0}
            explanation={"PostgreSQL остаётся source of truth."}
          />
          <QuizCard
            question={"Когда нужен invalidate?"}
            options={[
              "После изменения данных, от которых зависит cache",
              "После каждого GET",
              "Только при создании индекса",
            ]}
            correctIndex={0}
            explanation={"Иначе cache может возвращать устаревший результат."}
          />
        </div>
        <KeyTakeaways
          points={[
            <>{"Redis хранит значения по ключам и особенно полезен для временного состояния."}</>,
            <>{"TTL ограничивает срок актуальности значения."}</>,
            <>{"Cache hit возвращает готовое значение, miss идёт к source of truth."}</>,
            <>{"PostgreSQL остаётся источником истины StudyHub."}</>,
            <>{"После изменения данных зависимый cache инвалидируется."}</>,
            <>{"MongoDB и Redis выбираются по требованиям, а не добавляются ради количества технологий."}</>,
            <>{"Этап завершается защищаемой картой хранения данных."}</>,
          ]}
        />
        <PracticeCta text={"Реализуйте cached stats с TTL 60 секунд, проверьте hit/miss/invalidate и оформите итоговую матрицу PostgreSQL vs MongoDB vs Redis."} />
        <div className="lesson-practice-steps">
          <h3>{"Критерий готовности"}</h3>
          <p>{"Есть воспроизводимый артефакт, успешная проверка, ожидаемый сбой, объяснение результата и отдельный осмысленный Git-коммит."}</p>
        </div>
      </Section>
    </RichLesson>
  );
}
