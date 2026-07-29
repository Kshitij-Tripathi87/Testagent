"""Tests for the offline fixture path (Track A).

These cover the whole offline slice: loading a LocalFixtureStore, deriving a
DatasetContract from a local snapshot, running checks against injected
defects, lineage-tokenized walking via LocalLineageTracer, and JSON writeback.
Mirrors the live path's contract so the same unit tests will guard Track B.
"""

from __future__ import annotations

import json

from dhqa.dataset_model import DatasetContract
from dhqa.local_lineage_tracer import LocalLineageTracer, build_recheck_fn
from dhqa.local_writeback import write_local_result
from dhqa.test_generator import run_checks

from conftest import FACT_URN, RAW_URN, STG_URN


# --- fixture store ----------------------------------------------------------

def test_store_loads_snapshot_and_df(store):
    local = store.get_dataset(STG_URN)
    assert local.urn == STG_URN
    assert local.name == "stg_orders"
    assert {"id", "customer_id", "amount", "order_ts"} <= set(local.df.columns)
    assert "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.raw_orders,PROD)" in local.upstream_urns


def test_store_lineage_upstream_and_downstream(store):
    assert store.get_lineage(STG_URN, "upstream", 1) == [RAW_URN]
    assert store.get_lineage(RAW_URN, "downstream", 1) == [STG_URN]
    assert store.get_lineage(FACT_URN, "upstream", 1) == [STG_URN]


def test_store_raises_on_missing_manifest(tmp_path):
    import pytest
    from dhqa.local_fixture import LocalFixtureStore
    with pytest.raises(FileNotFoundError):
        LocalFixtureStore(tmp_path)


# --- contract derivation ----------------------------------------------------

def test_contract_from_local_snapshot_builds_constraints(store):
    contract = DatasetContract.from_local_snapshot(store.get_dataset(STG_URN))
    not_null_cols = {c.column for c in contract.constraints if c.kind == "not_null"}
    assert not_null_cols == {"id", "customer_id", "amount", "order_ts"}
    refs = [c for c in contract.constraints if c.kind == "referential"]
    assert [c.ref_urn for c in refs] == [RAW_URN]


# --- checks against injected defects ---------------------------------------

def test_stg_null_check_fails(store):
    contract = DatasetContract.from_local_snapshot(store.get_dataset(STG_URN))
    local = store.get_dataset(STG_URN)
    results = run_checks(contract, local.df, store.get_upstream_dfs(STG_URN))
    nn_customer = next(r for r in results if r.kind == "not_null" and r.column == "customer_id")
    assert not nn_customer.passed
    assert "null values" in nn_customer.detail


def test_fact_referential_check_fails(store):
    contract = DatasetContract.from_local_snapshot(store.get_dataset(FACT_URN))
    local = store.get_dataset(FACT_URN)
    results = run_checks(contract, local.df, store.get_upstream_dfs(FACT_URN))
    ref = next(r for r in results if r.kind == "referential")
    assert not ref.passed
    assert "orphaned keys" in ref.detail


# --- lineage tracer --------------------------------------------------------

def test_not_null_trace_pinpoints_stg_as_origin(store):
    contract = DatasetContract.from_local_snapshot(store.get_dataset(FACT_URN))
    local = store.get_dataset(FACT_URN)
    results = run_checks(contract, local.df, store.get_upstream_dfs(FACT_URN))
    failing = next(r for r in results if r.kind == "not_null" and r.column == "customer_id")

    from dhqa.lineage_tracer import trace_root_cause
    tracer = LocalLineageTracer(store)
    recheck = build_recheck_fn(store, "not_null", "customer_id")
    report = trace_root_cause(tracer, failing, FACT_URN, recheck)

    assert report.origin_urn == STG_URN
    # walk: fact -> stg (fail) -> raw (ok)  => 2 hops
    assert report.hop_distance == 2
    assert report.trace[0]["urn"] == STG_URN
    assert report.trace[0]["passed"] is False
    assert report.trace[1]["urn"] == RAW_URN
    assert report.trace[1]["passed"] is True
    assert "stg_orders" in report.summary()


def test_referential_trace_origin_is_fact_itself(store):
    # The orphaned-key defect is local to fact_orders (orphan ids invented in
    # fact), so walking upstream to stg should pass -> origin = fact_orders.
    contract = DatasetContract.from_local_snapshot(store.get_dataset(FACT_URN))
    local = store.get_dataset(FACT_URN)
    results = run_checks(contract, local.df, store.get_upstream_dfs(FACT_URN))
    failing = next(r for r in results if r.kind == "referential")

    from dhqa.lineage_tracer import trace_root_cause
    tracer = LocalLineageTracer(store)
    recheck = build_recheck_fn(store, "referential", None)
    report = trace_root_cause(tracer, failing, FACT_URN, recheck)

    assert report.origin_urn == FACT_URN
    assert report.hop_distance == 1
    assert report.trace[0]["urn"] == STG_URN
    assert report.trace[0]["passed"] is True


# --- writeback --------------------------------------------------------------

def test_write_local_result_serializes_incidents(store, tmp_path):
    contract = DatasetContract.from_local_snapshot(store.get_dataset(FACT_URN))
    local = store.get_dataset(FACT_URN)
    results = run_checks(contract, local.df, store.get_upstream_dfs(FACT_URN))
    failing = next(r for r in results if r.kind == "not_null" and r.column == "customer_id")

    from dhqa.lineage_tracer import trace_root_cause
    tracer = LocalLineageTracer(store)
    recheck = build_recheck_fn(store, "not_null", "customer_id")
    report = trace_root_cause(tracer, failing, FACT_URN, recheck)

    out = write_local_result(tmp_path, FACT_URN, results, [report])
    payload = json.loads(out.read_text())
    assert payload["dataset_urn"] == FACT_URN
    assert any(not c["passed"] for c in payload["checks"])
    assert payload["incidents"][0]["origin_urn"] == STG_URN
    assert payload["incidents"][0]["hop_distance"] == 2


# --- CLI --------------------------------------------------------------------

def test_cli_check_local_runs_end_to_end(store, fixture_dir, tmp_path, capsys):
    from dhqa.cli import main
    import sys
    out_dir = tmp_path / "results"
    argv = ["dhqa", "check",
            "--dataset", FACT_URN,
            "--local-fixture", str(fixture_dir),
            "--output", str(out_dir)]
    old = sys.argv
    sys.argv = argv
    try:
        main()
    finally:
        sys.argv = old
    captured = capsys.readouterr()
    assert "incident(s) traced" in captured.out
    assert "stg_orders" in captured.out
    json_files = list((out_dir).rglob("*.json"))
    assert json_files
