"""Two independent consumers share a deterministic synthetic delta algorithm."""
from pymentor_analytics.contracts.processing_v1 import ClaimedBatch, SyntheticDelta, synthetic_delta


def calculate(batch: ClaimedBatch) -> SyntheticDelta:
    """Only the immutable claimed facts are inputs; no wall clock or side effects."""
    return synthetic_delta(batch.facts)
