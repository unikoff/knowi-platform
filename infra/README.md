# Инфраструктура

`infra/` содержит конфигурацию окружений монорепозитория. Dockerfile и код приложений остаются в `services/core-api`, `services/runner-pool`, `apps/web`.

- `helm/` — chart и values.
- `gitops/` — Argo CD, окружения и bootstrap.
- `local-k3s/` — локальный Kubernetes-стенд, сборка/импорт образов.
- `local-postgres-redis/` — одноразовый Compose-стенд с управляемой миграцией и smoke.

Действующий Argo Application может всё ещё содержать `spec.source.path: deploy/gitops/preview`. Перед выпуском нужно проверить его live state и обновить Application на `infra/gitops/preview` через bootstrap или адресное изменение. Перенос вложенного Git-каталога сам по себе не обновляет внешний Application.

Перестройка не выполняет rollout, миграцию production-данных или смену Secrets/PVC. Контейнерный writable path Core `/app/data/core-api` сохранён.

Одноразовый Compose-стенд:

```sh
docker compose -f infra/local-postgres-redis/compose.yaml up --build -d core
docker compose -f infra/local-postgres-redis/compose.yaml --profile tools run --rm smoke
```

Данные этого стенда временные. Выпуск описан в [PRODUCTION_AUTOMATION.md](../.github/PRODUCTION_AUTOMATION.md), схема — в [Core README](../services/core-api/README.md).
