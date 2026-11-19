"""The optional leader ID is explicit, strictly typed and never inferred."""
import json
from pathlib import Path

import pytest

from pymentor_core.adapters.course_local.packages import CoursePackageError, _validate_manifest


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
