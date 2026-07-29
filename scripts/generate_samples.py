"""Regenerates examples/sample_generated/ from a fixed sample DatasetContract.

Run with: python scripts/generate_samples.py

Uses a hand-built DatasetSnapshot (not a live DataHub read) so the sample
outputs in the repo are reproducible without a DataHub instance. Swap in a
DataHubMCPClient().get_dataset(urn) call to regenerate from a live catalog.
"""

from __future__ import annotations

import os

from dhqa.codegen import to_dbt_model, to_schema_yml
from dhqa.dataset_model import DatasetContract
from dhqa.mcp_client import ColumnSpec, DatasetSnapshot
from dhqa.test_generator import generate_pytest_module

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "examples", "sample_generated")


def sample_snapshot() -> DatasetSnapshot:
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
        upstream_urns=["urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD)"],
    )


def main() -> None:
    contract = DatasetContract.from_snapshot(sample_snapshot())
    os.makedirs(OUT_DIR, exist_ok=True)

    with open(os.path.join(OUT_DIR, "fact_orders.sql"), "w") as f:
        f.write(to_dbt_model(contract))
    with open(os.path.join(OUT_DIR, "schema.yml"), "w") as f:
        f.write(to_schema_yml(contract))
    with open(os.path.join(OUT_DIR, "test_fact_orders.py"), "w") as f:
        f.write(generate_pytest_module(contract))

    print(f"Wrote sample outputs to {OUT_DIR}")


if __name__ == "__main__":
    main()
