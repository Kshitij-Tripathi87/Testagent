"""Generates a pytest-based contract test suite from a DatasetContract.

This is what makes generated pipeline code trustworthy rather than
plausible-looking: every test traces back to a constraint declared on the
DOM, which in turn traces back to real DataHub metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pandas as pd

from dhqa.dataset_model import DatasetContract


@dataclass
class CheckResult:
    check_id: str
    kind: str
    column: str | None
    passed: bool
    detail: str


def generate_pytest_module(contract: DatasetContract) -> str:
    """Render a pytest module with one test function per constraint."""
    lines = [
        f'"""Auto-generated contract tests for {contract.urn}."""',
        "import pandas as pd",
        "import pytest",
        "",
        f"DATASET_URN = {contract.urn!r}",
        "",
    ]
    for i, c in enumerate(contract.constraints):
        fn = f"test_{c.kind}_{i}"
        if c.kind == "not_null":
            lines.append(f"def {fn}(df: pd.DataFrame):")
            lines.append(f"    assert df[{c.column!r}].isnull().sum() == 0, "
                          f"'null values found in {c.column}'")
        elif c.kind == "unique":
            col = c.column or "id"
            lines.append(f"def {fn}(df: pd.DataFrame):")
            lines.append(f"    assert df[{col!r}].duplicated().sum() == 0, "
                          f"'duplicate values found in {col}'")
        elif c.kind == "referential":
            join_col = c.column or "id"
            ref_urn = c.ref_urn or "upstream"
            up_var = f"upstream_df_{i}"
            lines.append(f"def {fn}(df: pd.DataFrame, {up_var}: pd.DataFrame):")
            lines.append(f"    # every {join_col} in this dataset must exist upstream")
            lines.append(f"    assert set(df[{join_col!r}]).issubset(set({up_var}[{join_col!r}])), "
                          f"'orphaned keys not found in {ref_urn}'")
        elif c.kind == "freshness":
            lines.append(f"def {fn}(df: pd.DataFrame):")
            ts_col = c.column or "order_ts"
            sla = c.max_staleness_hours or 24
            lines.append(f"    # freshness: {ts_col} must be within {sla}h of now")
            lines.append(f"    _ts = pd.to_datetime(df[{ts_col!r}], errors='coerce')")
            lines.append(f"    _max = _ts.max()")
            lines.append(f"    assert _max is not pd.NaT and "
                          f"(pd.Timestamp.utcnow().tz_localize(None) - _max).total_seconds() <= {sla * 3600}, "
                          "'dataset is stale relative to its declared SLA'")
        lines.append("")
    return "\n".join(lines)


def generate_conftest(
    contract: DatasetContract,
    data_dir: str = "./data",
    fixture_dir: str | None = None,
) -> str:
    """Render a ``conftest.py`` so generated test files run standalone.

    The generated pytest module declares ``df`` / ``upstream_df_N`` fixtures
    via type hints (see ``generate_pytest_module``). Without a conftest, pytest
    errors with ``fixture 'df' not found``. This function emits the fixtures
    that load the dataset CSV (and its upstreams) from a fixture directory so
    ``pytest examples/sample_generated/<model>/test_<model>.py`` works without
    the ``dhqa check`` orchestrator.

    Args:
        contract: the contract the tests were generated from (tells us which
            file/upstream files to load).
        data_dir: relative path used to locate the fixture dir from the
            conftest's own location (default ``./data``).
        fixture_dir: explicit fixture dir (overrides ``data_dir``). When set,
            the conftest reads CSVs from here using the manifest file names.
    """
    base = fixture_dir or f"{data_dir}/fixtures"
    # Build the per-fixture CSV paths using the manifest's declared file names.
    # We don't know the file names from the contract alone, so the generated
    # conftest loads via dhqa's LocalFixtureStore (which reads the manifest),
    # keeping the generated tests decoupled from path conventions.
    urn = contract.urn
    upstreams_expr = ", ".join(f"{u!r}" for u in contract.upstream_urns) or ""
    return f'''"""Auto-generated conftest.py — wires the dhqa fixtures into the
`df` / `upstream_df_N` fixtures so these tests run standalone.

Generated alongside the test module by `dhqa generate`. Re-run
`dhqa generate --local-fixture <dir> ... -o <out>` to regenerate after
the contract changes.
"""
from pathlib import Path

import pandas as pd
import pytest

DATASET_URN = {urn!r}
UPSTREAM_URNS = [{upstreams_expr}]
FIXTURE_DIR = Path(__file__).resolve().parents[1] / {base!r} if Path({base!r}).is_absolute() else Path({base!r})


def _load_df(urn: str) -> pd.DataFrame:
    from dhqa.local_fixture import LocalFixtureStore
    store = LocalFixtureStore(FIXTURE_DIR)
    return store.get_dataset(urn).df


@pytest.fixture
def df() -> pd.DataFrame:
    return _load_df(DATASET_URN)


@pytest.fixture(params=UPSTREAM_URNS)
def upstream(request) -> pd.DataFrame:
    return _load_df(request.param)
'''


def _referential_join_key(constraint, dataset_urn: str) -> str:
    """Pick the join key for a referential check.

    Order of preference:
      1. Explicit ``Constraint.column`` (referential join key is a DOM field).
      2. Conventional `id` column name.
    """
    if getattr(constraint, "column", None):
        return constraint.column
    return "id"


def run_checks(
    contract: DatasetContract,
    df: pd.DataFrame,
    upstream_dfs: dict | None,
    freshness_now: datetime | None = None,
) -> list[CheckResult]:
    """Programmatic (non-pytest) runner used by ``dhqa check`` so results can
    be fed straight into the lineage tracer and write-back without shelling
    out.

    Args:
        contract: ``DatasetContract`` driving the checks.
        df: Current dataset as a pandas DataFrame.
        upstream_dfs: ``{urn: DataFrame}`` map for referential integrity.
        freshness_now: Reference time for freshness checks (defaults to
            ``utcnow()`` so tests are deterministic pass-by-default).
    """
    if upstream_dfs is None:
        upstream_dfs = {}
    if freshness_now is None:
        freshness_now = datetime.now(timezone.utc)

    results: list[CheckResult] = []
    for i, c in enumerate(contract.constraints):
        check_id = f"{c.kind}_{i}"
        if c.kind == "not_null":
            if c.column not in df.columns:
                results.append(CheckResult(
                    check_id, c.kind, c.column, False,
                    f"column {c.column!r} not present in dataset",
                ))
                continue
            nulls = int(df[c.column].isnull().sum())
            results.append(CheckResult(
                check_id, c.kind, c.column, nulls == 0,
                f"{nulls} null values in {c.column}",
            ))
        elif c.kind == "unique":
            col = c.column or "id"
            if col not in df.columns:
                results.append(CheckResult(
                    check_id, c.kind, col, False,
                    f"column {col!r} not present in dataset",
                ))
                continue
            duplicates = int(df[col].duplicated(keep=False).sum())
            results.append(CheckResult(
                check_id, c.kind, col, duplicates == 0,
                f"{duplicates} duplicate values in {col}",
            ))
        elif c.kind == "referential":
            join_key = _referential_join_key(c, contract.urn)
            upstream_df = upstream_dfs.get(c.ref_urn) if c.ref_urn else None
            if upstream_df is None:
                results.append(CheckResult(
                    check_id, c.kind, join_key, False,
                    f"no data available for upstream {c.ref_urn}",
                ))
                continue
            if join_key not in df.columns:
                results.append(CheckResult(
                    check_id, c.kind, join_key, False,
                    f"join column {join_key!r} not present in dataset",
                ))
                continue
            if join_key not in upstream_df.columns:
                results.append(CheckResult(
                    check_id, c.kind, join_key, False,
                    f"join column {join_key!r} not present in upstream {c.ref_urn}",
                ))
                continue
            orphans = set(df[join_key].dropna().unique()) - set(upstream_df[join_key].dropna().unique())
            results.append(CheckResult(
                check_id, c.kind, join_key, len(orphans) == 0,
                f"{len(orphans)} orphaned keys vs {c.ref_urn} (on {join_key})",
            ))
        elif c.kind == "freshness":
            ts_col = c.column or "order_ts"
            sla = c.max_staleness_hours or 24
            if ts_col not in df.columns:
                results.append(CheckResult(
                    check_id, c.kind, ts_col, False,
                    f"timestamp column {ts_col!r} not present",
                ))
                continue
            ts = pd.to_datetime(df[ts_col], errors="coerce")
            valid = ts.dropna()
            if valid.empty:
                results.append(CheckResult(
                    check_id, c.kind, ts_col, False,
                    f"no valid timestamps in {ts_col}",
                ))
                continue
            max_ts = valid.max()
            if max_ts.tzinfo is None:
                max_ts = max_ts.tz_localize("UTC")
            age_hours = (freshness_now - max_ts).total_seconds() / 3600.0
            results.append(CheckResult(
                check_id, c.kind, ts_col, age_hours <= sla,
                f"max {ts_col} is {age_hours:.2f}h old, SLA {sla}h",
            ))
    return results
