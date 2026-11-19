from uuid import uuid4
import pytest
from pymentor_analytics.app.config import Settings, activity_projection_config, load_settings

URL = "postgresql://analytics_test_processor@127.0.0.1:57392/analytics_test"


def test_defaults_and_processor_environment_names():
    default = Settings(database_url=URL)
    assert not default.activity_day_worker_enabled and not default.activity_day_query_enabled
    assert not default.activity_course_worker_enabled
    assert default.processor_bucket_worker_enabled and default.processor_generic_worker_enabled
    parsed = load_settings("processor", {"ANALYTICS_PROCESSOR_DATABASE_URL": URL,
        "ANALYTICS_PROCESSOR_BUCKET_WORKER_ENABLED": "false",
        "ANALYTICS_PROCESSOR_GENERIC_WORKER_ENABLED": "false",
        "ANALYTICS_PROCESSOR_ACTIVITY_DAY_WORKER_ENABLED": "true"})
    assert not parsed.processor_bucket_worker_enabled and not parsed.processor_generic_worker_enabled
    assert parsed.activity_day_worker_enabled
    with pytest.raises(ValueError): activity_projection_config(parsed, "processor")


def test_course_worker_requires_processor_day_worker():
    parsed = load_settings("processor", {"ANALYTICS_PROCESSOR_DATABASE_URL": URL,
        "ANALYTICS_PROCESSOR_ACTIVITY_COURSE_WORKER_ENABLED": "true"})
    assert parsed.activity_course_worker_enabled
    with pytest.raises(ValueError): activity_projection_config(parsed, "processor")
    with pytest.raises(ValueError): activity_projection_config(parsed, "query")


def test_boundary_credit_is_opt_in_and_requires_course_worker():
    run = uuid4()
    settings = Settings(database_url=URL, activity_projection_enabled=True, activity_projection_run_id=run,
                        trust_manifest_json="{}", privacy_authority_json="{}", activity_day_worker_enabled=True)
    assert not settings.activity_boundary_credit_enabled
    credit = settings.model_copy(update={"activity_boundary_credit_enabled": True})
    with pytest.raises(ValueError):
        activity_projection_config(credit, "processor")
    credit = credit.model_copy(update={"activity_course_worker_enabled": True})
    assert activity_projection_config(credit, "processor") == run
    with pytest.raises(ValueError):
        activity_projection_config(credit, "query")


@pytest.mark.parametrize("role,field", [("query", "activity_day_worker_enabled"),
                                         ("processor", "activity_day_query_enabled"),
                                         ("privacy", "activity_day_query_enabled")])
def test_role_specific_flags_fail_closed(role, field):
    settings = Settings(database_url=URL, activity_projection_enabled=True, activity_projection_run_id=uuid4(),
                        trust_manifest_json="{}", privacy_authority_json="{}", **{field: True})
    with pytest.raises(ValueError): activity_projection_config(settings, role)
