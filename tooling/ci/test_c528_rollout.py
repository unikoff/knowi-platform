import importlib.util
import pathlib
import unittest
from unittest.mock import Mock

path = pathlib.Path(__file__).with_name("rehearse_c528_rollout.py")
spec = importlib.util.spec_from_file_location("c528_rehearsal", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OpeningGuardTests(unittest.TestCase):
    def setUp(self):
        self.run = module.Rehearsal("kubectl", "isolated-config", "default", 1)
        self.run.stable = Mock(return_value=True)
        self.run.endpoints_are_new = Mock(return_value=True)
        self.run.weights_stable = Mock(return_value=True)
        self.run.get = Mock(return_value={"items": []})
        self.run.http_matches = Mock(return_value=True)

    def test_accepts_only_all_checks(self):
        self.run.opening_guard()
        self.run.http_matches.assert_called_once_with(503)

    def test_each_failed_check_blocks_opening(self):
        for name in ("stable", "endpoints_are_new", "weights_stable", "http_matches"):
            with self.subTest(check=name):
                check = getattr(self.run, name)
                check.return_value = False
                with self.assertRaises(RuntimeError):
                    self.run.opening_guard()
                check.return_value = True
        self.run.get.return_value = {"items": [{"metadata": {"name": "unexpected"}}]}
        with self.assertRaises(RuntimeError):
            self.run.opening_guard()

    def test_injected_failure_blocks_before_open(self):
        with self.assertRaisesRegex(RuntimeError, "injected readiness failure"):
            self.run.opening_guard(inject_failure=True)
        self.run.stable.assert_not_called()

    def test_current_maintenance_artifact_preserves_routes_config_and_token(self):
        items = self.run.maintenance()
        page = module.maintenance.DEFAULT_PAGE.read_text(encoding="utf-8")
        expected = module.maintenance.artifacts(page, "sha256:" + "1" * 64, self.run.token)
        original = expected["backend.json"]["items"] + expected["routes.json"]["items"]
        self.assertEqual(items[0]["data"], original[0]["data"])
        self.assertEqual(items[0]["metadata"]["annotations"]["knowi.io/maintenance-token"], self.run.token)
        for actual, source in zip(items[3:], original[3:]):
            self.assertEqual(actual["spec"]["routes"], source["spec"]["routes"])
            self.assertEqual(actual["spec"]["entryPoints"], source["spec"]["entryPoints"])
        self.assertEqual(items[1]["spec"]["template"]["spec"]["containers"][0]["image"], module.harness.NGINX)


if __name__ == "__main__":
    unittest.main()
