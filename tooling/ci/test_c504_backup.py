"""Unit guards for the synthetic PostgreSQL 16 backup rehearsal."""
from __future__ import annotations

import importlib.util
from argparse import Namespace
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

MODULE_PATH = Path(__file__).with_name("rehearse_c504_backup.py")
spec = importlib.util.spec_from_file_location("rehearse_c504_backup", MODULE_PATH)
backup = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(backup)


class AllowlistTests(unittest.TestCase):
    def test_only_exact_synthetic_database_endpoint_is_allowed(self):
        accepted = backup._allowlisted_url(backup.BASE_URL)
        self.assertEqual(accepted.database, "pymentor_test")
        for url in (
            backup.BASE_URL.replace("57382", "5432"),
            backup.BASE_URL.replace("127.0.0.1", "localhost"),
            backup.BASE_URL.replace("/pymentor_test", "/production"),
            backup.BASE_URL.replace("pymentor_test:pymentor_test", "other:pymentor_test"),
            backup.BASE_URL + "?host=elsewhere",
            "sqlite:///production.db",
        ):
            with self.subTest(url=url.split("@")[0].split("://")[0]):
                with self.assertRaises(backup.RehearsalError):
                    backup._allowlisted_url(url)

    def test_database_names_require_generated_prefix_and_uuid(self):
        for valid in ("c504_backup_ci_" + "a" * 32, "c504_restore_ci_" + "b" * 32):
            self.assertEqual(backup._db_url(valid).database, valid)
        for invalid in ("production", "c504_release_ci_" + "a" * 32,
                        "c504_backup_ci_" + "a" * 31, "c504_restore_ci_" + "A" * 32):
            with self.subTest(name=invalid):
                with self.assertRaises(backup.RehearsalError):
                    backup._db_url(invalid)


class ContainerGuardTests(unittest.TestCase):
    @staticmethod
    def metadata(image="postgres:16", host_ip="127.0.0.1", host_port="57382"):
        return [{"Config": {"Image": image, "Labels": {"unrelated": "ignored"}},
                 "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": host_ip, "HostPort": host_port}]}}}]

    def test_accepts_selected_pg16_target_without_requiring_private_labels(self):
        result = backup._target_metadata(self.metadata())
        self.assertEqual(result["Config"]["Image"], "postgres:16")

    def test_rejects_wrong_image_or_port_before_any_side_effect(self):
        bad_targets = (
            self.metadata(image="postgres:17"),
            self.metadata(image="postgres:15"),
            self.metadata(host_port="57152"),
            self.metadata(host_ip="0.0.0.0"),
            self.metadata(host_port="5432"),
            [],
        )
        for target in bad_targets:
            with self.subTest(target=target):
                with self.assertRaises(backup.RehearsalError):
                    backup._target_metadata(target)

    def test_invalid_target_does_not_create_output_or_open_database(self):
        output = Path.cwd() / ("c504-invalid-target-" + uuid4().hex)
        args = Namespace(container="selected-target", output=str(output), docker="docker")
        with patch.object(backup, "_inspect", side_effect=backup.RehearsalError("bad target")), \
             patch.object(backup, "_connect") as connect:
            with self.assertRaises(backup.RehearsalError):
                backup.run(args)
        self.assertFalse(output.exists())
        connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
