# Изолированный browser tracker v2

Статус: локальная source-интеграция в Workspace с синтетическими проверками; флаги по умолчанию выключены. Реальный сбор и приёмка этапа не выполнены. Старый `useRecordActivityVisit` и Core activity API сохранены. Контракт: `services/analytics/pymentor_analytics/contracts/v2.py`, JSON schemas и fixtures в `services/analytics/contracts/`, `docs/analytics/contracts.md`, `metrics-v1.md`, `privacy.md` и задачи CORP-67, 70–76.

## Интеграция

`useAnalyticsTrackerIdentity` создаёт контроллер при `enabled: true`. По умолчанию Workspace вызывает legacy visit. При `VITE_ANALYTICS_TRACKER_ENABLED=true` и выключенном `VITE_ANALYTICS_LEGACY_VISIT_CUTOVER` действует параллельный режим. Tracker включается для роли `user` при опубликованном каталоге и заголовке Core `X-Analytics-Collection-Mode: parallel`. При обоих включённых флагах legacy-запись отключена на всём жизненном цикле страницы. Tracker ожидает каталог и `X-Analytics-Browser-Presence-Required: true`. При недоступных метаданных измерения приостанавливаются. Режим legacy можно вернуть явным переключением флагов.

Согласованный с Core `GET /api/learning/analytics-catalog?course_id=...` возвращает `{course_id, catalog_version, lesson_ids}`; `null` и пустой список означают отсутствие подтверждённой публикации. Workspace требует валидную версию и пересечение server allowlist с загруженным разрешённым уроком. Некорректный ответ, отказ, смена user/course или timeout закрывают tracker; периодические запросы ограничены и не выполняются при выключенных флагах. Связь receipt с текущим digest обеспечивает Core; этот фронтенд её самостоятельно не доказывает.

BrowserTransport обращается к Core `POST /api/analytics/grants/collect` с cookie и JSON `{course_id}`, а к Collector `POST /api/analytics/events/batch` — с bearer grant без cookie. Fetch используют `no-store`; `baseUrl` задаёт префикс приложения. Запросы возможны только при роли `user` и валидном курсе.

Существующие точки вызова в Workspace:

```tsx
import { useAnalyticsTrackerIdentity } from "../features/analytics-tracker/useAnalyticsTracker";

const analytics = useAnalyticsTrackerIdentity({
  enabled: sources.tracker, // policy Core и подтверждённая metadata
  identityKey: user ? String(user.id) : null,
  role: user?.role ?? null,
  courseId,
  catalogVersion: publishedCatalog?.version ?? null,
  canTrackLesson, // server allowlist и проверка загруженного материала
  baseUrl: import.meta.env.BASE_URL.replace(/\/$/, ""),
});
// В существующем onActivePanel(panel), кроме setActivePanel(panel):
analytics.onActivePanel(panel);
// Синхронно до logout, expiry, deletion или замены пользователя:
analytics.stopBeforeLogout();
```

`onActivePanel` получает только глобально активную и видимую панель Dockview 8.5.1. Подписка проверяет изменение params и видимости и освобождается при cleanup. `id` и `kind` должны совпадать для theory/practice. Редактор относится к practice. Если catalog version ещё нет, grant и сбор для курса не запускаются. `App.tsx` пересоздаёт Workspace при смене auth revision и очищает очередь в памяти. Logout останавливает tracker до запроса. При ошибке выхода tracker создаётся заново.

Grant каждый раз запрашивается заново для курса конкретной партии и не кешируется. `subject_ref` сохраняется только в памяти как граница lifecycle: смена очищает неподтверждённую очередь до отправки. Уже выданный grant может оставаться действительным до TTL; отзыв или переключение сессии нужно сообщить tracker немедленно. `server_time` и `expires_at` проверяются как UTC, RTT ограничен пятью секундами; `ServerClock` якорит UTC на оценке server time и монотонном времени. Долгий запрос, истёкший grant, неподходящее время или скачок локальных часов останавливают сбор до нового допустимого ответа. Серверное время имеет секундную точность, оценка середины RTT не является точной синхронизацией часов. Старый standalone `browserClock` не даёт этой гарантии и не используется готовым контроллером. Wire не содержит timezone; группировка и display TZ относятся к отчёту.

Для прямого применения engine порт `grant(identityKey, signal, courseId)` должен возвращать grant либо `{status: 401 | 403}`; `send(grant, {events}, signal)` — `{status, body?, retryAfterMs?}`. Два аргумента старого callback остаются совместимы на уровне TypeScript, но пользовательский provider обязан учитывать третий аргумент и lifecycle. `queue` может быть только volatile memory; `VolatileQueue` при создании очищается. Никаких grant, identity или событий в localStorage/IndexedDB. Отмена уже отправленного запроса не гарантирует, что сервер его не принял; повтор использует те же ID и payload.

## Поведение и ограничения

События v2 содержат пустой payload, случайный event UUID, learning session UUID, курс и каталог. Новый Tracker отправляет только `activity_segment`. Старый `course_opened` поддерживается Collector для совместимости. Время хранится неперекрывающимися в рамках экземпляра интервалами до 60 секунд с точностью до миллисекунды. Пять минут после действия ограничивают измерение. Интенсивный ввод продлевает idle без отправки событий на каждое нажатие.

Адаптер отслеживает видимость, focus/blur, pagehide/pageshow, действие и online. Проверка каждые 15 секунд контролирует разрывы, завершает полные минуты и сохраняет остаток до следующей минуты или границы контекста. Задержка более 20 секунд, отрицательный монотонный интервал или расхождение wall/monotonic более секунды отбрасывают неизвестный промежуток. Поведение короткого системного сна различается между ОС. Пересечения между вкладками и устройствами разрешаются в Analytics.

Политика `privacy.md`: только память, максимум 24 часа / 200 событий / 512 KiB; при достижении границы удаляются старейшие неподтверждённые записи, с фиксированным счётчиком потери без содержимого. При logout/смене личности и 401 очищается вся очередь и отменяется незавершённый запрос; 403 удаляет и блокирует только затронутый курс. Состояние после перезагрузки не восстанавливается. Это намеренная потеря необязательной телеметрии, а не ACK. Технические счётчики доступны `testCounters()` для synthetic tests, UI и логи не добавлены.

Партия одного курса ограничена 100 событиями, 256 KiB UTF-8 и 4096 bytes на событие. Внутренняя FIFO-очередь сохраняет порядок внутри курса; wire v2 не содержит отдельного sequence. ACK сопоставляется по индексу и проверяет ненулевой event ID: accepted/duplicate снимаются, conflict/rejected снимаются с отдельными счётчиками, pending и отсутствующие/некорректные ACK остаются. После сетевой ошибки, timeout или неизвестного ACK повторяет тот же payload. Не более восьми попыток на запись, таймаут попытки 10 секунд, backoff с jitter до 60 секунд, соблюдение Retry-After и общий возраст очереди. Если Retry-After больше возраста записи, она истечёт без повторной отправки. Это bounded at-least-once попытки, не гарантия доставки или exactly-once. Серверная атомарная дедупликация обязательна.

Проверки из `apps/web`: `node --test --test-isolation=none src/features/analytics-tracker/tracker.test.mjs` и узкий `tsc` новых файлов. Тесты используют synthetic clock, память и фальшивый transport; они не доказывают взаимодействие реального browser, API, серверной дедупликации или device union. Перед включением нужны реальные browser lifecycle/route/auth проверки, интеграция каталога и проверка совместно с backend и privacy gates.

Для разрешённой браузерной проверки предусмотрен `/src/features/analytics-tracker/fixture.html` на существующем Vite: он использует только синтетический fetch stub и не отправляет события на сервер. Проверить theory/practice, смену курса, blur/возврат и logout; выводит только synthetic события. Сам запуск Vite и проверка браузера не входят в эту локальную реализацию и выполняются после согласования окна runtime у root.
