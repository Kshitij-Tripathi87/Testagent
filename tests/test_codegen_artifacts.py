"""Tests for the smart code-generation / PR description / incident report
generators (Track C — the difference between 'feature' and 'product').
"""

from __future__ import annotations

from datetime import datetime, timezone

from dhqa.codegen import to_dbt_model
from dhqa.dataset_model import DatasetContract
from dhqa.incident_report import render_incident_report
from dhqa.lineage_tracer import RootCauseReport
from dhqa.mcp_client import ColumnSpec, DatasetSnapshot
from dhqa.pr_description import render_pr_description
from dhqa.smart_codegen import to_smart_dbt_model
from dhqa.test_generator import CheckResult


def _contract() -> DatasetContract:
    snap = DatasetSnapshot(
        urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)",
        name="fact_orders",
        columns=[
            ColumnSpec(
                name="id", type="string", nullable=False,
                description="Primary key.",
            ),
            ColumnSpec(
                name="customer_id", type="string", nullable=False,
                description="Customer foreign key.",
            ),
            ColumnSpec(
                name="amount", type="float", nullable=True,
                description="Order total in USD.",
            ),
            ColumnSpec(
                name="order_ts", type="timestamp", nullable=False,
                description="When the order was placed.",
            ),
        ],
        owners=["urn:li:corpuser:data-team"],
        upstream_urns=[
            "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD)"
        ],
    )
    return DatasetContract.from_snapshot(snap)


def test_smart_codegen_wires_upstream_relationship_tests():
    c = _contract()
    sql = to_smart_dbt_model(c)
    assert "fact_orders" in sql
    assert "{ source('raw', 'fact_orders') }" in sql
    # relationships test must match each referential join column
    assert "relationships" in sql
    # freshness SLA must be referenced explicitly
    assert "order_ts" in sql
    assert "24" in sql  # SLA default


def test_smart_codegen_contains_dbt_config_block():
    c = _contract()
    sql = to_smart_dbt_model(c)
    assert "{{ config(" in sql
    assert "materialized" in sql
    assert "incremental" in sql


def test_pr_description_includes_all_constraint_classes():
    c = _contract()
    md = render_pr_description(c)
    assert "fact_orders" in md
    # not_null on id and customer_id
    assert "not_null" in md
    # unique on id (PK)
    assert "unique" in md
    # freshness SLA visible
    assert "24h" in md
    # references the upstream so reviewers see the lineage impact
    assert "stg_orders" in md
    assert "DataHub" in md


def test_incident_report_includes_origin_and_trace():
    c = _contract()
    failing = CheckResult(
        check_id="not_null_customer_id_1",
        kind="not_null",
        column="customer_id",
        passed=False,
        detail="10 null values in customer_id",
    )
    report = RootCauseReport(
        failing_check=failing,
        origin_urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD)",
        hop_distance=2,
        trace=[
            {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD)",
             "checked": "not_null_1", "passed": False,
             "detail": "10 null values in customer_id"},
            {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.raw_orders,PROD)",
             "checked": "not_null_0", "passed": True,
             "detail": "0 null values"},
        ],
    )
    md = render_incident_report(report, contract=c, all_results=[failing])
    # origin must be the short name "stg_orders"
    assert "stg_orders" in md
    # the hop-by-hop walk must appear
    assert "raw_orders" in md
    # the verdict / action items must appear
    assert "Action items" in md
    assert "DataHub lineage" in md


def test_incident_report_handles_empty_trace():
    """When trace is empty (origin is the current dataset), the report still reads sensibly."""
    failing = CheckResult(
        check_id="unique_id_3", kind="unique", column="id", passed=False,
        detail="3 duplicate values",
    )
    report = RootCauseReport(
        failing_check=failing,
        origin_urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)",
        hop_distance=0,
        trace=[],
    )
    md = render_incident_report(report)
    assert "fact_orders" in md
    assert "Action items" in md


def test_incident_report_referential_writes_why_section():
    """Regression: the 'Why we caught this' section must emit content for a
    failing `referential` check (the demo's actual failure mode), not a blank
    section."""
    c = _contract()
    failing = CheckResult(
        check_id="referential_4",
        kind="referential",
        column="id",
        passed=False,
        detail="3 orphaned keys vs urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD) (on id)",
    )
    report = RootCauseReport(
        failing_check=failing,
        origin_urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)",
        hop_distance=1,
        trace=[
            {"urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD)",
             "checked": "referential_5", "passed": True,
             "detail": "0 orphaned keys"},
        ],
    )
    md = render_incident_report(report, contract=c, all_results=[failing])
    # the section header must be present AND followed by real content
    assert "## Why we caught this" in md
    assert "referential" in md
    # the upstream parent (stg_orders) must be named in the rationale
    assert "stg_orders" in md
    # the failing column must be named
    assert "`id`" in md


def test_to_dbt_model_unchanged_baseline():
    """Guard against accidental changes to the basic codegen."""
    c = _contract()
    sql = to_dbt_model(c)
    for col in ("id", "customer_id", "amount", "order_ts"):
        assert col in sql
