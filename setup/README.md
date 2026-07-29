# Setup — local DataHub instance for the demo

This is Day 1–3 of `PLAN.md`. Goal: a running DataHub instance with a small,
realistic sample catalog (raw → staging → mart lineage, plus one ML feature
table) that `mcp_client.py` and `scripts/ingest_sample_data.py` can both
point at.

## 1. Stand up DataHub

DataHub ships an official quickstart that manages its own Docker Compose
stack — use that instead of hand-rolling a compose file, it stays correct
across DataHub versions:

```bash
pip install acryl-datahub
datahub docker quickstart
```

This brings up GMS (the metadata service), the frontend UI (localhost:9002),
Kafka, and the backing stores. First run takes a few minutes to pull images.

Verify it's healthy:
```bash
datahub docker check
```

## 2. Ingest the sample catalog

`scripts/ingest_sample_data.py` emits four datasets with real lineage edges
using the DataHub Python SDK's metadata emitter (`DatahubRestEmitter`) —
not a UI click-through, so it's reproducible and demo-safe:

```
orders.raw_orders  →  orders.stg_orders  →  orders.fact_orders
                                          ↘
                                       ml.customer_ltv_features  (feature table, for the ML-lineage stretch goal)
```

Run it once DataHub is up:
```bash
export DATAHUB_GMS_URL=http://localhost:8080
python scripts/ingest_sample_data.py
```

Confirm it landed: open http://localhost:9002, search `fact_orders`, and
check the Lineage tab shows the upstream chain.

## 3. Point the MCP Server / Agent Context Kit at this instance

Once DataHub's MCP Server is running against this instance (see DataHub's
own docs for enabling it — this varies by DataHub version, confirm against
whatever's current when you do this step), fill in `mcp_client.py`'s
`get_dataset` / `get_lineage` / `write_assertion` / `write_incident` methods
against the real MCP session. The method signatures and `DatasetSnapshot`
shape in that file are already built to match what the rest of `dhqa` needs
— don't change the contract, just fill in the bodies.

## 4. Break something on purpose (for the demo)

For the video's "money shot" (PLAN.md, 1:00–1:50): after ingesting clean
data once, re-run `scripts/ingest_sample_data.py --inject-fault` (see the
script's `--inject-fault` flag) to introduce null `customer_id` values in
`orders.stg_orders` without updating `orders.fact_orders` — this is exactly
the scenario the lineage tracer is built to catch and explain.
