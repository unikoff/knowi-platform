# Course creator toolkit

Полная схема папок, запуск и команды подключения курса: [руководство](../README.md).
Из корня проекта создавайте пакет через `tooling/courses/create_course.py`,
проверяйте через `services/core-api/tools/check_courses.py --courses-root courses`.
Frontend-кэш реестра генерируется автоматически; исходники общего runtime не редактируются.

This directory bundles repository-local creator instructions; nothing here installs or edits global Codex configuration. Discover a course by its folder and `courses/registry.json`. Keep authored content and course-specific identity inside that course package; use `courses/shared/` only for genuinely generic creator/runtime facilities.

## Skills

- `skills/course-author/SKILL.md` — create or edit one course artifact while following its assigned Stage.
- `skills/course-package/SKILL.md` — prepare and register a course folder using the repository's existing manifest and registry schema.
- `skills/course-check/SKILL.md` — run the available structural and interface checks and report their actual scope.

## Roles

- `agents/course-author.md` — responsible for one assigned course artifact and its evidence.
- `agents/course-reviewer.md` — independently checks the requested artifact against the canonical methodics; review does not imply edits or acceptance of runtime behavior.

A host may expose these bundled skills/roles to an agent explicitly. Do not copy them into global skills or agent settings as part of course authoring. For onboarding, start from a course folder and its manifest, then add/verify exactly one registry entry; never add an app route or product ID map.
