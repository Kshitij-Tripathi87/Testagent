"""Generates production pipeline code from a DatasetContract.

Ships with a dbt model generator. Airflow DAG generation is a stretch
target (see PLAN.md scope cuts) — add an `to_airflow_dag` alongside
`to_dbt_model` following the same shape if time allows.
"""

from __future__ import annotations

from dhqa.config import DEFAULT_SOURCE_NAME
from dhqa.dataset_model import DatasetContract
from dhqa.urn_utils import upstream_model_name


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
    from {{{{ source('{DEFAULT_SOURCE_NAME}', '{contract.name}') }}}}
)

select * from source
"""


def to_schema_yml(contract: DatasetContract) -> str:
    """Render the matching dbt schema.yml with column-level tests wired in.

    Emits ``not_null``, ``unique``, and ``relationships`` tests derived from
    the contract's declared constraints (previously this only emitted
    ``not_null``, silently dropping the unique/referential coverage the smart
    codegen already handled). The DBT test names here mirror what
    ``test_generator.py`` produces standalone, so the same contract drives
    both dbt-native tests and the dhqa test suite.
    """
    not_null_cols = {c.column for c in contract.constraints if c.kind == "not_null"}
    unique_cols = {c.column for c in contract.constraints if c.kind == "unique"}
    rels = [c for c in contract.constraints if c.kind == "referential"]

    lines = [f"version: 2\n\nmodels:\n  - name: {contract.name}\n    columns:"]
    for col in contract.columns:
        tests = []
        if col.name in not_null_cols:
            tests.append("not_null")
        if col.name in unique_cols:
            tests.append("unique")
        for rel in rels:
            if (rel.column or "id") == col.name:
                target = upstream_model_name(rel.ref_urn) or rel.ref_urn or "<upstream>"
                tests.append(f"relationships(to=ref('{target}'), field='{col.name}')")
        test_block = ("\n        tests:\n" + "\n".join(f"          - {t}" for t in tests)) if tests else ""
        lines.append(f"      - name: {col.name}{test_block}")
    return "\n".join(lines) + "\n"
