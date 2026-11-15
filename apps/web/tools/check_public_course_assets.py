"""Fail closed if known private server paths/fields appear in web build assets."""

import argparse
import json
from pathlib import Path


MARKERS = (
    "personal.settings.yaml", "PYMENTOR_LANDING_BOT_CREDENTIALS",
    "/backend/practice/", "/backend/plans/", "reference_code", "hidden_tests",
    "MC205_PRIVATE_JUDGE_SENTINEL_8a51d96e", "MC205_HIDDEN_CASE_102f24f1",
)
ASSET_SUFFIXES = {".js", ".mjs", ".map", ".json", ".html", ".css"}


def inspect_assets(root: Path) -> dict:
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("asset root must be a directory")
    checked = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in ASSET_SUFFIXES:
            continue
        if root not in path.resolve(strict=True).parents:
            raise ValueError("asset path escapes inspected build")
        content = path.read_text(encoding="utf-8")
        matches = [marker for marker in MARKERS if marker in content]
        if matches:
            raise ValueError(f"private server marker in {path.relative_to(root)}: {matches}")
        checked.append(path.relative_to(root).as_posix())
    if not any(path.endswith(".js") for path in checked):
        raise ValueError("no JavaScript build was inspected")
    return {"result": "PASS", "assets": len(checked), "javascript": sum(path.endswith(".js") for path in checked), "source_maps": sum(path.endswith(".map") for path in checked), "marker_count": len(MARKERS), "limitation": "Known private markers only; authored client-visible course text is not a private-secret guarantee"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = inspect_assets(args.build)
        if args.output:
            if args.output.exists():
                raise ValueError("choose a new output; evidence is never overwritten")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
    except (OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")
