"""Start the local Core + web workspace; stop only the children started here."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
CORE = ROOT / "services" / "core-api"
WEB = ROOT / "apps" / "web"


def commands(mode, api_port, web_port, base):
    python = CORE / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    node = shutil.which("node")
    vite = WEB / "node_modules" / "vite" / "bin" / "vite.js"
    if not python.is_file() or not node or not vite.is_file():
        raise ValueError("Install Core requirements-dev.txt in its .venv and run npm ci in apps/web first")
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHON_DOTENV_DISABLED="1", PYTHONIOENCODING="utf-8",
               PYTHONPATH=str(CORE), PYMENTOR_DEV_API_TARGET=f"http://127.0.0.1:{api_port}", VITE_BASE=base)
    if mode == "lab":
        api = [str(python), "-B", str(CORE / "tools/dev/multicourse_local.py"), "run", "--port", str(api_port)]
    else:
        if not all(env.get(key) for key in ("DATABASE_URL", "REDIS_URL")):
            raise ValueError("Configured mode requires exported DATABASE_URL and REDIS_URL; .env is not loaded")
        api = [str(python), "-B", "-m", "uvicorn", "pymentor_core.app.asgi:create_app", "--factory",
               "--host", "127.0.0.1", "--port", str(api_port)]
    web = [node, str(vite), "--config", "config/vite.config.ts", "--configLoader", "bundle",
           "--host", "127.0.0.1", "--port", str(web_port)]
    return api, web, env


def free_port(port):
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError as error:
            raise ValueError(f"Port {port} is already used; keep that process or choose a different port") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("lab", "configured"), default="configured",
                        help="configured: exported service settings; lab: this computer's disposable Windows lab")
    parser.add_argument("--api-port", type=int)
    parser.add_argument("--web-port", type=int, default=5173)
    parser.add_argument("--base", choices=("/", "/platform/"), default="/")
    parser.add_argument("--check", action="store_true", help="validate local dependencies without launching")
    args = parser.parse_args()
    api_port = args.api_port or (18001 if args.mode == "lab" else 8001)
    if api_port == args.web_port or any(not 1024 <= port <= 65535 for port in (api_port, args.web_port)):
        parser.error("choose two different loopback ports between 1024 and 65535")
    try:
        api_command, web_command, env = commands(args.mode, api_port, args.web_port, args.base)
        if args.check:
            print("PASS service-local Python, web dependencies and workspace command paths")
            return 0
        for port in (api_port, args.web_port):
            free_port(port)
    except ValueError as error:
        parser.error(str(error))
    children, logs = [], []
    try:
        for command, cwd, log in (
            (api_command, CORE, CORE / ".local/logs/dev-api.log"),
            (web_command, WEB, WEB / ".local/logs/dev-web.log"),
        ):
            log.parent.mkdir(parents=True, exist_ok=True)
            stream = log.open("a", encoding="utf-8")
            stream.write(f"\nStarting {datetime.now(timezone.utc).isoformat()}\n")
            stream.flush()
            logs.append(stream)
            children.append(subprocess.Popen(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        print(f"Core: http://127.0.0.1:{api_port}/health/ready", flush=True)
        print(f"Web: http://127.0.0.1:{args.web_port}{args.base}", flush=True)
        print("Logs: services/core-api/.local/logs and apps/web/.local/logs. Ctrl+C stops these children.", flush=True)
        while True:
            for child in children:
                result = child.poll()
                if result is not None:
                    print(f"Workspace process exited ({result}); inspect its local log.", file=sys.stderr)
                    return result or 1
            time.sleep(.25)
    except KeyboardInterrupt:
        return 0
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=3)
        for stream in logs:
            stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
