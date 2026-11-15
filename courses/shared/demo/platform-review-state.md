# Local platform visual verification

Historical evidence preserved from the original gallery before course isolation.
These observations are not verification of the current staged migration.

- Scope: user selected guest-only review: chapter 1 roadmap and lessons 1–5.1 (10 lessons).
- Existing Vite: 127.0.0.1:5173. API started on 127.0.0.1:8001; uvicorn child PID 23060.
- API process uses PYMENTOR_LOCAL_NOTIFICATIONS=0 and PYMENTOR_SCHEMA_MODE=managed. No persistent environment changes.
- health, readiness and proxied /api/learning/tracks returned HTTP 200. Platform catalog renders again.
- Do not authenticate, change account permissions/progress or submit practice solutions. Locked lessons are outside browser coverage.
- Initial browser check: Lesson01 rendered inside actual Dockview theory panel. Common hero, section and LinkedNotes visible.
- Opened roadmap + all 10 guest lessons on actual /backend, not authoring. Compared shared hero, heading, lead, callout, lists and interactive styles to matching plain/detailed demo configurations: no differences.
- Opened all 63 practice cards: 1=5, 1.1=5, 2=7, 2.1=4, 3=7, 3.1=5, 4=4, 4.1=7, 5=9, 5.1=10. Restored each initial active card.
- Found and fixed a real bypass in TaskBrief: given/todo/hint lists used task-spec__list and their own CSS. They now use ContentList; removed those local list rules. ContentList supports items without a title.
- Extended library guard to learning feature TSX and retired task-spec__list. Full TypeScript check + guard tests pass.
- Practice list styles match demo in every visited card; no old list classes or empty title elements observed. No code execution or submissions.
- Desktop review used 1440 × 1000; intermediate width 960 × 900; mobile 390 × 844. Viewport override was reset after review.
- All 11 theory pages (roadmap + 10 guest lessons) fit the mobile container: viewport/page 390 px, theory content/scroll width 312 px. No retired common classes observed.
- Revisited every one of the 63 practice cards at 390 px. No page/brief horizontal overflow. Computed list typography, spacing, borders and colors matched the same UL/OL components in the demo. Restored initial active card in each lesson.
- Expanded Lesson 3/card 1 hint: found one more local font-size override, removed it. Revealed hint now matches demo OL (16 px, line-height 26.4 px), including mobile rendering.
- Tested Lesson 1 quiz wrong/correct states against matching demo states; styles match and answer labels contain no code elements. Tested StepThrough advancing and Lesson 3 matching pairs (successful and unsuccessful pair).
- Light theme spot-check on Lesson 3 at normal 1280 px viewport; restored dark theme afterwards.
- Final checks: TypeScript check, production build, 367-module visual ownership guard, its 5 tests and focused diff whitespace check passed. Build retains the existing large-chunk advisory.
- Final health/readiness/proxied tracks checks returned HTTP 200. Local API remains running. No solution code was run/submitted; guest progress stayed at 2%.
- Coverage is shared-component consistency in guest theory and practice, not full pedagogical acceptance of every paragraph. Account-only lessons were not opened. The 367-module guard is source validation, not browser coverage of those modules.
