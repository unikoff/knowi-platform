"""Load independent coverage evidence from an explicit operator manifest."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from pymentor_analytics.contracts.windows_v1 import WindowDiagnostics, WindowKey, WindowStats, window_bounds
from pymentor_analytics.modules.analytics.activity.student_windows_reconcile import CoverageEvidence

MAX_MANIFEST_KEYS = 1000
MAX_MANIFEST_BYTES = 2 * 1024 * 1024


class ManifestWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    key: WindowKey
    expected_stats: WindowStats | None = None
    v2_source_complete: bool = False
    old_web_only: bool = False
    diagnostics: WindowDiagnostics | None = None


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    protocol_version: Literal[1]
    cohort_t0: AwareDatetime | None = None
    windows: tuple[ManifestWindow, ...] = Field(max_length=MAX_MANIFEST_KEYS)


def _read(path: Path) -> Manifest:
    with path.open("rb") as file:
        data = file.read(MAX_MANIFEST_BYTES + 1)
    if len(data) > MAX_MANIFEST_BYTES:
        raise ValueError("manifest exceeds the allowed size")
    return Manifest.model_validate_json(data)


def _evidence(manifest: Manifest) -> dict[WindowKey, CoverageEvidence]:
    evidence = {}
    for item in manifest.windows:
        if item.key in evidence:
            raise ValueError("manifest repeats a window key")
        if item.diagnostics is not None and item.diagnostics.key != item.key:
            raise ValueError("diagnostic receipt key differs from manifest key")
        evidence[item.key] = CoverageEvidence(
            manifest_expected=item.expected_stats, managed_scenario=True, old_web_only=item.old_web_only,
            v2_source_complete=item.v2_source_complete,
            client_final_seq=item.diagnostics.client_final_seq if item.diagnostics else None,
            reasons=item.diagnostics.coverage_reasons if item.diagnostics else (),
        )
    return evidence


def load_evidence(path: Path | None) -> dict[WindowKey, CoverageEvidence]:
    return _evidence(_read(path)) if path is not None else {}


def load_trusted_evidence(
    path: Path, *, cohort: Collection[UUID] | None, cohort_t0: datetime,
) -> dict[WindowKey, CoverageEvidence]:
    """Use an independent finite manifest for the configured audience and exact T0."""
    manifest = _read(path)
    if manifest.cohort_t0 != cohort_t0 or not manifest.windows:
        raise ValueError("trusted manifest must bind at least one key to the configured T0")
    allowed = None if cohort is None else set(cohort)
    for item in manifest.windows:
        if ((allowed is not None and item.key.student_id not in allowed)
                or window_bounds(item.key)[1] <= cohort_t0):
            raise ValueError("trusted manifest key is outside the observation cohort or time")
        if item.expected_stats is None or not item.v2_source_complete or item.old_web_only or item.diagnostics:
            raise ValueError("trusted manifest requires independent expected stats and confirmed v2 coverage")
    return _evidence(manifest)
