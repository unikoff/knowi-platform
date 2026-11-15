"""Probe only loopback Vite public/private file boundaries; never call API routes."""

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, build_opener


ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="http://127.0.0.1:18517")
    parser.add_argument("--base", choices=("/", "/platform/"), default="/")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    origin = urlsplit(args.origin)
    if origin.scheme != "http" or origin.hostname != "127.0.0.1" or origin.port not in {18517, 18518} or origin.path or origin.username or origin.password or origin.query or origin.fragment:
        parser.error("only owned loopback Vite ports are allowed")
    if args.output.exists():
        parser.error("choose a new evidence output")
    opener = build_opener(ProxyHandler({}))
    rows = []
    for course in ("backend", "backend_copy"):
        lesson_page = next(iter(sorted((ROOT / f"courses/{course}/frontend/course/chapter_01/block_01").glob("Lesson*.tsx"))), None)
        if lesson_page is None:
            raise RuntimeError("public lesson page fixture is missing")
        public = [f"courses/{course}/frontend/course/index.ts", lesson_page.relative_to(ROOT).as_posix()]
        private = [f"courses/{course}/personal.settings.yaml", f"courses/{course}/course.yaml", f"courses/{course}/backend/practice/chapter_01/block_01/code_tasks.py", f"courses/{course}/backend/plans/chapter_01/track.json"]
        for relative, allowed in [(path, True) for path in public] + [(path, False) for path in private]:
            file = ROOT / relative
            if not file.is_file():
                raise RuntimeError(f"probe fixture file is missing: {relative}")
            for suffix in (("",) if allowed else ("", "?raw", "?import")):
                path = args.base + "@fs/" + quote(file.as_posix(), safe="/:") + suffix
                try:
                    with opener.open(args.origin + path, timeout=30) as response:
                        status = response.status
                        media = response.headers.get("Content-Type", "")
                except HTTPError as error:
                    status, media = error.code, error.headers.get("Content-Type", "")
                    error.close()
                passed = (status == 200 and "javascript" in media) if allowed else status in {403, 404}
                rows.append({"course": course, "resource": relative, "query": suffix, "public": allowed, "status": status, "content_type": media, "pass": passed})
    report = {"origin": args.origin, "base": args.base, "api_requests": 0, "private_response_bodies_saved": False, "checks": rows, "pass": all(row["pass"] for row in rows)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"checks": len(rows), "passed": sum(row["pass"] for row in rows), "private_statuses": sorted({row["status"] for row in rows if not row["public"]}), "public_statuses": sorted({row["status"] for row in rows if row["public"]}), "api_requests": 0}))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
