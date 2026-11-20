# knowi01 Core deletion source — review only

This is preparation, not an instruction to apply from this directory. The owner
reported PostgreSQL 16.15 in `pymentor-rehearsal-data-20261109`, Deployment
`rehearsal-postgres`, Pod label `app=rehearsal-postgres`, TCP Pod port 57152,
with `ssl=off`. The Deployment is outside Argo; its canonical managed source
and current live state must be identified and rechecked before changing it.
The existing ingress policies remain in place. `core-db-ingress-staged.yaml`
is an additive proposal for the owner, not managed by the Analytics Application.

Use `migrated-data.pymentor-rehearsal-data-20261109.svc.cluster.local` as the
database hostname in the source URL and expectedHost; it must be present in the
server certificate SAN. The `knowi-postgres.pymentor.svc.cluster.local` alias is
not interchangeable for verify-full without its own matching SAN. The URL must
use `sslmode=verify-full` and `sslrootcert=/var/run/source-db-ca/ca.crt`, with
the dedicated read-only role and the separately mounted CA. No key or URL belongs
in Git. Verify the Service resolves to the intended Pod and that Service port
and target Pod port are both as expected. The profile selects the destination
namespace and Pod labels, avoiding a Pod IP that can change on restart. Verify
the CNI's actual enforcement and Service DNAT behavior with allowed and denied
connections; a rendered selector is not evidence of reachability or isolation.

Before any change, record the workload owner, exact live spec and its managed
fields, PostgreSQL TLS settings and certificate paths, active clients, previous
network policies, readiness and a tested return path. Confirm the existing
policies do not independently allow unintended sources. In an isolated copy,
confirm the exact proposed certificate, SAN, CA and restricted role, and reject
wrong CA, wrong SAN, wrong role and an unrelated source. Root reported an
independent PostgreSQL 16 reload rehearsal passing correct and incorrect CA/SAN,
an existing non-TLS connection, and return; that is not a live verification.

For a separately authorized live window, the owner provisions the protected
server certificate and key using the actual workload owner, validates access
and configuration, reloads PostgreSQL, then checks `SHOW ssl`, the active
certificate and both the old application's connections and new verify-full
connections. Confirm a new connection using the source role succeeds, incorrect
CA/SAN and unrelated source fail, and an authenticated deletion snapshot works.
Do not enable a source or user path merely because TCP readiness passes. Review
the additive ingress policy alongside the chart egress and inspect actual CNI
behavior before synchronizing either side.

On a failure, stop the rollout, keep user paths closed, and return the owner-
managed PostgreSQL settings and certificate mounts to the recorded baseline;
reload and verify both existing and new Core connections. Before reverting TLS,
stop or move any newly dependent verify-full clients: turning TLS off would break
their new connections. Remove the additional ingress only through its owner,
without modifying pre-existing policies. A chart/image rollback does not roll
back database configuration or data; keep restored history quarantined until
deletion reconciliation succeeds.
