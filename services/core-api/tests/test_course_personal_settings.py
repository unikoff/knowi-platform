import json
from pathlib import Path
import shutil
import uuid

import pytest

from pymentor_core.adapters.course_local.packages import CoursePackageError, load_registry
from pymentor_core.adapters.course_local.personal_settings import read_personal_settings


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def settings_root():
    parent = Path(__file__).resolve().parent
    root = parent / (".personal-settings-" + uuid.uuid4().hex)
    root.mkdir()
    try:
        examples = ROOT / "services/core-api/tests/fixtures/contracts/multicourse/MC1.4/examples"
        shutil.copyfile(examples / "registry.json", root / "registry.json")
        for course in ("backend", "backend_copy"):
            (root / course).mkdir()
            shutil.copyfile(examples / f"{course}.course.json", root / course / "course.yaml")
            shutil.copyfile(ROOT / "courses" / course / "personal.settings.yaml", root / course / "personal.settings.yaml")
        yield root
    finally:
        resolved = root.resolve(strict=True)
        assert resolved.parent == parent and resolved.name.startswith(".personal-settings-")
        shutil.rmtree(resolved)


def document():
    return {"schema_version": 1, "course_id": "backend", "contacts": None, "telegram": {"enabled": False, "chat_id": None, "topic_id": None}}


def write(root, data):
    (root / "backend/personal.settings.yaml").write_text(json.dumps(data), encoding="utf-8")


def test_two_real_templates_are_inactive_and_do_not_contain_recipient_or_contacts():
    registry = load_registry(ROOT / "courses")
    for course in ("backend", "backend_copy"):
        settings = read_personal_settings(registry, course)
        assert settings.course_id == course
        assert settings.contacts is None and settings.chat_id is None
        assert settings.telegram_enabled is False


@pytest.mark.parametrize("field,value", [("schema_version", 2), ("schema_version", True), ("course_id", "backend_copy"), ("contacts", {"bot_token": "should-not-be-logged"}), ("contacts", {"phone": ["invalid"]})])
def test_invalid_top_level_settings_fail_without_disclosing_values(settings_root, field, value):
    data = document()
    data[field] = value
    write(settings_root, data)
    with pytest.raises(CoursePackageError) as caught:
        read_personal_settings(load_registry(settings_root), "backend")
    assert "should-not-be-logged" not in str(caught.value)


@pytest.mark.parametrize("telegram", [
    {"enabled": True, "chat_id": None, "topic_id": None},
    {"enabled": "false", "chat_id": None, "topic_id": None},
    {"enabled": True, "chat_id": 0, "topic_id": None},
    {"enabled": True, "chat_id": True, "topic_id": None},
    {"enabled": True, "chat_id": "../other-course", "topic_id": None},
    {"enabled": True, "chat_id": str(2**63), "topic_id": None},
    {"enabled": False, "chat_id": None, "topic_id": 1},
])
def test_invalid_or_unsupported_destination_fails_before_any_sync(settings_root, telegram):
    data = document()
    data["telegram"] = telegram
    write(settings_root, data)
    with pytest.raises(CoursePackageError):
        read_personal_settings(load_registry(settings_root), "backend")


def test_parsing_a_synthetic_enabled_destination_does_not_activate_it(settings_root):
    data = document()
    data["telegram"].update(enabled=True, chat_id=-123456789)
    data["contacts"] = {"display_name": "Local fixture"}
    write(settings_root, data)
    parsed = read_personal_settings(load_registry(settings_root), "backend")
    assert parsed.chat_id == "-123456789" and parsed.telegram_enabled
    with pytest.raises(TypeError):
        parsed.contacts["display_name"] = "changed"
    assert "123456789" not in repr(parsed)


@pytest.mark.parametrize("content", ["!!python/object:os.system {}", "a: &alias [1]\nb: *alias", "#" * 8193])
def test_unsafe_or_oversized_yaml_is_rejected(settings_root, content):
    (settings_root / "backend/personal.settings.yaml").write_text(content, encoding="utf-8")
    with pytest.raises(CoursePackageError):
        read_personal_settings(load_registry(settings_root), "backend")


def test_course_path_and_personal_file_escape_are_rejected(settings_root):
    registry = load_registry(settings_root)
    with pytest.raises(CoursePackageError):
        read_personal_settings(registry, "../backend_copy")
    path = settings_root / "backend/personal.settings.yaml"
    path.unlink()
    with pytest.raises(CoursePackageError):
        read_personal_settings(registry, "backend")
    try:
        path.symlink_to(settings_root / "backend_copy/personal.settings.yaml")
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(CoursePackageError, match="outside course root"):
        read_personal_settings(registry, "backend")
