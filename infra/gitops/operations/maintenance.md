# Ручная страница технических работ

Заглушку обслуживает отдельный Nginx. При включении маршрутов он отвечает
внешним клиентам, пока frontend и API обновляются. Для работ с данными отдельно
остановите внутренние процессы, которые продолжают писать в базу.

Перед использованием подтвердите актуальный контекст и namespace кластера,
список внешних входов, Traefik entrypoints и приоритеты, имена TLS Secrets,
доступность указанного frontend image по immutable digest и отсутствие конфликта
ресурсов `knowi-maintenance`. В генераторе указаны хосты и TLS Secrets,
использованные при выпуске 15.11.2026.

## Подготовка

Из корня репозитория, указав digest проверенного образа и новый каталог:

```powershell
python infra/gitops/operations/prepare_maintenance.py --frontend-digest $frontendDigest --output $maintenanceDir
```

Укажите в `$frontendDigest` полный digest формата `sha256:…` и в
`$maintenanceDir` путь к новому каталогу. Команда создаст локальные
`backend.json` (ConfigMap, Deployment, Service) и `routes.json` (три IngressRoute).
По умолчанию в комплект входит `apps/web/public/maintenance.html`. Другую
страницу в UTF-8 можно передать через `--page`.

## Включение

1. Примените **только** `backend.json` в подтверждённом контексте и дождитесь
   готовности Deployment и Service endpoints. TCP readiness проверяет только
   открытый порт: отдельно проверьте содержимое через локальный port-forward
   Service, до публикации маршрутов.
2. Через port-forward проверьте `GET /`, `HEAD /`, `POST /`, `GET /api` и
   `POST /api/example`. Ожидаются статус 503 и `Cache-Control: no-store`.
   Браузерные пути отдают HTML, API возвращает JSON. Откройте страницу в браузере.
3. Примените `routes.json` только после проверки backend. Правила с приоритетом
   2000000000 перехватывают все пути четырёх исторических входов: `knowi.ru`,
   `www.knowi.ru`, `13-140-29-11.sslip.io`, `13.140.29.11` по HTTP и HTTPS.
   HTTP сохраняет доступ к `/.well-known/acme-challenge/`. HTTPS использует TLS Secrets
   `knowi-tls` для доменов knowi.ru и `preview-tls` для sslip.io и IP.
4. Проверьте каждый актуальный вход и оба протокола: главную и вложенную страницу,
   API и пути выполнения и отправки задач. Проверьте статус 503 и `no-store`
   для GET, HEAD и POST и убедитесь, что запросы обслуживает maintenance Service.

## Снятие

После успешной внутренней проверки приложения удалите три маршрута из
`routes.json`. Проверьте возврат обычных ответов сайта и API на всех входах.
После этого удалите объекты из `backend.json`. Если внешняя проверка выявит сбой,
перепроверьте maintenance Service и верните маршруты из `routes.json`.

Nginx сохраняет статус 503 при внутренней подстановке страницы.
[Документация `error_page`](https://nginx.org/en/docs/http/ngx_http_core_module.html#error_page).
