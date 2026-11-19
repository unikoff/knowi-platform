import base64
import json
from uuid import uuid4
import pytest
from pymentor_analytics.app.config import Settings, local_deletion_config


def sample():
    name = "analytics_live_" + uuid4().hex
    url = f"postgresql://{name}_query@127.0.0.1:57392/{name}"
    status = f"postgresql://{name}_transfer_status@127.0.0.1:57392/{name}"
    source = json.dumps({"url": "http://127.0.0.1:18041/api/internal/analytics/deletions", "instance_id": str(uuid4()),
                         "secret": base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()})
    return url, status, source


def test_local_profile_requires_isolated_loopback_and_required_controls():
    database, status, source = sample()
    with pytest.raises(ValueError): Settings(database_url=database, real_collection=True, issuer_mode="local")
    with pytest.raises(ValueError):
        Settings(database_url="postgresql://analytics_test_query@127.0.0.1:57392/analytics_test", real_collection=True,
                 issuer_mode="local", trust_manifest_json="{}", privacy_authority_json="{}", core_deletion_source_json=source)
    with pytest.raises(ValueError): Settings(database_url=database, real_collection=True, issuer_mode="synthetic")
    params = dict(real_collection=True, issuer_mode="local", trust_manifest_json="{}", core_deletion_source_json=source)
    for authority in ('{"fixture_mode":true,"runtime_mode":"local"}', '{"fixture_mode":false,"runtime_mode":"synthetic"}'):
        with pytest.raises(ValueError): Settings(database_url=database, privacy_authority_json=authority, **params)
    good = Settings(database_url=database, privacy_authority_json='{"fixture_mode":false,"runtime_mode":"local"}',
                    core_deletion_status_database_url=status, activity_projection_enabled=True,
                    activity_projection_run_id=uuid4(), activity_day_query_enabled=True, **params)
    assert local_deletion_config(good, "query") is not None
    bad = good.model_copy(update={"core_deletion_status_database_url": good.database_url})
    with pytest.raises(ValueError): local_deletion_config(bad, "query")
    with pytest.raises(ValueError): local_deletion_config(good, "privacy")
    with pytest.raises(ValueError): Settings(database_url=database, privacy_authority_json='{"fixture_mode":false,"runtime_mode":"local"}',
                                             fixture_catalog_json="{}", **params)


def test_default_remains_synthetic_and_rejects_real_collection_without_local_mode():
    url = "postgresql://analytics_test_query@127.0.0.1:57392/analytics_test"
    assert Settings(database_url=url).issuer_mode == "synthetic"
    with pytest.raises(ValueError): Settings(database_url=url, real_collection=True)


def test_source_configuration_rejects_nonlocal_and_noncanonical_secrets():
    from pymentor_analytics.adapters.core_privacy.client import SourceConfig
    good = {"url": "http://127.0.0.1:18041/api/internal/analytics/deletions", "instance_id": str(uuid4()),
            "secret": base64.urlsafe_b64encode(bytes(range(32))).rstrip(b"=").decode()}
    assert SourceConfig.from_json(json.dumps(good)).instance_id
    for patch in ({"url": "http://127.0.0.1:18042/api/internal/analytics/deletions"},
                  {"url": "http://example.com:18041/api/internal/analytics/deletions"},
                  {"url": good["url"] + "?a=b"}, {"secret": good["secret"] + "="},
                  {"instance_id": good["instance_id"].upper()}):
        with pytest.raises(ValueError): SourceConfig.from_json(json.dumps({**good, **patch}))
    database, _, _ = sample()
    with pytest.raises(ValueError): Settings(database_url=database)


def test_local_processor_requires_generic_worker_disabled():
    database, _, source = sample()
    settings = Settings(database_url=database, real_collection=True, issuer_mode="local", trust_manifest_json="{}",
                        privacy_authority_json='{"fixture_mode":false,"runtime_mode":"local"}',
                        core_deletion_source_json=source)
    with pytest.raises(ValueError): local_deletion_config(settings, "processor")
    assert local_deletion_config(settings.model_copy(update={"processor_generic_worker_enabled": False}), "processor")
