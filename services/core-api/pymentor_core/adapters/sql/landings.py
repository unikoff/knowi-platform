"""Landing registry, versioned Telegram destinations, and notification events."""

import hashlib
import json
import os
import re
from datetime import datetime
from datetime import timedelta
from datetime import timezone

from pymentor_core.modules.landings.contracts import LandingError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pymentor_core.adapters.sql import models
from pymentor_core.adapters.course_local.landing_catalog import course_landing_slugs

from pymentor_core.modules.landings.contracts import (
    VerifiedCourseDestination,
    LANDING_EVENT_CONTRACTS, MAX_TEST_EVENTS_PER_HOUR,
    MAX_CONSULTATION_REQUESTS_PER_HOUR, MAX_MANUAL_RETRIES,
)
_SECRET_REF_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,63}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _start_write_transaction(db: Session) -> None:
    if db.get_bind().dialect.name != "postgresql":
        raise RuntimeError("Landing writes require PostgreSQL")


def _canonical_payload(
    event_type: str, schema_version: int, payload: dict
) -> tuple[str, str]:
    canonical_event = json.dumps(
        {
            "event_type": event_type,
            "schema_version": schema_version,
            "payload": payload,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    serialized_payload = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return serialized_payload, hashlib.sha256(
        canonical_event.encode("utf-8")
    ).hexdigest()


def resolve_landing_bot_token(secret_ref: str) -> str:
    """Resolve an opaque alias from the externally managed runtime secret map."""
    if not _SECRET_REF_PATTERN.fullmatch(secret_ref):
        raise LandingError(
            status_code=422, detail="Недопустимая ссылка на секрет Telegram"
        )
    try:
        values = json.loads(os.getenv("PYMENTOR_LANDING_BOT_CREDENTIALS", "{}"))
    except (json.JSONDecodeError, TypeError):
        raise LandingError(
            status_code=503, detail="Хранилище секретов Telegram недоступно"
        ) from None
    if not isinstance(values, dict):
        raise LandingError(
            status_code=503, detail="Хранилище секретов Telegram недоступно"
        )
    token = values.get(secret_ref)
    if not isinstance(token, str) or not token.strip() or len(token) > 512:
        raise LandingError(status_code=503, detail="Секрет Telegram не настроен")
    return token.strip()


def create_landing(
    db: Session,
    *,
    slug: str,
    title: str,
    description: str,
    is_active: bool = True,
) -> models.Landing:
    if db.query(models.Landing).filter_by(slug=slug).first() is not None:
        raise LandingError(status_code=409, detail="Этот slug уже занят")

    landing = models.Landing(
        slug=slug,
        title=title,
        description=description,
        is_active=is_active,
    )
    db.add(landing)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise LandingError(status_code=409, detail="Этот slug уже занят") from None
    db.refresh(landing)
    return landing


def set_landing_enabled(
    db: Session, landing_id: str, *, enabled: bool
) -> models.Landing:
    _start_write_transaction(db)
    landing = (
        db.query(models.Landing).filter_by(id=landing_id).with_for_update().first()
    )
    if landing is None:
        raise LandingError(status_code=404, detail="Лендинг не найден")
    landing.is_active = enabled
    db.commit()
    db.refresh(landing)
    return landing


def configure_landing_telegram(
    db: Session,
    landing_id: str,
    *,
    secret_ref: str,
    chat_id: str,
    bot_username: str,
) -> models.LandingTelegramConfig:
    _start_write_transaction(db)
    landing = (
        db.query(models.Landing).filter_by(id=landing_id).with_for_update().first()
    )
    if landing is None:
        raise LandingError(status_code=404, detail="Лендинг не найден")
    if not _SECRET_REF_PATTERN.fullmatch(secret_ref):
        raise LandingError(
            status_code=422, detail="Недопустимая ссылка на секрет Telegram"
        )

    version = landing.config_version + 1
    config = models.LandingTelegramConfig(
        landing_id=landing.id,
        version=version,
        secret_ref=secret_ref,
        chat_id=chat_id,
        bot_username=bot_username[:64],
        status="verified",
        verified_at=_utcnow(),
    )
    landing.config_version = version
    landing.telegram_enabled = True
    db.add(config)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise LandingError(
            status_code=409, detail="Конфигурация Telegram была изменена параллельно"
        ) from None
    db.refresh(config)
    return config


def sync_course_destinations(
    db: Session,
    *,
    settings: dict[str, dict],
    expected_versions: dict[str, int],
    apply: bool = False,
    verified_destinations: dict[str, VerifiedCourseDestination] | None = None,
) -> dict[str, dict]:
    """Preview or atomically apply trusted course destinations to existing landings."""
    from sqlalchemy import select

    landing_slugs = course_landing_slugs()
    if set(settings) != set(landing_slugs):
        raise LandingError(status_code=422, detail="Требуются настройки всех разрешённых курсов")
    if set(expected_versions) != set(landing_slugs):
        raise LandingError(status_code=422, detail="Не заданы ожидаемые версии конфигурации")
    if apply and (verified_destinations is None or set(verified_destinations) != set(landing_slugs)):
        raise LandingError(status_code=409, detail="Каждое назначение требует отдельной проверки Telegram")
    parsed: dict[str, str] = {}
    for course_id, value in settings.items():
        if not isinstance(value, dict) or set(value) != {"schema_version", "course_id", "contacts", "telegram"}:
            raise LandingError(status_code=422, detail=f"Некорректные настройки курса {course_id}")
        telegram = value.get("telegram")
        if (
            value.get("schema_version") != 1
            or value.get("course_id") != course_id
            or not isinstance(telegram, dict)
            or set(telegram) != {"enabled", "chat_id", "topic_id"}
            or not isinstance(telegram.get("enabled"), bool)
            or telegram.get("topic_id") is not None
        ):
            raise LandingError(status_code=422, detail=f"Некорректные настройки курса {course_id}")
        chat_id = telegram.get("chat_id")
        if not telegram["enabled"]:
            raise LandingError(status_code=409, detail=f"Получатель курса {course_id} отключён")
        if type(chat_id) is not str or not re.fullmatch(r"-?[1-9]\d{0,18}", chat_id) or not -(2**63) <= int(chat_id) < 2**63:
            raise LandingError(status_code=422, detail=f"Недопустимый получатель курса {course_id}")
        parsed[course_id] = chat_id

    if apply:
        _start_write_transaction(db)
    slugs = set(landing_slugs.values())
    query = select(models.Landing).where(models.Landing.slug.in_(slugs)).order_by(models.Landing.slug)
    if apply:
        query = query.with_for_update()
    landings = {row.slug: row for row in db.execute(query).scalars().all()}
    if set(landings) != slugs or any(not row.is_active for row in landings.values()):
        raise LandingError(status_code=409, detail="Активные курсовые лендинги не найдены")
    current: dict[str, models.LandingTelegramConfig | None] = {
        course: current_landing_telegram_config(db, landings[slug])
        for course, slug in landing_slugs.items()
    }
    shared = next((config for config in current.values() if config is not None), None)
    if shared is None or not _SECRET_REF_PATTERN.fullmatch(shared.secret_ref):
        raise LandingError(status_code=409, detail="Общий Telegram secret alias не настроен")
    if any(config is not None and config.secret_ref != shared.secret_ref for config in current.values()):
        raise LandingError(status_code=409, detail="Курсовые лендинги используют разные secret alias")
    result: dict[str, dict] = {}
    for course_id, slug in landing_slugs.items():
        landing = landings[slug]
        expected = expected_versions[course_id]
        if type(expected) is not int or expected < 0:
            raise LandingError(status_code=422, detail="Недопустимая ожидаемая версия")
        if landing.config_version != expected:
            raise LandingError(status_code=409, detail=f"Конфигурация курса {course_id} изменилась параллельно")
        chat_id = parsed[course_id]
        old = current[course_id]
        same_destination = (
            old is not None
            and old.secret_ref == shared.secret_ref
            and old.chat_id == chat_id
            and old.status == "verified"
        )
        needs_enable = not landing.telegram_enabled
        result[course_id] = {
            "landing_id": landing.id,
            "slug": slug,
            "expected_version": expected,
            "version": landing.config_version if same_destination else landing.config_version + 1,
            "destination_config_id": old.id if same_destination else None,
            "chat_id": chat_id,
            "secret_ref": shared.secret_ref,
            "changed": not same_destination or needs_enable,
        }
    if apply:
        for course_id in landing_slugs:
            proof = verified_destinations[course_id]
            if (
                not isinstance(proof, VerifiedCourseDestination)
                or proof.course_id != course_id
                or proof.secret_ref != shared.secret_ref
                or proof.chat_id != parsed[course_id]
                or not isinstance(proof.bot_username, str)
                or not proof.bot_username.strip()
                or len(proof.bot_username) > 64
                or any(ord(char) < 32 for char in proof.bot_username)
            ):
                raise LandingError(status_code=409, detail=f"Telegram-проверка курса {course_id} не подтверждена")

    for course_id, slug in landing_slugs.items():
        landing = landings[slug]
        old = current[course_id]
        same_destination = (
            old is not None
            and old.secret_ref == shared.secret_ref
            and old.chat_id == parsed[course_id]
            and old.status == "verified"
        )
        needs_enable = not landing.telegram_enabled
        if apply and not same_destination:
            chat_id = parsed[course_id]
            config = models.LandingTelegramConfig(
                landing_id=landing.id,
                version=landing.config_version + 1,
                secret_ref=shared.secret_ref,
                chat_id=chat_id,
                bot_username=verified_destinations[course_id].bot_username.strip(),
                status="verified",
                verified_at=_utcnow(),
            )
            landing.config_version += 1
            landing.telegram_enabled = True
            db.add(config)
            db.flush()
            result[course_id]["destination_config_id"] = config.id
        elif apply and needs_enable:
            landing.telegram_enabled = True
    if apply:
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise LandingError(status_code=409, detail="Конфигурация курса изменилась параллельно") from None
    return result


def submit_course_consultation(db: Session, slug: str, event_id: str, payload: dict):
    course_id = next((course for course, landing_slug in course_landing_slugs().items() if landing_slug == slug), None)
    if course_id is None:
        raise LandingError(status_code=404, detail="Курсовой лендинг не найден")
    landing = db.query(models.Landing).filter_by(slug=slug, is_active=True).first()
    if landing is None:
        raise LandingError(status_code=404, detail="Активный лендинг не найден")
    config = current_landing_telegram_config(db, landing)
    if landing.telegram_enabled and config is not None and config.status == "verified":
        resolve_landing_bot_token(config.secret_ref)
    return enqueue_landing_event(
        db,
        landing_id=landing.id,
        event_id=event_id,
        event_type="consultation_request.v1",
        payload=payload,
    )


def disable_landing_telegram(db: Session, landing_id: str) -> models.Landing:
    _start_write_transaction(db)
    landing = (
        db.query(models.Landing).filter_by(id=landing_id).with_for_update().first()
    )
    if landing is None:
        raise LandingError(status_code=404, detail="Лендинг не найден")
    landing.telegram_enabled = False
    db.commit()
    db.refresh(landing)
    return landing


def current_landing_telegram_config(
    db: Session, landing: models.Landing
) -> models.LandingTelegramConfig | None:
    if landing.config_version < 1:
        return None
    return (
        db.query(models.LandingTelegramConfig)
        .filter_by(landing_id=landing.id, version=landing.config_version)
        .first()
    )


def _same_consultation_payload(event, event_type, payload, contract):
    if event_type != "consultation_request.v1" or event.event_type != event_type:
        return False
    try:
        previous = json.loads(event.payload_json)
    except (TypeError, json.JSONDecodeError):
        return False
    stable_fields = contract["payload_keys"] - {"consented_at"}
    return isinstance(previous, dict) and all(previous.get(key) == payload.get(key) for key in stable_fields)


def enqueue_landing_event(
    db: Session,
    *,
    landing_id: str,
    event_id: str,
    event_type: str,
    payload: dict,
) -> tuple[models.LandingNotificationOutbox, bool]:
    contract = LANDING_EVENT_CONTRACTS.get(event_type)
    if contract is None:
        raise LandingError(status_code=422, detail="Тип события лендинга не разрешён")

    _start_write_transaction(db)
    schema_version = contract["schema_version"]
    serialized, digest = _canonical_payload(event_type, schema_version, payload)
    existing = (
        db.query(models.LandingNotificationOutbox)
        .filter_by(landing_id=landing_id, event_id=event_id)
        .first()
    )
    if existing is not None:
        if existing.payload_sha256 != digest:
            if _same_consultation_payload(existing, event_type, payload, contract):
                return existing, True
            raise LandingError(
                status_code=409, detail="Ключ события уже использован с другими данными"
            )
        return existing, True

    if set(payload) != contract["payload_keys"]:
        raise LandingError(status_code=422, detail="Событие содержит недопустимые поля")
    landing = (
        db.query(models.Landing).filter_by(id=landing_id).with_for_update().first()
    )
    if landing is None or not landing.is_active:
        raise LandingError(status_code=404, detail="Активный лендинг не найден")
    if not landing.telegram_enabled:
        raise LandingError(
            status_code=409, detail="У лендинга отключены Telegram-уведомления"
        )
    config = current_landing_telegram_config(db, landing)
    if config is None or config.status != "verified":
        raise LandingError(
            status_code=409, detail="Для лендинга не подключён Telegram-бот"
        )

    recent_count = (
        db.query(models.LandingNotificationOutbox)
        .filter(
            models.LandingNotificationOutbox.landing_id == landing_id,
            models.LandingNotificationOutbox.event_type == event_type,
            models.LandingNotificationOutbox.created_at
            >= _utcnow() - timedelta(hours=1),
        )
        .count()
    )
    rate_limit = (
        MAX_TEST_EVENTS_PER_HOUR
        if event_type == "telegram_connection_test.v1"
        else MAX_CONSULTATION_REQUESTS_PER_HOUR
    )
    if recent_count >= rate_limit:
        detail = (
            "Лимит тестовых уведомлений на час исчерпан"
            if event_type == "telegram_connection_test.v1"
            else "Лимит заявок на консультацию на час исчерпан"
        )
        raise LandingError(status_code=429, detail=detail)

    event = models.LandingNotificationOutbox(
        landing_id=landing.id,
        destination_config_id=config.id,
        event_id=event_id,
        event_type=event_type,
        schema_version=schema_version,
        payload_json=serialized,
        payload_sha256=digest,
        status="pending",
        next_attempt_at=_utcnow(),
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = (
            db.query(models.LandingNotificationOutbox)
            .filter_by(landing_id=landing_id, event_id=event_id)
            .first()
        )
        if concurrent is not None and (
            concurrent.payload_sha256 == digest
            or _same_consultation_payload(concurrent, event_type, payload, contract)
        ):
            return concurrent, True
        if concurrent is not None:
            raise LandingError(
                status_code=409, detail="Ключ события уже использован с другими данными"
            ) from None
        raise
    db.refresh(event)
    return event, False


def landing_event_status(
    db: Session, landing_id: str, event_id: str
) -> models.LandingNotificationOutbox:
    event = (
        db.query(models.LandingNotificationOutbox)
        .filter_by(landing_id=landing_id, event_id=event_id)
        .first()
    )
    if event is None:
        raise LandingError(status_code=404, detail="Событие не найдено")
    return event


def retry_landing_event(
    db: Session, landing_id: str, event_id: str
) -> models.LandingNotificationOutbox:
    _start_write_transaction(db)
    event = (
        db.query(models.LandingNotificationOutbox)
        .filter_by(landing_id=landing_id, event_id=event_id)
        .with_for_update()
        .first()
    )
    if event is None:
        raise LandingError(status_code=404, detail="Событие не найдено")
    if event.status != "failed":
        raise LandingError(
            status_code=409,
            detail="Повторить можно только окончательно не доставленное событие",
        )
    if event.manual_retry_count >= MAX_MANUAL_RETRIES:
        raise LandingError(status_code=409, detail="Лимит ручных повторов исчерпан")
    event.status = "pending"
    event.attempts = 0
    event.manual_retry_count += 1
    event.next_attempt_at = _utcnow()
    event.locked_until = None
    event.lock_token = None
    event.last_error = None
    db.commit()
    db.refresh(event)
    return event
