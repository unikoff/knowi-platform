"""Isolated course manifest copy for HTTP integration tests."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
from uuid import uuid4


@contextmanager
def courses_for(leaders):
    parent = Path(__file__).resolve().parents[2]
    root = Path(__file__).resolve().parents[4]
    copied = parent / ('.scope-test-courses-' + uuid4().hex)
    assert copied.resolve().parent == parent and copied.name.startswith('.scope-test-courses-')
    try:
        shutil.copytree(root/'courses', copied, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'node_modules'))
        for course, uid in leaders.items():
            manifest = copied/course/'course.yaml'
            data = json.loads(manifest.read_text(encoding='utf-8'))
            data['leader_user_id'] = uid
            manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        yield copied
    finally:
        if copied.exists():
            assert copied.resolve().parent == parent
            shutil.rmtree(copied)
