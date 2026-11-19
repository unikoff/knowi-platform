# Перенос существующей статистики Core в Analytics

> Целевое решение от 17.11.2026 описано в [обзоре сервиса](service-overview.md#активный-день-и-перенос-истории). Ниже сохранены исходная карта Core и результаты проверки совместимого чтения. В целевом режиме активные дни строятся из измеренного времени. Исторические даты импортируются с отдельной отметкой условных 1 ч теории и 1 ч практики. Графики и рейтинг завершений рассчитывает Analytics. Календарь бронирований и факты прогресса принадлежат Core.

Снимок исходников 17.11.2026: ветка `feach/Analitick`, HEAD
`f787672ff2639434f0be331d05def5d8f31b3c1e`, с текущими рабочими изменениями.
Это подготовительный анализ для [CORP-117](https://platformpy.atlassian.net/browse/CORP-117).
Перенос данных, переключение чтения и отключение записей здесь не выполнены.

## Уточнение фактической разгрузки, 17.11.2026

Исходная карта ниже описывает baseline; последующие synthetic проверки и
реализация отражены в EXECUTION.md. При delivery/proof/grants, режиме чтения
`analytics`, записи `outbox_only` и подготовленном DB gate запросы calendar,
summary и admin visit/profile пропускают Core `user_activity_days`. Новый
активный таймер, интервалы и дневная нормализация находятся в Analytics.
Core сохраняет identity/access, авторитетный progress, transactional outbox,
revision/proof и возможность совместимого возврата. Publisher сканирует
bounded страницы; полный snapshot формируется только для нового cut,
а не безусловно каждые пять секунд.

Полная разгрузка всех производных показателей пока не подтверждена: глобальный
admin overview и course dashboard ещё группируют completions из Core progress
(`adapters/sql/administration.py`, `adapters/sql/role_admin_reads.py`). Это
остающийся scope CORP-89/432: перед заменой нужны полное покрытие/паритет и
сохранение отображаемых identity/ACL. Progress rows остаются в Core; удалять
их вместе со статистическими вычислениями нельзя. Фактическое снижение Core
CPU/RPS этой source-проверкой не измерено.

Новый серверный browser-presence gate предотвращает выдачу старого Core prefix
за календарь с browser days: Core flag по умолчанию0;1 требует analytics reads
и проверенную browser metadata, иначе503/unavailable. Existing P1 endpoint
передаёт серверную policy заголовком; Web только после него и валидного catalog
отключает legacy visit. До этого learner fallback использует POST{} с Moscow
day сервера. Это новая source/offline проверка (41Node/49distinctCore PASS),
а её actualPG/B2/browser повтор ещё UNRUN. Ранее выполненный native a011 chain
не доказывает live работу более поздних изменений. Main/retained не переключены;
Core56 и приёмка переноса остаются отдельными формальными gates.

## Что сейчас записывает Core

`Workspace` вызывает `useRecordActivityVisit` после появления пользователя.
Клиент отправляет `POST /api/user/activity/visit` с датой браузера; ошибка запроса
подавляется. Core принимает дату либо использует дату Москвы и сохраняет
`user_activity_days(user_id, activity_date, visited_at)` с уникальностью user/date.
Повторная отметка дня не заменяет время; старый NULL может быть заполнен.

Это отметка посещённого дня, а не длительность занятия. `visited_at` — время
серверной отметки, которое не доказывает исторический первый вход. В текущей ORM
`users` отдельных `first_seen`/`last_seen` нет. Из старого дня нельзя восстановить
минуты, курс, урок, фокус вкладки или тип теории/практики. Клиентская дата и
подавление ошибок ограничивают полноту и доверие историческим данным.

## Матрица функций и владельцев

| Функция | Текущий источник/потребитель | Целевой владелец и граница |
| --- | --- | --- |
| Отметка дня и время отметки | SQL activity adapter; `/api/user/activity/visit` POST и `/api/user/activity` GET | Analytics принимает и хранит совместимые факты; Core сохраняет проверку identity/access и надёжную доставку, если запрос пока идёт через него |
| Календарь, today, число дней, streak, последние 7 дней | identity activity/summary; аккаунт пользователя | Analytics рассчитывает существующие значения; форма действующего ответа и правила дат сохраняются до отдельной версии |
| Недельные завершённые уроки внутри summary | `UserLessonProgress` | Core остаётся авторитетным владельцем прогресса. Analytics получает доверенные факты/срез по контракту; прогресс не вычисляется из посещений |
| Активность в карточке ученика | administration student profile; `AdminStudentProfile` | Совместимое аналитическое чтение с прежними ACL; профили, имена и доступ остаются в Core |
| Глобальные посетители и тренд | administration dashboard; platform role-admin reads | Analytics считает по подтверждённому историческому охвату. Неизвестную активность по курсу нельзя выдавать за ноль или распределять по курсам |
| Удаление пользователя | Core administration account deletion | Core инициирует обязательное удаление; Analytics применяет независимый durable journal/fence до открытия чтения/worker после restore |
| Новое активное время по курсу/уроку | В текущем Core не измеряется | Tracker и расчёт Analytics этапа 3; старые дни не являются его входными минутами |

Подписки, бронирования, баланс занятий, аккаунты, права, каталог и авторитетный
прогресс остаются в Core. Исторический охват ролей фиксируется в fixtures;
ограничение новой учебной телеметрии ролью user не оправдывает незаметную потерю
старых ответов API при переносе.

## Порядок переключения

1. После приёмки CORP-109 и Core CORP-56 повторно зафиксировать реальный build,
   schema и полные ответы consumers в CORP-117. Эта исходная карта не заменяет
   приёмку готового Core.
2. Через существующие работы CORP-443–446 определить versioned серверный контракт
   и identity mapping. Сохранить исходные даты и NULL; обозначить legacy/global
   provenance. Не придумывать course_id и не подгонять старые строки под новый
   browser active-segment контракт.
3. Включить надёжную доставку новых изменений с ограниченным outbox/retry и
   идемпотентными ключами. Бизнес-транзакции Core не должны ждать HTTP Analytics.
   Analytics не читает Core SQL/ORM и не получает доступ к Redis auth-сессиям.
4. Выполнить bounded, возобновляемый backfill и догнать concurrent записи.
   Сверить покрытие истории, полный ответ календаря/streak/summary/admin,
   NULL/TZ, ACL и удаление. Минуты и новые экраны не prerequisites переноса.
5. Переключить совместимые чтения управляемым флагом. До отключения старого
   writer доказать parity и возврат на актуальных данных, включая записи,
   поступившие после переключения. Сохранить исходные таблицы/SQLite и архив.
6. Только после доказанного переключения прекратить старые статистические
   вычисления/записи Core. Gate CORP-454 фиксирует разгрузку Core и совместимость;
   отключение Core статистики до готового Analytics недопустимо.

## Источники и проверки

- `apps/web/src/app/Workspace.tsx:44` и
  `apps/web/src/features/account/useRecordActivityVisit.ts:6` — инициатор отметки.
- `services/core-api/pymentor_core/modules/identity/api/router.py:129` — POST;
  строки160–180 — чтение дней/summary. Router монтируется в `/api/user` и
  совместимый `/user`; GET пути — `/api/user/activity` и
  `/api/user/activity/summary`.
- `services/core-api/pymentor_core/adapters/sql/models.py:135` — таблица дней;
  `adapters/sql/activity.py:17` — запись, строка119 — связанный Core progress.
- `services/core-api/pymentor_core/modules/identity/activity_policy.py:41` —
  правила существующего summary/streak.
- `services/core-api/pymentor_core/adapters/sql/administration.py:201` — dashboard,
  строка653 — удаление, строка761 — профиль ученика;
  `adapters/sql/role_admin_reads.py:323` — глобальная активность, course null.
- `apps/web/src/features/account/useAccountSchedule.ts:33`,
  `apps/web/src/features/account/AccountWorkspace.tsx:40` и
  `apps/web/src/features/admin/students/AdminStudentProfile.tsx:83` — consumers.
- Существующие проверки: `test_core_ports.py:105`,
  `test_c2_1_3_api_contracts.py:547`, `test_account_progress_deletion.py`,
  `test_role_admin_scope_http.py:67`. В этом анализе они не запускались.

Подтверждено по исходникам независимым Terra scout. Новые parity/backfill/rollback
проверки — PROPOSED; runtime-миграции и разгрузка Core пока не доказаны.
