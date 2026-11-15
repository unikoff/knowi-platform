---
name: course-check
description: Run focused course package, lesson-structure, and public/private boundary checks and report their evidence limits.
---

# Check a course package

Inspect the current package, registry, and available tooling before choosing checks. Prefer focused existing commands for the changed course and Stage. Read tool help or implementation when CLI options are uncertain; do not guess flags. Run only checks authorized for the task and environment.

Check the relevant invariants: folder/manifest/registry agreement; stable IDs and progress; course-owned metadata; path safety and duplicate/reserved IDs; shared UI uses the app theme variables; private judge/tests/reference answers are not publicly served; unknown or disabled IDs fail closed. For authoring, use the applicable [Stage methodics](../../../../../docs/make%20les%20stage/START.md) as the acceptance source.

Report each command and actual outcome. Separate static structure, type/build, API delivery, browser rendering, runner behavior, and backend-private checks. Never claim a runtime layer passed because source inspection or build succeeded. If required dependencies or runtime are unavailable, state the exact unverified boundary and leave acceptance partial.

