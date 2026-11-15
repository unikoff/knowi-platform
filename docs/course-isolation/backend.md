# Backend course isolation worker

STATUS COMPLETE — assigned backend implementation and offline execution checks finished.
Source freeze: 2026-11-15. Only the staged repository was changed.

## RESULT / ownership

- Core loads trusted `courses/<id>/backend/catalog.py` in a registry-root/course/version namespace. Course-specific builders, practice assembly and identities now live independently in both product backends. Core compatibility imports delegate to the default backend; old backend aliases/default progress namespace remain intact.
- Pure author helpers and the generic `Catalog` protocol implementation live in `courses/shared/backend`. See its README for author-facing imports, required methods and metadata. A custom non-Python program needs its own folder/manifest/catalog and a registry entry.
- Learning and administration progress use course catalog `progress_policy`, including overview, optional cards, revision tracking and historical task completion metadata. Private manual-answer eligibility is course metadata. Shared authentication, subscription, async request SQL, mandatory idempotent login, Runner execution protections and persisted outbox destination/config pinning remain in their existing owners.
- Notification landing slugs resolve from the trusted registry, including disabled-course recognition to prevent legacy-route fallback. Settings remain explicitly inactive; routing tests use SQL connection tripwires and mocks.
- Manifest schema stays 2; no new manifest field, API endpoint, SQL schema or migration. Entrypoint convention is `backend/catalog.py`. Reserved slugs match frontend, and `shared` is a reserved course ID. Unknown/disabled courses, unsafe paths, links, invalid protocols and duplicate identities fail closed.
- `content_version(root, manifest)` is the stable runtime API reused by package recomputation. Digest covers manifest server layout/tracks, authoring resources, course backend server inputs, and all shared backend inputs including README. Frontend, settings and archives are outside this digest. Final version of both products: `00c403828668871f7345cf8e8b88a657d388e35e72f40e8d3d2dd712b5d0afc2`.

## CHANGED_FILES / removals

Exact paths and before/after SHA-256: `backend-files.json` (1256 entries: 569 added, 131 changed, 556 deleted). It compares only assigned paths with root's raw preimages; evidence/report files in this directory are additional outputs.

Major paths: both `courses/*/backend` trees and manifests; `courses/shared/backend`; Core `adapters/course_local`, affected SQL landing/admin/outbox adapters, learning progress/service, landing contracts/public route, `app/paths.py`; affected tools and tests.

Removed `services/core-api/content` (553 files), Core `_context.py` and `legacy_exports.py`, and obsolete `multicourse_legacy_facades.py`. Archived all 553 content files / 8,125,437 bytes under `courses/backend/archive/legacy-content`; `PRESERVATION.json` records every retained byte/hash. Archive is outside runtime import roots. Five legacy files changed from the frozen baseline already matched active canonical authoring before helper-import relocation; their raw bytes remain archived. No canonical authoring data was replaced by frozen copies.

Executable importers/tests now use course namespaces/shared helpers. Inventory reads canonical plans, practice and landing paths and writes fresh inventory under `docs/course-isolation/inventory`, preserving historical MC1.2 evidence.

Concurrent MAIN updates: refreshed `test_course_package_files.py` with the current source-byte parity test, then changed only its deleted-authoring check to digest rejection; merged current MAIN `rehearse_course_copy.py` with copying shared backend helpers. Root must update import preimages for these two files. Latest MAIN submit projection harness is byte-identical in STAGE; runtime suites were not edited by this worker and root must preserve the latest MAIN version.

## ACTUAL_CHECKS

Commands use `C:/Project Python/Pymentor_platform/services/core-api/.venv/Scripts/python.exe -B`, staged `PYTHONPATH=services/core-api`, dotenv disabled.

- Package/protocol/public-spec/settings/catalog focused pytest run: **92 passed** (before final validator genericization/documentation).
- Latest focused protocol + generic validator + source-byte parity + deleted-authoring digest checks: **11 passed in 37.18s**. Independent fixture has one non-Python track and supports root README/AGENTS. Covers missing/linked entrypoint, import/protocol failure cleanup and retry, unknown/disabled courses, reserved slugs, namespace/mutation isolation, server/helper/manifest digest drift, private projection and inactive generic notification routing.
- Migrated lesson consumers and current submit ASGI harness: **60 passed + 650 subtests**, one deliberately deselected broad reference-solution runner test. SQL Engine connect/raw_connection tripwires prevented any real SQL access. Published numbering, aliases, manual privacy, old completion and response projection checks passed.
- Creator copy rehearsal: **PASS**; current canonical backend/frontend bytes match, inactive settings retained, 9 tracks / 310 lessons / 204 tasks; no DB/app factory imported. `backend-rehearsal.json` retains output; disposable copy removed.
- Canonical inventory built without writes/network/SQL: **PASS**; 374 plans, 90 practice manual/code files, 310 lessons / 204 tasks.
- Final frozen-source `python -B services/core-api/tools/check_courses.py --courses-root courses`: **PASS**, 2610 files, generic catalog protocol validation, original two products restored by root.
- Final `python -B services/core-api/tools/courses/check_catalog_preservation.py --courses-root courses --baseline docs/course-isolation/catalog-before.json`: **PASS for both courses**, all 310 lesson IDs/aliases/titles/sources, 204 task IDs/revisions/full private hashes and existing content revision equal root's pre-relocation capture.
- Archive hash/byte recount: **PASS**, 553 files / 8,125,437 bytes. Changed Python AST parsing: **143 PASS**. Source inspection finds no old content imports in executable consumers.

Initial failures were attributed and resolved: shared demo changed during a copy check (parallel-owner race); restored authoring-extension validation; Windows restrictive TemporaryDirectory ACL (bounded fixture directory now used); symlink tests updated to the stricter link rejection message. Current MAIN test harness behavior was retained.

## UNVERIFIED / exact remainder

No implementation work remains in this assigned backend scope. Root owns import/backups, live PostgreSQL/Redis/Runner/browser acceptance, final combined suites and cleanup of its temporary course. Root reported live custom third-course/PG progress/Runner/private HTTP and frontend proof as PASS; these are coordinator evidence, not worker-run checks. This worker made no real SQL connection, notification delivery, Git/CI/infra/VPS change or MAIN write. Ignore incidental local test/Serena artifacts during import.
