from __future__ import annotations

import json
import os
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest import TestCase
from unittest.mock import patch

from pydantic import ValidationError
import pytest
from test_c2_1_4_auth_legacy import runtime_db

from pymentor_core.adapters.sql import course_destinations
from pymentor_core.adapters.sql import landings
from pymentor_core.adapters.sql import models
from pymentor_core.adapters.sql.notification_outbox import drain_notifications
from pymentor_core.modules.landings.api.schemas import LandingConsultationRequest
from pymentor_core.modules.landings.contracts import LandingError


ALIAS = "shared-course-bot"
SECRET = "synthetic-bot-secret"


def settings(course_id: str, chat_id: str, *, enabled: bool = True) -> dict:
    return {
        "schema_version": 1,
        "course_id": course_id,
        "contacts": None,
        "telegram": {"enabled": enabled, "chat_id": chat_id, "topic_id": None},
    }


class MulticourseNotificationRoutingTests(TestCase):
    @pytest.fixture(autouse=True)
    def database(self, runtime_db):
        self.sessions = runtime_db

    def setUp(self) -> None:
        self.db = self.sessions()
        self.backend = landings.create_landing(
            self.db, slug="backend", title="Backend", description=""
        )
        self.backend_copy = landings.create_landing(
            self.db, slug="backend_copy", title="Backend copy", description=""
        )
        self.backend_copy.telegram_enabled = False
        self.db.commit()
        self.original = landings.configure_landing_telegram(
            self.db,
            self.backend.id,
            secret_ref=ALIAS,
            chat_id="-700000000001",
            bot_username="synthetic_bot",
        )
        self.credentials = json.dumps({ALIAS: SECRET})

    def tearDown(self) -> None:
        self.db.close()

    def _sync(self, *, apply: bool, backend_chat: str = "-700000000101"):
        values = {
                "backend": settings("backend", backend_chat),
                "backend_copy": settings("backend_copy", "-700000000202"),
        }
        return course_destinations.sync_from_trusted_settings(
            self.sessions,
            expected_versions={"backend": self.backend.config_version, "backend_copy": self.backend_copy.config_version},
            apply=apply,
            settings_reader=lambda: values,
            verifier=lambda token, chat_id: "synthetic_bot",
        )

    def _consult(self, course: str, event_id: str):
        # Production opens a fresh request session after the separate config write.
        self.db.expire_all()
        with patch.dict(os.environ, {"PYMENTOR_LANDING_BOT_CREDENTIALS": self.credentials}):
            return landings.submit_course_consultation(
                self.db,
                course,
                event_id,
                {
                    "name": "Synthetic Learner",
                    "contact": "@synthetic",
                    "goal": "course request",
                    "consented_at": "2026-11-14T09:00:00+00:00",
                    "consent_version": "consultation-v3",
                },
            )

    def test_dry_run_apply_is_idempotent_and_uses_one_secret_alias(self) -> None:
        with patch.dict(os.environ, {"PYMENTOR_LANDING_BOT_CREDENTIALS": self.credentials}):
            preview = self._sync(apply=False)
        self.assertEqual(preview["backend"]["chat_id"], "-700000000101")
        self.assertEqual(preview["backend_copy"]["chat_id"], "-700000000202")
        self.assertEqual(self.backend.config_version, 1)
        self.assertEqual(self.backend_copy.config_version, 0)

        with patch.dict(os.environ, {"PYMENTOR_LANDING_BOT_CREDENTIALS": self.credentials}):
            applied = self._sync(apply=True)
            self.db.query(models.Landing).filter_by(id=self.backend_copy.id).update(
                {"telegram_enabled": False}, synchronize_session=False
            )
            self.db.commit()
            self.db.expire_all()
            self.assertFalse(self.db.get(models.Landing, self.backend_copy.id).telegram_enabled)
            reactivated = self._sync(apply=True)
            again = self._sync(apply=True)

        self.assertTrue(applied["backend"]["changed"])
        self.assertTrue(applied["backend_copy"]["changed"])
        self.assertTrue(reactivated["backend_copy"]["changed"])
        self.assertEqual(self.backend_copy.config_version, applied["backend_copy"]["version"])
        self.db.expire_all()
        self.assertTrue(self.db.get(models.Landing, self.backend_copy.id).telegram_enabled)
        self.assertFalse(again["backend"]["changed"])
        self.assertFalse(again["backend_copy"]["changed"])
        configs = self.db.query(models.LandingTelegramConfig).all()
        self.assertEqual({item.secret_ref for item in configs}, {ALIAS})
        current_ids = {
            landings.current_landing_telegram_config(self.db, self.backend).id,
            landings.current_landing_telegram_config(self.db, self.backend_copy).id,
        }
        self.assertEqual(
            {item.chat_id for item in configs if item.id in current_ids},
            {"-700000000101", "-700000000202"},
        )

    def test_course_envelope_rejects_client_course_and_recipient_fields(self) -> None:
        with self.assertRaises(ValidationError):
            LandingConsultationRequest.model_validate(
                {
                    "name": "Synthetic Learner",
                    "contact": "@synthetic",
                    "goal": "course request",
                    "consent": True,
                    "course_id": "backend_copy",
                }
            )
        with self.assertRaises(ValidationError):
            LandingConsultationRequest.model_validate(
                {
                    "name": "Synthetic Learner",
                    "contact": "@synthetic",
                    "goal": "course request",
                    "consent": True,
                    "chat_id": "-700000000202",
                }
            )

    def test_enqueue_claim_transport_ack_preserves_course_and_pinned_destination(self) -> None:
        with patch.dict(os.environ, {"PYMENTOR_LANDING_BOT_CREDENTIALS": self.credentials}):
            self._sync(apply=True)
            first, duplicate = self._consult("backend", "synthetic-backend-event-1")
            repeated, was_duplicate = self._consult("backend", "synthetic-backend-event-1")
            second, _ = self._consult("backend_copy", "synthetic-backend-copy-event-1")
            self.assertFalse(duplicate)
            self.assertTrue(was_duplicate)
            self.assertEqual(first.id, repeated.id)
            self.assertNotIn("course_id", json.loads(first.payload_json))
            self.assertNotIn("course_id", json.loads(second.payload_json))

            # A configuration update after enqueue affects future events only.
            current_backend = self.db.get(models.Landing, self.backend.id)
            current_copy = self.db.get(models.Landing, self.backend_copy.id)
            course_destinations.sync_from_trusted_settings(
                self.sessions,
                settings_reader=lambda: {
                    "backend": settings("backend", "-700000000303"),
                    "backend_copy": settings("backend_copy", "-700000000404"),
                },
                expected_versions={"backend": current_backend.config_version, "backend_copy": current_copy.config_version},
                apply=True,
                verifier=lambda token, chat_id: "synthetic_bot",
            )

        sink: list[tuple[str, str, str, str]] = []

        def fake_transport(event):
            sink.append((event.course_id, event.landing_slug, event.chat_id, event.secret_ref))

        counts = drain_notifications(
            session_factory=self.sessions,
            limit=4,
            budget_seconds=3,
            landing_event_keys=frozenset(
                {(self.backend.id, first.event_id), (self.backend_copy.id, second.event_id)}
            ),
            send=fake_transport,
        )
        self.assertEqual(counts["sent"], 2)
        self.assertEqual(
            {(course, slug, chat, alias) for course, slug, chat, alias in sink},
            {
                ("backend", "backend", "-700000000101", ALIAS),
                ("backend_copy", "backend_copy", "-700000000202", ALIAS),
            },
        )
        self.db.expire_all()
        self.assertEqual(self.db.get(models.LandingNotificationOutbox, first.id).status, "sent")
        self.assertEqual(self.db.get(models.LandingNotificationOutbox, second.id).status, "sent")

    def test_timeout_after_sink_acceptance_can_redeliver_but_keeps_original_recipient(self) -> None:
        with patch.dict(os.environ, {"PYMENTOR_LANDING_BOT_CREDENTIALS": self.credentials}):
            self._sync(apply=True)
            event, _ = self._consult("backend", "synthetic-timeout-event")

        accepted: list[tuple[str, str]] = []

        def first_attempt(claimed):
            accepted.append((claimed.course_id, claimed.chat_id))
            raise TimeoutError("synthetic timeout after sink acceptance")

        counts = drain_notifications(
            session_factory=self.sessions,
            limit=1,
            budget_seconds=3,
            landing_event_keys=frozenset({(self.backend.id, event.event_id)}),
            send=first_attempt,
        )
        self.assertEqual(counts["retry"], 1)
        self.db.expire_all()
        saved = self.db.get(models.LandingNotificationOutbox, event.id)
        self.assertEqual(saved.status, "pending")
        self.assertEqual(saved.destination_config_id, event.destination_config_id)
        saved.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.db.commit()

        recovery = drain_notifications(
            session_factory=self.sessions,
            limit=1,
            budget_seconds=3,
            landing_event_keys=frozenset({(self.backend.id, event.event_id)}),
            send=lambda claimed: accepted.append((claimed.course_id, claimed.chat_id)),
        )
        self.assertEqual(recovery["sent"], 1)
        self.assertEqual(accepted, [("backend", "-700000000101")] * 2)
        self.db.expire_all()
        final = self.db.get(models.LandingNotificationOutbox, event.id)
        self.assertEqual(final.status, "sent")
        self.assertEqual(final.attempts, 2)

    def test_disabled_invalid_and_stale_settings_fail_closed(self) -> None:
        for invalid in (
            settings("backend", "-700000000101", enabled=False),
            settings("backend_copy", "not-a-chat-id"),
            settings("other-course", "-700000000202"),
        ):
            values = {"backend": settings("backend", "-700000000101"), "backend_copy": settings("backend_copy", "-700000000202")}
            values[invalid["course_id"] if invalid["course_id"] in values else "backend_copy"] = invalid
            with self.assertRaises(LandingError):
                course_destinations.sync_from_trusted_settings(
                    self.sessions,
                    settings_reader=lambda: values,
                    expected_versions={"backend": self.backend.config_version, "backend_copy": self.backend_copy.config_version},
                    apply=True,
                    verifier=lambda token, chat_id: "synthetic_bot",
                )
            self.db.rollback()
        with patch.dict(os.environ, {"PYMENTOR_LANDING_BOT_CREDENTIALS": self.credentials}):
            with self.assertRaises(LandingError) as stale:
                course_destinations.sync_from_trusted_settings(
                    self.sessions,
                    settings_reader=lambda: {"backend": settings("backend", "-700000000101"), "backend_copy": settings("backend_copy", "-700000000202")},
                    expected_versions={"backend": 99, "backend_copy": 0},
                    apply=True,
                    verifier=lambda token, chat_id: "synthetic_bot",
                )
        self.assertEqual(stale.exception.status_code, 409)
        self.assertEqual(self.backend.config_version, 1)
        self.assertEqual(self.backend_copy.config_version, 0)

    def test_trusted_settings_reader_uses_only_allowlisted_canonical_paths(self) -> None:
        loaded = course_destinations.read_trusted_course_settings()
        self.assertEqual(set(loaded), {"backend", "backend_copy"})
        for course_id in ("backend", "backend_copy"):
            self.assertEqual(loaded[course_id]["course_id"], course_id)
            self.assertFalse(loaded[course_id]["telegram"]["enabled"])
            self.assertIsNone(loaded[course_id]["telegram"]["chat_id"])
        with self.assertRaises(LandingError) as disabled:
            course_destinations.sync_from_trusted_settings(
                self.sessions,
                expected_versions={"backend": 1, "backend_copy": 0},
            )
        self.assertEqual(disabled.exception.status_code, 409)
        self.assertEqual(self.backend.config_version, 1)
        self.assertEqual(self.backend_copy.config_version, 0)

