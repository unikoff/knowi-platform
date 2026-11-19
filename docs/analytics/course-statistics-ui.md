# Learner course statistics UI

The course account's Statistics tab renders `CourseStatisticsPanel.tsx`, which
selects the personal or clearly labeled demo view of `CourseStatisticsDashboard`.
Personal time reporting is intentionally disconnected: every actual time
measurement is missing (`null`), not zero. No analytics requests or tracking
changes were introduced.

## Demonstration mode

- Without `user.has_active_subscription === true`, demo is the default. Active
  subscribers start with personal data. Both can switch between the two views.
- Account identity, auth revision, course or subscription changes remount the
  mode panel, so its default matches the current account state.
- The flag matches the existing Payments UI. This is frontend presentation,
  not a substitute for future reporting API authorization or entitlements.
- `createCourseStatisticsDemo` creates fictional time and progress in fresh
  objects. Public lesson titles/structure can come from the current catalog,
  but actual completion flags/counts never enter the demo. An absent catalog
  uses a 12-lesson example curriculum across two chapters.
- Daily amounts are deterministic by course and date. Course, daily and lesson
  totals reconcile for active/theory/practice time; overlapping periods agree.
- The compact mode control, demo notice and lesson footer identify the source.
  Personal mode passes no demo snapshot and retains actual Core progress.
- The subscription CTA opens the existing Payments tab. It performs no purchase
  or subscription mutation.
- No demo time or completion is persisted or submitted to analytics/Core.
- The concurrently prepared live adapter is retained, but personal measurement
  requests require an additional explicit `personalMeasurementsEnabled` prop.
  Its default is false; AccountPanel leaves it false for this demo review even
  if the analytics rollout flag is enabled. Enabling real measurements later is
  a separate decision. Tests cover the rollout-enabled/default-disabled case.

## Current data boundary

- `courseId` is the top-level course selected by the workspace.
- `AccountWorkspace.statisticsData.courses` contains chapters/tracks of that
  already scoped course. A track ID must not be compared to `courseId`.
- The catalog supplies titles and ordering. Core supplies completion. Absence of
  time never implies an uncompleted lesson; time never establishes completion.
- Completion is current progress for the whole course, independent of the time
  filter. This distinction is displayed above the lesson list.
- Catalog fallback lessons without an authoritative completion record have an
  unknown completion status and do not appear in completed/uncompleted filters.

## Activity history

- One full-width card combines the taller daily chart and an attached breakdown.
- The "Всего" bars stack theory and editor practice in the same colors as the
  breakdown. A missing component is never painted as a fabricated proportion.
- The attached panel owns the range controls: week (default), month (rolling
  30 days), and custom inclusive dates. Custom changes apply on submission only.
  Validation rejects invalid/reversed/future dates and ranges over the report
  API's 31-day limit without changing the applied range.
- No day is selected initially. The breakdown explicitly says "За весь период"
  and uses the selected range's totals. A day selection switches its date, total,
  theory/practice values and shares together; the summary cards remain range totals.
- Click the selected day again, use "Весь период" or press Escape to clear it.
  Arrow keys, Home and End select a day; the chart retains one Tab stop even with
  no selection. Changing the period clears the day selection.
- Missing values stay unavailable. Measured zero displays zero time with no
  percentage split; neither state creates a fabricated ratio.

## Future reporting adapter

The presentational props are `timeData`, `timeState`, `onRangeChange`, `onRetry`.
The normalized contract is exported from `course-statistics-model.ts`:
`CourseTimeSnapshot` contains course ID, inclusive Moscow date keys `from/to`,
quality, snapshot `asOf`, totals, daily measurements and per-lesson measurements.
All durations use **milliseconds**. Missing measurements use `null`.

`onRangeChange` emits `{courseId, from, to}` once per selected range, even when a
parent recreates its callback. Presets are 7 and 30 days; custom ranges allow
1 through 31 days, within the current report API's limit. Bounds are converted from inclusive
Moscow dates to the half-open UTC interval:

```ts
from_time = new Date(`${from}T00:00:00+03:00`).toISOString();
to_time = new Date(`${shiftDate(to, 1)}T00:00:00+03:00`).toISOString();
```

Use the existing authenticated `features/analytics-reporting/client.ts` client
and ActivityReportV1 contract. The adapter must bind the learner's `account_ref`
and authorized course ID; a numeric user ID or an unscoped platform report is
not a substitute. Preserve the client's validation and cancellation behavior.
Key the request state by actor/auth revision, course and range; reject late
responses from a different key. `AccountPanel` already remounts the dashboard
when the user/account/auth revision/role or course changes.

Mapping:

| Source | View model |
| --- | --- |
| `course_id` | `courseId` (must equal the active course) |
| `quality`, `as_of` | `quality`, `asOf` |
| `active_ms`, `theory_ms`, `practice_ms` | `activeMs`, `theoryMs`, `practiceMs` |
| `daily[].date` | `days[].date`, Moscow calendar day |
| detail row `level=lesson`, matching `course_id` | `lessons[]`, joined by `lesson_id` |

Missing days are kept unknown, never filled with invented zeroes. Unknown or
partial coverage zeroes are displayed as missing; positive measurements remain
visible as the observed portion. Only complete daily coverage allows the count
of active days and mean time per active day. A zero active-day denominator has
no mean. No cross-course sums, global visits, visitor counts or fabricated
streaks are shown.

The component rejects snapshots whose course or date range differs from the
selected scope. Supply `ready` for a usable report, `stale` for a retained stale
snapshot, `loading` during a request and `error` when unavailable/invalid. Loading
and error states hide prior time values. `pending` is the unconnected default.
An adapter must preserve service freshness/quality distinctions; do not turn an
unavailable report into complete zero time. Lesson detail pages must be handled
explicitly (pagination is not a stable snapshot under concurrent ingestion).

Do not sum `unique_visitors` across days or infer completion from detail rows.
`trusted_progress` is currently unavailable; keep using authoritative Core
progress until a separately validated scoped completion source is supported.

## Chapter progress

`CourseStatisticsProgress` replaces the flat lesson list with a course outline.
Chapters start collapsed and open independently. Each chapter shows its Core
completion count and progress bar; search covers chapter, block and lesson titles.
Chapter identity uses its stable ID, so catalog title changes do not create a
second chapter. Catalog-only lessons retain unknown completion.

Expanded chapters show independently collapsible blocks, initially closed. Block
headers show Core completion counts/progress and summed time for the selected
period/metric. Sums use all lessons in the block, independent of search results;
missing lesson measurements or incomplete curriculum coverage make the block's
time unavailable instead of presenting a partial sum as its total.

Lessons appear inside expanded blocks. Each lesson keeps its completion status,
time for the selected reporting period/metric, and an optional detailed breakdown
of total/theory/editor practice time. Missing values remain unavailable rather
than zero. Chapter completion always covers the full course and does not change
with the time range. Reporting detail pagination remains available when supplied.

## Verification

- Pure model/demo tests: `node --test --test-isolation=none src/features/account/course-statistics-model.test.mjs src/features/account/course-statistics-demo.test.mjs`.
- TypeScript: `node_modules/.bin/tsc --noEmit -p config/tsconfig.json`.
- Vite build with the existing config.
- Disposable browser previews live under ignored `.cache/navigation-preview/`.
  The integration preview mounts the actual account. The dashboard preview also
  exercises clearly labeled synthetic snapshots, which never enter production.
- Check empty/known zero/observed positive time, other-course rejection, period
  changes, day keyboard selection, chapter/block/lesson disclosures, search, stable
  chapter identity, unknown completion, pagination, narrow containers and both
  color schemes.
