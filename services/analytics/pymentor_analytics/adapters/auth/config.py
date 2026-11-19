"""Bounded local public-key configuration, loaded at process startup."""
import json
import hashlib

from pymentor_analytics.adapters.auth.verifier import GrantVerifier, ProducerVerifier, build_verifiers

MAX_KEYRING_JSON_BYTES = 64 * 1024
MAX_ALLOWLIST_JSON_BYTES = 16 * 1024


def _object_without_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate configuration key")
        result[key] = value
    return result


def _read_json(secret, max_bytes):
    value = secret.get_secret_value()
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError("configuration too large")
    return json.loads(value, object_pairs_hook=_object_without_duplicates,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON")))


def _keys(secret):
    raw = _read_json(secret, MAX_KEYRING_JSON_BYTES)
    if not isinstance(raw, dict) or not 1 <= len(raw) <= 32:
        raise ValueError("invalid keyring")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        raise ValueError("invalid keyring")
    return {kid: pem.encode("ascii") for kid, pem in raw.items()}


def load_manifest(secret):
    """Validate both trust domains together and return a common fingerprint."""
    try:
        raw = _read_json(secret, MAX_KEYRING_JSON_BYTES * 2)
        if not isinstance(raw, dict) or set(raw) != {"grant", "producer"}:
            raise ValueError("invalid trust manifest")
        grant, producer = raw["grant"], raw["producer"]
        if not isinstance(grant, dict) or set(grant) != {"issuer", "keys"}:
            raise ValueError("invalid grant domain")
        if not isinstance(producer, dict) or set(producer) != {"issuer", "keys", "allowlist"}:
            raise ValueError("invalid producer domain")
        def keyring(value):
            if not isinstance(value, dict) or not 1 <= len(value) <= 32 or not all(isinstance(k, str) and isinstance(v, str) and len(v) <= 4096 for k, v in value.items()):
                raise ValueError("invalid keyring")
            return {k: v.encode("ascii") for k, v in value.items()}
        producers = producer["allowlist"]
        if not isinstance(producers, list) or not 1 <= len(producers) <= 100 or not all(isinstance(p, str) for p in producers) or len(set(producers)) != len(producers):
            raise ValueError("invalid producer allowlist")
        verifiers = build_verifiers(grant_issuer=grant["issuer"], grant_keys=keyring(grant["keys"]),
                                    producer_issuer=producer["issuer"], producer_keys=keyring(producer["keys"]),
                                    allowed_producers=set(producers))
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        return *verifiers, hashlib.sha256(canonical).hexdigest()
    except Exception:
        raise ValueError("invalid authentication configuration") from None


def load_verifiers(settings, *, role):
    """Return configured verifiers or None; configuration exceptions are sanitized."""
    try:
        grant_fields = (settings.grant_issuer, settings.grant_keys_json)
        producer_fields = (settings.producer_issuer, settings.producer_keys_json, settings.producer_allowlist_json)
        if settings.trust_manifest_json is not None:
            if any(value is not None for value in (*grant_fields, *producer_fields)):
                raise ValueError("independent configuration conflicts with common manifest")
            grant, producer, _ = load_manifest(settings.trust_manifest_json)
            if role not in {"collector", "query", "processor", "privacy"}:
                raise ValueError("invalid process role")
            return (grant if role in {"collector", "query"} else None,
                    producer if role == "collector" else None)
        grant_configured = any(value is not None for value in grant_fields)
        producer_configured = any(value is not None for value in producer_fields)
        if grant_configured and not all(value is not None for value in grant_fields):
            raise ValueError("incomplete grant configuration")
        if producer_configured and not all(value is not None for value in producer_fields):
            raise ValueError("incomplete producer configuration")
        if role not in {"collector", "query", "processor", "privacy"}:
            raise ValueError("invalid process role")
        if role in {"processor", "privacy"} and (grant_configured or producer_configured):
            raise ValueError("processor cannot receive HTTP verifier configuration")
        if role == "query" and producer_configured:
            raise ValueError("control credentials belong to collector")
        grant_keys = _keys(settings.grant_keys_json) if grant_configured else None
        producer_keys = _keys(settings.producer_keys_json) if producer_configured else None
        if producer_configured:
            producers = _read_json(settings.producer_allowlist_json, MAX_ALLOWLIST_JSON_BYTES)
            if not isinstance(producers, list) or not 1 <= len(producers) <= 100 or not all(isinstance(p, str) for p in producers):
                raise ValueError("invalid producer allowlist")
            if len(set(producers)) != len(producers):
                raise ValueError("duplicate producer")
            allowed = set(producers)
        if grant_configured and producer_configured:
            return build_verifiers(grant_issuer=settings.grant_issuer, grant_keys=grant_keys,
                                   producer_issuer=settings.producer_issuer, producer_keys=producer_keys,
                                   allowed_producers=allowed)
        grant = GrantVerifier(issuer=settings.grant_issuer, public_keys=grant_keys) if grant_configured else None
        producer = ProducerVerifier(issuer=settings.producer_issuer, public_keys=producer_keys,
                                    allowed_producers=allowed) if producer_configured else None
        return grant, producer
    except Exception:
        raise ValueError("invalid authentication configuration") from None


def validate_authority_key_separation(settings, public_keys):
    """The response signer is independent of both existing JWT trust domains."""
    from cryptography.hazmat.primitives import serialization
    try:
        if settings.trust_manifest_json is not None:
            grant, producer, _ = load_manifest(settings.trust_manifest_json)
        else:
            grant, producer = load_verifiers(settings, role="collector")
        def encoded(key):
            return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        existing = {encoded(key) for verifier in (grant, producer) if verifier is not None
                    for key in verifier._keys.values()}
        authority = {encoded(serialization.load_pem_public_key(pem.encode("ascii"))) for pem in public_keys.values()}
        if not authority or existing & authority:
            raise ValueError
    except Exception:
        raise ValueError("invalid authority configuration") from None
