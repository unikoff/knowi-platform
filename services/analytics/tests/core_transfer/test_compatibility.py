import json
from datetime import date
from pathlib import Path

from pymentor_analytics.modules.analytics.core_transfer.compatibility import admin_activity, calendar, summary

FIXTURE = Path(__file__).resolve().parents[3] / "core-api/contracts/analytics-transfer-parity-fixtures.json"


def test_core_public_response_fixture():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    today = date.fromisoformat(fixture["today"])
    visits = fixture["subject_visits"]
    completions = [{"completed_at": value} for value in fixture["completed_at"]] + [{"completed_at": None}]
    assert calendar(visits, date(2026, 11, 1), date(2026, 11, 30)) == fixture["calendar"]
    assert summary(today, visits, completions) == fixture["summary"]
    admin = fixture["admin_case"]
    users = [{"subject_ref": row["subject"], "activity_date": row["activity_date"]} for row in admin["subjects"]]
    students = {row["subject"] for row in admin["subjects"] if not row["is_admin"]}
    assert admin_activity(today, users, students) == {key: value for key, value in admin.items() if key != "subjects"}
