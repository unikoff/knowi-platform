# Analytics plan update — 16.11.2026

Completed planning update requested by the owner after the role/admin mini-sprint. Canonical plan: `ANALYTICS_SERVICE_PLAN_20261115.md` and Jira CORP-438. No Analytics implementation, migration, service launch, real collection or deployment was performed in this update.

## Approved decisions carried into Jira

* Three top-level responsibilities: Collector, Analytics, PostgreSQL Analytics. Collector admission/acceptance/transport and Analytics activity/metrics/learning/reports/catalog/privacy have explicit contracts/ports. Collector, Processor and Query entrypoints scale independently; Processor and Query are inside the Analytics module.
* One activity_facts source across courses. Separate internal modules do not require their own statistics table, process or DB. Technical recovery states and optional measured derived models do not become a second primary source.
* User-only learning telemetry. Staff may read allowed reports; staff performance and new operation logging remain future work. Existing Core operational audit is preserved.
* Visible focused context, pause after300 seconds idle, resume without counting the pause. Theory/practice at lesson level; editor is practice. Subject-wide overlap union prevents doubled time across panels/tabs/devices.
* Main KPIs: unique visitors, active time, trusted Core progress. Visit may have0 seconds; progress is not derived from timer. Subscription/booking/quota facts remain Core-owned.
* User sees own data; leader assigned course and personal booking roster; admin user-learning data globally and through course/lesson/period filters. Basic leader reports are required in the first product release; optional452 contains further extensions.
* Three stages preserved: synthetic infrastructure109 → compatible migration after109+Core56 and gate454 → Tracker/KPI/UI with local119 then separately authorized server122. At implementation start on16.11 the owner chose no automatic age deletion for detailed visit/time facts; subject deletion remains mandatory. Technical cadence/SLA and backup/recovery retention are specified separately in CORP-69.

## Existing issues updated

32 existing descriptions were saved; only two summaries were clarified to include the leader as a report reader. No new issues, links, parents, status transitions, sprint dates or memberships were created/changed.

* Main/reference:438,110,111,112.
* Architecture/contracts:57,58,59,60,69,113.
* Infrastructure substages:439,440,441,442; skeleton61.
* Product substages:447,448,449,450; optional scope452.
* Tracker/calculation/API/UI/E2E:70,71,72,75,82,84,91,92,93,94,97,105.

All numbers use project CORP. Imported source footer/history is preserved; touched PYM links were normalized to current CORP keys. Direct contradictory requirements were replaced: mandatory per-course rollup tables, code/other time as extra types, lesson-total-only UI, and exclusion of the basic leader report from first release.

## Verification

All32 save responses were checked against intended descriptions and original statuses.12 independent issue readbacks checked descriptions, parent and status. The main Jira table initially dropped numbered cell prefixes; using explicit “Этап1/2/3” labels fixed the formatting loss, and final normalized body roundtrip matched the local canonical source. Markdown fences and key source/role/time/module/stage terms were checked; `git diff --check` passed for the plan.

Independent bounded source review identified missing focus wording and an ambiguous single-source statement for progress; both were corrected. Future runtime/golden/load/recovery cases in the plan are PROPOSED, not executed checks. Existing local role/admin acceptance evidence was not rerun merely for this documentation change.
