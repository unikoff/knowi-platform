# Личная статистика: локальная интеграция, 17.11.2026

Владелец поручил этому форку обновить личный кабинет и дать чтение статистики
через Analytics. Ветка общая `feach/Analitick`; родительский чат отдельно занимается
CORP-62. Здесь нет K3s/deployment/DDL/backfill/отключения старых writers. Изменения
пользователя, retained Core/Analytics DB и история сохраняются.

## Граница первой рабочей версии

Core аутентифицирует текущего пользователя и экспортирует только его сырые даты
посещений и доверенные факты завершений. Отдельный stateless Query-процесс Analytics
вычисляет личный отчёт. Он не читает Core SQL/Redis и не хранит персональный snapshot.
Браузер получает отчёт через текущий Core API; серверная подпись не попадает в UI.
Это совместимое чтение имеющейся истории, а не завершённая миграция её хранения.

Старые дни не кодируют курс/минуты/теорию/практику. Время в отчёте null с явным
coverage, без демонстрационных чисел. Core остаётся владельцем прогресса, прав,
подписок и бронирований. Старые endpoints и записи посещений не отключаются.

## Контракт

Публичный authenticated self endpoint Core:
`GET /api/user/analytics/statistics?days=7|30&course_id=<current course>`.
Нельзя выбирать user_id/subject_ref. Существующие user-session/role/revocation
проверки обязательны; курс ограничивает завершения, дни посещений остаются глобальными.
Ответ при недоступном сервисе —503, без молчаливого fallback в старые вычисления.

Private Analytics: `POST /internal/learner-statistics`, loopback18051.
Строгий bounded request (512KiB), подписанный Core JWT EdDSA в Authorization:
iss `pymentor-core`, aud `analytics-learner-report`, scope `learner:report`,
kid `learner-preview-1`, sub совпадает с subject_ref, iat/exp (TTL<=60s),
`snapshot_sha256` SHA256 точных UTF-8 bytes HTTP body. Ключи отдельные от g1/p1;
private key только Core. Нет cookie forwarding, PII/request body/access logs.

Request JSON:
```
{schema_version:1, subject_ref:UUID, snapshot_at:aware ISO timestamp,
 period_days:7|30, course_id:string|null, activity_complete:bool,
 activity_dates:[YYYY-MM-DD],
 lesson_completions:[{course_id:string, lesson_id:string, completed_at:aware ISO timestamp}]}
```
Максимум10000 дат и5000 завершений, extra fields запрещены. Snapshot не старше60s
и не из будущего>5s. Core ограничивает чтения limit+1; неполнота не превращается
в заведомо неверные totals. Невалидная авторизация401/403, тело413/422, отказ503.

Response JSON:
```
{schema_version:1, source:"analytics", as_of:ISO timestamp,
 period:{days:7|30, start:YYYY-MM-DD, end:YYYY-MM-DD, timezone:"Europe/Moscow"},
 scope:{course_id:string|null, activity:"platform"},
 coverage:{visits:"legacy_global"|"partial", time:"unavailable", progress:"core"},
 summary:{active_days_total:number|null, current_streak:number|null,
          active_days_period:number, completed_lessons_period:number,
          active_seconds:null, theory_seconds:null, practice_seconds:null},
 days:[{date:YYYY-MM-DD, visited:bool, completed_lessons:number,
        active_seconds:null, theory_seconds:null, practice_seconds:null}]}
```
days содержит все7/30 дней по возрастанию, включая сегодня по Moscow.
Streak как действующий Core: если сегодня не было отметки, серия может
заканчиваться вчера. Future даты не входят в метрики. При activity_complete=false
all-time иstreak null. Завершения дедуплицируются по course_id/lesson_id; чужой курс
не входит в selected-course progress. Нулевые наблюдаемые counts —0, неизвестное
время —null. Новые поля не заменяют доверенный progress current course из Core.

## Интерфейс и приёмка

Сохранить существующий тёмный стиль/токены, привести масштаб графика к разумной
высоте, периоды7/30, ясную область «посещения платформы» и «прогресс текущего курса».
Удалить demo time, не превращать unknown/error/loading в0. Разделить загрузку,
ошибку с retry, пустую историю и готовые реальные значения. Смена user/course/period
не показывает старой персональной информации; запросы отменяются или fenced.
Бронирования/оплата/настройки/достижения/история совместимы. Backend tests:
подпись/hash/expiry/subject/body limit, формулы/границы дней/dedup, self-only.
Frontend types и meaningful API/state tests; широкий/узкий браузерный рендер,
период, ошибка/retry, отсутствие demo/fake values. Настоящий локальный
Core→Analytics→UI smoke на сохранённых данных без записи/миграции.

Root owns integration/runtime/docs; Terra workers receive nonoverlapping files.
No deployment/commit/push, old writers retirement or accepted Jira gates claimed.
