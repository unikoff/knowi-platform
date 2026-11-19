"""Versioned synthetic subscriptions, shared by intake and future consumers."""
from dataclasses import dataclass


@dataclass(frozen=True)
class WorkSubscription:
    consumer: str
    version: int


# Stable identities are independent of replicas. Neither is a progress/KPI handler.
SYNTHETIC_SUBSCRIPTIONS = (
    WorkSubscription("synthetic.activity", 1),
    WorkSubscription("synthetic.independent", 1),
)
