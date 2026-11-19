# CORP-60: контракт каталога Analytics v1

Это отдельная спецификация и in-memory reference consumer, не Core exporter, доставка или хранилище. Core остаётся источником каталога. Текущий `courses/registry.json` и `courses/backend/course.yaml`, `courses/backend_copy/course.yaml` задают продукты `backend` и `backend_copy`: совпадающие track, lesson и task ID не объединяются. Схема Core `services/core-api/contracts/course-package-v2.schema.json` допускает Unicode track ID; `LocalCourseProvider` выбирает продукт в каждой операции. Fixture использует точный первый track из обоих manifest; lesson/task IDs и версии в нём синтетические. Он не является полным снимком содержимого.

`services/core-api/tests/regression/test_c226_analytics_contract.py` и соседний `fixtures/c226` — синтетический Core v1 oracle: его `course_id` обозначает track, не product. `map_legacy_track` принимает только явно заданный ровно один product для точной строки track. Отсутствие и несколько кандидатов отклоняются; совпадение названий, default course и автоматический выбор запрещены. Для одинакового track двух продуктов нужна дополнительная достоверная контекстная информация и отдельно построенная однозначная карта; функция сама её не извлекает. Старый `v2.map_v1_track` оставлен без изменений, а его словарь сам по себе не выявляет ранее потерянную неоднозначность: адаптер обязан проверить кандидатов до вызова.

## Контракт и применение

`analytics_catalog_snapshot` и `analytics_catalog_delta`, `schema_version=1`, отличны от синтетического `CoreControl`/`catalog_snapshot` schema v2 CORP-58. Snapshot содержит один product и все его tracks, lessons и tasks, включая tombstones. Ключи: `(course_id, lesson_id)` и `(course_id, lesson_id, task_id, task_revision)`; track привязан к lesson отдельно и сохраняется посимвольно. Допускаются только ID, заголовки, порядок, aliases и tombstones; content, paths и private tests запрещены через extra=forbid. Заголовки/aliases должны предоставляться доверенным producer как безопасные метаданные; validator не определяет PII внутри произвольного текста. Алиасы уникальны в области уроков продукта или заданий урока и не заменяют product identity. Изменение revision, заголовка, порядка или track создаёт новую версию; старый подтверждённый snapshot хранит прежнее значение. Удаление обозначается tombstone, а не исчезновением идентификатора; предыдущие aliases и tombstones сохраняются. Реестр истории здесь только в памяти, на время жизни объекта.

Delta представляет полное новое состояние продукта с `base_version` и последовательным `sequence = previous + 1`: это намеренно простой контракт, не частичный patch. Первый snapshot может начать наблюдение с любой положительной sequence; более ранняя история неизвестна. Последующий delta допускается только от текущего подтверждённого base, новая версия уникальна в продукте. Повтор той же версии и состояния с корректной базой возвращает duplicate; другой payload для версии, потерянный или переставленный delta отклоняются без изменения подтверждённого состояния. Нельзя считать отсутствие Core подтверждением новой версии. Восстановление пропущенного диапазона требует получить отсутствующие deltas; схема не определяет безопасный перескок или восстановление утраченной истории. `classify` проверяет конкретную подтверждённую версию, включая aliases/revision/tombstone; неизвестная версия или продукт возвращают `pending/unknown_catalog`, не success. Повторная проверка после получения версии — обязанность будущего consumer; хранение pending, quarantine и их ограничения здесь не реализованы.

## Совместимость и переход

| Producer → consumer | Результат |
| --- | --- |
| CORP-58 synthetic `CatalogSnapshot v2` → существующий v2 consumer | Без изменений; прежняя схема/fixtures проверяются отдельно |
| Полный catalog v1 → v2 consumer | Несовместимо: новые kind, schema и структура; не отправлять напрямую |
| v2 snapshot → catalog v1 | Недостаточно сведений: нет task/revision, title/order и полной multi-track структуры; автоматического преобразования без доверенного обогащения нет |
| c226 v1 → catalog v1 | Нужна явная однозначная track→product карта и отдельно полученный полный доверенный каталог; готового importer нет |

Для будущей интеграции сначала согласовать права/источник и новые readers, затем экспорт и надёжную доставку, backfill истории и сверку всех версий и mapping, лишь после подтверждения переключать consumers. Смешанные версии, восстановление, сохранение pending и авторизация не проверены. Откат кода не восстанавливает потерянную историю или неоднозначно сопоставленные факты; удаление старого контракта/данных без сверки является необратимой границей. Ни backfill, ни переключение, ни доступ к Core DB не выполнялись.

## Повтор проверки

```powershell
$env:PYTHONPATH='services/analytics'
services/analytics/.venv/Scripts/python.exe -B services/analytics/tools/export_catalog.py --check
services/analytics/.venv/Scripts/python.exe -B services/analytics/tools/export_contracts.py --check
services/analytics/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider services/analytics/tests/catalog services/analytics/tests/contracts -q
```

JSON Schema описывает форму; проверки уникальности, последовательности, истории и mapping исполняет Python oracle, а не сама схема. Настоящие транспорт, атомарное durable сохранение версии и pending, конкурентные consumers, права, сбой и повтор с Core требуют реализации и отдельных испытаний на этапе 2.
