# Изолированные пакеты курсов — локальная реализация

15 ноября 2026. Целевой проект: `C:\Project Python\Pymentor_platform`.

## Что изменилось

Новый курс подключается папкой `courses/<course_id>` и записью `courses/registry.json`. Manifest задаёт название, публичный slug, backend-программу и три frontend entrypoints. Vite генерирует lazy loaders в ignored `.cache`; Core загружает собственный `backend/catalog.py` курса. Ручных правок общего frontend/Core при добавлении продукта больше нет. После изменения реестра требуется перезапуск dev/API или сборка.

`apps/web/src/lessons` и `services/core-api/content` выведены из авторства. Активные занятия/metadata/программа/практика/полные личные лендинги находятся в пакетах `backend` и `backend_copy`. Устаревшие 553 Core content-файла сохранены побайтово в `courses/backend/archive/legacy-content` с `PRESERVATION.json`. Архив не исполняется как fallback. Из 387 старых frontend-файлов 386 совпадали с каноническими; более новая версия Lesson04_1 сохранена в Backend. Независимые правки Backend Copy не заменялись.

Общие визуальные инструменты и CSS — `courses/shared/ui`; галерея с 21 инструментом/57 примерами — `courses/shared/demo`. Палитра остаётся единой в `apps/web/src/shared/theme/palettes.ts`; shared UI использует её CSS-переменные. Локальная геометрия отдельных авторских иллюстраций остаётся рядом с занятием. Общий backend SDK — `courses/shared/backend`; skills и роли для AI — `courses/shared/ai`. Глобальные настройки агентов не менялись.

Оба курса используют общий аккаунт/подписку и отдельный прогресс. Это независимые пакеты внутри одного frontend build и Core-процесса; авторский Python/JS является доверенным кодом проекта. Backend/settings/archive/AI не становятся frontend entrypoints.

## Проверено

- Временный `isolation_fixture` со slug `isolation-fixture`: собственная программа из одного занятия открылась через API и в браузере. Каталог реальных Backend/Copy содержал по 9 глав; новый — 1 главу. Его прогресс в фактической тестовой PostgreSQL не появился в других курсах.
- Финальная репетиция «папка + реестр»: 350 общих frontend/Core/shared исходников остались неизменными. Неудачное обновление версии при сломанном catalog откатило manifest. Временный продукт убран из реестра; исходники проверки сохранены вне основного проекта.
- Обе сборки `/` и `/platform/` прошли с тремя и затем двумя курсами. YAML/registry/routes/SSR/demo/private HTTP: 28 проверок. Shared UI/metadata/визуальные инструменты: 66 проверок. Backend package/protocol/consumer проверки описаны в отдельных отчётах; это пересекающиеся серии, их числа не суммируются.
- Финальные интеграционные тесты на принадлежащих задаче PostgreSQL/Redis: 8 PASS. Проверены доступ/подписка, прогресс двух пользователей/курсов, конкурирующие записи, Runner и восстановление тестовой очереди; реальная доставка заменена тестовым транспортом.
- Живой локальный Runner выполнил `print(6 * 7)` и вернул `42`. Windows сообщает о недоступности Linux OS memory/CPU limits — эта проверка подтверждает локальную функциональность, а не серверную изоляцию.
- 310 lesson IDs/aliases/titles/sources и 204 task IDs/revisions/full private hashes в каждом курсе, а также content revision совпали с независимым снимком до переноса.
- Браузер: оба курса загрузились, настоящее занятие Copy отрисовалось, временный курс отрисовал shared компоненты; галерея переключает примеры и интерактивный checklist. Светлая/тёмная тема меняет app/shared CSS roles. Прямые и кодированные `/@fs` запросы к backend/settings/archive/AI возвращали 403/404; обычные SPA URL возвращали HTML, а не приватные исходники.
- Предупреждения: существующий большой video.js chunk и deprecation TestClient/httpx. Они не скрывались изменением настроек.

## Как пользоваться

Полное руководство: [courses/shared/README.md](../../courses/shared/README.md).

```powershell
Set-Location -LiteralPath 'C:\Project Python\Pymentor_platform'
& '.\services\core-api\.venv\Scripts\python.exe' -B tooling/courses/create_course.py my_course --from backend --slug my-course --title 'Мой курс'
```

Первая строка выбирает проект. Вторая создаёт самостоятельный пакет из активного Backend и проверяет его перед добавлением в реестр; существующие файлы не перезаписывает, получателя Telegram не копирует. Затем редактируйте только новый пакет. Версию backend-материалов обновляет `tooling/courses/pin_course_version.py my_course`; все пакеты проверяет `services/core-api/tools/check_courses.py --courses-root courses`. Frontend `npm run build` из `apps/web` сам обновляет generated registry.

На этом компьютере существующая одноразовая лаборатория запускается через Docker Desktop и два контейнера `pym189-mc460-20261113-2048-source`, `mc460-20261113-2048-redis`. Локальные API и Vite можно совместно запустить:

```powershell
& '.\services\core-api\.venv\Scripts\python.exe' -B tooling/dev/run.py --mode lab --api-port 18011 --web-port 18531
```

Команда использует только ранее созданную тестовую PostgreSQL/Redis, отключает dotenv/реальные уведомления и занимает терминал; `Ctrl+C` останавливает её дочерние процессы. Если порты уже заняты текущим проверенным запуском, повторный запуск не нужен. Адреса: `/` — каталог; `/backend`, `/backend_copy` — обучение; `/<slug>/personal` — личный лендинг; `/<slug>/about` — заглушка лендинга курса; `/backend/demo` — библиотека.

## Сохранность и границы

Результат сначала собирался в изолированной копии. Перенос в MAIN выполняется по SHA-256 plan с резервными копиями до первой записи и журналом каждой замены/удаления. Конкурентные пользовательские изменения сверены; root README, Core README и актуальный runtime_suites сохранены. Итог импорта и финальная проверка основной папки записываются в `main-verification.json`.

Старая SQLite `services/core-api/.local/legacy/test.db` не подключалась к SQL; копия/контрольная сумма сняты как у обычного файла. Git, CI/CD, infra/VPS и реальные сообщения не использовались для изменений. В начале один worker ошибочно выполнил read-only `git status`; Git-состояние не менялось. Работа не является релизом или переносом данных на сервер.
