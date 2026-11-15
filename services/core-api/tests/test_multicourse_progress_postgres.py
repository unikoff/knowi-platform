"""Real PG16 course/user isolation, duplicates, revisions and legacy defaults."""
import asyncio
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tools'/'dev'))
from core_async_audit import seed
from sqlalchemy import select, func, text
from pymentor_core.app.asgi import create_app
from pymentor_core.adapters.sql import progress
from pymentor_core.adapters.sql.models import User, UserLessonProgress, UserTaskProgress
from pymentor_core.adapters.sql.unit_of_work import SqlAlchemyUnitOfWork


def test_two_users_courses_races_revision_and_old_writer_default():
    emails = [seed()[0] for _ in range(2)]
    async def exercise():
        app = create_app()
        async with app.router.lifespan_context(app):
            db = app.state.local_async_identity.database
            async with db.unit_of_work() as uow:
                ids = list((await uow.session.scalars(select(User.id).where(User.email.in_(emails)).order_by(User.id))).all())
            lesson_id='mc203-'+uuid4().hex[:20]
            task_id=lesson_id+'-task'
            async def write(uid, course):
                async with db.unit_of_work() as uow:
                    def work(session):
                        adapter = SqlAlchemyUnitOfWork(session)
                        progress.mark_task_completed(uid,task_id,'revision-1',session,uow=adapter,course_id=course)
                        progress.mark_lesson_completed(uid,lesson_id,session,uow=adapter,course_id=course)
                    await uow.session.run_sync(work)
            await asyncio.gather(*(write(ids[0],course) for course in ('backend','backend_copy') for _ in range(4)))
            await write(ids[1],'backend_copy')
            async with db.unit_of_work() as uow:
                async def read(uid,course):
                    return await uow.session.run_sync(lambda session:(progress.get_completed_lesson_ids(uid,session,course_id=course),progress.get_completed_task_revisions(uid,[task_id],session,course_id=course)))
                for course in ('backend','backend_copy'):
                    assert await read(ids[0],course) == ({lesson_id},{task_id:'revision-1'})
                assert await read(ids[1],'backend') == (set(),{})
                assert await read(ids[1],'backend_copy') == ({lesson_id},{task_id:'revision-1'})
                for model in (UserLessonProgress,UserTaskProgress):
                    assert await uow.session.scalar(select(func.count()).select_from(model).where(model.user_id.in_(ids))) == 3
                original_time=await uow.session.scalar(select(UserTaskProgress.completed_at).where(UserTaskProgress.user_id==ids[0],UserTaskProgress.course_id=='backend_copy'))
            await write(ids[0],'backend_copy')
            async with db.unit_of_work() as uow:
                row=await uow.session.scalar(select(UserTaskProgress).where(UserTaskProgress.user_id==ids[0],UserTaskProgress.course_id=='backend_copy'))
                assert row.completed_at == original_time
                await uow.session.run_sync(lambda session:progress.mark_task_completed(ids[0],task_id,'revision-2',session,course_id='backend'))
            async with db.unit_of_work() as uow:
                rows=(await uow.session.execute(select(UserTaskProgress.course_id,UserTaskProgress.task_revision).where(UserTaskProgress.user_id==ids[0]))).all()
                assert dict(rows)=={'backend':'revision-2','backend_copy':'revision-1'}
                # A legacy writer omitting the new column remains backend-only.
                await uow.session.execute(text('INSERT INTO user_lesson_progress(user_id,lesson_id) VALUES (:u,:l)'),{'u':ids[1],'l':lesson_id})
                await uow.commit()
            async with db.unit_of_work() as uow:
                rows=(await uow.session.execute(select(UserLessonProgress.course_id).where(UserLessonProgress.user_id==ids[1]))).scalars().all()
                assert set(rows)=={'backend','backend_copy'}
            assert db.engine.pool.checkedout()==0
    asyncio.run(exercise())
