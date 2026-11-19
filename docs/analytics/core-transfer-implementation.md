# Core → Analytics: изолированная локальная основа

Состояние: в Core добавлены опциональные пути записи, выдачи grants и чтения. Флаги
по умолчанию выключены; действующей миграции, фонового процесса и фактического
переноса нет. Существующие маршруты, таблицы и чтения Core остаются источником истины.
Это не приёмка CORP-109/56/117 и не разрешение на cutover.

## Контракт и существующие потребители

`services/core-api/contracts/analytics-transfer-v1.schema.json` и соседний MD —
внутреннее предложение, отдельное от браузерного collector, `core-control-v2`
и тестового c226. В последнем имеются лишь synthetic lesson events и delete;
использовать их как реальные legacy activity нельзя. v2 progress дополнительно
требует catalog_version, которого нет в строке Core. Для history transfer нужен
отдельно принятый consumer и контракт per-item receipt/status с одинаковым
fingerprint, отсутствия записи и conflict. До этого отправка отключена.

В legacy day сохраняются исходная дата (возможно, дата клиента), NULL и UTC
нормализация времени, `legacy_global`. Нет учебных минут, первого входа или
курса. У course/lesson progress `completed_at=null` обозначает удаление записи.
`analytics-transfer-parity-fixtures.json` задаёт точные календарь и summary и
две разные admin-метрики на synthetic примере: глобальный platform activity
включает все роли, legacy dashboard исключает администраторов; активность курса
неизвестна (`null`). Эти fixtures не доказывают parity двух сервисов.

## DDL specification, НЕ миграция

Новая отдельная SQLAlchemy `MetaData` описывает пять таблиц:

* `analytics_transfer_outbox`: UUID event ID PK, subject и kind, entity hash +
  revision unique, канонический payload/fingerprint, неизменяемый created_at,
  статус, attempts, uncertainty, время и lease. Индексы subject и due.
* `analytics_transfer_checkpoints`: имя потока PK, фиксированный high watermark
  и cursor. По одному активному владельцу каждого скана.
* `analytics_transfer_revisions`: opaque lifecycle UUID PK, monotonic revision;
  без FK на удаляемый аккаунт. Это не полноценный privacy journal.
* `analytics_transfer_activity_state`: дни, затронутые в режиме outbox-only,
  исходный timestamp и признак необходимости replay в Core.
* `analytics_transfer_write_gate`: singleton с состоянием mirror, outbox_only
  или replaying. SQL-файл задаёт начальный режим mirror для synthetic базы.

`services/core-api/tools/analytics_transfer/001_additive.sql` — ручной локальный
DDL-артефакт с инициализацией gate, не Alembic revision. Не запускать на сохранённой пользовательской
базе. Перед реальной Alembic-миграцией владелец интеграции должен подтвердить версию
PostgreSQL, имена/индексы, timeouts/lock budget, разрешения, квоты outbox,
политику хранения и DDL/rollback на отдельной копии. Не включать `metadata`
в активный Core schema bootstrap, не создавать head из этой спецификации и не
применять её к сохранённой базе. Таблицы additive, но совместимость разных
версий Core и фактическое поведение блокировок ещё не проверены.

## Точная интеграция, необходимая владельцу Core

1. Опциональная ветка уже берёт user lock и в одной транзакции создаёт или
   использует lifecycle, изменяет activity/lesson completion, выделяет revision
   и записывает outbox. `PYMENTOR_ANALYTICS_DELIVERY_ENABLED=1` включает её;
   startup проверяет наличие таблиц без DDL. `account_ref()` с отдельным commit
   для этого не используется. До включения и переноса необходимо подтвердить
   все вызывающие пути и фактическую совместимость на отдельной synthetic БД.
   Mutation и enqueue не разделяются промежуточным commit; если отсутствует
   lifecycle, он создаётся в той же транзакции под lock, а не из email или ID.
   Нужно отдельно подтвердить отсутствие внешних или старых writers при cutover.
2. При удалении под тем же lock атомарно очищаются subject-linked outbox rows и
   ставится durable delete. После Core commit отдельный worker добавляет v2 fence
   в независимый journal с P1 JWT и detached подписью, проверяет подписанную
   квитанцию и запрашивает receiver status. Только подтверждённый applied prefix
   и purge разрешают ACK. Интервал между Core commit и append ещё требует
   согласованного privacy barrier/ACL, а in-flight старые факты может подавить
   только receiver fence. Restore/replay должны
   сначала применить полный авторитетный journal/fence и закрыть чтение при
   неизвестной полноте. Одна outbox-строка не выполняет этот барьер. Receiver
   должен подавлять late/in-flight факты и удалить derived/shared contributions.
   При включённом capture DELETE требует `X-Account-Ref` и возвращает 202
   `{"status":"pending"}` до успешной записи подтверждённого purge в outbox.
   Повтор того же `(user_id, lifecycle_id)` возвращает 202 либо 204 и не удаляет
   новый аккаунт при повторном использовании числового ID; отсутствие записи
   outbox даёт 503. При выключенном capture сохраняется старый 200 с JSON.
3. Сначала развернуть совместимых writers и receiver с атомарными dedup+
   projection, tombstone и revision semantics. Отдельно доказать отсутствие
   старых writers, иначе записи позади cursor потеряются. По блокировке lifecycle
   выбрать безопасную точку включения capture; зафиксировать high watermark по
   `id` для каждого потока. Страницы читать строго `id > cursor AND id <= high`
   через `scan_page`, включая все роли. Отсутствие UUID — остановка без движения
   cursor, не пропуск строки. `stage_page` пишет события и cursor в одной
   транзакции; запускать одного владельца скана. Исчезнувшие/обновлённые строки
   компенсирует уже включённый transactional live capture и delete fence.
   Незавершённый скан не считается полным только из-за пустой страницы: сверить
   фактические source counts, live tail, watermark, удаления и ACL. Последняя
   подтверждённая страница может иметь id меньше high из-за удалённых строк.
4. Relay: короткая транзакция `claim`; затем записать `mark_sending` и commit до
   вызова транспорта. Передача вне DB-транзакции; потеря ответа означает
   unknown. При новом lease сначала lookup по тому же ID и fingerprint: accepted
   → ack, absent → допустим повтор, unknown → отложить, conflict → quarantine.
   `finish` выполняется в новой транзакции с текущим claim token. Лимиты на batch,
   lease, attempts, deadline, bounded jitter/backoff и размер хранилища должны
   быть заданы оператором; достигшие max attempts остаются видимыми pending и
   требуют явного контролируемого восстановления, не бесконечного retry.
   Receiver обязан commit-ить dedup и бизнес-проекцию вместе до ACK; при
   перекрытии lease возможны повторы, at-least-once не означает exactly-once.
   Реализованы `TransferHttp`, `AuthorityClient` и один явный bounded `run_once`;
   они не стартуют автоматически. Защищённый synthetic CLI указан ниже.
   Worker передаёт оставшийся общий бюджет. `httpx.AsyncClient` обёрнут в
   `asyncio.timeout` на весь сетевой обмен, поток ограничен по размеру и
   закрывается при отмене; redirect, proxy и content-encoding запрещены.
   Loopback-проверки задержки заголовков и медленного тела для transfer и
   authority показали UNKNOWN в пределах бюджета, закрытие соединения и
   повтор с тем же event/control без дополнительных потоков. Это не является
   проверкой реальной сети или PostgreSQL.
   Хранить token/header в секретах, не логировать payload, UUID субъекта или PII;
   нужны агрегированные ошибки, lag, состояние очереди и безопасная корреляция.
5. Проводить выборочное переключение только после полного сравнения ответов,
   часов/NULL, администратора и ACL, deletion/restore, отставания и отказов.
   До доказанного parity и обратного заполнения сохранить Core writer и данные.

## Совместимость и откат

| Сочетание | Статус |
| --- | --- |
| Старый Core + текущая схема и consumer | Без изменений этими файлами |
| Старый Core + добавочная схема | Совместимость предполагается; отдельный mixed-version rollout не проверен |
| Новый capture + старый writer | Небезопасно: старый writer пропускает outbox |
| Transfer v1 + существующий Analytics v2 | Несовместимо; требуется отдельный приёмник v1 |
| Новый receiver + исходный Core writer и сверка | Целевой переходный этап, не реализован |

Откат неактивного кода не меняет данные. Откат после записи только в Analytics
не восстанавливает данные Core: до отключения Core writer требуется проверенный
обратный перенос и отдельная авторизация. Удаление subject необратимо; простой
откат кода/восстановление старого backup не отменяет обязанность применить
последующие tombstones. Не отключать Core stats в этой работе.

## Локальное включение и ручной операторский инструмент

`PYMENTOR_ANALYTICS_GRANTS_ENABLED=1` отдельно включает POST
`/api/analytics/grants/collect` и `/api/analytics/grants/report`. Требуются
секретный `PYMENTOR_ANALYTICS_GRANT_KEY_FILE`, `PYMENTOR_ANALYTICS_GRANT_ISSUER`
и `PYMENTOR_ANALYTICS_GRANT_KID`. JWT Ed25519 содержит NumericDate `exp`, а
ответ имеет ISO `expires_at` и `server_time`. User получает collect и self report;
leader report ограничен своей course roster, не более 100 субъектов; admin —
global report. Фильтр не создаёт прав. В G1 user self определяется по `sub`,
`subject_refs=[]` согласно текущему валидатору Analytics.
Для drilldown `/report` принимает необязательный числовой `user_id` вместо
`subject_ref`; одновременно указать их нельзя. Core повторно проверяет роль,
course и roster, разрешает ID в действующий lifecycle и только в таком запросе
возвращает `target_subject_ref`. User может запросить только собственный ID,
leader — участника своей roster, admin — существующий аккаунт.

`PYMENTOR_ANALYTICS_LEGACY_READ_MODE=shadow|analytics` — отдельный opt-in;
по умолчанию `off`. Требуются `PYMENTOR_ANALYTICS_GRANTS_ENABLED=1` с его
signing config и `PYMENTOR_ANALYTICS_QUERY_URL`. Core использует G1 и Query
`/internal/analytics/v1/legacy/{calendar,summary,admin/activity}`. Shadow
оставляет ответ Core, сравнивает наблюдаемые поля и указывает источник и
неподтверждённое покрытие HTTP-заголовками; сбой Query не ломает чтение Core.
Analytics mode возвращает Query-данные только при `coverage=complete` и иначе
503 с `X-Analytics-Coverage`. Без включённого proof Query возвращает `unknown`,
поэтому переключения не происходит. Срез course activity остаётся неизвестным;
legacy calendar/summary для non-user ролей до отдельного контракта не переключаются.
В analytics mode пользовательские calendar/summary не выполняют Core-запрос к
старой таблице дней; admin пропускает старые activity-агрегаты и берёт их из
Query. Операционные данные и авторитетный прогресс остаются в Core, поэтому
admin-ответ маркируется `X-Analytics-Source: mixed`.

Отдельный `PYMENTOR_ANALYTICS_LEGACY_WRITE_MODE=outbox_only` требует включённого
capture и `LEGACY_READ_MODE=analytics`. Новое посещение сохраняет state и outbox
в одной транзакции, не меняя `user_activity_days`. В режиме mirror старый writer
остаётся активным. Только включённые capture writers сверяются с DB gate под
общей блокировкой: устаревший режим получает 503. Старые версии и процессы с
capture=off gate не знают, их необходимо остановить до переключения.
Порядок блокировок activity/replay: gate → user/lifecycle → state; переключение
режима удерживает только исключительную блокировку gate и фиксирует её до replay.

Synthetic последовательность: остановить writers; после независимой приёмки
coverage/parity выполнить `begin-outbox --writers-stopped --coverage-proved`;
запустить только новые outbox-only writers. Для отката остановить их, выполнить
`begin-replay --writers-stopped`, затем ограниченные `replay-page --writers-stopped`
до опустошения dirty state и `finish-replay --writers-stopped`, после чего
запустить mirror writers. Реальные подтверждения `--writers-stopped` и
`--coverage-proved` обязан предоставить оператор: флаги сами их не доказывают.
Replay сохраняет старый ненулевой timestamp и останавливается на конфликте;
история/таблицы не удаляются. Synthetic proof не является подтверждением
production coverage, поэтому фактический cutover не разрешён.

`python tools/analytics_transfer/run.py --help` описывает provision, export,
gate/replay и relay-once. Инструмент допускает только локальный
PostgreSQL и имя `analytics_delivery_test_*`, совпадающее с `--expected-db` и
фактическим `current_database()`, и не выполняет DDL. Это дополнительный барьер,
а не доказательство, что база disposable: root должен сначала явно подготовить
чистый synthetic target. Relay требует отдельные private P1/command keys,
public grant/authority keys, issuer/kid и URL через окружение; он проверяет
различие ключевого материала. Заголовки и JWT не печатаются. До экспорта capture
должен быть установлен всеми writers; provisioning и cursor проходят ограниченными
страницами, отставание и concurrent writes требуют отдельной сверки.

CLI использует `NullPool` и выполняет операции последовательно: одно запущенное
CLI имеет не более одного своего одновременного подключения и не удерживает его
между транзакциями. На один процесс Core текущие лимиты основного runtime —
3 синхронных и 3 асинхронных соединения, отдельный CLI добавляет до 1: суммарно
до 7. Параллельные CLI и дополнительные процессы Core прибавляют свои лимиты;
общего межпроцессного ограничения здесь нет. Ручной DDL-файл собственного
engine не создаёт. Значения не являются измерением фактического потребления.

## Доказательства и границы

Фокусные тесты используют только SQLite `:memory:` и synthetic UUID, не реальные
учётные записи. Они проверяют детерминированные DTO, конфликт одной ревизии,
откат source+outbox, порядок checkpoint, отсутствие lifecycle, потерянный ответ
и устаревший lease, а также fixtures через текущую Core policy. PostgreSQL,
конкурентная запись, замена lifecycle, реальный HTTP/приёмник, privacy journal,
полные admin API responses, performance и cross-service parity в этих исходных
offline checks не проверялись. Последующие synthetic проверки описаны ниже.
# Дополнительный a009 proof локального снимка

`002_snapshot_proof.sql` — отдельный ручной артефакт только для свежей
разрешённой synthetic Core DB; он не меняет c528 и не применяется при старте.
Флаг `PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED=1` требует включённый capture,
001 и 002. Флаг по умолчанию выключен; a007 остаётся совместимым без 002.

Штатная локальная установка addon выполняется отдельной командой
`tools/analytics_transfer/migrate.py`; Core startup выполняет только readiness
проверку. На заранее созданной fresh synthetic DB владелец создаёт отдельно
`public.analytics_delivery_install_sentinel(token UUID NOT NULL)` с одной
случайной UUID-записью. Установщик требует localhost URL базы вида
`analytics_delivery_test_*`, `--expected-db`, `--expected-owner` (он же
`current_user` и владелец DB), `--target-version 1|2` и тот же UUID в
`PYMENTOR_ANALYTICS_INSTALL_SENTINEL`. Токен и URL не выводятся.

Установщик берёт PostgreSQL advisory transaction lock, сверяет контрольные
суммы неизменённых 001/002 и применяет необходимые последовательные версии в
одной транзакции, сохраняя singleton `analytics_transfer_schema_version`.
Повтор на целевой версии проверяет схему и не выполняет DDL. Неизвестная или
частичная схема и старые прямые установки без marker отвергаются, а не
усыновляются автоматически. При capture readiness требует версию ≥1, при proof
≥2; flags OFF не требуют addon. Проверяется комплект таблиц, колонок, типов,
индексов и ключевых ограничений. Rollout в другую среду требует отдельного
одобрения, плана блокировок, резервной копии и миграционного сценария; этот CLI
специально ограничен synthetic target и не разрешает retained/production DDL.

Перед proof необходимо остановить все старые и не участвующие в capture writers,
обеспечить lifecycle mapping, завершить страницы `activity` и `progress`, затем
выполнить `seal-backfill --stream activity` и `seal-backfill --stream progress`
через synthetic CLI. Seal сериализуется с checkpoint, проверяет отсутствие
непрочитанных строк в фиксированном диапазоне и запрещает последующие страницы,
даже если proof flag в другом процессе выключен. Это не обнаруживает старый
writer, запущенный повторно после seal: его исключение является внешним gate.

`snapshot-stage` принимает `PYMENTOR_ANALYTICS_SNAPSHOT_USER_ID` и
`PYMENTOR_ANALYTICS_SNAPSHOT_ACCOUNT_REF`. Под gate и lifecycle lock он
фиксирует revision, читает legacy days и outbox-only state, активный progress,
проверяет существование совпадающего последнего события и атомарно сохраняет
proof. Новые revision-0 факты здесь не создаются. Границы: 10 000 дней, 5 000
progress, 20 000 исторических ключей и 8 MiB канонических строк. При конфликте,
превышении или deadline транзакция откатывается. DB statement/lock timeouts
ограничены отдельно; wall-clock всего DB snapshot пока не измерен на PostgreSQL.

Digest и proof identity заданы `snapshot_contract.py`; null timestamp и исходная
дата сохраняются, progress tombstones не входят в активный digest. Отдельный
`proof-relay-once` ждёт доставку Core фактов до cut, затем использует P1 POST и
GET receipt. `accepted` не означает `verified`; при потерянном ответе сначала
проверяется тот же proof ID, conflict или потеря ранее подтверждённого receipt
переводят proof в quarantine. Доставка ограничена попытками; исчерпанное число
требует явной диагностики и восстановления оператором.

Перед outbox-only выполните в режиме mirror на synthetic DB `prepare-init
--writers-stopped`, затем повторяйте `prepare-page --limit 100` до
`scan_exhausted=True` и выполните `prepare-seal`. Нужны delivery и snapshot
proof flags и установленный 002. Подготовка сохраняет исходные даты и nullable
timestamp в техническое состояние; курсор фиксируется в той же транзакции,
что и страница. Mirror writer синхронно обновляет state при изменении дня,
поэтому между страницами capture-enabled записи продолжаются. Старая версия
или noncapture writer не участвуют в этом протоколе и должны оставаться
остановленными. Незавершённую подготовку продолжать через `prepare-page`, а не
повторять init. Seal и смена gate сериализуются с mirror-записями. Outbox-only
не допускается до seal и для визитов использует только подготовленный state;
legacy days разрешено читать в подготовке, export, snapshot и replay.
После успешного `finish-replay` barrier удаляется, для следующего переключения
понадобится новая подготовка. Остановка writers и coverage flags являются
внешними утверждениями, не доказательством полноты или права на production.

Analytics read требует доказанный `coverage: complete`, равенство Core
revision до и после запроса и существующий lifecycle. Для admin подписывается
серверный roster до 80 lifecycle и целиком сравнивается с возвращённым vector и
повторным Core roster; переполнение или отсутствие lifecycle дают 503. Заголовки
показывают cut/snapshot и `X-Analytics-Source-HWM: unknown`,
`X-Analytics-Freshness: unknown`; это срез на момент повторной проверки, а не
гарантия актуальности после ответа. Пока старые writers не остановлены, такая
проверка revision не доказывает полноту. Для rollback из outbox-only необходим
существующий replay gate и проверка результата; откат флага не восстанавливает
пропущенные Core days автоматически.

При включённом Analytics a010 Query может дополнительно вернуть пару
`browser_quality: unknown` и `browser_as_of` в UTC. Core принимает пару целиком
или прежний envelope без неё; неполная либо некорректная пара отклоняется.
При authoritative read значения передаются в заголовках
`X-Analytics-Browser-Quality` и `X-Analytics-Browser-As-Of`, а source для
личного ответа становится `mixed`. `coverage: complete` подтверждает только
проверенный Core prefix и не доказывает полноту или свежесть browser-событий.

При analytics read admin profile и quota response получают посещения, streak и
агрегаты из `/internal/analytics/v1/legacy/admin/profile` для lifecycle,
разрешённого Core. Core сверяет роль, lifecycle и revision до и после Query;
для quota Query выполняется до мутации, а роль и lifecycle проверяются ещё раз
под lock перед изменением. Старая форма ответа и default-off путь сохранены.
Новые profile/quota пути проверены in-process с подписанным G1 и fake Query
store; PostgreSQL/HTTP race, недоступность Query и SQL-detector на этих путях
ещё требуют отдельного full-chain прогона.

001 отдельно оставляет revision и checkpoint как PostgreSQL INTEGER для a007.
002 расширяет четыре существующих столбца revision и checkpoint до BIGINT и
использует BIGINT для новых proof/barrier. Последующий synthetic full-chain run
проверил установщик и PostgreSQL roundtrip значений выше 2^31; см.
[cross-service evidence](cross-service.md). Никакой локальный proof не снимает
внешние acceptance и production cutover gates.

## Актуальная synthetic проверка

17 ноября 2026 fresh full-chain a007–a010 завершился с exit 0 и очисткой:
проверены versioned Core installer, proof, запросы, browser факты, privacy
journal и outbox-only/replay; восемь BIGINT полей прошли PostgreSQL roundtrip
значения 2^31+1 с rollback. Условия, ограничения и команда запуска записаны в
[cross-service evidence](cross-service.md). Это не проверка production,
длительной нагрузки или всех возможных старых API и не разрешение cutover.
