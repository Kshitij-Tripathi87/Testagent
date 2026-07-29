"""Ingests a small sample catalog into DataHub and generates matching local
sample data (CSVs) that `dhqa check` runs its contract tests against.

Catalog shape (deliberately small so the demo stays legible in 3 minutes):

    orders.raw_orders  -->  orders.stg_orders  -->  orders.fact_orders
                                                  \\-> ml.customer_ltv_features

Two independent things happen here:
  1. Metadata (schema, lineage, ownership) is emitted to a running DataHub
     instance via the real DataHub Python SDK emitter — this is what
     mcp_client.py reads back through the MCP Server/Agent Context Kit.
  2. Local sample data rows are written to data/*.csv — this is what
     dhqa's contract tests actually run against (DataHub is a metadata
     catalog, not a row-level query engine, so the agent needs somewhere
     to pull rows from; a warehouse in a real deployment, CSVs here).

Usage:
    export DATAHUB_GMS_URL=http://localhost:8080   # optional; script still
                                                     # writes local CSVs if
                                                     # DataHub isn't reachable
    python scripts/ingest_sample_data.py
    python scripts/ingest_sample_data.py --inject-fault   # for the demo:
                                                             # corrupts
                                                             # stg_orders
                                                             # without
                                                             # propagating
                                                             # the fix
"""

from __future__ import annotations

import argparse
import csv
import os

from datahub.emitter.mce_builder import make_dataset_urn, make_user_urn
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.metadata.schema_classes import (
    DatasetLineageTypeClass,
    OtherSchemaClass,
    OwnerClass,
    OwnershipClass,
    OwnershipTypeClass,
    SchemaFieldClass,
    SchemaFieldDataTypeClass,
    SchemaMetadataClass,
    StringTypeClass,
    NumberTypeClass,
    TimeTypeClass,
    UpstreamClass,
    UpstreamLineageClass,
)

PLATFORM = "snowflake"
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# (name, platform, [(field, type)], upstream_names, owner)
DATASETS = [
    ("orders.raw_orders", PLATFORM,
     [("id", "string"), ("customer_id", "string"), ("amount", "number"), ("order_ts", "time")],
     [], "data-team"),
    ("orders.stg_orders", PLATFORM,
     [("id", "string"), ("customer_id", "string"), ("amount", "number"), ("order_ts", "time")],
     ["orders.raw_orders"], "data-team"),
    ("orders.fact_orders", PLATFORM,
     [("id", "string"), ("customer_id", "string"), ("amount", "number"), ("order_ts", "time")],
     ["orders.stg_orders"], "data-team"),
    ("ml.customer_ltv_features", PLATFORM,
     [("customer_id", "string"), ("ltv_30d", "number"), ("order_count_30d", "number")],
     ["orders.fact_orders"], "ml-team"),
]

_TYPE_MAP = {
    "string": StringTypeClass(),
    "number": NumberTypeClass(),
    "time": TimeTypeClass(),
}


def build_mcps(name: str, platform: str, fields: list, upstreams: list, owner: str):
    urn = make_dataset_urn(platform=platform, name=name, env="PROD")

    schema_fields = [
        SchemaFieldClass(
            fieldPath=fname,
            type=SchemaFieldDataTypeClass(type=_TYPE_MAP[ftype]),
            nativeDataType=ftype,
        )
        for fname, ftype in fields
    ]
    schema_mcp = MetadataChangeProposalWrapper(
        entityUrn=urn,
        aspect=SchemaMetadataClass(
            schemaName=name,
            platform=f"urn:li:dataPlatform:{platform}",
            version=0,
            hash="",
            platformSchema=OtherSchemaClass(rawSchema=""),
            fields=schema_fields,
        ),
    )

    mcps = [schema_mcp]

    if upstreams:
        lineage_mcp = MetadataChangeProposalWrapper(
            entityUrn=urn,
            aspect=UpstreamLineageClass(
                upstreams=[
                    UpstreamClass(
                        dataset=make_dataset_urn(platform=platform, name=up, env="PROD"),
                        type=DatasetLineageTypeClass.TRANSFORMED,
                    )
                    for up in upstreams
                ]
            ),
        )
        mcps.append(lineage_mcp)

    ownership_mcp = MetadataChangeProposalWrapper(
        entityUrn=urn,
        aspect=OwnershipClass(
            owners=[OwnerClass(owner=make_user_urn(owner), type=OwnershipTypeClass.DATAOWNER)]
        ),
    )
    mcps.append(ownership_mcp)

    return mcps


def emit_to_datahub() -> None:
    gms_url = os.environ.get("DATAHUB_GMS_URL")
    if not gms_url:
        print("DATAHUB_GMS_URL not set — skipping DataHub emission, writing local sample data only.")
        return

    from datahub.emitter.rest_emitter import DatahubRestEmitter

    emitter = DatahubRestEmitter(gms_server=gms_url)
    try:
        emitter.test_connection()
    except Exception as e:
        print(f"Could not reach DataHub at {gms_url} ({e}) — skipping emission, writing local sample data only.")
        return

    for name, platform, fields, upstreams, owner in DATASETS:
        for mcp in build_mcps(name, platform, fields, upstreams, owner):
            emitter.emit(mcp)
        print(f"Emitted metadata for {name}")

    emitter.flush()
    print("Done emitting to DataHub.")


def write_sample_rows(inject_fault: bool) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    n = 20

    raw_rows = [{"id": f"o{i}", "customer_id": f"c{i % 7}", "amount": 10.0 + i, "order_ts": f"2026-08-01T{i:02d}:00:00"}
                for i in range(n)]

    stg_rows = [dict(r) for r in raw_rows]
    if inject_fault:
        # Corrupt 3 rows' customer_id in staging without it being caught upstream —
        # this is the fault the lineage tracer is built to walk back to.
        for i in (2, 5, 9):
            stg_rows[i]["customer_id"] = ""

    # fact_orders only carries forward rows whose customer_id was non-empty at
    # staging time in this simplified demo pipeline, but keeps stale ids that
    # no longer resolve upstream once corrupted — producing the orphaned-key
    # referential failure downstream of the real (staging) root cause.
    fact_rows = [dict(r) for r in stg_rows]

    ltv_rows = [{"customer_id": f"c{c}", "ltv_30d": 100.0 + c * 5, "order_count_30d": 3}
                for c in range(7)]

    _write_csv("raw_orders.csv", raw_rows)
    _write_csv("stg_orders.csv", stg_rows)
    _write_csv("fact_orders.csv", fact_rows)
    _write_csv("customer_ltv_features.csv", ltv_rows)
    print(f"Wrote sample data to {DATA_DIR} (inject_fault={inject_fault})")


def _write_csv(filename: str, rows: list[dict]) -> None:
    path = os.path.join(DATA_DIR, filename)
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inject-fault", action="store_true",
                         help="Corrupt orders.stg_orders customer_id values for the demo trace.")
    args = parser.parse_args()

    emit_to_datahub()
    write_sample_rows(inject_fault=args.inject_fault)


if __name__ == "__main__":
    main()
