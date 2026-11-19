"""Ed25519 signer loaded only for explicitly enabled Analytics integrations."""
from __future__ import annotations

import re
import time

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class Signer:
    def __init__(self, *, issuer: str, kid: str, private_pem: bytes, purpose: str = "grant"):
        if not _IDENTIFIER.fullmatch(issuer) or not _IDENTIFIER.fullmatch(kid) or purpose not in {"grant", "producer"}:
            raise ValueError("invalid signer configuration")
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        try:
            key = load_pem_private_key(private_pem, password=None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError
        except Exception:
            raise ValueError("invalid signer configuration") from None
        self._key, self.issuer, self.kid, self._purpose = key, issuer, kid, purpose

    def _encode(self, claims):
        import jwt
        return jwt.encode(claims, self._key, algorithm="EdDSA", headers={"kid": self.kid})

    def grant(self, *, subject: str, role: str, scope: str, courses: list[str],
              subjects: list[str], global_read: bool, now: int | None = None,
              legacy_admin_roster_v1: dict | None = None, course_completion_roster_v1: dict | None = None) -> str:
        from uuid import UUID
        if self._purpose != "grant":
            raise ValueError("incorrect signing purpose")
        if str(UUID(subject)) != subject or any(str(UUID(s)) != s for s in subjects):
            raise ValueError("invalid lifecycle identity")
        if len(courses) > 100 or len(subjects) > 1000 or any(not _IDENTIFIER.fullmatch(c) for c in courses):
            raise ValueError("invalid grant scope")
        valid = ((role == "user" and scope == "collect" and bool(courses) and not subjects and not global_read)
                 or (role == "user" and scope == "report:read" and bool(courses) and not subjects and not global_read)
                 or (role == "leader" and scope == "report:read" and len(courses) == 1 and not global_read)
                 or (role == "admin" and scope == "report:read" and not courses and not subjects and global_read)
                 or (role in {"admin", "leader"} and scope == "report:student"
                     and len(courses) == len(subjects) == 1 and not global_read))
        if not valid:
            raise ValueError("invalid grant scope")
        issued = int(time.time()) if now is None else now
        ttl = 300 if scope == "collect" else 60
        claims = {"grant_version": 1, "iss": self.issuer, "aud": "analytics", "sub": subject,
            "iat": issued, "nbf": issued, "exp": issued + ttl, "role": role, "scope": scope,
            "course_ids": courses, "subject_refs": subjects, "global_read": global_read}
        if legacy_admin_roster_v1 is not None:
            roster = legacy_admin_roster_v1
            if (role != "admin" or scope != "report:read" or not global_read or not isinstance(roster, dict)
                    or set(roster) != {"version", "members"} or type(roster["version"]) is not int or roster["version"] != 1
                    or not isinstance(roster["members"], list) or len(roster["members"]) > 80):
                raise ValueError("invalid admin roster")
            seen = set()
            for member in roster["members"]:
                if (not isinstance(member, dict) or set(member) != {"subject_ref", "revision", "student"}
                        or str(UUID(member["subject_ref"])) != member["subject_ref"]
                        or type(member["revision"]) is not int or not 0 <= member["revision"] <= (1 << 63) - 1
                        or type(member["student"]) is not bool or member["subject_ref"] in seen):
                    raise ValueError("invalid admin roster")
                seen.add(member["subject_ref"])
            claims["legacy_admin_roster_v1"] = roster
        if course_completion_roster_v1 is not None:
            roster = course_completion_roster_v1
            if (role not in {"admin", "leader"} or scope != "report:read" or not isinstance(roster, dict)
                    or set(roster) != {"version", "course_id", "members", "year", "month"} or roster["version"] != 1
                    or type(roster["version"]) is not int or not isinstance(roster["course_id"], str)
                    or not _IDENTIFIER.fullmatch(roster["course_id"])
                    or not ((roster["year"] is None and roster["month"] is None) or
                            (type(roster["year"]) is int and 2000 <= roster["year"] <= 2100 and
                             type(roster["month"]) is int and 1 <= roster["month"] <= 12))
                    or (role == "leader" and courses != [roster["course_id"]])
                    or not isinstance(roster["members"], list) or len(roster["members"]) > 80):
                raise ValueError("invalid course roster")
            seen = set()
            for member in roster["members"]:
                if (not isinstance(member, dict) or set(member) != {"subject_ref", "revision", "student"}
                        or str(UUID(member["subject_ref"])) != member["subject_ref"]
                        or type(member["revision"]) is not int or not 0 <= member["revision"] <= (1 << 63) - 1
                        or type(member["student"]) is not bool or member["subject_ref"] in seen):
                    raise ValueError("invalid course roster")
                seen.add(member["subject_ref"])
            if role == "leader" and set(subjects) != seen:
                raise ValueError("invalid course roster scope")
            claims["course_completion_roster_v1"] = roster
        token = self._encode(claims)
        if len(token.encode("ascii")) > 16 * 1024:
            raise ValueError("grant too large")
        return token

    def producer(self, producer_id: str, *, now: int | None = None) -> str:
        if self._purpose != "producer" or not _IDENTIFIER.fullmatch(producer_id):
            raise ValueError("invalid producer")
        issued = int(time.time()) if now is None else now
        return self._encode({"grant_version": 1, "iss": self.issuer, "aud": "analytics-control",
            "sub": producer_id, "iat": issued, "nbf": issued, "exp": issued + 60, "scope": "control:write"})

    def course_visitors(self, *, subject: str, course_id: str, body_sha256: str) -> str:
        return self._course_cohorts(subject, course_id, body_sha256, audience="analytics-course-visitors")

    def course_study_time(self, *, subject: str, course_id: str, body_sha256: str) -> str:
        return self._course_cohorts(subject, course_id, body_sha256, audience="analytics-course-study-time")

    def _course_cohorts(self, subject: str, course_id: str, body_sha256: str, *, audience: str) -> str:
        from uuid import UUID

        if (self._purpose != "grant" or str(UUID(subject)) != subject
                or not _IDENTIFIER.fullmatch(course_id) or not re.fullmatch(r"[a-f0-9]{64}", body_sha256)):
            raise ValueError("invalid course report credential")
        issued = int(time.time())
        return self._encode({
            "grant_version": 1, "iss": self.issuer, "aud": audience,
            "sub": subject, "iat": issued, "nbf": issued, "exp": issued + 15,
            "course_id": course_id, "body_sha256": body_sha256,
        })
