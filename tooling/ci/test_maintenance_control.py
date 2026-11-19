"""Offline tests for the maintenance operator's safety barriers."""

import argparse
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = ROOT / "infra/gitops/operations"
sys.path.insert(0, str(OPERATIONS))
try:
    import maintenance
    import prepare_maintenance as manifests
finally:
    sys.path.pop(0)


class FakeKube:
    """In-memory resources, with a shared event log for ordering assertions."""

    KINDS = {
        "ConfigMap": "configmap",
        "Deployment": "deployment.apps",
        "Service": "service",
        "IngressRoute": "ingressroute.traefik.io",
    }

    def __init__(self, events):
        self.events = events
        self.resources = {}
        self.fail_ready = False

    def get(self, resource):
        self.events.append(("get", resource))
        return copy.deepcopy(self.resources.get(resource))

    def write(self, verb, document):
        self.events.append(("write", verb, copy.deepcopy(document)))
        for item in document.get("items", [document]):
            key = self.KINDS[item["kind"]] + "/" + item["metadata"]["name"]
            if verb == "create" and key in self.resources:
                raise RuntimeError("already exists: " + key)
            self.resources[key] = copy.deepcopy(item)
            self.resources[key]["metadata"]["resourceVersion"] = "1"

    def delete(self, resources):
        self.events.append(("delete", tuple(resources)))
        for resource in resources:
            self.resources.pop(resource, None)

    def wait_ready(self):
        self.events.append(("ready",))
        if self.fail_ready:
            raise RuntimeError("not ready")


class FakeTraffic:
    def __init__(self, events):
        self.events = events
        self.fail_on = object()

    def wait(self, token):
        self.events.append(("traffic", token))
        if token == self.fail_on:
            raise RuntimeError("marker mismatch")


class MaintenanceControlTests(unittest.TestCase):
    def setUp(self):
        self.release = ROOT / "test-release.json"
        self.workflow = ROOT / "test-workflow.json"
        original_read = Path.read_text
        def read_text(path, *args, **kwargs):
            if path == self.release:
                return json.dumps({"frontend_digest": "sha256:" + "a" * 64})
            if path == self.workflow:
                return json.dumps({"operation": ["op"], "check": ["check"]})
            return original_read(path, *args, **kwargs)
        read_patch = patch.object(Path, "read_text", read_text)
        read_patch.start()
        self.addCleanup(read_patch.stop)
        self.events = []
        self.kube = FakeKube(self.events)
        self.traffic = FakeTraffic(self.events)

    def execute(self, action, recover=None):
        options = argparse.Namespace(action=action, context="test-context", release_file=self.release, timeout=1,
                                     workflow=self.workflow, recover=recover)
        return maintenance.execute(options, self.kube, self.traffic)

    def step(self, argv, phase, context):
        self.assertEqual(context, "test-context")
        self.events.append(("step", phase, argv))

    def test_on_then_off_orders_readiness_routes_and_traffic(self):
        self.execute("on")
        ready = self.events.index(("ready",))
        route_write = next(i for i, e in enumerate(self.events)
                           if e[0] == "write" and e[2].get("items", [{}])[0].get("kind") == "IngressRoute")
        active = next(i for i, e in enumerate(self.events) if e[0] == "traffic" and e[1] is not None)
        self.assertLess(ready, route_write)
        self.assertLess(route_write, active)
        self.assertTrue(all(r in self.kube.resources for r in maintenance.ROUTE_RESOURCES))
        self.execute("off")
        delete_routes = next(i for i, e in enumerate(self.events) if e[0] == "delete" and set(e[1]) == set(maintenance.ROUTE_RESOURCES))
        normal = self.events.index(("traffic", None))
        delete_backend = next(i for i, e in enumerate(self.events) if e[0] == "delete" and set(e[1]) == set(maintenance.BACKEND_RESOURCES))
        self.assertLess(delete_routes, normal)
        self.assertLess(normal, delete_backend)
        self.assertFalse(self.kube.resources)

    def test_run_refuses_existing_routes_without_running_workflow(self):
        item = manifests.routes()["items"][0]
        self.kube.resources[maintenance.ROUTE_RESOURCES[0]] = item
        with patch.object(maintenance, "run_step", self.step), self.assertRaises(RuntimeError):
            self.execute("run")
        self.assertFalse(any(e[0] == "step" for e in self.events))
        self.assertIn(maintenance.ROUTE_RESOURCES[0], self.kube.resources)

    def test_readiness_and_bad_marker_block_workflow(self):
        for failure in ("ready", "marker"):
            with self.subTest(failure=failure):
                self.events.clear()
                self.kube = FakeKube(self.events)
                self.traffic = FakeTraffic(self.events)
                self.kube.fail_ready = failure == "ready"
                if failure == "marker":
                    self.traffic.wait = lambda token: (_ for _ in ()).throw(RuntimeError("marker mismatch"))
                with patch.object(maintenance, "run_step", self.step), self.assertRaises(RuntimeError):
                    self.execute("run")
                self.assertFalse(any(e[0] == "step" for e in self.events))
                if failure == "ready":
                    self.assertFalse(any(r in self.kube.resources for r in maintenance.ROUTE_RESOURCES))

    def test_failed_operation_or_check_keeps_window_and_lock_for_recovery(self):
        for failed in ("operation", "check"):
            with self.subTest(failed=failed):
                self.events.clear()
                self.kube = FakeKube(self.events)
                self.traffic = FakeTraffic(self.events)
                def step(argv, phase, context):
                    self.step(argv, phase, context)
                    if phase == failed:
                        raise RuntimeError("workflow failed")
                with patch.object(maintenance, "run_step", step), self.assertRaises(RuntimeError):
                    self.execute("run")
                self.assertTrue(all(r in self.kube.resources for r in maintenance.ROUTE_RESOURCES))
                token = self.kube.resources[maintenance.LOCK]["data"]["operationId"]
                with self.assertRaises(RuntimeError):
                    self.execute("off")
                self.assertTrue(all(r in self.kube.resources for r in maintenance.ROUTE_RESOURCES))
                self.execute("off", recover=token)
                self.assertFalse(self.kube.resources)

    def test_successful_workflow_runs_check_before_disabling(self):
        with patch.object(maintenance, "run_step", self.step):
            self.execute("run")
        op = self.events.index(("step", "operation", ["op"]))
        check = self.events.index(("step", "check", ["check"]))
        reopening = next(i for i, e in enumerate(self.events) if e[0] == "delete" and set(e[1]) == set(maintenance.ROUTE_RESOURCES))
        self.assertLess(op, check)
        self.assertLess(check, reopening)
        self.assertFalse(self.kube.resources)

    def test_failed_reopening_restores_routes_and_keeps_backend(self):
        self.execute("on")
        token = self.kube.resources[maintenance.BACKEND_RESOURCES[0]]["metadata"]["annotations"]["knowi.io/maintenance-token"]
        self.traffic.fail_on = None
        with self.assertRaises(RuntimeError):
            self.execute("off")
        self.assertTrue(all(r in self.kube.resources for r in maintenance.ROUTE_RESOURCES))
        self.assertTrue(all(r in self.kube.resources for r in maintenance.BACKEND_RESOURCES))
        self.assertEqual([event for event in self.events if event[0] == "traffic"],
                         [("traffic", token), ("traffic", None), ("traffic", token)])

    def test_active_workflow_prevents_parallel_off_and_rejects_wrong_recovery_id(self):
        def step(argv, phase, context):
            self.step(argv, phase, context)
            with self.assertRaisesRegex(RuntimeError, "lock is held"):
                self.execute("off")
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                self.execute("off", recover="incorrect-id")
            self.assertTrue(all(resource in self.kube.resources for resource in maintenance.ROUTE_RESOURCES))

        with patch.object(maintenance, "run_step", step):
            self.execute("run")
        self.assertFalse(self.kube.resources)

    def test_public_probe_requires_the_current_marker(self):
        traffic = maintenance.PublicTraffic(timeout=0)
        with patch.object(traffic, "get", return_value=(503, "previous", "no-store", False)):
            with self.assertRaisesRegex(RuntimeError, "Public maintenance verification timed out"):
                traffic.wait("current")
        with patch.object(traffic, "get", return_value=(503, "current", "no-store", False)) as get:
            traffic.wait("current")
        self.assertEqual(get.call_count, len(manifests.PUBLIC_HOSTS) * 4)

    def test_public_reopening_rejects_200_from_non_application_content(self):
        traffic = maintenance.PublicTraffic(timeout=0)

        def get(host, scheme, path):
            return 200, None, "", path == "/"

        with patch.object(traffic, "get", side_effect=get):
            with self.assertRaisesRegex(RuntimeError, "knowi.ru/api/learning/catalog"):
                traffic.wait(None)

        with patch.object(traffic, "get", return_value=(200, None, "", True)):
            traffic.wait(None)


if __name__ == "__main__":
    unittest.main()
