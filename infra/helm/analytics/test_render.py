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


def render(values=None, namespace="knowi-analytics", values_file=None):
    command = [HELM, "template", "analytics", str(CHART), "--namespace", namespace]
    if values_file is not None:
        command.extend(["--values", str(values_file)])
    for key, value in (values or {}).items():
        command.extend(["--set" if value in ("true", "false") else "--set-string", f"{key}={value}"])
    return subprocess.run(command, text=True, capture_output=True, check=False)


class RenderTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
