# Перестройка монорепозитория — 14.11.2026

## Контракт

Требование пользователя: корневых `backend/`, `postman/`, `data/` быть не должно; код, данные и инструменты должны принадлежать своему сервису. Сохранить текущие материалы, пользовательские правки, старую базу и настройки. Продолжить работу в той же основной папке; без commit/push/deploy.

Целевая структура: `apps/web`, `services/core-api`, `services/runner-pool`, `courses`, `infra`, `tooling`, `docs`. Метаданные Git/Codex остаются в корне.

- `backend/content` → `services/core-api/content`: совместимый import `content`, без корневого backend.
- Postman → `services/core-api/postman`.
- Локальные locks/state → `services/core-api/.local/state`; прежние `.env` и SQLite → `.local/legacy` того же сервиса, без открытия SQL и изменения байтов.
- `frontend` → `apps/web`; локальная галерея → `apps/web/devtools/lesson-visual-demo`, URL `/backend/demo` сохраняется.
- `deploy` → `infra`; общие CI/authoring/dev команды → `tooling`; специализированные Core/web инструменты расположены у владельцев.
- Корневые requirements/start wrappers архивируются; Core имеет собственные requirements, virtualenv и инструкции.

Контракты API, course_id, source_file, content_version, состояние PostgreSQL/Redis и существующие container/PVC mount paths сохраняются. Изменение filesystem layout не является миграцией production-данных.

## Снимок и операции

До изменений: ветка `codex/local-project-integration-20261114`, значительные ранее существовавшие незакоммиченные изменения. Полный предыдущий CI: 549 проверок +11 subtests, frontend31, оба образа и запуск курсов проверены.

Свежий архив: `C:\Project Python\_archives\Pymentor_platform-monorepo-20261114-01a1da97`. `before/` и `snapshot.json`: 4882 файла, 198,1 МиБ, хеши копий проверены, ошибок нет. Зависимости/Git/cache исключены, Git сохраняется на месте. `moves.json` фиксирует выполненные переносы. Защищённый пустой pytest cache вынесен целиком в `retired/backend-cache-container`, без изменения ACL.

Создан новый service virtualenv `services/core-api/.venv`; установлены текущие runtime/migration requirements, pytest9.1.1 и httpx0.28.1. После реального запуска API/web старое корневое окружение перенесено в `retired/root-venv` архива. Сохранились только метаданные инструментов в скрытых корневых каталогах; старый защищённый `.pytest_cache` игнорируется Git.

## Разделение работ

- Root: физические переносы, `tooling/`, `infra/`, `.github/`, root README/ignore/guide paths, интеграция и окончательная проверка.
- `/root/monorepo_core_impl` (выбран Terra/medium): `services/core-api/**`, кроме приватных данных/окружения; source/tests/Docker/fixtures/service docs.
- `/root/monorepo_frontend_map` (выбран Luna/high, продолжение): `apps/web/**`, `courses/*/frontend/**`; импорты, Vite/TS, Docker, публичный путь галереи.
- `/root/monorepo_path_map`: read-only карта Python/runtime, завершена; продолжение заблокировано неучтённым worker в policy ledger, поэтому реализация назначена новому агенту после подтверждения terminal state. Исходники старым разведчиком не менялись.

Работники не изменяют чужие зоны, policy, Git или удалённую инфраструктуру. Root собирает результат и проверяет интеграционные границы.

## Приёмка

Нужны: отсутствие прежних корневых каталогов/файлов; хеши перенесённых env/SQLite совпадают; no stale active backend/content/frontend/deploy/scripts filesystem paths; Core импортируется из собственного каталога; оба пакета курсов загружаются; web/root/platform/demo/authoring пути работают; CI/tests/images собираются из новой структуры; действующие инструкции и service ownership понятны. Исторические документы не переписываются как описание сегодняшнего состояния.

Статус: локальная перестройка завершена. Прежних корневых backend/frontend/postman/data/deploy/scripts/tools, .env, .venv, requirements.txt и start.* нет. README описывает владельцев и актуальные команды. Рабочие каталоги: apps, services, courses, infra, tooling, docs; скрытые метаданные остаются в корне.

## Выполненные проверки

| Проверка | Фактический результат |
| --- | --- |
| Полный `tooling/ci/preview_checks.py` | exit0: 275 unit +48 PostgreSQL/Redis +219 HTTP +4 release-contract +4 Runner =550; дополнительно11 subtests |
| Все `apps/web/src/**/*.test.mjs` из apps/web | 50 passed; дополнительно candidate TypeScript и TaskResult SSR passed |
| Авторские инструменты | Python21, lesson structure15, visuals5 passed; проверка367 модулей в build прошла |
| Web build | TypeScript/Vite для `/` и `/platform/` прошли; известные приватные маркеры отсутствуют в894 assets каждой сборки |
| Vite HTTP | 14 маршрутов200: каталог, оба курса, authoring, demo и demo/main.tsx для обоих base;56 проверок public/private: public200, private403 |
| Core Docker | runtime image собран; image gate подтверждает отсутствие SQLite adapter и DDL tooling |
| Core image HTTP | readiness, регистрация, Redis-вход, PostgreSQL read/write профиля, оба каталога по9 tracks, logout прошли |
| Web Docker | финальный образ собран; страницы и галерея200,24 JS/CSS ресурса200 |
| Compose из infra | migration image, Core, smoke и worker запущены успешно; одноразовый Compose project удалён |
| Целостность | SHA-256 совпали для3 прежних env/SQLite,2 Postman-файлов,1101 файла серверной части/идентичности курсов,552 файла прежнего content и .gitattributes |
| Методика/skills | механически обновлены пути;37 обновлённых файловых ссылок существуют;13 skills валидны; lesson context находит источники по новым путям |

Первые прогоны выявили устаревшее ожидание root data в тесте и два неверных parents в regression-тестах; исправлены. HTTP проверка также выявила неверный demo rewrite для `/platform/`; исправлен без изменения fs deny. Браузерный render/console не проверены: установленный Playwright CLI недоступен (ENOTCACHED), запасной Windows browser tool остановлен политикой URL. HTTP/SSR/build проверки не выдаются за визуальную приёмку.

## Локальный запуск и сохранность

Оставлены запущенными `tooling/dev/run.py --mode lab` (Core18001 + Vite5173) и просмотр сборки на18519. Readiness и страницы повторно дали200. Логи принадлежат приложениям в `.local/logs`. Новый launcher по умолчанию использует переносимый `configured` режим; `lab` явно выбирает существующий Windows-стенд.

Контейнеры одноразового CI `knowi-monorepo-01a1da97-{pg,redis}` удалены по проверенным ID, именам и loopback-портам вместе с их временными томами. Существующие mc460 PostgreSQL/Redis и k3d сохранены. Оригинальные базы не открывались. Настройки/базы, зависимости и build output исключены из Git; три авторских JPG-образца галереи явно включены, чтобы clone не терял исходники.

Исходные материалы, manifests, course_id и content_version сохранены. В курсах менялись только frontend import paths к общей библиотеке. Исторические отчёты не переименованы в описание нового состояния. Логи, команды и JSON результатов сохранены в `validation/monorepo-final/` архива вместе с этим отчётом.

## Оставшиеся отдельные вопросы

- Архитектурная проверка по-прежнему сообщает одну прежнюю связь `modules.learning.api.service:19 -> adapters.legacy_runner.python`. Перестройка файлов не меняет механизм Runner.
- Вне canonical runtime suite исторический `test_course_package_files.py` по-прежнему даёт7 setup errors: frozen MC2.1 builder отклоняет `chapter_04/block_13/manual.py` (`retired source changed`). Все552 исходных файла content побайтно совпадают со снимком до перестройки: это прежнее расхождение, а не потеря при переносе. Frozen hashes и авторский код не переписывались ради зелёного теста.
- До production-выпуска нужно проверить live Alembic head и путь действующего Argo Application; новый путь — `infra/gitops/preview`. Кластер/production этой работой не обновлялись.
- Обе предыдущие архитектурные работы сохранены в локальной интеграции; детали первоначальной сверки144 файлов PR7 — в LOCAL_PROJECT_20261114.md. PR7 оставался draft/open на момент той проверки. Эта перестройка не выполняет commit, push, merge PR или deployment.
