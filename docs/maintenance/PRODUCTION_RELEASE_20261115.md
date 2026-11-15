# Production-выпуск — 15.11.2026

Статус: **DEPLOYED AND VERIFIED**, проверено15:12UTC /18:12МСК.
`https://knowi.ru` открыт; maintenance и его временные ресурсы удалены.

## Зафиксированные версии

- Код: `7fdeb2e13e6ddcb92071dc936d79a4ebcd241e06`.
- Публикация digest: `903b5de13c717a2628ae48145a290006ac33b1f6`.
- Возврат штатных notifications: `cb6d753767d439def915c548378007b6ddebe041`.
- [Полный Release CI43726180172](https://github.com/unikoff/knowi-platform/actions/runs/43726180172): checks/images/publish SUCCESS.
  Пройдены PostgreSQL/Redis suites, backup/restore/migration rehearsal, frontend tests/build,
  image smoke и critical scans всех четырёх релизных образов.

| Образ | Immutable digest |
|---|---|
| Core | `sha256:6387b3f141f23a556cd2bce9de1421a5cdb338ba6e4c4b0f2e8db1f883be6b73` |
| Frontend | `sha256:ae5dad11eb90c786363a95c0d160cfc4c1489817ed3500d86ae7000f31a70069` |
| Runner | `sha256:6e6337a62f918af6394d4f6404565542832748b3e9a12d628dd7f8b15c52fbe0` |
| Migrations | `sha256:32ec62aee8eb7e9ad3eff1275b6812bb644f6cdb9291862d49553c45e9384770` |

## Бэкап и миграция

Argo auto-sync приостановлена до публикации Release. Maintenance проверен для всех четырёх
входных хостов, HTTP/HTTPS, GET/POST и API/Runner. Старый Core и фоновые writers остановлены;
перед dump и DDL подтверждено отсутствие клиентских подключений к рабочей БД.

Свежий PostgreSQL16 custom dump с globals полностью восстановлен в отдельную UUID-базу.
Совпали head c503, строки всех23 прикладных таблиц, их SHA256, владельцы и ACL.
Временная база удалена после сверки точного имени/OID. Bundle зашифрован штатным CMS recipient,
скопирован на локальную машину и успешно расшифрован штатным локальным ключом;
SHA256 dump/globals совпали. Локальный plaintext не записывался.
SHA256 encrypted bundle: `0775bd619c801cfcef67b93ab31743c46aad1d780a281432fb875f04f9292d48`.

Рабочая `knowi_core` переведена `c503_dual_email_change` → `c504_course_progress` отдельной
ограниченной LOGIN-ролью через immutable migration Job. После DDL совпали прежние строки,
владельцы/ACL; исторический progress получил `course_id=backend`, новые unique constraints валидны.
Одноразовые роль, Secret, Job и NetworkPolicy удалены. Downgrade и старый образ после c504
не являются способом rollback; граница восстановления описана в runbook.

## Проверка работающего выпуска

- Argo `argocd-preview/pymentor-preview`: **Synced / Healthy**, путь `infra/gitops/preview`,
  tracking `Release`; auto-sync/self-heal включены, prune выключен.
- Core/Frontend: Healthy stable, по одному новому pod/ReplicaSet, проверены source SHA и digest;
  Service endpoints относятся только к новым pods, Traefik weights100/0.
- Внутренний и публичный API: `/health/ready`, `/health/live`, оба каталога
  `backend`/`backend_copy` (по9треков и310занятий), ожидаемый401 `/api/user/me` без сессии.
- Реальный `/run` для бесплатной задачи обоих курсов: уникальный marker вернулся в stdout.
  Регистрация, отправка email и `/submit` в smoke не выполнялись; progress не записывался.
- Frontend HTML и6 entry JS/CSS доступны. В браузере проверены корневая страница и
  загруженная учебная платформа `/backend` со списком занятий и теорией.
  Терминальный Playwright блокируется системной политикой Chrome; визуальная проверка
  выполнена во встроенном браузере Codex.
- knowi.ru, www.knowi.ru и технический sslip.io: HTTPS200 для `/` и `/health/ready`.
- Notifications возвращены через GitOps после внутренних проверок. Статистика: sent52,
  ожидающих событий и recent failures нет; широкого retry/replay не выполнялось.
- Собственные локальные контейнеры CI удалены, шесть исходных пользовательских контейнеров
  сохранены. Лимит WSL возвращён к6GB.

Приватные дампы, ключи, Secret values и операторские артефакты в Git не включены.
Параллельные архитектурные черновики пользователя сохранены отдельно от этого выпуска.
