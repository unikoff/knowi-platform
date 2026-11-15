"""Additional real PG acceptance for admin mutations and account deletion."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "dev"))

import httpx
from sqlalchemy import event, func, select, update

from core_async_audit import seed
from pymentor_core.app.asgi import create_app
from pymentor_core.app.database import engine
from pymentor_core.adapters.sql.models import User, UserLessonProgress, UserTaskProgress


def test_admin_mutations_deletion_and_aliases_use_asyncpg_and_preserve_other_users():
    admin_email, admin_password = seed()
    student_email, student_password = seed()
    sync_sql = []

    def observed(*_):
        sync_sql.append(True)

    event.listen(engine, "before_cursor_execute", observed)

    async def exercise():
        app = create_app()
        async with app.router.lifespan_context(app):
            database = app.state.local_async_identity.database
            sync_sql.clear()  # Only business requests are observed after startup.
            async with database.unit_of_work() as uow:
                await uow.session.execute(update(User).where(User.email == admin_email).values(is_admin=True))
                await uow.commit()
            transport = httpx.ASGITransport(app=app, client=(uuid4().hex, 1))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as admin, httpx.AsyncClient(transport=transport, base_url="http://test") as student:
                async def login(client, email, password):
                    result = await client.post("/api/user/login", json={"email": email, "password": password}, headers={"Idempotency-Key": str(uuid4())})
                    assert result.status_code == 200
                    return result.json()["user"]["id"]

                admin_id = await login(admin, admin_email, admin_password)
                student_id = await login(student, student_email, student_password)
                # Real SQL history that must be removed only for the target account.
                async with database.unit_of_work() as uow:
                    uow.session.add(UserLessonProgress(user_id=student_id, lesson_id="mc196-historical-lesson"))
                    uow.session.add(UserTaskProgress(user_id=student_id, task_id="mc196-historical-task", task_revision="a" * 64))
                    uow.session.add(UserLessonProgress(user_id=student_id, lesson_id="mc196-historical-lesson", course_id="backend_copy"))
                    uow.session.add(UserTaskProgress(user_id=student_id, task_id="mc196-historical-task", task_revision="a" * 64, course_id="backend_copy"))
                    await uow.commit()
                month = {"year": 2026, "month": 11}
                for prefix in ("/admin", "/api/admin"):
                    assert (await student.get(prefix + "/overview")).status_code == 403
                    assert (await student.get(prefix + f"/users/{student_id}/profile", params=month)).status_code == 403
                    assert (await admin.get(prefix + "/overview")).status_code == 200
                    assert (await admin.get(prefix + "/users")).status_code == 200
                    assert (await admin.get(prefix + "/slots", params=month)).status_code == 200
                    assert (await admin.get(prefix + "/landings")).status_code == 200
                profile = await admin.get(f"/api/admin/users/{student_id}/profile", params=month)
                assert profile.status_code == 200
                account_ref = profile.json()["profile"]["account_ref"]
                assert account_ref
                changed = await admin.patch(f"/api/admin/users/{student_id}/subscription", json={"days": None})
                assert changed.status_code == 200
                assert (await student.get("/api/user/me")).json()["user"]["has_active_subscription"] is False
                assert (await admin.patch(f"/admin/users/{student_id}/subscription", json={"days": 2})).status_code == 200
                assert (await student.get("/user/me")).json()["user"]["has_active_subscription"] is True
                assert (await student.delete(f"/api/admin/users/{admin_id}")).status_code == 403
                assert (await admin.delete(f"/api/admin/users/{admin_id}")).status_code == 400
                stale = await admin.delete(f"/api/admin/users/{student_id}", headers={"X-Account-Ref": str(uuid4())})
                assert stale.status_code == 409
                assert (await student.get("/api/user/me")).status_code == 200
                deleted = await admin.delete(f"/api/admin/users/{student_id}", headers={"X-Account-Ref": account_ref})
                assert deleted.status_code == 200, deleted.text
                assert deleted.json() == {"deleted": student_id}
                assert (await student.get("/api/user/me")).status_code == 401
                assert (await admin.get("/api/user/me")).json()["user"]["id"] == admin_id
                async with database.unit_of_work() as uow:
                    assert await uow.session.get(User, student_id) is None
                    assert await uow.session.get(User, admin_id) is not None
                    for model in (UserLessonProgress, UserTaskProgress):
                        count = await uow.session.scalar(select(func.count()).select_from(model).where(model.user_id == student_id))
                        assert count == 0
                assert database.engine.pool.checkedout() == 0
                assert sync_sql == []

    try:
        asyncio.run(exercise())
    finally:
        event.remove(engine, "before_cursor_execute", observed)
