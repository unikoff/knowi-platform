# Course isolation: creator toolkit

Added a bundled creator toolkit under `courses/shared/ai`: author/edit, package/register, and check skills, plus concise author and reviewer roles. The README describes folder + registry onboarding and that a host must explicitly expose these repository-local files; no global skills, agent configuration, frontend routes, or product maps are added.

Skills route authors to the canonical Stage 1–3 methodics instead of copying or relaxing them. They preserve Stage boundaries, theory before practice, stable IDs/progress, private backend checks versus public lesson content, and the app's existing theme variables.

## Contract assumptions

- Paths: `courses/shared/ai/skills/*/SKILL.md` and `courses/shared/ai/agents/*.md`; skills/roles are bundled, not globally installed or auto-discovered by every host.
- Course onboarding follows `courses/<course-folder>` plus `courses/registry.json`.
- The staged snapshot had no `.agents` or `tooling/courses` at implementation time. Canonical Stage flows were read from the read-only main source. Tool commands are therefore discovery-based until root provisions final tooling; no API or command was invented.

## Checks

- `quick_validate.py` against each of the three skill folders: **all valid**.
- Relative Markdown link audit across six toolkit Markdown files: **all links resolve inside staged root**.
- Runtime/package onboarding, registry integration, API, browser render, runner, and private/public resource boundary: **not exercised**; owned scope was the creator toolkit only.
