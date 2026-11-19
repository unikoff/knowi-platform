"""Prepare a private host image archive; never imports it into a node.

Default invocation is an offline plan. Explicit resource and image-probe GO
are required before the one-shot host export and bounded trusted-image probe.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from uuid import UUID

import benchmark_k3s as bench

MAX_ARCHIVE_MIB = 1024


def prepare(args):
    if (os.environ.get("ANALYTICS_BENCHMARK_IMAGE_PREPARE_GO") != "1"
            or os.environ.get("ANALYTICS_BENCHMARK_IMAGE_PROBE_GO") != "1"):
        raise bench.LiveFailure("separate image preparation and probe GO required")
    if os.name != "nt" or sys.version_info[:2] != (3, 12):
        raise bench.LiveFailure("Windows and Analytics Python 3.12 are required")
    run_id = UUID(args.operation_id)
    bench.namespace_for(run_id)
    if (str(run_id) != args.operation_id or not isinstance(args.host_image_id, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.host_image_id)
            or type(args.archive_max_mib) is not int or not 128 <= args.archive_max_mib <= MAX_ARCHIVE_MIB
            or args.target_revision not in bench.HISTORY_SHA256):
        raise bench.LiveFailure("invalid preparation identity or archive cap")
    import benchmark_activity as b
    import bootstrap_runtime as history
    import run_k3s as runtime
    import run_local as source_tools
    bench.verified_histories(history, args.target_revision)
    service = Path(__file__).resolve().parents[1]
    local = service / ".local"
    if (local.is_symlink() or (hasattr(os.path, "isjunction") and os.path.isjunction(local))
            or not local.is_dir() or local.resolve(strict=True).parent != service.resolve(strict=True)):
        raise bench.LiveFailure("unsafe local state directory")
    monitor = b.resource_floors(os.environ)
    monitor.check()
    if monitor.samples[-1]["free_disk_bytes"] < monitor.disk_floor_bytes + args.archive_max_mib * 1024 * 1024:
        raise bench.LiveFailure("disk floor lacks reserved archive capacity")
    source = bench._source_fingerprint(service)
    folder, token = b.owned.private_folder()
    folder = Path(folder)
    owner_path = str(folder.relative_to(service) / "owner.json")
    intent = {"operation_id": str(run_id), "host_image_id": args.host_image_id,
              "archive_max_mib": args.archive_max_mib, "target_revision": args.target_revision,
              "owner_record": owner_path, "source_sha256": source}
    lock = local / "benchmark-image-prepare.active.json"
    unique = local / ("benchmark-image-prepare-" + run_id.hex + ".intent.json")
    # Both records are exclusive and file-fsynced before Docker is invoked.
    # The directory-entry persistence boundary on Windows is not claimed.
    bench._write_exclusive(lock, intent)
    try:
        bench._write_exclusive(unique, intent)
        b.owned.record(folder, token, state="k3s_image_preparation_pending", **intent)
        archive = bench._host_image_proof(folder, token, run_id, "application", args.host_image_id,
                                          None, runtime, b.owned, monitor, args.archive_max_mib)
        monitor.check()
        if bench._source_fingerprint(service) != source:
            raise bench.LiveFailure("source changed before image probe; reconcile")
        proof = bench._source_probe(folder, token, run_id, "application", args.host_image_id,
                                    runtime, source_tools, b.owned, args.target_revision)
        if bench._source_fingerprint(service) != source:
            raise bench.LiveFailure("source changed during image probe; reconcile")
        result = {**intent, "archive": archive, "packaged_source": proof,
                  "node_attestation": "UNRUN", "node_import": "UNRUN",
                  "base_image_and_installed_dependencies": "NOT_VERIFIED"}
        bench._write_exclusive(folder / "image-preparation-result.json", result)
        b.owned.record(folder, token, state="k3s_image_preparation_finished", processes=[])
        if json.loads(lock.read_text(encoding="utf-8")) != intent:
            raise bench.LiveFailure("preparation lock ownership changed; reconcile")
        lock.unlink()
        return result
    except BaseException:
        try: b.owned.record(folder, token, state="k3s_image_preparation_reconciliation_required")
        except Exception: pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-image", action="store_true")
    parser.add_argument("--operation-id", help="new canonical UUIDv4")
    parser.add_argument("--host-image-id", help="immutable Docker image ID, possibly an OCI index")
    parser.add_argument("--target-revision", choices=("a010", "a011"), default="a011")
    parser.add_argument("--archive-max-mib", type=int, default=MAX_ARCHIVE_MIB)
    args = parser.parse_args(argv)
    if not args.prepare_image:
        print(json.dumps({"status": "UNRUN", "execution": "not_started", "node_import": "UNRUN",
                          "archive_max_mib": MAX_ARCHIVE_MIB, "platform": "linux/amd64"}, sort_keys=True))
        return 0
    if args.operation_id is None or args.host_image_id is None:
        print("image preparation refused: operation UUID and immutable host ID required", file=sys.stderr)
        return 2
    try:
        result = prepare(args)
    except Exception:
        print("image preparation refused or outcome unknown; inspect active marker and reconcile before retry", file=sys.stderr)
        return 2
    print(json.dumps({"operation_id": result["operation_id"], "owner_record": result["owner_record"],
                      "archive": result["archive"]["archive_name"], "node_import": "UNRUN"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
