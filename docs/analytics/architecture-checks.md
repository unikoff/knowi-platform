# CORP-57: проверки архитектуры и дальнейшая приёмка

Снимок исходников и границы описаны в [architecture.md](architecture.md). Здесь **PROPOSED** означает условие будущей проверки, а не выполненный тест. Для каждого запуска нужно зафиксировать revision/image и версии, изолированный synthetic dataset, конфигурацию, команды, expected и actual, время, логи без PII и ответственного. До реального сбора нужны решения CORP-69; реальные Core DB, Tracker и UI в первый этап не подключаются.

## Структурная приёмка CORP-57

Проверяем документом: три логические зоны и отдельные entrypoints; таблица ownership и публичных портов; subject-wide partition/fence; матрицы запуска, ресурсов, хранилища и extraction; явные границы Core и отдельный trusted progress; первый этап synthetic. Документ и обзор источников доказывают только согласованность проекта, а не его реализацию.

## PROPOSED: сценарии для реализации CORP-439–442

| Владелец | Сценарий и dataset | Ожидаемый наблюдаемый результат |
| --- | --- | --- |
| CORP-439 | Изолированный стенд без Core, Redis, Runner и course mounts; независимый synthetic issuer/producer; contract и architecture tests | Collector → общие facts → synthetic handlers Processor → Query; отсутствие Core imports, межсервисного SQL/FK/FDW и приватных межмодульных импортов; разные logical DB/roles и отдельная history |
| CORP-439 | Проверка прав ролей DB и запрета приватных импортов; попытка Processor изменить immutable payload | Операция запрещена; владелец Collector пишет payload, Processor может записать только разрешённое техническое/производное состояние |
| CORP-440 | Crash до commit, crash после commit до ACK; потеря ответа, retry тем же namespace/event_id/hash; затем тот же ID с иным payload | До commit нет подтверждённого факта; после commit reconciliation находит факт; повтор duplicate без второго факта/job, иное содержимое conflict; ACK только после commit |
| CORP-440 | Невалидный grant/source, staff telemetry, forged subject, unsupported schema, превышение лимитов и отказ DB | Отказ без записи факта; 429/Retry-After или 503 по контракту при перегрузке/недоступности; частичные результаты batch идентифицируют каждый item; никакого ложного ACK |
| CORP-441 | Два worker, один subject в разных курсах/устройствах, late и переставленные события, replay с отдельной identity | Один неперекрывающийся результат на subject с детерминированным attribution; события не теряются по high watermark; replay и live не смешивают checkpoints; сравнение с независимым oracle |
| CORP-441 | Lease expiry/takeover между вычислением и commit; гонка delete против старого worker и Query | Новый generation проходит, старый не коммитит/ACK; subject fence берётся до partition lease; удалённый lifecycle не выдаётся и не восстанавливается поздним событием или replay |
| CORP-441 | Crash между вычислением и транзакционным commit, poison item, отмена/drain; backlog replay одновременно с privacy | Нет ACK без effects и downstream jobs, и нет дублированного эффекта после повтора; poison в quarantine с причиной и bounded retry; cancellation не выдаёт ложный успех; privacy сохраняет зарезервированную ёмкость |
| CORP-442 | Остановить Query, затем Processor и Collector по отдельности; измерить повтор приёма и восстановления | Отказ каждого процесса локализован в его функции; Collector продолжает принимать при остановленном Query в пределах DB capacity; после восстановления backlog обрабатывается без потери подтверждённых фактов |
| CORP-442 | Restore disposable Analytics DB из snapshot до удаления с независимым journal; отдельно неполный journal | Выдача и workers закрыты до применения и проверки актуального watermark; удалённый субъект не возвращается; при неполном journal readiness остаётся закрытой |
| CORP-442 | Нагрузочный synthetic профиль и replay/backup одновременно, отдельный рост Collector 1→2, Processor 1→2, Query 1→2 | Измерены latency, lag, ошибки, pool/CPU/RAM/IOPS/storage и recovery в утверждённом CORP-113 бюджете; общая физическая DB/SPОF и предел single primary показаны; ускорение не предполагается |

Проверки сети, service accounts, secrets, namespace, AppProject, Helm/GitOps, маршрутов и backup/restore относятся к разрешённой среде CORP-439/442. Не проводить production fault injection. Приватность, replay и восстановление требуют реальной disposable PostgreSQL и конкурентных процессов; in-memory oracle или статическая схема сами по себе не доказывают поведение.

## Ещё не подтверждено

Runtime, тесты отказа, фактические права БД, миграции, производительность, deployment, интеграция Core, read/write ACL и UI не выполнялись в рамках этого документа. Схемы событий и численные лимиты оформляются CORP-58/59/60/69/113. Синтетическая проверка не подтверждает готовность к реальным данным или production.
