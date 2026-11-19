# Изолированный browser review Analytics

Launcher использован в отдельных synthetic browser holds; фактические результаты и ограничения записаны в EXECUTION.md и BATCH_*_REPORTS.md. Каждый следующий review требует действующего hold и измеренного бюджета памяти. Основной Vite на `127.0.0.1:5173` и его cache launcher не изменяет.

## Запуск после GO

Нужен уже работающий synthetic cross-service run в состоянии `browser_hold`. Root передаёт только его неперсональный 32-символьный UUID; никакие credentials, token или содержимое private directory в команду не вставляются. Из `apps/web`:

```powershell
node tools/analytics-review.mjs --check --operation <operation-uuid>
node tools/analytics-review.mjs --run --operation <operation-uuid>
```

Для отдельной проверки выключенных экранов отчётов после остановки предыдущего
review listener добавьте `--reports-off`. Этот test-only режим выключает только
REPORTS/PERSONAL_MEASUREMENTS; TRACKER/LEGACY_VISIT_CUTOVER остаются включены,
чтобы UI-тест не возвращал старую запись посещений в Core. Он не доказывает
выключение всех четырёх flags. Не запускайте оба режима на одном порту одновременно.

`--check` только читает и проверяет hold, без открытия портов и запуска Vite. `--run` повторяет проверку и запускает один Node/Vite процесс. Он слушает только `127.0.0.1:18782`; два встроенных TCP proxy слушают `127.0.0.2:18782` и `127.0.0.3:18782` и передают байты без переписывания HTTP или WebSocket. Все порты строгие: занятый адрес вызывает отказ и очистку созданных launcher ресурсов.

| Synthetic роль | Адрес |
| --- | --- |
| Ученик | `http://localhost:18782/backend/` |
| Администратор | `http://127.0.0.2:18782/backend/` |
| Руководитель | `http://127.0.0.3:18782/backend/` |

Разные host обеспечивают разные host-only session cookies; не подставлять `127.0.0.1` вместо `localhost` для ученика. Оба IP proxy и доступность `localhost` на целевой Windows нужно фактически проверить после GO. Вход и cookie-тест требуют разрешённой synthetic fixture; launcher не читает и не показывает её учётные данные.

Проксирование сохраняет исходные `Host` и `Origin`: `/api/analytics/events/batch` идёт на Collector `127.0.0.1:18152`, `/api/analytics/reports/` — на Query `127.0.0.1:18153`, остальной `/api` — на Core `127.0.0.1:18151`. Core CSRF остаётся включён. В дочерней среде launcher удаляет унаследованные `VITE_*` и включает только `VITE_ANALYTICS_TRACKER_ENABLED`, `VITE_ANALYTICS_LEGACY_VISIT_CUTOVER`, `VITE_ANALYTICS_REPORTS_ENABLED`, `VITE_ANALYTICS_PERSONAL_MEASUREMENTS_ENABLED`. `.env` не загружается. Это режим synthetic review, не разрешение на пользовательский cutover.

## Диагностика review

Только `--run` создаёт новый файл `apps/web/.local/analytics-review-traces/review-<uuid>.jsonl` через exclusive create и печатает его **точный абсолютный путь** в строке `Synthetic review trace:` после старта. Этот файл сохраняется после остановки launcher. Каталоги и их реальные пути проверяются на ссылки/junction; существующий файл не перезаписывается. Для чтения использовать точный напечатанный путь:

```powershell
Get-Content -LiteralPath '<путь из строки Synthetic review trace>'
```

Trace ограничен локальными `localhost`, `127.0.0.2`, `127.0.0.3` и точными методами/маршрутами analytics catalog, collect/report grant, batch, activity report/details, user/me и старого platform overview. Query string отбрасывается; сохраняются только host alias, method, route, HTTP status и заранее разрешённые поля: корректность catalog version и количество уроков; наличие grant, корректность UUID/времён/TTL и длительность proxy request; у report — фиксированные status/quality, признак null as_of, числовые totals и количества строк. Тела запросов, cookies, headers, JWT, UUID пользователя, произвольные ошибки и сырые ответы не записываются. Ошибочные, сжатые, не-JSON или превышающие 1 MiB ответы сохраняют только HTTP-метаданные; запись ограничена 5 MiB. Диагностика не меняет ответ и не прерывает трафик при ошибке записи.

Только review HTML содержит скрытый `#analytics-review-focus`: его атрибуты `data-visibility`, `data-focused`, `data-secure-context` и `data-random-uuid` отражают `document.visibilityState`, `document.hasFocus()`, `window.isSecureContext` и тип `window.crypto?.randomUUID`; они обновляются на соответствующих событиях и раз в секунду. Он ничего не меняет в состоянии приложения и не подменяет focus или события. Обычный Vite конфиг и приложение этот элемент не получают.

## Guard и cleanup

Требуется прямой каталог `services/analytics/.local/cross-service/<operation-uuid>`, без символьных ссылок/junction. При старте и каждые 250 мс guard проверяет `owner.json` с `state=browser_hold`, неизменными owner sentinel, `browser_holder_pid`, expiry, живым holder PID и отсутствием `release.request`. Адреса и identity fixture сверяются с owner; значения sentinel и содержимое fixture не выводятся. Guard использует и системные, и монотонные часы; любое изменение identity, ошибка чтения, завершение holder, release или истечение срока останавливают listener и закрывают текущие соединения. `process.kill(pid, 0)` подтверждает существование PID, но не защищает от теоретического повторного использования PID; настоящий cross-service hold отдельно проверяет свои процессы и принадлежность listener к job.

Для явно заданного DEBUG hold разрешён срок до 3600 секунд; launcher допускает одну дополнительную секунду округления временной метки при начальной проверке. Без явно заданного hold значение по умолчанию остаётся 0. Продление уже принятого hold и бесконечный запуск запрещены: изменённый expiry останавливает launcher. Это ограничение синтетической loopback-диагностики, не TTL grant и не изменение privacy-проверок.

Используется отдельный временный cache внутри `apps/web/.cache` с уникальным именем `.analytics-review-*`; при штатном завершении проверяются его абсолютный путь, непосредственный родитель и отсутствие ссылки, затем удаляется только этот каталог. Тот же путь cleanup используется после startup error и SIGINT/SIGTERM. Принудительное завершение процесса ОС закрывает его сокеты, но может оставить review-cache: при ошибке cleanup launcher сообщает об этом без автоматического удаления другого каталога. Launcher не удаляет fixture, БД, процессы Core/Analytics или чужие listeners. Пользоваться существующей командой cross-service `--release <operation-uuid>` для завершения hold; launcher увидит release, а владелец hold выполнит свои собственные reconciliation и cleanup.

## Проверки и границы

В `apps/web`:

```powershell
node --check tools/analytics-review.mjs
node --check tools/analytics-review-diagnostics.mjs
node --test --test-isolation=none tools/analytics-review.test.mjs tools/analytics-review-diagnostics.test.mjs
```

Тесты не открывают listening socket: моделируют hold, проверяют отказ при release/expiry/смене sentinel/потере PID/ссылках, сверяют endpoints и флаги, передают неизменённые HTTP/WebSocket bytes через in-memory streams, проверяют закрытие переданных synthetic handles, а также создают и удаляют отдельный тестовый cache, сохраняя соседний файл. Они не доказывают фактический bind, startup Vite, поведение HMR, cookie isolation, CSRF, frontend render или сквозной API. Эти проверки выполняются после GO против названного synthetic hold.

Локальная версия Node — 24.18.0, Vite — 8.1.3. Встроенный Vite использует `createServer`, `listen`, `close`, `strictPort`, `server.fs` и `proxy`; при запуске не загружает общий `vite.config.ts` и не перегенерирует общий course registry. Опора на установленный `vite/dist/node/index.d.ts` и source Vite 8.1.3; документация [Vite JavaScript API](https://github.com/vitejs/vite/blob/v8.0.10/docs/guide/api-javascript.md) и [Node 24 net](https://nodejs.org/docs/latest-v24.x/api/net.html) описывает соответствующие вызовы. Совпадение всех настроек с основным dev-сервером не заявляется: этот режим предназначен только для ограниченного review.
