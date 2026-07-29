# DataHub Contract QA Agent

**An agent that treats your DataHub catalog as the source of truth for data contracts — generates pipeline code and tests from it, root-causes failures by walking the lineage graph, and writes verdicts back into DataHub instead of just failing a CI job silently.**

Built for the DataHub Agent Hackathon — Category: *Metadata-Aware Code Generation & Development* (with a *Production ML Agents* extension for training/serving skew).

## The problem

Most "AI writes my dbt model" demos stop at code generation. They don't answer the question a data engineer actually asks before merging a PR: **is this code correct against what's really in production, and if it breaks, why?**

Two failure modes this agent targets:
1. Generated (or hand-written) pipeline code silently drifts from the real schema/lineage/ownership rules declared in DataHub.
2. When a data quality check fails, engineers get a red X with no story — they have to manually trace the failure upstream through the lineage graph themselves.

## What it does

1. **Reads** DataHub via the MCP Server / Agent Context Kit — schema, lineage, ownership, glossary terms, and existing assertions for a dataset. **Offline path:** the same code reads a local fixture directory (CSV + manifest), so the whole agent runs without a DataHub instance.
2. **Builds a Dataset Object Model (DOM)** — a typed, versioned contract object per dataset (borrowed from the Page-Object-Model pattern in UI test automation: one canonical object per entity, everything else references it instead of hardcoding assumptions).
3. **Generates** production pipeline code (dbt model / Airflow DAG) *and* a matching test suite (schema, null/uniqueness, referential integrity, freshness, lineage-consistency) from the DOM — not from a prompt guessing at the schema.
4. **Runs** the tests. On failure, the **Lineage Tracer** walks upstream through DataHub's lineage graph, hop by hop, until it finds the dataset/column where the anomaly actually originates, and produces a root-cause note instead of a bare failure.
5. **Writes back** to DataHub: pass/fail assertions, a data quality score, and an incident annotation with the traced root cause — so the next person (or agent) inherits the diagnosis, not just the alarm. **Offline path:** the same shapes are written to local JSON results that the dashboard reads.
6. **Gates CI**: a GitHub Action blocks the PR if generated/changed code fails its DataHub-derived contract tests, and posts the failure + root cause as a PR comment.
7. **Dashboard** (Streamlit): contract health across the catalog, pass/fail history, and a lineage-traced incident timeline.

## Why this is different from a generic "agent reads DataHub" demo

- It **writes back** to the graph (assertions + incidents), not just reads from it.
- It produces an artifact — a real PR with generated code, tests, and a CI gate — a data engineer could actually merge, per the hackathon's own submission bar.
- Root-cause tracing uses the lineage graph as an actual search space, not a lookup.
- The agent's **two data sources are interchangeable** behind the same DOM contract: an offline fixture dir (no infra) and live DataHub via MCP. Every code path — codegen, tests, tracer, writeback, dashboard — works on either.

## Architecture

```
DataHub (MCP Server / Agent Context Kit)   — or —   Local fixture (CSV + manifest.json)
         │  schema, lineage, ownership, assertions
         ▼
   mcp_client.py / local_fixture.py  ──►  dataset_model.py (DOM)
                                                  │
                          ┌───────────────────────┼───────────────────────┐
                          ▼                       ▼                       ▼
                    codegen.py          test_generator.py        lineage_tracer.py
                          │                       │                       │
                          └──────────► GitHub PR / local demo ◄───────────┘
                                                  │
                                          .github/workflows/dhqa-gate.yml (CI gate)
                                                  │
              writeback.py / local_writeback.py ─► DataHub assertions + incidents (or JSON results)
                                                  │
                                          dashboard/app.py (Streamlit)
```

## Repo layout

```
src/dhqa/
  local_fixture.py        # Offline fixture layer (Track A): CSV + manifest store
  local_lineage_tracer.py  # Local client shim around the shared lineage walker
  local_writeback.py       # JSON results writer (offline twin of DataHub write-back)
  mcp_client.py            # DataHub MCP Server / Agent Context Kit client wrapper
  dataset_model.py         # Dataset Object Model (DOM): typed contract per dataset
  codegen.py               # Generates dbt models / Airflow DAGs from a DOM
  test_generator.py        # Generates the matching test suite from a DOM
  lineage_tracer.py        # Walks DataHub lineage graph to root-cause a failing check
  writeback.py             # Writes assertions/incidents back to DataHub (live path)
  cli.py                   # `dhqa` command-line entrypoint
scripts/
  ingest_local_fixtures.py # Generates the offline demo fixtures (CSV + manifest)
  ingest_sample_data.py    # Emits the sample catalog to a live DataHub instance
data/fixtures/             # Offline fixture data + manifest.json (Track A)
tests/                     # Unit + offline-path tests for the agent
.github/workflows/         # CI gate that runs generated tests on PRs
examples/sample_generated/ # Sample generated dbt model + tests + incident report
dashboard/app.py           # Streamlit contract-health dashboard
```

## Quick start — offline (no DataHub needed)

The agent runs end-to-end offline on local fixtures, so you can see the
root-cause trace without standing up DataHub:

```bash
pip install -e .
python scripts/ingest_local_fixtures.py          # builds data/fixtures/
dhqa check --dataset "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)" \
           --local-fixture ./data/fixtures
```

Expected output: a not_null check on `fact_orders.customer_id` fails and is
traced **2 hops upstream to `orders.stg_orders`** (the defect origin); a
referential check on `fact_orders.id` fails and is traced **back to
`fact_orders` itself** (the defect is local, not propagated). Results are
written to `data/fixtures/results/<urn>/<timestamp>.json`.

Generate the same artifacts a PR would ship:

```bash
dhqa generate --dataset "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)" \
              --local-fixture ./data/fixtures -o ./examples/sample_generated
```

Dashboard (reads the local JSON results):

```bash
dhqa check --dataset "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)" --local-fixture ./data/fixtures
DHQA_RESULTS_DIR=./data/fixtures/results streamlit run dashboard/app.py
```

(With no `DHQA_RESULTS_DIR` set, the dashboard defaults to
`./data/fixtures/results`, so the override is only needed for custom paths.)

## Setup — live DataHub (Track B)

```bash
pip install -r requirements.txt
export DATAHUB_GMS_URL=<your DataHub instance>
export DATAHUB_TOKEN=<your token>
dhqa generate --dataset urn:li:dataset:(...) --mcp    # generate code + tests from live schema
dhqa check --dataset urn:li:dataset:(...) --mcp       # run tests, write back to DataHub
streamlit run dashboard/app.py
```

## Tests

```bash
python -m pytest -q          # 31 tests covering DOM, offline fixture, codegen, MCP, writeback
```

The offline test suite builds an ephemeral fixture directory under `tmp_path`
using the real fixture generator, then exercises the full path:
manifest/CSV load → contract → checks → tracer → JSON writeback → CLI.

The MCP integration tests are skipped unless `DATAHUB_GMS_URL` is set in the
environment:

```bash
DATAHUB_GMS_URL=http://localhost:8080 \
DATAHUB_TOKEN=... \
python -m pytest tests/test_mcp_client.py -v
```

## Demo in three commands

```bash
# 1. Build the offline fixture (raw -> stg -> fact -> ml feature table,
#    with deliberate defects: 10 null customer_ids in stg, 3 orphan keys in fact).
python scripts/ingest_local_fixtures.py

# 2. Walk the agent through the full pipeline:
dhqa generate --dataset "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)" \
              --local-fixture data/fixtures -o examples/sample_generated/fact_orders
dhqa check   --dataset "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)" \
              --local-fixture data/fixtures

# 3. View the contract health dashboards:
streamlit run dashboard/app.py
```

What you'll see:

| Output | Why it matters |
|--------|----------------|
| `fact_orders.sql` + `schema.yml` + `test_fact_orders.py` | The full dbt + pytest artifact, ready to PR |
| `fact_orders_smart.sql` | Production-style dbt config + relationships tests wired to upstream `stg_orders` |
| `PR_DESCRIPTION.md` | Auto-generated PR description naming lineage impact + test coverage |
| `incident_report_fact_orders.md` | The at-a-glance verdict + action items a junior engineer can act on |
| `<dataset>.results.json` + `<dataset>.incident.json` | The same shapes that would be written to DataHub assertions/incidents in the live path |

## Status

All three tracks complete and tested:

- **Track A** — offline fixture end-to-end demo (works without DataHub)
- **Track B** — MCP client (GraphQL + MCP Server transports, async, retry-ready)
- **Track C** — CI gate, dashboard, sample artifacts, auto-generated PR descriptions + incident reports

See `PLAN.md` for the day-by-day build plan.

## License

Apache 2.0 — see `LICENSE`.
