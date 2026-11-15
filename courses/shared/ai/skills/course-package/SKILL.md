---
name: course-package
description: Add or update a repository course package and its registry entry using the current manifest contract.
---

# Package and register a course

Use when the task explicitly includes onboarding a course. Inspect current package examples and the exact `courses/registry.json` and manifest schema first; preserve their conventions and validate IDs, slugs, paths, and enabled state. The course folder plus one registry entry are the discovery/onboarding source of truth. Do not add a shared app route, lazy import, hardcoded product map, or duplicate palette.

Keep course-specific catalog, metadata, identity, lessons, landing copy, and private practice checks inside that course's package. Reuse shared SDK/UI only for genuinely generic behavior. Course frontend modules are trusted build-time modules, not a sandbox for untrusted authors. Keep private backend material outside public build/dev file serving.

Check path containment, symlink/traversal handling, duplicate/reserved identifiers, and unknown/disabled-course behavior using available repository tooling or tests. Do not invent a schema or command when provisioning is incomplete; record the missing contract and ask the integrator to align it. Treat old progress and lesson IDs as immutable unless the request explicitly authorizes migration.

Current entrypoints from repository root: `python -B tooling/courses/create_course.py <id> --from <source_id> --slug <public-slug> --title <title>`; after backend authoring, `python -B tooling/courses/pin_course_version.py <id>`; validation: `python -B services/core-api/tools/check_courses.py --courses-root courses`. Use the project's Core virtual environment. Build/restart regenerates ignored frontend loader state; do not hand-edit it.
