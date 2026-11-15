# knowi — учебная платформа

Рабочий монорепозиторий: `C:\Project Python\Pymentor_platform`. Core использует PostgreSQL и Redis. Каталог — на `/`, продукты — `/backend` и `/backend_copy`.

## Структура

```text
apps/
  web/                      React-оболочка, кабинет и авторизация
services/
  core-api/                 FastAPI, worker, миграции, контракты, Postman
  runner-pool/              исполнитель ученического кода
courses/
  registry.json             реестр продуктов
  backend/                  исходный курс
  backend_copy/             независимая копия курса
  shared/                   общие учебные UI, demo, backend helpers и AI-инструкции
infra/                      Helm, GitOps, локальные стенды, hosting
tooling/                    общие dev, CI и авторские проверки
docs/                       методика, архитектура, записи обслуживания
```

Каждое приложение хранит свои зависимости, Dockerfile, тесты, инструменты и документацию у себя. Локальные данные/логи/результаты — в его `.local/`, зависимости — в его `.venv/` или `node_modules/`; всё это исключено из Git. `tooling/` координирует компоненты.

| Что менять | Где |
| --- | --- |
| API, PostgreSQL/Redis, бизнес-правила | [services/core-api](services/core-api/README.md) |
| Postman и API-примеры | `services/core-api/postman/`, `services/core-api/contracts/` |
| Оболочка платформы | `apps/web/src/` |
| Общие учебные компоненты и визуалы | `courses/shared/ui/` |
| Галерея `/backend/demo` | `courses/shared/demo/` |
| Теория и практика курса | `courses/<course_id>/backend/{plans,practice}/` |
| Страницы занятий и лендинги курса | `courses/<course_id>/frontend/` |
| Развёртывание | [infra](infra/README.md) |
| Методика уроков | [START.md](docs/make%20les%20stage/START.md) |

У каждого курса собственные `course.yaml`, `personal.settings.yaml`, `backend/` и `frontend/`. Вложенный `courses/backend/backend/` — серверная часть продукта; корневого приложения `backend/` больше нет. Имена продуктов и URL сохранены.

Core получает каталог через файловый адаптер и протокол пакета курса. Каталог, правила прогресса и материалы принадлежат `courses/<course_id>/backend/`, страницы — `frontend/`; реестр Vite генерируется в `apps/web/.cache/course-registry/`. Исторические материалы находятся в `courses/<course_id>/archive/legacy-content/`. Дублирующих деревьев `services/core-api/content/` и `apps/web/src/lessons/` больше нет.

## Зависимости и запуск

Из корня, Python 3.12+ и Node/npm:

```powershell
python -m venv services/core-api/.venv
services/core-api/.venv/Scripts/python.exe -m pip install -r services/core-api/requirements-dev.txt
npm --prefix apps/web ci
```

В Linux Python окружения — `services/core-api/.venv/bin/python`. Общего корневого `requirements.txt` нет.

На этом компьютере подготовлена одноразовая Windows-лаборатория PostgreSQL/Redis `mc460-20261113-2048`. При работающем Docker Desktop:

```powershell
tooling/dev/start.bat --mode lab
```

Web: http://127.0.0.1:5173/, готовность API: http://127.0.0.1:18001/health/ready. Режим `lab` использует этот тестовый стенд; настоящая доставка SMTP/Telegram отключена. Его приватные настройки находятся вне репозитория в `%LOCALAPPDATA%/Pymentor/c225/`.

Для другого окружения экспортируйте `DATABASE_URL`, `REDIS_URL`, `KNOWI_REDIS_PREFIX`, `KNOWI_LOGIN_RECOVERY_KEY` по [runbook Core](services/core-api/README.md), отдельно примените управляемые миграции и запустите:

```powershell
tooling/dev/start.bat --mode configured
```

Linux: `bash tooling/dev/start.sh --mode configured`. Это режим по умолчанию: Core8001, web5173. `.env` автоматически не загружается. `services/core-api/.venv/Scripts/python.exe tooling/dev/run.py --mode lab --check` проверяет зависимости. Дополнительные параметры: `--api-port`, `--web-port`, `--base /platform/`. Ctrl+C останавливает запущенные командой процессы; логи — в `.local/logs/` соответствующего приложения.

Сборка и просмотр web с API лаборатории:

```powershell
npm --prefix apps/web run build
node tooling/dev/multicourse_web.mjs --port 18519
```

`apps/web/dist/` генерируется из исходников и исключён из Git. Этот просмотр проксирует API на18001.

## Проверки и выпуск

```powershell
services/core-api/.venv/Scripts/python.exe tooling/ci/check_single_runtime.py
services/core-api/.venv/Scripts/python.exe -B tooling/ci/preview_checks.py
npm --prefix apps/web run test:release
npm --prefix apps/web run build
services/core-api/.venv/Scripts/python.exe tooling/ci/image_smoke.py
node tooling/courses/test_lesson_structure.cjs
services/core-api/.venv/Scripts/python.exe -B -m unittest tooling.courses.test_lesson_tools
```

Полный `preview_checks.py` требует одноразовые PostgreSQL/Redis на57382/57381 и сам готовит тестовые базы. На рабочих базах его не применяют. CI: [release.yml](.github/workflows/release.yml).

Docker-сборки выполняются **из корня монорепозитория**: Core читает пакеты курсов через файловый адаптер; web включает только браузерные части.

```powershell
docker build -f services/core-api/Dockerfile --target runtime -t knowi/core:local .
docker build -f apps/web/Dockerfile -t knowi/web:local .
```

Выпуск: `Release` → GitHub Actions → GitOps. Перед выпуском нужно учесть смену пути Argo Application на `infra/gitops/preview` — [подробности](infra/README.md). Локальная перестройка не обновляет действующие приложения кластера.

## Данные и архив

Прежние `.env`, `backend/.env`, `backend/test.db` перенесены без изменения байтов в `services/core-api/.local/legacy/{root.env,backend.env,test.db}`. Core их не читает. Локальный writable state — `services/core-api/.local/state/`; контейнерный/PVC путь `/app/data/core-api` сохранён.

Архивы вне проекта:

- `C:\Project Python\_archives\Pymentor_platform-20261114-01a1da97` — первоначальная очистка.
- `C:\Project Python\_archives\Pymentor_platform-monorepo-20261114-01a1da97` — снимок перед перестройкой, SHA-256, журнал переносов и проверок.

Результат перестройки: [отчёт](docs/maintenance/MONOREPO_RESTRUCTURE_20261114.md).
Текущая подготовка выпуска и оставшиеся проверки: [release readiness](docs/maintenance/RELEASE_READINESS_20261115.md).
[Первый отчёт](docs/maintenance/LOCAL_PROJECT_20261114.md) описывает состояние до переноса каталогов.
