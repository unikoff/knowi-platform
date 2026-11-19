# Main Core: восстановление локального runtime 17.11.2026

Актуальная проверка 17.11.2026,16:31UTC: Core18041 — PID26960,
Vite5173 — PID16272; новый локальный Analytics Collector/Query/Processor/Privacy
18042–18045 — PID6560, отдельная authority18046 — PID21040. Tracker, Reports и
Personal measurements включены; legacy cutover выключен. Web proxy Collector и
Query направлен на18042/18043. Core grants/catalog включены в проверенном local
parallel режиме; delivery/proof выключены, legacy read off/write mirror.
Основной Core PG/Redis не заменены и Core DDL не выполнялся. Отдельный QA ученик
создан обычным HTTP с отключённым SMTP. Локальный личный отчёт проверен в
браузере с настоящими B2 фактами, включая reload; подробности и ограничения в
`local-personal-live.md`, Jira CORP-92/comment10355. Runtime оставлен запущенным.
Ниже сведения прежних OFF запусков сохранены как история.

После перезагрузки восстановлен существующий Core на `127.0.0.1:18041` в checkout `feach/Analitick`, HEAD `f787672ff2639434f0be331d05def5d8f31b3c1e`. Это локальный runtime, не production и не Analytics cutover.

- По подтверждённым root идентификаторам запущены только сохранённые Core PostgreSQL `cb0ddbbd...` и Redis `7cb055c7...`; их loopback порты соответственно `57382` и `57381`. Контейнеры и тома не пересоздавались.
- Запущен существующий `services/core-api/.local/role-admin/run_api.ps1` из каталога Core. Для дочернего процесса явно установлены delivery/grants/snapshot proof/catalog metadata `0`, legacy read `off`, legacy write `mirror`. Секреты и настройки подключения в этот отчёт не записаны.
- 08:41 MSK: launcher PowerShell PID `7624`, venv Python PID `13440`, listener `127.0.0.1:18041` PID `16060`. PID действительны только для текущего сеанса.
- `/health/ready` — HTTP 200; `/api/learning/tracks?course_id=backend` — HTTP 200; `/openapi.json` — HTTP 200, Analytics grant/catalog routes отсутствуют. Это не проверка входа пользователя или интерфейса.
- Первая попытка launcher PID `17744` завершилась до запуска приложения из-за некорректной передачи пути с пробелом. Перед повтором отсутствие процесса подтверждено, повтор передал путь в кавычках. Приватные логи и текущая запись запуска находятся в `.local/role-admin`.

Миграции, DDL, seed, сбросы, flush и отправка уведомлений не выполнялись. Проверка лишь подтверждает доступность readiness и каталога; сохранность всех пользовательских данных и браузерный сценарий этой проверкой отдельно не подтверждаются.

## Повторное восстановление после browser hold

17.11.2026 около 09:43 MSK: после остановки прежнего listener процессом root повторно запущен тот же private launcher с теми же явными флагами. Новый launcher PID `25644`, Python listener PID `23952` на `127.0.0.1:18041`; readiness и backend tracks вернули HTTP 200. До запуска `netstat` не показывал listener на 18041; старый launcher PID `7624` оставлен без изменений. PG и Redis уже работали. Миграции и изменения схемы не выполнялись.

## Восстановление после второго reboot

17.11.2026, boot 11:05:23 MSK: все прежние host процессы завершены и Docker
Linux pipe отсутствовал. Docker Desktop запущен из фактического user-local
install root, скрыто. Docker29.9.2; exact IDs/loopback ports/volume names трёх
сохранённых PG/Redis контейнеров сверены до запуска. Все три healthy, retained
схемы и данные не изменялись. Остановленные K3s и чужие контейнеры не запускались.

Основной Core: launcher25020, listener22292/18041. Основной frontend:
PID26248/5173, четыре Analytics flags=false. Readiness, `/backend/` и proxied
backend tracks вернули HTTP200. Это новый сеанс, старые PID выше исторические.
Source publisher остаётся отдельным default-disabled процессом; основной
runtime его не запускает. Никаких миграций/seed/flush/уведомлений.
