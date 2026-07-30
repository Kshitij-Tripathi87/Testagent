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
import os
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "data" / "fixtures"
# Allow override via env var; fall back to the bundled fixture results dir.
RESULTS_DIR = Path(os.environ.get("DHQA_RESULTS_DIR", str(FIXTURES_DIR / "results")))


# ── Data fetching ─────────────────────────────────────────────────────

def _load_local_results() -> list[dict[str, Any]]:
    """Load all result JSONs from the local fixture results directory.

    Honors the ``DHQA_RESULTS_DIR`` environment variable (matching what the
    README documents); falls back to ``data/fixtures/results``.
    """
    datasets: list[dict[str, Any]] = []
    if not RESULTS_DIR.exists():
        return datasets
    for result_file in sorted(RESULTS_DIR.glob("results_*.json"), reverse=True):
        try:
            data = json.loads(result_file.read_text())
            datasets.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return datasets


def _merge_latest(datasets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only the most recent result per dataset URN.

    Assumes ``_load_local_results`` returns reverse-sorted (newest-first)
    entries; the first occurrence per URN wins.
    """
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

    # Filter + paginated table view so the catalog stays usable at scale
    # (the old st.write-per-row approach broke on >50 datasets).
    status_filter = st.selectbox(
        "Filter by status", ["all", "pass", "fail"], index=0,
        help="Show only passing or failing datasets."
    )
    if status_filter != "all":
        df = df[df["Status"] == status_filter]

    if df.empty:
        st.info(f"No datasets with status '{status_filter}'.")
        return

    page_size = st.select_slider("Rows per page", options=[10, 25, 50, 100], value=25)
    total_pages = max(1, (len(df) + page_size - 1) // page_size)
    page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)
    start, end = (int(page) - 1) * page_size, int(page) * page_size
    page_df = df.iloc[start:end]

    for _, row in page_df.iterrows():
        icon = "pass" if row["Status"] == "pass" else "fail"
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
    try:
        dataset = next(d for d in failing if d["dataset_urn"] == choice)
    except StopIteration:
        st.warning("Selected dataset no longer has open incidents.")
        return

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