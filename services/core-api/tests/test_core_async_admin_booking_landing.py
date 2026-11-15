"""Live PostgreSQL request checks for admin, booking, and landing flows."""
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path
from threading import get_ident
from uuid import uuid4
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools' / 'dev'))
import httpx
import pytest
from sqlalchemy import event, func, select, update
from core_async_audit import seed
from pymentor_core.app.asgi import create_app
from pymentor_core.app.database import engine
from pymentor_core.adapters.sql.models import User, BookingSlot, BookingNotificationOutbox, LandingNotificationOutbox


def test_admin_booking_and_landing_idempotency(monkeypatch):
    import pymentor_core.adapters.sql.landings as landing_store
    import pymentor_core.modules.landings.api.admin as landing_admin
    admin_email, admin_pw = seed()
    user_email, user_pw = seed()
    monkeypatch.setattr(landing_store, 'resolve_landing_bot_token', lambda _ref: 'synthetic-token')
    refs = []
    def verify(_token, _chat):
        assert get_ident() != refs[0]
        assert refs[1].engine.pool.checkedout() == 0
        return 'synthetic_bot'
    monkeypatch.setattr(landing_admin, 'verify_telegram_destination', verify)
    seen=[]
    def record(*args): seen.append(1)
    event.listen(engine, 'before_cursor_execute', record)
    async def exercise():
        refs.append(get_ident())
        app=create_app()
        async with app.router.lifespan_context(app):
            db=app.state.local_async_identity.database
            seen.clear()  # Exclude the preserved startup schema precheck.
            refs.append(db)
            async with db.unit_of_work() as uow:
                await uow.session.execute(update(User).where(User.email == admin_email).values(is_admin=True))
                await uow.commit()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=(uuid4().hex,1)),base_url='http://test') as admin, httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=(uuid4().hex,1)),base_url='http://test') as student:
                async def login(client,email,password):
                    return await client.post('/api/user/login',json={'email':email,'password':password},headers={'Idempotency-Key':str(uuid4())})
                assert (await login(admin,admin_email,admin_pw)).status_code == 200
                student_login=await login(student,user_email,user_pw)
                user_id=student_login.json()['user']['id']
                assert (await student.get('/api/admin/overview')).status_code == 403
                slot_date=date(2080,1,1)+timedelta(days=uuid4().int % 3600)
                slot = await admin.post('/api/admin/slots',json={'slot_date':slot_date.isoformat(),'start_time':'12:00','duration_minutes':60})
                assert slot.status_code == 200, slot.text
                slot_id=slot.json()['slot']['id']
                quota=await admin.post(f'/api/admin/users/{user_id}/quota',json={'year':slot_date.year,'month':slot_date.month,'add':1})
                assert quota.status_code == 200, quota.text
                calendar = await student.get('/api/user/slots',params={'year':slot_date.year,'month':slot_date.month})
                assert calendar.status_code == 200
                batch = await asyncio.gather(
                    *(student.post(f'/api/user/slots/{slot_id}/book') for _ in range(3)),
                    admin.get('/api/admin/slots', params={'year':slot_date.year,'month':slot_date.month}),
                    student.get('/api/user/slots',params={'year':slot_date.year,'month':slot_date.month}))
                results=batch[:3]
                assert batch[3].status_code == batch[4].status_code == 200
                assert sum(r.status_code == 200 for r in results) == 1, [r.status_code for r in results]
                async with db.unit_of_work() as uow:
                    stored=await uow.session.get(BookingSlot,slot_id)
                    count=await uow.session.scalar(select(func.count()).select_from(BookingNotificationOutbox).where(BookingNotificationOutbox.slot_id==slot_id,BookingNotificationOutbox.event_type=='booking_created'))
                    assert stored.student_id == user_id and count == 1
                cancelled=await student.post(f'/api/user/slots/{slot_id}/cancel')
                assert cancelled.status_code == 200
                async with db.unit_of_work() as uow:
                    stored=await uow.session.get(BookingSlot,slot_id)
                    count=await uow.session.scalar(select(func.count()).select_from(BookingNotificationOutbox).where(BookingNotificationOutbox.slot_id==slot_id,BookingNotificationOutbox.event_type=='booking_cancelled'))
                    assert stored.student_id is None and count == 1
                slug='mc196-'+uuid4().hex
                made=await admin.post('/api/admin/landings',json={'slug':slug,'title':'Synthetic','description':'','is_active':True})
                assert made.status_code == 201, made.text
                landing_id=made.json()['id']
                configured=await admin.put(f'/api/admin/landings/{landing_id}/telegram',json={'secret_ref':'synthetic','chat_id':'1'})
                assert configured.status_code == 200, configured.text
                key=str(uuid4()); payload={'name':'Synthetic','contact':'synthetic@example.invalid','goal':'test','consent':True,'consent_version':'consultation-v3'}
                first=await student.post(f'/api/landings/{slug}/consultations',json=payload,headers={'Idempotency-Key':key})
                repeat=await student.post(f'/api/landings/{slug}/consultations',json=payload,headers={'Idempotency-Key':key})
                assert (first.status_code,repeat.status_code)==(202,200), (first.text,repeat.text)
                async with db.unit_of_work() as uow:
                    count=await uow.session.scalar(select(func.count()).select_from(LandingNotificationOutbox).where(LandingNotificationOutbox.landing_id==landing_id,LandingNotificationOutbox.event_id==key))
                    assert count == 1
                concurrent_key=str(uuid4())
                results=await asyncio.gather(*(student.post(f'/api/landings/{slug}/consultations',json=payload,headers={'Idempotency-Key':concurrent_key}) for _ in range(3)))
                assert sorted(r.status_code for r in results)==[200,200,202], [r.status_code for r in results]
                conflicting=await student.post(f'/api/landings/{slug}/consultations',json={**payload,'goal':'different'},headers={'Idempotency-Key':concurrent_key})
                assert conflicting.status_code == 409
                async with db.unit_of_work() as uow:
                    count=await uow.session.scalar(select(func.count()).select_from(LandingNotificationOutbox).where(LandingNotificationOutbox.landing_id==landing_id,LandingNotificationOutbox.event_id==concurrent_key))
                    assert count == 1
                # Drop the first response only AFTER the real app committed.
                # Replaying the same operation must reconcile, not enqueue twice.
                lost_key = str(uuid4())
                class DropCommittedResponse(httpx.AsyncBaseTransport):
                    def __init__(self):
                        self.inner = httpx.ASGITransport(app=app, client=(uuid4().hex, 1))
                        self.drop = True
                    async def handle_async_request(self, request):
                        response = await self.inner.handle_async_request(request)
                        if self.drop:
                            self.drop = False
                            await response.aclose()
                            raise httpx.ReadTimeout('synthetic lost reply after commit', request=request)
                        return response
                async with httpx.AsyncClient(transport=DropCommittedResponse(), base_url='http://test') as retrying:
                    with pytest.raises(httpx.ReadTimeout):
                        await retrying.post(f'/api/landings/{slug}/consultations', json=payload, headers={'Idempotency-Key': lost_key})
                    async with db.unit_of_work() as uow:
                        committed = await uow.session.scalar(select(func.count()).select_from(LandingNotificationOutbox).where(LandingNotificationOutbox.landing_id==landing_id, LandingNotificationOutbox.event_id==lost_key))
                        assert committed == 1
                    recovered = await retrying.post(f'/api/landings/{slug}/consultations', json=payload, headers={'Idempotency-Key': lost_key})
                    assert recovered.status_code == 200 and recovered.json()['duplicate'] is True
                    async with db.unit_of_work() as uow:
                        assert await uow.session.scalar(select(func.count()).select_from(LandingNotificationOutbox).where(LandingNotificationOutbox.landing_id==landing_id, LandingNotificationOutbox.event_id==lost_key)) == 1
                assert seen == [] and db.engine.pool.checkedout()==0
    try: asyncio.run(exercise())
    finally: event.remove(engine,'before_cursor_execute',record)


def test_request_db_unavailable_fails_closed_without_sync_fallback(monkeypatch):
    from sqlalchemy.exc import OperationalError
    seen=[]
    def record(*args): seen.append(1)
    event.listen(engine,'before_cursor_execute',record)
    async def exercise():
        app=create_app()
        async with app.router.lifespan_context(app):
            database=app.state.local_async_identity.database
            seen.clear()  # Exclude the preserved startup schema precheck.
            def unavailable():
                raise OperationalError(None, None, RuntimeError('synthetic unavailable'))
            monkeypatch.setattr(database,'unit_of_work',unavailable)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                result=await client.post('/api/landings/nonexistent/consultations',json={'name':'Test','contact':'test@example.invalid','consent':True},headers={'Idempotency-Key':str(uuid4())})
                assert result.status_code==503
            assert seen == []
    try: asyncio.run(exercise())
    finally: event.remove(engine,'before_cursor_execute',record)
