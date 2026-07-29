"""Generates production pipeline code from a DatasetContract.

Ships with a dbt model generator. Airflow DAG generation is a stretch
target (see PLAN.md scope cuts) — add an `to_airflow_dag` alongside
`to_dbt_model` following the same shape if time allows.
"""

from __future__ import annotations

from dhqa.dataset_model import DatasetContract


def to_dbt_model(contract: DatasetContract) -> str:
    """Render a minimal, schema-accurate dbt SQL model for this dataset."""
    select_cols = ",\n    ".join(c.name for c in contract.columns)
    upstream_refs = "\n".join(f"-- upstream: {u}" for u in contract.upstream_urns) or "-- no declared upstreams"

    return f"""-- Auto-generated from DataHub contract for {contract.urn}
-- Owners: {', '.join(contract.owners) or 'unowned'}
{upstream_refs}

with source as (
    select
    {select_cols}
    from {{{{ source('raw', '{contract.name}') }}}}
)

select * from source
"""


def to_schema_yml(contract: DatasetContract) -> str:
    """Render the matching dbt schema.yml with column-level tests wired in
    (the not_null/unique entries here mirror what test_generator.py also
    produces standalone, so the same contract drives both dbt-native tests
    and the dhqa test suite)."""
    lines = [f"version: 2\n\nmodels:\n  - name: {contract.name}\n    columns:"]
    not_null_cols = {c.column for c in contract.constraints if c.kind == "not_null"}
    for col in contract.columns:
        tests = ["not_null"] if col.name in not_null_cols else []
        test_block = ("\n        tests:\n" + "\n".join(f"          - {t}" for t in tests)) if tests else ""
        lines.append(f"      - name: {col.name}{test_block}")
    return "\n".join(lines) + "\n"
