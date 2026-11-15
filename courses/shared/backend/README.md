# Trusted course backend protocol

Register a course folder in `courses/registry.json`. Manifest schema remains 2;
the conventional backend entrypoint is `backend/catalog.py`. Manifest paths
declare plans, practice, frontend entries and the course's own ordered tracks.
No Core source edit or SQL migration is needed for another course ID.

Core imports the entrypoint in a namespace specific to registry root, course ID
and content version. Relative imports resolve course backend files first and
these shared helpers second. This isolates trusted modules and their mutable
catalogs; it is not a sandbox for untrusted Python authors.

For a program assembled as dictionaries, use:

```python
from .catalog_api import Catalog

TRACKS = [
    # {"id": "own-track", "title": "Own program", "order": 1,
    #  "lessons": [...]}
]
globals().update(Catalog(TRACKS).exports())
```

`TRACKS` must match manifest track IDs/order. Each lesson needs a unique `id`,
its `track`, `module`, `title`, `access` (`free`, `registered`, `subscription`),
nonempty `source_file`, and `tasks` list. Supply learner display/material fields
such as `duration`, `status`, `theory`, `theory_markdown`, `self_check`,
`manual_practice` and `video` where used. Stable `legacy_id` aliases are optional.
Tasks use stable `id` and `revision`, public practice fields, and server-only
judge inputs. Keep published IDs, ordering and revisions stable deliberately.

A custom entrypoint may implement the protocol directly: `TRACKS`, flattened
`LESSONS`, `default_track_id()`, `find_track(id)`, `find_lesson(id)`,
`find_task(id)`, `find_lesson_by_task(id)`, `get_content_revision()`,
`get_tracks_summary()` and `get_public_lessons(track_id)`. The shared `Catalog`
implements these and `get_legacy_lesson_id_map()`. Core validates the protocol,
identities, aliases, access and public task structures before caching a snapshot.
Public projections remove judge specifications and manual answers.

Lesson `progress_policy` is course-owned data:

- `overview: true` designates an automatically complete, countable overview.
- `revision_tracked` defaults to true. Set false only for intentional legacy
  lesson-level progress.
- `completion` is `cards` (default) or `lesson`. Card completion supports a
  revision-bound manual self-check with the existing stable internal marker.
- `optional_task_ids` lists optional cards; other cards are required unless
  their explicit `required` field is false.
- `legacy_task_completions` maps historical task IDs to accepted revisions.

Set `private_manual_answers: true` to enable the access-checked answer endpoint
for authored manual cards. Neither policy nor chapter naming bypasses common
access, subscription, authentication, Runner or progress security.

`.practice_helpers` and `.theory_helpers` provide pure author helpers. Existing
Python-course assembly and identities live in each product's backend. Frozen
legacy content is archived under `courses/backend/archive/legacy-content` and
is never a runtime fallback.

`runtime.content_version(root, manifest)` and the package tool share one digest:
manifest server layout/tracks, plans/practice bytes, course backend inputs and
all shared backend inputs, including this README. Frontend, private personal
settings and archives are excluded. Ignore bytecode/cache files. Repin every
affected manifest after changing shared helpers or this protocol document.
After editing trusted backend authoring, repin and validate the affected course:

```text
python -B tooling/courses/pin_course_version.py <course_id>
```

The command preserves manifest formatting and validates the catalog; on catalog
failure it restores the old version if the manifest has not changed concurrently.
Restart the API after updating authoring. Validate all packages with:

```text
python -B services/core-api/tools/check_courses.py --courses-root courses
```

This validates resources/digests and executes enabled trusted catalog protocols
without SQL or app startup. It allows root README.md/AGENTS.md and reserved
`shared` SDK resources. Registered disabled courses are validated structurally
but are not executed. Existing identity/private-hash preservation is a separate
check using `tools/courses/check_catalog_preservation.py --courses-root courses
--baseline docs/course-isolation/catalog-before.json`.
