"""Privacy policy and restore proof contract; no database deletion implementation."""
from dataclasses import dataclass

POLICY_VERSION = "privacy-v1"
COLLECTION_GRANT_TTL_SECONDS = 300
REPORT_GRANT_TTL_SECONDS = 60
BROWSER_BUFFER_TTL_SECONDS = 86_400
BROWSER_BUFFER_MAX_ITEMS = 200
BROWSER_BUFFER_MAX_BYTES = 512 * 1024
OPERATIONAL_LOG_TTL_DAYS = 14
COMPLETED_JOB_TTL_DAYS = 7
VERIFIED_BACKUP_COPIES = 4
DELETION_ACK_DEADLINE_SECONDS = 60
PRIVACY_MAX_LAG_SECONDS = 5
CONTROL_HEARTBEAT_MAX_AGE_SECONDS = 15

# None explicitly means no automatic age-based deletion, never an unknown value.
ACTIVITY_FACT_TTL_DAYS = None
PERSONAL_HISTORY_TTL_DAYS = None
CATALOG_HISTORY_TTL_DAYS = None
DELETION_JOURNAL_TTL_DAYS = None
TOMBSTONE_TTL_DAYS = None


class PrivacyUnavailable(Exception):
    """The current authenticated deletion barrier cannot authorize an operation."""
    def __init__(self):
        super().__init__("privacy barrier unavailable")


@dataclass(frozen=True)
class RestoreProof:
    """Authenticated authority observation outside the restored application snapshot.

    Source authentication and obtaining a current high watermark are adapter
    responsibilities. A value copied from the restored backup is not evidence.
    This pure predicate does not perform synchronization or authorize requests.
    """

    authority_id: str
    high_watermark: int
    scanned_through: int
    locally_applied_through: int
    complete: bool
    authenticated_current_observation: bool
    stream_attached: bool
    opening_barrier: int

    def permits_opening(self, *, expected_authority_id: str) -> bool:
        return (
            bool(expected_authority_id)
            and self.authority_id == expected_authority_id
            and self.authenticated_current_observation is True
            and self.complete is True
            and self.stream_attached is True
            and type(self.high_watermark) is int
            and type(self.scanned_through) is int
            and type(self.locally_applied_through) is int
            and type(self.opening_barrier) is int
            and 0 <= self.high_watermark <= self.opening_barrier == self.scanned_through
            and self.locally_applied_through >= self.opening_barrier
        )
