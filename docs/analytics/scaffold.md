# CORP-61: isolated process scaffold

The initial service has independent `collector`, `processor`, `query` and
`migrate` commands. HTTP processes expose only `/live` and `/ready`: no batch or
report handler is installed, no processor is started by an HTTP server, and
there is no database connection, schema, issuer or grant verification yet.
The processor's HTTP server is only a probe host; no processing task runs.

Each role uses `ANALYTICS_<ROLE>_` settings: required `DATABASE_URL`, optional
`POOL_SIZE` (1–20), `POOL_OVERFLOW` (0–10), `DB_TIMEOUT_SECONDS` (>0–30),
`CONCURRENCY` (1–100), and `SHUTDOWN_TIMEOUT_SECONDS` (1–60). These are bounded
configuration reservations, not active pools or measured performance. Credentials
belong in the environment and are masked; connection strings are never printed.
DB names must start `analytics_`, Core's local port 57382 is rejected, and local
Analytics must use 57392. This guard is not a substitute for infrastructure
isolation and least-privilege database roles. A remote host's true identity cannot
be verified from a URL. `REAL_COLLECTION=true` and non-synthetic issuer modes are
rejected; synthetic is a future trust boundary, not an implemented issuer.

Invalid or missing role configuration prevents startup. Once configured,
`/live` does not access dependencies. `/ready` defaults to 503 until real
role-specific checks prove database, schema and privacy safety. A test-injected
readiness port exercises the ready branch; it is not a production adapter. The
probe is time-bounded and cancelled on timeout. Migrate
returns a nonzero status until CORP-63. No schema upgrade is claimed. Future DB
adapters must own async connection lifecycle and cancellation/transaction rollback;
CPU-heavy processing belongs outside the HTTP event loop. One future
`activity_facts` store is owned at the Collector boundary. No Core data, Redis,
course mounts, grant keys or user data are required for this scaffold.

Python 3.12 and independently pinned runtime/dev requirements are used. The image
has a non-root user and does not run migrations at startup. The runtime pins lock
package versions, not artifact hashes; build and network availability must be
verified in the target environment. Separate pools and connection budgets are
configuration boundaries only until CORP-63 implements and verifies them.
