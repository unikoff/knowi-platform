import importlib.util
import random
from datetime import timedelta
from pathlib import Path
from uuid import UUID
import pytest
from pymentor_analytics.modules.analytics.activity import bucket
from pymentor_analytics.modules.analytics.activity.calculator import _ms

spec=importlib.util.spec_from_file_location("bucket_golden",Path(__file__).parents[1]/"reference/test_activity_v1.py")
g=importlib.util.module_from_spec(spec); spec.loader.exec_module(g)

def clip_result(buckets, lo, hi, *, course=None, lesson=None):
    visitors=set(); theory=practice=0
    for subject, data in buckets:
        for row in data["winner_intervals"]:
            if course is not None and row["course_id"]!=course: continue
            if lesson is not None and row["lesson_id"]!=lesson: continue
            amount=max(0,min(hi,row["end_ms"])-max(lo,row["start_ms"]))
            if row["section"]=="theory": theory+=amount
            else: practice+=amount
        for row in data["presence_intervals"]:
            if course is not None and row["course_id"]!=course: continue
            if lesson is not None and row["lesson_id"]!=lesson: continue
            if row["start_ms"]<hi and row["end_ms"]>lo: visitors.add(subject)
        for row in data["presence_points"]:
            if lesson is not None or (course is not None and row["course_id"]!=course): continue
            if lo<=row["at_ms"]<hi: visitors.add(subject)
    return len(visitors),theory,practice

def test_bucket_overlap_boundaries_microseconds_and_loser_presence():
    boundary=g.BASE+timedelta(minutes=15)
    start=boundary-timedelta(seconds=10)+timedelta(microseconds=999)
    first=g.event(start,start+timedelta(seconds=20),number=1,course="backend",namespace="a")
    second=g.event(start+timedelta(microseconds=100),start+timedelta(seconds=20,microseconds=100),
                   number=2,course="backend_copy",namespace="b",section="practice")
    left=bucket.normalize_subject_bucket([first,second],subject_ref=g.SUBJECT,bucket_start=g.BASE)
    right=bucket.normalize_subject_bucket([second,first],subject_ref=g.SUBJECT,bucket_start=boundary)
    assert left["winner_intervals"][0]["course_id"]=="backend"
    assert left["winner_intervals"][0]["end_ms"]-left["winner_intervals"][0]["start_ms"]==1
    assert left["winner_intervals"][1]["course_id"]=="backend_copy"
    assert right["winner_intervals"][0]["course_id"]=="backend_copy"
    assert len(left["presence_intervals"])==2 and len(right["presence_intervals"])==2
    assert {r["course_id"] for r in left["presence_labels"]}=={"backend","backend_copy"}
    assert all(r["active_ms"]>0 for r in left["contributions"])
    assert sum(r["active_ms"] for r in left["contributions"])+sum(r["active_ms"] for r in right["contributions"])==20001
    assert clip_result([(g.SUBJECT,left),(g.SUBJECT,right)],_ms(start)+1,_ms(start)+19999,course="backend")== (1,0,0)

def test_course_opened_half_open_zero_time_and_labels():
    boundary=g.BASE+timedelta(minutes=15)
    open_event=g.event(boundary,boundary,opened=True)
    left=bucket.normalize_subject_bucket([open_event],subject_ref=g.SUBJECT,bucket_start=g.BASE)
    right=bucket.normalize_subject_bucket([open_event],subject_ref=g.SUBJECT,bucket_start=boundary)
    assert not left["presence_points"] and not left["presence_labels"]
    assert right["presence_points"]==[{"at_ms":_ms(boundary),"course_id":"backend"}]
    assert right["presence_labels"]==[{"course_id":"backend","lesson_id":None}]
    assert not right["winner_intervals"] and not right["contributions"]

def test_rejects_duplicate_mixed_subject_invalid_bucket_and_caps(monkeypatch):
    one=g.event(g.BASE,g.BASE+timedelta(seconds=15))
    other=g.event(g.BASE,g.BASE+timedelta(seconds=15),subject=g.OTHER,number=2)
    with pytest.raises(ValueError,match="deduplicated"):
        bucket.normalize_subject_bucket([one,one],subject_ref=g.SUBJECT,bucket_start=g.BASE)
    with pytest.raises(ValueError,match="another subject"):
        bucket.normalize_subject_bucket([one,other],subject_ref=g.SUBJECT,bucket_start=g.BASE)
    with pytest.raises(ValueError,match="boundary"):
        bucket.normalize_subject_bucket([one],subject_ref=g.SUBJECT,bucket_start=g.BASE+timedelta(seconds=1))
    monkeypatch.setattr(bucket,"MAX_BUCKET_FACTS",0)
    with pytest.raises(bucket.BucketOverflow):
        bucket.normalize_subject_bucket([one],subject_ref=g.SUBJECT,bucket_start=g.BASE)
    monkeypatch.setattr(bucket,"MAX_BUCKET_FACTS",10)
    monkeypatch.setattr(bucket,"MAX_BUCKET_INPUT_BYTES",1)
    with pytest.raises(bucket.BucketOverflow):
        bucket.normalize_subject_bucket([one],subject_ref=g.SUBJECT,bucket_start=g.BASE)
    monkeypatch.setattr(bucket,"MAX_BUCKET_INPUT_BYTES",10000)
    monkeypatch.setattr(bucket,"MAX_BUCKET_OUTPUT_BYTES",1)
    with pytest.raises(bucket.BucketOverflow):
        bucket.normalize_subject_bucket([one],subject_ref=g.SUBJECT,bucket_start=g.BASE)

def test_randomized_bucket_edges_match_oracle_across_midnight_and_subjects():
    rng=random.Random(10117)
    base=g.BASE+timedelta(hours=20,minutes=45)
    records=[]
    for i in range(110):
        start=base+timedelta(milliseconds=rng.randrange(0,1_700_000),microseconds=rng.randrange(0,1000))
        event=g.event(start,start+timedelta(seconds=15),number=i+1,
                      course=rng.choice(("backend","backend_copy")),section=rng.choice(("theory","practice")),
                      subject=rng.choice((g.SUBJECT,g.OTHER)),namespace=rng.choice(("a","b")))
        records.append(event)
    rows=[]
    for subject in (g.SUBJECT,g.OTHER):
        subject_records=[r for r in records if r.subject_ref==subject]
        for minute in (0,15):
            start=base+timedelta(minutes=minute)
            value=bucket.normalize_subject_bucket(subject_records,subject_ref=subject,bucket_start=start)
            assert value==bucket.normalize_subject_bucket(list(reversed(subject_records)),subject_ref=subject,bucket_start=start)
            rows.append((subject,value))
    for _ in range(25):
        left=base+timedelta(milliseconds=rng.randrange(0,1_300_000))
        right=min(base+timedelta(minutes=30),left+timedelta(milliseconds=rng.randrange(1,500_000)))
        for course in (None,"backend","backend_copy"):
            expected=g.report(records,start=left,end=right,as_of=base+timedelta(days=8),course_id=course)
            assert clip_result(rows,_ms(left),_ms(right),course=course)==(expected["visitors"],expected["theory_ms"],expected["practice_ms"])

def model_full_and_edge(rows,lo,hi,*,course=None,lesson=None):
    from collections import defaultdict
    from datetime import datetime, timezone
    visits=[]; times=[]
    for subject,start,data in rows:
        bucket_ms=_ms(start); day=(start+timedelta(hours=3)).date()
        full=lo<=bucket_ms and bucket_ms+bucket.BUCKET_MS<=hi
        if full:
            for r in data["contributions"]:
                if (course is None or r["course_id"]==course) and (lesson is None or r["lesson_id"]==lesson):
                    times.append((day,r["course_id"],r["lesson_id"],r["section"],r["active_ms"]))
            for r in data["presence_labels"]:
                if (course is None or r["course_id"]==course) and (lesson is None or r["lesson_id"]==lesson):
                    visits.append((subject,day,r["course_id"],r["lesson_id"]))
        else:
            for r in data["winner_intervals"]:
                if (course is None or r["course_id"]==course) and (lesson is None or r["lesson_id"]==lesson):
                    amount=max(0,min(hi,r["end_ms"])-max(lo,r["start_ms"]))
                    if amount: times.append((day,r["course_id"],r["lesson_id"],r["section"],amount))
            for r in data["presence_intervals"]:
                if (course is None or r["course_id"]==course) and (lesson is None or r["lesson_id"]==lesson) and r["start_ms"]<hi and r["end_ms"]>lo:
                    visits.append((subject,day,r["course_id"],r["lesson_id"]))
            for r in data["presence_points"]:
                if lesson is None and (course is None or r["course_id"]==course) and lo<=r["at_ms"]<hi:
                    visits.append((subject,day,r["course_id"],None))
    def values(selected_visits,selected_times):
        theory=sum(t[4] for t in selected_times if t[3]=="theory")
        practice=sum(t[4] for t in selected_times if t[3]=="practice")
        return {"unique_visitors":len({v[0] for v in selected_visits}),"active_ms":theory+practice,
                "theory_ms":theory,"practice_ms":practice}
    days=sorted({v[1] for v in visits}|{t[0] for t in times})
    daily=[{"date":day,**values([v for v in visits if v[1]==day],[t for t in times if t[0]==day])} for day in days]
    keys={(r[2],None) for r in visits}|{(r[1],None) for r in times}
    keys|={(r[2],r[3]) for r in visits if r[3] is not None}|{(r[1],r[2]) for r in times}
    details=[]
    for c,l in sorted(keys,key=lambda k:(k[0],k[1] is not None,k[1] or "")):
        v=[x for x in visits if x[2]==c and (l is None or x[3]==l)]
        t=[x for x in times if x[1]==c and (l is None or x[2]==l)]
        details.append({"level":"course" if l is None else "lesson","course_id":c,"lesson_id":l,**values(v,t)})
    return {"totals":values(visits,times),"daily":daily,"breakdown":details}

def test_compact_full_bucket_and_edge_semantics_match_report_with_filters():
    from pymentor_analytics.modules.analytics.activity.calculator import calculate
    rng=random.Random(3219)
    base=g.BASE+timedelta(hours=20,minutes=45)
    records=[]
    for i in range(85):
        start=base+timedelta(milliseconds=rng.randrange(0,3_500_000),microseconds=rng.randrange(0,1000))
        item=g.event(start,start+timedelta(seconds=15),number=i+1,subject=rng.choice((g.SUBJECT,g.OTHER)),
                     course=rng.choice(("backend","backend_copy")),section=rng.choice(("theory","practice")))
        item=item.model_copy(update={"event":item.event.model_copy(update={"lesson_id":rng.choice(("a","b"))})})
        records.append(item)
    records.append(g.event(base+timedelta(minutes=15),base+timedelta(minutes=15),opened=True,number=1000))
    for excluded in (None,g.OTHER):
        selected=[r for r in records if r.subject_ref!=excluded]
        rows=[]
        for subject in (g.SUBJECT,g.OTHER):
            if subject==excluded: continue
            subset=[r for r in selected if r.subject_ref==subject]
            for minutes in (0,15,30,45):
                start=base+timedelta(minutes=minutes)
                rows.append((subject,start,bucket.normalize_subject_bucket(subset,subject_ref=subject,bucket_start=start)))
        cases=[(base,base+timedelta(minutes=60)),(base+timedelta(milliseconds=1),base+timedelta(minutes=45,milliseconds=3)),
               (base+timedelta(minutes=14,seconds=59),base+timedelta(minutes=30,seconds=1))]
        for _ in range(15):
            left=base+timedelta(milliseconds=rng.randrange(0,3_000_000))
            right=min(base+timedelta(minutes=60),left+timedelta(milliseconds=rng.randrange(1,1_500_000)))
            cases.append((left,right))
        for left,right in cases:
            for course,lesson in ((None,None),("backend",None),("backend_copy","a")):
                expected=calculate(selected,start=left,end=right,as_of=base+timedelta(days=8),course_id=course,
                                   lesson_id=lesson,include_breakdown=True)
                assert model_full_and_edge(rows,_ms(left),_ms(right),course=course,lesson=lesson)==expected


def test_future_points_and_segments_are_clipped_at_snapshot_across_moscow_midnight():
    midnight=g.BASE+timedelta(hours=21)
    snapshot=midnight+timedelta(seconds=2,microseconds=999)
    events=[
        g.event(midnight-timedelta(seconds=8),midnight+timedelta(seconds=7),number=101),
        g.event(midnight+timedelta(seconds=5),midnight+timedelta(seconds=5),opened=True,number=102,subject=g.OTHER),
        g.event(midnight+timedelta(seconds=10),midnight+timedelta(seconds=25),number=103,subject=g.OTHER),
    ]
    rows=[]
    for subject in (g.SUBJECT,g.OTHER):
        subset=[event for event in events if event.subject_ref==subject]
        for start in (midnight-timedelta(minutes=15),midnight):
            rows.append((subject,start,bucket.normalize_subject_bucket(subset,subject_ref=subject,bucket_start=start)))
    observed=model_full_and_edge(rows,_ms(midnight-timedelta(seconds=10)),_ms(snapshot))
    assert observed["totals"]=={"unique_visitors":1,"active_ms":10000,"theory_ms":10000,"practice_ms":0}
    assert [day["date"] for day in observed["daily"]]==[(midnight-timedelta(seconds=1)+timedelta(hours=3)).date(),
                                                         (midnight+timedelta(hours=3)).date()]
    later=model_full_and_edge(rows,_ms(midnight-timedelta(seconds=10)),_ms(midnight+timedelta(seconds=30)))
    assert later["totals"]["unique_visitors"]==2 and later["totals"]["active_ms"]==30000

def test_normalizer_output_is_accepted_by_writer_metadata_contract():
    import json
    from pymentor_analytics.adapters.postgres.activity_projection import _valid_projection
    from pymentor_analytics.modules.analytics.activity.projection_ports import BucketClaim
    claim=BucketClaim(g.SUBJECT,g.BASE,1,0,UUID(int=1),UUID(int=2),UUID(int=3))
    first=g.event(g.BASE,g.BASE+timedelta(seconds=15),number=1)
    second=g.event(g.BASE+timedelta(seconds=5),g.BASE+timedelta(seconds=20),number=2,course="backend_copy")
    value=bucket.normalize_subject_bucket([first,second],subject_ref=g.SUBJECT,bucket_start=g.BASE)
    _,stats=_valid_projection(claim,value)
    assert stats["compact_count"]==len(value["contributions"])+len(value["presence_labels"])
    assert stats["edge_count"]==sum(len(value[k]) for k in ("winner_intervals","presence_intervals","presence_points"))
    def length(name):
        return len(json.dumps(value[name],ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode("utf8"))
    assert stats["compact_bytes"]==length("contributions")+length("presence_labels")
    assert stats["edge_bytes"]==sum(length(k) for k in ("winner_intervals","presence_intervals","presence_points"))
