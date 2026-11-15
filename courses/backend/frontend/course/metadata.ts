import type { LessonMetadata } from "@course-ui";

// One fallback per block for API-free authoring previews. On the platform the
// current catalog's module label wins; lesson files do not supply this metadata.
const blocks: Record<number, { title: string; tags: string[] }> = {
  1: { title: "Первые шаги в Python", tags: ["Python", "запуск программы"] },
  2: { title: "Условия, данные и циклы", tags: ["Python", "управление программой"] },
  3: { title: "Функции и инструменты", tags: ["функции", "инструменты разработки"] },
  4: { title: "Собираем свой Planner", tags: ["Planner", "CLI"] },
  5: { title: "Функции и улучшение существующего CLI", tags: ["функции", "CLI"] },
  6: { title: "Ошибки, модули и рабочее JSON-хранение", tags: ["JSON", "хранение данных"] },
  7: { title: "Обычные классы, состояние и Task", tags: ["классы", "состояние объекта"] },
  8: { title: "Интеграция модели, service/storage и тесты", tags: ["архитектура", "тестирование"] },
  9: { title: "От консольной команды к HTTP", tags: ["HTTP", "API"] },
  10: { title: "Postman, FastAPI и Pydantic", tags: ["FastAPI", "HTTP"] },
  11: { title: "Схемы, CRUD и организация приложения", tags: ["CRUD", "FastAPI"] },
  12: { title: "Роутеры, архитектура, тестирование и финальный проект", tags: ["API", "тестирование"] },
  13: { title: "Организация и зрелые механизмы FastAPI", tags: ["FastAPI", "архитектура"] },
  14: { title: "Подготовка базы и новой границы хранения", tags: ["SQL", "хранение данных"] },
  15: { title: "Полный SQL-путь и единое переключение", tags: ["SQLAlchemy", "Planner"] },
  16: { title: "Надёжность, запросы и передача", tags: ["SQL", "надёжность"] },
  17: { title: "Миграции базы", tags: ["SQLite", "миграции"] },
  18: { title: "Учётные записи", tags: ["пользователи", "аутентификация"] },
  19: { title: "Серверные сессии", tags: ["сессии", "безопасность"] },
  20: { title: "Владение задачами", tags: ["авторизация", "Planner"] },
  21: { title: "SQL как язык работы с данными", tags: ["SQL", "запросы"] },
  22: { title: "PostgreSQL и перенос StudyHub", tags: ["PostgreSQL", "перенос данных"] },
  23: { title: "JOIN, агрегаты и транзакции", tags: ["SQL", "транзакции"] },
  24: { title: "Индексы, планы запросов и модели хранения", tags: ["индексы", "планы запросов"] },
  25: { title: "Coroutine, event loop и async/await", tags: ["async/await", "event loop"] },
  26: { title: "Конкурентные задачи, timeout и cancellation", tags: ["конкурентность", "asyncio"] },
  27: { title: "Асинхронный FastAPI и внешние HTTP-сервисы", tags: ["FastAPI", "асинхронность"] },
  28: { title: "Async SQLAlchemy, нагрузка и наблюдаемость", tags: ["SQLAlchemy", "производительность"] },
  29: { title: "Linux, процессы, окружения и логи", tags: ["Linux", "окружение"] },
  30: { title: "Dockerfile и контейнер приложения", tags: ["Docker", "контейнеры"] },
  31: { title: "Docker Compose, API, PostgreSQL и Redis", tags: ["Docker Compose", "сервисы"] },
  32: { title: "GitHub Actions, CI/CD и первый деплой", tags: ["CI/CD", "деплой"] },
  33: { title: "Проектирование StudyHub LMS Core", tags: ["StudyHub", "архитектура"] },
  34: { title: "Курсы, зачисление и прогресс", tags: ["StudyHub", "учебный прогресс"] },
  35: { title: "Redis, кеш и фоновые операции", tags: ["Redis", "фоновые задачи"] },
  36: { title: "Финальное качество, портфолио и интервью", tags: ["релиз", "портфолио"] },
};

export function getLessonMetadata(source: string, module?: string, title?: string): LessonMetadata | null {
  const path = source.replace(/\\/g, "/");
  const blockNumber = Number(path.match(/(?:^|\/)block_(\d+)\//)?.[1]);
  const pathNumber = path.match(/\/(?:Lesson)?(\d+(?:[._]\d+)*)(?:\s*-|(?:\.tsx)?$)/)?.[1];
  if (!blockNumber || !pathNumber) return null; // Chapter roadmaps are not lessons.

  const catalog = module?.match(/^Блок\s+(\d+)\s*[.·:—–-]\s*(.+)$/i);
  const blockTitle = catalog && Number(catalog[1]) === blockNumber
    ? catalog[2].replace(/\s*\(\d+\s+заняти[^)]*\)\s*$/, "")
    : blocks[blockNumber]?.title;
  // These published components retain their old filenames after renumbering.
  const historicalNumber = path.match(/chapter_03\/block_10\/(?:Lesson54(?:\.tsx)?$|54\s*-)/) ? "48.1"
    : path.match(/chapter_03\/block_11\/(?:Lesson57(?:\.tsx)?$|57\s*-)/) ? "51.1" : undefined;
  const displayNumber = title?.match(/^\s*(\d+(?:\.\d+)*)(?:\.\s|\s*[-–—]\s)/)?.[1];
  const lessonNumber = (displayNumber ?? historicalNumber ?? pathNumber).replace(/_/g, ".")
    .split(".").map((part) => String(Number(part))).join(".");
  return { block: `Блок ${blockNumber}${blockTitle ? ` ${blockTitle}` : ""}`, blockNumber, lessonNumber, tags: getLessonTags(title ?? "", blockNumber) };
}

// Ordered from specific technologies to broader concepts. Rules are configured
// once and apply to existing, renamed and newly authored lesson titles.
const topics: [RegExp, string][] = [
  [/github\s+actions/i, "GitHub Actions"],
  [/\bci\b|\bcd\b/i, "CI/CD"],
  [/депло|deploy/i, "деплой"],
  [/docker\s+compose/i, "Docker Compose"],
  [/docker(?!\s+compose)/i, "Docker"],
  [/alembic/i, "Alembic"],
  [/baseline/i, "baseline"],
  [/sqlite/i, "SQLite"],
  [/postgres/i, "PostgreSQL"],
  [/sqlalchemy/i, "SQLAlchemy"],
  [/\bsql\b/i, "SQL"],
  [/fastapi/i, "FastAPI"],
  [/pydantic/i, "Pydantic"],
  [/postman/i, "Postman"],
  [/redis/i, "Redis"],
  [/json/i, "JSON"],
  [/github(?!\s+actions)|\bgit\b/i, "Git"],
  [/linux|терминал/i, "терминал"],
  [/схем[аыуеой]|столбц|ограничени/i, "схема базы"],
  [/восстанов|restore/i, "восстановление"],
  [/снимок|снимк|резерв|backup/i, "резервная копия"],
  [/миграци/i, "миграции"],
  [/регресси|тест|pytest|fixture/i, "тестирование"],
  [/парол|хеш|credentials/i, "пароли"],
  [/csrf|origin/i, "CSRF"],
  [/cookie/i, "cookie"],
  [/сесси/i, "сессии"],
  [/пользовател|учётн|регистраци/i, "пользователи"],
  [/владел|владени|доступ|авторизаци/i, "авторизация"],
  [/аутентификац|идентичност|вход/i, "аутентификация"],
  [/async|await|асинхрон/i, "асинхронность"],
  [/event\s+loop/i, "event loop"],
  [/timeout|таймаут/i, "таймауты"],
  [/cancellation|отмен/i, "отмена задач"],
  [/транзакци|transaction/i, "транзакции"],
  [/\bjoin\b/i, "JOIN"],
  [/индекс/i, "индексы"],
  [/http|content-type|статус.*ответ|метод.*запрос|заголовк/i, "HTTP"],
  [/\bapi\b|маршрут|роутер/i, "API"],
  [/cli|консоль/i, "CLI"],
  [/функци|return|параметр|аргумент/i, "функции"],
  [/класс|объект|экземпляр|self/i, "классы и объекты"],
  [/словар|список|кортеж|множеств|коллекци/i, "коллекции"],
  [/цикл|\bfor\b|\bwhile\b/i, "циклы"],
  [/услови|ветвлен|\bif\b|boolean/i, "условия"],
  [/строк|текст/i, "строки"],
  [/числ|арифмет|преобразован/i, "числа и типы"],
  [/ошиб|traceback|отлад|исключени/i, "ошибки и отладка"],
  [/файл|пут[ьи]|with|utf-8/i, "файлы"],
  [/readme|документ/i, "документация"],
  [/запуска|запуск|программ[аыу]/i, "запуск программы"],
  [/релиз|release|выпуск/i, "релиз"],
  [/planner/i, "Planner"],
  [/studyhub/i, "StudyHub"],
];

export function getLessonTags(title: string, blockNumber?: number): string[] {
  const matches = topics.filter(([pattern]) => pattern.test(title)).map(([, label]) => label);
  const defaults = blocks[blockNumber ?? 0]?.tags ?? ["программирование", "практика"];
  return [...new Set([...matches, ...defaults])].slice(0, 3);
}
