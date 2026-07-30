"""Tests for the Streamlit dashboard's data layer.

The pure functions (``_load_local_results``, ``_merge_latest``) are testable
without launching Streamlit. We import the module via importlib so missing
optional deps (streamlit) don't fail collection on a headless CI box.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_dashboard_module():
    """Import dashboard/app.py as a module without requiring streamlit.

    The dashboard only uses streamlit at render time, so stubbing it out lets
    us test the data layer without the heavy UI dependency. We also clear the
    cached module so re-imports pick up a fresh RESULTS_DIR.
    """
    if "dashboard.app" in sys.modules:
        del sys.modules["dashboard.app"]
    if "streamlit" not in sys.modules:
        sys.modules["streamlit"] = type(sys)("streamlit")

    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "dashboard_app_under_test", repo_root / "dashboard" / "app.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_result(p: Path, urn: str, ts: str, checks: list, incidents: list) -> None:
    p.write_text(json.dumps({
        "dataset_urn": urn,
        "timestamp": ts,
        "checks": checks,
        "incidents": incidents,
    }))


def test_load_local_results_reads_json_files(tmp_path):
    mod = _load_dashboard_module()
    mod.RESULTS_DIR = tmp_path
    _write_result(
        tmp_path / "results_a.json", "urn:1", "t1",
        [{"check_id": "c1", "passed": True}], [],
    )
    _write_result(
        tmp_path / "results_b.json", "urn:2", "t2",
        [{"check_id": "c2", "passed": False}], [{"origin_urn": "urn:up"}],
    )
    out = mod._load_local_results()
    assert {d["dataset_urn"] for d in out} == {"urn:1", "urn:2"}


def test_load_local_results_returns_empty_when_dir_missing(tmp_path):
    mod = _load_dashboard_module()
    mod.RESULTS_DIR = tmp_path / "missing"
    assert mod._load_local_results() == []


def test_load_local_results_skips_malformed_json(tmp_path):
    mod = _load_dashboard_module()
    mod.RESULTS_DIR = tmp_path
    _write_result(tmp_path / "results_ok.json", "urn:1", "t", [], [])
    (tmp_path / "results_bad.json").write_text("{ this is not json")
    out = mod._load_local_results()
    assert len(out) == 1
    assert out[0]["dataset_urn"] == "urn:1"


def test_merge_latest_keeps_newest_per_urn(tmp_path):
    mod = _load_dashboard_module()
    entries = [
        {"dataset_urn": "urn:1", "timestamp": "2020", "checks": []},
        {"dataset_urn": "urn:1", "timestamp": "2024", "checks": []},
        {"dataset_urn": "urn:2", "timestamp": "2021", "checks": []},
    ]
    merged = mod._merge_latest(entries)
    by_urn = {d["dataset_urn"]: d for d in merged}
    # Assumes entries are reverse-sorted (newest first); kept entry wins.
    assert by_urn["urn:1"]["timestamp"] == "2020"
    assert by_urn["urn:2"]["timestamp"] == "2021"
    assert len(merged) == 2
