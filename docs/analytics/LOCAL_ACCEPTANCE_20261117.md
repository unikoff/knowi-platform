# Analytics: локальная интеграция 17.11.2026

Ветка `feach/Analitick`, исходный HEAD `f787672ff2639434f0be331d05def5d8f31b3c1e`.
Рабочее дерево общее с исполнителями и содержит незакоммиченные изменения.
Коммиты, push, CI/CD и production в этой работе не выполняются.

## Проверено и принято

| Работа | Доказательство |
| --- | --- |
| CORP-62, отдельный локальный Docker/K3s стенд | Jira10307; signed B2→commit→jobs→receipts→synthetic projections, рестарты и независимое1→2→1 четырёх ролей |
| CORP-65, восстановление | Jira10302; отдельная synthetic копия и актуальный независимый deletion journal; не off-host production backup |
| CORP-114, обновление/откат | Jira10308; failed revision13, rollback14, serial mixed images15–18, narrow rollback19/forward20, данные/ACK сохранены |
| CORP-115, первый локальный бюджет | Jira10309; requests/limits/pools, Core соседи, память и disk stop criteria; не throughput/IOPS/VPS benchmark |
| CORP-439, подэтап каркаса | Jira10310; все четыре дочерние работы приняты |
| CORP-116, полный K3s synthetic сценарий | Jira10311; реальный Traefik/CNI, отказ и восстановление PG, lost ACK, lease generation2→3 и отклонение старого commit |
| CORP-109, инфраструктурный gate | Jira10312; ровно23 предшествующие работы Done, итоговая24-я принята |
| a007 межсервисный перенос | Реальные Core3.14/Analytics3.12 HTTP+PostgreSQL,6scenarios PASS; cleanup3DB/12roles/Redis-prefix/listeners проверен; полнота остаётсяunknown |
| a007→a008→a009 | Functional PG/HTTP PASS: каталог/proof/offload SQL detector/replay/BIGINT/purge/restore; Jira10316. Exit1 на filesystem cleanup, затем independent reconciliation и exact cleanup; исправлениеcleanup ждёт liveповтора |
| a007→a010 | Полный functional run exit0, cleanup проверен; Jira10318. Bucket backfill, Browser-only calendar, concurrent fencing, signed purge и logicalrestore/newrunid. Это ещё не encryptedrestore, UI3roles илиload |
| Каноническая цепочка a007→a010 | Повтор после source promotion прошёл exit0; Jira10319. Три fresh DB/роли/соединения и порты18151–18159 проверены как очищенные |

Подробные команды и исторические снимки находятся в [run-k3s.md](run-k3s.md).
Текущий кластер — `knowi-analytics-01a1df75-v2`; прежний сохраняется остановленным.
Frozen image `an1-20261117-report` не включает последующие изменения source.

## Интеграция в работе

| Область | Состояние и владелец |
| --- | --- |
| Сохранённый K3s | analytics_infra_finish; обратимая пауза после принятого109, данные/volumes сохраняются |
| Core activity/progress outbox, совместимые reads и возврат | core_analytics_transfer; default-off, сохранённые данные не переключены |
| Core receiver, projection worker, purge status и legacy Query bridge | analytics_core_receiver; canonical a007–a010 проверены в full-chain, retained datasets не переключены |
| Сводки времени и course/lesson details | analytics_activity_reports; a010 общая bucket projection устраняет подтверждённый global10kraw cap, 22 focused PG checks прошли; месячный объём и load проверяются отдельно |
| Каталог | analytics_catalog_publish; canonical a008, P1 publication/receipt/metadata проверены в full-chain |
| Реальный межпроцессный стенд | analytics_cross_service; новые UUID базы, sentinels/owner, отдельные Redis prefix/DB13, запуск после116 |
| Tracker в Workspace | analytics_tracker; два явных флага, без подтверждённого каталога закрыт |
| Личная статистика | analytics_learner_ui; source подключён за флагом, ожидает browser E2E |
| CourseAdmin / PlatformAdmin | два существующих Batch Runs чата; общий signed-grant client, source подключён, browser E2E впереди |

Короткие проверки source и HTTP fake не объявляются реальной PostgreSQL,
браузерной, нагрузочной или production проверкой. Результаты заносятся в Jira
после фактического выполнения.

## Текущие независимые проверки

Terra исполнители отдельно ведут encrypted restore/unseal a010, месячный объём
отчётов, адаптацию нагрузочного harness, оставшиеся Core reads, личный Dashboard
и изолированный browser launcher. Два существующих Batch Runs чата отвечают за
CourseAdmin и PlatformAdmin. Общие PG/нагрузочные окна выполняются последовательно,
чтобы сохранить память и не смешивать измерения.

Подтверждённый аудитом пропуск feature flag в CourseAdmin передан владельцу.
Личный Dashboard интегрируется через существующий адаптер данных без отдельного
дублирующего раздела. Основной frontend5173 и Core18041 остаются в прежнем режиме.

Полный profile-v1 содержит 4 ступени ×5 конфигураций ×3 повтора с 300s warmup
и 900s измерений: минимум20 часов окон без provisioning/cleanup и отдельных
recovery/outage проверок. Source harness и короткие проверки не закрывают CORP-107.

## Checkpoint после проверок 17.11.2026

- Canonical full-chain a010: exit 0 и независимый cleanup, Jira10319.
- Sparse month: 31 день, 50 subjects, 12 400 фактов; reader234ms,
  отдельный EXPLAIN244.641ms с temp spill; это не p95. Jira10321.
- Исправлен оставшийся Core admin-profile/quota путь: Query preflight до
  изменения квоты и повторная lifecycle-проверка. 112 focused Core tests,
  подписанный in-process HTTP contract; новые PG/race сценарии подготовлены,
  но пока не запускались. Прежний full-chain PASS предшествовал этому fix.
- Dashboard получает details Analytics через текущий UI. Реальное личное
  время требует роли user и двух flags REPORTS/PERSONAL_MEASUREMENTS;
  demo отдельно, основной процесс не переключён. TS и focused tests прошли.
- Browser review launcher и дополнительная практика fixture готовы, но
  full browser hold не запускался. Native Tracker fixture ранее проверен
  через IAB с заглушкой; это не сквозная проверка трёх ролей.
- Оба Batch Runs завершили source handoff: CourseAdmin день/7/30 + каталог
  и фильтр урока + bounded details, PlatformAdmin global/course/lesson rows
  и pagination. Course48 и Platform30 focused checks PASS (общий client
  suite входит в оба числа), TypeScript PASS. Реальная UI приёмка UNRUN.
- Encrypted restore a010 остановился на extraction до импорта данных.
  Старый stderr был подавлен, причина неизвестна. Target проверен пустым,
  app workers не запускались. Baseline CONNECT существовал при provisioning;
  утверждение о revoked CONNECT до extraction этим run не подтверждено.
  Подробности и cleanup — в local-a010-release.md, Jira10323.
- Свободная память опустилась примерно до1422MiB, ниже runtime floor1536.
  Новые тяжёлые окна не запускаются. CUA отказался управлять Windows Firefox
  из-за отсутствия URL policy enforcement; окно НЕ закрыто. Политику и
  ограничение Chrome DevTools не обходили.
  После очистки пустого временного restore-target новый срез дал2096MiB;
  это позволяет повторно проверить preflight для no-network диагностики,
  но не является обещанием достаточной памяти для полного browser/load.

### Локальные образы

| Artifact | OCI image ID |
| --- | --- |
| knowi-analytics:an2-20261117-a010 | 86051258c136fe31b81a8d889384a09fb6d1abfdec61f812d321e29bb5cd273c |
| knowi-core:an2-20261117-runtime | 5e12ab7bd1debf487f6d54b76246d86674ee9e88bfcb51816c961046605bb5ef |
| knowi-core:an2-20261117-tools | e17ba1c1c647917ca0b1ebd0b85ea4ef4775e283804ce048947863e66e540ac3 |

Python3.12.16, pip check, imports/OpenAPI/default-off, canonical a010 и
bundled001/002 checksum фактически проверены в bounded network-none контейнерах.
Это structural smoke, без lifespan/DB/production. Tools — отдельный explicit
Docker target со старым synthetic-only guard, отсутствует в serving runtime.
Ни image push, ни deployment не выполнялись; frozen stage1 image сохранён.
Jira10322. Исходники backup tooling вне app image, их проверяют отдельно.

Отдельный локальный test image на базе того же Core tools + pytest9.1.1:
`d7d05d5b037639b99518f694c2bdf14d1453ae01fa97cc0affc1d3a6b990234a`.
В Linux/Python3.12.16 реально прошли95 tests/analytics_delivery за4.14s,
network none, readonly mounts tests/contracts/current Analytics module и tmpfs
для временной SQLite. Это не live PostgreSQL и не HTTP между двумя images.
В обычный serving image pytest/tests не добавлялись. Более ранние112 focused
checks включали другие selected tests на Windows3.14; числа не суммировать.
Отдельный общий API contract regression ранее не собирался из-за существующего
несовпадения course content_version с authoring files; курс не исправляли здесь.

### Следующий безопасный порядок

Последний terminal checkpoint: доказана обязательность явного `--file` для
используемого pg_restore16.16. В той же host Python Popen схеме без выхода:
exit1/output_destination_required/0bytes, с `--file=-`: exit0/7231bytes,
оба без DB/network; контейнеры удалены. Helper исправлен, revoke до extraction
и Core days/progress/proof fixture интегрированы. 41 offline checks passed.
Expanded live повтор остановлен prestart guard до authority/PG/данных:
внешний срез около2130MiB был выше порога, после загрузки pytest — ниже2048MiB.
Это resource refusal/UNRUN, а не повторный functional restore FAIL.
Исходный точный stderr первого запуска отсутствует; его известные аргументы
воспроизводят обнаруженную CLI ошибку. Правка ещё ждёт реального restore.

Root на время остановил только собственный Vite3996, но RAM всё равно1769MiB;
он восстановлен новым PID31064 на5173, все4 Analytics flags=false. Последние
health checks: Core ready200, main/backend200, proxied tracks200. Firefox НЕ
закрывали. Сохранённые Core/Analytics DB и K3s volumes не менялись. Все native
исполнители и оба Batch Runs завершили текущие source поручения; фоновых load,
browser hold или новых PG tests нет. Не предполагать, что они продолжаются сами.

Новые source/PG задачи: Core profile/quota live race, expanded encryptedrestore,
браузер3ролей, dense query-only, full load. Formal56/454/122 остаются открытыми.

1. Восстановить resource preflight: RAM не менее2048MiB перед запуском,
   floor1536MiB в процессе, диск не менее8192MiB. Не ослаблять числа ради PASS.
2. Диагностика extraction завершена; выполнить новый fresh encrypted
   source-down restore/unseal после исправления `--file=-` и quarantine.
3. Новая canonical full-chain с profile/quota SQL detector и reused-ID race;
   только после этого browser hold с реальными ролями и тремя loopback hosts.
4. Проверить браузером обновлённые личный/CourseAdmin/PlatformAdmin интерфейсы.
5. Dense query-only dataset и реальный нагрузочный профиль отдельно. Ни
   подготовленный harness, ни одиночные234ms не закрывают CORP-107/119.
6. Formal CORP-56/454/122 gates сохраняются; локальные фикстуры их не заменяют.

## Границы данных и приёмки

Одна `activity_facts` для всех курсов. Legacy дни не становятся минутами:
новое время измеряется только для user, visible+focused, idle300s,
theory/practice, с union по subject до course/lesson фильтра.
Core сохраняет accounts/access/catalog, подтверждённый progress и бронирования;
подписка общая. Analytics не получает доступ к Core SQL/Redis.

В a007 coverage старого переноса `unknown`. Candidate a009 подтверждает конкретный
Core cut через полный snapshot digest/receipts и pre/post Core revision recheck;
при неизвестной полноте чтение закрывается. Default-off и сохранённые legacy данные
сами по себе не являются доказательством разгрузки Core. Функциональный synthetic
cutover/offload проверен, реальный пользовательский cutover не выполнялся.

CORP-109 требует23 принятые инфраструктурные работы. Формальная приёмка переноса
CORP-454 также требует фактического Core CORP-56. Его production cutover и
серверный Analytics CORP-122 не выполнены и не разрешены локальными проверками.
Локальная additive разработка продолжается без переключения реальных пользователей.
