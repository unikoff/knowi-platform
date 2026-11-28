"""Collect bounded timings on a fresh, owner-validated synthetic 607 target."""
import argparse
import asyncio
from contextvars import ContextVar
from functools import wraps
from itertools import count
import json
import os
import re
from pathlib import Path
import subprocess
import sys
from time import perf_counter_ns, time_ns
from uuid import UUID

ROLE = ContextVar("rca_role", default="background")
SPAN = ContextVar("rca_span", default=None)
CLAIM_ROLE = ContextVar("rca_claim_role", default="background")
ARRIVAL = ContextVar("rca_self_report_arrival", default=None)
DAY_SQL = ContextVar("rca_processor_day_sql", default=False)
TRACE_TAG = ContextVar("rca_report_ordinal", default=None)
TAG_HEADER = "x-corp607-diagnostic-ordinal"
TAG_PATTERN = re.compile(rb"[0-4]-[0-9]{2}")
SEQUENCE = count(1)
EMITTED = count(1)
ROUTES = {
    "/api/internal/analytics/deletions": "core.snapshot_http",
    "/api/analytics/events/batch": "collector.batch",
    "/api/analytics/reports/activity": "query.report",
    "/ready": "ready",
    "/health/ready": "ready",
    "/v1/attach": "authority.attach",
    "/v1/barrier": "authority.barrier",
    "/v1/observe": "authority.observe",
    "/v1/page": "authority.page",
}
FD = None


def start():
    return next(SEQUENCE), SPAN.get(), ROLE.get(), time_ns(), perf_counter_ns()


def done(name, state, *, error=None, code=None, line=None, backend_pid=None, trace_tag=None, group_span=None):
    ident, parent, role, utc, clock = state
    if role == "processor" and not DAY_SQL.get() and (name.startswith("db.") or name.startswith("statusdb.")):
        return
    emitted = next(EMITTED)
    if emitted <= 120_000:
        row = [ident,parent,role,name,utc,round((perf_counter_ns()-clock)/1e6,3),error,code,line,backend_pid,trace_tag,group_span]
        os.write(FD, (json.dumps(row,separators=(",",":"))+"\n").encode("ascii"))
    elif emitted == 120_001:
        os.write(FD, b'["sample_limit"]\n')


def failure(error, code):
    # Source line numbers and a bounded exception class list are all we emit.
    cursor = error.__traceback__
    line = None
    while cursor is not None:
        if cursor.tb_frame.f_code is code:
            line = cursor.tb_lineno
        cursor = cursor.tb_next
    context = error.__context__
    context_class = type(context).__name__ if context is not None else ""
    known = {"TimeoutError","CancelledError","PrivacyUnavailable","ValueError","OSError",
             "ConnectionError","DBAPIError","OperationalError","FenceUnavailable",
             "IncompleteReadError","ConnectionResetError","LimitOverrunError","ProjectionPending","ProjectionUnavailable","AuthorizationError"}
    error_class = type(error).__name__
    result = error_class if error_class in known else "Other"
    if context_class in known:
        result += "/" + context_class
    return result, line


def wrapped(function, name_for):
    code = getattr(function, "__code__", None)
    @wraps(function)
    async def asynchronous(*args, **kwargs):
        state = start()
        token = SPAN.set(state[0])
        mark, line = None, None
        try:
            return await function(*args, **kwargs)
        except BaseException as error:
            mark, line = failure(error, code)
            raise
        finally:
            SPAN.reset(token)
            done(name_for(args), state, error=mark, line=line)

    @wraps(function)
    def synchronous(*args, **kwargs):
        state = start()
        token = SPAN.set(state[0])
        mark, line = None, None
        try:
            return function(*args, **kwargs)
        except BaseException as error:
            mark, line = failure(error, code)
            raise
        finally:
            SPAN.reset(token)
            done(name_for(args), state, error=mark, line=line)
    return asynchronous if asyncio.iscoroutinefunction(function) else synchronous


def methods(cls, prefix, names):
    for name in names:
        setattr(cls,name,wrapped(getattr(cls,name),lambda _, name=name: prefix+name))


class TraceRoutes:
    def __init__(self, app, role):
        self.app, self.role = app, role

    async def __call__(self, scope, receive, send):
        rtoken = ROLE.set(self.role)
        label = ROUTES.get(scope.get("path"))
        if scope["type"] != "http" or label is None:
            try:
                return await self.app(scope, receive, send)
            finally:
                ROLE.reset(rtoken)
        state = start()
        token = SPAN.set(state[0])
        tag = None
        if label == "query.report":
            values = [value for name,value in scope.get("headers",()) if name == TAG_HEADER.encode("ascii")]
            if len(values) == 1 and TAG_PATTERN.fullmatch(values[0]):
                tag = values[0].decode("ascii")
        tag_token = TRACE_TAG.set(tag)
        code = None
        mark = None
        async def traced_send(message):
            nonlocal code
            if message["type"] == "http.response.start":
                code = message["status"]
            await send(message)
        try:
            return await self.app(scope, receive, traced_send)
        except BaseException as error:
            mark, _ = failure(error, None)
            raise
        finally:
            SPAN.reset(token)
            TRACE_TAG.reset(tag_token)
            done(label, state, code=code, error=mark, trace_tag=tag)
            ROLE.reset(rtoken)


def instrument_engine(engine, kind):
    from sqlalchemy import event
    engine.pool._rca_kind = kind
    engine.dialect.connect = wrapped(engine.dialect.connect,lambda _:kind+".connect")
    if kind == "db" and ROLE.get() == "processor":
        engine.dialect.do_commit = wrapped(engine.dialect.do_commit,lambda _:kind+".dbapi_commit")
        engine.dialect.do_rollback = wrapped(engine.dialect.do_rollback,lambda _:kind+".dbapi_rollback")
    def before(connection, cursor, statement, parameters, context, many):
        if ROLE.get() == "processor" and not DAY_SQL.get():
            return
        attempt = start()
        leading = statement.lstrip().upper()
        category = next((label for token,label in (
            ("PG_IS_IN_RECOVERY","identity"),("PG_CATALOG.PG_TRIGGER","schema"),
            ("SET TRANSACTION READ ONLY","read_only"),("CLOCK_TIMESTAMP(","clock"),
            ("ACCOUNT_DELETION_EVENTS","core_events"),("JSONB_TO_RECORDSET","deletion_status"),
            ("ANALYTICS.PRIVACY_STATE","privacy_state"),("ANALYTICS.ACTIVITY_FACTS","facts"),
            ("ADMISSION_BUDGET","admission_budget"),("ANALYTICS.SUBJECT_FENCES","fences"),
            ("PG_TRY_ADVISORY","subject_try_lock"),("PG_ADVISORY","lock"),
            ("LOCK_ACTIVITY_PROJECTION_RUN","generation_lock"),
            ("ROLLBACK TO SAVEPOINT","rollback_savepoint"),("RELEASE SAVEPOINT","release_savepoint"),
            ("SAVEPOINT","savepoint"),("FOR UPDATE","row_lock"),
            ("INSERT INTO ANALYTICS.ACTIVITY_PROJECTION_DAYS","day_write"),
            ("UPDATE ANALYTICS.ACTIVITY_PROJECTION_DAY_SOURCES","day_source_update"),
            ("PROJECTION","projection"),
        ) if token in leading),"other")
        backend_pid = None
        if DAY_SQL.get() or ROLE.get() == "query":
            try:
                backend_pid = connection.connection.driver_connection.get_server_pid()
            except (AttributeError, TypeError):
                pass
        context._rca_cursor = (attempt, category, backend_pid)
    def after(_, cursor, statement, parameters, context, many):
        recorded = getattr(context,"_rca_cursor",None)
        if recorded is not None:
            attempt, category, backend_pid = recorded
            done(kind+".sql."+category,attempt,backend_pid=backend_pid)
    event.listen(engine,"before_cursor_execute",before)
    event.listen(engine,"after_cursor_execute",after)


def patch_projection_batch():
    from pymentor_analytics.adapters.postgres.activity_report_batch import READ_SINGLE, SelfReportQueue, SelfReportRead
    from pymentor_analytics.adapters.postgres.activity_projection_reports import PostgresProjectionActivityReport
    from pymentor_analytics.app.probes import SingleflightReadiness

    arrivals = {}
    initialize_job = SelfReportRead.__init__
    def job(self,*args,**kwargs):
        initialize_job(self,*args,**kwargs)
        pending = ARRIVAL.get()
        if pending is not None:
            arrivals[id(self)] = pending
    SelfReportRead.__init__ = job

    enqueue = SelfReportQueue.submit
    async def submit(self,*args,**kwargs):
        state = start()
        token = ARRIVAL.set((state,TRACE_TAG.get()))
        try:
            response = await enqueue(self,*args,**kwargs)
            if response is READ_SINGLE:
                done("query.self_single_fallback",state)
            return response
        finally:
            ARRIVAL.reset(token)
    SelfReportQueue.submit = submit

    cancel = SelfReportQueue._cancel_waiter
    def cancel_waiter(self,job):
        arrivals.pop(id(job),None)
        return cancel(self,job)
    SelfReportQueue._cancel_waiter = cancel_waiter

    initialize_queue = SelfReportQueue.__init__
    def queue(self,read,*,concurrency):
        def dispatch(jobs):
            size = len(jobs)
            state = start()
            ident, _, role, utc, clock = state
            group = (ident,None,role,utc,clock)
            for queued in jobs:
                arrival = arrivals.pop(id(queued),None)
                if arrival is not None:
                    queued_state,tag = arrival
                    done(f"query.self_fifo_arrival_to_dispatch.size_{size}",queued_state,trace_tag=tag,group_span=ident)
            async def grouped():
                token = SPAN.set(ident)
                mark, line = None, None
                try:
                    return await read(jobs)
                except BaseException as error:
                    mark, line = failure(error, None)
                    raise
                finally:
                    SPAN.reset(token)
                    done(f"query.self_group.size_{size}",group,error=mark,line=line)
            return grouped()
        initialize_queue(self,dispatch,concurrency=concurrency)
    SelfReportQueue.__init__ = queue

    original_read = PostgresProjectionActivityReport.read_batched_activity
    timed = wrapped(original_read,lambda _: "query.claim."+CLAIM_ROLE.get())
    async def classified(self,grant,*args,**kwargs):
        claimed = getattr(grant,"role",None)
        token = CLAIM_ROLE.set(claimed if claimed in {"user","leader","admin"} else "other")
        try:
            return await timed(self,grant,*args,**kwargs)
        finally:
            CLAIM_ROLE.reset(token)
    PostgresProjectionActivityReport.read_batched_activity = classified
    PostgresProjectionActivityReport._read_snapshot = wrapped(PostgresProjectionActivityReport._read_snapshot,
        lambda _: "query.single."+CLAIM_ROLE.get())
    methods(PostgresProjectionActivityReport,"query.group_item.",("_read_self_report","_lock_run","_materialize_report"))
    original_retry = PostgresProjectionActivityReport._retry_pending
    async def retry(self,read):
        async def attempt():
            return await read()
        return await original_retry(self,wrapped(attempt,lambda _:"query.retry.attempt"))
    PostgresProjectionActivityReport._retry_pending = wrapped(retry,lambda _:"query.retry.total")
    original_ready = SingleflightReadiness.is_ready
    async def readiness(self):
        started = start()
        token = SPAN.set(started[0])
        marker = "closed" if self._closed else ("producer" if self._task is None or self._task.done() else "waiter")
        mark, line = None, None
        try:
            return await original_ready(self)
        except BaseException as error:
            mark, line = failure(error,None)
            raise
        finally:
            SPAN.reset(token)
            done("readiness.shared."+marker,started,error=mark,line=line)
    SingleflightReadiness.is_ready = readiness


def patch_analytics(*, processor_detail=False):
    from sqlalchemy.pool import AsyncAdaptedQueuePool
    from urllib.parse import urlsplit
    from pymentor_analytics.adapters.core_privacy.client import CoreDeletionClient
    from pymentor_analytics.adapters.core_privacy.guard import CoreDeletionGuard
    from pymentor_analytics.adapters.postgres.database import Database
    from pymentor_analytics.adapters.postgres.intake import PostgresIntake
    from pymentor_analytics.adapters.privacy_authority.client import AuthorityClient
    from pymentor_analytics.app import factory
    from pymentor_analytics.app.activity_projection_runtime import ActivityProjectionRuntime
    from pymentor_analytics.app.activity_day_projection_runtime import ActivityDayProjectionRuntime
    from pymentor_analytics.adapters.postgres.activity_projection import PostgresActivityProjection
    from pymentor_analytics.adapters.postgres.activity_day_projection import PostgresActivityDayProjection
    from pymentor_analytics.app.privacy import PrivacyRuntime
    AsyncAdaptedQueuePool._do_get = wrapped(AsyncAdaptedQueuePool._do_get,
        lambda args: getattr(args[0],"_rca_kind","db")+".checkout")
    inner = Database.__init__
    def db(self,settings):
        inner(self,settings)
        if ROLE.get() == "processor" and not processor_detail:
            return
        username = urlsplit(settings.database_url.get_secret_value()).username
        status = username is not None and username.endswith("_transfer_status")
        instrument_engine(self.engine.sync_engine, "statusdb" if status else "db")
    Database.__init__ = db
    transaction = Database.transaction
    def traced_transaction(self):
        admission = getattr(self,"transaction_admission",None)
        if ROLE.get() == "query" and isinstance(admission,asyncio.Semaphore) and not getattr(admission,"_rca_traced",False):
            admission.acquire = wrapped(admission.acquire,lambda _:"query.transaction_admission")
            admission._rca_traced = True
        return transaction(self)
    Database.transaction = traced_transaction
    methods(CoreDeletionClient,"core_client.",("snapshot","validate"))
    methods(CoreDeletionGuard,"core_guard.",("check",))
    methods(PostgresIntake,"intake.",("accept",))
    methods(AuthorityClient,"authority_client.",("attach","barrier","observe","page","_http_one"))
    methods(PrivacyRuntime,"privacy.",("tick","_read_cursor","_accept","_proof","check_in_transaction","is_ready"))
    if hasattr(PrivacyRuntime,"check_readiness_in_transaction"):
        methods(PrivacyRuntime,"privacy.",("check_readiness_in_transaction",))
    factory_create = factory.create_app
    def create(role,*args,**kwargs):
        app = factory_create(role,*args,**kwargs)
        app.add_middleware(TraceRoutes, role=role)
        return app
    factory.create_app = create
    patch_projection_batch()
    methods(ActivityProjectionRuntime,"projection.runtime.",("_cycle","_pause"))
    methods(ActivityDayProjectionRuntime,"projection.day_runtime.",("_cycle",))
    methods(PostgresActivityProjection,"projection.bucket_store.",("claim","commit"))
    if processor_detail:
        for name in ("claim","load","commit"):
            original = wrapped(getattr(PostgresActivityDayProjection,name),lambda _, name=name: "projection.day_store."+name)
            async def on_day(self,*args,original=original,**kwargs):
                token = DAY_SQL.set(True)
                try:
                    return await original(self,*args,**kwargs)
                finally:
                    DAY_SQL.reset(token)
            setattr(PostgresActivityDayProjection,name,on_day)
    else:
        methods(PostgresActivityDayProjection,"projection.day_store.",("claim","commit"))
    open_conn = asyncio.open_connection
    traced_conn = wrapped(open_conn,lambda args:"loopback_tcp_open")
    async def loopback_conn(host=None,port=None,*args,**kwargs):
        function = traced_conn if (host,port) in {("127.0.0.1",18041),("127.0.0.1",18046)} else open_conn
        return await function(host,port,*args,**kwargs)
    asyncio.open_connection = loopback_conn


def patch_core():
    from sqlalchemy.pool import NullPool, QueuePool
    from pymentor_core.app import asgi
    from pymentor_core.modules.analytics_delivery import local_collection
    NullPool._do_get = wrapped(NullPool._do_get,lambda args:"coredb.checkout")
    QueuePool._do_get = wrapped(QueuePool._do_get,lambda args:"coredb.checkout")
    inner = local_collection.create_engine
    def core_engine(*args,**kwargs):
        engine = inner(*args,**kwargs)
        instrument_engine(engine, "coredb")
        return engine
    local_collection.create_engine = core_engine
    local_collection.read_snapshot = wrapped(local_collection.read_snapshot,lambda _: "core.read_snapshot")
    create = asgi.create_app
    def create_app():
        app = create()
        app.add_middleware(TraceRoutes,role="core")
        return app
    asgi.create_app = create_app


def own_output(folder, component, generation):
    global FD
    suffix = "" if generation == 1 else f"-{generation}"
    path = folder / ("rca-timings-"+component+suffix+".ndjson")
    FD = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    os.write(FD,b'["span","parent","role","name","start_unix_ns","milliseconds","error","http_status","source_line","pg_backend_pid","trace_tag","group_span"]\n')


def serve(args):
    # The phase still uses the existing fixture's owner, staging, and live checks.
    import benchmark_windows_fixture as fixture
    import shadow_607_target as shared
    from pymentor_analytics.adapters.privacy_authority import local_server

    pair = local_server._instance_dir(str(args.pair))
    template = json.loads(local_server._read(pair,"windows-benchmark.template.json",131072))
    if template["owner"] != "corp598-607-benchmark" or str(args.pair) != template["pair"]:
        raise ValueError("diagnostic owner mismatch")
    folder = local_server._instance_dir(template["targets"][args.phase]["instance"])
    local_server._owner(folder,folder.name)
    if args.component == "analytics":
        own_output(folder,"analytics",args.generation)
        patch_analytics(processor_detail=args.processor_detail)
    else:
        def child_spawn(command,*pos,**kw):
            expected = {"core":"uvicorn","authority":"pymentor_analytics.adapters.privacy_authority.local_server"}
            if command[2:4] != ["-m",expected[args.component]]:
                raise ValueError("unexpected diagnostic child command")
            replacement = [*command[:2],str(Path(__file__).resolve()),"child",args.component,str(folder),str(args.generation),*command[4:]]
            return subprocess.Popen(replacement,*pos,**kw)
        class PrivateSubprocess:
            def __getattr__(self, name):
                return child_spawn if name=="Popen" else getattr(subprocess,name)
        shared.subprocess = PrivateSubprocess()
        fixture.subprocess = PrivateSubprocess()
    return fixture.serve_main(["--pair",str(args.pair),"--phase",args.phase,"--component",args.component])


def child(component, output, generation, original):
    sys.path.insert(0,str(Path.cwd()))
    folder = Path(output).absolute()
    expected = Path(__file__).resolve().parent.parent / ".local" / "local-live"
    if (component not in {"core","authority"} or folder.parent != expected
            or str(UUID(folder.name)) != folder.name or folder.resolve() != folder
            or not folder.is_dir()):
        raise ValueError("diagnostic child owner path mismatch")
    own_output(folder,component,generation)
    if component == "core":
        patch_core()
        import runpy
        sys.argv = ["uvicorn",*original]
        runpy.run_module("uvicorn",run_name="__main__",alter_sys=True)
    else:
        from pymentor_analytics.adapters.privacy_authority import local_server
        create = local_server.load_application
        def load_application(instance):
            app = create(instance)
            app.add_middleware(TraceRoutes,role="authority")
            return app
        local_server.load_application = load_application
        sys.argv = ["local_server",*original]
        local_server.main()


async def observe_burst(args, manifest, result):
    from datetime import UTC, datetime, timedelta
    from time import monotonic
    import httpx
    from benchmark_activity_windows import Course, Phase, Target, base, phase_observer, plan_evidence, setup_plan

    plan = setup_plan(args,[Course(**item) for item in manifest["courses"]])
    if not timedelta(minutes=24) <= datetime.now(UTC)-plan.anchor <= timedelta(hours=4):
        raise base.StopRun("diagnostic anchor outside normal admission bounds")
    openings = tuple(plan.v2(-1))
    minute = tuple(plan.v2(0))
    result.update(plan_evidence(plan,[minute],openings,args.mode))
    result.update(scope="diagnostic_first_minute_only", measured_full_profile_seconds=0,
                  scheduled_idle_waits_replayed=False)
    target = Target.from_record(manifest,"v2_only",plan)
    details = {"mode":"v2_only"}
    result["phases"].append(details)
    class DiagnosticPhase(Phase):
        async def request(self,method,role,path,*,kind,phase,**kwargs):
            tag = TRACE_TAG.get()
            if kind == "query" and tag is not None:
                kwargs["headers"] = {**kwargs["headers"],TAG_HEADER:tag}
            return await super().request(method,role,path,kind=kind,phase=phase,**kwargs)

    workload = DiagnosticPhase(target,plan,details)
    async with httpx.AsyncClient(timeout=5,trust_env=False,limits=httpx.Limits(max_connections=64)) as client:
        workload.client = client
        with phase_observer(target) as observer:
            observer.preflight()
            await observer.check(client)
            await workload.login(target.learners)
            for subject,event in openings:
                await workload.send_v2(subject,event,"opening")

            async def run_query(role,end,index,tag):
                token = TRACE_TAG.set(tag)
                started = monotonic()
                try:
                    await workload.report(role,plan.anchor,end,"warmup",index)
                    state = "200"
                except base.ObservedViolation as error:
                    state = str(error).removeprefix("query returned HTTP ")
                    if state not in {"400","401","403","404","429","500","503"}:
                        state = "failed"
                finally:
                    TRACE_TAG.reset(token)
                return {"role":role,"case":tag,"status":state,"ms":round((monotonic()-started)*1000,3)}

            roles = [("leader",0),("leader",1),("admin",0)]
            before = plan.anchor+timedelta(seconds=30)
            details["opening_queries"] = await asyncio.gather(*(run_query(role,before,i,f"0-{index:02}") for index,(role,i) in enumerate(roles)))
            if any(item["status"]!="200" for item in details["opening_queries"]):
                raise base.ObservedViolation("diagnostic opening report failed")
            for subject,event in minute:
                await workload.send_v2(subject,event,"warmup")
            await observer.check(client)
            boundary = plan.anchor+timedelta(seconds=60)
            roles.extend(("user",i) for i in range(plan.active))
            details["query_bursts"] = []
            details["seeding_finished_utc"] = datetime.now(UTC).isoformat()
            for number in range(1,args.repeats+1):
                if number > 1:
                    await asyncio.sleep(4)
                    cutoff = monotonic()+8
                    while not await observer.drained():
                        if monotonic() >= cutoff:
                            raise base.StopRun("diagnostic projection did not drain before ready-repeat burst")
                        await asyncio.sleep(.2)
                    await observer.check(client)
                observed_projection = {
                    "buckets":dict(observer.connection.execute(
                        "SELECT state,count(*) FROM analytics.activity_projection_buckets GROUP BY state"
                    ).fetchall()),
                    "day_sources":dict(observer.connection.execute(
                        "SELECT state,count(*) FROM analytics.activity_projection_day_sources GROUP BY state"
                    ).fetchall()),
                }
                begin_utc = datetime.now(UTC).isoformat()
                started = monotonic()
                burst = await asyncio.gather(*(run_query(role,boundary,i,f"{number}-{index:02}") for index,(role,i) in enumerate(roles)))
                details["query_bursts"].append({
                    "number":number,"begin_utc":begin_utc,"end_utc":datetime.now(UTC).isoformat(),"projection_before_dispatch":observed_projection,
                    "burst_wall_ms":round((monotonic()-started)*1000,3),"reports":burst,
                    "http_wall_summary":base.summary([row["ms"] for row in burst]),
                })
                if len(burst)!=33 or any(item["status"]!="200" for item in burst):
                    raise base.ObservedViolation("diagnostic query burst had HTTP failures or bad denominator")
            details["facts_after_burst"] = observer.connection.execute(
                "SELECT count(*) FROM analytics.activity_facts"
            ).fetchone()[0]
            if len(openings)!=36 or len(minute)!=36 or details["facts_after_burst"]!=72:
                raise base.ObservedViolation("diagnostic burst denominator or v2 facts differ")

    result["status"] = "OBSERVED_BURST"


def burst(argv):
    from datetime import datetime
    from benchmark_activity_windows import base, private_manifest

    parser = argparse.ArgumentParser(description="Replay the first exact smoke50 Query burst without idle waits.")
    parser.add_argument("--stage",required=True,type=int,choices=(50,))
    parser.add_argument("--repeats",type=int,choices=(1,2,3,4),default=1)
    parser.add_argument("--mode",required=True,choices=("smoke",))
    parser.add_argument("--pair",required=True,type=UUID)
    parser.add_argument("--anchor",required=True,type=datetime.fromisoformat)
    parser.add_argument("--manifest",required=True,type=Path)
    parser.add_argument("--run",required=True,action="store_true")
    parser.add_argument("--phase",required=True,choices=("v2_only",))
    parser.add_argument("--output",required=True,type=Path)
    parser.set_defaults(profile=base.ROOT/"bench/profile-v1.json")
    args = parser.parse_args(argv)
    if os.environ.get("ANALYTICS_BENCHMARK_GO")!="1":
        parser.error("explicit synthetic diagnostic slot required")
    output = base.output_path(args.output)
    manifest = private_manifest(args.manifest,args.pair)
    result = {"status":"UNRUN"}
    with output.open("x",encoding="utf-8") as artifact:
        try:
            asyncio.run(observe_burst(args,manifest,result))
        except base.ObservedViolation as error:
            result.update(status="FAIL",stop_reason=str(error))
        except base.StopRun as error:
            result.update(status="UNRUN",stop_reason=str(error))
        except Exception as error:
            result.update(status="UNRUN",stop_kind=type(error).__name__)
        finally:
            json.dump(result,artifact,indent=2,allow_nan=False)
            artifact.write("\n")
    print(json.dumps({"status":result["status"],"phases":len(result.get("phases",()))}))
    return {"OBSERVED_BURST":0,"FAIL":1,"UNRUN":2}[result["status"]]


def main():
    if len(sys.argv)>1 and sys.argv[1]=="burst":
        return burst(sys.argv[2:])
    if len(sys.argv) > 3 and sys.argv[1] == "child":
        component, output, generation, *arguments = sys.argv[2:]
        if generation not in {"1","2"}:
            raise ValueError("diagnostic child generation not admitted")
        return child(component,output,int(generation),arguments)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=("serve",))
    parser.add_argument("--pair",required=True,type=UUID)
    parser.add_argument("--phase",required=True,choices=("v2_only","shadow"))
    parser.add_argument("--component",required=True,choices=("core","analytics","authority"))
    parser.add_argument("--generation",type=int,choices=(1,2),default=1)
    parser.add_argument("--processor-detail",action="store_true")
    return serve(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())

