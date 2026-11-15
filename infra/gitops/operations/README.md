# Одноразовый переход c503 → c504

Статус: **APPLIED AND VERIFIED 15.11.2026**. Выпуск с c504 работает в production;
maintenance снят, Argo Synced/Healthy. [Фактический отчёт](../../../docs/maintenance/PRODUCTION_RELEASE_20261115.md).
Далее сохранена одноразовая инструкция с исходными условиями; повторно выполнять её на c504 нельзя.

Baseline до выкладки 15.11.2026: VPS `knowi01`, K3s context `default`, Application
`argocd-preview/pymentor-preview`, UID `69a2f746-52ef-4965-a581-4b9d3f6dba1e`.
Argo CD 3.6.1 читает `Release:deploy/gitops/preview`, автоматическая синхронизация и
self-heal включены. Новая структура использует `infra/gitops/preview`.

Рабочая БД — **knowi_core**, PostgreSQL16.15, head `c503_dual_email_change`, роль
runtime `knowi_app`, владелец таблиц `knowi_migrator`. Цель:
`migrated-data.pymentor-rehearsal-data-20261109.svc.cluster.local:57152`.
Отдельная `pymentor_preview` с c426 не является рабочей целью миграции.

## Почему нельзя обычный canary

Новый Core требует c504. Работающий Core принимает только c503; после DDL его
readiness перестанет проходить. Миграция меняет unique constraints прогресса,
старый код не фильтрует progress по course_id, downgrade намеренно запрещён.
Существующий Rollout оставляет старый stable при весах10/25/50, а live analysis
идёт через публичный sslip.io. Maintenance сломает analysis; преждевременное
снятие maintenance вернёт часть запросов на старый stable. Автоматический PreSync
миграции эту несовместимость не устраняет.

## Подготовка пакета

`prepare_c504_cutover.py` только создаёт локальные файлы. Он не использует kubectl,
SSH, credentials и не меняет кластер. Нужны **два разных назначения SHA**:

- `CODE_SHA` — commit приложения, по которому CI построил образы; совпадает с `release.json.source_sha`.
- `GITOPS_SHA` — commit с опубликованными digest в `infra/gitops/preview`. В текущем CI это следующий commit бота, а не CODE_SHA.

Из зафиксированного GITOPS_SHA получите `release.json` и проверьте его происхождение,
все digest и связь с успешным CI. Затем:

```sh
python infra/gitops/operations/prepare_c504_cutover.py \
  --release-file /private/release.json \
  --code-revision "$CODE_SHA" --gitops-revision "$GITOPS_SHA" \
  --output /private/c504-cutover
```

Генерируются UID-guarded JSON Patch для паузы auto-sync и смены пути на pinned
GitOps revision, плюс Job с immutable migration image и явной целью c504.
Job использует отдельный Secret `pymentor/knowi-core-migrations`, ключ
`KNOWI_MIGRATION_URL` с asyncpg URL отдельного пользователя **knowi_c504_<CODE_SHA[:12]>**.
Администратор создаёт этот временный LOGIN как INHERIT member роли владельца схемы
`knowi_migrator`, без SUPERUSER/CREATEDB/CREATEROLE/REPLICATION/BYPASSRLS. Это позволяет
выполнить ALTER существующих таблиц, сохранив владельца, без поиска или ротации старого
пароля владельца. После завершения Job проверяются владельцы/ACL, затем Secret и
временный LOGIN удаляются. Host/database/user
закреплены отдельно в Job и должны совпасть с URL; runtime Secret не заменяет доступ
мигратора. Значения Secret не хранятся в Git. Отдельный файл
`01-migrator-networkpolicy.json` разрешает вход к PostgreSQL только от migration Pod
в namespace `pymentor` с полным source SHA выпуска и только на TCP 57152. Это
дополнительное правило к действующим NetworkPolicy; DNS и исходящий доступ Job
нужно также проверить в репетиции.

## Порядок исполнения после успешной репетиции

1. Подтвердить host, Application UID, head c503, schema policy старого образа,
   владельца таблиц и будущие immutable digests. Сохранить исходные manifests и revision.
2. **До push/публикации нового Release** приостановить auto-sync текущего Application
   уже сохранённым в репозитории `pause-application.patch.json` (он не требует новых
   image digests); убедиться, что нет уже выполняющейся sync operation.
   Workflow сам публикует digest в Release: пауза должна быть подтверждена раньше этого события.
3. Опубликовать и дождаться успешного CI/scan/images; зафиксировать именно GITOPS_SHA.
   Проверить `release.json` из этого commit и подготовить остальные файлы пакета.
4. Включить независимый maintenance для **всех** внешних входов: knowi.ru,
   www.knowi.ru,13-140-29-11.sslip.io,13.140.29.11; HTTP и HTTPS. Проверить, что API,
   interpreter и task run/submit больше не принимают внешние операции.
5. Остановить/дренировать все старые Core writers и соответствующие фоновые работы,
   приостановить notifications CronJob, сверить активные Job/подключения к рабочей БД.
   После подтверждённой паузы Argo и maintenance удалить старый Core Rollout с
   `--cascade=foreground --wait=true`, проверив его сохранённую идентичность.
   Дождаться отсутствия его pods и ReplicaSets. `replicas=0` вместе с `paused=true`
   в проверенном weighted Rollout оставляли stable pod живым; это не остановка writers.
   Сохранённые manifests/старый commit нужны для возврата до DDL. Проверить также
   внутренних клиентов: удаление Rollout не останавливает независимые workers/Job.
6. После остановки writers создать защищённый согласованный backup, проверить его
   восстановление и сохранить head/aggregate hashes прогресса. До DDL это точка возврата.
7. Перед запуском Job применить `01-migrator-networkpolicy.json` в namespace БД и
   проверить, что его source SHA совпадает с меткой Pod в `02-migration-job.json`.
   Выполнить Job один раз; backoff0, deadline300s. При неизвестном результате сначала
   проверить живой Alembic head и Job logs, не повторять вслепую. После завершения Job
   и выяснения результата удалить именно эту временную NetworkPolicy по её полному
   имени из артефакта. Подтвердить c504, сохранность прежних строк/ID/timestamps и
   default backend; при ошибке сохранить maintenance до решения о восстановлении.
8. Под maintenance переключить Application патчем `03-switch-pinned-path.patch.json`.
   Auto-sync остаётся выключенным. Отрепетированный bootstrap должен создать новый
   c504 stable без старых c503 ReplicaSet/endpoints; обычный canary здесь не применяется.
   Старый Rollout уже удалён на шаге5. Новый создаётся как первый deployment;
   selectors, ownership, первый stable и отсутствие преждевременного analysis
   проверяются изолированной репетицией перед выполнением на рабочем кластере.
   До ручной sync также удалить старый `frontend-progressive` с foreground cascade
   и проверить отсутствие его pods/ReplicaSets: его обычный canary analysis обращается
   к публичному сайту, который ещё закрыт maintenance. Первый deployment нового
   frontend пропускает эти шаги; обе новые stable ревизии проверяются до открытия.
9. Проверить внутри namespace нужные digest/revision, readiness, auth, запись/чтение
   профиля, оба курса, изоляцию прогресса, logout и worker. Проверить, что все Service
   endpoints относятся только к новому Core. При сбое оставаться в maintenance.
10. Только после этих проверок согласованно вернуть notifications/GitOps и снять
    maintenance; затем повторить внешние smoke на knowi.ru. Перед возвратом tracking
    `Release` сравнить его head с проверенным GITOPS_SHA: не разрешать незамеченный drift.

## Граница восстановления

До подтверждённого commit c504: после проверки фактической схемы можно вернуть
старый выпуск/маршруты. После commit c504: старый образ и `alembic downgrade` не
являются допустимым rollback. Используется исправление вперёд с course-aware кодом
либо восстановление согласованного backup при остановленных writers с явным учётом
всех записей после backup. Простое восстановление старого dump потеряет новые записи.

Это управляемая операция выпуска, не действие команды подготовки. Для реального
окна всё ещё нужны опубликованные immutable images/успешный CI, отдельный migrator
Secret, свежий согласованный backup и проверка остановки всех writers.

## Выполненная локальная репетиция

15.11.2026 выполнены PostgreSQL16 c503→c504 tests и настоящий pg_dump/restore на
собственных disposable DB, затем отдельный свежий K3s1.37.1/Argo Rollouts1.10.2/
Traefik3.7.15 стенд. Команды из корня (нужны Docker, kubectl и зависимости Core):

```sh
python tooling/ci/rehearse_c504_backup.py --container OWN_PG16_CONTAINER --output NEW_PRIVATE_OUTPUT
python tooling/ci/rehearse_c504_rollout.py --execute
```

Backup helper жёстко ограничен synthetic PG16 на127.0.0.1:57382; это не команда
резервного копирования production. Rollouts helper создаёт собственный временный
Docker K3s, не использует текущий kube-context и удаляет только свой кластер.

Фактически проверены: deny-all перед migration policy; SQL57152 только для нужного
source SHA, отказ Redis6379 и другому SHA; все4 хоста ×4 пути ×GET/POST ×HTTP/HTTPS;
maintenance503/no-store, сохранение барьера при сбое, foreground-удаление старого
Rollout/pods/ReplicaSets, создание нового stable без canary pause/AnalysisRun, оба
Service endpoints только на новые pods, веса Traefik100/0 и HTTP200 c504 после открытия.

Это раздельные проверки: в K3s используется synthetic Core/TCP listeners, базовый
Nginx и тестовый self-signed TLS. Настоящие DDL, backup и restore проверены отдельно
на PG16; production credentials и TLS Secrets в стенд не переносились. Полный отчёт —
[release readiness](../../../docs/maintenance/RELEASE_READINESS_20261115.md).

Опора: [Alembic current](https://alembic.sqlalchemy.org/en/latest/api/commands.html#alembic.command.current),
[Argo phases/waves](https://argo-cd.readthedocs.io/en/stable/user-guide/sync-waves/).
