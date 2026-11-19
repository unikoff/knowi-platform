"""Explicit process selection; migration is never run by HTTP replicas."""
import argparse
import sys
import re
import ipaddress

from pydantic import ValidationError
import uvicorn

from .config import ROLES, load_settings
from .factory import create_app


def main() -> int:
    parser = argparse.ArgumentParser(prog="analytics")
    parser.add_argument("role", choices=ROLES)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--revision", default="head")
    args = parser.parse_args()
    if (args.role != "migrate" and args.revision != "head") or not re.fullmatch(r"head|a[0-9]{3}", args.revision):
        parser.error("--revision must identify an Analytics migration revision")
    try:
        settings = load_settings(args.role)
    except ValidationError:
        print(f"Invalid {args.role} configuration; check role-specific settings.", file=sys.stderr)
        return 2
    if settings.real_collection and args.role != "migrate":
        try:
            if "%" in args.host or not ipaddress.ip_address(args.host).is_loopback:
                raise ValueError
        except ValueError:
            print("Local Analytics requires a numeric loopback host.", file=sys.stderr)
            return 2
    if args.role == "migrate":
        from pymentor_analytics.adapters.postgres.migrate import upgrade
        try:
            upgrade(settings, target_revision=args.revision)
        except Exception:
            print("Analytics migration failed; inspect the database and retry safely.", file=sys.stderr)
            return 2
        return 0
    uvicorn.run(create_app(args.role, settings), host=args.host, port=args.port,
                timeout_graceful_shutdown=settings.shutdown_timeout_seconds, access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
