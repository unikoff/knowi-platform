import copy
from uuid import uuid4

import pytest

from .guards import validate
from .resources import core_admin_url


def fixture():
    users = {name: {"id": index, "email": name + "@example.test", "password": "fixture", "subject_ref": str(uuid4())}
             for index, name in enumerate(("admin", "leader", "student", "other"), 1)}
    browser = {name: {"id": index, "email": "browser-" + name + "@example.test", "password": "fixture", "subject_ref": str(uuid4())}
               for name, index in (("admin", 5), ("student", 7))}
    browser["leader"] = dict(users["leader"])
    core = "analytics_delivery_test_" + uuid4().hex
    analytics = "analytics_test_" + uuid4().hex
    return {"core_url": "http://127.0.0.1:18151", "collector_url": "http://127.0.0.1:18152",
            "query_url": "http://127.0.0.1:18153", "privacy_url": "http://127.0.0.1:18154",
            "databases": {"core": {"name": core, "owner": "pymentor_test", "sentinel": "a"*64,
                                   "dsn": f"postgresql://test:fixture@127.0.0.1:57382/{core}"},
                          "analytics": {"name": analytics, "owner": analytics+"_migrate", "sentinel": "b"*64,
                                        "dsn": f"postgresql://test:fixture@127.0.0.1:57392/{analytics}"}},
            "users": users, "browser_users": browser, "course_id": "backend", "catalog_version": "catalog-1", "roster": [users["student"]["subject_ref"], browser["student"]["subject_ref"]]}


def test_accepts_explicit_synthetic_fixture():
    value = fixture()
    assert validate(value) is value


@pytest.mark.parametrize("change", [
    lambda c: c.update(core_url="http://localhost:18151"),
    lambda c: c.update(query_url="http://127.0.0.1:18153/path"),
    lambda c: c["databases"]["core"].update(name="pymentor_test"),
    lambda c: c["databases"]["analytics"].update(owner="postgres"),
    lambda c: c["databases"]["core"].update(sentinel="missing"),
    lambda c: c["users"]["student"].update(email="person@example.com"),
    lambda c: c.update(roster=[]),
])
def test_rejects_non_synthetic_or_incomplete_target(change):
    value = copy.deepcopy(fixture())
    change(value)
    with pytest.raises(ValueError):
        validate(value)


@pytest.mark.parametrize("target", [
    "postgresql://pymentor_test:x@localhost:57382/pymentor_test",
    "postgresql://pymentor_test:x@127.0.0.1:57382/real_users",
    "postgresql://postgres:x@127.0.0.1:57382/pymentor_test",
    "postgresql://pymentor_test:x@127.0.0.1:57392/pymentor_test",
    "postgresql://pymentor_test:x@127.0.0.1:57382/pymentor_test?host=elsewhere",
])
def test_core_admin_allowlist_rejects_other_targets(target):
    with pytest.raises(ValueError):
        core_admin_url(target)
