"""Local structural checks only; these do not prove network enforcement."""
import json
import os
from pathlib import Path
import re
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[4]
CHART = ROOT / "infra/helm/analytics"
LOCAL = ROOT / "infra/analytics-local"


class ReleaseStructure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        env = os.environ.copy()
        for role in ("COLLECTOR", "PROCESSOR", "QUERY", "MIGRATE"):
            env[f"ANALYTICS_{role}_DATABASE_URL"] = (
                f"postgresql://{role.lower()}:synthetic@analytics-postgres:5432/analytics_test"
            )
        result = subprocess.run(
            ["docker", "compose", "-f", str(LOCAL / "compose.yaml"), "--profile", "migration", "config", "--format", "json"],
            cwd=ROOT, env=env, check=True, capture_output=True, text=True,
        )
        cls.compose = json.loads(result.stdout)

    def test_local_ports_credentials_and_network(self):
        services = self.compose["services"]
        self.assertEqual(set(services), {"collector", "processor", "query", "migrate", "ingress"})
        for role in ("collector", "processor", "query", "migrate"):
            service = services[role]
            self.assertEqual(service["image"], os.environ.get("ANALYTICS_IMAGE", "knowi-analytics:corp63-synthetic"))
            self.assertNotIn("ports", service)
            self.assertNotIn("volumes", service)
            self.assertEqual(service["command"][0], role)
            self.assertEqual(set(service["networks"]), {"analytics-data"})
            self.assertIn(f"ANALYTICS_{role.upper()}_DATABASE_URL", service["environment"])
        self.assertEqual(services["migrate"]["profiles"], ["migration"])
        ports = services["ingress"]["ports"]
        self.assertEqual(len(ports), 1)
        self.assertEqual((ports[0]["host_ip"], str(ports[0]["published"]), ports[0]["target"]), ("127.0.0.1", "57400", 8000))
        self.assertTrue(self.compose["networks"]["analytics-data"]["external"])
        self.assertEqual(self.compose["networks"]["analytics-data"]["name"], "knowi-analytics-01a1df75-net")
        self.assertNotIn("docker.sock", json.dumps(services["ingress"]))
        self.assertEqual(services["ingress"]["image"], "traefik:v3.7.15")

    def test_route_scope_and_priority(self):
        chart = (CHART / "templates/ingressroute.yaml").read_text(encoding="utf-8")
        local = (LOCAL / "routes.yaml").read_text(encoding="utf-8")
        for route in (chart, local):
            self.assertIn("Path(`/api/analytics/events/batch`)", route)
            self.assertIn("Path(`/api/analytics/reports`)", route)
            self.assertIn("PathPrefix(`/api/analytics/reports/`)", route)
            self.assertNotIn("PathPrefix(`/api/analytics/reports`)", route)
            self.assertEqual(route.count("priority: 110"), 2)
            self.assertNotIn("control", route)
            self.assertNotIn("processor", route)
        for path, expected in (("/api/analytics/events/batch", "collector"),
                               ("/api/analytics/events/batch/extra", None),
                               ("/api/analytics/reports", "query"),
                               ("/api/analytics/reports/activity", "query"),
                               ("/api/analytics/reports-other", None),
                               ("/api/analytics/control", None),
                               ("/api/learning/interpreter/run", None)):
            matched = "collector" if path == "/api/analytics/events/batch" else (
                "query" if path == "/api/analytics/reports" or path.startswith("/api/analytics/reports/") else None)
            self.assertEqual(matched, expected, path)

    def test_chart_security_config_and_separation(self):
        values = (CHART / "values.yaml").read_text(encoding="utf-8")
        workloads = (CHART / "templates/workloads.yaml").read_text(encoding="utf-8")
        job = (CHART / "templates/migration.yaml").read_text(encoding="utf-8")
        policy = (CHART / "templates/networkpolicy.yaml").read_text(encoding="utf-8")
        config = (CHART / "templates/configmap.yaml").read_text(encoding="utf-8")
        guard = (CHART / "templates/_guard.tpl").read_text(encoding="utf-8")
        self.assertEqual(values.count("replicas: 1"), 4)
        self.assertEqual(len(set(re.findall(r"secretName: (analytics-\w+-db)", values))), 5)
        self.assertRegex(values, r"privacy:\s+enabled: false\s+replicas: 1")
        self.assertIn('databaseCIDR: ""', values)
        self.assertIn("enabled: false", values)
        self.assertIn("policyTypes: [Ingress, Egress]", policy)
        self.assertIn("namespaceSelector", policy)
        self.assertIn("podSelector", policy)
        self.assertIn("0.0.0.0/0", policy)
        self.assertIn("secretKeyRef", workloads)
        self.assertEqual(values.count('imageTag: ""'), 4)
        self.assertIn("default $.Values.image.tag $cfg.imageTag", workloads)
        self.assertIn("ANALYTICS_TRUST_MANIFEST_JSON", workloads)
        self.assertIn("$.Values.trustManifest.configMapName", workloads)
        self.assertIn("configMapKeyRef", workloads)
        self.assertIn('if and (eq $role "collector") $.Values.fixtureCatalog.configMapName', workloads)
        self.assertIn("ANALYTICS_FIXTURE_CATALOG_JSON", workloads)
        self.assertNotIn("ANALYTICS_FIXTURE_CATALOG_JSON", config)
        self.assertIn("automountServiceAccountToken: false", workloads)
        self.assertIn('name: FORWARDED_ALLOW_IPS, value: ""', workloads)
        self.assertIn('if or (ne $role "privacy") $cfg.enabled', workloads)
        self.assertIn('if or (eq $role "collector") (eq $role "query")', workloads)
        self.assertIn("ANALYTICS_PRIVACY_AUTHORITY_JSON", workloads)
        self.assertIn("$.Values.privacyAuthority.secretName", workloads)
        self.assertNotIn("ANALYTICS_PRIVACY_AUTHORITY_JSON", config)
        self.assertIn("authorityCIDR", policy)
        self.assertIn("$exactIPv4", policy)
        self.assertIn("$exactIPv6", policy)
        self.assertIn("readinessProbe", workloads)
        self.assertIn("path: /ready", workloads)
        self.assertIn("resources:", workloads)
        self.assertIn("POOL_SIZE", config)
        self.assertIn("POOL_OVERFLOW", config)
        self.assertIn('ISSUER_MODE: "synthetic"', config)
        self.assertIn("if .Values.migration.enabled", job)
        self.assertIn("backoffLimit: 0", job)
        self.assertIn('args: ["migrate", "--revision",', job)
        self.assertIn('^a[0-9]{3}$', guard)
        self.assertIn("secretKeyRef", job)
        self.assertIn('.Release.Namespace "knowi-analytics"', guard)
        self.assertIn("uniq $secrets", guard)
        self.assertNotIn("redis", (values + workloads + job + policy).lower())


if __name__ == "__main__":
    unittest.main()
