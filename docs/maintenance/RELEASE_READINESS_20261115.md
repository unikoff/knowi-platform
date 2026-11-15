# Проверка локального кандидата — 15.11.2026

**Обновление:** управляемая выкладка выполнена15.11.2026; см. [production-отчёт](PRODUCTION_RELEASE_20261115.md).
Ниже сохранены результаты локальной подготовки до commit/push и production-операций.

Статус на момент локальной проверки: **локальный кандидат проверен и готов к управляемой выкладке**.
Ветка `codex/local-project-integration-20261114`; прежние пользовательские правки сохранены.
Commit, push, публикация образов, production DDL и изменение Secrets не выполнялись.

## Исправления и согласование

- Интегрирована новая изоляция курсов из чата Tutot. Core использует протокол каталога;
  материалы и frontend принадлежат пакетам курсов, общие UI/demo/backend/AI — `courses/shared/`.
  Дубли `services/core-api/content` и `apps/web/src/lessons` удалены с сохранением архива.
  Активные README и Docker contexts приведены к фактической структуре.
- Learning обращается к Runner через practice port. Файловый landing catalog вынесен
  из modules в adapter; архитектурная проверка теперь обязательна в CI.
- Package-builder сравнивает все текущие байты независимых пакетов, не применяет frozen
  hashes к живым материалам и не перезаписывает изменённое непустое назначение.
- Исправлена API-регрессия после нового catalog protocol: прогресс вычисляется по приватной
  политике исходного каталога, которая не попадает в публичный DTO. API golden не изменён.
  Synthetic fixtures явно задают политику; прежние assertions сессий/ревизий сохранены.
- CI дополнен backup restore, frontend contracts/build/private markers и актуальными
  Runner/builder/projection/migration tests. Image smoke удаляет свои volumes.
- В базе Nginx найден critical CVE-2026-4738: Web Dockerfile устанавливает Alpine zlib
  >=1.4-r0. Повторные critical scan и HTTP smoke исправленного образа прошли.
- Read-only проверка VPS выявила, что текущая DB NetworkPolicy не допускает отдельный
  мигратор. Пакет теперь содержит узкое временное правило: namespace и SHA migration Pod,
  только TCP57152. Job получает соответствующую метку; production policy не применялась.
- Реальная репетиция показала, что paused weighted Rollout сохранял stable pod при
  replicas=0. Процедура использует foreground-удаление старого Rollout под maintenance,
  после подтверждённого отключения Argo sync, до backup/DDL.

## Фактические проверки

| Проверка | Результат |
| --- | --- |
| Полный `preview_checks.py` на WSL2GB | Exit0: **319 unit +5 backup guards +50 runtime +219 HTTP =593 pytest tests**, 27 subtests;25 HTTP groups; обязательных skips нет |
| Финальные CI artifact contracts / Runner Pool | **7 +4 unittest PASS** |
| Архитектура Core |130 файлов,1036 import edges, **0 violations /0 errors** |
| Frontend release contracts | **107 Node tests**, без skips; TypeScript candidate и TaskResult SSR PASS |
| Web build `/` и `/platform/` | PASS;1794 assets проверены на8 известных приватных маркеров; совпадений и source maps нет |
| Core/migrations/Web/Runner Docker images | Все4 собраны; local Scout1.27.0: **0 critical findings** в каждом |
| Compose smoke на2GB | Миграция, health, Redis auth/cookie, PostgreSQL write/read, logout, worker и отсутствие SQLite/DDL tooling в serving image — PASS |
| Финальные image HTTP checks | Core: health/auth/profile/оба каталога/logout PASS; Web после zlib patch:5 маршрутов и24 assets PASS |
| Linux/Python3.12 Runner resources | **11 tests PASS**: CPU/memory/output/process/timeout limits, восстановление после ошибок |
| PG16 c503→c504 на2GB | **2 tests PASS**: lock timeout второй таблицы откатывает весь DDL/head; retry, прежние ID/timestamps/revisions, изоляция курсов, повторный upgrade и отказ downgrade |
| pg_dump/restore/upgrade на2GB | PASS: c503 dump восстановлен в отдельную UUID DB; hashes/IDs/timestamps сохранены; c504 и Backend Copy проверены в обеих БД; собственные DB удалены |
| Сохранность курсов | Оба:310 lessons/204 tasks; stable IDs/revisions/private hashes совпадают с baseline перед изоляцией |
| Приватные данные / Postman |3 исходных приватных файла и2 Postman-файла побайтово сохранены; SQLite не открывалась как БД |
| GitOps artifacts | Kustomize/server dry-run PASS; новые maintenance Deployment/Service/3 IngressRoutes и migration Job приняты без применения |
| Сеть мигратора | Финальные NetworkPolicy/Job server dry-run PASS; на свежем K3s проверены deny-before, SQL только для нужного SHA, отказ другому SHA и Redis6379 |
| Argo/Traefik cutover | Свежие K3s1.37.1/Rollouts1.10.2/Traefik3.7.15: все4 хоста ×4 пути ×GET/POST ×HTTP/HTTPS; maintenance503/no-store, сбой с закрытым барьером, старые pods/RS удалены, новый stable, новые endpoints, веса100/0, AnalysisRun отсутствуют, после открытия HTTP200 c504; стенд удалён |
| Live после проверок | knowi.ru и sslip.io `/health/ready`: HTTPS200; прежние Argo revision и head БД сохранены |

Основной Python CI выполнен на Windows/Python3.14; production images и дополнительные
Linux Runner tests — Python3.12. Это локальная проверка обязательных suites.
GitHub-hosted workflow и Anchore scans после публикации должны пройти штатно.
Marker scan проверяет известные приватные строки, не является универсальным поиском секретов.
K3s-сценарий использует synthetic Core/TCP listeners, базовый Nginx и self-signed TLS;
настоящие миграция/backup/restore проверены отдельно на PG16. Production Secrets не копировались.

Промежуточный запуск получил statement timeout при TRUNCATE auth fixture во время
параллельной Docker-сборки. Отдельная auth-группа34 PASS и финальный последовательный
полный прогон PASS; persistent blocker в наблюдении не обнаружен. Таймауты не увеличивались.

## WSL и локальные ресурсы

По поручению пользователя WSL временно работал с2GB (1998864384 bytes).
После CI и миграционных проверок возвращены6GB (6217277440 bytes); `.wslconfig`
восстановлен побайтово, вместе с processors4/swap8GB. Все исходные6 контейнеров запущены.
Два собственных CI PG/Redis контейнера удалены с volumes. Исходные lab/k3d не удалялись.
Временные каталоги перенесены в service `.local`; старый ignored корневой `.pytest_cache`
остался из-за файлового Access denied, ACL не менялся.
Текущий код перезапущен: Web `http://127.0.0.1:18531/`, API `http://127.0.0.1:18011/health/ready`.
Команда: `python tooling/dev/run.py --mode lab --api-port 18011 --web-port 18531`.

## Production baseline и порядок выпуска

Read-only проверен `knowi01/13.140.29.11`, K3s1.37.2, Argo CD3.6.1.
Application `argocd-preview/pymentor-preview`, UID `69a2f746-52ef-4965-a581-4b9d3f6dba1e`:
Synced/Healthy на `fa35703af60f922a353a32037412ec1feb1699ae`, source
`Release:deploy/gitops/preview`, auto-sync/self-heal включены, prune выключен.

Рабочая БД проверена через фактический Core pod в READ ONLY transaction без вывода DSN:
`migrated-data.pymentor-rehearsal-data-20261109.svc.cluster.local:57152/knowi_core`,
PostgreSQL16.15, runtime `knowi_app`, head **c503_dual_email_change**; владелец `knowi_migrator`.
Старая отдельная `pymentor_preview` c426 не является целью. Старый Core отклоняет c504;
после DDL простой возврат прежнего image и downgrade недопустимы.

Выпуск выполняется по [cutover runbook](../../infra/gitops/operations/README.md):
до push Release остановить auto-sync, дождаться CI/images/scans, закрепить CODE_SHA и
отдельный GITOPS_SHA, подготовить migrator Secret, закрыть все HTTP/HTTPS входы maintenance,
остановить старых writers, сделать свежий проверенный backup, выполнить миграцию и bootstrap
нового stable. Трафик открыть после внутренних smoke и проверки новых endpoints.
После backup новые записи требуют отдельного учёта при restore: синтетическая репетиция
явно показывает, что восстановление старого dump их не содержит.

Evidence: `%USERPROFILE%/.codex/visualizations/2026/11/14/01a1da97-2aab-7f11-a0c5-d766474ad952/`:
`release-readiness-ci-2gb-accepted.log`, `release-ci-summary.json`,
`release-migration-rehearsal-2gb.log`, `release-backup-restore-2gb.log`,
`release-*-scout*.log`, `release-*-image-http*.log`, `release-linux-runner-resources.log`,
`prod-readonly-final.json`, `release-maintenance-server-dry-run.json`, `wsl-memory-session.json`.
Финальный cutover: `release-rollout-network-rehearsal.log`,
`release-networkpolicy-server-dry-run.json`, `release-current-dev-runtime.json`.
Dump и отчёт: `services/core-api/.local/release-rehearsals/backup-20261115-01a1da97/`.
Приватные runtime credentials в evidence не копировались.
