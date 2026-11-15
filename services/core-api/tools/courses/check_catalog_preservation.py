"""Compare stable catalog identities and full private task hashes without SQL/I/O."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

CORE_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(CORE_ROOT))

from pymentor_core.adapters.course_local.runtime import CourseRuntime


def capture(runtime, course_id):
    catalog = runtime.snapshot(course_id).catalog
    return {
        "content_revision": catalog.get_content_revision(),
        "tracks": [[track["id"], track["order"]] for track in catalog.TRACKS],
        "task_count": sum(len(lesson["tasks"]) for lesson in catalog.LESSONS),
        "lessons": [{
            "track_id": track["id"], "id": lesson["id"], "legacy_id": lesson["legacy_id"],
            "source_file": lesson["source_file"], "title": lesson["title"],
            "tasks": [{"id": task["id"], "revision": task["revision"],
                       "private_sha256": hashlib.sha256(json.dumps(task, ensure_ascii=False,
                           sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()}
                      for task in lesson["tasks"]],
        } for track in catalog.TRACKS for lesson in track["lessons"]],
    }


def check(courses_root, baseline):
    before = json.loads(Path(baseline).read_text(encoding="utf-8"))
    runtime = CourseRuntime(courses_root)
    result = {}
    for course_id, expected in before.items():
        actual = capture(runtime, course_id)
        if actual != expected:
            raise ValueError(f"catalog preservation mismatch: {course_id}")
        result[course_id] = {"lessons": len(actual["lessons"]),
                             "tasks": sum(len(lesson["tasks"]) for lesson in actual["lessons"]),
                             "stable_ids_revisions_private_hashes": "PASS"}
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--courses-root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.courses_root, args.baseline), ensure_ascii=False))
