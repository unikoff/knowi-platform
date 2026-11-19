# Проверка Analytics в браузере

## Reporting OFF: последовательный живой прогон 17.11.2026

Операция `02e86e0b04ba46be8ad8a69b2caf7d66`, обе временные Analytics БД
фактически a010. Один isolated Chromium, session
`analytics-off-sequential-20261117`, роли проверены последовательно на разных
loopback hosts. Review launcher `--reports-off`: REPORTS и PERSONAL выключены,
TRACKER и LEGACY_VISIT_CUTOVER включены. Это не проверка всех четырёх flags OFF.

Ученик вошёл в свежую synthetic учётную запись. В «Мои данные» показаны
«Данные о времени недоступны», прочерки вместо времени и прежний progress1/1.
Демо отдельно подписано вымышленными данными. Реальные legacy activity/summary
GET200 и collect-grant/batch200; в списке запросов Analytics присутствовали
только collect и events/batch, report-запросов не было. Screenshot
`.local/analytics-browser/review-20261117/personal-off.png` просмотрен root.

Ведущий: прежний course-admin обзор и вкладки «Обзор», «Аккаунты», «Слоты
записи», «Календарь» присутствуют; overview/calendar-context GET200, нового
Analytics блока и report-запросов нет. Само взаимодействие всех вкладок этим
прогоном не проверено. Администратор: `/admin-platform` загрузил прежний обзор,
overview/courses GET200; выбор backend дал overview?course_id=backend GET200,
нового Analytics блока и report-запросов нет. Этот OFF-прогон desktop1440;
mobile/keyboard были проверены в предыдущем ON-прогоне, здесь не повторялись.

Безопасные method/URL/status журналы: `off-student-requests.txt`,
`off-leader-requests.txt`, `off-admin-filter-requests.txt` в той же `.local`
папке. Review trace: `review-5d325e53-407a-48f1-ad75-69fdc64b8758.jsonl`.
Браузер закрыт до release; launcher завершился exit0 и очистил свой cache.
Cross harness завершился exit0, post-hold ID-reuse PASS. Независимо проверены
удаление3DB/12roles/private fixture и отсутствие тестовых Redis ключей.

Предыдущая попытка `b17b1abe906145b98e789c282e68fa32` с тремя одновременными
браузерами прервана при RAM<1536MiB и не считается полным PASS; её ресурсы
также очищены с проверкой. Успешная попытка: preflight3747MiB, минимум среди
5-секундных samples1859MiB (не абсолютный минимум). По разрешению пользователя
закрыт проверенный Telegram18476; основная платформа и звонок сохранены.

## Реальный synthetic hold: 17.11.2026, 09:29–10:29 UTC

Операция `2da86bcd7fff4f5fb44ccce2dcadde12`, реальный Core/Collector/Processor/
Query/PostgreSQL a010. Отдельный установленный Chromium PlaywrightCLI0.1.24,
isolated profile; основной5173/18041 и retained данные не переключались.
Query процесс импортировал `activity_projection_reports.py` версии
`8f09abfa85bab8ed6290bf8601fc2ca3faad18e3113c2889bd3b224999a87916`.
Новые v3/v4 исходники во время hold не подгружались в этот процесс.

- После исправления native fetch receiver получены настоящие collect-grant200,
  batch200 и факты user7. Первоначальный переход на курс записан ровно один раз;
  после reload `course_opened` по-прежнему1.
- Практика: конец последнего idle-сегмента `09:39:46.567Z`, last action
  `09:34:46.568Z` (299999ms). После позднего flush накоплено299573ms/27сегментов;
  между `09:40:25.121Z` и `09:41:36.295Z` значение не выросло (71.174s).
- Playwright по умолчанию эмулировал focus у обеих вкладок. Простое открытие
  blank поэтому НЕ было hidden PASS. После отключения только этой тестовой
  эмуляции через Chromium CDP настоящий DOM урока дал visible/focus=false.
  Теория остановилась на331383ms/31сегментах между `10:05:41.368Z` и
  `10:06:11.492Z`, хотя конец последнего сегмента был лишь через108.524s после
  last action, существенно до idle300s. Это observed blur PASS; настоящий
  `visibilityState=hidden` отдельно не проверен.
- Личный режим «Мои данные» получил реальный signed-grant/details200:
  сначала480108ms (180533теория/299575практика), затем671288ms
  (371713/299575) после дополнительной теории. Экран показал8мин и затем11мин,
  только свой курс/урок, с явной неизвестной полнотой. Демо выбран отдельно;
  неизвестные нули/среднее/число дней не выданы за доказанный ноль.
- Logout через UI `10:25:27Z`; два PG среза `10:27:05.171Z`/`10:27:25.284Z`
  совпали: opened1, theory34/371713ms, practice28/299575ms. До logout уже был
  открыт неучебный Account, поэтому это post-logout отсутствие событий,
  а не отдельное доказательство abort при активном интервале/in-flight ACK.
- Во всех этих срезах Core legacy days user7=0; staff B2 для трёх staff
  identities=0/0/0. Временные dirty buckets стали ready, Core/Analytics cut0
  proof текущий. Визиты и время хранятся в Analytics, не в старой таблице Core.

Два Batch Runs фактически проверили staff UI. Course: pages20+7, одна строка
курса и26 уникальных уроков; выбранный user3=117s, mobile390/keyboard/table
scroll. Platform: global distinct4, backend4, extra1 (без суммы4+1), pages20+9
global,20+7 backend,2строки extra; desktop1440/mobile390 без overflow.
Принудительные503/401/403 были отдельными UI mocks и не являются server ACL
доказательством; реальные ответы после снятия mocks получены отдельно.
Полные ограничения — BATCH_COURSE_REPORTS.md и BATCH_PLATFORM_REPORTS.md.

Core legacy calendar/summary иногда отвечали503 при переходе с урока. Позднее
те же чтения в той же сессии200/52ms и200/53ms; прямой G1 Query тоже200.
Отсутствующий proof опровергнут. Dirty projection — правдоподобная причина по
коду и отдельным срезам, но не снята атомарно с конкретной ошибкой.
Activity worker имеет idle backoff до10s, поэтому Account GET теперь имеют
ограниченные503 retry250/500/1000/2000/4000/8000ms, максимум7 запросов;
401/403 не повторяются, cleanup/scope change отменяют ожидание. Mock503 на2.5s
в браузере дал конечный настоящий200 (requests792–802), затем mock снят.
12 fake-clock tests и полный project TypeScript PASS. Это не latency SLA.

Часовой hold истёк штатно: runner exit0, post-hold ID-reuse PASS. Независимо
проверены отсутствие3временных БД/12ролей, удаление private fixture и пустой
Redis DB13 namespace cross-service-*. ReviewNode36666 завершился0 и удалил
свой cache. Все три тестовые браузерные сессии закрыты.

Private synthetic screenshots/CLI artifacts: `.local/analytics-browser/`;
safe proxy trace: `apps/web/.local/analytics-review-traces/review-0e99ba19-1b33-42c0-b109-c99be2510e89.jsonl`.
OFF reporting runtime ещё UNRUN; launcher `--reports-off` подготовлен и13
offline checks PASS. Полный keyboard/mobile drilldown со второй страницы,
настоящий hidden/sleep и все fault interleavings не объявляются принятыми.

## Предыдущая компонентная проверка с заглушкой

17.11.2026, существующий Vite5173, отдельная IAB вкладка с
`/src/features/analytics-tracker/fixture.html`. Реальный основной Core18041
и сохранённые данные не переключались. На основном frontend flags выключены.

Проверены кнопки запуска, theory→practice, переход на другой курс и выход.
В UI появились по одному `course_opened` каждого курса и соответствующие
сегменты. Максимальный сегмент15011ms. Без нового ввода второй курс дошёл до
299987ms и в последующем наблюдении остался на этом значении. После выхода
состояние «Выход», число42 не выросло; ошибок console нет. UI snapshot снят
встроенным browser API, временная вкладка закрыта.

Fixture обслуживает grants/batch локальной заглушкой. Эта проверка не является
Core/Collector E2E и не доказывает hidden/blur/sleep, offline или все три роли.
Эти пункты проверяются отдельно на синтетическом межсервисном стенде.

PlaywrightCLI0.1.24 подготовлен в игнорируемом npm cache проекта. У системного
Chrome политика запрещает DevTools remote debugging; она не изменялась.
Доступный IAB использован через штатный browser API. Private CLI config и любые
будущие auth artifacts остаются в `.local/analytics-browser`.
## Subsequent source changes, browser recheck pending

After the recorded ON/OFF observations,17.11 source changes added server-owned
browser-presence/header cutover admission and learner metadata fallback with
server Moscow date. Root41Node and49distinctCore tests PASS; actualPG/B2/browser
and fulltsc for this later source are UNRUN. Existing hour-hold results do not
cover those changes. Jira431/comment10351.

Platform Batch additionally fixed same-period report refresh and Moscow-midnight
stale today: root16Node tests PASS, no UI redesign/polling. Its browser/live
recheck is UNRUN. Jira105/comment10353. Course Batch read-only review found no
new frontend issue; that is not a new backend ACL result. Main5173/18041 remains
separate/defaultOFF; K3s test node protectively stopped for low host memory.
