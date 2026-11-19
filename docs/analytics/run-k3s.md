# CORP-62: подготовка отдельного synthetic k3d

## Текущий локальный v2 checkpoint, 17.11.2026

### Пауза стенда после CORP-116 и CORP-109

После приёмки CORP-116 и CORP-109 координатором v2 server и его отдельная privacy authority штатно остановлены для освобождения ресурсов. Перед остановкой проверены exact container ID, image, labels, сети и тома; все 10 synthetic processing jobs были completed, единственный Kubernetes Job — успешно завершённая миграция, временных lease и Traefik fixtures нет. Сначала выполнен `docker stop --time 30 96cc40b0343d9387bed6f1721895095b211eaf897276b5c62e247c728fadeb4f` (exit 0, container exited 143), затем `docker stop --time 15 d314f9d300074c3970ab28df3f4b36ce4e65fffce4636f94197ad215219c880d` (exit 0, container exited 0). Контейнеры, их сети, private kubeconfig и все тома сохранены, включая K3s datastore и authority journal.

После паузы Analytics PostgreSQL и Core PostgreSQL/Redis работают и healthy, Core `/health/ready` вернул 200. Windows available — 2,976,243,712 bytes на момент измерения; это не прогноз свободной памяти при возобновлении. Временно остановленный Vite возвращает координатор. Данные PostgreSQL не сбрасывались.

Для будущего возобновления сначала заново проверить identity, тома, свободную память и полный фазовый budget с резервами для node 2 GiB, authority 128 MiB, PostgreSQL и Core/Web. Только после успешной проверки штатно запустить **те же контейнеры** в порядке `docker start d314f9d300074c3970ab28df3f4b36ce4e65fffce4636f94197ad215219c880d`, затем `docker start 96cc40b0343d9387bed6f1721895095b211eaf897276b5c62e247c728fadeb4f`; после каждого старта сверить identity/состояние, а затем явным private context и namespace проверить API, node Ready, роли, endpoints, свежий signed authority barrier и сохранность a006/данных. Не создавать новый кластер, не повторять миграцию и не удалять volumes при ошибке; неизвестный результат сначала сверить.

### CORP-116: выполненный локальный synthetic сценарий

Проверены private context `k3d-knowi-analytics-01a1df75-v2`, namespace `knowi-analytics`, K3s `v1.35.5+k3s1`, a006 и локальный image snapshot `an1-20261117-report` (OCI index `sha256:c18dc63228fd84dc11e258693bcb0a3e081789dac29b577a1b29ea115079127a`). До проверок 116 были три synthetic Browser v2 facts двух отдельных subjects, шесть completed jobs, шесть receipts и четыре контрольные проекции; после двух дополнительных событий стало 5 / 10 / 10 / 8 соответственно. Это не learner/UI PASS и не HA. После 114/115 их prior evidence повторно использовано, а не представлено как новая performance-проверка.

Вычисление shared guard скорректировано после подтверждения cgroup ancestry: Traefik Pod располагается внутри уже зарезервированного parent node cap 2 GiB, поэтому его 128 MiB не прибавляются второй раз к `node cap - memory.current`. Остались parent и PG/authority remaining, Core PG/Redis remaining, host reserve 512 MiB, transient 256 MiB, Core/Web growth allowance 512 MiB, отдельная margin 128 MiB и 85%/pressure/swap/OOM guard. `web_temporarily_paused=True` разрешён только при подтверждённо остановленном Vite listener; reserve для его возвращения сохраняется. Это коррекция модели учёта, не измеренное улучшение производительности.

Реальный изолированный Traefik v3.7.15 Pod в `kube-system` использовал проверенные registry OCI index `sha256:0f6150202bc1fed72a7bd2107460eaa510e5b8a905f3fdb5b84f2c8619c33368`, manifest `sha256:10cc4f33663c9d71049b71775d0b2ac67c18f7ade489ce9d826acc292d7eb82a` и config `sha256:6cfd9663a5d3591fde0170042b72dd00614a6b99208cb377a46ec23a91560b65`. Два исходных k3d direct import не завершились; reconciliation не обнаружила tag, OCI objects или активный import. `docker image save` показал архив только с amd64 содержимым при multi-platform index, а k3d 5.9 direct использует `--all-platforms`; точный первоначальный stderr не сохранён. После диагностики один прямой поток `docker image save` → node `ctr -n k8s.io images import --platform linux/amd64 -` завершился за 4.91s без tools-node; OCI chain внутри node подтверждена. Временный Pod UID `de1baf74-a7bc-4cba-ad3f-0486c1c2e0e7` и ConfigMap UID `a219b35a-815a-4a55-a8b8-9f2a2e566b7c` после проверки удалены по подтверждённой идентичности; отсутствие обоих проверено.

Через localhost port-forward к этому Traefik, затем фактическую CNI-сеть к приложениям, неподписанные batch и activity report вернули 401; `/metrics`, `/ready`, `/api/analytics/control`, `/api/analytics/events/batch/extra`, `/api/analytics/reports-other` вернули 404. Подписанный точный повтор двух прежних событий через Traefik вернул HTTP 200 и два `duplicate/same_payload`, числа фактов/jobs/receipts/results не изменились. Подписанный read-only activity report с корректным UTC interval вернул HTTP 200, `status=ok`, `quality=unknown`. Первая попытка отчёта вернула 400 из-за неподходящего выравнивания микросекунд и была исправлена только для повторного read-only запроса; batch не повторяли. Это file-provider fixture, не production Traefik, внешний DNS или TLS.

Перед DB fault guard прошёл; семь client-соединений относились только к выделенной K3s synthetic DB. Один штатный `docker stop --timeout 10` аттестованного Analytics PG `693d9afb4c4270438dd8130064a938e1611d61bcbf0641654ab03442f30f85c4` завершился с кодом 0. Во время отсутствия DB все четыре роли дали `/live=200` и `/ready=503`, подписанный повтор batch вернул 503 без ACK. Спустя 27.34s до вызова восстановления запущен тот же контейнер; тот же volume и image подтверждены, PG healthy. После восстановления все `/live` и `/ready` дали 200, свежий подписанный authority HWM 0, та же DB identity/a006/cursor 0 и неизменный snapshot `sha256:d42e68e9b2264735c53409d1f3be463a1ef9777a996460b24eae331b29dff7df`. Эти 27.34s включают заданное наблюдение и не являются RTO.

**Потерянный ACK.** Для нового synthetic event `975f7ed9-03cd-420a-a69d-62c0dcc497f2` в операции `96825c84-5c6f-4d72-a5c7-1a78357dae57` HTTP-клиент отправил запрос Collector, отдельная read-only проверка увидела committed fact, после чего клиент закрыл socket, не читая ответ. Повтор того же event и payload вернул HTTP 200 `duplicate/same_payload`. В БД остались один fact, два completed jobs, два receipts и два результата с `backend=1`. Первоначальный snapshot двух subjects до этой проверки уже сравнен после DB fault; дополнительные subjects не заменяют его.

**Lease handoff и fencing.** После drain Processor через Helm до 0 опубликован отдельный synthetic event `2a4d9f76-60d1-4cf1-bb40-6e07992c3873`, операция `52ab8ac1-ce99-4d27-a668-5bbc6b82df7c`. Одноразовый Processor-role Job с тем же закреплённым image, Processor service account, role-specific Secrets и NetworkPolicy получил через реальный `PostgresProcessor.claim` lease generation 2 и завершился до `finish`. Job UID `0db614fe-5745-40ec-bb41-bdbb037028f4`, Pod UID `caf6e90f-4d8b-4277-9dd8-366f58c06947`; Job удалён, Processor штатно восстановлен Helm до 1. После TTL настоящий Pod Processor обработал job с generation 3. Каждый из двух consumers имеет ровно один completed job, один receipt и один результат `backend=1`. Затем попытка `PostgresProcessor.commit` с сохранённой generation 2 внутри Processor Pod вернула `stale_claim_rejected`; после неё generation 3 не имеет владельца, счётчики сохранились. SQL не менял epoch или TTL. Реальные source fixture: `infra/analytics-local/lease-claim.py` и `lease-stale.py`; credential values не передавались через аргументы и не выводились.

Первый stale-check завершился `ValidationError` до DB-вызова: строгая модель UUID/datetime получила JSON-строки через Python validation. После read-only сверки generation 3 и receipts fixture исправлен на `model_validate_json`, а проверка повторена. Перед успешной попыткой guard дал available **3,087,028,224**, required **3,049,267,200** bytes, включая прежние резервы; результаты зависят от текущей памяти и не отменяют новый guard. Ранее guard обоснованно отказал при дефиците 282,959,872 bytes; координатор освободил свои idle процессы и временно остановил Vite. Это исторический отказ, после которого выполнен новый PASS, а не ослабление лимитов.

Последняя read-only сверка: node Ready, четыре Deployment desired/ready 1, четыре app Pod Running/Ready с 0 restarts, Collector и Query по одному ready endpoint, Core ready 200. Единственный оставшийся Job — ранее завершённая миграция; временный lease Job отсутствует. БД a006 содержит 5 facts, 10 jobs, 10 receipts и 8 results; lost-ACK fact ровно один. Окно runtime передано координатору, который возвращает Vite. Production, пользовательские данные, Core PG/Redis и их volumes не менялись. Внешние DNS/TLS, production ingress, HA и performance SLO этим локальным сценарием не подтверждены.

### Архив: состояние подготовленного окна CORP-116

Зависимости CORP-114 и CORP-115 приняты координатором. Реальный изолированный ingress и fault PostgreSQL ещё не проверены. Для fixture подготовлен `infra/analytics-local/traefik-fixture.yaml` с exact batch/report rules из Helm chart, Pod в `kube-system`, без host port и без service-account token, с memory limit 128 MiB. Официальный registry для `traefik:v3.7.15` подтвердил index `sha256:0f6150202bc1fed72a7bd2107460eaa510e5b8a905f3fdb5b84f2c8619c33368`, linux/amd64 manifest `sha256:10cc4f33663c9d71049b71775d0b2ac67c18f7ade489ce9d826acc292d7eb82a`, config `sha256:6cfd9663a5d3591fde0170042b72dd00614a6b99208cb377a46ec23a91560b65`; origin сверялся с registry, но это не runtime PASS.

Один direct import вернул неизвестный исход. Сверка показала отсутствие Traefik tag и всех трёх OCI content objects внутри containerd, активного `k3d.exe` нет; повтор не выполнен: свежий shared guard показал available 3,337,928,704, required 3,373,498,368 bytes с полными резервами, дефицит 35,569,664 bytes. Fixture ConfigMap/Pod и fault не создавались. Перед следующим действием — одна свежая проверка `require_shared_local_budget`; у временно остановленного координатором Vite следует явно указать `web_temporarily_paused=True`, который проверяет отсутствие listener, но сохраняет 512 MiB Core/Web reserve. Ниже этого guard не выполнять import, новый Pod или fault.

Ограниченный allowlist cgroup guard допускает тестовый Pod только при явно переданном UID и совпадении namespace/name, image config digest, labels, memory cap и security. Он не допускает произвольный Pod; при отсутствии или отличии fixture проверка отказывает. Для разрешённого продолжения сначала подтвердить OCI chain после import, затем актуальные routes, Core health и сетевые пробы; после теста удалить только подтверждённые fixture ресурсы. Отдельно аттестовать Analytics PostgreSQL и volume, перед краткой недоступностью зафиксировать данные и гарантировать обычный start того же контейнера. Данные Core/его PG/Redis не затрагивать. Публичный DNS/TLS и production ingress остаются вне этого локального доказательства.

### CORP-114: локальный rollout и rollback

Все Helm-команды использовали private kubeconfig `services/analytics/.local/k3s-kubeconfig-v2.yaml`, context `k3d-knowi-analytics-01a1df75-v2` и namespace `knowi-analytics`. Исходный image `knowi-analytics:an1-20261117-metrics`: OCI index `sha256:3bbed65491fdbce339c9231cdd55d23617339a99a91a7d6b376afde547bf0cef`, config `sha256:b8f3395a83c5acca0586f894077b1c3ff2aec5010a08b1cfbd2d2da0d23f7a53`. Новый `knowi-analytics:an1-20261117-report`: index `sha256:c18dc63228fd84dc11e258693bcb0a3e081789dac29b577a1b29ea115079127a`, manifest `sha256:57d4b75dd68a0e285f96a87c129d8f4ea2bcb8cd5d7d405e0dfde238f7c8765b`, config `sha256:f51528ba1b3e7182ef4f771964f432473ff142693f1928b837e7d1dab267f90a`. Новый image импортирован direct за 8.97s; index → linux/amd64 manifest → config проверены по хэшам внутри node. Это локальные snapshots; совпадение с последующими изменениями дерева не предполагается.

Сценарий отказа: `helm upgrade analytics infra/helm/analytics -f <private-values> --set roles.query.secretName=analytics-failed-fixture --wait --timeout 45s`, с context/namespace выше. Render отличался только ссылкой Query на отсутствующий Secret. Revision 13 завершилась failed за 46.47s, новый Pod имел `CreateContainerConfigError`; во всех 20 срезах прежний Query Pod и endpoint оставались Ready. Затем `helm rollback analytics 12 --wait --timeout 90s` создал deployed revision 14 за 2.73s. После отката четыре роли Ready, old image config, a006, signed HWM 0 и snapshot двух ACKed событий сохранены. Перед будущим восстановлением проверять `helm history`: доступные revisions ограничены.

Chart поддерживает отдельный необязательный `roles.<role>.imageTag`; пустое значение наследует общий `image.tag`. Render показал изменение image только выбранной роли. Helm upgrades с накоплением `--set roles.<role>.imageTag=an1-20261117-report`: Collector revision 15, Processor 16, Query 17, Privacy 18; длительности 3.51 / 3.78 / 4.14 / 4.30s. На каждом шаге остальные Pod templates и состояние DB сверялись. Старый Collector вернул два `duplicate/same_payload` на повтор сохранённых ID. Новый Collector вернул те же duplicate при старых остальных ролях и принял новое Browser v2 событие другого synthetic subject, обработанное старым Processor обоими consumers. Это проверка ограниченного mixed-version пути.

Узкий image rollback: `helm rollback analytics 17 --wait --timeout 90s` создал revision 19 за 6.83s и вернул только Privacy на старый image при новых остальных ролях. `helm rollback analytics 18 --wait --timeout 90s` создал revision 20 за 5.92s и вернул новый image Privacy. После каждого все роли Ready, signed свежий HWM 0, тот же a006/DB identity/cursor и неизменный snapshot двух synthetic subjects `sha256:d42e68e9b2264735c53409d1f3be463a1ef9777a996460b24eae331b29dff7df`: 3 facts, 6 completed jobs, 6 receipts, 4 контрольные проекции. Одновременный image rollback всех четырёх ролей, datastore/PVC/schema rollback и production rollout не выполнялись. Operation IDs и ACK сохранены в private `.local` без credentials.

### CORP-115: срез бюджета, 17.11.2026 01:09–01:10 UTC

После исходного снимка координатор штатно запустил сохранённые Core PG/Redis и dev-процессы. По его замеру Core PID11824: RSS 145.48 MiB, peak 152.09 MiB и ready 200; Vite PID6284: RSS 330.59 MiB, peak 344.64 MiB и `/backend=200`. Это snapshot, не hard caps Windows процессов. Core PostgreSQL cgroup current 38,727,680 из 536,870,912 bytes, Redis current 35,086,336 из 67,108,864 bytes; уже charged память повторно не резервировать. CLI relay после правки владельца использует NullPool и один одновременно используемый connection; Core sync3 + async3 остаются на каждый процесс.

Для дальнейших изменений использовалась дополнительная надбавка: остаток Core PG/Redis до caps + 128 MiB будущий ingress + 512 MiB на рост Core/Web поверх postguard и его 128 MiB margin. При image rollback available 3,526,451,200 bytes, required 3,386,343,424 bytes; при forward 3,557,437,440 и 3,387,547,648. Надбавка 512 MiB — операционный резерв, не hard cap и не гарантия. Перед новым действием нужен свежий guard; остановить рост при 85% node cap, swap/pressure/OOM, нехватке резервов, неготовом control-plane или деградации Core.

Компонентный backpressure принят в CORP-80: [backpressure.md](backpressure.md), prior real-PG suite 21 cases включает общий quota между репликами, насыщение backlog, 429/Retry-After, duplicate на лимите и отказ DB. Это не нагрузочное испытание полного профиля CORP-113 и не доказательство p95 или IOPS.

**Диск и остановка fixture, срез 01:32 UTC.** При 3 synthetic facts, 6 jobs, 6 receipts и 4 results `pg_database_size` равен 8,674,327 bytes. Для `activity_facts`: heap 8,192, indexes 81,920, total включая служебные страницы 98,304 bytes; для `processing_jobs`: heap 8,192, indexes 98,304, total 114,688 bytes. По всем 11 таблицам схемы: heap 65,536, indexes 425,984, total 532,480 bytes. На таком малом наборе преобладают страницы и пустые индексы: делить этот объём на число событий для прогноза нельзя. Текущий каталог `pg_wal` занимает 33,562,624 bytes; счётчик `wal_bytes` является накопленным значением, не скоростью роста.

В Docker filesystem `df` показал 1,018,802,769,920 bytes свободно из 1,081,101,176,832; на дисках Windows отдельно: C 35,023,564,800 bytes свободно из 511,987,150,848, E 486,360,154,112 из 1,000,081,453,056. В локальном Docker Desktop найден `docker_data.vhdx` на C; виртуальный свободный объём не равен гарантированному физическому месту. Контролировать фактический диск каждого DB volume, backup destination, staging и journal; если размещение нельзя подтвердить, не считать виртуальный `df` доказательством host capacity. Предыдущий отдельно проверенный encrypted backup другого synthetic fixture имел 6,252 bytes; он не является backup текущей v2 DB и не доказывает наличие четырёх копий.

Для **только этого локального fixture** операторский минимум свободного физического места на самом ограниченном общем диске до запуска growth/backup: 2 GiB для хоста + 2 × 512 MiB под восстановленную DB и временное построение + 5 × 512 MiB под четыре сохраняемые копии и новую кандидатную копию + 256 MiB под WAL + 64 MiB под журнал/служебное, итого **5,952 MiB (6,241,124,352 bytes)**. 512 MiB — действующий logical budget synthetic DB и расчётный минимум на копию, не доказанная верхняя граница её шифрованного dump; 256 MiB WAL и 64 MiB journal — начальные резервы, не измеренный максимум. До реального backup/restore увеличить их до измеренного размера наибольшего проверенного артефакта, фактического staging/restore и ожидаемого WAL за окно восстановления; если эти значения неизвестны, восстановление с гарантированным запасом не подтверждено. На C в данном срезе остаётся достаточно места для этого ограниченного envelope, но это не IOPS, нагрузочная или production оценка.

Операционные условия: при свободном месте ≤ **8,000 MiB (8,388,608,000 bytes)** на любом общем носителе fixture — предупредить и прекратить плановое увеличение нагрузки; при ≤ **5,952 MiB** или менее пересчитанного резерва, либо недоступной метрике — прекратить новые приёмы/growth и создание новой копии, сохранить четыре предыдущие проверенные копии и разбирать причину. Эти filesystem пороги — ручной runbook, автоматического мониторинга в fixture здесь не доказано. Отдельная действующая DB admission policy предупреждает при 80% и отклоняет новые записи при 90% своего logical budget; она не учитывает весь диск или WAL. Никакой TTL или автоматическое удаление фактов/копий для освобождения места не включать.

Это наблюдение idle fixture, не нагрузочный benchmark и не подтверждение ёмкости VPS. Четыре роли по одной: каждый request 50m CPU / 64 MiB; limits Collector, Processor, Query 500m / 256 MiB, Privacy 500m / 128 MiB. Суммарно 200m / 256 MiB requests и 2 CPU / 896 MiB limits. Дополнительно CoreDNS и metrics-server запрашивают вместе 200m / 140 MiB, local-path не задаёт request/limit, у metrics-server limit не задан. У node 1 CPU и родительский cap 2 GiB, куда уже включены системные и прикладные Pods; лимиты CPU намеренно overcommit, а их сумма не означает наличие двух CPU. `kubectl top` в спокойном состоянии показал 5–6m и 58–59 MiB на роль. HPA отсутствует; эти срезы не годятся для назначения новых limits или доказательства throughput.

Число одновременных соединений с **отдельным synthetic Analytics PostgreSQL**: C/P/Q по 2 + overflow 0 на replica, Privacy 1, migration 1, admin 2, backup 2. Выполненный `check_profile.py` считает 12 для 1/1/1, 14 при независимом 2/1/1, 1/2/1 или 1/1/2, 18 для модельного 2/2/2. `SHOW max_connections=40`, из них 3 `superuser_reserved_connections`, `reserved_connections=0`: обычным ролям доступны до 37 до учёта прочих потребителей. При Privacy 2 и C/P/Q по1 сумма 13, остаётся 24 обычных слота. Свободные расчётные слоты для перечисленных трёх профилей соответственно 25 / 23 / 19; это верхние резервации, не фактически открытые подключения. Наблюдалось 6 сессий synthetic DB включая диагностическую; максимум не измерен. 2/2/2 — арифметика, одновременная нагрузка на такое число реплик не выполнялась. Ручной 1→2→1 каждой роли проверен отдельно; второй экземпляр DB не создавался.

Collector ограничен 4 запросами в роли, batch до 100 items и 256 KiB; Processor ограничен двумя одновременно выполняемыми заданиями на процесс, batch до 100, DB timeout 5s, pool overflow 0. Значения — действующий synthetic профиль, а не оценка необходимого размера для production. Дальнейшее ручное увеличение Processor возможно только после измерения lag/ошибок, CPU, pool saturation и доступных PG соединений; авто-HPA до измеренных сигналов и проверки запаса не включать.

Текущий стенд использует отдельный PG: Core не делит его 40 слотов. В Core source одновременно существуют sync pool 3 и async pool 3 на процесс (overflow 0); CLI relay создаёт отдельный engine с NullPool и одной одновременно используемой connection после правки владельца. Если когда-либо выбрать общую физическую PG, суммировать все Core процессы и engines, relay и maintenance, миграции и backup, реплики Analytics и системные reserved slots; пока нет подтверждённой полной инвентаризации процессов/общего max_connections, такой общий бюджет не принят.

Windows available был 3,980,369,920 bytes. Зафиксированный postguard требовал 2,073,636,864 bytes сверх уже charged памяти; дополнительные 128 MiB margin, остановленные Core PG cap 512 MiB, Core Redis cap 64 MiB и возможный ingress cap 128 MiB дают вместе 2,946,052,096 bytes, оставляя около 986 MiB до **неизвестного** потребления Core процессов и иных изменений. Это не доказательство достаточности при одновременном запуске и не суммирование подов поверх родительского cap. Новый снимок обязателен перед действием. Лимиты остановленных Core объектов прочитаны без запуска и доступа к данным.

Отдельный PG 16: cap 384 MiB / 0.5 CPU, shared_buffers 32 MB, work_mem 2 MB, fsync и synchronous_commit включены. Idle sample: ~58.4 MiB Docker working set, 3.89% CPU, synthetic DB 8,674,327 bytes, pg_wal 33,562,624 bytes; свободное место 1,018,810,662,912 bytes относится к общему Docker filesystem. Это не измерение резервируемого места, IOPS, p95, пикового WAL или будущего роста индексов. Дисковый прогноз и резерв под restore/backup требуют отдельного измерения; хранение подтверждённых facts не ограничено TTL. Проверка encrypted backup/restore принята отдельно по CORP-65; не считать наличие файла доказательством restore.


**Завершено для передачи координатору:** локальные проверки четырёх ролей и независимого масштабирования, два synthetic HTTP события и процессные restarts выполнены. Координатор подтвердил CORP-62 как Done, Jira evidence 10307. Проверки отказа PostgreSQL и реального публичного ingress относятся к следующим этапам и здесь не пройдены.

**Исторический срез v2 до масштабирования:** после первого разрешённого target-local `memory.reclaim` (256 MiB, swappiness=0) node `memory.current` уменьшился с 1,827,332,096 до 1,551,941,632 bytes, `file` с 1,236,713,472 до 968,241,152; `anon` 503,734,272 → 503,730,176; swap/pressure/OOM 0, API оставался доступен. Это только разовое наблюдение: cache может восстановиться. Пороги и лимиты не менялись.

Failed release revision 1 сверили с нулевыми counts и штатно удалили только Helm-owned ресурсы выпуска. Privacy-first install прошёл; из privacy Pod проверены fresh signed attach/barrier HWM 0 и соответствующий DB cursor 0. Затем Collector, Processor, Query включены последовательными Helm upgrade с отдельными guard; все четыре 1/1 Ready, 0 restarts. На момент проверки Collector и Query имели по одному ready endpoint; authority EndpointSlice не задаёт `conditions.ready`, поэтому не считать это число нулём.

Перед scale в отдельном разрешённом окне выполнен ещё один адресный reclaim 512 MiB: inactive_file до него составлял 666,730,496 bytes; `memory.current` 1,770,151,936 → 1,205,903,360, `file` 979,746,816 → 443,191,296, `anon` 700,116,992 → 700,108,800. Swap/pressure/OOM оставались 0, все четыре Pod и API сохранили доступность. Уменьшение реплик выполнялось как Helm deallocation; после одного шага guard дождался удаления завершающегося Pod.

На четырёх Pod до последующего image rollout: DNS authority разрешается, TCP к отдельным DB и authority доступен, локальный `/metrics` вернул 200. Из Processor подключение к Collector Pod:8000 получило connection refused, в то время как node смог обратиться к тому же `/metrics`; Collector → Kubernetes API также отказал. Это ограниченные network observations. Traefik в fixture отключён: реальный ingress и отказ запроса `/metrics` через него не проверялись. Chart содержит только routes batch/reports. Независимые 1→2→1 выполнены для всех четырёх ролей через Helm: Collector 5.08/0.84s, Processor 2.64/0.88s, Query 5.06/0.95s, Privacy 2.51/0.81s; остальные роли оставались 1/1, шаблон и image не менялись. После одного signed Collector HTTP batch с двумя новыми synthetic событиями проверены 2 facts, 4 completed jobs и 4 receipts для двух consumers и две контрольные проекции `backend: 2`. Затем последовательно заменён один Pod каждой роли: Collector 3.51s, Processor 3.80s, Query 3.69s, Privacy 3.95s. После каждого новые UID и Ready, fresh подписанный attach/barrier HWM 0, тот же DB identity/a006/cursor 0, неизменный snapshot фактов, jobs, receipts и проекций (`sha256:16f1d940e7249747ef32494e7fc2d630b4f82064f5459d4891367c4af6fc4a09`). Это проверка рестарта процессов с сохранением DB; отказ или рестарт PostgreSQL/node не проверялись.


**Первая попытка:** первый `analytics` Helm release завершился `failed`, revision 1: Collector 0/1 Ready, остальные роли 0 replicas. В новой БД `activity_facts`, `processing_jobs` и `privacy_state` имеют по 0 строк; migration Job Complete. Это последствия порядка запуска, а не подтверждение неисправности приложения. Повторных Job и прямых изменений Deployment не было. После прохождения guard release сверили и выполнили Helm recovery: privacy первым, затем другие роли по очереди. PVC/DB/Secrets/migration Job и данные не удалять.

На ограничивающем снимке node `memory.current=1,826,500,608` из `2,147,483,648` bytes (85.05%, guard требует ниже 85%). `anon=502,820,864`, `file=1,236,709,376`, `inactive_file=1,033,256,960`; swap, pressure и OOM не отмечены. File cache не гарантированно reclaimable. Оценивался только точечный cgroup v2 `memory.reclaim` для этого server; [документация ядра](https://docs.kernel.org/admin-guide/cgroup-v2.html) допускает неполное или избыточное освобождение. На момент этого снимка reclaim ещё не выполнялся; результат последующего одного reclaim приведён выше, пороги не менялись. Внешние WSL/cache и остальные контейнеры не затронуты.


Старый `knowi-analytics-01a1df75` остановлен и сохранён в карантине после попадания synthetic token в прежнюю внутреннюю диагностику. Данные приложения в старом namespace не создавались; ротация и перенос не выполнялись. Текущая отдельная цель — `knowi-analytics-01a1df75-v2`, context `k3d-knowi-analytics-01a1df75-v2`, namespace `knowi-analytics`, отдельный `.local/k3s-kubeconfig-v2.yaml`, API `127.0.0.1:57443`. Общий context не переключали. Снимки ниже в старых разделах относятся к прежнему узлу и не описывают состояние v2.

- Node K3s `v1.35.5+k3s1` Ready; CoreDNS, local-path-provisioner и metrics-server Ready. Выделенный Analytics PostgreSQL и volume аттестованы и сохранены. До создания было available 6,898,991,104 bytes при резерве 3,623,878,656 bytes, включающем полный cap node и PG, authority, transient, host и дополнительные 128 MiB. Для каждого следующего этапа `require_post_create_budget` также требует дополнительные 128 MiB сверх рассчитанного `required_bytes`.
- Свой автоматически созданный tools-node после проверки только `noop` штатно остановлен без удаления. Независимый authority и отдельная synthetic БД созданы; после несовпадения формата Windows bind пути выполнено read-only reconciliation, исправлен точный match Docker Desktop path и подтверждён тот же работающий контейнер. Права файлов не расширялись.
- Frozen image `knowi-analytics:an1-20261117-metrics`, index `sha256:3bbed65491fdbce339c9231cdd55d23617339a99a91a7d6b376afde547bf0cef`, импортирован напрямую за 10.62s; внутри containerd проверены хэши index → linux/amd64 manifest `sha256:a93c49f13133af5655360fab18e13f118458a546ca259d1ced4f99709ede3892` → config `sha256:b8f3395a83c5acca0586f894077b1c3ff2aec5010a08b1cfbd2d2da0d23f7a53`. Он соответствует зафиксированному stage 1 snapshot, не обязательно последующим изменениям source.
- Одноразовый Job из Helm template с `backoffLimit: 0`, explicit `a006`, pull policy `Never` завершился Complete; независимая проверка БД подтвердила `a006` и `ready`. Роли и отдельный authority Secret переданы через stdin. Public trust, Service, EndpointSlice и каталог `backend/catalog-1/synthetic-track/lesson-1` созданы. Helm lint и template прошли с публичными локальными values.
- **Порядок запуска:** privacy worker должен первым аутентифицировать authority и связать DB cursor; до этого Collector/Processor/Query сохраняют readiness 503. Первый collector-only Helm install обнаружил это условие (DB privacy_state row count 0); не форсировать readiness. Последующее состояние release и фактическую проверку signed barrier, CNI, масштабирования и restart необходимо документировать отдельно. Одной настройки или завершения миграции недостаточно для объявления CORP-62 PASS.


## Архивный снимок старого кластера, 17.11.2026, 02:29 MSK — HOLD

**Обновление 02:33 MSK:** для последовательной проверки CORP-65 root разрешил
временно остановить пустую synthetic node. Перед остановкой проверены exact ID
`6f7f431e55ee3941bde58ac40679be0f86d111e8542c024bd99ae121569530a8`, image,
cap и API; Pods/Jobs и активных helper/import не было. Штатный `docker stop`
завершился, контейнер сохранён в состоянии Exited 143, cap 2 GiB неизменён.
Analytics PG остался healthy, Core `/health` и `/ready` вернули 200. После stop
Windows сообщила available 1,991,057,408 bytes; это не admission snapshot для
будущего запуска node. Не запускать её до handoff от проверки CORP-65.

Локально проверен [официальный Helm v3.21.4](https://github.com/helm/helm/releases/tag/v3.21.4):
SHA-256 Windows archive `268a7b98b313403055e4f31807aeaac529c90e1188acd7857ae3e960b0f67cce`,
exe `08e9cc1c8c7761ddbf0edf9ad2d131d96811c4cf4124201d216c18be26df0d7b`.
Без Docker выполнены lint и template при Kubernetes 1.35.5: default даёт три
Deployment и ноль Jobs; privacy — четыре Deployment, catalog ref у Collector и
authority Secret ref у четырёх ролей; migration — один Job с `a006` и
`backoffLimit: 0`. Отдельный ingress render не содержит route `/metrics`.
Template отказал при неверном namespace, отсутствии authority Secret и неверной
revision. Наблюдаемый пиковый working set процессов Helm меньше 35 MiB. Эти
проверки не подтверждают фактическое поведение CNI, ingress или rollout.

После перезапуска Docker Desktop проверена идентичность остановленного synthetic
PostgreSQL: контейнер `693d9afb4c4270438dd8130064a938e1611d61bcbf0641654ab03442f30f85c4`,
выделенные volume и сеть, localhost port `57392` и cap 384 MiB совпали. Выполнен
штатный `docker start` этого существующего контейнера; он `running`, `healthy`.
Пересоздания контейнера или volume не было. Core `/health` и `/ready` вернули 200,
существующие Core PostgreSQL и Redis были healthy.

После запуска повторён phase-aware guard: доступно **1,674,248,192 bytes**,
требуется **2,603,122,688 bytes**, дефицит **928,874,496 bytes (885.84 MiB)**;
для дополнительного запаса 128 MiB не хватает ещё 128 MiB. Результат **BLOCKED**,
новые этапы не запускались. Перед любым следующим этапом необходим новый снимок
guard с запасом не менее 128 MiB сверх required; нельзя ослаблять лимиты и reserve.

На снимке node `k3d-knowi-analytics-01a1df75-server-0` Ready, K3s
`v1.35.5+k3s1`, Docker memory cap 2 GiB, один CPU; все три системных Pod Running.
Metrics-server отвечает (`kubectl top node`: 30m CPU, 587 MiB), это разовая
выборка, а не бюджет host. `local-path` — default StorageClass. В namespace
нет Deployments, Jobs, PVC, Services и NetworkPolicies; отдельный вызов показал,
что Pods тоже отсутствуют. Private DB record, operation lock и каталог authority
отсутствуют. Локальный frozen image ID совпал с ранее зафиксированным. DB stage,
authority, Secret, image import, migration, Helm deployment и CNI checks **UNRUN**.
Сохранены server и остановленный k3d tools container; никаких дополнительных
контейнеров для тестирования не запускали.

**PHASE 2 — TERMINAL HOLD: локальный k3d создан, прикладной стенд ещё не готов.** После отдельного GO созданы target server и namespace с приватным kubeconfig и проверенным API; приложение и данные в кластере не развёрнуты. Последний post-create budget не прошёл; новые ресурсные этапы остановлены.

## Цель и проверенный inventory

- Единственная цель: новый `knowi-analytics-01a1df75`, namespace `knowi-analytics`, API `127.0.0.1:57443`; 1 server, 0 agents, server memory 2 GiB, K3s `v1.35.5-k3s1`. У K3s отключены встроенные Traefik и servicelb; системный kubeconfig/current context не изменяются.
- 16.11.2026: `k3d.exe version` показал v5.9.0; SHA-256 `49d0b9c796f5b8ba6f58a1dd5469e8b7b34bf9b6ce9078633eedb2789680a034` совпал с [официальным release asset](https://api.github.com/repos/k3d-io/k3d/releases/assets/436305724). Подтверждены local Docker endpoint и identity выделенных synthetic PostgreSQL/volume. После GO загружен только `rancher/k3s:v1.35.5-k3s1`, ID `sha256:2074403abe1bded11ef3dde09d457e13be8e0b64c218b1c4f8269b4565cfbc65`; до create было свободно 3648 MiB, после — 2471 MiB, при повторной проверке 2411 MiB при пороге 2560 MiB. Память меняется: измерять перед каждым дальнейшим ресурсным действием.
- В [исходном коде k3d v5.9.0](https://github.com/k3d-io/k3d/blob/v5.9.0/pkg/client/cluster.go#L536-L542) при отключённом load balancer API binding назначается первому server. `k3d config migrate ... -` подтвердил схему `disableLoadbalancer: true` и `serversMemory: 2g`; поле memory существует в [v1alpha5 типе v5.9.0](https://github.com/k3d-io/k3d/blob/v5.9.0/pkg/config/v1alpha5/types.go). Docker inspect после создания подтвердил server/image/labels, binding `127.0.0.1:57443` и limit 2 GiB; authenticated `/readyz` через отдельный kubeconfig вернул `ok`. Namespace имеет synthetic label; node подключён к проверенной Analytics PG сети. Один node Ready, CoreDNS/local-path-provisioner/metrics-server 1/1 Running без рестартов. Это не проверка CNI policy или приложения.

## Подготовлено

Chart по умолчанию сохраняет три процесса и не создаёт миграционный Job. Необязательный privacy worker включается только `roles.privacy.enabled=true`, имеет отдельный DB Secret, pool 1 и memory limit 128 MiB, без Service. Общий `ANALYTICS_PRIVACY_AUTHORITY_JSON` поступает из Kubernetes Secret; общий public trust manifest остаётся ConfigMap. Дополнительный public synthetic catalog ConfigMap задаётся через `fixtureCatalog.configMapName` и доступен только Collector как `ANALYTICS_FIXTURE_CATALOG_JSON`; по умолчанию источник отключён. Egress разрешается только DNS и отдельно указанным точечным DB/authority CIDR; пустые поля не открывают эти направления. На практике параметры `/32` строятся генератором после проверки Docker IP; фактическое действие CNI ещё нужно измерить.

[k3s_authority.py](../../services/analytics/tools/k3s_authority.py) готовит отдельный synthetic authority без host ports, UID 10001, read-only rootfs и собственным label-помеченным journal volume с уникальной identity. Bind config находится только в `services/analytics/.local/k3s-authority/config`; он и содержащиеся там ключи приватны. Runtime helper требует отдельного разрешённого вызова и проверенного image ID после source freeze.

[setup_k3s_fixture.py](../../services/analytics/tools/setup_k3s_fixture.py) формирует **только публичные** JSON Service, EndpointSlice и значения chart из двух разных частных IPv4 и указанного image tag. Входные IP должны быть получены из проверенных Docker объектов выделенной сети Analytics непосредственно перед применением, а не взяты из этого документа. Secret, private key и credential генератор не выводит и не создаёт. Client URL должен быть `http://privacy-authority.knowi-analytics.svc:57420`; EndpointSlice указывает на внешний адрес fixture, а не на ClusterIP другого Service.

```powershell
python -B services/analytics/tools/run_k3s.py inventory
services/analytics/.venv/Scripts/python.exe -B -m unittest discover -s services/analytics/tests/k3s_fixture -p test_k3s_fixture.py -v
```

CLI `run_k3s.py` не запускает k3d и не меняет context. Функции `create_after_go` и `verify_api_after_go` выполнялись после GO и подтвердили создание и API. Первое создание сохраняет пустой exclusive kubeconfig при неизвестном исходе; никакого автоматического повтора или удаления нет. Тесты проверяют генерацию, аттестацию и исходную структуру chart; они не являются Helm render или сетевым runtime test.

## Read-only аудит остаточной памяти

На снимке 16.11 node использовал cgroup v2 с private cgroup namespace и parent `memory.max=2048 MiB`. K3s находится в `/k3s`, CoreDNS и metrics-server — в дочерних `/kubepods/...` того же parent. [Kernel cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory-interface-files) учитывает в `memory.current` группу вместе с потомками, поэтому будущие app Pods включаются в этот cap: их лимиты нельзя повторно добавлять поверх уже учтённого полного остатка node. `memory.current` node составлял 1,107,968,000 bytes, из них `inactive_file` 640,884,736 bytes; приблизительный working set — 445.45 MiB. На cgroup v2 [Docker stats вычитает `inactive_file`](https://docs.docker.com/reference/cli/docker/container/stats/), поэтому его вывод отличается от полного `memory.current`.

Повторный снимок: node `memory.current=1,107,841,024`, PostgreSQL `memory.current=148,525,056` при его cap 384 MiB, `memory.swap.current=0` у обоих; `oom/oom_kill=0`. При предположении, что вся уже charged память resident и исключена из Windows available, оставшийся рост до cap составлял 991.48 MiB для node и 242.36 MiB для PostgreSQL. Root утвердил post-create модель: node и PG `max-current`, authority 128 MiB до старта и `max-current` после аттестованного старта, transient reserve 256 MiB и host reserve 512 MiB. Для этого снимка это `ceil(991.48 + 242.36 + 128 + 256 + 512)` = **2130 MiB**. Подсчёт от working set повторно резервировал бы до ~688 MiB уже charged inactive cache при этом предположении.

Это локальная budget-политика, а не гарантия: Windows `vmmem` имел агрегированный working set 2115.29 MiB и private commit 5314.14 MiB; эти значения не устанавливают физическое размещение страниц конкретных cgroup. Нулевой guest swap не исключает host paging или compression Hyper-V. В [исходниках k3d v5.9.0](https://github.com/k3d-io/k3d/blob/v5.9.0/pkg/client/tools.go#L170-L243) `--mode direct` стримит image в node без отдельного tools-node, но пик памяти Go/Docker daemon не измерен. Pre-create guard 2560 MiB сохранён. Post-create helper перед каждым этапом отдельно перепроверяет API/identity, parent cgroup и Pod ancestry, cap, swap, pressure, OOM, порог 85%, обе выборки доступной RAM, дополнительные running k3d containers и остаточную формулу; при недостающем измерении он отказывает. Два вызова DB stage отказали **до изменения БД**; private DB record и operation lock отсутствовали.

После отдельного разрешения root остановлен только проверенный idle k3d tools-node `53f6e57340e64d7151b79e916c59dfb3ab4879f6ef9b67155de774ff76ddf0a2`: состояние Exited, код 143 после штатного stop; server, сети и volumes сохранены. Ровно один последующий post-create снимок вернул **BLOCKED**: available 2,173,067,264 bytes (2072.40 MiB), required 2,231,037,952 bytes (2127.68 MiB), дефицит 55.29 MiB. Server и PostgreSQL остались running, PG healthy, Core `127.0.0.1:18041/health/ready` вернул 200. В `knowi-analytics` нет Pods. Новая БД, authority, Secrets, Job и image import не запускались. Четыре ранее остановленных app-контейнера сохранены. 17 fixture unit-тестов прошли; локального `helm.exe` нет, а вспомогательный контейнер Helm при HOLD не запускался.

Node сообщает allocatable 2,147,483 KiB (около 2097.15 MiB), что на 49.15 MiB выше Docker parent cap; Kubernetes scheduler не заменяет проверку родительского cgroup. Системные requests: CoreDNS 70 MiB и metrics-server 70 MiB, local-path без request; при одной реплике каждого из четырёх app requests добавляют 256 MiB, а их лимиты составляют 896 MiB. Migration Job идёт отдельно; rollout surge и ручное масштабирование требуют проверки доступного внутри parent бюджета.

## Остаток после GO

1. Перед следующим ресурсным действием повторно выполнить phase-aware guard и identity checks, не создавая заново существующий cluster. Даже при PASS нужен запас не менее 128 MiB **поверх** возвращённого `required_bytes`; при меньшем запасе остаётся HOLD. Порог 2560 MiB относится к pre-create, post-create использует описанную формулу. Root подтвердил замороженный app image `knowi-analytics:an1-20261116-86`, Docker ID `sha256:fca46395a5c5982802808b208038acc95aad694d5899b637b081cf1542e5f1ee`; в node он ещё не импортирован.
2. Подготовить новую собственную БД через [k3s_database.py](../../services/analytics/tools/k3s_database.py): UUID/отдельные роли/sentinel/owner, отдельный private `k3s-runtime.json`, без изменения `runtime.json`. Partial provisioning сохранить для ручного reconciliation. Общий `bootstrap_runtime.init()` не использовать: он сразу мигрирует. DB helper прошёл mock-тесты, но ещё не вызывался для provisioning.
3. Подготовить новые независимые response signing и command key пары и reader secret через `k3s_authority.prepare()` в приватном `.local/k3s-authority`, не переиспользуя private g1/p1. Server config задаёт `fixture_mode=true`, `bind_host=0.0.0.0`, port 57420; producer public p1/issuer/allowlist берутся из проверенного public trust manifest. Client config содержит только authority public key, reader secret и допустимый DNS URL. Helper проверен mock-тестами, но подготовка ещё не вызывалась.
4. Через `k3s_authority.start(..., authorized=True)` после новой проверки памяти запустить authority: helper проверяет доступность приватного bind для UID 10001 до создания отдельного volume, готовит владельца только нового пустого volume и инициализирует journal однократно. При частичном состоянии marker сохраняется для reconciliation; runtime start ещё не вызывался.
5. Существующий целевой k3d уже создан; повторно его не создавать. До следующих изменений повторно проверять private kubeconfig/context, Docker server identity, memory cap, binding и API. При неизвестном результате сначала инвентаризация и reconciliation; не использовать reset, drain или force-delete.
6. Проверенный server-node уже подключён только к дополнительной synthetic Analytics PG Docker network. Все `kubectl` вызовы задают отдельный `--kubeconfig`, `--context k3d-knowi-analytics-01a1df75` и namespace `knowi-analytics` там, где применимо. Подготовленная `create_secret_after_go` проверяет API до mutation, получает DB URL через аттестующий helper и передаёт Secret только JSON через stdin `kubectl create -f -`, без argv/stdout и без обновления существующих Secret; Secret ещё не созданы. Migration URL выдаётся helper однократно после записи маркера. Применить отдельный Job миграции с явной revision `a006` лишь после identity/sentinel/owner проверки; timeout означает неизвестный исход и требует чтения revision до повтора.
7. После подтверждённого импорта image и отдельной успешной миграции проверить `ready=200` всех четырёх процессов только при аутентифицированном source HWM 0 и privacy cursor; отказ authority должен закрывать readiness. Измерить CNI разрешение только DNS/точечных DB/authority, независимое 1→2 масштабирование и сохранение БД при рестарте без автосброса. `FixtureCatalog` уже реализован; по сообщению root signed HTTP + PostgreSQL test прошёл локально. Реальный K3s end-to-end остаётся **UNRUN**.

Безопасная read-only сверка текущего checkpoint в PowerShell — без содержимого kubeconfig или DB credentials:

```powershell
kubectl --kubeconfig services/analytics/.local/k3s-kubeconfig.yaml --context k3d-knowi-analytics-01a1df75 --namespace knowi-analytics get nodes
kubectl --kubeconfig services/analytics/.local/k3s-kubeconfig.yaml --context k3d-knowi-analytics-01a1df75 --namespace knowi-analytics get pods
Test-Path services/analytics/.local/k3s-runtime.json
Test-Path services/analytics/.local/k3s-database.operation.lock
```

Пока authority не запущен, один свежий read-only budget snapshot можно получить так; если он уже запущен, вызову дополнительно нужен его аттестованный `authority_image_id`, иначе helper откажет:

```powershell
$code = @'
import json, sys
sys.path.insert(0, 'services/analytics/tools')
import run_k3s as r
try:
    s = r.require_post_create_budget(
        r'C:\Users\Unik\AppData\Local\Programs\DockerDesktop\resources\bin\kubectl.exe',
        '923a299e3718ba3b527c75b83afeab40c92c83ff364a99363adad6027e9a37d5',
        'sha256:2074403abe1bded11ef3dde09d457e13be8e0b64c218b1c4f8269b4565cfbc65')
    s['margin_128_mib_met'] = s['available_bytes'] - s['required_bytes'] >= 128 * 1024 * 1024
except r.InsufficientPostCreateBudget as e:
    s = {'status': 'budget_blocked', 'available_bytes': e.available_bytes, 'required_bytes': e.required_bytes}
except Exception:
    s = {'status': 'identity_or_pressure_guard_refused'}
print(json.dumps(s))
'@
python -B -c $code
```

Helm lint/template после расширения chart, прикладные probes, CNI, Kubernetes Secrets и failure scenarios сейчас **UNRUN**. Production, существующий Core chart и GitOps bootstrap не изменялись.

**Конец архивной истории старого кластера.** Актуальные v2, CORP-114 и CORP-115 результаты находятся в начале этого файла; старые HOLD/UNRUN выше не описывают текущий стенд.
