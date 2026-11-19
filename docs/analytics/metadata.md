# CORP-79: metadata и время события

Helper подключён к HTTP и транзакционному store. `RealCollection=false` сохраняется. Контракт ограничен `AcceptedEvent` v2: verified `BrowserIdentity` даёт `subject_ref` и `producer_namespace`, source всегда серверный `browser`; `received_at` берётся из server clock и приводится к UTC. Исходные aware timestamps браузера не исправляются. В helper не передаются IP, forwarded headers, user-agent, referrer, query, cookies, device или geo; произвольные поля v2 запрещены. Это не доказывает proxy identity E2E на других уровнях.

`build_record(identity, event, *, clock=...)` формирует запись без проверки окна и допускает инъекцию часов для теста; naive server clock отклоняется. `validate_new_record(record)` выбрасывает `MetadataRejected`, поле `reason` равно `future_clock` или `too_late`. Default policy CORP-59: `ended_at <= received_at + 120s` и `received_at <= ended_at + 7 days`, включительно. Предел 60 секунд и idle 300 секунд проверяются v2, повторно их здесь не меняем. Порядок доставки сам по себе не запрещён; offline событие внутри окна допустимо. `make_record(identity,event,*,clock=...)` объединяет обе операции и безопасен **только для заведомо нового ключа**.

## Обязательная граница dedup и wiring

`canonical_hash` исключает `received_at`. Router вызывает только `build_record`; публичная clock policy находится в contracts/metadata_v1.py. PostgresIntake сначала разрешает duplicate/conflict под subject lock; только inserted факт валидируется по времени до jobs/commit. Ошибка откатывает tentative insert, не резервирует ID и возвращается как rejected/future_clock либо rejected/too_late. Committed retry через8дней остаётся duplicate, изменённый payload —conflict. Эти два кода добавлены в пока не выпущенный v2 и схемы пересозданы; старых внешних consumers нет, последующая публичная совместимость требует обычной проверки версии.

`uvicorn.run(..., access_log=False)` в штатном CLI отключает Uvicorn access log, который мог содержать URL/query. Это не отключает другие логгеры, access logs proxy/ingress или другие способы запуска; их конфигурация и проверка остаются отдельной задачей. Этот код не добавляет журнал запросов и не выводит headers/body.

## Проверка

```powershell
$env:PYTHONPATH='services/analytics'
services/analytics/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider services/analytics/tests/metadata -q
```

Helper-тесты проверяют identity/source, UTC, границы и CLI. Отдельный real-PG HTTP suite подтвердил rejected/future_clock и too_late без фактов/jobs, duplicate/conflict через8дней и retry после потерянного ACK. Его readiness/catalog/early fence — synthetic doubles, commit fence — реальный DB check. Proxy identity E2E, полная privacy recovery и внешняя конфигурация логов остаются непроверенными.
