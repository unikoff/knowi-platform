"""Explicit server-only settings reads; parsing never activates a destination."""

from dataclasses import dataclass
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

from .packages import CoursePackageError, CourseRegistry, _parse_document, load_course


MAX_SETTINGS_BYTES = 8192
CONTACT_FIELDS = frozenset({"display_name", "email", "phone", "telegram", "website"})
_CHAT_ID = re.compile(r"^-?[1-9][0-9]{0,18}$")


@dataclass(frozen=True, repr=False)
class PersonalSettings:
    course_id: str
    contacts: Mapping[str, str | None] | None
    telegram_enabled: bool
    chat_id: str | None
    # Topic delivery is reserved, not silently ignored.
    topic_id: None = None


def _keys(value, expected, label):
    if not isinstance(value, dict) or set(value) != expected:
        raise CoursePackageError(f"invalid {label} fields")


def read_personal_settings(registry: CourseRegistry, course_id: str) -> PersonalSettings:
    """Caller supplies trusted registry/course identity, never an arbitrary path."""
    manifest = load_course(registry, course_id)
    root = (registry.root / manifest.course_id).resolve(strict=True)
    path = root / "personal.settings.yaml"
    try:
        resolved = path.resolve(strict=True)
        if root not in resolved.parents or not resolved.is_file() or resolved.stat().st_size > MAX_SETTINGS_BYTES:
            raise CoursePackageError("settings file is missing, oversized or outside course root")
        try:
            data = _parse_document(resolved, max_bytes=MAX_SETTINGS_BYTES)
        except CoursePackageError:
            # Private settings must not expose YAML excerpts through exception chains.
            raise CoursePackageError("invalid personal settings YAML/JSON") from None
    except OSError as exc:
        raise CoursePackageError("settings file is unavailable") from exc
    _keys(data, {"schema_version", "course_id", "contacts", "telegram"}, "personal settings")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise CoursePackageError("unsupported personal settings schema_version")
    if data["course_id"] != manifest.course_id:
        raise CoursePackageError("settings course_id does not match registered package")
    contacts = data["contacts"]
    if contacts is not None:
        if not isinstance(contacts, dict) or not set(contacts).issubset(CONTACT_FIELDS):
            raise CoursePackageError("unsupported contact fields")
        if any(value is not None and (not isinstance(value, str) or len(value) > 512 or any(ord(char) < 32 for char in value)) for value in contacts.values()):
            raise CoursePackageError("contacts must be bounded text or null")
        contacts = MappingProxyType(contacts.copy())
    telegram = data["telegram"]
    _keys(telegram, {"enabled", "chat_id", "topic_id"}, "telegram settings")
    if type(telegram["enabled"]) is not bool:
        raise CoursePackageError("telegram.enabled must be boolean")
    if telegram["topic_id"] is not None:
        raise CoursePackageError("telegram.topic_id is reserved and is not supported")
    chat_id = telegram["chat_id"]
    if chat_id is not None:
        if type(chat_id) is int:
            chat_id = str(chat_id)
        if not isinstance(chat_id, str) or not _CHAT_ID.fullmatch(chat_id) or not -(2**63) <= int(chat_id) < 2**63:
            raise CoursePackageError("telegram.chat_id must be a nonzero signed numeric chat ID")
    if telegram["enabled"] and chat_id is None:
        raise CoursePackageError("enabled telegram settings require a chat_id")
    return PersonalSettings(manifest.course_id, contacts, telegram["enabled"], chat_id)
