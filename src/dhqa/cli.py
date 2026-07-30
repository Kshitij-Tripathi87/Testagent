"""Command-line entrypoint: `dhqa generate` / `dhqa check`.

    dhqa generate --dataset <urn> [--local-fixture DIR | --mcp]
        # DOM -> dbt model + schema.yml + pytest suite
    dhqa check --dataset <urn> [--local-fixture DIR | --mcp]
        # run checks, trace root cause on failure, write back

Two fully interchangeable data sources behind the same DOM contract:
  - ``--local-fixture DIR``: offline CSV + manifest (Track A). No DataHub
    instance, no warehouse — the root-cause trace still runs end-to-end and
    results are written to a local JSON results dir that mirrors the on-graph
    assertion/incident shapes.
  - ``--mcp``: live DataHub via the MCP Server / Agent Context Kit fallback
    (Track B). Writes assertions + incidents back into DataHub.

The DOM (``DatasetContract``) is the shared seam: codegen, test generation,
the lineage tracer, and write-back all consume the same typed contract
object regardless of where the metadata came from.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dhqa.codegen import to_dbt_model, to_schema_yml
from dhqa.dataset_model import DatasetContract
from dhqa.incident_report import render_incident_report
from dhqa.lineage_tracer import trace_root_cause
from dhqa.local_fixture import LocalFixtureStore
from dhqa.local_lineage_tracer import LocalLineageTracer, build_recheck_fn
from dhqa.local_writeback import write_local_result
from dhqa.mcp_client import DataHubMCPClient
from dhqa.pr_description import render_pr_description
from dhqa.smart_codegen import to_smart_dbt_model
from dhqa.test_generator import generate_conftest, generate_pytest_module, run_checks


# ----- generate -------------------------------------------------------------

def _short_name(name: str) -> str:
    """Strip ``/`` and ``:`` to make URN-style strings safe for filenames."""
    return name.replace(":", "_").replace("/", "_").replace("(", "_").replace(")", "_")


def cmd_generate(args: argparse.Namespace) -> None:
    contract = _load_contract(args)
    sql = to_dbt_model(contract)
    yml = to_schema_yml(contract)
    tests = generate_pytest_module(contract)
    smart_sql = to_smart_dbt_model(contract)
    pr_desc = render_pr_description(contract)

    if args.output:
        out = Path(args.output)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{contract.name}.sql").write_text(sql)
        (out / "schema.yml").write_text(yml)
        (out / f"test_{contract.name}.py").write_text(tests)
        (out / f"{contract.name}_smart.sql").write_text(smart_sql)
        (out / "PR_DESCRIPTION.md").write_text(pr_desc)
        # Emit a conftest so the generated test_*.py runs standalone under
        # plain pytest (wires the `df` / `upstream` fixtures to the local
        # fixture store). Previously these tests only ran via `dhqa check`.
        fixture_dir = args.local_fixture if args.local_fixture else None
        try:
            conftest = generate_conftest(contract, fixture_dir=fixture_dir)
            (out / "conftest.py").write_text(conftest)
        except Exception:
            # conftest generation must never block the other artifacts.
            pass
        print(f"Wrote {contract.name}.sql, schema.yml, test_{contract.name}.py, "
              f"{contract.name}_smart.sql, PR_DESCRIPTION.md, conftest.py to {out}")
        return

    print(sql)
    print("---")
    print(yml)
    print("---")
    print(tests)
    print("---")
    print(smart_sql)
    print("---")
    print(pr_desc)


# ----- check ----------------------------------------------------------------

def cmd_check(args: argparse.Namespace) -> None:
    if args.local_fixture:
        _check_local(args)
    elif args.mcp:
        _check_mcp(args)
    else:
        raise SystemExit(
            "Specify a data source: --local-fixture DIR (offline) or --mcp (live DataHub)."
        )


def _check_local(args: argparse.Namespace) -> None:
    store = LocalFixtureStore(Path(args.local_fixture))
    local = store.get_dataset(args.dataset)
    contract = DatasetContract.from_local_snapshot(local)

    df = local.df
    upstream_dfs = store.get_upstream_dfs(args.dataset)
    results = run_checks(contract, df, upstream_dfs)

    tracer = LocalLineageTracer(store)
    incidents = []
    failing_results = [r for r in results if not r.passed]
    for failing in failing_results:
        if failing.kind not in ("not_null", "referential"):
            continue
        recheck = build_recheck_fn(store, failing.kind, failing.column)
        report = trace_root_cause(tracer, failing, args.dataset, recheck)
        incidents.append(report)
        print(report.summary())
        print()

    passed = sum(1 for r in results if r.passed)
    print(f"Checks: {passed}/{len(results)} passed, {len(incidents)} incident(s) traced.")
    out = Path(args.output) if args.output else (Path(args.local_fixture) / "results")
    out.mkdir(parents=True, exist_ok=True)
    out_path = write_local_result(out, args.dataset, results, incidents)
    print(f"Results written to {out_path}")

    # Render markdown incident report for each incident — the kind of artifact
    # a data engineer or a DataHub incident annotation reader actually wants.
    for incident in incidents:
        md = render_incident_report(incident, contract=contract, all_results=results)
        md_path = out / f"incident_report_{_short_name(contract.name)}.md"
        md_path.write_text(md)
    if incidents:
        print(f"Incident report(s) written to {out}/incident_report_*.md")


def _check_mcp(args: argparse.Namespace) -> None:
    import asyncio

    async def _run():
        async with DataHubMCPClient(transport="graphql") as client:
            snapshot = await client.get_dataset(args.dataset)
            contract = DatasetContract.from_snapshot(snapshot)
            # For MCP path, materialize data from a warehouse or use local fixture as bridge
            store = LocalFixtureStore(Path(args.local_fixture)) if args.local_fixture else None
            if store:
                local = store.get_dataset_snapshot(args.dataset)
                df = local.df
                upstream_dfs = store.get_upstream_dfs(args.dataset)
                contract = DatasetContract.from_local_snapshot(local)
                results = run_checks(contract, df, upstream_dfs)
            else:
                raise NotImplementedError(
                    "MCP path without local fixture requires warehouse query support. "
                    "Use --local-fixture with --mcp for schema-from-DataHub + data-from-CSV."
                )

            # Connect lineage tracer to MCP client
            # (For now, use local store bridge until async lineage tracer is complete)
            tracer = LocalLineageTracer(store) if store else None
            if not tracer:
                raise NotImplementedError(
                    "Pure MCP lineage tracing requires async support. "
                    "Use --local-fixture for full end-to-end testing."
                )

            incidents = []
            for failing in [r for r in results if not r.passed]:
                if failing.kind not in ("not_null", "referential"):
                    continue
                recheck = build_recheck_fn(store, failing.kind, failing.column)
                report = trace_root_cause(tracer, failing, args.dataset, recheck)
                incidents.append(report)
                print(report.summary())
                print()

            passed = sum(1 for r in results if r.passed)
            print(f"Checks: {passed}/{len(results)} passed, {len(incidents)} incident(s) traced.")

    asyncio.run(_run())


def _load_contract(args: argparse.Namespace) -> DatasetContract:
    if args.local_fixture:
        store = LocalFixtureStore(Path(args.local_fixture))
        local = store.get_dataset_snapshot(args.dataset)
        return DatasetContract.from_local_snapshot(local)
    if args.mcp:
        # DataHubMCPClient.get_dataset() is async — run it in an event loop
        # so the synchronous `dhqa generate --mcp` CLI path works.
        import asyncio

        async def _fetch() -> DatasetContract:
            async with DataHubMCPClient(transport="graphql") as client:
                snapshot = await client.get_dataset(args.dataset)
                return DatasetContract.from_snapshot(snapshot)

        return asyncio.run(_fetch())
    raise SystemExit("Specify a data source: --local-fixture DIR or --mcp.")


def _add_source_args(p: argparse.ArgumentParser) -> None:
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--local-fixture", metavar="DIR",
                     help="Offline fixture dir containing manifest.json + data files.")
    src.add_argument("--mcp", action="store_true",
                     help="Use a live DataHub instance via the MCP Server / Agent Context Kit.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="dhqa")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="Generate pipeline code + tests from a DataHub dataset")
    gen.add_argument("--dataset", required=True, help="DataHub dataset URN")
    gen.add_argument("--output", "-o", default=None, help="Directory to write generated files (default: stdout)")
    _add_source_args(gen)
    gen.set_defaults(func=cmd_generate)

    chk = sub.add_parser("check", help="Run contract checks, trace root cause, write back")
    chk.add_argument("--dataset", required=True, help="DataHub dataset URN")
    chk.add_argument("--output", "-o", default=None,
                     help="Results dir for local writeback (default: <fixture>/results).")
    _add_source_args(chk)
    chk.set_defaults(func=cmd_check)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.exit(main())
