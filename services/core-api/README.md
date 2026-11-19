# Core API

Единственный рабочий пакет — `pymentor_core`, runtime — PostgreSQL и Redis.
Это модульный монолит с границами для дальнейшего
выделения сервисов. Курсы поставляются пакетами `courses/<course_id>`; общий Core
и интерфейс остаются в монорепозитории.

```text
services/core-api/
  pymentor_core/
    app/                  factory, настройки, lifespan, сборка зависимостей, ASGI/worker
    modules/
      identity/           аккаунты, права, вход и восстановление повторного запроса
      learning/           каталог, доступ и правила учебного прогресса
      practice/           отправка решения и порт исполнения
      bookings/           время, квоты и публичные операции бронирования
      landings/           заявки и представления
      notifications/      контракты, retry-политика и сервис очереди
      administration/     проверка доступа и административные HTTP сценарии
    adapters/
      sql/                общие модели, UoW и SQL реализации команд
      postgres/           async sessions, cleanup и защита импортированного outbox
      redis/              хранилище сеансов
      course_local/       чтение существующих файлов через CourseCatalog/LessonContent/PracticeSpec
      legacy_runner/      текущий subprocess interpreter за портом
      telegram/           транспорт уведомлений
    shared/               внутренние порты, ошибки, неизменяемые bindings
  migrations/             один Alembic; выпущенные revisions сохранены
  tools/maintenance/      явная команда прав администратора для PostgreSQL
  tools/lab/              guarded запуск единственной factory на disposable PG/Redis
  tests/regression/       сохранённые сценарии API, данных, курсов и отказов
  tests/architecture/     автоматические проверки границ
```

HTTP выполняет валидацию/авторизацию и вызывает сценарий. Политика не импортирует
ORM/Redis. Адаптер фиксирует данные в UoW; outbox записывается вместе с бизнес-записью,
Telegram отправляется worker вне бизнес-транзакции. Код/токены не становятся событиями
аналитики. Shared — только внутренняя часть Core, не общий ORM-пакет микросервисов.

## Как ориентироваться и добавлять код

- `app/factory.py` собирает HTTP-приложение, `app/worker.py` запускает обработчик уведомлений.
- `app/wiring.py` подключает реализации; `app/paths.py` задаёт все пути к коду, контенту и writable state.
- `app/schema_policy.py` хранит ожидаемую ревизию/набор таблиц; runtime не выполняет DDL.
- `modules/<область>/` содержит правила и операции области. `api/` — её HTTP-граница:
  роуты, модели запроса/ответа, авторизация и перевод ошибок.
- `adapters/<технология>/` содержит SQL, Redis, файловые и сетевые реализации. Блокировки,
  транзакции и внутренние SQL-функции не публикуются через HTTP-фасады.
- В `course_local` чтение практики и теории находится в `practice.py` и `theory.py`.
  Сценарии `complete_lesson.py` и `save_progress.py` лежат прямо в своих бизнес-модулях.
- `contracts/` — сохранённые внешние контракты; `shared/` — небольшие внутренние контракты Core.

Новый файл добавляется по ответственности, новая подпапка — когда в ней действительно
несколько связанных частей. Не нужны отдельные `application/` или `src/` ради одного файла.
Публичные границы и транзакционные гарантии сохраняются; количество файлов само по себе
не является поводом объединять разные бизнес-области или удалять проверки отказов.

Проверка зависимостей из корня проекта:

```powershell
python services/core-api/tools/check_architecture.py
```

Она анализирует реальный пакет, включая относительные импорты, и завершается с ошибкой
при пустом/отсутствующем каталоге, синтаксической ошибке или запрещённой зависимости.
Runtime не зависит от tools/tests; модули не импортируют адаптеры/Redis; shared не зависит
от приложения и бизнес-модулей. Чистые бизнес-правила не импортируют SQLAlchemy/FastAPI.
Динамические импорты проверяются отдельными тестами запуска, а не этим AST-анализатором.

## Единственный runtime

Core требует явные `DATABASE_URL` (PostgreSQL), `REDIS_URL`, Redis prefix и login recovery key.
SQLite default/fallback, SQL-only auth и выбор двух DB-профилей отсутствуют.
`PYMENTOR_PROFILE` не нужен; старые значения отклоняются. Приложение не загружает `.env`
и не применяет DDL на startup/readiness. Настройки передаются окружением/Secret store.

На AlphaVPS уже актуальные данные. Этот спринт удаляет legacy-код; новый перенос,
importer, переинициализация сессий и повторный cutover не требуются.
Пример переменных без секретов: [`.env.example`](.env.example).

| Настройка | Контракт |
| --- | --- |
| `DATABASE_URL` | `postgresql+psycopg://...` или `postgresql+asyncpg://...`; обязательна |
| `REDIS_URL` | `redis://...` или `rediss://...`; обязательна |
| `KNOWI_REDIS_PREFIX` | Отдельный namespace окружения; сохранить текущее значение |
| `KNOWI_LOGIN_RECOVERY_KEY` | 64 hex символа; сохранить текущий ключ для повторов входа |
| `PYMENTOR_STATE_DIR` | Writable абсолютный каталог для локальных locks; не база данных |
| `PYMENTOR_COURSE_TRANSPORT` | `local` в этой копии; `http` требует `COURSE_CONTENT_URL` и отдельного сервиса |

`PYMENTOR_REDIS_PREFIX` и `PYMENTOR_LOGIN_RECOVERY_KEY` остаются равнозначными aliases.
При одновременном задании KNOWI/PYMENTOR значения должны совпадать. Изменение prefix/key
затрагивает существующие сессии; cleanup сохраняет текущие Secret references и значения.
PostgreSQL `sessions` хранит digests/revocation/committed-operation evidence; это не SQL-only
способ входа. HTTP auth требует Redis. При его недоступности возвращается контролируемый
отказ; восстановление потерянной проекции допускается только по подтверждённой операции.

Общие SQL models/UoW и Alembic history сохраняются. HTTP-сценарии используют asyncpg
и Redis; отдельные служебные проверки и совместимые бизнес-функции используют sync
PostgreSQL. Авторский контент находится в `courses/<course_id>/backend`, React-страницы
и лендинги — в `courses/<course_id>/frontend`. Core загружает каталог через адаптер
и протокол пакета курса; прежние материалы сохранены в `courses/<course_id>/archive/legacy-content`.
Дублирующего `services/core-api/content` больше нет. ID ресурсов сохранены; `course_id` необязателен
для прежних API-клиентов и по умолчанию выбирает `backend`.

## Установка и запуск

Из корня проекта создайте окружение сервиса (canonical image использует Python 3.12):

```powershell
python -m venv services/core-api/.venv
services/core-api/.venv/Scripts/python.exe -m pip install -r services/core-api/requirements-dev.txt
$env:PYTHONPATH="$PWD\services\core-api"
$env:DATABASE_URL="postgresql+psycopg://<app-role>:<secret>@<host>:5432/<database>"
$env:REDIS_URL="redis://<host>:6379/0"
$env:KNOWI_REDIS_PREFIX="<environment>:"
$env:KNOWI_LOGIN_RECOVERY_KEY="<existing 64 hex characters>"
services/core-api/.venv/Scripts/python.exe -m uvicorn pymentor_core.app.asgi:create_app --factory --host 127.0.0.1 --port 8001
```

Для разработки API+Vite установите зависимости `npm ci` в `apps/web`, затем из
корня проекта запустите `tooling/dev/start.bat --mode configured` в Windows или
`tooling/dev/start.sh --mode configured` в Linux. Эти команды используют окружение
сервиса, требуют экспортированные переменные выше и запускают тот же ASGI factory.
Backend автоматически не выбирает SQLite и не читает локальную `.env`.

### Схема PostgreSQL

Ожидаемая ревизия — `c504_course_progress`, включая verified/email-change state
и разделение прогресса по курсам. Перед выпуском этой версии нужно проверить текущий
head целевой PostgreSQL и отдельно применить управляемую миграцию. Состояние c504 на
AlphaVPS этой локальной интеграцией не проверялось; production-данные не менялись.
Runtime-роль использует DML, отдельная migration-роль — DDL. При выпуске с новой схемой
оператор отдельно применяет проверенный migration image/command к явной цели, до старта API.
Read-only проверка 15.11.2026 подтвердила на рабочей `knowi_core` ревизию
`c503_dual_email_change`; новый runtime требует `c504_course_progress`.
Старый runtime отклоняет c504, поэтому этот переход требует остановки старых writers
и управляемого окна обслуживания, а не обычного смешанного canary. Порядок и ограничения:
[c504 cutover](../../infra/gitops/operations/README.md).

Команда миграции после выполнения preconditions:

```powershell
python -m pip install -r services/core-api/requirements-migration.txt
# Через защищённое окружение задать совпадающие KNOWI_MIGRATION_URL (asyncpg),
# KNOWI_MIGRATION_HOST, KNOWI_MIGRATION_DATABASE, KNOWI_MIGRATION_USER.
# Для c527/c528 на удалённой БД также задать KNOWI_MIGRATION_OWNER и
# KNOWI_MIGRATION_APP_ROLE. Логин миграции должен иметь право SET ROLE
# на постоянного владельца таблиц. См. ../../infra/gitops/operations/c528-cutover.md.
python -m alembic -c services/core-api/alembic-runtime.ini upgrade head
```

Это не команда импорта данных и не обязательный шаг текущего cleanup на AlphaVPS.
Не подменять runtime DSN migration credentials. Исторические revision IDs и проверенный
additive schema bridge сохраняются. Старые SQLite import/lab инструменты удалены;
актуальные данные PostgreSQL используются без повторного импорта.

### Worker и health

```powershell
python -m pymentor_core.app.worker --stats
```

`--stats` читает статистику без отправок. Обычный worker без этого флага обрабатывает
настоящий outbox; API не запускает sender автоматически. Пробные сценарии используют
синтетические данные и sinks. Telegram/SMTP credentials не копируются в тестовую среду.

`GET /health/live` — процесс жив; `GET /health/startup` — startup окончен;
`GET /health/ready` — доступны необходимые PostgreSQL schema/Redis. Ошибка зависимости
не включает SQLite fallback. Перед проверками отказов используется disposable среда.

## Образ, smoke и CI

```powershell
docker build -f services/core-api/Dockerfile --target runtime -t knowi/core-local:pg06 .
docker build -f services/core-api/Dockerfile --target migrations -t knowi/core-local-migrations:pg06 .
python tooling/ci/image_smoke.py
```

Один Dockerfile, без build-time DB profile. Runtime работает от UID10001, содержит Core
и материалы курса, исключает SQLite adapter/wrappers, БД, `.env`, Git/tests/lab и Alembic.
Migration tooling находится в отдельном target. Smoke поднимает только свой временный
Compose project (PG/Redis, без host ports/постоянных volumes), проверяет health → login/
cookie → PG write/read → logout → worker stats и убирает только свои ресурсы.

```powershell
python tooling/ci/check_single_runtime.py
python tooling/ci/check_single_runtime.py --image knowi/core-local:pg06
python tooling/ci/image_smoke.py
# Сервисные test groups требуют отдельные disposable PG/Redis по release.yml:
python tooling/ci/preview_checks.py
```

Группы: `tests/runtime_suites.json`. Required CI использует loopback57382 (DB/user
pymentor_test), Redis57381, внешнюю подготовку только именованных test DB и отдельные
процессы HTTP suites. Пустой/failed/skipped JUnit, недоступные сервисы и SQLite import/DSN
блокируют gate. Старые lab/import fixtures удалены; бизнес-проверки работают на PostgreSQL.

Canonical pipeline — `.github/workflows/release.yml`, candidate через PR в `Release`.
Checks обязательны; images/publish выполняются только разрешённым push в Release.
Успешный PR CI не означает merge или deployment. Для выпуска фиксируются source SHA,
runtime/migration digests и config revision; фактический rollout проверяется отдельно.

При обновлении VPS существующие даты завершения занятий сохраняются. Если в
фактическом источнике обновления найдутся завершённые занятия без `completed_at`,
заполните только их одним временем выпуска с часовым поясом, согласованным для
дня релиза по Москве. При импорте задайте это значение до вставки, пока видны
исходные пропуски: PostgreSQL сам подставляет время вставки, если поле опущено.
Перед включением чтений проверьте число заполненных пропусков и сохранность уже
имевшихся дат. Если пропусков нет, шаг заканчивается проверкой.

## Переключение browser activity

`PYMENTOR_ANALYTICS_BROWSER_PRESENCE_REQUIRED=1` требует
`PYMENTOR_ANALYTICS_LEGACY_READ_MODE=analytics`: Core откажется запускаться с
режимами `off` или `shadow`. Для calendar, summary, global admin и профиля
отсутствие проверенной Query-пары `browser_quality` / `browser_as_of` приводит
к 503; обновление quota также не начинается, если профиль недоступен. По
умолчанию значение `0` сохраняет Core-only поведение. `coverage=complete`
относится к Core transfer, а `browser_quality=unknown` и `browser_as_of`
не подтверждают полноту доставки событий браузера.

Успешный `GET /api/learning/analytics-catalog` возвращает заголовок
`X-Analytics-Browser-Presence-Required: true` только при включённом серверном
требовании и режиме чтения `analytics`. Его отсутствие запрещает frontend
отключать legacy visits. Заголовок сообщает о серверной политике, а не о
полноте данных или текущей готовности Query; ответ каталога остаётся `no-store`.

До включения обоих frontend-флагов `VITE_ANALYTICS_TRACKER_ENABLED` и
`VITE_ANALYTICS_LEGACY_VISIT_CUTOVER` включите это требование на всех Core
репликах и убедитесь, что Query и Processor используют включённую a010
projection с одинаковым RUN, завершённым backfill и готовой privacy-проверкой.
Проверьте опубликованный каталог и доступность Collector: при выбранном
cutover без версии каталога ни Tracker, ни legacy visit не записывают посещение.
Независимые флаги не обеспечивают атомарное переключение: deployment должен
отклонять несовместимую комбинацию, а на rollout проверять все реплики.
Для отката сначала отключите frontend cutover; сохраняйте серверное требование
и browser projection, пока старые страницы и кешированные сборки могут писать
только через Tracker. После cutover уже записанные browser days требуют
projection и не восстанавливаются простым возвратом legacy writer. При
недоступной projection оставляйте серверное требование включённым: отчёт
вернёт 503, пока смешанное чтение не восстановлено. a011 day flag не заменяет
a010.

### Локальный параллельный сбор

`PYMENTOR_ANALYTICS_LOCAL_COLLECTION_ENABLED=1` включает отдельный local-only
режим: grants и metadata каталога должны быть включены, legacy read — `off`,
write — `mirror`, delivery — `0`. Core DB URL должен указывать на числовой
loopback-адрес, а `PYMENTOR_ANALYTICS_LOCAL_EXPECTED_DATABASE` — на точное имя
базы. `PYMENTOR_ANALYTICS_LOCAL_INSTANCE_ID` задаёт стабильный UUID Core;
`PYMENTOR_ANALYTICS_LOCAL_SOURCE_KEY_FILE` указывает строго на
`services/analytics/.local/local-live/<dataset-id>/source.key` с 32 случайными
байтами в canonical base64url без padding. Core проверяет права на `local-live`,
dataset directory и файл, запрещает ссылки и hardlinks; на POSIX требуются
0700 для каталогов и 0600 для файла. Ключ отличается от grant key.
При старте и каждом запросе проверяются primary, имя DB и неизменённые функции
и trigger Core426; результат отсутствия проверки — отказ. Core426 события
не должны редактироваться или удаляться привилегированным оператором.

В этом режиме `GET /api/learning/analytics-catalog` добавляет
`X-Analytics-Collection-Mode: parallel`. Это подтверждает локальную политику,
не доступность Analytics и не полноту данных. Заголовок legacy cutover остаётся
независимым. Клиент обязан отдельно подтвердить данные и catalog version.

Внутренний `GET /api/internal/analytics/deletions` доступен только с loopback.
Параметры `instance_id` и одноразовый `nonce` — canonical UUID; заголовок
`X-Analytics-Request-MAC` — lowercase hex HMAC-SHA256 ключом источника.
Подписываются байты домена `pymentor:analytics:core-deletions:request:v1` с
завершающим NUL и canonical JSON `{version:1,instance_id,nonce}` (UTF-8,
отсортированные ключи, разделители `,` и `:`). Ответ содержит `version`,
`instance_id`, `nonce`, `as_of`, `events` и `mac`. Каждый event содержит
только `subject_ref` и `deleted_at`; время UTC с шестью знаками и `Z`. MAC
вычисляется по тем же правилам над ответом без `mac`, с доменом
`pymentor:analytics:core-deletions:response:v1` и NUL. Снимок читается из
primary в новой read-only READ COMMITTED транзакции; больше 1024 событий,
replica и ошибка проверки закрываются 503. На каждом ответе `no-store`.
Это источник для Analytics privacy guard, а не подтверждение purge или замена
независимого журнала; чтения и записи Analytics должны проверять guard до и
после операции.

Local P1 publisher запускается из `services/core-api` командой
`python -B -m pymentor_core.modules.analytics_delivery.local_catalog --course-id <course>`.
Нужны одинаковые для Core и publisher `PYMENTOR_ANALYTICS_LOCAL_DATASET_ID`
(UUID), `PYMENTOR_ANALYTICS_LOCAL_CATALOG_NAMESPACE`,
`PYMENTOR_ANALYTICS_LOCAL_CATALOG_URL` (числовой loopback HTTP),
`PYMENTOR_ANALYTICS_LOCAL_CATALOG_PRODUCER_KEY_SHA256` (SHA256 raw public key),
`PYMENTOR_ANALYTICS_PRODUCER_ID`, `PYMENTOR_ANALYTICS_PRODUCER_ISSUER` и
`PYMENTOR_ANALYTICS_PRODUCER_KID`. Только publisher также получает private
`PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE`. Receipt и marker хранятся в отдельном
private каталоге dataset/namespace; marker связывает также Core instance,
endpoint и identity ключа. Marker отражает указанную оператором конфигурацию,
а не доказывает саму идентичность базы Collector. Pending сохраняется до HTTP;
после неизвестного ответа publisher сначала сверяет тот же fingerprint и не
повторяет POST без подтверждённого `absent`. Synthetic CLI не изменён.

## Почта и состояние

Email links: `PYMENTOR_EMAIL_TOKEN_SECRET` (не менее32байт),
`PYMENTOR_EMAIL_PUBLIC_URL` (HTTPS), `PYMENTOR_SMTP_HOST/PORT/FROM/USERNAME/PASSWORD/STARTTLS`.
Секреты хранятся вне Git. Для loopback SMTP sink разрешены локальный HTTP и STARTTLS0;
удалённый SMTP требует STARTTLS. Общие rate limits находятся в Redis, отказ не включает
process-local limiter. Формат подписанных ссылок/сроки и проверка verified сохраняются.

`PYMENTOR_STATE_DIR` по умолчанию `services/core-api/.local/state`, в образе `/app/data/core-api`.
Для worker/Runner с локальными locks каталог должен быть writable; локальные файлы не
заменяют PostgreSQL transactional state и Redis. Логи/locks не включаются в image.

## Работа с уже существующими данными

Cleanup не меняет PostgreSQL tables/roles, Secret values/prefixes, данные и действующие
сессии. Приватные архивы и PostgreSQL backup сохраняются отдельно от исходников;
перенос из них не входит в очистку. Старые import/recovery tools удалены. Снятие template с desired
state не является разрешением удалить фактический PVC/архив.

Backup/restore текущего PostgreSQL выполняются по существующему операторскому runbook
и политике CORP-426; это отдельная операция с проверкой целевой БД и сохранением новых
записей. Возврат runtime DSN к старому SQLite snapshot не является recovery.

Read-only baseline AlphaVPS13.11: `knowi01`, namespace`pymentor`, PostgreSQL revisionc503,
Redis PING/readiness200; Core/worker используют Secret`preview-migrated-runtime`, без
SQLite mounts. Подробные checks и ограничения — CORP-519 в Jira. Данные/секреты не
дублируются в документации. SQLite adapter, Passenger/cron wrappers и старые build profiles удалены;
course-local/Runner сохраняются до своих отдельных переходов.
