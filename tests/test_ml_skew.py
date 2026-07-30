"""Tests for the ML feature-table skew check."""

from __future__ import annotations

import pandas as pd

from dhqa.ml_skew_check import (
    ColumnBaseline,
    SkewConfig,
    baseline_from_df,
    check_skew,
)


def _baseline_df() -> pd.DataFrame:
    return pd.DataFrame({
        "customer_id": ["c1", "c2", "c3"],
        "ltv_score": [100.0, 200.0, 300.0],
        "churn_risk": [0.1, 0.5, 0.9],
    })


def test_check_skew_passes_when_current_matches_baseline():
    base = _baseline_df()
    baselines = baseline_from_df(base)
    result = check_skew(base.copy(), baselines, check_id="skew_0")
    assert result.passed
    assert result.kind == "skew"
    assert "no skew" in result.detail


def test_check_skew_flags_mean_drift():
    base = _baseline_df()
    baselines = baseline_from_df(base)
    # Inflate ltv_score mean well beyond tolerance
    skewed = base.copy()
    skewed["ltv_score"] = skewed["ltv_score"] * 10
    result = check_skew(skewed, baselines, cfg=SkewConfig(relative_tol=0.25))
    assert not result.passed
    assert "ltv_score" in result.detail
    assert "mean" in result.detail


def test_check_skew_flags_out_of_bounds_values():
    base = _baseline_df()
    baselines = baseline_from_df(base)
    skewed = base.copy()
    skewed.loc[0, "ltv_score"] = 10_000.0  # far outside [100, 300]
    result = check_skew(
        skewed, baselines, cfg=SkewConfig(max_out_of_bounds_ratio=0.0)
    )
    assert not result.passed
    assert "outside baseline" in result.detail


def test_check_skew_flags_null_rate_drift():
    base = _baseline_df()
    baselines = baseline_from_df(base)
    skewed = base.copy()
    skewed.loc[0, "ltv_score"] = None
    skewed.loc[1, "ltv_score"] = None
    result = check_skew(skewed, baselines, cfg=SkewConfig(relative_tol=0.1))
    assert not result.passed
    assert "null-rate" in result.detail


def test_check_skew_flags_new_categorical_values():
    base = pd.DataFrame({"customer_id": ["c1", "c2", "c3"]})
    baselines = baseline_from_df(base)
    skewed = pd.DataFrame({"customer_id": ["c1", "c2", "unseen_1", "unseen_2"]})
    result = check_skew(
        skewed, baselines, cfg=SkewConfig(max_new_category_ratio=0.10)
    )
    assert not result.passed
    assert "new categories" in result.detail


def test_check_skew_flags_missing_column():
    base = _baseline_df()
    baselines = baseline_from_df(base)
    result = check_skew(base.drop(columns=["churn_risk"]), baselines)
    assert not result.passed
    assert "missing" in result.detail


def test_baseline_from_df_skips_nonexistent_columns():
    df = _baseline_df()
    bl = baseline_from_df(df, columns=["ltv_score", "does_not_exist"])
    cols = {b.column for b in bl}
    assert cols == {"ltv_score"}


def test_baseline_from_df_numeric_baseline_populated():
    df = _baseline_df()
    bl = {b.column: b for b in baseline_from_df(df)}
    ltv = bl["ltv_score"]
    assert ltv.mean is not None
    assert ltv.min == 100.0
    assert ltv.max == 300.0


def test_baseline_from_df_categorical_baseline_populated():
    df = _baseline_df()
    bl = {b.column: b for b in baseline_from_df(df)}
    cust = bl["customer_id"]
    assert cust.distinct_values is not None
    assert "c1" in cust.distinct_values
