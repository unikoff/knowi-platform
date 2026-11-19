"""Real ASGI + signed verified grant with fake report storage (no PostgreSQL)."""
import asyncio
import time
from uuid import UUID
import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from pymentor_analytics.adapters.auth.verifier import GrantVerifier
from pymentor_analytics.contracts.auth_v1 import AuthorizationError
from pymentor_analytics.modules.analytics.api.activity_router import router
from pymentor_analytics.modules.analytics.reports.activity_scope import resolve_scope

USER=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OTHER=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
PATH="/api/analytics/reports/activity"
PARAMS={"from_time":"2026-01-01T00:00:00Z","to_time":"2026-01-02T00:00:00Z","course_id":"backend"}
class Probe:
    async def is_ready(self): return True
class Settings: db_timeout_seconds=2
class Store:
    async def read_activity(self,grant,query,*,include_breakdown=False):
        scope=resolve_scope(grant,query)
        out={"as_of":None,"quality":"unknown","lag_seconds":None,"course_id":scope.course_id,
                "totals":{"unique_visitors":0,"active_ms":0,"theory_ms":0,"practice_ms":0},"daily":[]}
        if include_breakdown:
            out["breakdown"]=[{"level":"course","course_id":"backend","lesson_id":None,"unique_visitors":0,"active_ms":0,"theory_ms":0,"practice_ms":0},
                              {"level":"lesson","course_id":"backend","lesson_id":"lesson-a","unique_visitors":0,"active_ms":0,"theory_ms":0,"practice_ms":0}]
        return out
def make_app(role="user", subject=USER, roster=(), courses=("backend",), global_read=False, *, scope="report:read"):
    key=Ed25519PrivateKey.generate()
    pub=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo)
    verifier=GrantVerifier(issuer="core",public_keys={"test":pub})
    now=int(time.time())
    claims={"grant_version":1,"iss":"core","aud":"analytics","sub":str(subject),"iat":now,"nbf":now,
            "exp":now+50,"role":role,"scope":scope,"course_ids":list(courses),
            "subject_refs":[str(x) for x in roster],"global_read":global_read}
    token=jwt.encode(claims,key,algorithm="EdDSA",headers={"typ":"JWT","kid":"test"})
    app=FastAPI(); app.include_router(router); app.state.trust_manifest_validated=True
    app.state.grant_verifier=verifier; app.state.probe=Probe(); app.state.settings=Settings(); app.state.activity_reports=Store()
    return app,token
async def request(app,token,params=PARAMS):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        return await client.get(PATH,params=params,headers={"Authorization":f"Bearer {token}"})
def test_user_wire_unknown_not_nodata_and_scope():
    app,token=make_app()
    result=asyncio.run(request(app,token))
    assert result.status_code==200
    data=result.json()
    assert data["status"]=="ok" and data["quality"]=="unknown" and data["as_of"] is None
    assert data["totals"]["unique_visitors"]==0 and data["lag_seconds"] is None
    denied=asyncio.run(request(app,token,{**PARAMS,"subject_ref":str(OTHER)}))
    assert denied.status_code==403
    assert asyncio.run(request(app,"wrong")).status_code==401
    assert asyncio.run(request(app,token,{**PARAMS,"role":"admin"})).status_code==400
    assert asyncio.run(request(app,token,{**PARAMS,"to_time":"2026-04-02T00:00:00Z"})).status_code==400
    assert asyncio.run(request(app,token,{**PARAMS,"from_time":"2026-01-01T03:00:00+03:00"})).status_code==400

def test_leader_roster_and_admin_signed_grant():
    leader,token=make_app(role="leader",subject=OTHER,roster=(USER,),courses=("backend",))
    assert asyncio.run(request(leader,token,{**PARAMS,"subject_ref":str(USER)})).status_code==200
    assert asyncio.run(request(leader,token,{**PARAMS,"subject_ref":str(OTHER)})).status_code==403
    admin,token=make_app(role="admin",subject=OTHER,courses=(),global_read=True)
    assert asyncio.run(request(admin,token,{**PARAMS,"subject_ref":str(USER)})).status_code==200

def test_router_default_closed_and_store_failure():
    app,token=make_app(); del app.state.activity_reports
    assert asyncio.run(request(app,token)).status_code==503
    app,token=make_app(); app.state.trust_manifest_validated=False
    assert asyncio.run(request(app,token)).status_code==503
    class Failing:
        async def read_activity(self,*args): raise TimeoutError()
    app,token=make_app(); app.state.activity_reports=Failing()
    assert asyncio.run(request(app,token)).status_code==503

def test_details_signed_scope_pagination_and_unavailable_progress():
    app,token=make_app()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
            headers={"Authorization":f"Bearer {token}"}
            first=await client.get(PATH+"/details",params={**PARAMS,"limit":1},headers=headers)
            second=await client.get(PATH+"/details",params={**PARAMS,"limit":1,"offset":1},headers=headers)
            denied=await client.get(PATH+"/details",params={**PARAMS,"subject_ref":str(OTHER)},headers=headers)
            no_course=await client.get(PATH,params={"from_time":PARAMS["from_time"],"to_time":PARAMS["to_time"],"lesson_id":"lesson-a"},headers=headers)
            invalid=await client.get(PATH+"/details",params={**PARAMS,"limit":101},headers=headers)
            return first,second,denied,no_course,invalid
    first,second,denied,no_course,invalid=asyncio.run(run())
    assert first.status_code==200 and first.json()["next_offset"]==1
    assert second.json()["next_offset"] is None and second.json()["rows"][0]["lesson_id"]=="lesson-a"
    assert first.json()["report"]["trusted_progress"]=={"state":"unavailable","completed_lessons":None}
    assert denied.status_code==403 and no_course.status_code==400 and invalid.status_code==422

def test_query_factory_openapi_keeps_existing_and_adds_details():
    from pymentor_analytics.app.config import Settings
    from pymentor_analytics.app.factory import create_app
    app=create_app("query",Settings(database_url="postgresql://analytics_user@localhost:57392/analytics_test"))
    spec=app.openapi()
    original=spec["paths"][PATH]["get"]
    details=spec["paths"][PATH+"/details"]["get"]
    assert original["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/ActivityReport")
    assert details["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/ActivityDetailsReport")
    assert {p["name"] for p in details["parameters"]} >= {"lesson_id","offset","limit"}
    assert details["security"]==[{"AnalyticsGrant":[]}]
    assert "AnalyticsGrant" in spec["components"]["securitySchemes"]
