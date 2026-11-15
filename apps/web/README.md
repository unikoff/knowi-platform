# Web

Общая React-оболочка платформы. Страницы занятий и лендинги продуктов находятся в `courses/<course_id>/frontend/`; учебные компоненты — в `courses/shared/ui/`. `apps/web/src/` содержит оболочку, кабинет и авторизацию.

Из этого каталога:

```sh
npm ci
npm run dev
npm run build
npm run test:release
```

Vite по умолчанию проксирует `/api` на `http://127.0.0.1:18001`; `PYMENTOR_DEV_API_TARGET` меняет цель. Для совместного запуска с Core используйте `tooling/dev/start.*` из корня — [инструкция](../../README.md).

`VITE_BASE` задаёт публичный базовый URL сборки, например `/platform/`. Credentials в `VITE_*` не размещаются. Сборка — `dist/`, кэш — `.cache/`, логи и локальные результаты — `.local/`; все исключены из Git.

`courses/shared/demo/` — исходники галереи `/backend/demo`. `authoring.html` — локальный предпросмотр для разработки уроков. `tools/course-registry.mjs` генерирует реестр из manifests в `.cache/course-registry/`; `test:release` проверяет зарегистрированные курсы, общие компоненты, API-клиент, границы Vite и учебные контракты. Серверная практика/настройки продуктов в браузерную сборку не входят.

Dockerfile выполняется с контекстом корня: `docker build -f apps/web/Dockerfile -t knowi/web:local .`. Nginx настроен на Kubernetes Core DNS; этот upstream применяется только к `/api/`.
