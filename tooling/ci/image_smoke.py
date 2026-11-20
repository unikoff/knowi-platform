"""Exercise the disposable Compose stack, optionally using published candidate digests."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "infra/local-postgres-redis/compose.yaml"
IMAGE_PREFIX = "ghcr.io/unikoff/knowi-platform-"


def candidate_image(value, name):
    """Restrict the release gate to the intended repository and an immutable digest."""
    if not re.fullmatch(re.escape(IMAGE_PREFIX + name) + r"@sha256:[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError(f"expected {IMAGE_PREFIX}{name}@sha256:<64 lowercase hex>")
    return value


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-image", type=lambda value: candidate_image(value, "core"))
    parser.add_argument("--migration-image", type=lambda value: candidate_image(value, "core-migrations"))
    args = parser.parse_args(argv)
    if bool(args.core_image) != bool(args.migration_image):
        parser.error("--core-image and --migration-image must be supplied together")
    return args


def run_smoke(args):
    project = "knowi-local-ci-" + uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="knowi-image-smoke-") as temporary:
        command = ["docker", "compose", "-p", project, "-f", str(COMPOSE_FILE)]
        if args.core_image:
            # JSON is also a Compose YAML document. Paths in the base file remain
            # relative to that file (the first -f), not this temporary override.
            override = Path(temporary) / "candidate.json"
            override.write_text(json.dumps({"services": {
                "core": {"image": args.core_image},
                "worker": {"image": args.core_image},
                "migrate": {"image": args.migration_image},
            }}), encoding="utf-8")
            command.extend(["-f", str(override)])

        def run(arguments, timeout=180):
            subprocess.run([*command, *arguments], cwd=ROOT, check=True, timeout=timeout)

        try:
            if args.core_image:
                for image in (args.core_image, args.migration_image):
                    subprocess.run(["docker", "pull", image], cwd=ROOT, check=True, timeout=600)
            run(["up", "--no-build" if args.core_image else "--build", "--wait", "--wait-timeout", "180", "core"], timeout=600)
            run(["run", "--rm", "--no-deps", "smoke"])
            run(["run", "--rm", "--no-deps", *(["--pull", "never"] if args.core_image else []), "worker"])
            subprocess.run([sys.executable, "tooling/ci/check_single_runtime.py", "--image",
                            args.core_image or "knowi/core-local:pg06"],
                           cwd=ROOT, check=True, timeout=90)
        finally:
            run(["down", "--volumes", "--remove-orphans"], timeout=90)


def main(argv=None):
    run_smoke(parse_args(argv))


if __name__ == "__main__":
    main()
