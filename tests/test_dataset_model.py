from datetime import datetime, timedelta, timezone

from dhqa.codegen import to_dbt_model, to_schema_yml
from dhqa.dataset_model import DatasetContract
from dhqa.mcp_client import ColumnSpec, DatasetSnapshot
from dhqa.test_generator import generate_conftest, generate_pytest_module, run_checks


def _sample_snapshot() -> DatasetSnapshot:
    return DatasetSnapshot(
        urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)",
        name="fact_orders",
        columns=[
            ColumnSpec(name="id", type="string", nullable=False),
            ColumnSpec(name="customer_id", type="string", nullable=False),
            ColumnSpec(name="amount", type="float", nullable=True),
            ColumnSpec(name="order_ts", type="timestamp", nullable=False),
        ],
        owners=["urn:li:corpuser:data-team"],
        upstream_urns=["urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.raw_orders,PROD)"],
    )


def test_contract_derives_not_null_constraints():
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    not_null_cols = {c.column for c in contract.constraints if c.kind == "not_null"}
    assert not_null_cols == {"id", "customer_id", "order_ts"}


def test_contract_derives_unique_constraint_for_pk():
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    unique_cols = {c.column for c in contract.constraints if c.kind == "unique"}
    assert "id" in unique_cols


def test_contract_derives_referential_constraint_per_upstream():
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    referential = [c for c in contract.constraints if c.kind == "referential"]
    assert len(referential) == 1
    assert referential[0].ref_urn == _sample_snapshot().upstream_urns[0]
    # join key defaults to "id" — drives the referential check correctly.
    assert referential[0].column == "id"


def test_contract_derives_freshness_constraint_with_timestamp_column():
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    freshness = [c for c in contract.constraints if c.kind == "freshness"]
    assert len(freshness) == 1
    assert freshness[0].column == "order_ts"
    assert freshness[0].max_staleness_hours == 24


def test_dbt_model_includes_all_columns():
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    sql = to_dbt_model(contract)
    for col in ("id", "customer_id", "amount", "order_ts"):
        assert col in sql


def test_schema_yml_marks_not_null_columns():
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    yml = to_schema_yml(contract)
    assert "id" in yml and "not_null" in yml


def test_schema_yml_emits_unique_and_relationships_tests():
    """schema.yml must carry unique (PK) and relationships (lineage) tests,
    not only not_null — previously the basic codegen silently dropped these."""
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    yml = to_schema_yml(contract)
    assert "unique" in yml, "unique test must be emitted for the PK column"
    assert "relationships" in yml, "relationships test must be emitted for upstream lineage"
    # relationships must wire to the upstream model via ref(), not a raw URN
    assert "ref('raw_orders')" in yml or "ref('fact_orders')" in yml
    # parsed YAML must remain valid (the generator must not emit broken YAML)
    import yaml as _yaml
    parsed = _yaml.safe_load(yml)
    assert parsed["version"] == 2
    assert parsed["models"][0]["name"] == "fact_orders"


def test_generate_conftest_is_valid_python_and_wires_fixtures():
    """The generated conftest.py must be syntactically valid Python and must
    provide the `df` / `upstream`/`upstream_df_*` fixtures the generated test
    module references (otherwise pytest errors with `fixture 'df' not found`)."""
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    src = generate_conftest(contract, fixture_dir="data/fixtures")
    # must compile (catches syntax errors / unescaped URNs with parens)
    compile(src, "<generated-conftest>", "exec")
    assert "def df" in src or "@pytest.fixture" in src
    assert "_load_df" in src
    assert "DATASET_URN" in src
    assert "UPSTREAM_URNS" in src


def test_pytest_module_has_one_test_per_constraint():
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    module_src = generate_pytest_module(contract)
    assert module_src.count("def test_") == len(contract.constraints)


def test_pytest_module_generates_unique_test():
    """Pytest module must include a uniqueness test for the PK column."""
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    module_src = generate_pytest_module(contract)
    assert "def test_unique_" in module_src
    assert ".duplicated()" in module_src


def test_pytest_module_generates_freshness_test_with_sla():
    """Pytest module must include a freshness test against the timestamp column."""
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    module_src = generate_pytest_module(contract)
    assert "def test_freshness_" in module_src
    assert "order_ts" in module_src
    assert "24" in module_src  # 24h SLA


def _fresh_df(num_rows=10, hours_old=1):
    import pandas as pd
    now = datetime.now(timezone.utc)
    return pd.DataFrame({
        "id": [f"id_{i}" for i in range(num_rows)],
        "customer_id": [f"cust_{i}" for i in range(num_rows)],
        "order_ts": [now - timedelta(hours=hours_old)] * num_rows,
    })


def test_run_checks_not_null_passes_when_clean():
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    df = _fresh_df(num_rows=10, hours_old=1)
    results = run_checks(contract, df, upstream_dfs={}, freshness_now=datetime.now(timezone.utc))
    nn = [r for r in results if r.kind == "not_null"]
    assert all(r.passed for r in nn)


def test_run_checks_unique_fails_on_duplicates():
    import pandas as pd
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    df = pd.DataFrame({
        "id": ["a", "a", "b"],  # duplicate
        "customer_id": ["c", "c", "d"],
        "order_ts": [datetime.now(timezone.utc)] * 3,
    })
    results = run_checks(contract, df, upstream_dfs={}, freshness_now=datetime.now(timezone.utc))
    unique = [r for r in results if r.kind == "unique"]
    assert any(not r.passed for r in unique)


def test_run_checks_freshness_passes_for_recent_data():
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    df = _fresh_df(num_rows=5, hours_old=2)
    now = datetime.now(timezone.utc)
    results = run_checks(contract, df, upstream_dfs={}, freshness_now=now)
    freshness = [r for r in results if r.kind == "freshness"]
    assert freshness and freshness[0].passed
    assert isinstance(freshness[0].column, str)


def test_run_checks_freshness_fails_for_stale_data():
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    df = _fresh_df(num_rows=5, hours_old=72)  # outside 24h SLA
    now = datetime.now(timezone.utc)
    results = run_checks(contract, df, upstream_dfs={}, freshness_now=now)
    freshness = [r for r in results if r.kind == "freshness"]
    assert freshness
    assert not freshness[0].passed


def test_run_checks_referential_uses_join_key_column():
    """Verify the referential check uses the constraint's column, not hardcoded."""
    import pandas as pd
    contract = DatasetContract.from_snapshot(_sample_snapshot())
    # Build a contract that joins on customer_id (override default join).
    contract.constraints.append(
        __import__("dhqa.dataset_model", fromlist=["Constraint"]).Constraint(
            kind="referential",
            column="customer_id",
            ref_urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.customers,PROD)",
        )
    )
    df = pd.DataFrame({
        "id": ["a", "b"],
        "customer_id": ["c1", "c2"],
        "order_ts": [datetime.now(timezone.utc)] * 2,
    })
    upstream = {"urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.customers,PROD)": pd.DataFrame({"customer_id": ["c1"]})}
    results = run_checks(contract, df, upstream_dfs=upstream)
    refs = [r for r in results if r.kind == "referential" and r.column == "customer_id"]
    assert refs
    assert not refs[0].passed  # c2 is orphaned
