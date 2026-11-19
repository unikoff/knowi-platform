# Переход Core c504 → c528: подготовка

Статус: синтетическая репетиция PostgreSQL; production и Kubernetes не проверены.
Исторический `prepare_c504_cutover.py` предназначен только для c503 → c504 и
не готовит этот переход. До операции нужны точные immutable образы, проверенные
манифесты и отдельный план работы с текущим GitOps-контроллером и всеми writers.

| Приложение | Схема | Решение |
|---|---|---|
| Старый Core | c504 | исходное состояние |
| Старый Core | c528 | не открывать; совместимость не доказана |
| Новый Core | c504 | не открывать до миграции |
| Новый Core | c528 | целевое состояние после smoke и проверки прав |

1. Проверить фактический head, владельцев, ACL, место, целевой cluster и revision.
   Подготовить проверенный workflow и запустить его через `maintenance.py run`:
   защищённое окно должно начаться до остановки writers и продолжаться до всех
   внутренних проверок из шага 3. Порядок и восстановление блокировки описаны в
   [инструкции maintenance](maintenance.md). Остановить **все** writers, включая фоновые jobs.
   Проверить отсутствие работающих процессов и незавершённых транзакций. Снять
   согласованный c504 backup с владельцами и ACL; заранее проверить восстановление
   в отдельной БД и сохранить контрольные результаты, включая deletion outbox.
2. Выполнить миграцию один раз отдельным короткоживущим LOGIN с правом `SET ROLE`
   постоянного владельца существующих таблиц. Задать `KNOWI_MIGRATION_URL`,
   `KNOWI_MIGRATION_HOST`, `KNOWI_MIGRATION_DATABASE`, `KNOWI_MIGRATION_USER`,
   `KNOWI_MIGRATION_OWNER=knowi_migrator` и `KNOWI_MIGRATION_APP_ROLE=knowi_app`;
   имена подтвердить по фактическим ролям. Владелец должен создавать объекты в
   схеме; login, владелец и app — разные роли. Цель `c528_course_leaders`.
   Секреты и доступ выдаются только на время операции. Не запускать исторический Job.
3. При неопределённом результате сначала проверить Job, транзакцию и фактический
   Alembic head. Не повторять вслепую. Под maintenance проверить owner и целевые
   права новых объектов, сохранность строк и событий удаления, готовность нового
   Core и прикладные read/write smoke; убедиться, что endpoints ведут только на
   новый Core. Лишь после полного прохождения проверок открыть записи и трафик.

После commit c528 старый образ и `alembic downgrade` не являются проверенным
возвратом: downgrade миграций явно запрещён. Предпочтителен forward fix. Для
возврата к c504 остановить writers и восстановить согласованный snapshot в
изолированную свежую БД с владельцами и ACL, проверить строки и deletion outbox.
**Любые записи после snapshot, включая события удаления, будут потеряны при
простом restore**: до открытия необходимы явная инвентаризация и согласованная
сверка/перенос всех таких изменений либо forward fix. Наличие backup само по себе
не доказывает безопасного возврата.

Локальная воспроизводимая DB-проверка (Core virtualenv, Docker и образ
`postgres:16`):

```powershell
services/core-api/.venv/Scripts/python.exe tooling/ci/rehearse_c528_cutover.py --execute
```

Она создаёт и удаляет собственный UUID-контейнер с синтетическими данными;
проверяет c504 → c528, транзакционный отказ при неверной роли, владельца и
выборочные права app, trigger, отказ downgrade и точное восстановление выбранных
строк, событий удаления, owners и ACL. Запись после snapshot демонстрирует
потребность в reconciliation. Реальный legacy binary, HTTP, полный набор таблиц,
конкурентных writers и K3s rollout эта репетиция не проверяет.

## Офлайн-пакет c528

`prepare_c528_cutover.py` создаёт только локальные JSON-файлы и не исполняет
операцию. Он требует пять digest (`core`, `frontend`, `runner`, `migration`,
`analytics`) и совпадение `source_sha`, затем сравнивает весь `release.json`
с содержимым `infra/gitops/preview/release.json` в локально доступном
`GITOPS_SHA`. Эта сверка сама по себе не доказывает публикацию commit, успешный
CI, доступность образов или соответствие реальных deployment manifests: их
нужно независимо подтвердить по источнику публикации и registry.

```powershell
python infra/gitops/operations/prepare_c528_cutover.py `
  --release-file $releaseFile --code-revision $codeSha --gitops-revision $gitopsSha `
  --app-uid $liveApplicationUid --old-path $liveApplicationPath `
  --old-revision $liveApplicationRevision --owner $actualOwner --app-role $actualAppRole `
  --output $newOutputDirectory
```

UID, path и revision считываются из фактического Application в проверенном
context/namespace; оба JSON Patch атомарно проверяют UID и исходный source,
а switch также требует отключённый auto-sync и закрепляет GitOps SHA. Если
публикация изменит отслеживаемую ветку, auto-sync надо остановить **до неё**
отдельно подготовленным и проверенным UID-guarded patch: пакет с опубликованным
release не может служить предварительным барьером до самой публикации. Если
sync уже выключен, сгенерированный pause patch с проверкой `enabled=true` не
применять; убедиться, что нет выполняющейся sync operation. Проверить также
целевой Application и SHA непосредственно перед каждым применением.

Job использует временный LOGIN `knowi_c528_<первые 12 символов CODE_SHA>`,
Secret `knowi-core-migrations` с ключом `KNOWI_MIGRATION_URL`, точный target
`c528_course_leaders`, `backoffLimit=0` и deadline 300 секунд. Owner и app role
задаются явно и должны совпадать с реальными ролями БД; login должен иметь
право SET ROLE владельца. Подтвердить DNS-имя, базу `knowi_core`, endpoint
57152 и назначение namespace/selector NetworkPolicy до запуска: они переняты
из исторического c504 и не являются свидетельством актуального состояния.
Policy добавочная, ограничивает ingress PostgreSQL по namespace и полному SHA;
проверить также эффективные существующие разрешения, DNS и egress Job. После
установления результата отозвать временные доступы и удалить именно созданные
временные ресурсы. Не выводить секреты в лог.

Пакет не управляет maintenance, backup, writers или Argo sync. `maintenance.py
run` требует отдельно созданных и проверенных оператором operation/check scripts,
которые удерживают окно до завершения всей операции и внутренних проверок. До
их готовности автоматизированный production cutover не подготовлен.
