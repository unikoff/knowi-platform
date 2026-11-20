import json
from contextlib import nullcontext
import subprocess
import unittest
from unittest.mock import patch

import image_smoke


CORE = "ghcr.io/unikoff/knowi-platform-core@sha256:" + "a" * 64
MIGRATION = "ghcr.io/unikoff/knowi-platform-core-migrations@sha256:" + "b" * 64


class CandidateSmokeTests(unittest.TestCase):
    def test_requires_both_exact_repository_digests(self):
        for argv in (["--core-image", CORE],
                     ["--core-image", CORE + "0", "--migration-image", MIGRATION],
                     ["--core-image", MIGRATION, "--migration-image", CORE],
                     ["--core-image", CORE.replace("@sha256:", ":"), "--migration-image", MIGRATION]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit):
                image_smoke.parse_args(argv)

    def test_candidate_uses_pulled_digests_and_scoped_cleanup(self):
        calls = []
        overrides = []

        def fake_run(command, **kwargs):
            calls.append(command)

        with patch.object(image_smoke.subprocess, "run", side_effect=fake_run), \
             patch.object(image_smoke.tempfile, "TemporaryDirectory", return_value=nullcontext("temporary")), \
             patch.object(image_smoke.Path, "write_text", side_effect=lambda value, **kwargs: overrides.append(json.loads(value))):
            image_smoke.main(["--core-image", CORE, "--migration-image", MIGRATION])
        self.assertEqual(calls[:2], [["docker", "pull", CORE], ["docker", "pull", MIGRATION]])
        self.assertTrue(all(override["services"] == {
            "core": {"image": CORE}, "worker": {"image": CORE},
            "migrate": {"image": MIGRATION},
        } for override in overrides))
        compose = [call for call in calls if call[:2] == ["docker", "compose"]]
        self.assertEqual(len({call[3] for call in compose}), 1)
        self.assertTrue(compose[0][3].startswith("knowi-local-ci-"))
        self.assertTrue(all("--build" not in call for call in compose))
        self.assertIn("--no-build", compose[0])
        self.assertEqual(compose[-1][-3:], ["down", "--volumes", "--remove-orphans"])
        self.assertEqual(calls[-2][-1], CORE)

    def test_cleanup_runs_after_pull_failure(self):
        calls = []

        def fail_pull(command, **kwargs):
            calls.append(command)
            if command[:2] == ["docker", "pull"]:
                raise subprocess.CalledProcessError(1, command)

        with patch.object(image_smoke.subprocess, "run", side_effect=fail_pull), \
             patch.object(image_smoke.tempfile, "TemporaryDirectory", return_value=nullcontext("temporary")), \
             patch.object(image_smoke.Path, "write_text"):
            with self.assertRaises(subprocess.CalledProcessError):
                image_smoke.main(["--core-image", CORE, "--migration-image", MIGRATION])
        self.assertEqual(calls[-1][-3:], ["down", "--volumes", "--remove-orphans"])

    def test_default_keeps_local_build(self):
        calls = []
        with patch.object(image_smoke.subprocess, "run", side_effect=lambda command, **kwargs: calls.append(command)), \
             patch.object(image_smoke.tempfile, "TemporaryDirectory", return_value=nullcontext("temporary")):
            image_smoke.main([])
        self.assertIn("--build", calls[0])
        self.assertEqual(calls[-2][-1], "knowi/core-local:pg06")


if __name__ == "__main__":
    unittest.main()
