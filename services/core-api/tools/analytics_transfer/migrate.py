"""Install a versioned addon into an explicitly identified fresh synthetic DB."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from run import _create_engine, _synthetic_database
from pymentor_core.adapters.sql.analytics_delivery_ops.addon_migration import migrate


def main():
    parser = argparse.ArgumentParser(description="Explicit synthetic Analytics addon migration")
    parser.add_argument("--expected-db", required=True)
    parser.add_argument("--expected-owner", required=True)
    parser.add_argument("--target-version", type=int, choices=(1, 2), required=True)
    args = parser.parse_args()
    parsed = _synthetic_database(os.environ["DATABASE_URL"], args.expected_db)
    engine = _create_engine(parsed)
    try:
        with engine.begin() as connection:
            version = migrate(connection, expected_db=args.expected_db, expected_owner=args.expected_owner,
                              sentinel=os.environ["PYMENTOR_ANALYTICS_INSTALL_SENTINEL"], target_version=args.target_version)
        print({"addon_version": version})
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Synthetic addon migration failed; check target identity and schema") from None
