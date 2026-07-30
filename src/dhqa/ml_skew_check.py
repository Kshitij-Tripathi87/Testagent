"""ML feature-table skew check — compares a feature dataset's current
distribution against a reference baseline to detect train / serving skew.

This satisfies the README's "Production ML Agents" extension claim, which
previously referenced an ML feature table (`customer_ltv_features`) without
any actual ML-aware check: the table existed, but nothing flagged drift.

The check is a simple per-column statistical gate:

  - For numeric columns: mean and the fraction of nulls must stay within
    ``relative_tol`` of the reference; values outside the reference
    [min, max] range count as out-of-bounds and surface in the detail.
  - For non-numeric columns: the null-rate and the set of distinct values
    must not drift beyond ``max_new_category_ratio``.

The check plugs into the existing ``CheckResult`` shape so the lineage
tracer, writeback, and dashboard all consume it without modification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from dhqa.test_generator import CheckResult


@dataclass
class ColumnBaseline:
    """Reference statistics for a single column.

    Build one of these per column you want to gate (typically every column
    in a feature table) and pass the list to :func:`check_skew`.
    """

    column: str
    mean: float | None = None
    min: float | None = None
    max: float | None = None
    null_rate: float = 0.0
    distinct_values: set[str] | None = None


@dataclass
class SkewConfig:
    relative_tol: float = 0.25
    # Max relative change in mean before the column is flagged (25%).
    max_out_of_bounds_ratio: float = 0.05
    # Max fraction of rows outside the reference [min, max] band.
    max_new_category_ratio: float = 0.10
    # Max fraction of categorical values not seen in the reference set.


def check_skew(
    df: pd.DataFrame,
    baselines: list[ColumnBaseline],
    cfg: SkewConfig | None = None,
    check_id: str = "skew_0",
) -> CheckResult:
    """Compare ``df`` against a list of column baselines; return one CheckResult.

    A single aggregate result is returned (rather than per-column) so the
    check slots into the existing constraint-driven flow. The ``detail``
    string enumerates every column that drifted so the on-call engineer can
    act immediately.
    """
    cfg = cfg or SkewConfig()
    failures: list[str] = []

    for bl in baselines:
        if bl.column not in df.columns:
            failures.append(f"{bl.column!r} missing from current data")
            continue
        col = df[bl.column]
        null_rate = float(col.isnull().mean())
        if abs(null_rate - bl.null_rate) > cfg.relative_tol:
            failures.append(
                f"{bl.column} null-rate {null_rate:.2f} vs baseline {bl.null_rate:.2f}"
            )

        if bl.mean is not None and pd.api.types.is_numeric_dtype(col):
            cur_mean = float(col.dropna().mean()) if col.dropna().size else 0.0
            denom = abs(bl.mean) or 1.0
            rel_drift = abs(cur_mean - bl.mean) / denom
            if rel_drift > cfg.relative_tol:
                failures.append(
                    f"{bl.column} mean {cur_mean:.3f} vs baseline {bl.mean:.3f} "
                    f"(drift {rel_drift:.0%})"
                )
            if bl.min is not None and bl.max is not None:
                oob = float(((col < bl.min) | (col > bl.max)).mean())
                if oob > cfg.max_out_of_bounds_ratio:
                    failures.append(
                        f"{bl.column} {oob:.1%} of values outside "
                        f"baseline [{bl.min}, {bl.max}]"
                    )

        if bl.distinct_values is not None and not pd.api.types.is_numeric_dtype(col):
            cur_values = set(col.dropna().astype(str).unique())
            new_values = cur_values - bl.distinct_values
            if col.dropna().size:
                ratio = len(new_values) / max(len(cur_values), 1)
                if ratio > cfg.max_new_category_ratio:
                    failures.append(
                        f"{bl.column} {ratio:.0%} new categories not in baseline"
                    )

    passed = not failures
    detail = "; ".join(failures) if failures else "no skew detected within tolerances"
    return CheckResult(
        check_id=check_id,
        kind="skew",
        column=None,
        passed=passed,
        detail=detail,
    )


def baseline_from_df(
    df: pd.DataFrame, columns: list[str] | None = None
) -> list[ColumnBaseline]:
    """Build a list of ColumnBaselines from a reference DataFrame.

    Convenience: snapshot a known-good period of your feature table and let
    this derive the [min, max, mean, null_rate, distinct_values] for each
    column. Persist the result and feed it back to :func:`check_skew` at
    each run.
    """
    columns = columns or list(df.columns)
    out: list[ColumnBaseline] = []
    for c in columns:
        if c not in df.columns:
            continue
        col = df[c]
        bl = ColumnBaseline(
            column=c,
            null_rate=float(col.isnull().mean()),
        )
        if pd.api.types.is_numeric_dtype(col):
            clean = col.dropna()
            if clean.size:
                bl.mean = float(clean.mean())
                bl.min = float(clean.min())
                bl.max = float(clean.max())
        else:
            bl.distinct_values = set(col.dropna().astype(str).unique())
        out.append(bl)
    return out
