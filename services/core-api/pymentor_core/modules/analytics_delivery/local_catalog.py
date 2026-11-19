"""Compatibility entry point for publishing to a bound local Analytics dataset."""

from pymentor_core.app.analytics_catalog import main


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit("Local catalog publication unavailable") from None
