"""Repeatable failure/recovery coverage against the owned multicourse lab PG."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest


RUN_ID = "mc460-20261113-2048"
ALIAS = "mc4-synthetic-shared"
FAKE_TOKEN = "mc4-fake-only-token"
CHAT_A1 = "-700000000101"
CHAT_B1 = "-700000000202"
CHAT_A2 = "-700000000303"
CHAT_B2 = "-700000000404"


def _configure_owned_lab() -> None:
    if os.environ.get("PYMENTOR_MC4_RUN_ID") != RUN_ID:
        pytest.skip("set PYMENTOR_MC4_RUN_ID to the explicitly owned disposable PG lab")
    helper_path = Path(__file__).resolve().parents[1] / "tools" / "dev" / "multicourse_local.py"
    spec = importlib.util.spec_from_file_location("mc4_multicourse_local", helper_path)
    assert spec is not None and spec.loader is not None
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    helper.configure(RUN_ID)


def _settings(chat_a: str | None, chat_b: str | None, *, enabled_a=True, enabled_b=True):
    return {
        "backend": {
            "schema_version": 1,
            "course_id": "backend",
            "contacts": None,
            "telegram": {"enabled": enabled_a, "chat_id": chat_a, "topic_id": None},
        },
        "backend_copy": {
            "schema_version": 1,
            "course_id": "backend_copy",
            "contacts": None,
            "telegram": {"enabled": enabled_b, "chat_id": chat_b, "topic_id": None},
        },
    }


def test_real_postgres_multicourse_notification_sync_and_recovery(monkeypatch):
    _configure_owned_lab()

    # Import Core only after configure() has installed the guarded lab environment.
    from fastapi.testclient import TestClient
    from sqlalchemy import and_
    from sqlalchemy import or_
    from sqlalchemy.orm import sessionmaker

    from pymentor_core.adapters.sql import course_destinations
    from pymentor_core.adapters.sql import landings
    from pymentor_core.adapters.sql import models
    from pymentor_core.adapters.sql.notification_outbox import _claim_next_landing
    from pymentor_core.adapters.sql.notification_outbox import drain_notifications
    from pymentor_core.adapters.telegram.transport import TelegramDeliveryError
    from pymentor_core.app.asgi import create_app
    from pymentor_core.app.database import engine
    from pymentor_core.modules.landings.contracts import LandingError

    assert (engine.url.host, engine.url.port, engine.url.database, engine.url.username) == (
        "127.0.0.1", 57152, "pymentor_test", "pymentor_app"
    )
    monkeypatch.setenv("PYMENTOR_LANDING_BOT_CREDENTIALS", json.dumps({ALIAS: FAKE_TOKEN}))
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    created_landing_ids: list[str] = []
    created_config_ids: list[str] = []
    created_outbox_ids: set[int] = set()
    ledger: dict = {"external_transport": False, "courses": {}, "scenarios": {}}
    cleanup_reconciled = False

    with sessions() as db:
        before_pending = db.query(models.LandingNotificationOutbox).filter(
            models.LandingNotificationOutbox.status.in_(("pending", "processing"))
        ).count()
        collisions = db.query(models.Landing).filter(
            models.Landing.slug.in_(("backend", "backend_copy"))
        ).count()
        assert collisions == 0, "canonical slugs already exist; preserve and reconcile before retry"
        backend = landings.create_landing(db, slug="backend", title="MC4 synthetic", description="")
        backend_copy = landings.create_landing(db, slug="backend_copy", title="MC4 synthetic", description="")
        created_landing_ids.extend((backend.id, backend_copy.id))
        db.refresh(backend)
        backend.config_version = 1
        backend.telegram_enabled = False
        db.add(models.LandingTelegramConfig(
            landing_id=backend.id,
            version=1,
            secret_ref=ALIAS,
            chat_id="-700000000001",
            bot_username="unverified_seed",
            status="pending",
            verified_at=None,
        ))
        db.flush()
        created_config_ids.append(db.query(models.LandingTelegramConfig).filter_by(
            landing_id=backend.id, version=1
        ).one().id)
        db.commit()
        landing_ids = {"backend": backend.id, "backend_copy": backend_copy.id}
        versions = {"backend": backend.config_version, "backend_copy": backend_copy.config_version}

    app = create_app()
    first_settings = _settings(CHAT_A1, CHAT_B1)
    verified_calls: list[tuple[str, str]] = []

    def reader(value):
        return lambda: copy.deepcopy(value)

    def fake_verifier(token: str, chat_id: str) -> str:
        # The external verifier must run after the planning session returned its connection.
        assert engine.pool.checkedout() == 0
        verified_calls.append((token, chat_id))
        return "synthetic_bot_v1" if chat_id in {CHAT_A1, CHAT_B1} else "synthetic_bot_v2"

    def current_versions():
        with sessions() as db:
            rows = {row.slug: row for row in db.query(models.Landing).filter(
                models.Landing.id.in_(tuple(landing_ids.values()))
            ).all()}
            return {course: rows[slug].config_version for course, slug in {
                "backend": "backend", "backend_copy": "backend_copy"
            }.items()}

    def snapshot():
        with sessions() as db:
            rows = {row.slug: row for row in db.query(models.Landing).filter(
                models.Landing.id.in_(tuple(landing_ids.values()))
            ).all()}
            configs = db.query(models.LandingTelegramConfig).filter(
                models.LandingTelegramConfig.landing_id.in_(tuple(landing_ids.values()))
            ).all()
            return (
                {course: rows[slug].config_version for course, slug in {
                    "backend": "backend", "backend_copy": "backend_copy"
                }.items()},
                {course: rows[slug].telegram_enabled for course, slug in {
                    "backend": "backend", "backend_copy": "backend_copy"
                }.items()},
                {(c.landing_id, c.version, c.chat_id, c.status, c.bot_username) for c in configs},
            )

    def call_sync(value, *, expected=None, apply=True, verifier=fake_verifier):
        return course_destinations.sync_from_trusted_settings(
            sessions,
            expected_versions=expected or current_versions(),
            apply=apply,
            settings_reader=reader(value),
            verifier=verifier,
        )

    def post(client, course: str, key: str):
        return client.post(
            f"/api/landings/{course}/consultations",
            json={
                "name": "Synthetic Learner",
                "contact": "@synthetic",
                "goal": "multicourse PG test",
                "consent": True,
                "consent_version": "consultation-v3",
            },
            headers={"Idempotency-Key": key},
        )

    def fetch_event_keys(event_ids: list[str]):
        predicates = [
            and_(models.LandingNotificationOutbox.landing_id == landing_ids[course],
                 models.LandingNotificationOutbox.event_id == event_id)
            for course, event_id in event_ids
        ]
        with sessions() as db:
            return db.query(models.LandingNotificationOutbox).filter(or_(*predicates)).all()

    try:
        preview = call_sync(first_settings, expected=versions, apply=False)
        assert preview["backend"]["changed"] and preview["backend_copy"]["changed"]
        assert snapshot()[1] == {"backend": False, "backend_copy": False}

        # Explicit apply without verification proof cannot write or enable either route.
        with sessions() as db, pytest.raises(LandingError, match="отдельной проверки"):
            landings.sync_course_destinations(
                db, settings=first_settings, expected_versions=versions, apply=True
            )
        assert snapshot()[1] == {"backend": False, "backend_copy": False}

        invalid_cases = (
            _settings(CHAT_A1, CHAT_B1, enabled_b=False),
            _settings(CHAT_A1, None),
            _settings(CHAT_A1, "@not-a-numeric-chat"),
            {"backend": _settings(CHAT_A1, CHAT_B1)["backend"]},
        )
        for invalid in invalid_cases:
            before = snapshot()
            calls_before = len(verified_calls)
            with pytest.raises(LandingError):
                call_sync(invalid, apply=True)
            assert snapshot() == before
            assert len(verified_calls) == calls_before

        # Missing shared alias fails before verification and before activation.
        monkeypatch.setenv("PYMENTOR_LANDING_BOT_CREDENTIALS", "{}")
        calls_before = len(verified_calls)
        with pytest.raises(LandingError):
            call_sync(_settings(CHAT_A2, CHAT_B2), apply=True)
        assert len(verified_calls) == calls_before
        assert snapshot()[1] == {"backend": False, "backend_copy": False}
        monkeypatch.setenv("PYMENTOR_LANDING_BOT_CREDENTIALS", json.dumps({ALIAS: FAKE_TOKEN}))

        # A failed check for either destination leaves both course routes disabled.
        def second_chat_fails(token, chat_id):
            assert engine.pool.checkedout() == 0
            if chat_id == CHAT_B1:
                raise TelegramDeliveryError("synthetic verifier rejection", retryable=False)
            return fake_verifier(token, chat_id)

        before = snapshot()
        with pytest.raises(TelegramDeliveryError):
            call_sync(first_settings, expected=versions, apply=True, verifier=second_chat_fails)
        assert snapshot() == before
        applied = call_sync(first_settings, expected=versions, apply=True)
        assert applied["backend"]["changed"]
        assert verified_calls[-2:] == [(FAKE_TOKEN, CHAT_A1), (FAKE_TOKEN, CHAT_B1)]
        with sessions() as db:
            new_configs = db.query(models.LandingTelegramConfig).filter(
                models.LandingTelegramConfig.landing_id.in_(tuple(landing_ids.values()))
            ).all()
            created_config_ids.extend(c.id for c in new_configs)
            assert {(c.chat_id, c.bot_username, c.status) for c in new_configs if c.chat_id in {CHAT_A1, CHAT_B1}} == {
                (CHAT_A1, "synthetic_bot_v1", "verified"),
                (CHAT_B1, "synthetic_bot_v1", "verified"),
            }
        assert snapshot()[1] == {"backend": True, "backend_copy": True}

        shared_id = str(uuid4())
        concurrent_id = str(uuid4())
        old_ids = [("backend", shared_id), ("backend_copy", shared_id)]
        with TestClient(app) as client:
            backend_response = post(client, "backend", shared_id)
            duplicate_response = post(client, "backend", shared_id)
            copy_response = post(client, "backend_copy", shared_id)
            assert [backend_response.status_code, duplicate_response.status_code, copy_response.status_code] == [202, 200, 202]
            assert duplicate_response.json()["duplicate"] is True
            assert len(fetch_event_keys(old_ids)) == 2
            created_outbox_ids.update(row.id for row in fetch_event_keys(old_ids))

            barrier = Barrier(4)
            payload = {
                "name": "Synthetic Learner",
                "contact": "@synthetic",
                "goal": "multicourse PG test",
                    "consented_at": "2026-11-14T09:00:00+00:00",
                "consent_version": "consultation-v3",
            }

            def concurrent_submit(_index):
                barrier.wait(timeout=5)
                # The SQL adapter is the same command used by the HTTP endpoint; each worker owns a session.
                with sessions() as db:
                    return landings.submit_course_consultation(
                        db,
                        "backend",
                        concurrent_id,
                            payload,
                    )

            with ThreadPoolExecutor(max_workers=4) as pool:
                concurrent_results = list(pool.map(concurrent_submit, range(4)))
            assert len({event.id for event, _ in concurrent_results}) == 1
            assert sum(not duplicate for _, duplicate in concurrent_results) == 1
            assert len(fetch_event_keys([("backend", concurrent_id)])) == 1
            created_outbox_ids.update(row.id for row in fetch_event_keys([("backend", concurrent_id)]))

            # Replace both destinations. Queued events above must keep config v1.
            second_settings = _settings(CHAT_A2, CHAT_B2)
            call_sync(second_settings, apply=True)
            with sessions() as db:
                created_config_ids.extend(
                    row.id
                    for row in db.query(models.LandingTelegramConfig).filter(
                        models.LandingTelegramConfig.landing_id.in_(tuple(landing_ids.values()))
                    ).all()
                    if row.id not in created_config_ids
                )
            new_ids = [("backend", str(uuid4())), ("backend_copy", str(uuid4()))]
            new_backend = post(client, "backend", new_ids[0][1])
            new_copy = post(client, "backend_copy", new_ids[1][1])
            timeout_id = str(uuid4())
            restart_id = str(uuid4())
            assert post(client, "backend", timeout_id).status_code == 202
            assert post(client, "backend", restart_id).status_code == 202
            created_outbox_ids.update(row.id for row in fetch_event_keys(new_ids + [("backend", timeout_id), ("backend", restart_id)]))

        event_keys = old_ids + [("backend", concurrent_id)] + new_ids + [
            ("backend", timeout_id), ("backend", restart_id)
        ]
        all_rows = fetch_event_keys(event_keys)
        assert len(all_rows) == len(event_keys)
        created_outbox_ids.update(row.id for row in all_rows)
        route_by_landing = {landing_ids["backend"]: "backend", landing_ids["backend_copy"]: "backend_copy"}
        row_by_key = {(route_by_landing[row.landing_id], row.event_id): row for row in all_rows}
        assert row_by_key[old_ids[0]].destination_config_id is not None
        assert row_by_key[old_ids[1]].destination_config_id is not None
        assert row_by_key[old_ids[0]].destination_config_id != row_by_key[new_ids[0]].destination_config_id
        assert row_by_key[old_ids[1]].destination_config_id != row_by_key[new_ids[1]].destination_config_id
        assert row_by_key[old_ids[0]].destination_config_id != row_by_key[old_ids[1]].destination_config_id

        # Simulate crash after committed claim, expire the lease, then recover in a new worker call.
        restart_key = (landing_ids["backend"], restart_id)
        with sessions() as db:
            claimed = _claim_next_landing(db, event_keys=frozenset({restart_key}))
            assert claimed is not None and claimed.chat_id == CHAT_A2
        with sessions() as db:
            restart_row = db.query(models.LandingNotificationOutbox).filter_by(
                landing_id=landing_ids["backend"], event_id=restart_id
            ).one()
            restart_row.locked_until = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        restart_sink: list[tuple[str, str]] = []
        restart_counts = drain_notifications(
            session_factory=sessions,
            limit=1,
            budget_seconds=5,
            landing_event_keys=frozenset({restart_key}),
            send=lambda event: restart_sink.append((event.course_id, event.chat_id)),
        )
        assert restart_counts["sent"] == 1
        assert restart_sink == [("backend", CHAT_A2)]

        timeout_key = (landing_ids["backend"], timeout_id)
        timeout_sink: list[tuple[str, str]] = []

        def accept_then_timeout(event):
            timeout_sink.append((event.course_id, event.chat_id))
            raise TimeoutError("synthetic timeout after fake sink acceptance")

        timeout_first = drain_notifications(
            session_factory=sessions,
            limit=1,
            budget_seconds=5,
            landing_event_keys=frozenset({timeout_key}),
            send=accept_then_timeout,
        )
        assert timeout_first["retry"] == 1
        with sessions() as db:
            timeout_row = db.query(models.LandingNotificationOutbox).filter_by(
                landing_id=landing_ids["backend"], event_id=timeout_id
            ).one()
            pinned_timeout_config = timeout_row.destination_config_id
            timeout_row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        timeout_second = drain_notifications(
            session_factory=sessions,
            limit=1,
            budget_seconds=5,
            landing_event_keys=frozenset({timeout_key}),
            send=lambda event: timeout_sink.append((event.course_id, event.chat_id)),
        )
        assert timeout_second["sent"] == 1
        assert timeout_sink == [("backend", CHAT_A2), ("backend", CHAT_A2)]

        main_sink: dict[str, list[tuple[str, str]]] = {"backend": [], "backend_copy": []}
        main_keys = frozenset(
            (landing_ids[course], event_id)
            for course, event_id in old_ids + [("backend", concurrent_id)] + new_ids
        )
        main_counts = drain_notifications(
            session_factory=sessions,
            limit=len(main_keys),
            budget_seconds=8,
            landing_event_keys=main_keys,
            send=lambda event: main_sink[event.course_id].append((event.event_id, event.chat_id)),
        )
        assert main_counts["sent"] == len(main_keys)
        backend_deliveries = dict(main_sink["backend"])
        copy_deliveries = dict(main_sink["backend_copy"])
        assert backend_deliveries[shared_id] == CHAT_A1
        assert backend_deliveries[concurrent_id] == CHAT_A1
        assert backend_deliveries[new_ids[0][1]] == CHAT_A2
        assert copy_deliveries[shared_id] == CHAT_B1
        assert copy_deliveries[new_ids[1][1]] == CHAT_B2

        with sessions() as db:
            final_rows = fetch_event_keys(event_keys)
            assert all(row.status == "sent" for row in final_rows)
            timeout_final = db.query(models.LandingNotificationOutbox).filter_by(
                landing_id=landing_ids["backend"], event_id=timeout_id
            ).one()
            restart_final = db.query(models.LandingNotificationOutbox).filter_by(
                landing_id=landing_ids["backend"], event_id=restart_id
            ).one()
            assert timeout_final.attempts == 2 and timeout_final.destination_config_id == pinned_timeout_config
            assert restart_final.attempts == 2 and restart_final.status == "sent"
            after_pending = db.query(models.LandingNotificationOutbox).filter(
                models.LandingNotificationOutbox.status.in_(("pending", "processing"))
            ).count()
            assert after_pending == before_pending
        cleanup_reconciled = True
        ledger.update({
            "course_sinks": {
                "backend": sorted({chat for _, chat in main_sink["backend"]} | {chat for _, chat in timeout_sink} | {chat for _, chat in restart_sink}),
                "backend_copy": sorted({chat for _, chat in main_sink["backend_copy"]}),
            },
            "http_statuses": [202, 200, 202],
            "same_event_id_across_courses": True,
            "concurrent_same_course_submits": {"calls": 4, "rows": 1, "created": 1},
            "config_change_pinned": True,
            "timeout_after_accept": {"attempts": timeout_final.attempts, "same_destination": timeout_sink[0] == timeout_sink[1], "final": timeout_final.status},
            "expired_lease_reclaim": {"attempts": restart_final.attempts, "final": restart_final.status},
            "other_pending_before": before_pending,
            "other_pending_after": after_pending,
            "synthetic_event_rows": len(created_outbox_ids),
        })
    finally:
        # Only delete this test's outbox IDs after reconciling every owned row as terminal.
        if created_outbox_ids:
            with sessions() as db:
                owned = db.query(models.LandingNotificationOutbox).filter(
                    models.LandingNotificationOutbox.id.in_(created_outbox_ids)
                ).all()
                if cleanup_reconciled and len(owned) == len(created_outbox_ids) and all(
                    row.status in {"sent", "failed"} for row in owned
                ):
                    db.query(models.LandingNotificationOutbox).filter(
                        models.LandingNotificationOutbox.id.in_(created_outbox_ids)
                    ).delete(synchronize_session=False)
                    db.commit()
                else:
                    ledger["cleanup"] = "preserved_unreconciled_synthetic_rows"
        if created_landing_ids and (cleanup_reconciled or not created_outbox_ids):
            with sessions() as db:
                foreign_outbox = db.query(models.LandingNotificationOutbox.id).filter(
                    models.LandingNotificationOutbox.landing_id.in_(created_landing_ids)
                ).count()
                if foreign_outbox == 0:
                    if created_config_ids:
                        db.query(models.LandingTelegramConfig).filter(
                            models.LandingTelegramConfig.id.in_(set(created_config_ids))
                        ).delete(synchronize_session=False)
                    db.query(models.Landing).filter(models.Landing.id.in_(created_landing_ids)).delete(
                        synchronize_session=False
                    )
                    db.commit()
                    ledger["cleanup"] = "synthetic_rows_removed"
                else:
                    ledger["cleanup"] = "preserved_rows_for_reconciliation"
        engine.dispose()
    print("MC4_PG_LEDGER=" + json.dumps(ledger, sort_keys=True))
