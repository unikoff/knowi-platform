import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "pymentor_analytics"


def test_no_core_or_private_cross_module_imports():
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parts = path.relative_to(PACKAGE).parts
        owner = parts[1] if len(parts) > 2 and parts[0] == "modules" else None
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if owner and node.level >= 2:
                    assert not (node.module or "").startswith(("collector", "analytics")), str(path)
            for name in names:
                assert not (name == "pymentor_core" or name.startswith("pymentor_core.")), str(path)
                # Business/application modules and public ports must not load a
                # concrete adapter or the composition root. API is a transport.
                if owner and "api" not in parts[2:]:
                    assert not name.startswith(("pymentor_analytics.adapters.", "pymentor_analytics.app.")), (str(path), name)
                prefix = "pymentor_analytics.modules."
                if owner and name.startswith(prefix):
                    target = name[len(prefix):].split(".")
                    if target[0] != owner:
                        assert len(target) == 2 and target[1] == "ports", (str(path), name)


def test_runtime_image_has_no_course_mount_or_core_copy():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8").lower()
    assert "volume " not in dockerfile
    assert "core-api" not in dockerfile and "courses/" not in dockerfile
    assert "user 10001" in dockerfile
