# Landing migration handoff

STATUS: COMPLETE — landing worker #4 implementation and offline checks. Integrated browser/build/backend acceptance belongs to root.

## RESULT / contracts

- Both declared `frontend.personal_landing` entries export `Page({ courseId: string, legacy?: boolean })`. Each course owns its full personal page, offer/pricing/reviews, mentor sections, FAQ, consent/privacy pages, forms, analytics, CSS, assets and existing demo tests. `backend_copy` has independent files and no imports from `backend`.
- Both declared `frontend.course_landing` entries export the same Page props; about remains an explicit placeholder with its existing course title and course-specific learning link.
- Existing `landingPersonal` / `landingCourse` metadata and manifest entry paths are compatible. No manifest, registry, App, config or generic runtime edits by this worker.
- Generic SDK imports used: `@platform/shared/course-paths` (`courseUrls`, ID→slug, both bases); `@platform/shared/paths` (legacy URL exports); `@platform/shared/api/client` (`apiRequest`); `@platform/shared/lib/python-highlight` (`highlightPython`). No app-private per-course implementation dependency. No shared visual component changes or new shared landing dependency.
- Required internal course/url props replace old implicit backend defaults. Navigation, footer, FAQ and form consent links use supplied course URLs. Personal subpages and legacy backend URLs retain previous dispatch. Keying the rendered personal page by courseId resets local form state when the selected course changes.
- Existing consultation contract retained: POST `/landings/{courseId}/consultations`; `Idempotency-Key`; body `name/contact/goal/consent/consent_version=consultation-v3`; success requires `accepted`. Existing contacts, offers, CTA text, operator details and analytics settings retained.
- Course styles reference app palette variables; local literal palette values were replaced with inherited semantic roles/color mixes. Canvas particles derive color from inherited `--accent`, refresh on app theme changes and disconnect the theme observer on cleanup. Layout/type/spacing remain course-owned.

## CHANGED_FILES / removals

Exact 144-path owned inventory and source→target hashes: [landing-files.json](landing-files.json).

- `courses/{backend,backend_copy}/frontend/landing-personal/**`: 46 files each, including existing metadata entry plus Page and complete moved source.
- `courses/{backend,backend_copy}/frontend/landing-course/{index.ts,Page.tsx}`.
- `apps/web/src/features/landing/README.md`: now describes the generic route. Original README retained byte-for-byte in both course packages.
- `docs/course-isolation/{landing.md,landing-files.json,check-landings.cjs}`.
- Removed 43 moved files under `apps/web/src/features/landing/**` and `apps/web/src/app/BackendLandingRoute.tsx` (44 removals). The app landing directory retains only the frontend worker's generic `CourseLandingRoute.tsx` and README. No executable references to removed implementations remain.
- Source accounting against current main, read-only: 44 original feature files per package, no missing replacements; 25 byte-identical, 19 changed for imports/props/theme. All 11 media assets per package byte-identical. Root's snapshot/hash checks remain authoritative at import.

## ACTUAL_CHECKS

- From `apps/web`: `node_modules/.bin/tsc.cmd --noEmit -p config/tsconfig.json` → exit 0 in the integrated staged snapshot after import/prop fixes.
- From repo root: `node docs/course-isolation/check-landings.cjs` → exit 0; 36 React SSR renders covering both products, personal/home + reviews/platform/pricing/consent/privacy, about placeholders, legacy links, / and /platform/. Validates original offer/mentor/form content and copy links staying in the copy course.
- The same checker executes each real consultation handler with simulated hooks/transport: course-specific endpoint, normalized payload/consent, pending disable, suppressed duplicate submit, rejected request, retry preserving the idempotency key, accepted success state → pass. No network request sent.
- `node --test --test-isolation=none courses/backend/frontend/landing-personal/analytics.test.mjs courses/backend/frontend/landing-personal/components/demo-machine.test.mjs courses/backend_copy/frontend/landing-personal/analytics.test.mjs courses/backend_copy/frontend/landing-personal/components/demo-machine.test.mjs` → 12 pass. Ordinary subprocess-isolated test execution earlier hit Windows spawn EPERM; isolation=none runs successfully.
- Read-only source/asset hashing and TypeScript AST audit → all JSX text nodes unchanged in all 11 original TSX files in each package; all media hashes match; all 44 removals have course-owned replacements.
- Searches → no stale executable removed-path imports, no escaping old relative app imports, no literal hex/rgb/hsl palette colors in either personal package.
- SSR emits the existing CareerLaunch useLayoutEffect server warning. SSR does not run DOM/layout/media/effect cleanup or prove browser interaction.

## UNVERIFIED / exact root remainder

1. Run both complete base builds after integration; offline transpilation/SSR does not verify Vite asset emission, CSS rendering or private dev boundaries.
2. Browser-check both course personal pages, their documents/detail pages and about placeholders; legacy links; desktop/mobile; light/dark theme including canvas; tabs/dialog keyboard/focus, video/resume/stories and mocked form error/retry. No real notification delivery.
3. Root owns the ID≠slug third-course fixture, actual API/PG/Redis/Runner/browser smoke, source import/backups, old data invariants and final acceptance. No changes or execution in those areas by this worker.
4. Operational deviation: initial context gathering mistakenly ran one read-only `git status --short`; it resolved to the outer workspace. No Git mutation occurred and no subsequent Git command was run.

