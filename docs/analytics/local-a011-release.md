# Локальный a011: проверка реализации

Текущий образ `an3-20261117-a011-clean` прошёл полный packaged-source probe.
Первоначальный `an3-20261117-a011` отклонён из-за cached Python bytecode и
для node не используется. Его Python source SHA и история действительно
совпали; отказ полной упаковки сохранён в диагностическом evidence. Исключения
в `.dockerignore` исправлены на `**/__pycache__`, `**/*.pyc`, `**/*.pyo` согласно
[правилам Docker build context](https://docs.docker.com/build/building/context/#dockerignore-files).
Runtime source и migration SHA не менялись. Архив clean image75,885,568B,
SHA893905052c2fcba046c531700988d69c7c1e46dca912b2939d3123c8e32482eb,
full packaged SHA84708fb880a5235899671266572941da3822e03273a44920d814fb063bd43956.
Evidence `.local/cross-service/c11cd07a62d64bf693fabb8a16713d2e/image-preparation-result.json`.
Source probe confirmed, no cache, exact container и active marker отсутствуют.

Это локальные исходники и проверки; main и retained a006 не мигрировались.
Все day flags по умолчанию выключены. Старые privacy workers должны быть
выведены до появления первых a011 строк, включая trigger при flags OFF.

Последующее состояние17.11: первый K3s case b551ed25… остановился до прогрева
из-за несовпадения harness с реальными CRI/PV формами. Узкие guards исправлены,
206offline+53subtests и read-only actual recheck прошли. При падении host RAM
ниже1536MiB node защитно остановлен; binder/PVC/PV/DB/namespace и active marker
сохранены для reconciliation. Нагрузочного результата нет. Exact IDs и stop
operation — в EXECUTION.md. Старые4deployment остаются0, retained authority OFF.

Общая subject×московский день проекция остаётся производной от единой
activity_facts/a010; полный день читает компактные данные, частичный — buckets.
Source registry, generation/run/epoch, privacy и raw caps проверяются до выдачи.

Проверки:

- Интегрированный PG-набор:68 passed/1 dense skipped, включая7 реальных PG
  сценариев; дополнительно9 реальных concurrent/scan/lease сценариев прошли.
  Все fresh DB и роли очищены с проверкой.
- Регрессии runtime/privacy/Core transfer/observability:161 passed,
  39 opt-in skipped,12 subtests; это отдельный набор, числа не складываются.
- Плотный набор31day/50subjects(30active)/89280buckets/6428160presence:
  query-only20correct, p50/p95=328/406ms. Настоящий day worker построил960
  записей за89s/961cycle без ошибок; последующие20reads снова328/406ms,
  EXPLAIN317.785ms/temp0. Privacy guard в плотном тесте — fixture stub,
  source — заранее готовые synthetic buckets; это не production SLO.
- Native signed fullchain a011: exit0, session88083/chunkd90b61; late event,
  logical restore/new RUN/day rebuild/privacy purge и Core ID reuse прошли.
  DB analytics_delivery_test_dfd50a95e9cc4ae785ab235a20a2ab50,
  analytics_test_fe35dbf0b8524640baee964906e552cd,
  analytics_test_3215cf00419c4de5a3b2ae41b3361861 и12ролей/Redis очищены.
  No-hold runner не вывел operation UUID; после cleanup он недоступен.
- Browser reporting-OFF/ON проверен на a010 API; подробности и ограничения
  в browser-check-20261117.md. Новых frontend изменений a011 не вводит.

Образ knowi-analytics:an3-20261117-a011-clean:

| Значение | SHA256 |
| --- | --- |
| Docker ID / OCI index | 694d6065cca96ecc8a41842afee1045b9ab7ee340d121ecd90ae77cb372b67eb |
| OCI manifest для node pin | 1fc54f2afd2d2cdcdb7cc358cf43e171e8bb0b05ea3b4db55e8363f479aa508a |
| OCI config | b1a7d720e61f3f710414193e2e1ce3698f53771d324decfa1a7e151c7d39a7b6 |
| Runtime source | ed95dee4b91076d3cf6b18a42620368feffc89446b740293075e5458e2759902 |
| Полная a011 history | 8a9e80bd33828e53d737fe1f5803af9bb41dd72f95b073fbffb402b90720aa50 |

Изолированный Linux source/import/default-OFF/pip-check прошёл на Python3.12.16,
UID10001, network none/read-only,128MiB/.25CPU/pids64. Полный Linux PG/HTTP,
K3s load этим smoke не проверен. Отдельный containerd import/alias и проверка
manifest/config/layers/source позднее прошли на exact v2 node96cc40b0…:
operation82376e5a-d504-4268-8b8b-d38ca4f87dc7, evidence
`.local/cross-service/8ee35d13573e49e5843a039bf9dae21a/node-image-import-82376e5ad50442688b8bd38ca4f87dc7.confirmed.json`.
Node работает; retained authority остановлен, четыре старых Helm deployment
scaled0 с rollback snapshot. Новые benchmark namespace/DB/PVC/Pods и нагрузка
ещё не запускались. Registry push не было.

Dockerfile SHAef7a1c74d95e4de7fbe6a89fe933f216828134578e9c6e1926fec5822f1f7728,
.dockerignore SHAe4d9f9c79fbbea08d9ca3670b20c49081b27a68160099249ee7f2a60d4078cd1.

Encrypted a011 private-helper source-down recovery PASS:1test/14.41s total,
not RTO. Source container stopped before target; encrypted parity/journal purge/
newUUID/both real rebuilds/reconciliation/role-switched guarded reader/final
grant и surviving report1visitor/60000ms проверены. Lost replies оставались
pending, повтор запрещён, исход сверялся отдельно.2ownedcontainers/networks и
artifact/key/stage/journal независимо очищены. Ready snapshot; in-flight claims
и HTTP auth этим тестом не проверены. Публичные restore/unseal CLI позднее
прошли отдельный реальный сценарий и расширенный сценарий с Core. От
подтверждённой остановки источника до guarded reader6.406s, до положительных
browser+Core reads6.860s. Проверены calendar/admin/progress/null visited_at/
proof и ожидаемая потеря одного post-snapshot факта (4→5→4). Это однократная
малая synthetic fixture, не гарантия RPO/RTO и не проверка 900s расписания.
Независимая очистка двух собственных контейнеров/сетей/файлов прошла.
Подробные границы измерений — day-recovery.md/Jira108/comment10345.
Первый failed-WHEN fixture был
очищен до повторного запуска; pg_get_triggerdef заменил некорректный decoder.

Actual PG negative membership case также PASS: прямое чтение collector
запрещено, SET ROLE через членство подтверждено, restore отказал до записи;
одноразовая DB и6ролей очищены. Historical SQLite smoke позднее PASS на свежей
copy/frozen4107334d…/synthetic login с настоящим HTTP;49offline regressions.
Root независимо подтвердил original+2archive SHA/size/mtime/no sidecars.
3testcontainers/image/privatecopies очищены, audit manifestSHAe7e91f… сохранён.
Jira108/comment10349; детали legacy-preservation.md.

После native fullchain добавлен Core/Web cutover gate и metadata fallback:
Core requires browser presence только при явном новом flag, published catalog
header допускает Web cutover, иначе learner сохраняет legacy POST{} с server
Moscow day. Staff при cutover не собираются. Root41Node и49distinctCore tests
PASS. Это более новый source, чем native session88083: его actualPG/B2/browser
и fulltsc пока UNRUN; см. EXECUTION.md. Analytics packaged app/history не менялись.

Jira:84/10339,10341,10347;105/10338,10343;107/10340,10342,10348;
108/10345,10349;119/10344,10346.
Core56 и production454/122 остаются отдельными незакрытыми gate.
