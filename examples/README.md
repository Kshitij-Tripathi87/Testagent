# Sample generated outputs

Everything in `sample_generated/` was produced by running the actual `dhqa`
codegen and test-generator code in this repo against the offline fixture
(`data/fixtures/`) — not hand-written.

Regenerate them with:

```bash
python scripts/ingest_local_fixtures.py
dhqa generate --dataset "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)" \
              --local-fixture ./data/fixtures -o ./examples/sample_generated
dhqa check  --dataset "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)" \
            --local-fixture ./data/fixtures -o ./examples/sample_generated/results
```

- `fact_orders.sql` — generated dbt model
- `schema.yml` — generated dbt schema/test config
- `test_fact_orders.py` — generated pytest contract test suite
- `incident_report.md` — a humanized rendering of the traced root cause(s)
  from a real `dhqa check` run (both the propagated-null and the local-orphan
  incidents), as written back to DataHub in the live path
- `results/<urn>/<timestamp>.json` — the on-graph-shape payload the agent
  writes; mirrors what `writeback.py` pushes to DataHub (TSV: assertion +
  incident) in the live path. Excluded from git; regenerate locally.

Judges can review `fact_orders.sql`, `schema.yml`, `test_fact_orders.py`,
and `incident_report.md` directly without running the project to evaluate
output quality.
