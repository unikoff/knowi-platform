import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("cutover", ROOT / "infra/gitops/operations/prepare_c504_cutover.py")
cutover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cutover)


class CutoverPackageTests(unittest.TestCase):
    def test_preparation_pins_revisions_and_preserves_the_disabled_sync_barrier(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release.json"
            release.write_text(json.dumps({"source_sha": "a" * 40, "migration_digest": "sha256:" + "1" * 64, "frontend_digest": "sha256:" + "2" * 64}))
            before = release.read_bytes()
            artifacts = cutover.prepare(release, "a" * 40, "b" * 40, root / "output")
            self.assertEqual(release.read_bytes(), before)
            pause = artifacts["01-pause-application.patch.json"]
            self.assertEqual(pause, json.loads((ROOT / "infra/gitops/operations/pause-application.patch.json").read_text()))
            switch = artifacts["03-switch-pinned-path.patch.json"]
            self.assertIn({"op": "replace", "path": "/spec/syncPolicy/automated/enabled", "value": False}, pause)
            self.assertIn({"op": "test", "path": "/spec/syncPolicy/automated/enabled", "value": False}, switch)
            self.assertIn({"op": "replace", "path": "/spec/source/targetRevision", "value": "b" * 40}, switch)
            self.assertFalse(any(p.get("path") == "/spec/syncPolicy/automated/enabled" and p.get("value") is True for p in switch))
            container = artifacts["02-migration-job.json"]["spec"]["template"]["spec"]["containers"][0]
            self.assertTrue(container["image"].endswith("@sha256:" + "1" * 64))
            self.assertEqual(container["command"][-2:], ["upgrade", "c504_course_progress"])
            self.assertIn({"name": "KNOWI_MIGRATION_USER", "value": "knowi_c504_" + "a" * 12}, container["env"])
            policy = artifacts["01-migrator-networkpolicy.json"]
            self.assertEqual(policy["metadata"]["namespace"], "pymentor-rehearsal-data-20261109")
            self.assertEqual(policy["spec"]["podSelector"]["matchLabels"], {"app": "rehearsal-postgres"})
            self.assertEqual(policy["spec"]["policyTypes"], ["Ingress"])
            ingress, = policy["spec"]["ingress"]
            peer, = ingress["from"]
            self.assertEqual(peer["namespaceSelector"]["matchLabels"], {"kubernetes.io/metadata.name": "pymentor"})
            self.assertEqual(peer["podSelector"]["matchLabels"], artifacts["02-migration-job.json"]["spec"]["template"]["metadata"]["labels"])
            self.assertEqual(peer["podSelector"]["matchLabels"], {"app.kubernetes.io/component": "migrations", "knowi.io/source-sha": "a" * 40})
            self.assertEqual(ingress["ports"], [{"protocol": "TCP", "port": 57152}])
            self.assertEqual(json.loads((root / "output" / "01-migrator-networkpolicy.json").read_text()), policy)
            items = artifacts["00-maintenance.json"]["items"]
            deployment = next(x for x in items if x["kind"] == "Deployment")
            self.assertTrue(deployment["spec"]["template"]["spec"]["containers"][0]["image"].endswith("@sha256:" + "2" * 64))
            routes = [x for x in items if x["kind"] == "IngressRoute"]
            self.assertEqual(len(routes), 3)
            self.assertTrue(all(x["spec"]["routes"][0]["priority"] == 2000000000 for x in routes))
            self.assertTrue(all(host in routes[0]["spec"]["routes"][0]["match"] for host in cutover.PUBLIC_HOSTS))
            self.assertIn("!PathPrefix(`/.well-known/acme-challenge/`)", routes[0]["spec"]["routes"][0]["match"])
            self.assertEqual([r["spec"].get("tls", {}).get("secretName") for r in routes], [None, "knowi-tls", "preview-tls"])
            before_policy = (root / "output" / "01-migrator-networkpolicy.json").read_bytes()
            with self.assertRaises(ValueError):
                cutover.prepare(release, "a" * 40, "b" * 40, root / "output")
            self.assertEqual((root / "output" / "01-migrator-networkpolicy.json").read_bytes(), before_policy)

    def test_network_policy_rejects_invalid_sha(self):
        for sha in ("Release", "a" * 39, "A" * 40, "g" * 40):
            with self.subTest(sha=sha), self.assertRaises(ValueError):
                cutover.migration_network_policy(sha)

    def test_mismatched_or_mutable_revisions_fail_before_creating_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release.json"
            release.write_text(json.dumps({"source_sha": "a" * 40, "migration_digest": "sha256:" + "1" * 64}))
            for code, gitops in (("c" * 40, "b" * 40), ("a" * 40, "Release")):
                with self.assertRaises(ValueError):
                    cutover.prepare(release, code, gitops, root / "output")
                self.assertFalse((root / "output").exists())
            with self.assertRaises(ValueError):
                cutover.prepare(release, "a" * 40, "b" * 40, root / "output")
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
