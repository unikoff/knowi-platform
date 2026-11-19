"""Owned Docker publisher for the opt-in local rehearsal, never a deployment."""
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import resources as r

LABEL = "org.pymentor.cross-service.operation"
IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
KEYS = {
    "PYMENTOR_ANALYTICS_PRODUCER_KEY_FILE": "producer.private.pem",
    "PYMENTOR_ANALYTICS_COMMAND_KEY_FILE": "command.private.pem",
    "PYMENTOR_ANALYTICS_GRANT_PUBLIC_KEY_FILE": "grant.public.pem",
    "PYMENTOR_ANALYTICS_AUTHORITY_PUBLIC_KEY_FILE": "authority.public.pem",
}
ENV_KEYS = (
    "PYMENTOR_ANALYTICS_PUBLISHER_ENABLED", "PYMENTOR_ANALYTICS_DELIVERY_ENABLED",
    "PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED", "PYMENTOR_ANALYTICS_PUBLISHER_BACKFILL_COMPLETE",
    "PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_DATABASE", "PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_USER",
    "PYMENTOR_SCHEMA_MODE", "PYMENTOR_ANALYTICS_PRODUCER_ISSUER", "PYMENTOR_ANALYTICS_PRODUCER_KID",
    "PYMENTOR_ANALYTICS_PRODUCER_ID", "PYMENTOR_ANALYTICS_AUTHORITY_ID",
    "PYMENTOR_ANALYTICS_AUTHORITY_ISSUER", "PYMENTOR_ANALYTICS_AUTHORITY_KID",
)


def docker(*args, timeout=12, check=True):
    result = subprocess.run(["docker", *args], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, timeout=timeout,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if check and result.returncode:
        raise RuntimeError("Docker operation failed; inspect private ownership record")
    return result


def inspect(kind, name):
    result = docker("inspect", "--type", kind, name, check=False)
    if result.returncode:
        if result.stdout.strip() == "[]" and ("No such" in result.stderr or "not found" in result.stderr):
            return None
        raise RuntimeError("Docker inspect unavailable; outcome unknown")
    data = json.loads(result.stdout)
    if len(data) != 1:
        raise RuntimeError("Docker identity ambiguous")
    return data[0]


def checked_host_url(value, expected, *, database=False):
    if value != expected:
        raise ValueError("fixture endpoint mismatch")
    parsed = urlsplit(value)
    if (parsed.hostname != "127.0.0.1" or parsed.query or parsed.fragment
            or (database and (parsed.scheme != "postgresql+psycopg" or parsed.port != 57382))
            or (not database and (parsed.scheme != "http" or not parsed.port or parsed.username
                                  or parsed.password or parsed.path not in ("", "/")))):
        raise ValueError("fixture endpoint outside allowlist")
    credentials = parsed.netloc.rsplit("@", 1)[0] + "@" if parsed.username else ""
    netloc = credentials + "host.docker.internal:" + str(parsed.port)
    return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))


def container_env(env, fixture, core_db, authority_url, folder):
    values = {key: env[key] for key in ENV_KEYS}
    if (not r.CORE_NAME.fullmatch(core_db["name"]) or core_db["owner"] != "pymentor_test"
            or fixture["collector_url"] != "http://127.0.0.1:18152"
            or values["PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_DATABASE"] != core_db["name"]
            or values["PYMENTOR_ANALYTICS_PUBLISHER_EXPECTED_USER"] != core_db["owner"]):
        raise ValueError("publisher database identity mismatch")
    if any(values[key] != "1" for key in ("PYMENTOR_ANALYTICS_PUBLISHER_ENABLED",
           "PYMENTOR_ANALYTICS_DELIVERY_ENABLED", "PYMENTOR_ANALYTICS_SNAPSHOT_PROOF_ENABLED",
           "PYMENTOR_ANALYTICS_PUBLISHER_BACKFILL_COMPLETE")):
        raise ValueError("publisher admission flags mismatch")
    database_url = urlsplit(env["DATABASE_URL"])
    if database_url.username != core_db["owner"] or database_url.path != "/" + core_db["name"]:
        raise ValueError("publisher database URL identity mismatch")
    values["DATABASE_URL"] = checked_host_url(env["DATABASE_URL"], core_db["dsn"].replace("postgresql://", "postgresql+psycopg://", 1), database=True)
    checked_host_url(env["PYMENTOR_ANALYTICS_TRANSFER_URL"], fixture["collector_url"])
    checked_host_url(env["PYMENTOR_ANALYTICS_AUTHORITY_URL"], authority_url)
    values["PYMENTOR_ANALYTICS_TRANSFER_URL"] = fixture["collector_url"]
    values["PYMENTOR_ANALYTICS_AUTHORITY_URL"] = authority_url
    mounts = []
    for key, filename in KEYS.items():
        source = (folder / filename).resolve(strict=True)
        if source.parent != folder.resolve(strict=True) or str(source) != str(Path(env[key]).resolve(strict=True)) or "," in str(source):
            raise ValueError("fixture key path mismatch")
        target = "/run/publisher-keys/" + filename
        values[key] = target
        mounts.extend(["--mount", f"type=bind,src={source},dst={target},readonly"])
    if any("\n" in value or "\r" in value for value in values.values()):
        raise ValueError("invalid env file value")
    return values, mounts


class DockerPublisher:
    def __init__(self, image, folder, token, env, fixture, core_db, authority_url):
        if not IMAGE.fullmatch(image):
            raise ValueError("publisher image must be an exact local sha256 ID")
        resolved = docker("image", "inspect", image, "--format", "{{.Id}}").stdout.strip()
        if resolved != image:
            raise ValueError("publisher image identity mismatch")
        self.image, self.folder, self.token = image, folder, token
        self.operation = folder.name
        if not re.fullmatch(r"[0-9a-f]{32}", self.operation):
            raise ValueError("invalid fixture identity")
        self.network = "cross-publisher-" + self.operation
        self.relay = self.network + "-relay"
        self.relay_port = urlsplit(authority_url).port
        self.names = []
        self.network_intent = False
        self.relay_intent = False
        self.env, self.mounts = container_env(env, fixture, core_db, authority_url, folder)
        self.envfile = folder / "publisher-container.env"
        with self.envfile.open("x", encoding="utf-8", newline="\n") as stream:
            for key, value in self.env.items():
                stream.write(key + "=" + value + "\n")
    def prepare(self):
        if inspect("network", self.network) is not None:
            raise RuntimeError("owned network name already exists")
        self.network_intent = True
        r.record(self.folder, self.token, docker_network=self.network, docker_image=self.image, docker_network_state="create_intent")
        docker("network", "create", "--label", LABEL + "=" + self.operation, self.network)
        self._network()
        r.record(self.folder, self.token, docker_network_state="created")
        if inspect("container", self.relay) is not None:
            raise RuntimeError("relay container name already exists")
        relay_source = Path(__file__).with_name("container_loopback_relay.py").resolve(strict=True)
        if "," in str(relay_source):
            raise ValueError("invalid relay source path")
        self.relay_intent = True
        r.record(self.folder, self.token, docker_relay=self.relay, docker_relay_state="create_intent")
        docker("create", "--name", self.relay, "--label", LABEL + "=" + self.operation,
               "--network", self.network, "--pull", "never", "--user", "10001:10001", "--read-only",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "96m",
               "--memory-swap", "96m", "--cpus", "0.25", "--pids-limit", "48", "--restart", "no",
               "--mount", f"type=bind,src={relay_source},dst=/run/relay.py,readonly", "--entrypoint", "python",
               self.image, "-B", "-u", "/run/relay.py", "18152", str(self.relay_port))
        if self._container(self.relay) is None:
            raise RuntimeError("created relay container missing")
        r.record(self.folder, self.token, docker_relay_state="start_intent")
        docker("start", self.relay)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.poll(self.relay) is not None:
                raise RuntimeError("relay exited before readiness")
            logs = docker("logs", self.relay)
            if "relay_ready" in logs.stdout + logs.stderr:
                r.record(self.folder, self.token, docker_relay_state="running")
                return
            time.sleep(.2)
        raise TimeoutError("relay readiness unknown")

    def _network(self):
        item = inspect("network", self.network)
        if item is None or item.get("Labels", {}).get(LABEL) != self.operation or item.get("Name") != self.network:
            raise RuntimeError("owned Docker network identity mismatch")
        return item

    def _container(self, name):
        item = inspect("container", name)
        if item is None:
            return None
        if (item.get("Name") != "/" + name or item.get("Image") != self.image
                or item.get("Config", {}).get("Labels", {}).get(LABEL) != self.operation):
            raise RuntimeError("owned Docker container identity mismatch")
        return item

    def start(self, label):
        if label not in ("publisher-first", "publisher-restart"):
            raise ValueError("invalid publisher label")
        name = self.network + "-" + label
        if inspect("container", name) is not None:
            raise RuntimeError("publisher container name already exists")
        self.names.append(name)
        r.record(self.folder, self.token, docker_container=name, docker_container_state="create_intent")
        docker("create", "--name", name, "--label", LABEL + "=" + self.operation,
               "--network", "container:" + self.relay, "--pull", "never", "--user", "10001:10001", "--read-only",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "256m",
               "--memory-swap", "256m", "--cpus", "0.75", "--pids-limit", "64", "--restart", "no",
               "--env-file", str(self.envfile), *self.mounts, "--entrypoint", "python", self.image,
               "-B", "-m", "pymentor_core.app.analytics_publisher")
        if self._container(name) is None:
            raise RuntimeError("created publisher container missing")
        r.record(self.folder, self.token, docker_container_state="start_intent")
        docker("start", name)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.poll(name) is not None:
                raise RuntimeError("publisher container exited before readiness")
            logs = docker("logs", name)
            if "publisher_ready" in logs.stdout + logs.stderr:
                r.record(self.folder, self.token, docker_container_state="running")
                return name
            time.sleep(.2)
        raise TimeoutError("publisher container readiness unknown")

    def poll(self, name):
        item = self._container(name)
        if item is None:
            raise RuntimeError("publisher container disappeared")
        return None if item["State"]["Running"] else item["State"]["ExitCode"]

    def stop(self, name):
        if self.poll(name) is not None:
            raise RuntimeError("publisher exited before requested SIGTERM")
        r.record(self.folder, self.token, docker_container=name, docker_container_state="sigterm_intent")
        docker("kill", "--signal", "SIGTERM", name)
        result = docker("wait", name, timeout=20)
        item = self._container(name)
        logs = docker("logs", name)
        (self.folder / (name + ".log")).write_text(logs.stdout + logs.stderr, encoding="utf-8")
        if result.stdout.strip() != "0" or item is None or item["State"]["Running"] or item["State"]["ExitCode"] != 0 or "publisher_stopped" not in logs.stderr + logs.stdout:
            raise RuntimeError("publisher graceful shutdown not proven")
        r.record(self.folder, self.token, docker_container_state="sigterm_confirmed")
        docker("rm", name)
        if self._container(name) is not None:
            raise RuntimeError("publisher removal not confirmed")
        r.record(self.folder, self.token, docker_container_state="removed")

    def close(self):
        for name in reversed(self.names):
            item = self._container(name)
            if item is not None:
                if item["State"]["Running"]:
                    self.stop(name)
                else:
                    raise RuntimeError("stopped publisher requires reconciliation")
        relay = self._container(self.relay) if self.relay_intent else None
        if relay is not None:
            if not relay["State"]["Running"]:
                raise RuntimeError("stopped relay requires reconciliation")
            r.record(self.folder, self.token, docker_relay_state="sigterm_intent")
            docker("kill", "--signal", "SIGTERM", self.relay)
            result = docker("wait", self.relay, timeout=15)
            relay = self._container(self.relay)
            logs = docker("logs", self.relay)
            if result.stdout.strip() != "0" or relay is None or relay["State"]["Running"] or "relay_stopped" not in logs.stdout + logs.stderr:
                raise RuntimeError("relay shutdown not proven")
            docker("rm", self.relay)
            if self._container(self.relay) is not None:
                raise RuntimeError("relay removal not confirmed")
            r.record(self.folder, self.token, docker_relay_state="removed")
        item = inspect("network", self.network) if self.network_intent else None
        if item is not None:
            self._network()
            r.record(self.folder, self.token, docker_network_state="remove_intent")
            docker("network", "rm", self.network)
            if inspect("network", self.network) is not None:
                raise RuntimeError("publisher network removal not confirmed")
        r.record(self.folder, self.token, docker_network_state="removed")
