import importlib.util
import random
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from pymentor_analytics.contracts.reports_v1 import ActivityQuery, ActivityReport
from pymentor_analytics.modules.analytics.activity.calculator import calculate

spec = importlib.util.spec_from_file_location("golden_helpers", Path(__file__).parents[1] / "reference/test_activity_v1.py")
golden = importlib.util.module_from_spec(spec)
spec.loader.exec_module(golden)

def measured(records, **kwargs):
    return calculate(records, start=golden.BASE, end=golden.BASE + timedelta(hours=1),
                     as_of=golden.BASE + timedelta(days=8), **kwargs)

@pytest.mark.parametrize("case", ["idle_resume", "visibility_focus_sleep", "courses", "overlap"])
def test_golden_and_oracle_parity(case):
    records = golden.expand(golden.FIXTURES[case]["runs"])
    for course in (None, "backend", "backend_copy"):
        actual = measured(records, course_id=course)
        expected = golden.report(records, course_id=course)
        assert actual["totals"] == {"unique_visitors": expected["visitors"], "active_ms": expected["total_ms"],
                                    "theory_ms": expected["theory_ms"], "practice_ms": expected["practice_ms"]}
        if case == "idle_resume" and course is None:
            assert actual["totals"]["active_ms"] == golden.FIXTURES[case]["expected"]["after_resume_ms"]
        if case == "courses" and course == "backend":
            assert actual["totals"]["active_ms"] == golden.FIXTURES[case]["expected"]["backend_total_ms"]
        if case == "overlap" and course == "backend":
            assert actual["totals"]["active_ms"] == 0
            assert actual["totals"]["unique_visitors"] == 1

def test_permutation_and_subject_union_properties():
    rng = random.Random(872)
    records = []
    for i in range(140):
        start = rng.randrange(0, 300)
        records.append(golden.event(golden.BASE + timedelta(seconds=start), golden.BASE + timedelta(seconds=start + 15),
                                    number=i+1, course=rng.choice(["backend", "backend_copy"]),
                                    section=rng.choice(["theory", "practice"]), subject=rng.choice([golden.SUBJECT, golden.OTHER])))
    actual = measured(records)
    rng.shuffle(records)
    assert measured(records) == actual
    oracle = golden.report(records)
    assert actual["totals"]["active_ms"] == oracle["total_ms"]
    assert actual["totals"]["active_ms"] <= 2 * 3600 * 1000
    assert sum(measured(records, course_id=c)["totals"]["active_ms"] for c in ("backend", "backend_copy")) == actual["totals"]["active_ms"]
    with pytest.raises(ValueError):
        measured(records + [records[0]])

def test_midnight_visitor_and_loser_course():
    start = golden.BASE + timedelta(hours=20, minutes=59, seconds=45)
    records = [golden.event(start, start + timedelta(seconds=30), number=1, course="backend"),
               golden.event(start, start + timedelta(seconds=30), number=2, course="backend_copy")]
    result = calculate(records, start=start, end=start + timedelta(seconds=30),
                       as_of=start + timedelta(days=8), course_id="backend")
    assert result["totals"]["unique_visitors"] == 1
    assert result["totals"]["active_ms"] == 0
    assert [row["unique_visitors"] for row in result["daily"]] == [1, 1]

def test_dto_is_bounded_and_unknown_is_not_zero():
    common = dict(course_id="backend", from_time=golden.BASE, to_time=golden.BASE + timedelta(days=1))
    query = ActivityQuery(subject_ref=golden.SUBJECT, **common)
    assert query.timezone == "Europe/Moscow"
    unavailable = ActivityReport(status="unavailable", quality="unknown", as_of=None, lag_seconds=None,
                                 totals=None, daily=[], **common)
    assert unavailable.totals is None
    with pytest.raises(ValidationError):
        ActivityReport(status="ok", quality="unknown", as_of=None, lag_seconds=None, totals=None, daily=[], **common)
    with pytest.raises(ValidationError):
        ActivityReport(status="no_data", quality="unknown", as_of=None, lag_seconds=None, totals=None, daily=[], **common)
    with pytest.raises(ValidationError):
        ActivityQuery(subject_ref=golden.SUBJECT, **{**common, "to_time": golden.BASE + timedelta(days=32)})

@pytest.mark.parametrize("name", ["reports-v1-ok.json", "reports-v1-unavailable.json", "reports-v1-observed.json"])
def test_wire_fixtures(name):
    path = Path(__file__).parents[2] / "contracts/fixtures" / name
    parsed = ActivityReport.model_validate_json(path.read_text(encoding="utf-8"))
    assert ActivityReport.model_validate_json(parsed.model_dump_json()) == parsed

def test_submillisecond_quantization_and_namespace_tie():
    start = golden.BASE + timedelta(microseconds=999)
    end = start + timedelta(milliseconds=1001)
    one = golden.event(start, end, number=1, namespace="a", course="backend")
    two = golden.event(start, end, number=1, namespace="z", course="backend_copy")
    result = calculate([one, two], start=golden.BASE+timedelta(milliseconds=1),
                       end=golden.BASE+timedelta(milliseconds=1000),
                       as_of=golden.BASE+timedelta(days=8), course_id="backend_copy")
    assert result["totals"]["active_ms"] == 999
    assert result["totals"]["unique_visitors"] == 1
    assert measured([one, two], course_id="backend")["totals"]["active_ms"] == 0

def test_cutoff_and_empty_are_measurements_only():
    item = golden.event(golden.BASE, golden.BASE+timedelta(seconds=15), number=1)
    out = calculate([item], start=golden.BASE, end=golden.BASE+timedelta(minutes=1),
                    as_of=golden.BASE+timedelta(seconds=10))
    assert out["totals"]["active_ms"] == 0  # record was not yet received
    out = calculate([], start=golden.BASE, end=golden.BASE+timedelta(minutes=1), as_of=golden.BASE)
    assert out["totals"]["unique_visitors"] == 0  # caller must establish coverage

def test_course_allowlist_applied_after_competing_course():
    records = [golden.event(golden.BASE, golden.BASE+timedelta(seconds=15), number=1, course="backend"),
               golden.event(golden.BASE, golden.BASE+timedelta(seconds=15), number=2, course="backend_copy")]
    out = measured(records, allowed_courses=("backend",))
    assert out["totals"]["unique_visitors"] == 1 and out["totals"]["active_ms"] == 0

def test_lesson_boundary_filters_after_union_and_same_id_across_courses():
    def lesson(number, course, name, start=0, end=15, subject=golden.SUBJECT):
        record=golden.event(golden.BASE+timedelta(seconds=start),golden.BASE+timedelta(seconds=end),
                            number=number,course=course,subject=subject)
        return record.model_copy(update={"event":record.event.model_copy(update={"lesson_id":name})})
    records=[lesson(1,"backend","a"),lesson(2,"backend","b",5,10),
             lesson(3,"backend_copy","a",10,15),lesson(4,"backend","a",subject=golden.OTHER)]
    out=measured(records,course_id="backend",lesson_id="a",include_breakdown=True)
    assert out["totals"]["active_ms"]==20000
    assert out["totals"]["unique_visitors"]==2
    assert [r["level"] for r in out["breakdown"]]==["course","lesson"]
    assert out["breakdown"][1]["lesson_id"]=="a"
    assert out["breakdown"][1]["active_ms"]==20000
    shuffled=list(reversed(records))
    assert measured(shuffled,course_id="backend",lesson_id="a",include_breakdown=True)==out
    all_courses=measured(records,include_breakdown=True)
    assert {r["course_id"] for r in all_courses["breakdown"] if r["lesson_id"]=="a"}=={"backend","backend_copy"}
    for row in all_courses["breakdown"]:
        assert row["active_ms"]==row["theory_ms"]+row["practice_ms"]

def test_lesson_does_not_count_course_open_only():
    opened=golden.event(golden.BASE,golden.BASE,number=1,opened=True)
    result=measured([opened],course_id="backend",lesson_id="same-lesson",include_breakdown=True)
    assert result["totals"]["unique_visitors"]==0 and result["breakdown"]==[]

def test_lesson_midnight_and_losing_segment_visitor():
    start=golden.BASE+timedelta(hours=20,minutes=59,seconds=45)
    first=golden.event(start,start+timedelta(seconds=30),number=1,course="backend")
    second=golden.event(start,start+timedelta(seconds=30),number=2,course="backend")
    second=second.model_copy(update={"event":second.event.model_copy(update={"lesson_id":"other"})})
    result=calculate([first,second],start=start,end=start+timedelta(seconds=30),
                     as_of=start+timedelta(days=8),course_id="backend",lesson_id="same-lesson",include_breakdown=True)
    assert result["totals"]["unique_visitors"]==1 and result["totals"]["active_ms"]==0
    assert [row["unique_visitors"] for row in result["daily"]]==[1,1]
    assert result["breakdown"][1]["unique_visitors"]==1

def test_typed_details_fixture():
    from pymentor_analytics.contracts.reports_v1 import ActivityDetailsReport
    path=Path(__file__).parents[2]/"contracts/fixtures/reports-v1-details.json"
    parsed=ActivityDetailsReport.model_validate_json(path.read_text(encoding="utf-8"))
    assert parsed.report.quality=="unknown" and parsed.next_offset==1
    assert parsed.report.trusted_progress.state=="unavailable"

def test_randomized_lesson_partition_and_permutation():
    rng=random.Random(20261117)
    records=[]
    for i in range(160):
        start=rng.randrange(0,300)
        record=golden.event(golden.BASE+timedelta(seconds=start),golden.BASE+timedelta(seconds=start+15),
                            number=i+1,course=rng.choice(["backend","backend_copy"]),
                            subject=rng.choice([golden.SUBJECT,golden.OTHER]),section=rng.choice(["theory","practice"]))
        record=record.model_copy(update={"event":record.event.model_copy(update={"lesson_id":rng.choice(["a","b","c"])})})
        records.append(record)
    whole=measured(records,include_breakdown=True)
    rng.shuffle(records)
    assert measured(records,include_breakdown=True)==whole
    for course in ("backend","backend_copy"):
        one=measured(records,course_id=course)
        pieces=[measured(records,course_id=course,lesson_id=lesson)["totals"]["active_ms"] for lesson in ("a","b","c")]
        assert sum(pieces)==one["totals"]["active_ms"]
        assert one["totals"]["active_ms"]==golden.report(records,course_id=course)["total_ms"]
