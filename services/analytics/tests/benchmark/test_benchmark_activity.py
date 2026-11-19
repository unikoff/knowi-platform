"""Offline checks only. No sockets, processes, files or databases are opened."""
import asyncio
import ctypes
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"tools"))
spec=importlib.util.spec_from_file_location("benchmark_activity",ROOT/"tools/benchmark_activity.py")
b=importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
PROFILE=b.profile_load(ROOT/"bench/profile-v1.json")
RUN=UUID("11111111-1111-4111-8111-111111111111")

@pytest.mark.parametrize("stage,active,overlap,streams",[(10,6,2,8),(15,9,2,11),(25,15,3,18),(50,30,6,36)])
def test_profile_sizes_and_bounded_segment(stage,active,overlap,streams):
    plan=b.stream_plan(PROFILE,stage,PROFILE["seed"],RUN)
    assert (plan["active"],plan["overlapping"],len(plan["streams"]))==(active,overlap,streams)
    assert len(plan["subjects"])==stage and len(set(plan["subjects"]))==stage
    assert len(set(plan["courses"]))==2
    events=[b.event_for(x,run_id=RUN,seed=PROFILE["seed"],stage=stage,number=1,start=b.MODEL_START) for x in plan["streams"]]
    assert len({e["event_id"] for e in events})==streams
    assert all(e["active_ms"]==15000 and e["visible"] and e["focused"] for e in events)
    assert all(e["ended_at"]==(b.MODEL_START+timedelta(seconds=15)).isoformat() for e in events)
    assert b.stream_plan(PROFILE,stage,PROFILE["seed"],RUN)==plan
    assert b.event_for(plan["streams"][0],run_id=RUN,seed=PROFILE["seed"],stage=stage,number=1,start=b.MODEL_START)==events[0]

def test_grants_are_real_signed_scoped_and_short_lived():
    key=Ed25519PrivateKey.generate()
    plan=b.stream_plan(PROFILE,50,PROFILE["seed"],RUN)
    token=b.grant(key,issuer="bench-test",kid="bench-g1",subject=plan["subjects"][0],role="user",scope="collect",courses=(plan["courses"][0],))
    claims=jwt.decode(token,key.public_key(),algorithms=["EdDSA"],issuer="bench-test",audience="analytics")
    assert claims["exp"]-claims["iat"]==50 and claims["course_ids"]==[plan["courses"][0]] and claims["role"]=="user"
    with pytest.raises(ValueError): b.stream_plan(PROFILE,50,PROFILE["seed"]+1,RUN)

def test_rejects_unowned_config_and_full_pass_claim():
    for value in ("3,1,1","2,2,1","1,1", "x,1,1"):
        with pytest.raises(ValueError): b.parse_replicas(value)
    with pytest.raises(b.UnsafeTarget): b.output_path(ROOT/"bench/profile-v1.json")
    with pytest.raises(ValueError): b.profile_load(ROOT/"contracts/fixtures/activity-golden-v1.json")
    good={"oracle":{"status":"PASS"},"collector":{"p95_ms":1},"query":{"p95_ms":1},"timeouts":0,"requests":{"collector:200":10,"query:200":1},"ack":{"accepted":10},"expected_measurement_segments":10,
          "opening_ack":{"accepted":1},"expected_openings":1,"warmup_ack":{"accepted":1},"expected_warmup_segments":1,
          "expected_measurement_queries":1}
    assert b.classify(good,PROFILE)=="UNRUN"  # replay/privacy/repeats not executed
    assert b.classify({**good,"requests":{"collector:429":1}},PROFILE)=="FAIL"

def test_percentile_and_plan_never_claim_execution():
    assert b.summary([10,20,30,40,50])["p95_ms"]==50
    assert b.planned(PROFILE,50,PROFILE["seed"],(2,1,1),RUN)["execution"]=="not_started"

def test_matched_smoke_plan_is_explicit_short_and_non_acceptance(capsys):
    args=["--profile",str(ROOT/"bench/profile-v1.json"),"--stage","50","--replicas","1,2,1",
          "--seed",str(PROFILE["seed"]),"--output",str(ROOT/".local/benchmark-results"/"smoke-plan.json")]
    assert b.main(args+["--matched-smoke"])==2
    assert b.main(args+["--matched-cadence","60","--matched-smoke"])==0
    plan=json.loads(capsys.readouterr().out)
    assert (plan["warmup_seconds"],plan["measurement_seconds"],plan["heartbeat_seconds"])==(60,120,15)
    assert plan["matched_smoke"] is True and plan["acceptance_status"]=="UNRUN" and plan["status"]=="UNRUN"
    assert (PROFILE["warmup_seconds"],PROFILE["measurement_seconds"])==(300,900)

def test_smoke_activity_poll_is_bounded_recorded_and_processor_only(monkeypatch,capsys):
    args=["--profile",str(ROOT/"bench/profile-v1.json"),"--stage","10","--replicas","1,1,1",
          "--seed",str(PROFILE["seed"]),"--output",str(ROOT/".local/benchmark-results"/"smoke-plan.json")]
    assert b.main(args+["--activity-poll-ms","100"])==2
    assert b.main(args+["--matched-cadence","15","--activity-poll-ms","100"])==0
    full_plan=json.loads(capsys.readouterr().out)
    assert full_plan["activity_poll_ms"]==100 and (full_plan["warmup_seconds"],full_plan["measurement_seconds"])==(300,900)
    assert b.main(args+["--matched-cadence","15"])==0
    assert "activity_poll_ms" not in json.loads(capsys.readouterr().out)
    for value in ("49","501"):
        assert b.main(args+["--matched-cadence","15","--matched-smoke","--activity-poll-ms",value])==2
    assert b.main(args+["--matched-cadence","15","--matched-smoke"])==0
    assert json.loads(capsys.readouterr().out)["activity_poll_ms"]==500
    assert b.main(args+["--matched-cadence","15","--matched-smoke","--activity-poll-ms","100"])==0
    assert json.loads(capsys.readouterr().out)["activity_poll_ms"]==100
    seen=[]; ports=iter((23001,23002,23003,23004))
    monkeypatch.setattr(b.owned,"unused_port",lambda:next(ports))
    monkeypatch.setattr(b.owned,"wait_port",lambda *args:None)
    def spawn(args,**kwargs):
        seen.append(kwargs["env"]); return type("Process",(),{"pid":2000+len(seen)})()
    monkeypatch.setattr(b.owned,"spawn_owned",spawn)
    job=type("Job",(),{"assert_listener":lambda self,port:None})()
    urls={role:"synthetic-"+role for role in ("collector","processor","query","privacy")}
    b.OwnedProcesses(urls,(1,1,1),{},[],{},Path("unused"),"owner",job,[],projection_run_id=RUN,activity_poll_ms=100).start()
    key="ANALYTICS_PROCESSOR_ACTIVITY_POLL_SECONDS"
    assert seen[1][key]=="0.1" and all(key not in env for env in (seen[0],seen[2],seen[3]))
    class Low:
        def check(self): raise b.StopRun("resource floor reached")
        def result(self): return {"sample_count":1}
    monkeypatch.setattr(b,"write_result",lambda *args:None)
    result=asyncio.run(b.run_owned(PROFILE,10,PROFILE["seed"],(1,1,1),RUN,Path("unused"),Low(),
        matched_cadence=15,matched_smoke=True,activity_poll_ms=100))
    assert result["activity_poll_ms"]==100 and result["acceptance_status"]=="UNRUN"
    full=asyncio.run(b.run_owned(PROFILE,10,PROFILE["seed"],(1,1,1),RUN,Path("unused"),Low(),
        matched_cadence=15,activity_poll_ms=100))
    assert full["activity_poll_ms"]==100 and (full["warmup_seconds"],full["measurement_seconds"])==(300,900)

def test_matched_smoke_preflight_retains_short_non_acceptance_artifact(monkeypatch):
    class Low:
        def check(self): raise b.StopRun("resource floor reached")
        def result(self): return {"sample_count":1}
    captured=[]
    monkeypatch.setattr(b,"write_result",lambda path,value:captured.append(value.copy()))
    result=asyncio.run(b.run_owned(PROFILE,10,PROFILE["seed"],(1,2,1),RUN,Path("unused"),Low(),matched_cadence=15,matched_smoke=True))
    assert result["matched_smoke"] is True and result["acceptance_status"]=="UNRUN"
    assert result["status"]=="UNRUN" and (result["warmup_seconds"],result["measurement_seconds"])==(60,120)
    assert captured==[result]

def test_a014_tuned_plan_and_role_settings(monkeypatch, capsys):
    args = ["--profile", str(ROOT / "bench/profile-v1.json"), "--stage", "50",
            "--replicas", "1,1,1", "--seed", str(PROFILE["seed"]),
            "--output", str(ROOT / ".local/benchmark-results/a014-tuned-plan.json"),
            "--matched-cadence", "60", "--matched-schema", "a014", "--matched-smoke",
            "--activity-poll-ms", "50", "--query-pool-size", "4"]
    assert b.main(args) == 0
    plan = json.loads(capsys.readouterr().out)
    assert (plan["matched_schema"], plan["stage"], plan["activity_poll_ms"], plan["query_pool_size"]) == ("a014", 50, 50, 4)
    assert plan["execution"] == "not_started"

    ports = iter(range(23001, 23005))
    role_envs = {}

    def spawn(args, **kwargs):
        role_envs[args[4]] = kwargs["env"]
        return SimpleNamespace(pid=2000 + len(role_envs))

    monkeypatch.setattr(b.owned, "unused_port", lambda: next(ports))
    monkeypatch.setattr(b.owned, "wait_port", lambda *args: None)
    monkeypatch.setattr(b.owned, "spawn_owned", spawn)
    job = SimpleNamespace(assert_listener=lambda port: None)
    urls = {role: "synthetic-" + role for role in ("collector", "processor", "query", "privacy")}
    processes = b.OwnedProcesses(
        urls, (1, 1, 1), {}, [], {}, Path("unused"), "owner", job, [],
        projection_run_id=RUN, matched_schema="a014", activity_poll_ms=50, query_pool_size=4,
    )
    processes.start()
    assert role_envs["processor"]["ANALYTICS_PROCESSOR_ACTIVITY_POLL_SECONDS"] == "0.05"
    assert role_envs["processor"]["ANALYTICS_PROCESSOR_ACTIVITY_COURSE_WORKER_ENABLED"] == "true"
    boundary = "ANALYTICS_PROCESSOR_ACTIVITY_BOUNDARY_CREDIT_ENABLED"
    assert role_envs["processor"][boundary] == "true"
    assert all(boundary not in role_envs[role] for role in ("collector", "query", "privacy"))
    assert role_envs["query"]["ANALYTICS_QUERY_ACTIVITY_COURSE_QUERY_ENABLED"] == "true"
    assert role_envs["query"]["ANALYTICS_QUERY_POOL_SIZE"] == "4"


class Response:
    def __init__(self,data): self.status_code=200; self.content=json.dumps(data).encode(); self.headers={}
    def json(self): return json.loads(self.content)

class Client:
    async def request(self,method,url,headers=None,json=None,params=None):
        if method=="POST":
            event=json["events"][0]
            return Response({"items":[{"index":0,"event_id":event["event_id"],"status":"accepted","code":"committed"}]})
        return Response({"status":"ok","quality":"unknown","as_of":params["to_time"],"lag_seconds":None,
            "timezone":"Europe/Moscow","metric_version":"activity-v1","course_id":params.get("course_id"),
            "from_time":params["from_time"],"to_time":params["to_time"],
            "totals":{"unique_visitors":0,"active_ms":0,"theory_ms":0,"practice_ms":0},"daily":[],"trusted_progress":None})

class Processes:
    children=[]
    endpoints={role:["http://127.0.0.1:1"] for role in ("collector","processor","query","privacy")}

@pytest.mark.parametrize("cadence",[15,60])
def test_matched_smoke_virtual_clock_uses_short_windows(monkeypatch,cadence):
    actual_datetime=datetime
    class Clock: seconds=0.0
    clock=Clock(); boundaries=[]
    class FakeDatetime(actual_datetime):
        @classmethod
        def now(cls,tz=None): return cls.fromtimestamp(1767225600+clock.seconds,tz=timezone.utc)
    async def sleep(seconds): clock.seconds+=seconds
    async def ready(*args): pass
    async def boundary(label,elapsed): boundaries.append((label,elapsed))
    monkeypatch.setattr(b,"datetime",FakeDatetime); monkeypatch.setattr(b.time,"monotonic",lambda:clock.seconds)
    monkeypatch.setattr(b.asyncio,"sleep",sleep); monkeypatch.setattr(b,"ready",ready)
    smoke={**PROFILE,"warmup_seconds":60,"measurement_seconds":120}
    result=asyncio.run(b.execute_workload(Client(),Processes(),smoke,10,PROFILE["seed"],RUN,
        Ed25519PrivateKey.generate(),"bench-test","fp",matched_cadence=cadence,matched_smoke=True,measurement_boundary=boundary))
    assert result["elapsed_seconds"]==180 and result["measurement_elapsed_seconds"]==120
    assert boundaries==[("start_after_warmup_tick",60),("end_after_final_tick",180)]
    assert result["expected_warmup_segments"]==8*60//cadence
    assert result["expected_measurement_segments"]==8*120//cadence
    assert result["expected_measurement_queries"]==6*2+3*4
    with pytest.raises(ValueError):
        asyncio.run(b.execute_workload(Client(),Processes(),smoke,10,PROFILE["seed"],RUN,
            Ed25519PrivateKey.generate(),"bench-test","fp",matched_cadence=cadence))


def test_startup_timeout_keeps_last_not_ready_role_without_response_body(monkeypatch):
    class ReadinessClient:
        async def get(self, url):
            response = Response({"status": "ready", "private": "internal diagnostics"})
            response.headers["X-Analytics-Trust-Fingerprint"] = "fingerprint"
            if url.endswith(":8002/ready"):
                response.status_code = 503
            return response

    class ReadinessProcesses:
        children = []
        endpoints = {"collector": ["http://127.0.0.1:8001"], "query": ["http://127.0.0.1:8002"]}

    now = 0.0

    async def sleep(seconds):
        nonlocal now
        now += seconds

    monkeypatch.setattr(b.time, "monotonic", lambda: now)
    monkeypatch.setattr(b.asyncio, "sleep", sleep)
    with pytest.raises(b.StopRun) as failure:
        asyncio.run(b.await_ready(ReadinessClient(), ReadinessProcesses(), "fingerprint"))
    assert str(failure.value) == "readiness deadline expired (query[0] readiness HTTP 503)"


@pytest.mark.parametrize("stage,streams,active",[(10,8,6),(50,36,30)])
def test_full_schedule_using_virtual_clock_and_fake_http(monkeypatch,stage,streams,active):
    # Fake time is strictly a unit-test scheduler; production uses the real clock.
    actual_datetime=datetime
    class Clock:
        seconds=0.0
    clock=Clock()
    class FakeDatetime(actual_datetime):
        @classmethod
        def now(cls,tz=None): return cls.fromtimestamp(1767225600+clock.seconds,tz=timezone.utc)
    async def sleep(seconds): clock.seconds+=seconds
    async def ready(*args): pass
    monkeypatch.setattr(b,"datetime",FakeDatetime)
    monkeypatch.setattr(b.time,"monotonic",lambda:clock.seconds)
    monkeypatch.setattr(b.asyncio,"sleep",sleep)
    monkeypatch.setattr(b,"ready",ready)
    key=Ed25519PrivateKey.generate()
    result=asyncio.run(b.execute_workload(Client(),Processes(),PROFILE,stage,PROFILE["seed"],RUN,key,"bench-test","fingerprint"))
    assert result["elapsed_seconds"]==1200 and result["measurement_elapsed_seconds"]==900
    assert result["opening_ack"]=={"accepted":streams}
    assert result["warmup_ack"]=={"accepted":streams*20}
    assert result["ack"]=={"accepted":streams*60}
    assert result["requests"]=={"collector:200":streams*60,"query:200":active*15+90}
    assert result["expected_measurement_segments"]==streams*60 and result["expected_measurement_queries"]==active*15+90
    assert result["verification_requests"]=={"query:200":1}
    assert result["query_roles"]=={"leader":60,"admin":30,"user":active*15}
    assert result["oracle"]["status"]=="FAIL"  # deliberately incorrect fake report
    assert b.classify(result,PROFILE)=="FAIL"

def test_matched_cadences_cover_identical_intervals_and_sections(monkeypatch):
    actual_datetime=datetime
    class Clock: seconds=0.0
    clock=Clock()
    class FakeDatetime(actual_datetime):
        @classmethod
        def now(cls,tz=None): return cls.fromtimestamp(1767225600+clock.seconds,tz=timezone.utc)
    async def sleep(seconds): clock.seconds+=seconds
    async def ready(*args): pass
    monkeypatch.setattr(b,"datetime",FakeDatetime)
    monkeypatch.setattr(b.time,"monotonic",lambda:clock.seconds)
    monkeypatch.setattr(b.asyncio,"sleep",sleep)
    monkeypatch.setattr(b,"ready",ready)
    class Capture(Client):
        def __init__(self): self.events=[]; self.inflight=0; self.maximum=0
        async def request(self,method,url,headers=None,json=None,params=None):
            if method=="POST":
                self.inflight+=1; self.maximum=max(self.maximum,self.inflight)
                self.events.append(json["events"][0].copy())
                try: return await super().request(method,url,headers=headers,json=json,params=params)
                finally: self.inflight-=1
            return await super().request(method,url,headers=headers,json=json,params=params)
    samples=[]
    for cadence in (15,60):
        clock.seconds=0; client=Capture(); boundaries=[]
        async def boundary(label,elapsed): boundaries.append((label,elapsed))
        result=asyncio.run(b.execute_workload(client,Processes(),PROFILE,10,PROFILE["seed"],RUN,
            Ed25519PrivateKey.generate(),"bench-test","fp",matched_cadence=cadence,measurement_boundary=boundary))
        assert boundaries==[("start_after_warmup_tick",300),("end_after_final_tick",1200)]
        assert client.maximum==1
        assert result["expected_warmup_segments"]==8*300//cadence
        assert result["expected_measurement_segments"]==8*900//cadence
        assert result["ack"]=={"accepted":8*900//cadence}
        assert result["requests"]["query:200"]==6*15+90
        lag=result["matched_ack_to_admin"]
        assert lag["status"]=="OBSERVED" and lag["summary_ms"]["count"]==15 and lag["max_ms"]==0
        assert "does not prove inclusion" in lag["note"]
        samples.append([e for e in client.events if e["type"]=="activity_segment"])
    def minutes(events):
        result={}
        for e in events:
            start=datetime.fromisoformat(e["started_at"])
            minute=int((start-b.MODEL_START).total_seconds())//60
            key=(e["session_id"],minute,e["section"])
            result[key]=result.get(key,0)+e["active_ms"]
        return result
    assert minutes(samples[0])==minutes(samples[1])
    assert all(value==60000 for value in minutes(samples[0]).values())

def test_measurement_deltas_require_complete_stable_counter_snapshots():
    def sample(label,n):
        return {"label":label,"elapsed_seconds":n,"status":"OBSERVED",
                "database":{"database_bytes":100+n,"database_reset":"same","wal_bytes":1000+n,"wal_reset":"same",
                            "tables":{"activity_facts":{"oid":42,"insert":n,"update":n*2,"delete":0}}},
                "cpu":[{"role":"processor","index":0,"values":{"analytics_process_cpu_seconds_total":n}}]}
    start=sample("start_after_warmup_tick",1); end=sample("end_after_final_tick",3)
    result=b.summarize_measurement_boundaries([start,end])
    assert result["status"]=="OBSERVED" and result["cluster_wal_bytes_delta"]==2
    assert result["table_writes"]["activity_facts"]=={"insert":2,"update":4,"delete":0}
    assert result["process_cpu_seconds_delta"]=={"processor:0":2}
    for item,n in ((start,1),(end,3)):
        item["database"]["tables"]["activity_facts"].update(table_bytes_including_toast=100*n,index_bytes=20*n,estimated_live_rows=4*n)
    footprint=b.summarize_measurement_boundaries([start,end])["relation_footprint"]["activity_facts"]
    assert footprint["start"]=={"table_bytes_including_toast":100,"index_bytes":20,"estimated_live_rows":4}
    assert footprint["end"]=={"table_bytes_including_toast":300,"index_bytes":60,"estimated_live_rows":12}
    assert b.summarize_measurement_boundaries([start])["status"]=="UNRUN"
    end["database"]["wal_reset"]="changed"
    assert b.summarize_measurement_boundaries([start,end])["status"]=="UNRUN"
    end["database"]["wal_reset"]="same"; end["database"]["tables"]["activity_facts"]["oid"]=43
    assert b.summarize_measurement_boundaries([start,end])["status"]=="UNRUN"

def test_matched_ack_latency_includes_remaining_batch_and_report(monkeypatch):
    actual_datetime=datetime
    class Clock: seconds=0.0
    clock=Clock()
    class FakeDatetime(actual_datetime):
        @classmethod
        def now(cls,tz=None): return cls.fromtimestamp(1767225600+clock.seconds,tz=timezone.utc)
    async def sleep(seconds): clock.seconds+=seconds
    async def ready(*args): pass
    class TimedClient(Client):
        async def request(self,method,url,headers=None,json=None,params=None):
            clock.seconds+=0.01 if method=="POST" else 0.02
            return await super().request(method,url,headers=headers,json=json,params=params)
    monkeypatch.setattr(b,"datetime",FakeDatetime)
    monkeypatch.setattr(b.time,"monotonic",lambda:clock.seconds)
    monkeypatch.setattr(b.asyncio,"sleep",sleep)
    monkeypatch.setattr(b,"ready",ready)
    result=asyncio.run(b.execute_workload(TimedClient(),Processes(),PROFILE,10,PROFILE["seed"],RUN,
        Ed25519PrivateKey.generate(),"bench-test","fp",matched_cadence=60))
    sample=result["matched_ack_to_admin"]
    assert sample["summary_ms"]["count"]==15
    # Seven remaining serial ACKs plus the three earlier admin-tick requests.
    assert sample["max_ms"]==pytest.approx(130,abs=.01)

def test_run_flag_without_root_go_never_provisions(monkeypatch):
    monkeypatch.delenv("ANALYTICS_BENCHMARK_GO",raising=False)
    def forbidden(): raise AssertionError("must not provision before GO")
    monkeypatch.setattr(b.owned,"private_folder",forbidden)
    output=ROOT/".local/benchmark-results"/(str(RUN)+"-should-not-exist.json")
    code=b.main(["--profile",str(ROOT/"bench/profile-v1.json"),"--stage","10","--replicas","1,1,1",
                 "--seed",str(PROFILE["seed"]),"--output",str(output),"--run"])
    assert code==2 and not output.exists()

def test_resource_floors_are_explicit_and_metrics_numeric():
    with pytest.raises(b.UnsafeTarget): b.resource_floors({})
    m=b.resource_floors({"ANALYTICS_BENCHMARK_MIN_FREE_MEMORY_MIB":"1200", "ANALYTICS_BENCHMARK_MIN_FREE_DISK_MIB":"2000"})
    assert m.memory_floor_bytes==1200*1024*1024 and m.result()["sample_count"]==0
    lines='analytics_process_cpu_seconds_total{role="query"} 1.2\nanalytics_database_pool_checked_out_known{role="query"} 1\n'
    assert b.parse_metrics(lines)["analytics_process_cpu_seconds_total"]==1.2
    with pytest.raises(b.StopRun): b.parse_metrics('analytics_process_cpu_seconds_total NaN')

def test_timeout_unknown_stops_without_retry(monkeypatch):
    import httpx
    class TimeoutClient:
        calls=0
        async def request(self,*args,**kwargs):
            self.calls+=1
            raise httpx.ReadTimeout("synthetic lost reply")
    client=TimeoutClient(); stats=b.new_stats()
    async def ready(*args): pass
    monkeypatch.setattr(b,"ready",ready)
    with pytest.raises(b.StopRun,match="outcome unknown"):
        asyncio.run(b.execute_workload(client,Processes(),PROFILE,10,PROFILE["seed"],RUN,
                                      Ed25519PrivateKey.generate(),"bench-test","fp",stats=stats))
    assert 1<=client.calls<=8 and stats["timeouts"]==client.calls

def test_rate_limit_is_recorded_without_retry(monkeypatch):
    actual_datetime=datetime
    class Clock: seconds=0.0
    clock=Clock()
    class FakeDatetime(actual_datetime):
        @classmethod
        def now(cls,tz=None): return cls.fromtimestamp(1767225600+clock.seconds,tz=timezone.utc)
    class Limited(Client):
        posts=0
        async def request(self,method,url,headers=None,json=None,params=None):
            if method=="POST":
                self.posts+=1
                if self.posts==169:
                    result=Response({}); result.status_code=429; result.headers={"Retry-After":"2"}; return result
            return await super().request(method,url,headers=headers,json=json,params=params)
    async def sleep(seconds): clock.seconds+=seconds
    async def ready(*args): pass
    monkeypatch.setattr(b,"datetime",FakeDatetime); monkeypatch.setattr(b.time,"monotonic",lambda:clock.seconds)
    monkeypatch.setattr(b.asyncio,"sleep",sleep); monkeypatch.setattr(b,"ready",ready)
    client=Limited()
    result=asyncio.run(b.execute_workload(client,Processes(),PROFILE,10,PROFILE["seed"],RUN,
                                         Ed25519PrivateKey.generate(),"bench-test","fp"))
    assert client.posts==8+8*80 and result["ack"]=={"accepted":479}
    assert result["retry_after"]==["2"] and result["requests"]["collector:429"]==1
    assert b.classify(result,PROFILE)=="FAIL"

def test_matched_rate_limit_stops_without_retry(monkeypatch):
    class Limited(Client):
        calls=0
        async def request(self,method,url,headers=None,json=None,params=None):
            self.calls+=1
            result=Response({}); result.status_code=429; result.headers={"Retry-After":"1"}
            return result
    client=Limited(); stats=b.new_stats()
    with pytest.raises(b.StopRun,match="429; matched workload incomplete"):
        asyncio.run(b.execute_workload(client,Processes(),PROFILE,10,PROFILE["seed"],RUN,
            Ed25519PrivateKey.generate(),"bench-test","fp",stats=stats,matched_cadence=60))
    assert client.calls==1 and stats["warmup_requests"]=={"collector:429":1}
    assert stats["retry_after"]==["1"] and b.observed_violation(stats)


def test_projection_503_stops_without_retry(monkeypatch):
    actual_datetime=datetime
    class Clock: seconds=0.0
    clock=Clock(); readiness=[]; waits=[]
    class FakeDatetime(actual_datetime):
        @classmethod
        def now(cls,tz=None): return cls.fromtimestamp(1767225600+clock.seconds,tz=timezone.utc)
    class DirtyOnce(Client):
        queries=0
        async def request(self,method,url,headers=None,json=None,params=None):
            if method=="GET":
                self.queries+=1
                if self.queries==4:
                    result=Response({}); result.status_code=503; return result
            return await super().request(method,url,headers=headers,json=json,params=params)
    async def sleep(seconds): clock.seconds+=seconds
    async def ready(*args): readiness.append(clock.seconds)
    async def wait(): waits.append(clock.seconds)
    monkeypatch.setattr(b,"datetime",FakeDatetime); monkeypatch.setattr(b.time,"monotonic",lambda:clock.seconds)
    monkeypatch.setattr(b.asyncio,"sleep",sleep); monkeypatch.setattr(b,"ready",ready)
    client=DirtyOnce(); diagnostics=[]
    async def diagnostic(context):
        diagnostics.append(context)
        return {"status":"OBSERVED","tick":context["tick"],"role":context["role"]}
    stats=b.new_stats()
    with pytest.raises(b.StopRun,match="unavailable/privacy"):
        asyncio.run(b.execute_workload(client,Processes(),PROFILE,10,PROFILE["seed"],RUN,
            Ed25519PrivateKey.generate(),"bench-test","fp",projection_wait=wait,stats=stats,query_failure_diagnostic=diagnostic))
    assert stats["warmup_requests"]["query:503"]==1
    assert len(readiness)==4 and waits==[]
    # Concurrent peers already scheduled in the same burst may finish; no retry is issued.
    assert client.queries==12 and b.observed_violation(stats)
    assert len(diagnostics)==1 and diagnostics[0]["tick"]==4 and diagnostics[0]["phase"]=="warmup"
    assert stats["query_failure_diagnostic"]["status"]=="OBSERVED"

def test_failure_log_exposes_only_allowlisted_reasons(monkeypatch):
    from io import BytesIO
    contents=(b"sensitive token=secret\nactivity_report_unavailable reason=privacy_unavailable\n"
              b"activity_report_unavailable reason=secret\nactivity_report_unavailable reason=projection_invalid\n")
    monkeypatch.setattr(Path,"open",lambda *args,**kwargs:BytesIO(contents))
    assert b.query_failure_log_reasons(Path("owned"),0)==["privacy_unavailable","projection_invalid"]

def test_boundary_query_count_and_write_order_with_virtual_clock(monkeypatch):
    actual_datetime=datetime
    class Clock: seconds=0.0
    clock=Clock(); trace=[]; captured=[]
    class FakeDatetime(actual_datetime):
        @classmethod
        def now(cls,tz=None): return cls.fromtimestamp(1767225600+clock.seconds,tz=timezone.utc)
    class AtBoundary(Client):
        async def request(self,method,url,headers=None,json=None,params=None):
            trace.append((clock.seconds,method))
            if method == "GET":
                assert datetime.fromisoformat(params["from_time"]) == datetime.fromtimestamp(1767225600, timezone.utc)
                assert datetime.fromisoformat(params["to_time"]) > datetime.fromisoformat(params["from_time"])
            if method=="GET" and clock.seconds>=300:
                result=Response({}); result.status_code=503; return result
            return await super().request(method,url,headers=headers,json=json,params=params)
    async def sleep(seconds): clock.seconds+=seconds
    async def ready(*args): pass
    async def diagnostic(context): captured.append(context.copy()); return {"status":"OBSERVED"}
    monkeypatch.setattr(b,"datetime",FakeDatetime); monkeypatch.setattr(b.time,"monotonic",lambda:clock.seconds)
    monkeypatch.setattr(b.asyncio,"sleep",sleep); monkeypatch.setattr(b,"ready",ready)
    stats=b.new_stats()
    with pytest.raises(b.StopRun,match="unavailable/privacy"):
        asyncio.run(b.execute_workload(AtBoundary(),Processes(),PROFILE,10,PROFILE["seed"],RUN,
            Ed25519PrivateKey.generate(),"bench-test","fp",matched_cadence=15,stats=stats,query_failure_diagnostic=diagnostic))
    assert sum(method=="GET" and t<300 for t,method in trace)==51
    boundary=[method for t,method in trace if t==300]
    assert boundary[:8]==["POST"]*8 and boundary[8:]==["GET"]*9
    assert len(captured)==1 and captured[0]["tick"]==20 and captured[0]["phase"]=="warmup"
    assert captured[0]["from_time"] == datetime.fromtimestamp(1767225600, timezone.utc)


def test_guard_stop_is_not_a_measured_failure():
    stats=b.new_stats()
    assert not b.observed_violation(stats)
    stats["requests"]["query:503"]=1
    assert b.observed_violation(stats)
    assert issubclass(b.ObservedViolation,b.StopRun)

def test_resource_preflight_failure_does_not_open_resources(monkeypatch):
    class Low:
        def check(self): raise b.StopRun("resource floor reached")
        def result(self): return {"sample_count":1}
    def forbidden(): raise AssertionError("preflight must stop before provisioning")
    captured=[]
    monkeypatch.setattr(b.owned,"private_folder",forbidden)
    monkeypatch.setattr(b,"write_result",lambda path,value:captured.append(value.copy()))
    result=asyncio.run(b.run_owned(PROFILE,10,PROFILE["seed"],(1,1,1),RUN,Path("unused"),Low()))
    assert result["execution"]=="failed" and result["status"]=="UNRUN" and len(captured)==1


def test_low_windows_commit_headroom_stops_before_provisioning(monkeypatch):
    mib = 1024 * 1024

    def memory_status(pointer):
        memory = pointer._obj
        memory.avail_phys = 4096 * mib
        memory.avail_page = 331 * mib
        return 1

    def forbidden():
        raise AssertionError("commit floor must stop before provisioning")

    monkeypatch.setattr(b, "os", SimpleNamespace(name="nt", cpu_count=b.os.cpu_count))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(
        GlobalMemoryStatusEx=memory_status)), raising=False)
    monkeypatch.setattr(b.shutil, "disk_usage", lambda path: SimpleNamespace(free=16384 * mib))
    monkeypatch.setattr(b.owned, "private_folder", forbidden)
    captured = []
    monkeypatch.setattr(b, "write_result", lambda path, value: captured.append(value))
    monitor = b.resource_floors({
        "ANALYTICS_BENCHMARK_MIN_FREE_MEMORY_MIB": "1500",
        "ANALYTICS_BENCHMARK_MIN_FREE_DISK_MIB": "8000",
        "ANALYTICS_BENCHMARK_MIN_FREE_COMMIT_MIB": "512",
    })

    result = asyncio.run(b.run_owned(PROFILE, 50, PROFILE["seed"], (1, 1, 1), RUN, Path("unused"), monitor))

    assert captured == [result]
    assert (result["execution"], result["status"]) == ("failed", "UNRUN")
    assert result["failure_reason"] == "Windows commit headroom floor reached"
    assert result["resources"]["commit_floor_bytes"] == 512 * mib
    assert result["resources"]["min_free_pagefile_bytes"] == 331 * mib


def test_projection_generation_requires_owned_migrate_identity(monkeypatch):
    from contextlib import contextmanager
    statements=[]
    class Result:
        def fetchone(self): return ("owner_migrate",)
    class Connection:
        def execute(self,sql,params=None): statements.append((sql,params)); return Result()
    @contextmanager
    def guarded(target,*,write=False):
        assert write is True
        yield Connection()
    monkeypatch.setattr(b,"_guarded_connection",guarded)
    b.initialize_projection({"owner":"owner_migrate"},RUN)
    assert statements[-1]==("SELECT analytics.begin_activity_projection_backfill(NULL,%s)",(RUN,))
    with pytest.raises(b.UnsafeTarget): b.initialize_projection({"owner":"different"},RUN)
    with pytest.raises(ValueError): b.initialize_projection({"owner":"owner_migrate"},None)

def test_owned_replicas_scrub_environment_and_check_listeners(monkeypatch):
    monkeypatch.setenv("DATABASE_URL","must-not-inherit")
    monkeypatch.setenv("ANALYTICS_GRANT_SECRET","must-not-inherit")
    ports=iter((23001,23002,23003,23004,23005))
    monkeypatch.setattr(b.owned,"unused_port",lambda:next(ports))
    monkeypatch.setattr(b.owned,"wait_port",lambda *args:None)
    seen=[]
    class Process:
        def __init__(self,pid): self.pid=pid
    class Job:
        ports=[]
        def assert_listener(self,port): self.ports.append(port)
    def spawn(args,**kwargs):
        seen.append(kwargs["env"]); return Process(2000+len(seen))
    monkeypatch.setattr(b.owned,"spawn_owned",spawn)
    urls={role:"synthetic-"+role for role in ("collector","processor","query","privacy")}
    job=Job(); processes=b.OwnedProcesses(urls,(2,1,1),{},[],{},Path("unused"),"owner",job,[],projection_run_id=RUN)
    processes.start()
    assert len(seen)==5 and job.ports==[23001,23002,23003,23004,23005]
    assert all("DATABASE_URL" not in env and "ANALYTICS_GRANT_SECRET" not in env for env in seen)
    assert [d["role"] for d in processes.descriptors]==["collector","collector","processor","query","privacy"]
    for env,descriptor in zip(seen,processes.descriptors):
        role=descriptor["role"]
        if role in ("processor","query"):
            assert env["ANALYTICS_ACTIVITY_PROJECTION_RUN_ID"]==str(RUN)
            assert env[f"ANALYTICS_{role.upper()}_ACTIVITY_PROJECTION_ENABLED"]=="true"
        else:
            assert "ANALYTICS_ACTIVITY_PROJECTION_RUN_ID" not in env

@pytest.mark.parametrize("schema",["a010","a011","a012"])
def test_matched_schema_enables_only_its_projection_flags(monkeypatch,schema):
    seen=[]; ports=iter((23001,23002,23003,23004))
    monkeypatch.setattr(b.owned,"unused_port",lambda:next(ports))
    monkeypatch.setattr(b.owned,"wait_port",lambda *args:None)
    def spawn(args,**kwargs):
        seen.append(kwargs["env"]); return type("Process",(),{"pid":2000+len(seen)})()
    monkeypatch.setattr(b.owned,"spawn_owned",spawn)
    job=type("Job",(),{"assert_listener":lambda self,port:None})()
    urls={role:"synthetic-"+role for role in ("collector","processor","query","privacy")}
    b.OwnedProcesses(urls,(1,1,1),{},[],{},Path("unused"),"owner",job,[],projection_run_id=RUN,matched_schema=schema).start()
    processor,query=seen[1],seen[2]
    for role,env,suffix in (("PROCESSOR",processor,"WORKER"),("QUERY",query,"QUERY")):
        assert (f"ANALYTICS_{role}_ACTIVITY_DAY_{suffix}_ENABLED" in env)==(schema!="a010")
        assert (f"ANALYTICS_{role}_ACTIVITY_COURSE_{suffix}_ENABLED" in env)==(schema=="a012")

def test_matched_schema_cli_is_opt_in_and_requires_cadence(capsys):
    args=["--profile",str(ROOT/"bench/profile-v1.json"),"--stage","10","--replicas","1,1,1",
          "--seed",str(PROFILE["seed"]),"--output",str(ROOT/".local/benchmark-results"/"plan.json")]
    assert b.main(args+["--matched-schema","a012"])==2
    assert b.main(args+["--matched-cadence","15","--matched-schema","a012"])==0
    assert json.loads(capsys.readouterr().out)["matched_schema"]=="a012"

def test_alternate_postgres_is_explicit_and_narrow(capsys):
    from pymentor_analytics.app.config import Settings, load_settings
    name="analytics_test_"+"a"*32
    target={"project":"knowi-analytics-s16-benchmark","synthetic_only":True,"host":"127.0.0.1",
            "port":62332,"user":"analytics_bootstrap","database":"analytics_bootstrap","password":"fixture"}
    b.bootstrap.validate(target,benchmark=True)
    f_target={**target,"project":"knowi-analytics-s16-benchmark-f","port":62333}
    b.bootstrap.validate(f_target,benchmark=True)
    with pytest.raises(ValueError): b.bootstrap.validate(f_target)
    with pytest.raises(ValueError): b.bootstrap.validate({**f_target,"port":62332},benchmark=True)
    with pytest.raises(ValueError): b.bootstrap.validate({**target,"port":62333},benchmark=True)
    with pytest.raises(ValueError): b.bootstrap.validate(target)
    for key,value in (("port",57392),("host","localhost"),("project","other"),("synthetic_only",False)):
        with pytest.raises(ValueError): b.bootstrap.validate({**target,key:value},benchmark=True)
    url=f"postgresql://{name}_query:fixture@127.0.0.1:62332/{name}"
    assert Settings(database_url=url).database_url.get_secret_value()==url
    assert load_settings("query",{"ANALYTICS_QUERY_DATABASE_URL":url})
    f_url=url.replace("62332","62333")
    assert Settings(database_url=f_url) and load_settings("query",{"ANALYTICS_QUERY_DATABASE_URL":f_url})
    with pytest.raises(ValueError): load_settings("collector",{"ANALYTICS_COLLECTOR_DATABASE_URL":f_url})
    with pytest.raises(ValueError): load_settings("collector",{"ANALYTICS_COLLECTOR_DATABASE_URL":url})
    for bad in (url.replace("127.0.0.1","localhost"),url.replace("62332","62334"),
                url.replace(name+"_query",name+"_admin"),url.replace(name,"analytics_test_bad"),
                url.replace("127.0.0.1","example.com")):
        with pytest.raises(ValueError): Settings(database_url=bad)
    with pytest.raises(ValueError): Settings(database_url=url,real_collection=True)
    args=["--profile",str(ROOT/"bench/profile-v1.json"),"--stage","10","--replicas","1,1,1",
          "--seed",str(PROFILE["seed"]),"--output",str(ROOT/".local/benchmark-results"/"plan.json")]
    assert b.main(args+["--benchmark-postgres"])==2
    assert b.main(args+["--matched-cadence","15","--benchmark-postgres"])==0
    assert json.loads(capsys.readouterr().out)["postgres_target"]=="benchmark_wsl"

def test_missed_heartbeat_stops_instead_of_catchup_burst(monkeypatch):
    actual_datetime=datetime
    class Clock: seconds=0.0
    clock=Clock()
    class FakeDatetime(actual_datetime):
        @classmethod
        def now(cls,tz=None): return cls.fromtimestamp(1767225600+clock.seconds,tz=timezone.utc)
    async def late_sleep(seconds): clock.seconds+=seconds+16
    async def ready(*args): pass
    monkeypatch.setattr(b,"datetime",FakeDatetime); monkeypatch.setattr(b.time,"monotonic",lambda:clock.seconds)
    monkeypatch.setattr(b.asyncio,"sleep",late_sleep); monkeypatch.setattr(b,"ready",ready)
    with pytest.raises(b.StopRun,match="missed heartbeat"):
        asyncio.run(b.execute_workload(Client(),Processes(),PROFILE,10,PROFILE["seed"],RUN,
                                      Ed25519PrivateKey.generate(),"bench-test","fp"))

def test_replica_metrics_must_be_available_and_bounded():
    class MetricsResponse:
        status_code=200
        text=("analytics_process_cpu_seconds_total{role=\"query\"} 1\n"
              "analytics_database_pool_checked_out_known{role=\"query\"} 1\n"
              "analytics_database_pool_checked_out{role=\"query\"} 0\n"
              "analytics_database_pool_max_known{role=\"query\"} 1\n"
              "analytics_database_pool_max{role=\"query\"} 2\n"
              "analytics_diagnostics_available 1\nanalytics_database_connections 4\n")
    class MetricsClient:
        async def get(self,url): return MetricsResponse()
    rows=asyncio.run(b.sample_metrics(MetricsClient(),Processes()))
    assert {r["role"] for r in rows}=={"collector","processor","query","privacy"}
    MetricsResponse.text=MetricsResponse.text.replace("analytics_diagnostics_available 1","analytics_diagnostics_available 0")
    with pytest.raises(b.StopRun,match="diagnostics unavailable"):
        asyncio.run(b.sample_metrics(MetricsClient(),Processes()))

def test_separate_correctness_http_mode_uses_dedup_and_gap():
    class MemoryClient:
        def __init__(self): self.events={}
        async def post(self,url,headers,json):
            event=json["events"][0]; subject=jwt.decode(headers["Authorization"].split()[1],options={"verify_signature":False})["sub"]
            key=(subject,event["event_id"])
            previous=self.events.get(key)
            status="accepted" if previous is None else "duplicate" if previous==event else "conflict"
            if previous is None: self.events[key]=event
            code={"accepted":"committed","duplicate":"same_payload","conflict":"payload_conflict"}[status]
            return Response({"items":[{"index":0,"event_id":event["event_id"],"status":status,"code":code}]})
        async def get(self,url,headers,params):
            records=[b.AcceptedEvent.model_validate_json(__import__("json").dumps({"producer_namespace":"browser:"+subject,
                     "subject_ref":subject,"source":"browser","event":event,"received_at":event["ended_at"]}))
                     for (subject,_),event in self.events.items()]
            start=datetime.fromisoformat(params["from_time"]); end=datetime.fromisoformat(params["to_time"])
            now=datetime.now(timezone.utc).replace(microsecond=0)
            result=b.oracle(records,start=start,end=end,as_of=now,input_complete=False)
            values={"unique_visitors":result["visitors"],"active_ms":result["total_ms"],
                    "theory_ms":result["theory_ms"],"practice_ms":result["practice_ms"]}
            daily=[{"date":day,"unique_visitors":1,"active_ms":v["total_ms"],"theory_ms":v["theory_ms"],"practice_ms":v["practice_ms"]} for day,v in result["daily"].items()]
            return Response({"status":"ok","quality":"unknown","as_of":now.isoformat(),"lag_seconds":None,
                             "timezone":"Europe/Moscow","metric_version":"activity-v1","course_id":None,
                             "from_time":params["from_time"],"to_time":params["to_time"],"totals":values,"daily":daily})
    result=asyncio.run(b.execute_correctness(MemoryClient(),Processes(),PROFILE,10,PROFILE["seed"],RUN,
                                             Ed25519PrivateKey.generate(),"bench-test"))
    assert result["status"]=="PASS" and result["expected"]["active_ms"]==35000
    assert result["acks"]==["accepted","accepted","accepted","accepted","duplicate","conflict"]
    assert result["tracker_visibility"]=="UNRUN"

def test_failed_measurement_has_nonzero_exit_code():
    assert b.exit_code({"execution":"finished","status":"FAIL"})==1
    assert b.exit_code({"execution":"finished","status":"UNRUN"})==0
    assert b.exit_code({"execution":"failed","status":"UNRUN"})==1

def test_replay_quota_and_reused_operation_id_with_virtual_clock(monkeypatch):
    from types import SimpleNamespace
    class Clock: seconds=0.0
    clock=Clock()
    async def sleep(seconds): clock.seconds+=seconds
    monkeypatch.setattr(b.time,"monotonic",lambda:clock.seconds); monkeypatch.setattr(b.asyncio,"sleep",sleep)
    handle=object()
    class Replay:
        begins=[]; limits=[]; phases=iter(("scan","scan","drain"))
        async def begin(self,consumer,version,subject,request_id):
            self.begins.append(request_id); return SimpleNamespace(handle=handle,phase="cleanup")
        async def step(self,request,limit):
            assert request is handle; self.limits.append(limit)
            return SimpleNamespace(handle=handle,phase=next(self.phases))
        async def finish(self,request): return SimpleNamespace(handle=handle,phase="complete")
    class Monitor:
        def check(self): pass
    replay=Replay()
    result=asyncio.run(b.paced_replay(replay,UUID(int=1),RUN,Monitor()))
    assert replay.begins==[RUN,RUN] and replay.limits==[1,1,1]
    assert result["minimum_step_interval_seconds"]==2 and result["time_projection"]=="UNRUN"

def test_delete_timeout_is_unknown_and_not_retried():
    class Monitor:
        def check(self): pass
    calls=[]
    async def append(): calls.append(1); raise TimeoutError()
    async def wait(seq): raise AssertionError("must not poll after unknown append")
    with pytest.raises(b.StopRun,match="outcome unknown"):
        asyncio.run(b.measure_deletion(append,wait,Monitor()))
    assert calls==[1]

def test_recovery_orchestration_offline_closed_until_purge(monkeypatch):
    from types import SimpleNamespace
    import pymentor_analytics.adapters.postgres.database as dbmod
    import pymentor_analytics.adapters.postgres.replay as replaymod
    import pymentor_analytics.adapters.privacy_authority.client as authmod
    import pymentor_analytics.app.privacy as privmod
    trace=[]
    class Monitor:
        def check(self): trace.append("monitor")
    class DB:
        def __init__(self,settings): self.transaction_guard=None
        async def close(self): trace.append("db_closed")
    class Gate:
        def __init__(self,client,db): pass
        async def check_in_transaction(self,*args): pass
        def start(self): trace.append("gate_started")
        async def is_ready(self): return True
        async def stop(self): trace.append("gate_stopped")
    class Authority:
        next_seq=0
        def __init__(self,config): pass
        async def append(self,control,**kwargs):
            self.next_seq+=1; trace.append("append"); return SimpleNamespace(seq=self.next_seq)
    class Config:
        @classmethod
        def model_validate(cls,obj): return obj
    class Replay:
        def __init__(self,db): pass
    class Bytes:
        def read_bytes(self): return b"synthetic"
    class Folder:
        def __truediv__(self,child): return Bytes()
    class RecoveryClient:
        post_count=0; ready_count=0
        async def post(self,url,headers,json):
            self.post_count+=1
            if self.post_count==7:
                r=Response({}); r.status_code=403; return r
            return Response({"items":[{"index":0,"event_id":json["events"][0]["event_id"],"status":"accepted","code":"committed"}]})
        async def get(self,url,headers=None,params=None):
            if url.endswith("/ready"):
                self.ready_count+=1; r=Response({}); r.status_code=503 if self.ready_count==1 else 200
                r.headers={"X-Analytics-Trust-Fingerprint":"fp"}; return r
            r=Response({}); r.status_code=403; return r
    async def ready(*args): trace.append("source_ready")
    async def paced(*args): return {"status":"PASS","time_projection":"UNRUN"}
    async def direct_thread(fn,*args): return fn(*args)
    source={"name":"synthetic"}; restored={"name":"restored"}
    monkeypatch.setattr(dbmod,"Database",DB); monkeypatch.setattr(replaymod,"PostgresReplay",Replay)
    monkeypatch.setattr(authmod,"AuthorityClient",Authority); monkeypatch.setattr(privmod,"PrivacyRuntime",Gate)
    monkeypatch.setattr(b,"AuthorityConfig",Config)
    monkeypatch.setattr(b.serialization,"load_pem_private_key",lambda *args,**kwargs:object())
    monkeypatch.setattr(b,"producer_grant",lambda *args:"token")
    monkeypatch.setattr(b,"read_replay_result",lambda *args:(4,{"course":4},"fingerprint"))
    monkeypatch.setattr(b,"browser_snapshot",lambda *args:{"activity_facts":["{}"],"privacy_state":["{}"]})
    monkeypatch.setattr(b,"read_deletion_status",lambda *args:True)
    monkeypatch.setattr(b,"transplant_browser_snapshot",lambda *args:trace.append("transplanted"))
    monkeypatch.setattr(b,"verify_database",lambda *args:None)
    monkeypatch.setattr(b.owned,"create_analytics",lambda *args:(restored,{"query":"q","privacy":"p","processor":"w"},{}))
    monkeypatch.setattr(b.owned,"install_analytics",lambda *args,**kwargs:None)
    monkeypatch.setattr(b,"initialize_projection",lambda *args:trace.append("initialize_projection"))
    monkeypatch.setattr(b.owned,"verify_analytics_grants",lambda *args:None)
    monkeypatch.setattr(b,"spawn_restore_role",lambda role,*args,**kwargs:trace.append("spawn_"+role) or "http://owned")
    monkeypatch.setattr(b,"await_ready",ready); monkeypatch.setattr(b,"paced_replay",paced)
    monkeypatch.setattr(b.asyncio,"to_thread",direct_thread)
    result=asyncio.run(b.execute_recovery(RecoveryClient(),Processes(),PROFILE,10,PROFILE["seed"],RUN,
        Ed25519PrivateKey.generate(),"bench-test",{}, {"processor":"postgresql://user@127.0.0.1:57392/analytics_test"},
        source,Folder(),"owner",object(),[],{},[],Monitor(),"fp"))
    assert result["replay_sample"]["status"]=="PASS" and len(result["privacy_deletion_sample"]["samples"])==3
    assert result["restore_barrier_sample"]["initial_ready_status"]==503
    assert result["full_backup_restore_rpo_rto"]=="UNRUN" and trace.count("append")==3
    assert trace.index("spawn_query") < trace.index("spawn_privacy")
    assert trace.index("transplanted") < trace.index("initialize_projection") < trace.index("spawn_query") < trace.index("spawn_processor")

def test_mixed_background_is_bounded_and_uses_separate_privacy_client(monkeypatch):
    from types import SimpleNamespace
    import pymentor_analytics.adapters.postgres.database as dbmod
    import pymentor_analytics.adapters.postgres.replay as replaymod
    import pymentor_analytics.adapters.privacy_authority.client as authmod
    import pymentor_analytics.app.privacy as privmod
    trace=[]
    class Monitor:
        def check(self): pass
    class DB:
        def __init__(self,settings): self.transaction_guard=None
        async def close(self): trace.append("db_closed")
    class Gate:
        def __init__(self,client,db): trace.append("gate_client")
        async def check_in_transaction(self,*args): pass
        def start(self): trace.append("gate_start")
        async def is_ready(self): return True
        async def stop(self): trace.append("gate_stop")
    class Authority:
        next_seq=0
        def __init__(self,config): trace.append("separate_client")
        async def append(self,control,**kwargs):
            self.next_seq+=1; trace.append("append"); return SimpleNamespace(seq=self.next_seq)
    class Config:
        @classmethod
        def model_validate(cls,obj): return obj
    class Replay:
        def __init__(self,db): pass
    class Bytes:
        def read_bytes(self): return b"synthetic"
    class Folder:
        def __truediv__(self,child): return Bytes()
    async def sleep(seconds): trace.append(("sleep",seconds))
    async def paced(*args,**kwargs): trace.append("replay"); return {"status":"PASS","time_projection":"UNRUN"}
    async def thread(fn,*args): return fn(*args)
    monkeypatch.setattr(dbmod,"Database",DB); monkeypatch.setattr(replaymod,"PostgresReplay",Replay)
    monkeypatch.setattr(authmod,"AuthorityClient",Authority); monkeypatch.setattr(privmod,"PrivacyRuntime",Gate)
    monkeypatch.setattr(b,"AuthorityConfig",Config); monkeypatch.setattr(b,"paced_replay",paced)
    monkeypatch.setattr(b.serialization,"load_pem_private_key",lambda *args,**kwargs:object())
    monkeypatch.setattr(b,"producer_grant",lambda *args:"token"); monkeypatch.setattr(b,"read_deletion_status",lambda *args:True)
    monkeypatch.setattr(b.asyncio,"sleep",sleep); monkeypatch.setattr(b.asyncio,"to_thread",thread)
    victims=[UUID(int=i) for i in (20,21,22)]
    result=asyncio.run(b.mixed_background(PROFILE,10,PROFILE["seed"],RUN,{},
        {"processor":"postgresql://user@127.0.0.1:57392/analytics_test"},{},Folder(),"bench-test",Monitor(),victims))
    assert ("sleep",300) in trace and trace.count("append")==3
    assert trace.index("gate_stop")<trace.index("append") and trace.count("separate_client")==2
    assert result["time_projection"]=="UNRUN" and len(result["privacy_deletion_sample"]["samples"])==3

def test_mixed_reconciles_after_live_and_hides_subject(monkeypatch):
    subject=UUID(int=42)
    async def seed(*args): return [UUID(int=10),UUID(int=11),UUID(int=12)]
    async def background(*args): return {"replay_subject":subject,"replay_sample":{"status":"PASS"},
        "privacy_deletion_sample":{"status":"PASS","samples":[]},"time_projection":"UNRUN"}
    async def live(*args,**kwargs):
        await kwargs["background_task"]
        return {"oracle":{"status":"PASS"}}
    async def wait(fn,*args,**kwargs): assert fn()
    monkeypatch.setattr(b,"seed_mixed_victims",seed); monkeypatch.setattr(b,"mixed_background",background)
    monkeypatch.setattr(b,"execute_workload",live); monkeypatch.setattr(b,"wait_condition",wait)
    monkeypatch.setattr(b,"read_replay_result",lambda *args:(4,{},"fp")); monkeypatch.setattr(b,"count_browser_facts",lambda *args:4)
    result,extra=asyncio.run(b.execute_mixed(Client(),Processes(),PROFILE,10,PROFILE["seed"],RUN,
        Ed25519PrivateKey.generate(),"issuer","fp",{}, {}, {}, Path("unused"), object(),b.new_stats()))
    assert result["oracle"]["status"]=="PASS" and "replay_subject" not in extra
    assert extra["replay_sample"]["post_quiescence_matches_fact_count"] and extra["background_seed_events"]==3

def test_mixed_failure_cancels_background_without_retry(monkeypatch):
    trace=[]
    async def seed(*args): return [UUID(int=1)]
    async def background(*args):
        try:
            await asyncio.Event().wait()
        finally:
            trace.append("cancelled")
    async def live(*args,**kwargs):
        await asyncio.sleep(0)
        raise b.StopRun("simulated privacy unavailable")
    monkeypatch.setattr(b,"seed_mixed_victims",seed); monkeypatch.setattr(b,"mixed_background",background)
    monkeypatch.setattr(b,"execute_workload",live)
    with pytest.raises(b.StopRun,match="privacy unavailable"):
        asyncio.run(b.execute_mixed(Client(),Processes(),PROFILE,10,PROFILE["seed"],RUN,
            Ed25519PrivateKey.generate(),"issuer","fp",{}, {}, {}, Path("unused"), object(),b.new_stats()))
    assert trace==["cancelled"]

def test_restore_refuses_nonempty_target_before_any_insert(monkeypatch):
    from contextlib import contextmanager
    statements=[]
    class Result:
        def fetchone(self): return (True,)
    class Conn:
        def execute(self,statement,*args): statements.append(str(statement)); return Result()
    @contextmanager
    def guarded(*args,**kwargs): yield Conn()
    monkeypatch.setattr(b,"_guarded_connection",guarded)
    with pytest.raises(b.UnsafeTarget,match="not empty"):
        b.transplant_browser_snapshot({}, {"activity_facts":["{}"],"privacy_state":["{}"]})
    assert len(statements)==1 and "INSERT" not in statements[0]

def test_replay_begin_unknown_is_not_retried():
    class Monitor:
        def check(self): pass
    class Replay:
        calls=0
        async def begin(self,*args):
            self.calls+=1
            raise TimeoutError()
    replay=Replay()
    with pytest.raises(b.StopRun,match="outcome unknown"):
        asyncio.run(b.paced_replay(replay,UUID(int=1),RUN,Monitor()))
    assert replay.calls==1

def test_delete_transport_error_is_unknown_and_not_retried():
    class Monitor:
        def check(self): pass
    calls=[]
    async def append(): calls.append(1); raise OSError("synthetic transport loss")
    async def wait(seq): raise AssertionError("must not poll after unknown append")
    with pytest.raises(b.StopRun,match="outcome unknown"):
        asyncio.run(b.measure_deletion(append,wait,Monitor()))
    assert calls==[1]
