"""Failures exposed by Analytics service ports."""


class LearnerReadUnavailable(Exception):
    """The learner analytics read is unavailable."""


class LegacyReadUnavailable(Exception):
    """The compatibility analytics read is unavailable."""


class AuthorityConflict(Exception):
    """An authority rejected a reused control ID with different content."""
