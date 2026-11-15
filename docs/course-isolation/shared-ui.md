# Shared UI handoff

STATUS: COMPLETE — assigned shared UI/demo/metadata/tooling scope implemented and narrow checks passed. Root integration acceptance remains separate.

## Integration contract for root/frontend owner

- `@course-ui` -> `courses/shared/ui/index.ts`; `@course-ui/*` -> `courses/shared/ui/*`; `@platform/*` -> `apps/web/src/*`. Existing Vite/TS aliases already match these requirements.
- Shared provider exports: `LessonMetadataProvider`, `LessonMetadataContext`, `LessonMetadata`. Props: `source: string`, `module?: string`, `title?: string`, `children`, `resolveMetadata?: (source: string, module?: string, title?: string) => LessonMetadata | null`. Metadata: `{ block: string; blockNumber: number; lessonNumber: string; tags?: string[] }`. The optional resolver owns course-specific interpretation and tags; generic fallback runs only when resolver is absent. An explicit resolver result `null` remains null.
- Both course entries export `getLessonMetadata` from their own `metadata.ts`; both course facades re-export `@course-ui`. Both course page registries import `apps/web/src/course-runtime/page-registry`.
- Demo source is `courses/shared/demo/main.tsx`; canonical HTML is `courses/shared/demo/index.html`. Its script marker `/courses/shared/demo/main.tsx` is intentionally stable: the current frontend-owned Vite plugin replaces it for dev/build and emits the legacy `devtools/lesson-visual-demo.html` destination. Browser routes remain `/backend/demo` and `/platform/backend/demo`; this worker inspected wiring but did not run Vite/browser tests.
- `react-dom/client` resolves through the app dependency installation. Current frontend-owned Vite alias `react-dom` and TS paths `react-dom` / `react-dom/*` are present. The scoped demo TS config includes app `vite-env.d.ts` for CSS declarations. No extra dependency or palette was added.

Root owns app config/consumer changes, real browser/PG checks, old authored chapter reconciliation/removal and final import. This worker writes only staged shared UI/demo, course metadata/entry/facades and course visual/structure tooling.

## Result and changed files

- `courses/shared/ui/**`: 27 moved component/style files plus canonical index, generic metadata/context/placeholder, generic metadata tests and API README (33 files total). All 47 previous visual exports remain; 5 exports added: metadata provider/context/type and both placeholders. Normalized component comparison against the read-only source found only the intended RichHero tag-resolution change beyond import paths.
- `courses/shared/demo/**`: all 13 files from the original gallery, plus original outer HTML preserved as `backend-demo.html` (14 total). All 5 original JPG/SVG assets are byte-identical; no original gallery file is missing. Current gallery has 5 groups, 21 tools and 57 variants. Gallery content/examples are preserved; imports, theme/bootstrap paths, config, docs and demo-owned layout class were adjusted.
- `courses/{backend,backend_copy}/frontend/course/{metadata.ts,metadata.test.mjs,index.ts,shared.ts,page-registry.ts}`: independent course metadata maps/tests, metadata export, `@course-ui` facade and generic platform page registry path.
- `tooling/courses/{check_lesson_visuals.cjs,test_lesson_visuals.cjs,test_course_ui.cjs,lesson_tools.py,test_lesson_tools.py}`: inspect canonical course TSX/CSS and shared UI, enforce shared ownership/token use, SSR coverage, course-aware read-only lesson inventory. `lesson_tools.py --course <folder>` defaults to backend and uses current conventional `backend/plans`, `backend/practice`, `frontend/course` paths; it does not interpret alternative manifest layouts. Unknown/malformed course selection does not fall back. Practice parsing remains static and does not execute course Python.
- Exact path list and move map: `docs/course-isolation/shared-ui-files.json`. No manifest/schema/version changes by this worker; existing `frontend.course` entries stay compatible. Root's `tooling/courses/create_course.py` is untouched.

## Removals and theme audit

Moved originals: `apps/web/src/lessons/components/**`, `course-placeholder.tsx`, `lesson-metadata-context.tsx`, `lesson-metadata.ts`, `lesson-metadata.test.mjs`; `apps/web/devtools/lesson-visual-demo/**` and its outer HTML. No author chapter was moved/deleted by this worker. Root performed chapter reconciliation and old registry/API removal.

Shared CSS has no hex/rgb/hsl literal colors. All referenced CSS variables resolve to existing platform palette/tokens or local geometry/state variables in the source audit. Replaced literal shadow/backdrop values with `--surface-shadow` / `--black`; replaced undefined legacy `--font` with `--font-body`. Syntax and semantic status colors consume existing app roles; no separate color exceptions or palette definitions were added. Fixed SVG screenshot colors and historical JPG images are retained as teaching-image content, not themed UI. Historical gallery review notes are explicitly labeled historical.

`applyTheme` was executed for both real app palettes against a captured document style sink: theme/scheme and shared semantic role assignments passed. This is bootstrap evidence, not browser computed-style evidence.

## Actual checks

All commands ran in staged checkout; exit 0 unless described otherwise.

| Command | Outcome |
| --- | --- |
| `node tooling/courses/test_lesson_visuals.cjs` | 6 tests passed: alias/retired APIs, CSS ownership, platform color roles, unknown token rejection |
| `node tooling/courses/test_lesson_structure.cjs` | 15 tests passed; existing frame/interactive/visual checks retained |
| `node tooling/courses/test_course_ui.cjs` | 5 tests passed: resolver arguments/tags/null, fallback with no stale metadata, real Lesson01 SSR in both courses, all 57 gallery variants SSR, actual theme bootstrap for both schemes |
| `node courses/shared/ui/metadata.test.mjs` | 3 tests passed including non-lesson and mismatched block metadata |
| `node courses/backend/frontend/course/metadata.test.mjs` | 7 tests passed; 316 active registered lesson metadata entries inspected |
| `node courses/backend_copy/frontend/course/metadata.test.mjs` | 7 tests passed; 316 independent copy entries inspected |
| `python -m unittest tooling.courses.test_lesson_tools` | 23 tests passed, including separate course plans/practice/preview and invalid/missing course rejection |
| `node tooling/courses/check_lesson_visuals.cjs` | 734 course TSX modules; shared/course/app CSS ownership and shared token/literal-color audit passed |
| From `apps/web`: `node node_modules/typescript/bin/tsc --noEmit -p ../../courses/shared/demo/tsconfig.json` | Passed; compiles actual moved library and full gallery |
| `python tooling/courses/lesson_tools.py context --course backend_copy --chapter chapter_01 --lesson 1.1` | Resolved copy-owned plan, TSX, separate practice sources and course-aware preview URL |

Total: 66 focused tests passed. An earlier normal `node --test` invocation could not spawn its test subprocess (`EPERM`); direct Node execution of the same `node:test` files passed. No app/CI config was changed for this workaround. No SQL, notifications, main-repo writes or Git actions were performed.

## Unverified and exact remaining root work

No remaining implementation in this worker's scope. Root must finish its integrated `/` and `/platform/` builds and real-browser checks of demo/lesson routes, image assets (including any legacy asset aliases), light/dark computed styles, desktop/mobile layout, gallery navigation/keyboard/focus, interactive reset and image error fallback. SSR and theme assignment tests do not establish these browser outcomes. Live PG/API/progress isolation and final source import remain root acceptance.

Mandatory Serena initialization created local `.serena` project metadata in staged checkout; it is not a source deliverable and is excluded from this worker's path manifest.
