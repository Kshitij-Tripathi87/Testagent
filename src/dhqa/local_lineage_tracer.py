"""Local lineage tracer — mirrors the MCP client lineage interface using
local fixture data instead of live DataHub lineage calls.

Reuses the same ``trace_root_cause`` function from lineage_tracer.py
because it already accepts any object with a ``get_lineage()`` method.
"""

from __future__ import annotations

from typing import Callable

import pandas as pd

from dhqa.local_fixture import LocalFixtureStore
from dhqa.test_generator import CheckResult, run_checks


class LocalLineageTracer:
    """Adapter that wraps LocalFixtureStore to provide a get_lineage() method
    compatible with dhqa.lineage_tracer.trace_root_cause().
    """

    def __init__(self, store: LocalFixtureStore):
        self._store = store

    def get_lineage(self, urn: str, direction: str = "upstream", max_hops: int = 5) -> list[str]:
        return self._store.get_lineage(urn, direction=direction, max_hops=max_hops)


def build_recheck_fn(
    store: LocalFixtureStore,
    check_kind: str,
    column: str | None = None,
) -> Callable[[str], CheckResult]:
    """Build a ``recheck_fn`` suitable for passing to ``trace_root_run``.

    At each upstream hop the tracer calls ``recheck_fn(urn)`` which loads
    the dataset's DataFrame and runs the requested class of check against
    it. If ``column`` is set, only checks on that column are considered,
    so re-checking ``not_null(customer_id)`` returns the customer_id
    not-null result — not whatever ``not_null`` happens to be first.
    """

    def _recheck(urn: str) -> CheckResult:
        from dhqa.dataset_model import DatasetContract

        local = store.get_dataset_snapshot(urn)
        contract = DatasetContract.from_local_snapshot(local)
        df = local.df
        upstream_dfs = store.get_upstream_dfs(urn)

        results = run_checks(contract, df, upstream_dfs)
        matches = [
            r for r in results
            if r.kind == check_kind and (column is None or r.column == column)
        ]
        if not matches:
            return CheckResult(
                check_id=f"{check_kind}_recheck",
                kind=check_kind,
                column=column,
                passed=True,
                detail=f"no {check_kind} check applicable at {urn}",
            )
        return matches[0]

    return _recheck