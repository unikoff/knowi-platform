import json
from types import SimpleNamespace

import pytest

from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app
from pymentor_analytics.adapters.privacy_authority.client import AuthorityConfig
from pymentor_analytics.adapters.core_privacy.client import SourceConfig


@pytest.fixture
def production(monkeypatch):
    monkeypatch.setattr(AuthorityConfig, "from_json", lambda _: SimpleNamespace(runtime_mode="production"))
    monkeypatch.setattr(SourceConfig, "from_json", lambda _: SimpleNamespace(runtime_mode="production"))
    return dict(database_url="postgresql://analytics_prod_collector@db:5432/analytics_prod",
                issuer_mode="production", trust_manifest_json="{}", privacy_authority_json="{}",
                core_deletion_source_json="{}", core_deletion_status_database_url=
                "postgresql://analytics_prod_transfer_status@db:5432/analytics_prod",
                catalog_enabled=True, catalog_namespace="core",
                core_transfer_namespaces_json=json.dumps({"core": "core"}))


def test_production_browser_route_is_opt_in_and_requires_catalog(production):
    off = create_app("collector", Settings(**production))
    assert "/api/analytics/events/batch" not in off.openapi()["paths"]
    on = create_app("collector", Settings(**production, real_collection=True))
    assert "/api/analytics/events/batch" in on.openapi()["paths"]
    with pytest.raises(ValueError):
        Settings(**{**production, "catalog_enabled": False}, real_collection=True)
    with pytest.raises(ValueError):
        create_app("collector", Settings(**production, real_collection=True), intake=object())
    with pytest.raises(ValueError):
        create_app("query", Settings(**production, real_collection=True))
