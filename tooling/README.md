# Общие инструменты

- `dev/` — совместный запуск Core и web: `run.py --help`, `start.bat`, `start.sh`.
- `ci/` — межсервисные проверки runtime, выпуска и образов.
- `courses/` — общие проверки структуры, планов и визуалов уроков.
- `.local/` — результаты и браузерные артефакты; исключены из Git.

Команды выполняются из корня. Python берётся из `services/core-api/.venv`, JS-инструменты — из `apps/web/node_modules`. Core не импортирует runtime-код из `tooling/`.

Специализированные инструменты API — в `services/core-api/tools/{dev,courses,maintenance,lab}/`; проверки браузерных границ — в `apps/web/tools/`. Базовые команды и размещение файлов — в [README](../README.md).
