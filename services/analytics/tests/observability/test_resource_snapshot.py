import importlib.util
import io
import json
from pathlib import Path
from contextlib import redirect_stdout
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location("resource_snapshot", ROOT / "services/analytics/tools/resource_snapshot.py")
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


def container_fixture():
    return [{
        "Id": "a" * 64, "Name": "/" + snapshot.TARGET, "State": {"Running": True},
        "Config": {"Labels": {"com.docker.compose.project": snapshot.PROJECT,
                               "com.docker.compose.service": snapshot.SERVICE,
                               "knowi.analytics.synthetic": "true", "private": "DO_NOT_PRINT"}},
        "HostConfig": {"PortBindings": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "57392"}]},
                       "Memory": 402653184, "NanoCpus": 500000000},
        "NetworkSettings": {"Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "57392"}]}},
        "Mounts": [{"Type": "volume", "Name": snapshot.VOLUME, "Destination": snapshot.DATA,
                    "Source": "DO_NOT_PRINT"}],
    }]


def volume_fixture():
    return [{"Name": snapshot.VOLUME, "Driver": "local", "Mountpoint": "DO_NOT_PRINT",
             "Labels": {"com.docker.compose.project": snapshot.PROJECT,
                        "com.docker.compose.volume": "analytics_postgres_data"}}]


class ResourceSnapshotTests(unittest.TestCase):
    def test_parses_docker_units_and_rejects_unavailable(self):
        self.assertEqual(snapshot.parse_size("1.5MiB"), 1572864)
        self.assertEqual(snapshot.parse_size("2.5 MB"), 2500000)
        self.assertEqual(snapshot.parse_stats("12.25%|1MiB / 384MiB"), (12.25, 1048576, 402653184))
        for value in ("NaN%|1MiB / 1GiB", "--|-- / --", "-1%|1MiB / 1GiB"):
            with self.subTest(value=value), self.assertRaises(snapshot.Refused):
                snapshot.parse_stats(value)

    def test_df_and_wal_convert_1024_byte_blocks(self):
        self.assertEqual(snapshot.parse_df("Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/test 100 30 60 34% /data"), (102400, 30720, 61440))
        self.assertEqual(snapshot.parse_du("123\t" + snapshot.DATA + "/pg_wal"), 125952)
        with self.assertRaises(snapshot.Refused):
            snapshot.parse_du("1\t/unexpected")
        with self.assertRaises(snapshot.Refused):
            snapshot.parse_df("bad\n/dev/test 100 101 60 34% /data")

    def test_wrong_container_never_reaches_stats_or_exec(self):
        for mutation in ("name", "project", "service", "synthetic", "host", "volume", "extra_mount", "running", "id"):
            fixture = container_fixture()
            obj = fixture[0]
            if mutation == "name": obj["Name"] = "/core-postgres"
            if mutation == "project": obj["Config"]["Labels"]["com.docker.compose.project"] = "core"
            if mutation == "service": obj["Config"]["Labels"]["com.docker.compose.service"] = "core"
            if mutation == "synthetic": obj["Config"]["Labels"]["knowi.analytics.synthetic"] = "false"
            if mutation == "host": obj["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostIp"] = "0.0.0.0"
            if mutation == "volume": obj["Mounts"][0]["Name"] = "core-data"
            if mutation == "extra_mount": obj["Mounts"].append({"Type": "bind", "Destination": "/core"})
            if mutation == "running": obj["State"]["Running"] = False
            if mutation == "id": obj["Id"] = "invalid"
            with self.subTest(mutation=mutation), self.assertRaises(snapshot.Refused):
                snapshot.verify_container(fixture)

    def test_volume_ownership_mismatch_refused(self):
        volume = volume_fixture()
        volume[0]["Labels"]["com.docker.compose.project"] = "core"
        with self.assertRaises(snapshot.Refused):
            snapshot.verify_volume(volume)

    def test_wrong_identity_stops_before_volume_stats_and_exec(self):
        wrong = container_fixture()
        wrong[0]["Config"]["Labels"]["com.docker.compose.project"] = "core"
        calls = []
        def fake_command(args, env):
            calls.append(args)
            if args[:3] == ["docker", "context", "show"]: return "local"
            if args[:3] == ["docker", "context", "inspect"]:
                return json.dumps([{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}])
            if "--type" in args: return json.dumps(wrong)
            raise AssertionError("read continued after incorrect identity")
        with patch.object(snapshot, "command", side_effect=fake_command):
            with self.assertRaises(snapshot.Refused):
                snapshot.snapshot({})
        self.assertEqual(len(calls), 3)

    def test_remote_or_overridden_docker_endpoint_refused(self):
        with patch.object(snapshot, "command") as call:
            with self.assertRaises(snapshot.Refused):
                snapshot.local_context({"DOCKER_HOST": "tcp://remote:2375"})
            call.assert_not_called()
        with patch.object(snapshot, "command", side_effect=["remote", json.dumps([{"Endpoints": {"docker": {"Host": "tcp://remote:2375"}}}])]):
            with self.assertRaises(snapshot.Refused):
                snapshot.local_context({})

    def test_snapshot_is_bounded_read_only_and_prints_only_fixed_fields(self):
        calls = []
        def fake_command(args, env):
            calls.append(args)
            if args[:3] == ["docker", "context", "show"]: return "local"
            if args[:3] == ["docker", "context", "inspect"]:
                return json.dumps([{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}])
            if "--type" in args: return json.dumps(container_fixture())
            if "volume" in args: return json.dumps(volume_fixture())
            if "stats" in args: return "0.20%|32MiB / 384MiB"
            if "df" in args: return "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/private 100 30 60 34% /data"
            if "du" in args: return "123\t" + snapshot.DATA + "/pg_wal"
            raise AssertionError("unexpected command")
        with patch.object(snapshot, "command", side_effect=fake_command):
            result = snapshot.snapshot({})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(set(result), {"status", "timestamp_utc", "target_identity", "volume_identity", "docker_endpoint",
                                       "cpu_percent", "cpu_limit_cores", "memory_used_bytes_approx", "memory_limit_bytes",
                                       "stats_memory_limit_bytes_approx", "filesystem_total_bytes", "filesystem_used_bytes",
                                       "filesystem_available_bytes", "wal_disk_usage_bytes"})
        self.assertEqual(result["memory_limit_bytes"], 402653184)
        self.assertEqual(result["wal_disk_usage_bytes"], 125952)
        self.assertEqual(result["cpu_limit_cores"], 0.5)
        rendered = json.dumps(result)
        for forbidden in ("DO_NOT_PRINT", snapshot.TARGET, snapshot.VOLUME, snapshot.DATA, "unix:", "/dev/private"):
            self.assertNotIn(forbidden, rendered)
        data_calls = [a for a in calls if "stats" in a or "exec" in a]
        self.assertEqual(len(data_calls), 3)
        for call in data_calls:
            self.assertIn("a" * 64, call)
        exec_calls = [a for a in data_calls if "exec" in a]
        self.assertEqual([call[call.index("exec"):] for call in exec_calls], [
            ["exec", "--user", "postgres", "a" * 64, "df", "-Pk", snapshot.DATA],
            ["exec", "--user", "postgres", "a" * 64, "du", "-sk", snapshot.DATA + "/pg_wal"],
        ])
        self.assertTrue(all("sh" not in call and "bash" not in call for call in calls))

    def test_refusal_output_never_exposes_failure_detail(self):
        output = io.StringIO()
        with patch.object(snapshot, "snapshot", side_effect=snapshot.Refused("target_identity")), redirect_stdout(output):
            code = snapshot.main()
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output.getvalue()), {"status": "refused", "reason": "target_identity"})


if __name__ == "__main__":
    unittest.main()
