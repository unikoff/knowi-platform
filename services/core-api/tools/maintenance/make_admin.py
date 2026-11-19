"""Preview an administrator permission change against an explicitly selected database."""

import argparse
import os
from pymentor_core.app.settings import postgres_database_url
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("email")
    parser.add_argument("--revoke", action="store_true")
    parser.add_argument("--database-url", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    selected_url = postgres_database_url(args.database_url).set(drivername='postgresql+psycopg')
    # The SQL model module imports the application's engine.  Bind that module
    # to the explicitly selected CLI target, not a hidden inherited default.
    os.environ['DATABASE_URL'] = selected_url.render_as_string(hide_password=False)
    from pymentor_core.adapters.sql import models
    engine = create_engine(selected_url, hide_parameters=True)
    try:
        with sessionmaker(bind=engine)() as db:
            from pymentor_core.adapters.sql.role_admin_cutover import lock_mode
            from tools.maintenance.course_leader_config import LEGACY_ADMINS_MARKER
            from fastapi import HTTPException
            try:
                lock_mode(db)
            except HTTPException as exc:
                parser.exit(2, str(exc.detail) + '\n')
            db.execute(text('LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE'))
            if not args.revoke and db.execute(text('SELECT 1 FROM role_admin_bootstrap_markers WHERE marker=:marker'),
                                              {'marker': LEGACY_ADMINS_MARKER}).scalar_one_or_none() is None:
                parser.exit(2, 'Complete the legacy administrator migration before granting new administrator access.\n')
            user = db.query(models.User).filter(models.User.email == args.email).first()
            if user is None:
                parser.exit(1, "Account not found.\n")
            if not args.revoke and db.execute(text('SELECT 1 FROM course_leadership WHERE user_id=:id AND revoked_at IS NULL'),
                                              {'id': user.id}).first() is not None:
                parser.exit(2, 'A course leader cannot become an administrator while the assignment is active.\n')
            if args.apply:
                if args.revoke and user.is_admin and not db.query(models.User).filter(models.User.is_admin.is_(True), models.User.id != user.id).count():
                    parser.exit(1, "Cannot remove the last platform administrator.\n")
                user.is_admin = not args.revoke
                db.commit()
            else:
                db.rollback()
            print(
                ("Applied: " if args.apply else "Preview: ")
                + "administrator access "
                + ("revoked." if args.revoke else "granted.")
            )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
