"""Streamlit dashboard: contract health across the DataHub catalog.

Two views:
  1. Catalog health — pass/fail state per dataset, most recent check.
  2. Incident detail — the lineage trace for a selected failing check.

Run with: streamlit run dashboard/app.py

Supports two data sources:
  - Local fixture (default): reads from ``data/fixtures/results/``
  - Live DataHub: reads via ``dhqa.mcp_client.DataHubMCPClient``
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"
RESULTS_DIR = FIXTURES_DIR / "results"


# ── Data fetching ─────────────────────────────────────────────────────

def _load_local_results() -> list[dict[str, Any]]:
    """Load all result JSONs from the local fixture results directory."""
    datasets: list[dict[str, Any]] = []
    if not RESULTS_DIR.exists():
        return datasets
    for result_file in sorted(RESULTS_DIR.glob("results_*.json"), reverse=True):
        try:
            data = json.loads(result_file.read_text())
            datasets.append(data)
        except Exception:
            continue
    return datasets


def _merge_latest(datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the most recent result per dataset URN."""
    seen: dict[str, dict[str, Any]] = {}
    for d in datasets:
        urn = d.get("dataset_urn", "unknown")
        if urn not in seen:
            seen[urn] = d
    return list(seen.values())


# ── Render helpers ────────────────────────────────────────────────────

def render_catalog_health(datasets: list[dict[str, Any]]) -> None:
    st.subheader("Catalog Health")
    if not datasets:
        st.info("No results yet. Run `dhqa check --local-fixture data/fixtures` first.")
        return

    rows = []
    for d in datasets:
        checks = d.get("checks", [])
        passed = sum(1 for c in checks if c.get("passed"))
        total = len(checks)
        rows.append({
            "Dataset": d.get("dataset_urn", "unknown"),
            "Status": "pass" if passed == total else "fail",
            "Checks": f"{passed}/{total}",
            "Incidents": len(d.get("incidents", [])),
            "Last Checked": d.get("timestamp", "unknown"),
        })

    df = pd.DataFrame(rows)
    for _, row in df.iterrows():
        icon = "🟢" if row["Status"] == "pass" else "🔴"
        st.write(
            f"{icon} `{row['Dataset']}` — "
            f"Checks: {row['Checks']} | Incidents: {row['Incidents']} | {row['Last Checked']}"
        )


def render_incident_detail(datasets: list[dict[str, Any]]) -> None:
    st.subheader("Incident Detail")
    failing = [d for d in datasets if any(not c.get("passed", True) for c in d.get("checks", []))]
    if not failing:
        st.write("No open incidents.")
        return

    choice = st.selectbox("Dataset", [d["dataset_urn"] for d in failing])
    dataset = next(d for d in failing if d["dataset_urn"] == choice)

    st.write("### Failing Checks")
    for check in dataset.get("checks", []):
        if not check.get("passed"):
            st.write(f"❌ `{check.get('check_id')}` ({check.get('kind')}): {check.get('detail')}")

    st.divider()
    st.write("### Root Cause Traces")
    for incident in dataset.get("incidents", []):
        st.write(f"**Origin**: `{incident.get('origin_urn')}` (hops: {incident.get('hop_distance', 0)})")
        for hop in incident.get("trace", []):
            status = "❌" if not hop.get("passed") else "✅"
            st.write(f"  {status} `{hop.get('urn')}` — {hop.get('detail')}")
        with st.expander("Full summary"):
            st.text(incident.get("summary", ""))


# ── Main ──────────────────────────────────────────────────────────────

def main() -> None:
    st.title("DataHub Contract QA Agent")
    st.caption(
        "Generated contracts, test results, and lineage-traced root causes — "
        "written back to DataHub."
    )

    datasets = _merge_latest(_load_local_results())
    render_catalog_health(datasets)
    st.divider()
    render_incident_detail(datasets)


if __name__ == "__main__":
    main()