import pytest
from pydantic import ValidationError

from pymentor_analytics.app.config import Settings, load_settings
from pymentor_analytics.app.factory import create_app

URL = "postgresql://analytics_user@localhost:57392/analytics_test"


def test_processor_configuration_is_bounded_and_role_specific():
    for field, value in (("processor_max_inflight", 3), ("processor_poll_min_seconds", 0),
                         ("processor_poll_max_seconds", float("inf")),
                         ("processor_activity_poll_seconds", 0.049),
                         ("processor_activity_poll_seconds", 0.501),
                         ("processor_activity_poll_seconds", float("nan")),
                         ("processor_activity_poll_seconds", float("inf")),
                         ("processor_job_timeout_seconds", 61),
                         ("processor_consumers", "synthetic.activity,synthetic.activity"),
                         ("processor_consumers", "synthetic.unknown")):
        with pytest.raises(ValidationError):
            Settings(database_url=URL, **{field: value})
    with pytest.raises(ValidationError):
        Settings(database_url=URL, processor_poll_min_seconds=4, processor_poll_max_seconds=1)
    config = load_settings("processor", {"ANALYTICS_PROCESSOR_DATABASE_URL": URL,
                                       "ANALYTICS_PROCESSOR_CONSUMERS": "synthetic.independent"})
    assert config.processor_consumers == "synthetic.independent"
    assert config.processor_activity_poll_seconds == 0.5
    for value in ("0.05", "0.1", "0.5"):
        config = load_settings("processor", {"ANALYTICS_PROCESSOR_DATABASE_URL": URL,
            "ANALYTICS_PROCESSOR_ACTIVITY_POLL_SECONDS": value})
        assert config.processor_activity_poll_seconds == float(value)
    with pytest.raises(ValueError):
        load_settings("collector", {"ANALYTICS_COLLECTOR_DATABASE_URL": URL,
                                    "ANALYTICS_COLLECTOR_PROCESSOR_MAX_INFLIGHT": "1"})
    for values in ({"pool_size": 3}, {"pool_overflow": 1}):
        with pytest.raises(ValueError, match="processor pool"):
            create_app("processor", Settings(database_url=URL, **values))
