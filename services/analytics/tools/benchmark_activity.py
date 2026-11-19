"""Bounded synthetic Analytics benchmark; no services run without explicit --run.

Only ephemeral child processes and a separately provisioned disposable sentinel DB
are supported. With explicit GO the harness creates and migrates its own database;
it retains that database for reconciliation and never automatically drops it.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import math
import shutil
import os
import platform
from pathlib import Path
import sys
import time
from datetime import datetime, timedelta, timezone
from contextlib import suppress
from uuid import UUID, uuid4, uuid5

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from pymentor_analytics.adapters.auth.config import load_manifest, validate_authority_key_separation
from pymentor_analytics.adapters.privacy_authority.client import AuthorityConfig
from pymentor_analytics.app.config import Settings
from pymentor_analytics.contracts.v2 import AcceptedEvent, BatchAck, BrowserEvent, GrantClaims
from pymentor_analytics.contracts.reports_v1 import ActivityReport
from reference.activity_v1 import calculate as oracle
import bootstrap_postgres as bootstrap
# Reuse the existing Windows job, private owner record, migration and sentinel
# protocol. Import is inert: it opens no database or process.
sys.path.insert(0, str(ROOT / "tests"))
from cross_service import resources as owned
import test_cross_service as cross_service

UTC = timezone.utc
MATCHED_SCHEMAS = ("a010", "a011", "a012", "a014")
COURSE_SCHEMAS = ("a012", "a014")
MODEL_START = datetime(2026, 1, 1, tzinfo=UTC)
STAGES = (10, 15, 25, 50)
REPLICAS = {(1,1,1), (2,1,1), (1,2,1), (1,1,2), (2,2,2)}
FROZEN_PROFILE_SHA256 = "946a55b71ca666b240f67325032d0280e36d72322ebf67dc99493c90e63decbe"

class UnsafeTarget(ValueError): pass
class StopRun(RuntimeError): pass
class ObservedViolation(StopRun): pass

def profile_load(path):
    # A version string alone does not prevent silently changing frozen inputs.
    profile = json.loads(Path(path).read_text(encoding="utf-8"))
    canonical_bytes=(ROOT / "bench/profile-v1.json").read_bytes()
    canonical = json.loads(canonical_bytes)
    encoded=json.dumps(canonical,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode("ascii")
    if hashlib.sha256(encoded).hexdigest()!=FROZEN_PROFILE_SHA256:
        raise ValueError("accepted profile was changed")
    if profile != canonical or profile["version"] != "analytics-synthetic-profile-v1":
        raise ValueError("profile does not match accepted v1")
    return profile

def parse_replicas(value):
    try:
        result=tuple(int(v) for v in value.split(","))
    except (ValueError,AttributeError):
        raise ValueError("replicas must be collector,processor,query") from None
    if result not in REPLICAS:
        raise ValueError("unsupported replica comparison")
    return result

def stream_plan(profile, stage, seed, run_id):
    if stage not in STAGES or type(seed) is not int or seed != profile["seed"] or not isinstance(run_id, UUID) or run_id.version != 4:
        raise ValueError("invalid stage, seed or run identity")
    active=math.ceil(stage*profile["active_ratio"])
    overlapping=math.ceil(active*profile["overlap_ratio"])
    subjects=[uuid5(run_id, f"{seed}:{stage}:user:{i}") for i in range(stage)]
    courses=(f"bench-{run_id.hex}-a",f"bench-{run_id.hex}-b")
    streams=[]
    for index,subject in enumerate(subjects[:active]):
        streams.append({"subject":subject,"user":index,"device":0,"course":courses[0]})
        if index < overlapping:
            streams.append({"subject":subject,"user":index,"device":1,"course":courses[1]})
    return {"subjects":subjects,"courses":courses,"streams":streams,"active":active,"overlapping":overlapping}

def event_for(stream, *, run_id, seed, stage, number, start, kind="activity_segment", cadence_seconds=15, section=None):
    if cadence_seconds not in (15, 60): raise ValueError("unsupported cadence")
    duration=timedelta(seconds=cadence_seconds) if kind=="activity_segment" else timedelta(0)
    event={"schema_version":2,"event_id":str(uuid5(run_id,f"{seed}:{stage}:{stream['user']}:{stream['device']}:{kind}:{number}")),
           "type":kind,"course_id":stream["course"],"catalog_version":"bench-v1",
           "lesson_id":"bench-lesson" if duration else None,
           "section":(section or ("theory" if number%2==0 else "practice")) if duration else None,
           "session_id":str(uuid5(run_id,f"{seed}:{stage}:{stream['user']}:{stream['device']}:session")),
           "started_at":start.isoformat(),"ended_at":(start+duration).isoformat(),"last_action_at":start.isoformat(),
           "visible":True,"focused":True,"active_ms":int(duration.total_seconds()*1000),"payload":{}}
    BrowserEvent.model_validate_json(json.dumps(event))
    return event

def grant(key, *, issuer, kid, subject, role, scope, courses=(), subjects=(), global_read=False, now=None):
    now=int(time.time()) if now is None else now
    value={"grant_version":1,"iss":issuer,"aud":"analytics","sub":str(subject),"iat":now,"nbf":now,"exp":now+50,
           "role":role,"scope":scope,"course_ids":list(courses),"subject_refs":[str(v) for v in subjects],"global_read":global_read}
    GrantClaims.model_validate_json(json.dumps(value))
    return jwt.encode(value,key,algorithm="EdDSA",headers={"typ":"JWT","kid":kid})

def summary(samples):
    values=sorted(samples)
    if not values: return {"count":0,"p50_ms":None,"p95_ms":None}
    def nearest(q): return values[max(0,math.ceil(q*len(values))-1)]
    return {"count":len(values),"p50_ms":nearest(.5),"p95_ms":nearest(.95)}

def verify_database(target, config, *, benchmark_postgres=False):
    """Recheck the owned disposable sentinel, database owner and fresh inputs."""
    name=target["name"]
    if not bootstrap.PATTERN.fullmatch(name): raise UnsafeTarget("not a disposable Analytics DB")
    with bootstrap.connect(config,name,benchmark=benchmark_postgres) as conn:
        if conn.execute("SELECT token FROM public.analytics_test_sentinel").fetchall()!=[(target["sentinel"],)]:
            raise UnsafeTarget("database sentinel mismatch")
        for table in ("activity_facts","processing_jobs","subject_fences","privacy_receipts"):
            if conn.execute(f"SELECT EXISTS(SELECT 1 FROM analytics.{table} LIMIT 1)").fetchone()[0]:
                raise UnsafeTarget("database is not fresh")
        clock=conn.execute("SELECT clock_timestamp()").fetchone()[0]
        if abs((datetime.now(UTC)-clock).total_seconds())>30: raise UnsafeTarget("database clock outside admission bound")
    with bootstrap.connect(config,benchmark=benchmark_postgres) as conn:
        owner=conn.execute("SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname=%s",(name,)).fetchone()
    if owner!=(target["owner"],): raise UnsafeTarget("database owner mismatch")

class OwnedProcesses:
    """Own only child processes in a kill-on-close job and assert their listeners."""
    def __init__(self,urls,replicas,manifest,catalog,authority,folder,owner_token,job,children,*,projection_run_id,matched_schema="a010",activity_poll_ms=None,query_pool_size=2):
        self.urls,self.replicas,self.manifest,self.catalog,self.authority=urls,replicas,manifest,catalog,authority
        self.folder,self.owner_token,self.job,self.children=folder,owner_token,job,children
        if not isinstance(projection_run_id,UUID): raise ValueError("projection generation UUID required")
        if matched_schema not in MATCHED_SCHEMAS: raise ValueError("unsupported projection schema")
        if activity_poll_ms is not None and (type(activity_poll_ms) is not int or not 50<=activity_poll_ms<=500): raise ValueError("invalid activity poll interval")
        if type(query_pool_size) is not int or query_pool_size not in (2,4): raise ValueError("invalid query pool size")
        self.query_pool_size=query_pool_size
        self.projection_run_id=projection_run_id
        self.matched_schema=matched_schema
        self.activity_poll_ms=activity_poll_ms
        self.endpoints={r:[] for r in ("collector","processor","query","privacy")}
        self.processes=[]; self.descriptors=[]
    def start(self):
        base=cross_service.base_env()
        base.update(PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE="1",ANALYTICS_TRUST_MANIFEST_JSON=json.dumps(self.manifest),
                    ANALYTICS_PRIVACY_AUTHORITY_JSON=json.dumps(self.authority),ANALYTICS_FIXTURE_CATALOG_JSON=json.dumps(self.catalog))
        counts=dict(zip(("collector","processor","query"),self.replicas)); counts["privacy"]=1
        ports=[]
        for role,count in counts.items():
            for _ in range(count):
                for attempt in range(20):
                    port=owned.unused_port()
                    if port not in ports: break
                else: raise UnsafeTarget("could not reserve unique loopback ports")
                ports.append(port)
                env=dict(base); env[f"ANALYTICS_{role.upper()}_DATABASE_URL"]=self.urls[role]
                if role=="processor" and self.activity_poll_ms is not None:
                    env["ANALYTICS_PROCESSOR_ACTIVITY_POLL_SECONDS"]=str(self.activity_poll_ms/1000)
                if role in {"processor","query"}:
                    env["ANALYTICS_ACTIVITY_PROJECTION_RUN_ID"]=str(self.projection_run_id)
                    env[f"ANALYTICS_{role.upper()}_ACTIVITY_PROJECTION_ENABLED"]="true"
                    if self.matched_schema != "a010":
                        name="WORKER" if role=="processor" else "QUERY"
                        env[f"ANALYTICS_{role.upper()}_ACTIVITY_DAY_{name}_ENABLED"]="true"
                        if self.matched_schema in COURSE_SCHEMAS:
                            env[f"ANALYTICS_{role.upper()}_ACTIVITY_COURSE_{name}_ENABLED"]="true"
                    if role=="processor" and self.matched_schema=="a014":
                        env["ANALYTICS_PROCESSOR_ACTIVITY_BOUNDARY_CREDIT_ENABLED"]="true"
                pool_size = 1 if role=="privacy" else self.query_pool_size if role=="query" else 2
                env[f"ANALYTICS_{role.upper()}_POOL_SIZE"]=str(pool_size)
                env[f"ANALYTICS_{role.upper()}_POOL_OVERFLOW"]="0"
                def record(processes):
                    owned.record(self.folder,self.owner_token,state="processes_starting",processes=[p.pid for p in processes])
                proc=owned.spawn_owned([sys.executable,"-B","-m","pymentor_analytics.app.cli",role,"--host","127.0.0.1","--port",str(port)],
                    cwd=ROOT,env=env,folder=self.folder,label=f"{role}-{len(self.endpoints[role])}",
                    children=self.children,job=self.job,on_spawn=record)
                self.descriptors.append({"role":role,"index":len(self.endpoints[role]),"pid":proc.pid,
                                         "loopback_port":port,"pool_size":pool_size,"pool_overflow":0})
                self.processes.append(proc); self.endpoints[role].append(f"http://127.0.0.1:{port}")
                owned.wait_port(proc,port)
                self.job.assert_listener(port)
    def stop(self):
        owned.processes_stop(self.children)

def keys_and_catalog(run_id,courses,keys,folder):
    issuer=f"bench-{run_id.hex}"
    manifest={"grant":{"issuer":issuer,"keys":{"bench-g1":keys["grant"]}},
              "producer":{"issuer":issuer+"-control","keys":{"bench-p1":keys["producer"]},"allowlist":["bench-producer"]}}
    catalog=[{"schema_version":2,"kind":"catalog_snapshot","control_id":str(uuid5(run_id,"catalog:"+course)),
              "course_id":course,"catalog_version":"bench-v1","track_id":"bench-track",
              "lessons":[{"lesson_id":"bench-lesson"}]} for course in courses]
    from pydantic import SecretStr
    _,_,fingerprint=load_manifest(SecretStr(json.dumps(manifest)))
    key=serialization.load_pem_private_key((folder/"grant.private.pem").read_bytes(),password=None)
    if not isinstance(key,Ed25519PrivateKey): raise UnsafeTarget("invalid synthetic grant key")
    return key,issuer,manifest,catalog,fingerprint

def authority_start(folder,token,job,children,run_id,keys,secret,issuer):
    port=owned.unused_port(); authority_id=f"bench-{run_id.hex}"
    authority={"url":f"http://127.0.0.1:{port}","authority_id":authority_id,"issuer":issuer+"-authority",
               "public_keys":{"bench-a":keys["authority"]},"reader_secret":secret,"fixture_mode":True}
    AuthorityConfig.model_validate(authority)
    server={"fixture_mode":True,"bind_host":"127.0.0.1","port":port,"issuer":issuer+"-authority","kid":"bench-a",
            "signing_key_file":str(folder/"authority.private.pem"),"producer_issuer":issuer+"-control",
            "producer_public_keys":{"bench-p1":keys["producer"]},"allowed_producers":["bench-producer"],
            "producer_command_public_keys":{"bench-producer":keys["command"]},"reader_secret_file":str(folder/"reader.secret")}
    owned.write_new(folder/"authority.json",server)
    tool=ROOT/"tools/synthetic_privacy_authority.py"
    env=cross_service.base_env(); env.update(PYTHONDONTWRITEBYTECODE="1",CROSS_SERVICE_PRIVATE_DIR=str(folder))
    owned.run_child([sys.executable,"-B",str(tool),"init","--journal",str(folder/"journal.sqlite"),"--authority-id",authority_id],cwd=ROOT,env=env)
    def record(processes): owned.record(folder,token,state="processes_starting",processes=[p.pid for p in processes])
    proc=owned.spawn_owned([sys.executable,"-B",str(tool),"serve","--journal",str(folder/"journal.sqlite"),
                             "--authority-id",authority_id,"--config",str(folder/"authority.json")],
                            cwd=ROOT,env=env,folder=folder,label="authority",children=children,job=job,on_spawn=record)
    owned.wait_port(proc,port); job.assert_listener(port)
    return authority

async def ready(client, processes, fingerprint):
    for role, endpoints in processes.endpoints.items():
        for index, endpoint in enumerate(endpoints):
            source = f"{role}[{index}]"
            try:
                response = await client.get(endpoint + "/ready")
            except httpx.HTTPError:
                raise StopRun(f"{source} readiness transport unavailable") from None
            if response.status_code != 200:
                raise StopRun(f"{source} readiness HTTP {response.status_code}")
            try:
                payload = response.json()
            except ValueError:
                raise StopRun(f"{source} readiness response invalid") from None
            if not isinstance(payload, dict) or payload.get("status") != "ready":
                raise StopRun(f"{source} readiness response invalid")
            if response.headers.get("X-Analytics-Trust-Fingerprint") != fingerprint:
                raise StopRun(f"{source} readiness trust mismatch")
    if any(p.poll() is not None for p in processes.children):
        raise StopRun("owned process exited")

async def await_ready(client, processes, fingerprint):
    deadline=time.monotonic()+30
    last_failure = None
    while time.monotonic()<deadline:
        if any(p.poll() is not None for p in processes.children): raise StopRun("owned process exited before readiness")
        try:
            await ready(client,processes,fingerprint); return
        except StopRun as exc:
            last_failure = str(exc)
            await asyncio.sleep(.25)
    raise StopRun(f"readiness deadline expired ({last_failure})" if last_failure else "readiness deadline expired")

def parse_metrics(text):
    """Only fixed scalar metrics are consumed; labels and event data are ignored."""
    if len(text.encode("utf-8"))>65536: raise StopRun("metrics response too large")
    wanted={"analytics_process_cpu_seconds_total","analytics_process_resident_memory_bytes_known",
            "analytics_process_resident_memory_bytes","analytics_database_pool_checked_out_known",
            "analytics_database_pool_checked_out","analytics_database_pool_max_known","analytics_database_pool_max",
            "analytics_diagnostics_available","analytics_oldest_pending_seconds_known","analytics_oldest_pending_seconds",
            "analytics_database_bytes","analytics_database_connections","analytics_quarantined_jobs"}
    result={}
    for line in text.splitlines():
        parts=line.split()
        if len(parts)!=2: continue
        key=parts[0].split("{",1)[0]
        if key not in wanted: continue
        try: value=float(parts[1])
        except ValueError: raise StopRun("invalid metrics value") from None
        if not math.isfinite(value) or value<0 or key in result: raise StopRun("invalid metrics value")
        result[key]=value
    return result

async def sample_metrics(client,processes):
    rows=[]
    for role,endpoints in processes.endpoints.items():
        for index,endpoint in enumerate(endpoints):
            response=await client.get(endpoint+"/metrics")
            if response.status_code!=200: raise StopRun("metrics unavailable")
            data=parse_metrics(response.text)
            required=("analytics_process_cpu_seconds_total","analytics_database_pool_checked_out_known",
                      "analytics_database_pool_max_known")
            if any(name not in data for name in required): raise StopRun("metrics unavailable")
            if (data["analytics_database_pool_checked_out_known"]!=1 or data["analytics_database_pool_max_known"]!=1
                    or "analytics_database_pool_checked_out" not in data or "analytics_database_pool_max" not in data):
                raise StopRun("database pool metrics unavailable")
            if role=="processor":
                if data.get("analytics_diagnostics_available")!=1 or "analytics_database_connections" not in data:
                    raise StopRun("processor diagnostics unavailable")
                if data["analytics_database_connections"]>40 or data.get("analytics_quarantined_jobs",0)>0:
                    raise StopRun("database connection or quarantine limit exceeded")
            rows.append({"role":role,"index":index,"values":data})
    return rows

def new_stats():
    return {"collector_ms":[],"query_ms":[],"requests":{},"warmup_requests":{},"verification_requests":{},"ack":{},"opening_ack":{},
            "warmup_ack":{},"query_roles":{},"retry_after":[],"timeouts":0,"schedule_lag_ms":[],"metrics":[],"matched_ack_to_admin_ms":[]}

def count(stats,group,value):
    group=stats[group]; group[value]=group.get(value,0)+1

async def execute_workload(client, processes, profile, stage, seed, run_id, key, issuer, fingerprint, *, stats=None, monitor=None, metrics_sampler=None, background_task=None, projection_wait=None, matched_cadence=None, measurement_boundary=None, query_failure_diagnostic=None, matched_smoke=False):
    plan=stream_plan(profile,stage,seed,run_id)
    if matched_cadence not in (None, 15, 60): raise ValueError("unsupported matched cadence")
    if measurement_boundary is not None and matched_cadence is None: raise ValueError("boundary sampling requires matched cadence")
    warmup=profile["warmup_seconds"]; duration=profile["measurement_seconds"]; heartbeat=profile["heartbeat_seconds"]
    if type(matched_smoke) is not bool or (matched_smoke and matched_cadence is None): raise ValueError("smoke requires matched cadence")
    if (warmup,duration,heartbeat)!=((60,120,15) if matched_smoke else (300,900,15)): raise ValueError("unexpected fixed time profile")
    # Wall-clock anchor translates the fixed Jan-2026 model without changing
    # interval lengths; no server clock is mocked or altered.
    real_start=datetime.now(UTC).replace(microsecond=0)
    measure_start=real_start+timedelta(seconds=warmup)
    measure_end=measure_start+timedelta(seconds=duration)
    wall_anchor=time.monotonic()
    stats=new_stats() if stats is None else stats
    accepted=[]; collector_idx=query_idx=0; tick=0; diagnostic_task=None; tick_acks=[]
    role_names=("collector","processor","query","privacy")
    for role in role_names:
        if not processes.endpoints[role]: raise StopRun("missing component")
    leader_ids=[uuid5(run_id,"leader:"+str(i)) for i in range(2)]
    admin=uuid5(run_id,"admin")
    courses=plan["courses"]

    async def call(method,url,*,headers=None,payload=None,params=None,kind,measured,verification=False,diagnostic_context=None):
        nonlocal diagnostic_task
        started=time.monotonic()
        try:
            response=await client.request(method,url,headers=headers,json=payload,params=params)
        except httpx.TimeoutException:
            stats["timeouts"]+=1
            raise StopRun("request timeout; outcome unknown, reconcile before retry") from None
        except httpx.HTTPError:
            raise StopRun("transport unavailable; outcome unknown, reconcile before retry") from None
        elapsed=(time.monotonic()-started)*1000
        group="requests" if measured else "verification_requests" if verification else "warmup_requests"
        count(stats,group,f"{kind}:{response.status_code}")
        if response.status_code==429: stats["retry_after"].append(response.headers.get("Retry-After"))
        if response.status_code==503:
            if kind=="query" and query_failure_diagnostic is not None:
                if diagnostic_task is None:
                    diagnostic_context["elapsed_seconds"]=time.monotonic()-wall_anchor
                    async def capture():
                        try: return await query_failure_diagnostic(diagnostic_context)
                        except Exception: return {"status":"UNAVAILABLE"}
                    diagnostic_task=asyncio.create_task(capture())
                stats["query_failure_diagnostic"]=await asyncio.shield(diagnostic_task)
            raise StopRun("unavailable/privacy; stop without retry")
        if response.status_code==429 and matched_cadence is not None:
            raise StopRun(f"{kind} HTTP 429; matched workload incomplete")
        if response.status_code==429:
            return None
        if response.status_code!=200:
            raise StopRun(f"{kind} HTTP error {response.status_code}")
        return response,elapsed

    async def send(stream,event,measured):
        nonlocal collector_idx
        endpoint=processes.endpoints["collector"][collector_idx%len(processes.endpoints["collector"])]; collector_idx+=1
        token=grant(key,issuer=issuer,kid="bench-g1",subject=stream["subject"],role="user",scope="collect",courses=(stream["course"],))
        outcome=await call("POST",endpoint+"/api/analytics/events/batch",headers={"Authorization":"Bearer "+token},payload={"events":[event]},kind="collector",measured=measured)
        if outcome is None: return
        response,elapsed=outcome
        ack_time=time.monotonic()
        try:
            ack=BatchAck.model_validate_json(response.content)
            if len(ack.items)!=1 or str(ack.items[0].event_id)!=event["event_id"]: raise ValueError
            status=ack.items[0].status
        except (ValueError,TypeError): raise ObservedViolation("invalid durable ACK") from None
        group="opening_ack" if event["type"]=="course_opened" else "ack" if measured else "warmup_ack"
        count(stats,group,status)
        if status!="accepted": raise ObservedViolation("unexpected durable ACK outcome")
        if matched_cadence is not None and measured and tick%4==0:
            tick_acks.append(ack_time)
        if measured: stats["collector_ms"].append(elapsed)
        accepted.append(AcceptedEvent.model_validate_json(json.dumps({"producer_namespace":"browser:"+str(stream["subject"]),
            "subject_ref":str(stream["subject"]),"source":"browser","event":event,"received_at":event["ended_at"]})))

    async def report(role,subject,scope_courses,roster=(),course=None,measured=True,verification=False):
        nonlocal query_idx
        endpoint_index=query_idx%len(processes.endpoints["query"])
        endpoint=processes.endpoints["query"][endpoint_index]; query_idx+=1
        token=grant(key,issuer=issuer,kid="bench-g1",subject=subject,role=role,scope="report:read",
                    courses=scope_courses,subjects=roster,global_read=role=="admin")
        if measured: count(stats,"query_roles",role)
        now=datetime.now(UTC); now=now.replace(microsecond=(now.microsecond//1000)*1000)
        report_start = real_start if matched_cadence is not None and not measured and not verification else measure_start
        params={"from_time":report_start.isoformat(),"to_time":max(now,report_start+timedelta(milliseconds=1)).isoformat()}
        if course is not None: params["course_id"]=course
        context={"role":role,"endpoint_index":endpoint_index,"tick":tick,"elapsed_seconds":time.monotonic()-wall_anchor,
                 "phase":"verification" if verification else "measurement" if measured else "warmup",
                 "from_time":report_start,"to_time":datetime.fromisoformat(params["to_time"]),
                 "subjects":None if role=="admin" else tuple(roster) if role=="leader" else (subject,)}
        outcome=await call("GET",endpoint+"/api/analytics/reports/activity",headers={"Authorization":"Bearer "+token},params=params,kind="query",measured=measured,verification=verification,diagnostic_context=context)
        if outcome is None: return None
        response,elapsed=outcome
        try: result=ActivityReport.model_validate_json(response.content)
        except ValueError: raise ObservedViolation("invalid report response") from None
        if result.status!="ok" or result.quality not in {"unknown","partial","complete"}: raise ObservedViolation("unexpected report status")
        if measured: stats["query_ms"].append(elapsed)
        if matched_cadence is not None and measured and tick%4==0 and role=="admin" and tick_acks:
            stats["matched_ack_to_admin_ms"].append((time.monotonic()-min(tick_acks))*1000)
        return result

    async def wait_at(seconds):
        delay=wall_anchor+seconds-time.monotonic()
        if delay>0: await asyncio.sleep(delay)
        lag=max(0,(time.monotonic()-wall_anchor-seconds)*1000)
        stats["schedule_lag_ms"].append(lag)
        if lag >= heartbeat*1000:
            raise StopRun("missed heartbeat; do not compress elapsed load")
        if abs((datetime.now(UTC)-(real_start+timedelta(seconds=time.monotonic()-wall_anchor))).total_seconds())>30:
            raise StopRun("wall clock changed during run")

    if metrics_sampler is not None: stats["metrics"].append(await metrics_sampler())
    async def send_batch(events, measured):
        if matched_cadence is None:
            await asyncio.gather(*(send(s,e,measured) for s,e in events))
        else:
            # Identical client concurrency of one in both matched variants.
            for s,e in events: await send(s,e,measured)
    await send_batch([(s,event_for(s,run_id=run_id,seed=seed,stage=stage,number=0,start=real_start,kind="course_opened")) for s in plan["streams"]],False)
    for tick in range(1,(warmup+duration)//heartbeat+1):
        tick_acks.clear()
        await wait_at(tick*heartbeat)
        if monitor is not None: monitor.check()
        await ready(client,processes,fingerprint)
        if background_task is not None and background_task.done():
            background_task.result()
        if metrics_sampler is not None and tick%4==0: stats["metrics"].append(await metrics_sampler())
        segment_start=real_start+timedelta(seconds=(tick-1)*heartbeat)
        measured=segment_start>=measure_start
        cadence=matched_cadence or heartbeat
        if tick%(cadence//heartbeat)==0:
            segment_start=real_start+timedelta(seconds=tick*heartbeat-cadence)
            section=("practice" if ((tick-1)//4)%2==0 else "theory") if matched_cadence else None
            await send_batch([(s,event_for(s,run_id=run_id,seed=seed,stage=stage,number=tick,start=segment_start,
                cadence_seconds=cadence,section=section)) for s in plan["streams"]],measured)
        queries=[]
        if tick%2==0:
            for i,course in enumerate(courses):
                roster=tuple(plan["subjects"][:plan["active"]])
                queries.append(report("leader",leader_ids[i],(course,),roster=roster,course=course,measured=measured))
            queries.append(report("admin",admin,(),measured=measured))
        if tick%4==0:
            queries.extend(report("user",subject,courses,measured=measured) for subject in plan["subjects"][:plan["active"]])
        if queries: await asyncio.gather(*queries)
        if measurement_boundary is not None and tick in (warmup//heartbeat,(warmup+duration)//heartbeat):
            label="start_after_warmup_tick" if tick==warmup//heartbeat else "end_after_final_tick"
            await measurement_boundary(label, time.monotonic()-wall_anchor)
    measurement_elapsed=max(0,time.monotonic()-wall_anchor-warmup)
    if projection_wait is not None: await projection_wait()
    final=await report("admin",admin,(),measured=False,verification=True)
    parity={"status":"UNRUN","reason":"no accepted observations"}
    if final is not None and final.as_of is not None:
        expected=oracle(accepted,start=measure_start,end=measure_end,as_of=final.as_of,input_complete=False)
        values=final.totals
        actual={"unique_visitors":values.unique_visitors,"active_ms":values.active_ms,"theory_ms":values.theory_ms,"practice_ms":values.practice_ms}
        wanted={"unique_visitors":expected["visitors"],"active_ms":expected["total_ms"],"theory_ms":expected["theory_ms"],"practice_ms":expected["practice_ms"]}
        parity={"status":"PASS" if actual==wanted else "FAIL","expected":wanted,"observed":actual,"coverage":final.quality}
    return {"model_start":MODEL_START.isoformat(),"actual_start":real_start.isoformat(),
            "translation_seconds":(real_start-MODEL_START).total_seconds(),"measurement_start":measure_start.isoformat(),
            "measurement_end":measure_end.isoformat(),"elapsed_seconds":time.monotonic()-wall_anchor,
            "measurement_elapsed_seconds":measurement_elapsed,
            "active_users":plan["active"],"overlap_users":plan["overlapping"],"streams":len(plan["streams"]),
            "collector":summary(stats["collector_ms"]),"query":summary(stats["query_ms"]),
            "requests":stats["requests"],"warmup_requests":stats["warmup_requests"],
            "verification_requests":stats["verification_requests"],"ack":stats["ack"],
            "opening_ack":stats["opening_ack"],"warmup_ack":stats["warmup_ack"],"query_roles":stats["query_roles"],
            "retry_after":stats["retry_after"],"timeouts":stats["timeouts"],
            "expected_openings":len(plan["streams"]),
            "expected_warmup_segments":len(plan["streams"])*(warmup//(matched_cadence or heartbeat)),
            "expected_measurement_segments":len(plan["streams"])*(duration//(matched_cadence or heartbeat)),
            "expected_measurement_queries":plan["active"]*(duration//60)+3*(duration//30),
            "max_schedule_lag_ms":max(stats["schedule_lag_ms"],default=0),"metrics":stats["metrics"],"oracle":parity,
            "matched_ack_to_admin": {"status":"OBSERVED" if stats["matched_ack_to_admin_ms"] else "UNRUN",
                "scope":"earliest_accepted_ack_to_successful_admin_response_same_60s_measurement_tick",
                "note":"Elapsed includes remaining serial batch and query; response does not prove inclusion or freshness of the accepted event.",
                "summary_ms":summary(stats["matched_ack_to_admin_ms"]),
                "max_ms":max(stats["matched_ack_to_admin_ms"],default=None)}}

async def execute_correctness(client,processes,profile,stage,seed,run_id,key,issuer,*,monitor=None,projection_wait=None):
    """Separate synthetic HTTP run, no tracker/browser claim and no load-rate claim."""
    plan=stream_plan(profile,stage,seed,run_id)
    subject=plan["subjects"][0]
    streams=({"subject":subject,"user":0,"device":0,"course":plan["courses"][0]},
             {"subject":subject,"user":0,"device":1,"course":plan["courses"][1]})
    now=datetime.now(UTC).replace(microsecond=0)
    base=now-timedelta(hours=1)
    opened=event_for(streams[0],run_id=run_id,seed=seed,stage=stage,number=0,start=base,kind="course_opened")
    first=event_for(streams[0],run_id=run_id,seed=seed,stage=stage,number=1,start=base)
    competing=event_for(streams[1],run_id=run_id,seed=seed,stage=stage,number=1,start=base+timedelta(seconds=5))
    resumed=event_for(streams[0],run_id=run_id,seed=seed,stage=stage,number=2,start=base+timedelta(seconds=120))
    conflict=dict(first); conflict["section"]="theory" if first["section"]=="practice" else "practice"
    responses=[]; accepted=[]
    async def send(stream,event,expected):
        if monitor is not None: monitor.check()
        token=grant(key,issuer=issuer,kid="bench-g1",subject=subject,role="user",scope="collect",courses=(stream["course"],))
        try:
            response=await client.post(processes.endpoints["collector"][0]+"/api/analytics/events/batch",
                headers={"Authorization":"Bearer "+token},json={"events":[event]})
        except httpx.HTTPError: raise StopRun("correctness outcome unknown; no retry") from None
        if response.status_code!=200: raise StopRun("correctness collector unavailable or overloaded")
        try:
            ack=BatchAck.model_validate_json(response.content)
            if len(ack.items)!=1 or str(ack.items[0].event_id)!=event["event_id"]: raise ValueError
            status=ack.items[0].status
        except (ValueError,TypeError): raise StopRun("invalid correctness ACK") from None
        responses.append(status)
        if status!=expected: raise ObservedViolation("correctness ACK mismatch")
        if status=="accepted":
            accepted.append(AcceptedEvent.model_validate_json(json.dumps({"producer_namespace":"browser:"+str(subject),
                "subject_ref":str(subject),"source":"browser","event":event,"received_at":event["ended_at"]})))
    for stream,event,status in ((streams[0],opened,"accepted"),(streams[0],first,"accepted"),
                                (streams[1],competing,"accepted"),(streams[0],resumed,"accepted"),
                                (streams[0],first,"duplicate"),(streams[0],conflict,"conflict")):
        await send(stream,event,status)
    if projection_wait is not None: await projection_wait()
    token=grant(key,issuer=issuer,kid="bench-g1",subject=subject,role="user",scope="report:read",courses=plan["courses"])
    end=base+timedelta(seconds=135)
    try:
        response=await client.get(processes.endpoints["query"][0]+"/api/analytics/reports/activity",
            headers={"Authorization":"Bearer "+token},params={"from_time":base.isoformat(),"to_time":end.isoformat()})
    except httpx.HTTPError: raise StopRun("correctness query unavailable") from None
    if response.status_code!=200: raise StopRun("correctness query unavailable")
    report=ActivityReport.model_validate_json(response.content)
    if report.status!="ok" or report.as_of is None or report.totals is None:
        raise StopRun("correctness report unavailable")
    expected=oracle(accepted,start=base,end=end,as_of=report.as_of,input_complete=False)
    observed={"unique_visitors":report.totals.unique_visitors,"active_ms":report.totals.active_ms,
              "theory_ms":report.totals.theory_ms,"practice_ms":report.totals.practice_ms}
    wanted={"unique_visitors":expected["visitors"],"active_ms":expected["total_ms"],
            "theory_ms":expected["theory_ms"],"practice_ms":expected["practice_ms"]}
    return {"status":"PASS" if observed==wanted else "FAIL","acks":responses,"expected":wanted,
            "observed":observed,"coverage":report.quality,"late_by_seconds":3600,
            "scenarios":["late_within_7_days","cross_course_overlap","unobserved_gap","duplicate","conflict"],
            "tracker_visibility":"UNRUN"}

async def paced_replay(replay,subject,request_id,monitor,*,deadline_seconds=120):
    """Exercise the existing synthetic consumer only, <=1 scanned fact per 2s.

    A timeout is an unknown outcome. The caller must reconcile by request_id;
    no implicit retry or second request identity is used.
    """
    deadline=time.monotonic()+deadline_seconds
    monitor.check()
    try:
        state=await asyncio.wait_for(replay.begin("synthetic.activity",1,subject,request_id),timeout=5)
        original=state.handle
        duplicate=await asyncio.wait_for(replay.begin("synthetic.activity",1,subject,request_id),timeout=5)
    except Exception:
        raise StopRun("replay outcome unknown; reconcile original request") from None
    if duplicate.handle!=original: raise StopRun("replay request identity mismatch")
    steps=[]
    while state.phase!="complete":
        if time.monotonic()>=deadline or len(steps)>=60: raise StopRun("replay deadline exceeded; reconcile original request")
        monitor.check()
        if steps:
            delay=2-(time.monotonic()-steps[-1])
            if delay>0: await asyncio.sleep(delay)
        steps.append(time.monotonic())
        try:
            if state.phase in {"cleanup","scan"}:
                state=await asyncio.wait_for(replay.step(original,limit=1),timeout=min(5,max(.001,deadline-time.monotonic())))
            elif state.phase=="drain":
                state=await asyncio.wait_for(replay.finish(original),timeout=min(5,max(.001,deadline-time.monotonic())))
            else: raise StopRun("unexpected replay phase")
        except StopRun:
            raise
        except Exception:
            raise StopRun("replay outcome unknown; reconcile original request") from None
    intervals=[steps[i]-steps[i-1] for i in range(1,len(steps))]
    if intervals and min(intervals)<2: raise StopRun("replay quota exceeded")
    return {"status":"PASS","consumer":"synthetic.activity","version":1,"step_limit":1,
            "minimum_step_interval_seconds":min(intervals) if intervals else None,"steps":len(steps),
            "request_id_reused":True,"time_projection":"UNRUN"}

async def measure_deletion(append_once,wait_until_purged,monitor,*,deadline_seconds=60):
    """One journal command, then observation only; never append again on timeout."""
    monitor.check(); started=time.monotonic()
    try:
        async with asyncio.timeout(deadline_seconds):
            sequence=await append_once()
            if type(sequence) is not int or sequence<1: raise StopRun("invalid signed journal receipt")
            await wait_until_purged(sequence)
    except TimeoutError:
        raise StopRun("deletion outcome unknown; reconcile control ID") from None
    except StopRun:
        raise
    except Exception:
        raise StopRun("deletion outcome unknown; reconcile control ID") from None
    elapsed=time.monotonic()-started
    return {"status":"PASS","completion_seconds":elapsed,"sequence":sequence}

async def seed_mixed_victims(client,processes,profile,stage,seed,run_id,key,issuer,monitor):
    """Three extra controlled facts; they precede warmup and are not live-rate samples."""
    plan=stream_plan(profile,stage,seed,run_id); course=plan["courses"][0]
    subjects=[uuid5(run_id,f"mixed-victim:{i}") for i in range(3)]
    base=datetime.now(UTC).replace(microsecond=0)-timedelta(minutes=1)
    for i,subject in enumerate(subjects):
        monitor.check()
        stream={"subject":subject,"user":stage+i,"device":0,"course":course}
        event=event_for(stream,run_id=run_id,seed=seed,stage=stage,number=1,start=base)
        token=grant(key,issuer=issuer,kid="bench-g1",subject=subject,role="user",scope="collect",courses=(course,))
        try:
            response=await client.post(processes.endpoints["collector"][0]+"/api/analytics/events/batch",
                                       headers={"Authorization":"Bearer "+token},json={"events":[event]})
        except httpx.HTTPError: raise StopRun("mixed seed outcome unknown; reconcile") from None
        if response.status_code!=200: raise StopRun("mixed seed unavailable")
        ack=BatchAck.model_validate_json(response.content)
        if len(ack.items)!=1 or ack.items[0].status!="accepted" or str(ack.items[0].event_id)!=event["event_id"]:
            raise StopRun("mixed seed ACK mismatch")
    return subjects

def count_browser_facts(target,subject):
    with _guarded_connection(target) as conn:
        return conn.execute("SELECT count(*) FROM analytics.activity_facts WHERE subject_ref=%s AND source_kind='browser_v2'",(subject,)).fetchone()[0]

async def mixed_background(profile,stage,seed,run_id,authority,urls,target,folder,issuer,monitor,victims):
    """Replay then three deletion samples while the separate live workload runs."""
    from pymentor_analytics.adapters.postgres.database import Database
    from pymentor_analytics.adapters.postgres.replay import PostgresReplay
    from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient
    from pymentor_analytics.app.privacy import PrivacyRuntime
    from pymentor_analytics.contracts.v2 import DeletionFence
    await asyncio.sleep(profile["warmup_seconds"])
    plan=stream_plan(profile,stage,seed,run_id)
    # A non-overlap user bounds the first replay to one stream while live continues.
    subject=plan["subjects"][plan["overlapping"]]
    database=Database(Settings(database_url=urls["processor"],pool_size=1,pool_overflow=0,db_timeout_seconds=5))
    gate=PrivacyRuntime(AuthorityClient(AuthorityConfig.model_validate(authority)),database)
    database.transaction_guard=gate.check_in_transaction
    gate.start()
    try:
        deadline=time.monotonic()+30
        while not await gate.is_ready():
            if time.monotonic()>=deadline: raise StopRun("mixed replay privacy gate unavailable")
            monitor.check(); await asyncio.sleep(.25)
        replay=await paced_replay(PostgresReplay(database),subject,uuid5(run_id,"mixed-replay"),monitor,deadline_seconds=180)
    finally:
        await gate.stop(); await database.close()
    pkey=serialization.load_pem_private_key((folder/"producer.private.pem").read_bytes(),password=None)
    ckey=serialization.load_pem_private_key((folder/"command.private.pem").read_bytes(),password=None)
    control_client=AuthorityClient(AuthorityConfig.model_validate(authority))
    samples=[]
    for index,victim in enumerate(victims):
        control=DeletionFence(schema_version=2,kind="deletion_fence",control_id=uuid5(run_id,f"mixed-delete:{index}"),
                              subject_ref=victim,epoch=1,occurred_at=datetime.now(UTC))
        async def append_once(control=control):
            receipt=await control_client.append(control,producer="bench-producer",producer_token=producer_grant(pkey,issuer),command_key=ckey)
            return receipt.seq
        async def wait_purge(sequence,control=control,victim=victim):
            await wait_condition(lambda:read_deletion_status(target,victim,control.control_id,sequence),monitor,timeout=55)
        samples.append(await measure_deletion(append_once,wait_purge,monitor))
    return {"replay_sample":replay,"privacy_deletion_sample":{"status":"PASS","samples":samples},
            "replay_subject":subject,"time_projection":"UNRUN"}

async def execute_mixed(client,processes,profile,stage,seed,run_id,key,issuer,fingerprint,
                        authority,urls,target,folder,monitor,stats,*,projection_wait=None):
    """One full-duration live workload with a bounded replay and deletion samples."""
    victims=await seed_mixed_victims(client,processes,profile,stage,seed,run_id,key,issuer,monitor)
    background=asyncio.create_task(mixed_background(profile,stage,seed,run_id,authority,urls,target,folder,issuer,monitor,victims))
    try:
        live=await execute_workload(client,processes,profile,stage,seed,run_id,key,issuer,fingerprint,
                                   stats=stats,monitor=monitor,metrics_sampler=lambda:sample_metrics(client,processes),background_task=background,
                                   projection_wait=projection_wait)
        try:
            other=await asyncio.wait_for(asyncio.shield(background),timeout=30)
        except TimeoutError:
            raise StopRun("mixed background outcome unknown; reconcile") from None
        subject=other.pop("replay_subject")
        def caught_up():
            projected=read_replay_result(target,subject)
            return projected is not None and projected[0]==count_browser_facts(target,subject)
        await wait_condition(caught_up,monitor,timeout=30)
        other["replay_sample"]["post_quiescence_matches_fact_count"]=True
        other["background_seed_events"]=3
        return live,other
    finally:
        if not background.done():
            background.cancel()
            with suppress(asyncio.CancelledError): await background

def _guarded_connection(target, *, write=False):
    """Connections are allowed only to this run's exact disposable owner/sentinel."""
    from contextlib import contextmanager
    import psycopg
    @contextmanager
    def open_connection():
        if not bootstrap.PATTERN.fullmatch(target["name"]): raise UnsafeTarget("unsafe recovery database")
        options="-c statement_timeout=3000 -c lock_timeout=1000"
        if not write: options+=" -c default_transaction_read_only=on"
        with psycopg.connect(target["dsn"],connect_timeout=3,options=options) as conn:
            identity=conn.execute("SELECT current_database(),pg_get_userbyid(datdba) FROM pg_database WHERE datname=current_database()").fetchone()
            marker=conn.execute("SELECT token FROM public.cross_service_sentinel").fetchall()
            if identity!=(target["name"],target["owner"]) or marker!=[(target["sentinel"],)]:
                raise UnsafeTarget("recovery sentinel or owner mismatch")
            yield conn
    return open_connection()

def measurement_database_snapshot(target):
    """Read only owned synthetic metadata; WAL is cluster-wide, not DB-scoped."""
    with _guarded_connection(target) as conn:
        conn.execute("SELECT pg_stat_clear_snapshot()")
        db=conn.execute("SELECT pg_database_size(current_database()), stats_reset FROM pg_stat_database WHERE datname=current_database()").fetchone()
        wal=conn.execute("SELECT wal_bytes, stats_reset FROM pg_stat_wal").fetchone()
        rows=conn.execute("""SELECT relname, relid, n_tup_ins, n_tup_upd, n_tup_del,
                   pg_table_size(relid), pg_indexes_size(relid), n_live_tup
            FROM pg_stat_user_tables WHERE schemaname='analytics' ORDER BY relname""").fetchall()
        if not db or not wal or not rows: raise ValueError("statistics unavailable")
        return {"database_bytes":int(db[0]),"database_reset":str(db[1]),
                "wal_bytes":int(wal[0]),"wal_reset":str(wal[1]),
                "tables":{name:{"oid":int(oid),"insert":int(ins),"update":int(upd),"delete":int(delete),
                                "table_bytes_including_toast":int(table_bytes),"index_bytes":int(index_bytes),
                                "estimated_live_rows":int(live_rows)}
                          for name,oid,ins,upd,delete,table_bytes,index_bytes,live_rows in rows}}

def query_failure_snapshot(target,run_id,context):
    """Post-response, bounded aggregate only; never emit subjects or secrets."""
    with _guarded_connection(target) as conn:
        subjects=context["subjects"]
        rows=conn.execute("""SELECT state,count(*),count(*) FILTER (WHERE projection_generation IS DISTINCT FROM generation)
            FROM analytics.activity_projection_buckets
            WHERE bucket_start>=to_timestamp(floor(extract(epoch FROM %s::timestamptz)/900)*900)
              AND bucket_start<LEAST(%s::timestamptz,statement_timestamp())
              AND %s::timestamptz<LEAST(%s::timestamptz,statement_timestamp())
              AND (%s::boolean OR subject_ref=ANY(%s::uuid[])) GROUP BY state""",
            (context["from_time"],context["to_time"],context["from_time"],context["to_time"],subjects is None,list(subjects or ()))).fetchall()
        backfill=conn.execute("SELECT phase,run_id=%s FROM analytics.activity_projection_backfill WHERE singleton=true",(run_id,)).fetchone()
        privacy=conn.execute("SELECT count(*),min(applied_through) FROM analytics.privacy_state").fetchone()
        receipts=conn.execute("SELECT count(*) FROM analytics.privacy_receipts WHERE phase<>'complete'").fetchone()
        return {"scope":"post_response_scoped_bucket_snapshot","bucket_states":{str(state):int(count) for state,count,_ in rows},
                "generation_mismatches":sum(int(mismatch) for _,_,mismatch in rows),
                "backfill_phase":backfill[0] if backfill else None,"backfill_run_matches":bool(backfill[1]) if backfill else False,
                "privacy_state_rows":int(privacy[0]),"privacy_applied_through":privacy[1],"pending_privacy_receipts":int(receipts[0])}

def query_failure_log_reasons(folder,index):
    """Read only the bounded tail of this owned process; discard all other text."""
    allowed={"projection_pending_timeout","projection_invalid","privacy_unavailable","fence_unavailable",
             "report_limit","report_deadline","query_not_ready","query_failure"}
    path=folder/f"query-{index}.log"
    with path.open("rb") as stream:
        stream.seek(0,2); stream.seek(max(0,stream.tell()-65536))
        tail=stream.read(65536).decode("utf-8",errors="replace")
    reasons=[]
    for line in tail.splitlines():
        marker="activity_report_unavailable reason="
        if marker in line:
            words=line.split(marker,1)[1].split()
            if words and words[0] in allowed: reasons.append(words[0])
    return reasons[-8:]

def summarize_measurement_boundaries(samples):
    """Never infer deltas across reset, replacement, missing or incomplete samples."""
    result={"status":"UNRUN","wal_scope":"cluster","table_scope":"owned_database_analytics_schema",
            "note":"PostgreSQL statistics can lag; WAL includes other cluster writers.",
            "boundaries":[{"label":s["label"],"elapsed_seconds":s["elapsed_seconds"],"status":s["status"]} for s in samples]}
    if len(samples)!=2 or [s["label"] for s in samples]!=["start_after_warmup_tick","end_after_final_tick"] or any(s["status"]!="OBSERVED" for s in samples): return result
    a,b=(s["database"] for s in samples)
    if (a["database_reset"]!=b["database_reset"] or a["wal_reset"]!=b["wal_reset"]
            or b["wal_bytes"]<a["wal_bytes"] or set(a["tables"])!=set(b["tables"])): return result
    tables={}; footprint={}
    for name,old in a["tables"].items():
        new=b["tables"][name]
        if old["oid"]!=new["oid"] or any(new[k]<old[k] for k in ("insert","update","delete")): return result
        tables[name]={k:new[k]-old[k] for k in ("insert","update","delete")}
        fields=("table_bytes_including_toast","index_bytes","estimated_live_rows")
        if all(k in old and k in new for k in fields):
            footprint[name]={"start":{k:old[k] for k in fields},"end":{k:new[k] for k in fields}}
    cpu={}
    for sample in samples:
        values={(m["role"],m["index"]):m["values"]["analytics_process_cpu_seconds_total"] for m in sample["cpu"]}
        if len(values)!=len(sample["cpu"]): return result
        if sample is samples[0]: before=values
        elif set(values)!=set(before) or any(values[k]<before[k] for k in before): return result
        else: cpu={f"{k[0]}:{k[1]}":values[k]-before[k] for k in before}
    result.update(status="OBSERVED",database_bytes_start=a["database_bytes"],database_bytes_end=b["database_bytes"],
                  cluster_wal_bytes_delta=b["wal_bytes"]-a["wal_bytes"],table_writes=tables,process_cpu_seconds_delta=cpu,
                  relation_footprint=footprint,relation_footprint_note="pg_table_size includes TOAST and table auxiliary forks; pg_indexes_size covers table indexes; live row counts are estimates and may lag.")
    return result

def initialize_projection(target,run_id):
    """Begin a dataset generation only in this run's fresh, verified database."""
    if not isinstance(run_id,UUID): raise ValueError("projection generation UUID required")
    with _guarded_connection(target,write=True) as conn:
        if conn.execute("SELECT current_user").fetchone()!=(target["owner"],):
            raise UnsafeTarget("projection initialization requires owned migrate role")
        conn.execute("SELECT analytics.begin_activity_projection_backfill(NULL,%s)",(run_id,))

def projection_drained(target,run_id,*,matched_schema="a010"):
    if matched_schema not in MATCHED_SCHEMAS: raise ValueError("unsupported projection schema")
    with _guarded_connection(target) as conn:
        buckets=conn.execute("""SELECT EXISTS(SELECT 1 FROM analytics.activity_projection_backfill
                WHERE singleton=true AND phase='complete' AND run_id=%s)
                AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_buckets WHERE state<>'ready')""",(run_id,)).fetchone()==(True,)
        if not buckets or matched_schema=="a010": return buckets
        day=conn.execute("""SELECT EXISTS(SELECT 1 FROM analytics.activity_projection_day_backfill
                WHERE singleton=true AND run_id=%s AND phase='complete')
                AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_day_sources WHERE state<>'ready')
                AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_day_sources s
                  LEFT JOIN analytics.activity_projection_days d USING(subject_ref,local_day,metric_version)
                  WHERE d.subject_ref IS NULL OR d.run_id IS DISTINCT FROM %s OR d.generation IS DISTINCT FROM s.generation)""",(run_id,run_id)).fetchone()==(True,)
        if not day or matched_schema=="a011": return day
        return conn.execute("""SELECT NOT EXISTS(SELECT 1 FROM analytics.activity_projection_buckets
                    WHERE course_projection_generation IS DISTINCT FROM generation OR course_projection_run_id IS DISTINCT FROM %s)
                AND NOT EXISTS(SELECT 1 FROM analytics.activity_projection_days
                    WHERE course_projection_generation IS DISTINCT FROM generation OR course_projection_run_id IS DISTINCT FROM %s)""",(run_id,run_id)).fetchone()==(True,)

def read_replay_result(target,subject):
    with _guarded_connection(target) as conn:
        return conn.execute("""SELECT covered_count,courses,fingerprint_xor FROM analytics.processing_results
            WHERE consumer='synthetic.activity' AND consumer_version=1 AND subject_ref=%s""",(subject,)).fetchone()

def browser_snapshot(target,subject):
    """One bounded synthetic subject and privacy cursor, not a full backup."""
    with _guarded_connection(target) as conn:
        facts=[row[0] for row in conn.execute("""SELECT row_to_json(f)::text FROM analytics.activity_facts f
            WHERE subject_ref=%s AND source_kind='browser_v2' ORDER BY producer_namespace,event_id LIMIT 101""",(subject,))]
        state=[row[0] for row in conn.execute("SELECT row_to_json(p)::text FROM analytics.privacy_state p")]
    if not 1<=len(facts)<=100 or len(state)!=1: raise UnsafeTarget("snapshot bounds or privacy state invalid")
    return {"activity_facts":facts,"privacy_state":state}

def transplant_browser_snapshot(target,snapshot):
    """Only insert our in-memory snapshot into an empty, freshly owned database."""
    from psycopg import sql
    if set(snapshot)!={"activity_facts","privacy_state"} or not 1<=len(snapshot["activity_facts"])<=100 or len(snapshot["privacy_state"])!=1:
        raise UnsafeTarget("invalid recovery snapshot")
    with _guarded_connection(target,write=True) as conn:
        for table in ("activity_facts","privacy_state"):
            if conn.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM analytics.{} LIMIT 1)").format(sql.Identifier(table))).fetchone()[0]:
                raise UnsafeTarget("restore target is not empty")
            for row in snapshot[table]:
                json.loads(row)
                conn.execute(sql.SQL("INSERT INTO analytics.{} SELECT (jsonb_populate_record(NULL::analytics.{}, %s::jsonb)).*")
                             .format(sql.Identifier(table),sql.Identifier(table)),(row,))

def read_deletion_status(target,subject,control,sequence):
    with _guarded_connection(target) as conn:
        row=conn.execute("""SELECT r.phase,p.applied_through,f.deleted,
            (SELECT count(*) FROM analytics.activity_facts a WHERE a.subject_ref=%s) AS facts,
            (SELECT count(*) FROM analytics.processing_jobs j WHERE j.subject_ref=%s) AS jobs,
            (SELECT count(*) FROM analytics.processing_receipts q WHERE q.subject_ref=%s) AS receipts,
            (SELECT count(*) FROM analytics.processing_results x WHERE x.subject_ref=%s) AS results,
            (SELECT count(*) FROM analytics.processing_leases x WHERE x.subject_ref=%s) AS leases,
            (SELECT count(*) FROM analytics.processing_replays x WHERE x.subject_ref=%s) AS replays,
            (SELECT count(*) FROM analytics.admission_subject_windows x WHERE x.subject_ref=%s) AS windows,
            (SELECT count(*) FROM analytics.activity_projection_buckets x WHERE x.subject_ref=%s) AS buckets
            FROM analytics.privacy_receipts r CROSS JOIN analytics.privacy_state p
            LEFT JOIN analytics.subject_fences f ON f.subject_ref=r.subject_ref
            WHERE r.control_id=%s AND r.seq=%s AND r.subject_ref=%s AND p.singleton=true""",
            (subject,subject,subject,subject,subject,subject,subject,subject,control,sequence,subject)).fetchone()
    return bool(row and row[0]=="complete" and row[1]>=sequence and row[2] is True and all(value==0 for value in row[3:]))

async def wait_condition(function,monitor,*,timeout=30,interval=.5):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        monitor.check()
        if await asyncio.to_thread(function): return
        await asyncio.sleep(interval)
    raise StopRun("bounded reconciliation deadline exceeded")

def producer_grant(key,issuer):
    from pymentor_analytics.contracts.producer_v1 import ProducerClaims
    now=int(time.time())
    claims={"grant_version":1,"iss":issuer+"-control","aud":"analytics-control","sub":"bench-producer",
            "iat":now,"nbf":now,"exp":now+50,"scope":"control:write"}
    ProducerClaims.model_validate_json(json.dumps(claims))
    return jwt.encode(claims,key,algorithm="EdDSA",headers={"typ":"JWT","kid":"bench-p1"})

def spawn_restore_role(role,url,*,folder,token,job,children,manifest,catalog,authority,projection_run_id=None):
    port=owned.unused_port()
    env=cross_service.base_env()
    env.update(PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE="1",ANALYTICS_TRUST_MANIFEST_JSON=json.dumps(manifest),
               ANALYTICS_PRIVACY_AUTHORITY_JSON=json.dumps(authority),ANALYTICS_FIXTURE_CATALOG_JSON=json.dumps(catalog))
    env[f"ANALYTICS_{role.upper()}_DATABASE_URL"]=url
    if role in {"query","processor"}:
        if not isinstance(projection_run_id,UUID): raise ValueError("restored projection generation required")
        env["ANALYTICS_ACTIVITY_PROJECTION_RUN_ID"]=str(projection_run_id)
        env[f"ANALYTICS_{role.upper()}_ACTIVITY_PROJECTION_ENABLED"]="true"
    env[f"ANALYTICS_{role.upper()}_POOL_SIZE"]="1" if role=="privacy" else "2"
    env[f"ANALYTICS_{role.upper()}_POOL_OVERFLOW"]="0"
    def record(processes): owned.record(folder,token,state="recovery_processes_starting",processes=[p.pid for p in processes])
    proc=owned.spawn_owned([sys.executable,"-B","-m","pymentor_analytics.app.cli",role,"--host","127.0.0.1","--port",str(port)],
        cwd=ROOT,env=env,folder=folder,label="restore-"+role,children=children,job=job,on_spawn=record)
    owned.wait_port(proc,port); job.assert_listener(port)
    return f"http://127.0.0.1:{port}"

async def execute_recovery(client,processes,profile,stage,seed,run_id,key,issuer,authority,urls,target,
                           folder,owner_token,job,children,manifest,catalog,monitor,fingerprint):
    """Small separately owned replay/deletion/restore sample, not the load profile."""
    from pymentor_analytics.adapters.postgres.database import Database
    from pymentor_analytics.adapters.postgres.replay import PostgresReplay
    from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient
    from pymentor_analytics.app.privacy import PrivacyRuntime
    from pymentor_analytics.contracts.v2 import DeletionFence
    plan=stream_plan(profile,stage,seed,run_id)
    course=plan["courses"][0]
    subjects=[uuid5(run_id,f"recovery-victim:{i}") for i in range(3)]
    now=datetime.now(UTC).replace(microsecond=0); base=now-timedelta(minutes=2)
    seeds={}
    for index,subject in enumerate(subjects):
        stream={"subject":subject,"user":stage+index,"device":0,"course":course}
        records=[event_for(stream,run_id=run_id,seed=seed,stage=stage,number=n,
                           start=base+timedelta(seconds=n*15)) for n in range(4 if index==0 else 1)]
        for event in records:
            monitor.check()
            access=grant(key,issuer=issuer,kid="bench-g1",subject=subject,role="user",scope="collect",courses=(course,))
            try:
                response=await client.post(processes.endpoints["collector"][0]+"/api/analytics/events/batch",
                    headers={"Authorization":"Bearer "+access},json={"events":[event]})
            except httpx.HTTPError: raise StopRun("recovery seed outcome unknown; reconcile") from None
            if response.status_code!=200: raise StopRun("recovery seed unavailable")
            ack=BatchAck.model_validate_json(response.content)
            if len(ack.items)!=1 or ack.items[0].status!="accepted" or str(ack.items[0].event_id)!=event["event_id"]:
                raise StopRun("recovery seed ACK mismatch")
        seeds[subject]=records[0]
    subject=subjects[0]
    await wait_condition(lambda: (row:=read_replay_result(target,subject)) is not None and row[0]==4,monitor)
    before=await asyncio.to_thread(read_replay_result,target,subject)
    snapshot=await asyncio.to_thread(browser_snapshot,target,subject)
    database=Database(Settings(database_url=urls["processor"],pool_size=1,pool_overflow=0,db_timeout_seconds=5))
    gate=PrivacyRuntime(AuthorityClient(AuthorityConfig.model_validate(authority)),database)
    database.transaction_guard=gate.check_in_transaction
    gate.start()
    try:
        deadline=time.monotonic()+30
        while not await gate.is_ready():
            if time.monotonic()>=deadline: raise StopRun("replay privacy gate unavailable")
            monitor.check(); await asyncio.sleep(.25)
        replay_result=await paced_replay(PostgresReplay(database),subject,uuid5(run_id,"replay-request"),monitor)
    finally:
        await gate.stop(); await database.close()
    after=await asyncio.to_thread(read_replay_result,target,subject)
    if before!=after: raise ObservedViolation("replay changed the synthetic logical count")
    pkey=serialization.load_pem_private_key((folder/"producer.private.pem").read_bytes(),password=None)
    ckey=serialization.load_pem_private_key((folder/"command.private.pem").read_bytes(),password=None)
    control_client=AuthorityClient(AuthorityConfig.model_validate(authority))
    deletion_samples=[]
    for index,victim in enumerate(subjects):
        control=DeletionFence(schema_version=2,kind="deletion_fence",control_id=uuid5(run_id,f"recovery-delete:{index}"),
                              subject_ref=victim,epoch=1,occurred_at=datetime.now(UTC))
        async def append_once(control=control):
            receipt=await control_client.append(control,producer="bench-producer",producer_token=producer_grant(pkey,issuer),command_key=ckey)
            return receipt.seq
        async def wait_purge(sequence,control=control,victim=victim):
            await wait_condition(lambda:read_deletion_status(target,victim,control.control_id,sequence),monitor,timeout=55)
        deletion_samples.append(await measure_deletion(append_once,wait_purge,monitor))
    await await_ready(client,processes,fingerprint)
    denied_token=grant(key,issuer=issuer,kid="bench-g1",subject=subject,role="user",scope="collect",courses=(course,))
    denied=await client.post(processes.endpoints["collector"][0]+"/api/analytics/events/batch",
                             headers={"Authorization":"Bearer "+denied_token},json={"events":[seeds[subject]]})
    if denied.status_code!=403: raise ObservedViolation("deleted subject admitted again")
    # Fresh second DB, bound to the same independent journal; no truncation or
    # arbitrary restore target. Owner/sentinel are rechecked on every data access.
    monitor.check()
    restored,restore_urls,restore_config=owned.create_analytics(folder,owner_token)
    owned.install_analytics(restore_urls,full_chain=True,activity_projection=True); owned.verify_analytics_grants(restored)
    verify_database(restored,restore_config)
    await asyncio.to_thread(transplant_browser_snapshot,restored,snapshot)
    restore_run_id=uuid4()
    await asyncio.to_thread(initialize_projection,restored,restore_run_id)
    started=time.monotonic()
    restored_query=spawn_restore_role("query",restore_urls["query"],folder=folder,token=owner_token,job=job,children=children,
                                     manifest=manifest,catalog=catalog,authority=authority,projection_run_id=restore_run_id)
    initial=await client.get(restored_query+"/ready")
    if initial.status_code!=503: raise ObservedViolation("restored reader opened before deletion barrier")
    spawn_restore_role("privacy",restore_urls["privacy"],folder=folder,token=owner_token,job=job,children=children,
                       manifest=manifest,catalog=catalog,authority=authority)
    spawn_restore_role("processor",restore_urls["processor"],folder=folder,token=owner_token,job=job,children=children,
                       manifest=manifest,catalog=catalog,authority=authority,projection_run_id=restore_run_id)
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        monitor.check()
        response=await client.get(restored_query+"/ready")
        if response.status_code==200 and response.headers.get("X-Analytics-Trust-Fingerprint")==fingerprint: break
        await asyncio.sleep(.5)
    else: raise StopRun("restore barrier did not reopen within deadline")
    last_control=uuid5(run_id,"recovery-delete:0")
    if not await asyncio.to_thread(read_deletion_status,restored,subject,last_control,deletion_samples[0]["sequence"]):
        raise ObservedViolation("restored subject data survived deletion")
    query_token=grant(key,issuer=issuer,kid="bench-g1",subject=subject,role="user",scope="report:read",courses=(course,))
    denied_query=await client.get(restored_query+"/api/analytics/reports/activity",
        headers={"Authorization":"Bearer "+query_token},params={"from_time":base.isoformat(),"to_time":now.isoformat(),"course_id":course})
    if denied_query.status_code!=403: raise ObservedViolation("restored deleted subject is readable")
    return {"replay_sample":replay_result,"privacy_deletion_sample":{"status":"PASS","samples":deletion_samples},
            "restore_barrier_sample":{"status":"PASS","initial_ready_status":503,"final_ready_status":200,
                                       "elapsed_seconds":time.monotonic()-started,"kind":"logical_subject_snapshot"},
            "full_backup_restore_rpo_rto":"UNRUN","concurrent_900_second_load":"UNRUN"}

class ResourceMonitor:
    """Monitor Windows memory and workspace disk against caller-supplied floors."""
    def __init__(self,memory_floor_bytes,disk_floor_bytes,*,commit_floor_bytes=None):
        if type(memory_floor_bytes) is not int or memory_floor_bytes<=0 or type(disk_floor_bytes) is not int or disk_floor_bytes<=0:
            raise ValueError("explicit positive resource floors required")
        if commit_floor_bytes is not None and (type(commit_floor_bytes) is not int or commit_floor_bytes<=0):
            raise ValueError("commit floor must be positive")
        self.memory_floor_bytes=memory_floor_bytes
        self.disk_floor_bytes=disk_floor_bytes
        self.commit_floor_bytes=commit_floor_bytes
        self.samples=[]

    def check(self):
        if os.name!="nt": raise StopRun("Windows resource monitoring unavailable")
        import ctypes
        from ctypes import wintypes
        class MemoryStatus(ctypes.Structure):
            _fields_=[("length",wintypes.DWORD),("load",wintypes.DWORD),
                      ("total_phys",ctypes.c_ulonglong),("avail_phys",ctypes.c_ulonglong),
                      ("total_page",ctypes.c_ulonglong),("avail_page",ctypes.c_ulonglong),
                      ("total_virtual",ctypes.c_ulonglong),("avail_virtual",ctypes.c_ulonglong),
                      ("avail_extended",ctypes.c_ulonglong)]
        status=MemoryStatus(); status.length=ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise StopRun("Windows memory measurement unavailable")
        disk_free=shutil.disk_usage(ROOT).free
        self.samples.append({"free_physical_bytes":status.avail_phys,"free_pagefile_bytes":status.avail_page,
                             "free_disk_bytes":disk_free})
        if self.commit_floor_bytes is not None and status.avail_page<self.commit_floor_bytes:
            raise StopRun("Windows commit headroom floor reached")
        if status.avail_phys<self.memory_floor_bytes or disk_free<self.disk_floor_bytes:
            raise StopRun("resource floor reached")

    def result(self):
        return {"memory_floor_bytes":self.memory_floor_bytes,"disk_floor_bytes":self.disk_floor_bytes,
                "commit_floor_bytes":self.commit_floor_bytes,
                "sample_count":len(self.samples),
                "min_free_physical_bytes":min((s["free_physical_bytes"] for s in self.samples),default=None),
                "min_free_pagefile_bytes":min((s["free_pagefile_bytes"] for s in self.samples),default=None),
                "min_free_disk_bytes":min((s["free_disk_bytes"] for s in self.samples),default=None)}

def resource_floors(environ):
    try:
        memory=int(environ["ANALYTICS_BENCHMARK_MIN_FREE_MEMORY_MIB"])
        disk=int(environ["ANALYTICS_BENCHMARK_MIN_FREE_DISK_MIB"])
        commit=environ.get("ANALYTICS_BENCHMARK_MIN_FREE_COMMIT_MIB")
        commit_bytes=None if commit is None else int(commit)*1024*1024
        return ResourceMonitor(memory*1024*1024,disk*1024*1024,commit_floor_bytes=commit_bytes)
    except (ValueError,KeyError): raise UnsafeTarget("explicit resource floors required") from None

def output_path(value):
    root=ROOT.resolve(); local=(ROOT/".local").resolve()
    if local.parent!=root or (ROOT/".local").is_symlink(): raise UnsafeTarget("unsafe local output root")
    allowed=(local/"benchmark-results").resolve()
    candidate=Path(value).resolve()
    if allowed.parent!=local or candidate.parent!=allowed or candidate.suffix!=".json" or candidate.exists():
        raise UnsafeTarget("output must be a new JSON file inside .local/benchmark-results")
    return candidate

def write_result(path, result):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as stream:
        json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.flush(); os.fsync(stream.fileno())

def classify(result, profile):
    if result["oracle"]["status"]=="FAIL": return "FAIL"
    if result["timeouts"] or any(not key.endswith(":200") and value for group in ("requests","warmup_requests","verification_requests") for key,value in result.get(group,{}).items()): return "FAIL"
    if result.get("ack",{}).get("accepted")!=result.get("expected_measurement_segments"): return "FAIL"
    if result.get("opening_ack",{}).get("accepted")!=result.get("expected_openings"): return "FAIL"
    if result.get("warmup_ack",{}).get("accepted")!=result.get("expected_warmup_segments"): return "FAIL"
    query_count=sum(value for key,value in result["requests"].items() if key.startswith("query:"))
    if query_count!=result.get("expected_measurement_queries"): return "FAIL"
    for component in ("collector","query"):
        p95=result[component]["p95_ms"]
        if p95 is None: return "UNRUN"
        if p95>profile["targets"][component+"_p95_ms"]: return "FAIL"
    if result["oracle"]["status"]!="PASS": return "UNRUN"
    # Profile also requires replay, privacy load, separate correctness scenarios,
    # three repetitions and independent resource/load admission. This runner's
    # ordinary time-series alone cannot prove them.
    return "UNRUN"

def observed_violation(stats):
    """A guard interruption alone is not evidence of a failed workload target."""
    if stats["timeouts"]: return True
    if any(not key.endswith(":200") and value for group in ("requests","warmup_requests","verification_requests")
           for key,value in stats[group].items()): return True
    return any(status!="accepted" and value for group in ("ack","opening_ack","warmup_ack")
               for status,value in stats[group].items())

def planned(profile,stage,seed,replicas,run_id):
    plan=stream_plan(profile,stage,seed,run_id)
    return {"status":"UNRUN","profile":profile["version"],"stage":stage,"replicas":list(replicas),"seed":seed,
            "run_id":str(run_id),"active_users":plan["active"],"overlap_users":plan["overlapping"],
            "streams":len(plan["streams"]),"warmup_seconds":profile["warmup_seconds"],
            "measurement_seconds":profile["measurement_seconds"],"heartbeat_seconds":profile["heartbeat_seconds"],
            "model_start":MODEL_START.isoformat(),"execution":"not_started"}

async def run_owned(profile,stage,seed,replicas,run_id,output,monitor,*,correctness=False,recovery=False,mixed=False,matched_cadence=None,matched_schema="a010",benchmark_postgres=False,matched_smoke=False,activity_poll_ms=None,query_pool_size=2):
    if type(query_pool_size) is not int or query_pool_size not in (2,4) or (query_pool_size != 2 and matched_cadence is None): raise ValueError("query pool requires matched cadence and size 2 or 4")
    if type(matched_smoke) is not bool or (matched_smoke and matched_cadence is None): raise ValueError("smoke requires matched cadence")
    if activity_poll_ms is not None and (matched_cadence is None or type(activity_poll_ms) is not int or not 50<=activity_poll_ms<=500): raise ValueError("activity poll requires matched cadence and 50-500 ms")
    if matched_smoke and activity_poll_ms is None: activity_poll_ms=500
    if matched_smoke: profile={**profile,"warmup_seconds":60,"measurement_seconds":120}
    artifact=planned(profile,stage,seed,replicas,run_id)
    artifact["execution"]="starting"
    if sum((correctness,recovery,mixed))>1: raise ValueError("choose one scenario")
    if matched_cadence not in (None,15,60) or (matched_cadence is not None and (correctness or recovery or mixed)):
        raise ValueError("matched cadence requires standalone load")
    if matched_schema not in MATCHED_SCHEMAS or (matched_schema!="a010" and matched_cadence is None):
        raise ValueError("matched schema requires matched cadence")
    if type(benchmark_postgres) is not bool or (benchmark_postgres and matched_cadence is None):
        raise ValueError("benchmark postgres requires matched cadence")
    artifact["mode"]="correctness" if correctness else "recovery" if recovery else "mixed" if mixed else "load"
    if matched_cadence is not None:
        artifact.update(mode="matched_cadence",cadence_seconds=matched_cadence,client_concurrency=1,
                        section_change_seconds=60,matched_schema=matched_schema,
                        postgres_target="benchmark_wsl" if benchmark_postgres else "default",
                        warmup_report_scope="from_workload_start",query_pool_size=query_pool_size)
        if activity_poll_ms is not None: artifact["activity_poll_ms"]=activity_poll_ms
        if matched_smoke: artifact.update(matched_smoke=True,acceptance_status="UNRUN")
    if correctness or recovery:
        artifact["warmup_seconds"]=None
        artifact["measurement_seconds"]=None
    artifact["provenance"]={"harness_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                            "profile_sha256":hashlib.sha256((ROOT/"bench/profile-v1.json").read_bytes()).hexdigest(),
                            "oracle_sha256":hashlib.sha256((ROOT/"reference/activity_v1.py").read_bytes()).hexdigest(),
                            "python_version":sys.version.split()[0],"os":platform.system(),"cpu_count":os.cpu_count()}
    artifact["scenarios"]={"live":"UNRUN","oracle":"UNRUN","replay":"UNRUN","privacy_load":"UNRUN",
                           "late_duplicate_conflict":"UNRUN","deletion_barrier":"UNRUN","backup_restore":"UNRUN"}
    folder=token=job=target=None; children=[]; stats=new_stats(); boundary_samples=[]
    try:
        monitor.check()
        folder,token=owned.private_folder()
        job=owned.OwnedJob(); owned.set_job(job)
        target,urls,db_config=owned.create_analytics(folder,token,benchmark_postgres=benchmark_postgres)
        owned.install_analytics(urls,full_chain=True,activity_projection=True,day_projection=matched_schema!="a010")
        owned.verify_analytics_grants(target)
        verify_database(target,db_config,benchmark_postgres=benchmark_postgres)
        if matched_schema in COURSE_SCHEMAS:
            if urls["migrate"]!=target["dsn"]: raise UnsafeTarget("migration target mismatch")
            with _guarded_connection(target) as conn:
                if conn.execute("SELECT current_user").fetchone()!=(target["owner"],) or conn.execute("SELECT version_num FROM alembic_version").fetchall()!=[("a011",)]:
                    raise UnsafeTarget("owned a011 migration target required")
            from pymentor_analytics.adapters.postgres.migrate import upgrade
            upgrade(Settings(database_url=urls["migrate"]),target_revision=matched_schema)
            with _guarded_connection(target) as conn:
                if conn.execute("SELECT version_num FROM alembic_version").fetchall()!=[(matched_schema,)]:
                    raise UnsafeTarget("requested migration not confirmed")
        initialize_projection(target,run_id)
        plan=stream_plan(profile,stage,seed,run_id)
        keys,secret=cross_service.key_material(folder)
        key,issuer,manifest,catalog,fingerprint=keys_and_catalog(run_id,plan["courses"],keys,folder)
        authority=authority_start(folder,token,job,children,run_id,keys,secret,issuer)
        validate_authority_key_separation(Settings(database_url=urls["collector"],trust_manifest_json=json.dumps(manifest)), authority["public_keys"])
        processes=OwnedProcesses(urls,replicas,manifest,catalog,authority,folder,token,job,children,projection_run_id=run_id,matched_schema=matched_schema,activity_poll_ms=activity_poll_ms,query_pool_size=query_pool_size)
        processes.start()
        async def projection_wait():
            await wait_condition(lambda:projection_drained(target,run_id,matched_schema=matched_schema),monitor,timeout=60)
        limits=httpx.Limits(max_connections=64,max_keepalive_connections=64)
        async with httpx.AsyncClient(timeout=httpx.Timeout(5),limits=limits,trust_env=False) as client:
            await await_ready(client,processes,fingerprint)
            if matched_schema!="a010":
                await projection_wait()
            async def measurement_boundary(label,elapsed_seconds):
                sample={"label":label,"elapsed_seconds":elapsed_seconds,"status":"UNRUN"}
                boundary_samples.append(sample)
                try:
                    sample["database"]=await asyncio.to_thread(measurement_database_snapshot,target)
                    sample["cpu"]=await sample_metrics(client,processes)
                    sample["status"]="OBSERVED"
                except Exception:
                    # No DSNs, credentials, SQL errors or partial metadata in the artifact.
                    sample.pop("database",None); sample.pop("cpu",None)
            async def query_failure_diagnostic(context):
                result={"status":"OBSERVED","request_role":context["role"],"query_replica_index":context["endpoint_index"],
                        "tick":context["tick"],"phase":context["phase"],"response_elapsed_seconds":context["elapsed_seconds"],
                        "database_snapshot":{"status":"UNAVAILABLE"},"query_log":{"status":"UNAVAILABLE"},
                        "readiness":{"status":"UNAVAILABLE"},"note":"Post-response observations may differ from the failed query snapshot; log reasons are not request-correlated."}
                try:
                    result["database_snapshot"]={"status":"OBSERVED",**await asyncio.to_thread(query_failure_snapshot,target,run_id,context)}
                except Exception: pass
                try:
                    result["query_log"]={"status":"OBSERVED","recent_allowlisted_reasons":await asyncio.to_thread(query_failure_log_reasons,folder,context["endpoint_index"])}
                except Exception: pass
                try:
                    response=await client.get(processes.endpoints["query"][context["endpoint_index"]]+"/ready")
                    result["readiness"]={"status":"OBSERVED","http_status":response.status_code,
                        "ready":response.status_code==200 and response.json().get("status")=="ready",
                        "trust_fingerprint_matches":response.headers.get("X-Analytics-Trust-Fingerprint")==fingerprint}
                except Exception: pass
                return result
            owned.record(folder,token,state="benchmark_running",processes=[p.pid for p in children])
            artifact["execution"]="running"
            if correctness:
                await sample_metrics(client,processes)
                result=await execute_correctness(client,processes,profile,stage,seed,run_id,key,issuer,monitor=monitor,projection_wait=projection_wait)
            elif recovery:
                await sample_metrics(client,processes)
                result=await execute_recovery(client,processes,profile,stage,seed,run_id,key,issuer,authority,urls,target,
                    folder,token,job,children,manifest,catalog,monitor,fingerprint)
            elif mixed:
                result,mixed_result=await execute_mixed(client,processes,profile,stage,seed,run_id,key,issuer,fingerprint,
                                                        authority,urls,target,folder,monitor,stats,projection_wait=projection_wait)
            else:
                result=await execute_workload(client,processes,profile,stage,seed,run_id,key,issuer,fingerprint,
                                              stats=stats,monitor=monitor,metrics_sampler=lambda:sample_metrics(client,processes),
                                                projection_wait=projection_wait,matched_cadence=matched_cadence,
                                                measurement_boundary=measurement_boundary if matched_cadence is not None else None,
                                                query_failure_diagnostic=query_failure_diagnostic,matched_smoke=matched_smoke)
        if recovery:
            artifact["recovery"]=result
            artifact["scenarios"]["replay_sample"]=result["replay_sample"]["status"]
            artifact["scenarios"]["privacy_deletion_sample"]=result["privacy_deletion_sample"]["status"]
            artifact["scenarios"]["restore_barrier_sample"]=result["restore_barrier_sample"]["status"]
            # Samples do not prove the full 900s concurrent profile or RPO/RTO.
            artifact["status"]="UNRUN"
        elif correctness:
            artifact["correctness"]=result
            artifact["scenarios"]["late_duplicate_conflict"]=result["status"]
            artifact["status"]="FAIL" if result["status"]=="FAIL" else "UNRUN"
        else:
            artifact.update(result)
            artifact["scenarios"]["live"]="OBSERVED"
            artifact["scenarios"]["oracle"]=result["oracle"]["status"]
            artifact["status"]=classify(result,profile)
            if mixed:
                artifact["mixed"]=mixed_result
                artifact["scenarios"]["replay_sample"]=mixed_result["replay_sample"]["status"]
                artifact["scenarios"]["privacy_deletion_sample"]=mixed_result["privacy_deletion_sample"]["status"]
        artifact["resources"]=monitor.result()
        artifact["execution"]="finished"
        artifact["replicas_observed"]=processes.descriptors
        artifact["monitoring_traffic"]={"readiness_interval_seconds":15,"metrics_interval_seconds":60,
                                        "included_in_report_latency":False}
        artifact["isolation"]={"database_name":target["name"],"sentinel_verified":True,"owner_verified":True,
                               "listener_ownership":"Windows Job","separate_authority":True,"core_connected":False}
        artifact["activity_projection"]={"migration":matched_schema,"enabled":True,"dataset_run_id":str(run_id)}
        owned.record(folder,token,state="benchmark_finished",processes=[p.pid for p in children])
    except BaseException as exc:
        # Never leak URLs, tokens, event bodies or key material via public output.
        running=artifact["execution"]=="running"
        artifact["execution"]="interrupted" if isinstance(exc,(KeyboardInterrupt,asyncio.CancelledError)) else "failed"
        artifact["failure_kind"]=type(exc).__name__
        artifact["resources"]=monitor.result()
        if isinstance(exc,StopRun): artifact["failure_reason"]=str(exc)
        artifact.update(collector=summary(stats["collector_ms"]),query=summary(stats["query_ms"]),
                        requests=stats["requests"],warmup_requests=stats["warmup_requests"],
                        verification_requests=stats["verification_requests"],ack=stats["ack"],
                        opening_ack=stats["opening_ack"],warmup_ack=stats["warmup_ack"],query_roles=stats["query_roles"],
                        retry_after=stats["retry_after"],timeouts=stats["timeouts"],metrics=stats["metrics"])
        if "query_failure_diagnostic" in stats: artifact["query_failure_diagnostic"]=stats["query_failure_diagnostic"]
        artifact["status"]="FAIL" if running and (isinstance(exc,ObservedViolation) or observed_violation(stats)) else "UNRUN"
        if folder is not None:
            try: owned.record(folder,token,state="benchmark_failed",processes=[p.pid for p in children])
            except Exception: pass
        if isinstance(exc,(KeyboardInterrupt,asyncio.CancelledError)): raise
    finally:
        if matched_cadence is not None:
            artifact["measurement_resources"]=summarize_measurement_boundaries(boundary_samples)
        stopped=False
        try:
            owned.processes_stop(children)
            stopped=True
        except Exception:
            artifact["execution"]="cleanup_failed"
            artifact["status"]="UNRUN"
            artifact["cleanup"]="reconciliation_required"
        finally:
            if job is not None: job.close()
            owned.set_job(None)
            if folder is not None:
                try:
                    owned.record(folder,token,state="processes_stopped_resources_retained" if stopped else "process_reconciliation_required",
                                 processes=[p.pid for p in children])
                except Exception:
                    artifact["cleanup"]="owner_record_reconciliation_required"
                    artifact["status"]="UNRUN"
            # DB and private owner record intentionally remain for reconciliation;
            # no automatic deletion occurs on success, failure, or unknown outcome.
            write_result(output,artifact)
    return artifact

def exit_code(result):
    return 0 if result.get("execution")=="finished" and result.get("status")!="FAIL" else 1

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile",required=True)
    parser.add_argument("--stage",type=int,required=True,choices=STAGES)
    parser.add_argument("--replicas",required=True)
    parser.add_argument("--seed",type=int,required=True)
    parser.add_argument("--output",required=True)
    parser.add_argument("--run",action="store_true",help="requires separate root GO and resource preflight")
    scenarios=parser.add_mutually_exclusive_group()
    scenarios.add_argument("--correctness",action="store_true",help="short correctness sample, never load PASS")
    scenarios.add_argument("--recovery",action="store_true",help="short replay/deletion/restore sample, never full RPO/load PASS")
    scenarios.add_argument("--mixed",action="store_true",help="full live duration with bounded synthetic replay and deletion samples")
    scenarios.add_argument("--matched-cadence",type=int,choices=(15,60),help="standalone matched 15/60 second workload, client concurrency 1")
    parser.add_argument("--matched-schema",choices=MATCHED_SCHEMAS[1:],help="opt in to a matched projection schema")
    parser.add_argument("--benchmark-postgres",action="store_true",help="use fixed isolated synthetic WSL PostgreSQL target for matched workload")
    parser.add_argument("--matched-smoke",action="store_true",help="non-acceptance matched smoke: 60s warmup, 120s measurement")
    parser.add_argument("--activity-poll-ms",type=int,help="matched Processor activity poll interval, 50-500 ms")
    parser.add_argument("--query-pool-size",type=int,choices=(2,4),help="matched Query pool size (default 2)")
    args=parser.parse_args(argv)
    try:
        profile=profile_load(args.profile); replicas=parse_replicas(args.replicas)
        if args.query_pool_size is not None and args.matched_cadence is None: raise ValueError("query pool size requires matched cadence")
        if args.matched_schema and args.matched_cadence is None: raise ValueError("matched schema requires matched cadence")
        if args.benchmark_postgres and args.matched_cadence is None: raise ValueError("benchmark postgres requires matched cadence")
        if args.matched_smoke and args.matched_cadence is None: raise ValueError("smoke requires matched cadence")
        if args.activity_poll_ms is not None and (args.matched_cadence is None or not 50<=args.activity_poll_ms<=500): raise ValueError("activity poll requires matched cadence and 50-500 ms")
        path=output_path(args.output); run_id=uuid4()
        plan_profile={**profile,"warmup_seconds":60,"measurement_seconds":120} if args.matched_smoke else profile
        plan=planned(plan_profile,args.stage,args.seed,replicas,run_id)
        plan["mode"]="correctness" if args.correctness else "recovery" if args.recovery else "mixed" if args.mixed else "load"
        if args.matched_cadence is not None:
            plan.update(mode="matched_cadence",cadence_seconds=args.matched_cadence,client_concurrency=1,
                        section_change_seconds=60,matched_schema=args.matched_schema or "a010",
                        postgres_target="benchmark_wsl" if args.benchmark_postgres else "default",
                        warmup_report_scope="from_workload_start",query_pool_size=args.query_pool_size or 2)
            if args.activity_poll_ms is not None: plan["activity_poll_ms"]=args.activity_poll_ms
            if args.matched_smoke: plan.update(matched_smoke=True,acceptance_status="UNRUN",activity_poll_ms=args.activity_poll_ms if args.activity_poll_ms is not None else 500)
        if args.correctness or args.recovery:
            plan["warmup_seconds"]=None
            plan["measurement_seconds"]=None
        if not args.run:
            print(json.dumps(plan,ensure_ascii=False)); return 0
        if os.environ.get("ANALYTICS_BENCHMARK_GO")!="1":
            raise UnsafeTarget("root resource preflight GO required")
        if os.name!="nt" or sys.version_info[:2]!=(3,12): raise UnsafeTarget("owned Windows job and Analytics Python 3.12 required")
        monitor=resource_floors(os.environ)
        result=asyncio.run(run_owned(profile,args.stage,args.seed,replicas,run_id,path,monitor,correctness=args.correctness,recovery=args.recovery,mixed=args.mixed,matched_cadence=args.matched_cadence,matched_schema=args.matched_schema or "a010",benchmark_postgres=args.benchmark_postgres,matched_smoke=args.matched_smoke,activity_poll_ms=args.activity_poll_ms,query_pool_size=args.query_pool_size or 2))
        print(json.dumps({"execution":result["execution"],"status":result["status"],"run_id":str(run_id)}))
        return exit_code(result)
    except (ValueError,OSError,KeyError,TypeError):
        print("Benchmark rejected: check profile, output isolation and GO.",file=sys.stderr)
        return 2

if __name__=="__main__":
    raise SystemExit(main())
