"""Local writeback — writes dhqa results to JSON files instead of DataHub.

This allows the demo to show "writeback" without needing a live DataHub
instance or mutation permissions.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from dhqa.lineage_tracer import RootCauseReport
from dhqa.test_generator import CheckResult


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_name(urn: str) -> str:
    return urn.replace(":", "_").replace("(", "_").replace(")", "_").replace(",", "_")


def write_local_result(
    out_dir: str | Path,
    dataset_urn: str,
    results: list[CheckResult],
    reports: list[RootCauseReport],
) -> Path:
    """Top-level writeback for the local fixture path — writes a single
    results JSON file and returns its path."""
    ts = _timestamp()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"results_{_safe_name(dataset_urn)}_{ts}.json"

    payload = {
        "dataset_urn": dataset_urn,
        "timestamp": ts,
        "checks": [
            {
                "check_id": r.check_id,
                "kind": r.kind,
                "column": r.column,
                "passed": r.passed,
                "detail": r.detail,
            }
            for r in results
        ],
        "incidents": [
            {
                "origin_urn": rep.origin_urn,
                "hop_distance": rep.hop_distance,
                "trace": rep.trace,
                "summary": rep.summary(),
            }
            for rep in reports
        ],
    }
    path.write_text(json.dumps(payload, indent=2, default=str))

    # Also write per-incident detail files
    for rep in reports:
        incident_path = out_dir / f"incident_{_safe_name(dataset_urn)}_{_timestamp()}.json"
        incident_payload = {
            "dataset_urn": dataset_urn,
            "failing_check_id": rep.failing_check.check_id,
            "origin_urn": rep.origin_urn,
            "hop_distance": rep.hop_distance,
            "trace": rep.trace,
            "summary": rep.summary(),
            "written_at": _timestamp(),
        }
        incident_path.write_text(json.dumps(incident_payload, indent=2, default=str))

    return path