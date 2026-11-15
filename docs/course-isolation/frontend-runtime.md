# Frontend runtime — final handoff

STATUS: COMPLETE (assigned frontend scope).

RESULT: registry-driven lazy course/landing discovery, generic ID-to-slug routing and selected-course author preview are implemented. Ordinary safe YAML/JSON is supported. Final three-course builds, TypeScript, SSR and private HTTP boundary checks pass. Runtime source is frozen for root acceptance.

## Changed files / ownership

All paths are relative to the staged repository.

- apps/web/tools/course-registry.mjs: validated discovery and ignored-state generation.
- apps/web/tools/course-document.mjs: bounded safe YAML/JSON parser.
- apps/web/tools/{course-document,course-registry,course-demo,course-boundary}.test.mjs: focused checks.
- apps/web/src/course-runtime/{course-registry,page-registry}.ts: generic loaders and lazy page registry.
- apps/web/config/{vite.config.ts,tsconfig.json}: discovery integration, aliases, shared demo build/dev routing, shared demo typing and private filesystem boundary.
- apps/web/package.json and package-lock.json: explicit yaml@2.9.0 dev dependency and test/prebuild/predev commands. Lock comparison found only the new yaml package and root dependency addition.
- apps/web/src/app/{App,Workspace}.tsx and app/workspace/{WorkspacePanels,WorkspaceWorkbench}.tsx.
- apps/web/src/authoring/{main,ReferenceLesson}.tsx.
- apps/web/src/shared/course-paths.ts, course-paths.test.mjs, shared/api/client.test.mjs.
- apps/web/src/features/learning/useLearningCourse.ts, practice/{TaskBrief.tsx,lesson-interactions.css}, theory/MarkdownView.tsx.
- apps/web/src/features/landing/CourseLandingRoute.tsx and apps/web/src/README.md.
- This report and frontend-source-hashes.json.

Other workers own all shared UI/demo/landing implementation and course-authored pages. No backend/Core, infrastructure, Git or main-checkout mutation was made.

## Removals

Removed obsolete apps/web/src/lessons/{course-registry,page-registry,registry,shared}.ts after their callers and old chapters were migrated; removed its stale README after the shared worker supplied canonical courses/shared/ui/README.md. The old lessons directory is now empty/removed.

Removed temporary source-generated src/course-runtime/{course-ids,generated-loaders,generated-preview}.ts. These generated files were introduced during this task; their final location is apps/web/.cache/course-registry/. Generated demo HTML is also there. Main repository already ignores **/.cache/.

## Manifest/API compatibility

- A folder, registry entry and manifest.frontend.{course,personal_landing,course_landing} select trusted lazy modules. No product map remains in app code.
- ID follows Core's ASCII identifier syntax with a 64-character bound; slug drives URLs, course_id drives API/storage. Current fixture ID isolation_fixture maps to isolation-fixture. Disabled and unknown IDs/slugs have no fallback. Existing aliases and both bases remain.
- Safe ordinary YAML/JSON uses YAML 1.1 scalar semantics matching Core's UniqueKeySafeLoader. Duplicate keys, aliases, unknown/custom tags, multiple documents, documents over 256 KiB, depth over 32, non-object roots and non-string keys are rejected. Inline safe merges retain Core's duplicate-after-merge rejection.
- Frontend paths reject traversal, symlinks and Windows path aliases. Only enabled registered frontend dirs and shared ui/demo/landing browser subtrees extend the app fs allowlist. Explicit deny covers backend, archives, shared/ai, credential patterns and course/settings documents. Public route prefixes (api/assets/devtools/etc.) are reserved slugs.
- @platform/* maps to app src; @course-ui and subpaths map to shared UI. Per-course Page receives courseId and legacy. Optional getLessonMetadata flows into provider resolveMetadata; generic fallback remains possible. Course/module data cannot overwrite selected ID. Loaded registry and markdown state are keyed by course/lesson.
- Author preview uses ?course=<course_id>&lesson=chapter_.../block_.../Lesson... with backend legacy default; form/examples preserve selected course and base.
- npm predev/prebuild and Vite config evaluation regenerate ignored state. Standalone route/SSR tests also regenerate. After registry changes, restart dev/build; generated files are excluded from source-only hash proof.
- Shared gallery content is canonical in courses/shared/demo. Vite generates a build entry in ignored state and emits the compatible devtools/lesson-visual-demo.html using Rolldown emitFile. /backend/demo and old HTML route work under both bases.

## Actual checks

Final three-course state: backend, backend_copy, isolation_fixture. Registry was NOT restored by this worker.

PASS:
- npm run test:course-runtime: 24 tests, including all 4 existing SSR API client cases, both route bases, aliases, YAML/JSON/merge behavior, rejection boundaries, ordinary YAML registry, ID/slug mismatch, Windows aliases and symlink rejection.
- npm run test:course-demo: 2 tests, both bases. GET /backend/demo, trailing-slash alias and old devtools HTML return shared demo HTML; referenced shared main.tsx responds 200. Servers terminate cleanly.
- npm run test:course-boundary: 2 tests with three-course allowlist. Registered frontend entry responds 200; each base rejects 8 direct/encoded private paths: 6 HTTP 403 and 2 HTTP 404. Backend, archive and shared/ai are covered. Credential/settings deny rules are asserted without requesting credential contents.
- node node_modules/typescript/bin/tsc --noEmit -p config/tsconfig.json: exit 0, including shared demo types.
- npm run build -- --outDir .cache/course-build-root: exit 0 with two courses, then exit 0 with THREE courses. Full visual gate and TypeScript included.
- VITE_BASE=/platform/ npm run build -- --outDir .cache/course-build-platform: exit 0 with two courses, then exit 0 with THREE courses.
- Build logs: apps/web/.cache/course-build-{root,platform}-three.log; current build dirs contain the three-course artifacts. Existing >500 kB video.js chunk warning remains.
- Artifact assertions on the two-course builds: both app/demo HTML files used the correct base and all referenced assets existed; two course metadata chunks were lazy; 715 lesson chunks were emitted; ignored build entry HTML was relocated to its compatible public filename.
- Pure Python import of Core packages.py (no app startup/SQL) accepted ordinary YAML and safe inline merge and rejected duplicate keys, merge duplicates, aliases, custom tags and multiple documents, agreeing with JS checks.
- 211 common frontend/shared-browser source SHA-256 entries captured before final three-course builds. ALL UNCHANGED after both builds and final tests; see frontend-source-hashes.json. Generated state and registered course folders are excluded.

SSR failure diagnosis: ordinary Node child runner exposed native Windows exit 3221225477 (0xC0000005), rather than an assertion failure. A plain Vite create/load/close loop passed. Disabling unrelated client dependency discovery ONLY in SSR contract tests resolved all four cases. One-shot HTTP tests disable FS watching after config merge to prevent watcher handles surviving test shutdown. Production/dev configuration retains its normal optimizer/watcher behavior. Narrow require_escalated calls permitted child-process execution; no broad environment mutation was used.

Official version sources: https://github.com/eemeli/yaml/tree/v2.9.0/docs and https://github.com/vitejs/vite/tree/v8.0.10/docs. Installed Vite 8.1.3 and Rolldown contracts were also checked locally.

## Unverified / exact root remainder

No frontend implementation blocker remains. Root retains browser rendering/interaction, custom third-course catalog/API/storage and live runtime acceptance. This worker's HTTP checks are not browser render evidence.

Root owns restoration to two products AFTER third-course proof. Registry currently remains at three; after restoration, regenerate .cache state/rebuild to return artifacts to two. Root can compare frontend-source-hashes.json through fixture changes while excluding registered-course packages and generated .cache output. No SQL, real notifications, Git/CI/deployment or production checks were performed here.
