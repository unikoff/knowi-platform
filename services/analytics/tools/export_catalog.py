"""Export independent catalog v1 JSON Schemas deterministically."""
from pathlib import Path
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymentor_analytics.contracts.catalog_v1 import Snapshot, Delta

ROOT = Path(__file__).resolve().parents[1] / "contracts" / "catalog"

def main():
    if set(sys.argv[1:]) - {"--check"}:
        raise SystemExit("usage: export_catalog.py [--check]")
    check = "--check" in sys.argv[1:]
    for name, model in (("snapshot", Snapshot), ("delta", Delta)):
        path = ROOT / f"{name}-v1.schema.json"
        content = json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                raise SystemExit(f"outdated artifact: {path}")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    print("catalog artifacts " + ("match" if check else "exported"))

if __name__ == "__main__":
    main()
