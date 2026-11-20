"""The optional leader ID is explicit, strictly typed and never inferred."""
import json
from pathlib import Path
import sys

import pytest

from pymentor_core.adapters.course_local.packages import CoursePackageError, _validate_manifest
from pymentor_core.adapters.course_local.runtime import CourseRuntime


COURSES = Path(__file__).resolve().parents[3] / 'courses'


@pytest.fixture
def manifest():
    root = Path(__file__).resolve().parents[3]
    return json.loads((root/'courses/backend/course.yaml').read_text(encoding='utf-8'))


@pytest.mark.parametrize('value', [0, -1, True, False, '2', 2.0, [], {}])
def test_invalid_leader_ids_are_rejected(manifest, value):
    manifest['leader_user_id'] = value
    with pytest.raises(CoursePackageError):
        _validate_manifest(manifest)


def test_optional_leader_and_unknown_keys(manifest):
    manifest.pop('leader_user_id', None)
    assert _validate_manifest(manifest).leader_user_id is None
    assert _validate_manifest({**manifest, 'leader_user_id': None}).leader_user_id is None
    assert _validate_manifest({**manifest, 'leader_user_id': 42}).leader_user_id == 42
    with pytest.raises(CoursePackageError):
        _validate_manifest({**manifest, 'leader_id': 42})


def test_deployment_override_and_snapshot_are_pinned(monkeypatch):
    # Unrelated work in the authoring checkout can leave its digest stale.
    monkeypatch.setattr(sys, 'dont_write_bytecode', True)
    monkeypatch.setattr('pymentor_core.adapters.course_local.runtime.content_version',
                        lambda root, manifest: manifest.content_version)
    monkeypatch.setenv('KNOWI_COURSE_LEADER_IDS_JSON', '{"backend": 42, "backend_copy": null}')
    runtime = CourseRuntime(COURSES)
    monkeypatch.setenv('KNOWI_COURSE_LEADER_IDS_JSON', '{"backend": 43, "backend_copy": null}')
    for course, leader in [('backend', 42), ('backend_copy', None)]:
        snapshot = runtime.snapshot(course)
        assert runtime.leader_user_ids[course] == leader
        assert snapshot.manifest.leader_user_id == leader
        assert snapshot.manifest.metadata['leader_user_id'] == leader
        assert runtime.snapshot(course) is snapshot
    assert CourseRuntime(COURSES).leader_user_ids['backend'] == 43


def test_absent_override_uses_manifests(monkeypatch):
    monkeypatch.delenv('KNOWI_COURSE_LEADER_IDS_JSON', raising=False)
    runtime = CourseRuntime(COURSES)
    for course, leader in runtime.leader_user_ids.items():
        data = json.loads((COURSES / course / 'course.yaml').read_text(encoding='utf-8'))
        assert leader == data.get('leader_user_id')


@pytest.mark.parametrize('raw', [
    '', '{', 'null', '[]', '{}',
    '{"backend": 42}',
    '{"backend": 42, "backend_copy": null, "unknown": null}',
    '{"backend": 42, "backend": 43, "backend_copy": null}',
    '{"backend": 42, "backend_copy": 42}',
    '{"backend": true, "backend_copy": null}',
    '{"backend": 0, "backend_copy": null}',
    '{"backend": -1, "backend_copy": null}',
    '{"backend": 1.0, "backend_copy": null}',
    '{"backend": "1", "backend_copy": null}',
    '{"backend": [], "backend_copy": null}',
])
def test_invalid_deployment_override_fails_closed(monkeypatch, raw):
    monkeypatch.setenv('KNOWI_COURSE_LEADER_IDS_JSON', raw)
    with pytest.raises(CoursePackageError):
        CourseRuntime(COURSES)
