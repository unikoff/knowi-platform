"""Filesystem guard for private, task-owned cross-service fixture state."""
import json
import os
from pathlib import Path
import re
from uuid import UUID


def fixture_root(value):
    if not value:
        raise ValueError("fixture directory required")
    # The Analytics workspace owns these fixtures, not the Core runtime state.
    analytics = Path(__file__).resolve().parents[3].parent / "analytics"
    base = analytics / ".local" / "cross-service"
    path = Path(value)
    if not path.is_absolute() or not re.fullmatch(r"[0-9a-f]{32}", path.name) or UUID(hex=path.name).version != 4:
        raise ValueError("invalid fixture directory")
    for part in (analytics / ".local", base, path):
        if part.is_symlink() or os.path.isjunction(part):
            raise ValueError("fixture links are forbidden")
    base = base.resolve(strict=True)
    if path.parent.resolve(strict=True) != base:
        raise ValueError("fixture directory is outside the owned root")
    path = path.resolve(strict=True)
    if path.parent != base or not path.is_dir():
        raise ValueError("fixture directory is outside the owned root")
    return path


def read_json(path):
    if path.is_symlink() or os.path.isjunction(path) or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("invalid fixture document")
    def unique(pairs):
        output = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate fixture field")
            output[key] = value
        return output
    result = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise ValueError("invalid fixture document")
    return result
