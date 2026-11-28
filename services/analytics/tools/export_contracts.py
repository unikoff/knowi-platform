"""Export deterministic contract artifacts. This is a specification, not an HTTP server."""
from pathlib import Path
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import TypeAdapter

from pymentor_analytics.contracts import v2, windows_v1
from pymentor_analytics.app.config import Settings
from pymentor_analytics.app.factory import create_app

ROOT = Path(__file__).resolve().parents[1] / "contracts"
MODELS = {
    "browser-event-v2": v2.BrowserEvent,
    "browser-batch-v2": v2.BrowserBatch,
    "accepted-event-v2": v2.AcceptedEvent,
    "batch-ack-v2": v2.BatchAck,
    "grant-header-v1": v2.JwtHeader,
    "grant-claims-v1": v2.GrantClaims,
    "core-control-v2": v2.CoreControl,
    "report-request-v1": v2.ReportRequest,
    "report-response-v1": v2.ReportResponse,
    "error-v1": v2.ErrorResponse,
    "window-batch-v1": windows_v1.WindowBatch,
    "window-batch-ack-v1": windows_v1.WindowBatchAck,
    "window-grant-request-v1": windows_v1.WindowGrantRequest,
    "window-grant-claims-v1": windows_v1.WindowGrantClaims,
    "window-grant-response-v1": windows_v1.WindowGrantResponse,
    "window-error-v1": windows_v1.WindowError,
    "window-diagnostics-v1": windows_v1.WindowDiagnostics,
}


def openapi():
    app = create_app("collector", Settings(database_url="postgresql://synthetic@localhost:57392/analytics_test"))
    app.title = "Analytics contract specification"
    app.version = "2.0.0"
    responses = {
        400: {"model": v2.ErrorResponse, "description": "Invalid envelope"},
        401: {"model": v2.ErrorResponse, "description": "Invalid grant"},
        403: {"model": v2.ErrorResponse, "description": "Scope denied"},
        413: {"model": v2.ErrorResponse, "description": "Batch too large"},
        429: {"model": v2.ErrorResponse, "description": "Backpressure", "headers": {"Retry-After": {"schema": {"type": "integer", "minimum": 1}}}},
        503: {"model": v2.ErrorResponse, "description": "Unavailable"},
    }
    @app.post("/api/analytics/reports/activity", response_model=v2.ReportResponse, responses=responses, operation_id="readActivityReport")
    def report(body: v2.ReportRequest):
        raise NotImplementedError
    schema = app.openapi()
    schema["components"]["securitySchemes"] = {"AnalyticsGrant": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}
    for operation in (schema["paths"]["/api/analytics/events/batch"]["post"], schema["paths"]["/api/analytics/reports/activity"]["post"]):
        operation["security"] = [{"AnalyticsGrant": []}]
    schema["info"]["description"] = "Collector HTTP exists but durable intake remains unavailable by default; report route is planned only."
    schema["paths"]["/api/analytics/reports/activity"]["post"]["x-implementation-status"] = "planned"
    fixtures = ROOT / "fixtures"
    batch = json.loads((fixtures / "browser-batch.json").read_text(encoding="utf-8"))
    examples = json.loads((fixtures / "scenarios.json").read_text(encoding="utf-8"))
    collect_op = schema["paths"]["/api/analytics/events/batch"]["post"]
    collect_op["requestBody"]["content"]["application/json"]["example"] = batch
    collect_op["responses"]["200"]["content"]["application/json"]["example"] = {"items": examples["acks"]["items"][:2]}
    report_op = schema["paths"]["/api/analytics/reports/activity"]["post"]
    report_op["requestBody"]["content"]["application/json"]["example"] = {"course_id": "backend", "subject_ref": None, "from_time": "2026-11-01T00:00:00Z", "to_time": "2026-11-16T00:00:00Z", "timezone": "UTC", "limit": 100}
    report_op["responses"]["200"]["content"]["application/json"]["examples"] = {key: {"value": examples[key]} for key in ("report_zero", "report_no_data", "report_stale", "report_unavailable")}
    for op in (collect_op, report_op):
        op["responses"]["429"]["content"]["application/json"]["example"] = examples["error"]
    return schema


def outputs():
    result = {ROOT / "schemas" / f"{name}.schema.json": TypeAdapter(model).json_schema() for name, model in MODELS.items()}
    result[ROOT / "openapi.json"] = openapi()
    return {path: json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n" for path, value in result.items()}


def main():
    check = "--check" in sys.argv[1:]
    openapi_only = "--openapi-only" in sys.argv[1:]
    if set(sys.argv[1:]) - {"--check", "--openapi-only"}:
        raise SystemExit("usage: export_contracts.py [--check] [--openapi-only]")
    bad = []
    for path, content in outputs().items():
        if openapi_only and path.name != "openapi.json":
            continue
        if check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                bad.append(str(path))
        elif not path.exists() or path.read_text(encoding="utf-8") != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if bad:
        raise SystemExit("outdated artifacts: " + ", ".join(bad))
    print("contract artifacts " + ("match" if check else "exported"))


if __name__ == "__main__":
    main()
