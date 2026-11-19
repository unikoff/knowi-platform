from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import pytest
from sqlalchemy.pool import NullPool

path = Path(__file__).parents[2] / "tools/analytics_transfer/run.py"
spec = spec_from_file_location("synthetic_transfer_tool", path)
tool = module_from_spec(spec)
spec.loader.exec_module(tool)


def test_only_named_local_synthetic_database_is_accepted():
    expected = "analytics_delivery_test_isolated"
    assert tool._synthetic_database(f"postgresql+psycopg://synthetic@127.0.0.1/{expected}", expected).database == expected
    with pytest.raises(ValueError):
        tool._synthetic_database("postgresql+psycopg://synthetic@127.0.0.1/retained_user_data", expected)
    with pytest.raises(ValueError):
        tool._synthetic_database(f"postgresql+psycopg://synthetic@external.example/{expected}", expected)


def test_oneshot_engine_does_not_allocate_a_queue_pool():
    expected = "analytics_delivery_test_isolated"
    parsed = tool._synthetic_database(f"postgresql+psycopg://synthetic@127.0.0.1/{expected}", expected)
    engine = tool._create_engine(parsed)
    try:
        assert isinstance(engine.pool, NullPool)
    finally:
        engine.dispose()
