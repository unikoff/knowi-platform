# CORP-81: цикл synthetic Processor

`analytics processor` — отдельный процесс с `/live` и `/ready`; при импорте модуля и в процессах collector/query worker не запускается. В lifecycle processor создаётся `PostgresProcessor` через публичный `ProcessorStore`, после чего запускается цикл. Существующий `ClosedPrivacyReadiness` всегда закрыт: даже при доступной PostgreSQL цикл не вызывает `claim`, а `/ready` возвращает 503. Открытие authenticated privacy barrier и подключение реальных данных остаются отдельной задачей; переключателя готовности через env нет. Явно внедряемый probe применяется только в тестовом harness.

Подписки `synthetic.activity` и `synthetic.independent` с версией 1 берутся из `contracts.work_v1`, не содержат replica ID и обрабатываются отдельными задачами. Сбой/poison одной подписки не останавливает другую при наличии доступной ёмкости. Каждая партия до 100 immutable фактов вычисляется `synthetic_delta`: по ещё не применённым фактам определяются счётчики на курс и XOR fingerprint. Это техническая synthetic projection, не минуты, посещения или подтверждённый прогресс. Факты, пришедшие после claim, относятся к другим jobs. DB adapter отвечает за атомарные effects, dedup receipts и ACK; stale lease/epoch не даёт commit. Тесты цикла с fake port не доказывают эти транзакционные свойства.

## Ограничения и остановка

По умолчанию максимум 2 одновременных задания, DB pool не более 2 и overflow 0; при меньшем pool параллелизм дополнительно уменьшается. Только эти значения доступны из role-specific `ANALYTICS_PROCESSOR_...`:

| Переменная после префикса | Значение по умолчанию | Допустимый диапазон |
| --- | --- | --- |
| `MAX_INFLIGHT` | 2 | 1–2 |
| `POLL_MIN_SECONDS` | 0.25 | 0.05–5 |
| `POLL_MAX_SECONDS` | 5 | 0.1–30 и не меньше min |
| `JOB_TIMEOUT_SECONDS` | 10 | 0.1–60 |
| `CONSUMERS` | обе synthetic подписки | непустой список разрешённых IDs через запятую |

При отсутствии работы или отказе цикла пауза растёт с bounded backoff/jitter до `POLL_MAX_SECONDS`. Lease выдаётся на 30 с, пока идёт вычисление отправляется bounded renew; DB commit заново проверяет fence и generation. Перед claim и commit проверяется readiness. Прерывание до начала commit освобождает известный claim, а ошибка вычисления или timeout перед commit передаётся `fail` для ограниченного retry/quarantine в storage. При timeout/cancel уже начатого commit результат неизвестен: цикл не выдаёт ACK и не делает предположений о rollback, восстановление идёт по lease expiry и dedup receipts.

CPU-вычисление выполняется в отдельном ограниченном `ThreadPoolExecutor`, не в HTTP event loop. Слот общего пула и слот конкретного consumer освобождаются по фактическому завершению thread, даже если ожидание в async задаче уже отменено. Очередь вычислений ограничена числом consumer и слотов; при одном доступном слоте физически невозможно выполнять два consumer одновременно. Python thread нельзя принудительно безопасно остановить: текущий handler является ограниченной чистой функцией от партии, а uncooperative/бесконечный handler не поддерживается.

При штатном завершении новые claims прекращаются, текущие задачи получают до `SHUTDOWN_TIMEOUT_SECONDS` на drain. Затем задачи отменяются и им даётся до 1 с на реакцию; если порт игнорирует cancellation, shutdown сообщает ошибку и не выдаёт фиктивный успех. Для аварийного kill и незавершённого commit исход остаётся неизвестным; восстановление обеспечивает срок lease и транзакционный dedup adapter. Это не доказательство exactly-once и не измерение производительности.

## Проверки

`tests/processor_runtime` использует контролируемые fake ports: закрытый privacy gate, независимость подписок, deterministic delta, poison, stale commit, потерю lease, смену readiness, отмену compute и commit, корректный drain, зависший порт и удержание CPU slots. Эти сценарии являются тестами приложения, не PostgreSQL. Отдельные тесты двух DB workers, lease takeover, idempotent commit, receipts и новых jobs выполняются владельцем PostgreSQL adapter в изолированной Analytics DB. Product Query, runtime recovery с настоящим privacy journal и нагрузочный профиль сюда не входят.
