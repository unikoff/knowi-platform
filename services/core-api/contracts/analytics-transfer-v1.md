# Core transfer v1 and task progress extension

This contract describes the Core outbox and the opt-in internal transfer path.
Browser events and the independent signed deletion journal have separate contracts.

Every subject is the stored `account_lifecycles.lifecycle_id` UUID, with no
email, integer user ID, cookie, password or contact. Missing mapping stops
export. A new registration needs a new lifecycle UUID. `event_id` is UUIDv5
over canonical UTF-8 JSON of the whole payload (sorted keys, compact separators)
and a contract namespace, so retries preserve identity; a changed entity and
revision cannot reuse its previous event. A receiver must atomically enforce
both event ID/fingerprint and entity/revision uniqueness, and reject conflicts.

`legacy_activity_day` preserves the stored client-or-fallback date, nullable
timestamp and global provenance. It never implies a course, learning time, or
first-ever visit. `lesson_progress` reflects Core's actual course/lesson row;
`completed_at=null` means the row was removed, never an invented timestamp.
The task extension uses `course_id`, `task_id`, `task_revision` and canonical
UTC `completed_at`. The entity key is course plus task within the account
lifecycle. The same confirmed task revision creates no new outbox entry.
Both timestamp kinds serialize UTC, interpreting naive legacy timestamps as UTC
as current Core does. `subject_deleted` is an irreversible lifecycle tombstone;
it dominates all facts including late deliveries and backfills. This DTO is not
the independent, signed privacy authority journal needed for restore barriers.

Revision 0 is a backfill snapshot. Live mutations use a strictly increasing
positive subject revision allocated under the same lifecycle lock and transaction
as source mutation and outbox insertion. The receiver applies the greatest
revision for each activity day, course/lesson or course/task key, handles equal revision only
when identical, and retains deletion fences against every late version. A
snapshot at revision 0 can never supersede live state. `progress_fact` v2
requires a catalog version absent from these legacy rows; no fabricated version
is supplied. An actual mapping and consumer integration are required.

Enable task capture with `PYMENTOR_ANALYTICS_TASK_PROGRESS_ENABLED=1` only
when Collector, Processor and Query all run compatible code. Prepare the
`tasks` keyset backfill and seal it before starting the publisher. Proof v1
covers activity and lessons. Proof v2 also covers tasks, with a distinct scope
and cut. Once task facts are accepted, retain task-aware delivery and verifier
code during read rollback. An old processor cannot consume those facts.

For a new history migration, set `PYMENTOR_ANALYTICS_HISTORY_DATES_ONLY=1`
through export and proof staging. Backfill revision zero copies dates and
lifecycle and leaves `visited_at` null. The old compatibility path preserves
its existing behavior with this option off. Keep the option fixed within a
run. Conditional hours live only in the separately marked history response.
