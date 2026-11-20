"""Offline Helm rendering checks; never contacts a cluster.

Run with: python infra/helm/analytics/test_render.py [path-to-helm]
"""
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

CHART = Path(__file__).resolve().parent
ROOT = CHART.parents[2]
HELM = sys.argv.pop(1) if len(sys.argv) > 1 else shutil.which("helm") or str(
    ROOT / "services/analytics/.local/helm-sourcecheck/helm.exe"
)
UUID = "11111111-1111-4111-8111-111111111111"
DIGEST = "sha256:" + "a" * 64
BASE = {
    "activityProjection.enabled": "true",
    "activityProjection.runId": UUID,
    "trustManifest.configMapName": "analytics-trust",
    "privacyAuthority.secretName": "analytics-authority",
}
PRODUCTION = {
    "runtime.mode": "production", "image.digest": DIGEST,
    "trustManifest.configMapName": "analytics-trust",
    "privacyAuthority.secretName": "analytics-authority-reader",
    "authorityClientTLS.secretName": "authority-client-tls",
    "coreDeletion.sourceSecretName": "core-deletion-source",
    "coreDeletion.statusSecretName": "core-deletion-status",
    "coreDeletion.relaySecretName": "core-deletion-relay",
    "coreDeletion.tlsSecretName": "core-deletion-tls",
    "coreDeletion.namespace": "knowi-core",
    "coreDeletion.podLabels.app": "core-proxy",
    "internalTLS.enabled": "true",
    "internalTLS.proxyImageRepository": "example/frontend",
    "internalTLS.proxyImageDigest": DIGEST,
    "internalTLS.collectorSecretName": "collector-tls",
    "internalTLS.querySecretName": "query-tls",
    "network.coreIngress.enabled": "true",
    "network.coreIngress.namespace": "knowi-core",
    "network.coreIngress.podLabels.app": "core",
}


def render(values=None, namespace="knowi-analytics", values_file=None):
    command = [HELM, "template", "analytics", str(CHART), "--namespace", namespace]
    if values_file is not None:
        command.extend(["--values", str(values_file)])
    for key, value in (values or {}).items():
        command.extend(["--set" if value in ("true", "false") else "--set-string", f"{key}={value}"])
    return subprocess.run(command, text=True, capture_output=True, check=False)


class RenderTests(unittest.TestCase):
    def test_knowi01_staged_profile_fails_closed_and_renders_with_inputs(self):
        profile = CHART / "values-knowi01-staged.yaml"
        self.assertNotEqual(render(values_file=profile).returncode, 0)
        inputs = {
            "image.repository": "example/analytics", "image.digest": DIGEST,
            "internalTLS.proxyImageRepository": "example/frontend", "internalTLS.proxyImageDigest": DIGEST,
            "network.databaseCIDR": "192.0.2.1/32", "authorityServer.anchorCIDR": "192.0.2.2/32",
            "privateCoreSource.imageRepository": "example/core", "privateCoreSource.imageDigest": DIGEST,
            "privateCoreSource.expectedDatabase": "core",
            "privateCoreSource.expectedUser": "readonly", "privateCoreSource.instanceId": UUID,
        }
        result = render(inputs, values_file=profile)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('ANALYTICS_COLLECTOR_REAL_COLLECTION: "false"', result.stdout)
        self.assertIn('ANALYTICS_PROCESSOR_BUCKET_WORKER_ENABLED: "false"', result.stdout)
        self.assertIn("name: analytics-analytics-authority", result.stdout)
        self.assertIn("name: analytics-private-core-source", result.stdout)
        self.assertIn('migrated-data.pymentor-rehearsal-data-20261109.svc.cluster.local', result.stdout)
        source_policy = next(doc for doc in result.stdout.split("\n---\n")
                             if "kind: NetworkPolicy" in doc and "name: analytics-private-core-source" in doc)
        self.assertIn('kubernetes.io/metadata.name: "pymentor-rehearsal-data-20261109"', source_policy)
        self.assertIn("app: rehearsal-postgres", source_policy)
        self.assertIn("port: 57152", source_policy)
        self.assertNotIn("ipBlock:", source_policy)
        self.assertIn("path: /run/knowi-analytics-recovery, type: Directory", result.stdout)
        self.assertNotIn("kind: Job", result.stdout)
        self.assertNotIn("kind: IngressRoute", result.stdout)
        self.assertNotIn("location = /api/analytics/events/batch", result.stdout)
        for unsafe in ("migration.enabled", "ingress.enabled", "browserCollection.enabled",
                       "coreTransfer.enabled", "catalog.enabled", "activityProjection.enabled",
                       "activityDay.enabled", "activityCourse.enabled", "activityBoundaryCredit.enabled"):
            with self.subTest(unsafe=unsafe):
                self.assertNotEqual(render({**inputs, unsafe: "true"}, values_file=profile).returncode, 0)
        for required in ("image.repository", "network.databaseCIDR", "authorityServer.anchorCIDR",
                         "privateCoreSource.expectedHost", "privateCoreSource.databaseNamespace",
                         "privateCoreSource.databasePodLabels"):
            with self.subTest(required=required):
                self.assertNotEqual(render({**inputs, required: ""}, values_file=profile).returncode, 0)
        self.assertNotEqual(render({**inputs, "privateCoreSource.databaseCIDR": "192.0.2.3/32"},
                                   values_file=profile).returncode, 0)

    def test_browser_collection_requires_guarded_production(self):
        base = {**PRODUCTION, "migration.revision": "a014", "roles.privacy.enabled": "true",
                "catalog.enabled": "true", "catalog.namespace": "core",
                "coreTransfer.namespacesConfigMapName": "catalog-map",
                "activityProjection.enabled": "true", "activityProjection.runId": UUID,
                "activityDay.enabled": "true", "activityCourse.enabled": "true"}
        off = render(base)
        self.assertEqual(off.returncode, 0, off.stderr)
        self.assertNotIn("location = /api/analytics/events/batch", off.stdout)
        self.assertIn('ANALYTICS_PROCESSOR_BUCKET_WORKER_ENABLED: "true"', off.stdout)
        on = render({**base, "browserCollection.enabled": "true"})
        self.assertEqual(on.returncode, 0, on.stderr)
        self.assertIn('ANALYTICS_COLLECTOR_REAL_COLLECTION: "true"', on.stdout)
        self.assertIn("location = /api/analytics/events/batch { client_max_body_size 256k;", on.stdout)
        self.assertIn("location = /internal/analytics/catalog/v1 { client_max_body_size 2m;", on.stdout)
        self.assertIn('ANALYTICS_PROCESSOR_BUCKET_WORKER_ENABLED: "true"', on.stdout)
        self.assertIn('ANALYTICS_PROCESSOR_GENERIC_WORKER_ENABLED: "false"', on.stdout)
        for invalid in ({"catalog.enabled": "false"}, {"roles.privacy.enabled": "false"},
                        {"network.coreIngress.namespace": ""}, {"runtime.mode": "synthetic"}):
            with self.subTest(invalid=invalid):
                self.assertNotEqual(render({**base, "browserCollection.enabled": "true", **invalid}).returncode, 0)

    def test_defaults_keep_features_off(self):
        result = render()
        self.assertEqual(result.returncode, 0, result.stderr)
        for marker in ("ACTIVITY_PROJECTION_ENABLED", "ACTIVITY_DAY_WORKER_ENABLED", "ACTIVITY_DAY_QUERY_ENABLED", "kind: Job"):
            self.assertNotIn(marker, result.stdout)

    def test_compatible_revisions_and_role_flags(self):
        for revision in ("a010", "a011", "a012", "a013", "a014"):
            with self.subTest(revision=revision):
                values = {**BASE, "migration.revision": revision}
                if revision != "a010":
                    values.update({"activityDay.enabled": "true", "roles.privacy.enabled": "true"})
                result = render(values)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("ANALYTICS_PROCESSOR_ACTIVITY_PROJECTION_ENABLED", result.stdout)
                self.assertIn("ANALYTICS_QUERY_ACTIVITY_PROJECTION_ENABLED", result.stdout)
                self.assertEqual(result.stdout.count("ANALYTICS_ACTIVITY_PROJECTION_RUN_ID"), 2)
                self.assertEqual("ANALYTICS_PROCESSOR_ACTIVITY_DAY_WORKER_ENABLED" in result.stdout, revision != "a010")
                self.assertEqual("ANALYTICS_QUERY_ACTIVITY_DAY_QUERY_ENABLED" in result.stdout, revision != "a010")
                self.assertNotIn("ACTIVITY_COURSE_WORKER_ENABLED", result.stdout)
                self.assertNotIn("ACTIVITY_BOUNDARY_CREDIT_ENABLED", result.stdout)

    def test_invalid_configurations_fail_closed(self):
        cases = [
            {**BASE, "migration.revision": "a015"},
            {**BASE, "migration.revision": "a009"},
            {**BASE, "migration.revision": "a014", "activityProjection.runId": "bad"},
            {**BASE, "migration.revision": "a014", "trustManifest.configMapName": ""},
            {**BASE, "migration.revision": "a014", "privacyAuthority.secretName": ""},
            {**BASE, "migration.revision": "a014", "activityDay.enabled": "true"},
            {**BASE, "migration.revision": "a010", "activityDay.enabled": "true", "roles.privacy.enabled": "true"},
            {"migration.enabled": "true", "migration.revision": "a999"},
            {"migration.revision": "a015"},
            {**BASE, "migration.revision": "a011", "activityDay.enabled": "true", "roles.privacy.enabled": "true", "activityCourse.enabled": "true"},
            {**BASE, "migration.revision": "a014", "activityCourse.enabled": "true"},
            {**BASE, "migration.revision": "a012", "activityDay.enabled": "true", "roles.privacy.enabled": "true", "activityCourse.enabled": "true", "activityBoundaryCredit.enabled": "true"},
            {**BASE, "migration.revision": "a014", "activityDay.enabled": "true", "roles.privacy.enabled": "true", "activityBoundaryCredit.enabled": "true"},
            {"activityCourse.enabled": "yes"},
            {"activityBoundaryCredit.enabled": "yes"},
            {"activityProjection.processorPollMs": "49"},
            {"activityProjection.processorPollMs": "501"},
            {"activityProjection.processorPollMs": "5e1"},
        ]
        for values in cases:
            with self.subTest(values=values):
                self.assertNotEqual(render(values).returncode, 0)

    def test_course_and_boundary_flags_are_role_scoped(self):
        for revision, boundary in (("a012", False), ("a013", True), ("a014", True)):
            with self.subTest(revision=revision):
                values = {**BASE, "migration.revision": revision, "activityDay.enabled": "true",
                          "roles.privacy.enabled": "true", "activityCourse.enabled": "true"}
                if boundary:
                    values["activityBoundaryCredit.enabled"] = "true"
                result = render(values)
                self.assertEqual(result.returncode, 0, result.stderr)
                docs = result.stdout.split("\n---\n")
                deployments = {role: next(doc for doc in docs if "kind: Deployment" in doc
                                          and f"name: analytics-analytics-{role}" in doc)
                               for role in ("collector", "processor", "query", "privacy")}
                for role, doc in deployments.items():
                    self.assertEqual("ACTIVITY_COURSE_WORKER_ENABLED" in doc, role == "processor")
                    self.assertEqual("ACTIVITY_COURSE_QUERY_ENABLED" in doc, role == "query")
                    self.assertEqual("ACTIVITY_BOUNDARY_CREDIT_ENABLED" in doc, boundary and role == "processor")

    def test_tuned_poll_is_set_only_for_processor(self):
        result = render({**BASE, "migration.revision": "a014"},
                        values_file=CHART / "values-a014-performance.yaml")
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = result.stdout.split("\n---\n")
        processor = next(doc for doc in docs if "kind: Deployment" in doc and "name: analytics-analytics-processor" in doc)
        self.assertIn("replicas: 2", processor)
        self.assertIn('ANALYTICS_PROCESSOR_ACTIVITY_POLL_SECONDS, value: "0.05"', processor)
        self.assertEqual(result.stdout.count("ANALYTICS_PROCESSOR_ACTIVITY_POLL_SECONDS"), 1)
        self.assertIn('ANALYTICS_QUERY_POOL_SIZE: "4"', result.stdout)

    def test_migration_uses_explicit_revision_and_digest(self):
        values = {**BASE, "activityDay.enabled": "true", "roles.privacy.enabled": "true",
                  "migration.enabled": "true", "migration.revision": "a014", "image.digest": DIGEST}
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('args: ["migrate", "--revision", "a014"]', result.stdout)
        self.assertEqual(result.stdout.count(f'"knowi-analytics@{DIGEST}"'), 5)
        benchmark = {**values, "syntheticBenchmark.enabled": "true", "image.digest": ""}
        self.assertNotEqual(render(benchmark, "knowi-analytics-bench-" + "1" * 32).returncode, 0)

    def test_authority_is_opt_in_and_requires_complete_isolated_configuration(self):
        self.assertNotIn("analytics-authority\n", render().stdout)
        values = {
            **PRODUCTION, "authorityServer.enabled": "true",
            "authorityServer.configSecretName": "authority-private",
            "authorityServer.journalClaimName": "authority-journal",
            "authorityServer.anchorCIDR": "10.42.5.8/32",
            "authorityServer.coreNamespace": "knowi-core",
            "authorityServer.corePodLabels.app": "core",
            "authorityClientTLS.secretName": "authority-client-tls",
        }
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("type: ClusterIP", result.stdout)
        self.assertIn('claimName: "authority-journal"', result.stdout)
        self.assertIn('cidr: "10.42.5.8/32"', result.stdout)
        self.assertIn("strategy: {type: Recreate}", result.stdout)
        self.assertNotIn("analytics-ingress\n", result.stdout)
        self.assertNotIn("recovery-permit", result.stdout)
        guarded = render({**values, "runtime.hostBootRecoveryGate": "true", "roles.privacy.enabled": "true"})
        self.assertEqual(guarded.returncode, 0, guarded.stderr)
        self.assertEqual(guarded.stdout.count("path: /run/knowi-analytics-recovery, type: Directory"), 5)
        self.assertEqual(guarded.stdout.count("mountPath: /var/run/analytics-recovery, readOnly: true"), 5)
        self.assertIn('"--host-boot-recovery-gate"', guarded.stdout)
        self.assertNotEqual(render({**PRODUCTION, "runtime.hostBootRecoveryGate": "true"}).returncode, 0)
        shared = render({**values, "runtime.hostBootRecoveryGate": "true", "network.databaseCIDR": "10.42.5.8/32"})
        self.assertEqual(shared.returncode, 0, shared.stderr)
        for changes in ({"image.digest": ""}, {"authorityServer.configSecretName": ""},
                        {"authorityServer.coreNamespace": ""},
                        {"authorityServer.anchorCIDR": ""},
                        {"authorityServer.anchorBackend": "sqlite"},
                        {"authorityServer.anchorCIDR": "10.42.5.8/32", "network.databaseCIDR": "10.42.5.8/32"},
                        {"authorityClientTLS.secretName": ""},
                        {"fixtureCatalog.configMapName": "synthetic"}, {"ingress.enabled": "true"},
                        {"internalTLS.proxyImageDigest": ""}, {"internalTLS.querySecretName": "collector-tls"}):
            with self.subTest(changes=changes):
                self.assertNotEqual(render({**values, **changes}).returncode, 0)

    def test_traefik_access_requires_explicit_ingress(self):
        self.assertNotIn("name: analytics-analytics-ingress", render().stdout)
        result = render({"ingress.enabled": "true"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("name: analytics-analytics-ingress", result.stdout)

    def test_production_profile_wires_guards_and_migration(self):
        values = {**PRODUCTION, "roles.privacy.enabled": "true", "migration.enabled": "true", "migration.revision": "a014"}
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('ISSUER_MODE: "production"'), 5)
        self.assertIn('ANALYTICS_PROCESSOR_BUCKET_WORKER_ENABLED: "false"', result.stdout)
        self.assertIn('ANALYTICS_PROCESSOR_GENERIC_WORKER_ENABLED: "false"', result.stdout)
        self.assertEqual(result.stdout.count("name: ANALYTICS_CORE_DELETION_SOURCE_JSON"), 5)
        self.assertEqual(result.stdout.count("_CORE_DELETION_STATUS_DATABASE_URL"), 3)
        self.assertEqual(result.stdout.count("name: ANALYTICS_PRIVACY_CORE_DELETION_RELAY_JSON"), 1)
        self.assertIn('args: ["migrate", "--revision", "a014"]', result.stdout)
        self.assertEqual(result.stdout.count('"--host", "127.0.0.1"'), 2)
        self.assertEqual(result.stdout.count("ssl_verify_client on;"), 2)
        self.assertEqual(result.stdout.count("name: internal-tls\n"), 2)
        self.assertIn("ports: [{name: https, port: 8443, targetPort: https}]", result.stdout)
        self.assertIn("ports: [{protocol: TCP, port: 8443}]", result.stdout)
        for changes in ({"coreDeletion.statusSecretName": ""}, {"coreDeletion.sourceSecretName": "analytics-query-db"},
                        {"coreDeletion.namespace": ""}, {"runtime.mode": "local"},
                        {"fixtureCatalog.configMapName": "synthetic"}):
            with self.subTest(changes=changes):
                self.assertNotEqual(render({**values, **changes}).returncode, 0)

    def test_private_core_source_is_closed_by_default_and_scoped(self):
        self.assertNotIn("name: analytics-private-core-source", render().stdout)
        values = {**{k: v for k, v in PRODUCTION.items() if k != "coreDeletion.podLabels.app"}, "privateCoreSource.enabled": "true",
                  "privateCoreSource.imageRepository": "example/core", "privateCoreSource.imageDigest": DIGEST,
                  "privateCoreSource.databaseSecretName": "source-db", "privateCoreSource.hmacSecretName": "source-hmac",
                  "privateCoreSource.databaseCASecretName": "source-db-ca", "privateCoreSource.serverTLSSecretName": "source-tls",
                  "privateCoreSource.expectedHost": "core-db.local", "privateCoreSource.expectedDatabase": "core",
                  "privateCoreSource.expectedUser": "read_only", "privateCoreSource.instanceId": UUID,
                  "privateCoreSource.databaseCIDR": "10.42.5.9/32", "coreDeletion.namespace": "knowi-analytics",
                  "coreDeletion.podLabels.app\\.kubernetes\\.io/name": "analytics-core-source",
                  "coreDeletion.podLabels.app\\.kubernetes\\.io/instance": "analytics", "coreDeletion.port": "8443"}
        result = render(values)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"pymentor_core.modules.analytics_delivery.production_collection:create_app"', result.stdout)
        self.assertIn("location = /api/internal/analytics/deletions", result.stdout)
        self.assertIn('cidr: "10.42.5.9/32"', result.stdout)
        for changes in ({"privateCoreSource.hmacSecretName": "source-db"}, {"privateCoreSource.databaseCIDR": "0.0.0.0/0"},
                        {"privateCoreSource.imageDigest": ""}, {"coreDeletion.namespace": "another"},
                        {"privateCoreSource.databaseNamespace": "db"}):
            with self.subTest(changes=changes):
                self.assertNotEqual(render({**values, **changes}).returncode, 0)


if __name__ == "__main__":
    unittest.main()
