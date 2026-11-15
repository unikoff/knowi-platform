# Локальная интеграция проекта — 14.11.2026

Цель: понятная рабочая копия `C:\Project Python\Pymentor_platform`, сохранение пользовательских изменений, согласованная локальная версия SQLite retirement + multicourse.

## Источники и Git

- «Заложить основу удаления SQLite в Jira»: `01a1cce8-9bb4-7df2-b755-e08f4a8a102b`, worktree `postgres-redis-sprint`, PR [№7](https://github.com/unikoff/knowi-platform/pull/7), head `b2f0e8ab42e481be40f8d6a4f3e0bd3d69cb73da`.
- «Tutot»: `01a1b143-b08b-7803-b122-a1646852f289`, согласованный перенос уже выполнен в основной каталог; `docs/multicourse/main-import-report.md` содержит исходный отчёт.
- Все 144 изменённых пути PR7 сравнены с основной папкой до текущих исправлений: совпали, включая отсутствие удалённых исходников.
- GitHub на момент проверки: PR7 открыт как draft, `merged=false`; base Release `fa35703af60f922a353a32037412ec1feb1699ae`. Локальное объединение не является слиянием PR на GitHub.
- Исходный HEAD основной папки: `6c70393696f412fc71e059680e601068b6203466`, detached. Создана рабочая ветка `codex/local-project-integration-20261114` на том же коммите, файлы и индекс не сбрасывались. Новых коммитов и push нет.

## Резервная копия и очистка

Архив: `C:\Project Python\_archives\Pymentor_platform-20261114-01a1da97`.

- `before/` + `snapshot.json`: 19 888 файлов, 1182,0 МиБ; каждый записанный файл проверен по SHA-256, ошибок копирования нет. Git и восстанавливаемые зависимости/cache исключены из снимка; оригинальные Git, `.venv` и `frontend/node_modules` сохранены на месте.
- `removed/`: 67 групп старых сборок, логов, Stage-проектов и устаревших обёрток перенесены из основного дерева. `cleanup-moves.json` — точный журнал.
- `remaining-output/`: ещё 122 исторических элемента `output` перенесены без удаления. `remaining-output-moves.json` — журнал.
- `backend/test.db`, оба существовавших `.env` и текущие исходники сохранены. SQLite-файл копировался как байты; SQL-подключение не создавалось.
- `frontend/backend/demo/build.log` был открыт другим процессом и оставлен. Исходники галереи, её assets и активно меняющаяся общая библиотека не переносились. Старые TSX пути занятий нужны текущему authoring preview и сохранены.
- `docs/stage-runs` сохраняет итоговые MD/JSON-отчёты. Тяжёлые подкаталоги конкретных проверок доступны в архиве.

## Найденные и исправленные стыки

1. Multicourse-перенос добавил SQLite-фикстуру в `test_multicourse_notification_routing.py`; source gate воспроизвёл нарушение. Сценарии переведены на существующую PostgreSQL/Redis-фикстуру и включены в обязательный HTTP-набор CI.
2. Frontend Dockerfile собирался с контекстом `frontend/`, поэтому TypeScript не находил `courses/...`. Ошибка воспроизведена настоящей Docker-сборкой. Контекст перенесён на корень, добавлен отдельный allowlist `frontend/Dockerfile.dockerignore`, исправлены GitHub workflow и локальный image builder.
3. Core image не включал `courses/`. Добавлены пакеты курсов и соответствующий build allowlist. Без них прогрев курсов при старте не мог работать.
4. `passenger_wsgi.py` ссылался на уже удалённые модули. Файл перенесён в архив, source gate теперь явно запрещает его возврат.
5. Стандартные launchers запускали API8001 при Vite proxy18001. Launchers теперь явно задают target8001; отдельный lab launcher сохраняет18001. Vite cache больше не привязан к `C:/Temp`.
6. Статический lab web server по умолчанию читает актуальный `frontend/dist`, а запросы записывает в игнорируемый `output/`. `.gitignore` исключает новые cache/build/log артефакты и сохраняет для Git методики и необходимые замороженные multicourse fixtures.
7. README заменён актуальной картой каталогов и командами запуска. Core runbook согласован с courses/, переменной PYMENTOR_COURSE_TRANSPORT и обязательной схемой c504; несуществующий старый smoke.ps1 заменён действующим scripts/ci/image_smoke.py. Историческая локальная инструкция Passenger перенесена в архив.
8. Установленный локально PyYAML отсутствовал в runtime requirements, поэтому внутри Core image возникал `ModuleNotFoundError`. Добавлена проверенная версия 6.0.3; корневой requirements теперь ссылается на канонический runtime-набор вместо прежнего Passenger/SQLite списка.
9. Старые регрессионные fixtures согласованы с async HTTP-обёртками, content-портом, необязательным `course_id`, публичной проекцией результатов проверки и новым Alembic head. Проверки данных, rollback, гонок, скрытия judge-значений и полей ответа сохранены. Проверки email/login-миграций теперь подтверждают присутствие миграции в цепочке текущего head вместо привязки к устаревшему c503.
10. Обнаружена потеря request UoW в сохранённой совместимой HTTP-обёртке `/me`. Передача `composition_root.for_request(db).uow` восстановлена именно в этой обёртке; раздельные сессии чтения и записи практики сохранены. Изменение подтверждается port/UoW-регрессией. Serving asyncpg/Redis-путь не переключается на тестовые адаптеры.

## Проверки

Завершено. Единый заключительный запуск `python scripts/ci/preview_checks.py` вернул exit 0.

| Проверка | Результат |
| --- | --- |
| Обязательный unit/contract набор | 274 passed + 11 subtests |
| Реальные PostgreSQL/Redis runtime suites | 48 passed |
| Обязательные HTTP suites, 25 отдельных процессов | 219 passed |
| Release contracts + Runner/security contracts | 4 + 4 passed |
| Ошибки/пропуски обязательного набора | 0 / 0 |
| Дополнительная проверка login migration chain | 5 passed |
| Раздельный прогресс двух пользователей в двух курсах, дубликаты/ревизии/старый writer | 1 passed на существующей синтетической лаборатории |
| Frontend: API client, auth/email/session coordination, analytics, course routes и metadata | 31 passed |
| TypeScript/Vite build для `/` и `/platform/` | PASS; итоговый root dist повторно собран после завершения параллельных UI-правок |
| Core и frontend Docker build с корневым контекстом | PASS |
| Точный итоговый Core image: health/startup/ready, Redis login/logout, PostgreSQL profile write/read, оба каталога | PASS, read-only container, только тестовые данные |
| Source/config retirement guard и runtime image guard | PASS |
| Course packages, content_version, карты файлов | PASS, 1893 файла, два курса по 9 треков |
| Known-private-marker scan актуального root dist | PASS, 888 текстовых assets, 861 JS, source maps нет |
| Локальный 18519 отдаёт текущий frontend/dist; оба каталога API | HTTP200; SHA-256 index.html совпал |
| `.env`, `backend/.env`, `backend/test.db` относительно снимка до очистки | SHA-256 совпали |
| Whitespace/error check адресно изменённых файлов | PASS |

Весь обязательный набор: 541 pytest-проверка + 8 release/Runner-проверок = 549, дополнительно 11 подслучаев. Расширенный набор в ходе работы выявил реальные несовместимости объединения; после их устранения полностью повторён единым CI-скриптом. Финальные тесты запускались без ограничений sandbox на временные каталоги/дочерние процессы, которые мешали первому локальному запуску.

Финальный Core image ID: `sha256:baafdab8119d9ea60ba23f923e5235c18e3a9c1c2a1fe27965ce02c7ab8a085a`. HTTP-smoke сохранён как `image-http-smoke-accepted.json`; основной лог — `required-ci-complete.log`. Локальный CI выполнен на Windows/Python3.14; Linux-образы отдельно собраны и Core проверен через настоящий HTTP. Новый GitHub Actions run не создавался, поскольку push не выполнялся.

Сохранились предупреждения Starlette/httpx и большого landing chunk. Общий `git diff --check` видит прежние пробелы в строках пользовательских TSX уроков; эти файлы ради whitespace не переписывались. Архитектурный checker имеет отдельное прежнее замечание ниже.

Временные контейнеры `knowi-cleanup-01a1da97-{pg,redis,api}` и тестовые тома удалены. Существующие лабораторные контейнеры и k3s не менялись. Локальные API 18001, web 18519 и Vite 5173 оставлены работающими.

После остановки работы параллельного UI-чата его последние сборки/скриншоты и старые multicourse cache builds вынесены в `late-artifacts/` и `builds/` архива. В рабочем дереве остаются исходники галереи и её действующий лог Vite. Итоговый объём без Git/установленных зависимостей и Python caches: около 198,1 МиБ и 4882 файлов против 1182,0 МиБ перед очисткой. Разница вынесена из проекта, а не объявлена освобождённым местом на диске.

Доказательства скопированы в `validation/` внешнего архива. Материалы курса, credentials и SQLite не публиковались.

Логи текущей проверки: `C:\Users\Unik\.codex\visualizations\2026\11\14\01a1da97-2aab-7f11-a0c5-d766474ad952`.

## Известная граница

Архитектурный checker обнаруживает прежний прямой импорт `modules.learning.api.service:19 -> adapters.legacy_runner.python`. Он существовал до обоих переносов и не связан с SQLite. Его устранение требует отдельного изменения Runner-контракта; архитектурный PASS не заявляется. Работающий Runner и его проверки сохраняются.

VPS, production-данные, GitHub PR и удалённые ветки не изменялись. Этот результат — локальная интеграция, а не выполненный выпуск.

## Перед публикацией

Рабочая копия сохранена на ветке `codex/local-project-integration-20261114`. Локальные изменения ещё не закоммичены; PR7 на GitHub остаётся draft/open и не слит. Новые commit/push, удалённый CI и выпуск выполняются отдельным действием. Для запуска этой версии на целевой PostgreSQL обязателен проверенный Alembic head c504; факт его применения на production здесь не устанавливался.
