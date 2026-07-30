# 20-Day Solo Build Plan

Scope discipline: the demo needs to be narrow, real, and reliable — one DataHub instance, a handful of datasets, one code target (dbt), one CI gate. Depth beats breadth for judging.

## Days 1–3 — Foundation
- Stand up a local/dev DataHub instance (docker-compose quickstart) and ingest a small, realistic sample dataset set (5–8 tables with real lineage edges — e.g. a raw → staging → mart chain plus one ML feature table). Reuse a public sample dataset so the demo story is legible to judges in 3 minutes.
- Get the MCP Server (or Agent Context Kit) talking end-to-end: pull schema, lineage, ownership, and any existing assertions for one dataset. This is the riskiest integration point — de-risk it first, not last.
- Lock the Dataset Object Model (DOM) schema: fields, types, constraints, lineage edges, owner, glossary terms.

## Days 4–7 — Codegen + test generation
- `codegen.py`: DOM → dbt model (start with dbt only; Airflow DAG is a stretch goal, not a requirement).
- `test_generator.py`: DOM → test suite (schema match, null/uniqueness, referential integrity against declared lineage parents, freshness).
- Deliberately break one upstream table (bad type, dropped column) and confirm the generated tests actually catch it. This is your core proof of value — get it working early, not the night before.

## Days 8–11 — Lineage tracer (the differentiator)
- `lineage_tracer.py`: on a failing check, walk DataHub's lineage graph upstream hop-by-hop, running the same class of check at each ancestor, until you find the origin. Produce a structured root-cause note (dataset, column, hop distance, evidence).
- This is the single most demo-able moment — invest the most polish here. Build the CLI output and the eventual dashboard view around narrating this trace.

## Days 12–14 — Write-back
- `writeback.py`: push pass/fail assertions and an incident annotation (with the traced root cause) back into DataHub for the affected datasets. Confirm it's visible in the DataHub UI — judges reading "contributes back to the graph" will want to see this, not take your word for it.

## Days 15–16 — CI gate
- `.github/workflows/dhqa-gate.yml`: on PR, run `dhqa check` against changed dbt models, fail the build and post a PR comment with the root-cause trace if a contract test fails. Do this against a real (throwaway) GitHub repo so you have a real PR screenshot/clip for the video.

## Days 17–18 — Dashboard + polish
- `dashboard/app.py`: contract health across the sample catalog, pass/fail history, incident timeline with lineage traces. Keep it small — 3 views max (catalog health, incident detail, history).
- Fill `examples/sample_generated/` with real generated dbt model + tests + one incident report, so judges can evaluate output quality without running anything.

## Day 19 — Video + README pass
- Script the 3-minute demo tightly (see below). Record, don't wing it — judges are not required to watch past 3:00.
- Finalize README: what it does, why it's different, setup, architecture diagram, sample outputs called out explicitly.

## Day 20 — Buffer
- Fix whatever broke. Submit early, not at the deadline.

---

## Completed (post-hackathon scope polish)

- **Bug fixes** — 8 session fixes:
  - `mcp_client.py`: `assertion_list` -> `assertion_urns`; `self._graphql` -> `self._execute_graphql`
  - `tests/conftest.py`: wrong fixture-generator function names + wrong manifest format
  - `tests/test_mcp_client.py`: `dhqa.cloud` -> `dhqa.mcp_client`
  - `local_fixture.py.get_dataset()` now returns the snapshot (not raw DataFrame)
  - `dataset_model.py`: stop mutating columns when deriving PKs
  - `cli.py`: dedup duplicate check blocks

- **Contract completeness** — added `unique` + `freshness` checks, parameterized `referential` join key (was hardcoded `"id"`)

- **Smart codegen** — `smart_codegen.to_smart_dbt_model()` renders a production-quality dbt model (config block + freshness filter + relationship tests wired to upstream via `ref()`) beyond the existing passthrough template

- **Artifact polish** — `pr_description.render_pr_description()` auto-generates a markdown PR description with lineage impact + test coverage; `incident_report.render_incident_report()` produces the markdown incident report that lands as a DataHub incident annotation

- **Tests** — 67 tests passing (5 dataset_model + 7 codegen_artifacts + 10 local_fixture + 3 skipped mcp + 13 urn/mcp/writeback/dashboard/ml + 3 new codegen/conftest) covering: DOM derivation (incl PK/timestamp heuristics), fixture store, lineage walk, writeback serialization, CLI end-to-end, smart codegen, PR description, incident report (incl. referential "why we caught this"), generated conftest.py, URN parsing edge cases, MCP client unit (GraphQL), writeback async path, ML skew check, dashboard data layer

- **Live demo path** — dashboard reads JSON results locally; CI gate workflow posts
  root-cause PR comments; sample artifacts in `examples/sample_generated/`
  include the dbt model + schema + pytest + smart model + PR description
  for `fact_orders` and `stg_orders`

- **Pre-release polish** — 2 final-round fixes:
  - `pyproject.toml`: pinned `testpaths = ["tests"]` so `pytest` no longer
    collects the generated sample tests under `examples/sample_generated/`
    (13 spurious "fixture 'df' not found" errors gone — clean 31 pass / 3 skip)
  - `incident_report.py`: added the missing `referential` branch to the
    "Why we caught this" section (the demo's actual failure mode); previously
    emitted an empty section header. Regression test added in
    `tests/test_codegen_artifacts.py`

## Demo video script (≤3 min)
1. (0:00–0:20) The problem in one sentence: generated pipeline code drifts from reality; failures don't explain themselves.
2. (0:20–1:00) Show `dhqa generate` pulling schema/lineage from DataHub, generating a dbt model + tests. Open the DataHub UI to show the source of truth side by side.
3. (1:00–1:50) Break an upstream table live. Show the generated test failing, then the lineage tracer narrating the walk upstream to the real cause — this is the money shot.
4. (1:50–2:20) Show the write-back landing in DataHub as an assertion/incident, and the GitHub PR getting blocked with the root cause in a comment.
5. (2:20–2:50) Quick dashboard pan.
6. (2:50–3:00) One line: what a real team gets from this on day one.

## Scope cuts if time runs short (in order)
1. Drop Airflow DAG generation — dbt only.
2. Drop the dashboard's history view — keep just catalog health + incident detail.
3. Drop multi-hop tracing polish — a working 2-hop trace is enough to prove the concept.
4. Never cut: write-back to DataHub, and the CI gate. Those are what separate this from a read-only demo.
