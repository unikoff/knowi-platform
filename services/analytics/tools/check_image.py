"""Run isolated image health/lifecycle checks without DB or host networking."""
import json
import subprocess
import sys

PROBE = r'''
import json, os, signal, subprocess, sys, time, urllib.error, urllib.request
role = sys.argv[1]
env = dict(os.environ)
env[f"ANALYTICS_{role.upper()}_DATABASE_URL"] = "postgresql://synthetic:synthetic@unreachable:5432/analytics_smoke"
proc = subprocess.Popen([sys.executable, "-B", "-m", "pymentor_analytics.app.cli", role, "--port", "18080"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
try:
    deadline = time.monotonic() + 15
    while True:
        try:
            with urllib.request.urlopen("http://127.0.0.1:18080/live", timeout=1) as response:
                assert response.status == 200
            break
        except (OSError, urllib.error.URLError):
            if proc.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("liveness startup failed")
            time.sleep(.05)
    try:
        urllib.request.urlopen("http://127.0.0.1:18080/ready", timeout=2)
        raise AssertionError("skeleton falsely ready")
    except urllib.error.HTTPError as error:
        assert error.code == 503
    with urllib.request.urlopen("http://127.0.0.1:18080/metrics", timeout=3) as response:
        metrics = response.read(65536).decode("utf-8")
        assert response.status == 200
        assert 'analytics_process_resident_memory_bytes_known{role="' + role + '"} 1' in metrics
        assert "synthetic:synthetic" not in metrics and "unreachable" not in metrics
    proc.send_signal(signal.SIGTERM)
    output, _ = proc.communicate(timeout=15)
    assert "Application shutdown complete" in output, output
    assert proc.returncode in (0, -signal.SIGTERM), proc.returncode
    print(json.dumps({"role": role, "live": 200, "ready": 503, "private_metrics": True, "linux_rss_known": True, "graceful_shutdown": True, "uid": os.getuid(), "python": sys.version.split()[0]}))
finally:
    if proc.poll() is None:
        proc.kill()
        proc.communicate()
'''


def main():
    image = sys.argv[1] if len(sys.argv) == 2 else "knowi-analytics:an1-local"
    for role in ("collector", "processor", "query", "privacy"):
        result = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--memory", "128m",
             "--cpus", "0.5", "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=8m",
             "--entrypoint", "python", image, "-B", "-c", PROBE, role],
            capture_output=True, text=True, timeout=45,
        )
        if result.returncode:
            raise SystemExit(result.stdout + result.stderr)
        record = json.loads(result.stdout)
        assert record["uid"] == 10001, record
        print(json.dumps(record))


if __name__ == "__main__":
    main()
