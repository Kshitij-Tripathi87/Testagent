"""Auto-generated contract tests for urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)."""
import pandas as pd
import pytest

DATASET_URN = 'urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)'

def test_not_null_0(df: pd.DataFrame):
    assert df['id'].isnull().sum() == 0, 'null values found in id'

def test_not_null_1(df: pd.DataFrame):
    assert df['customer_id'].isnull().sum() == 0, 'null values found in customer_id'

def test_not_null_2(df: pd.DataFrame):
    assert df['order_ts'].isnull().sum() == 0, 'null values found in order_ts'

def test_unique_3(df: pd.DataFrame):
    assert df['id'].duplicated().sum() == 0, 'duplicate values found in id'

def test_referential_4(df: pd.DataFrame, upstream_df_4: pd.DataFrame):
    # every id in this dataset must exist upstream
    assert set(df['id']).issubset(set(upstream_df_4['id'])), 'orphaned keys not found in urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD)'

def test_freshness_5(df: pd.DataFrame):
    # freshness: order_ts must be within 24h of now
    _ts = pd.to_datetime(df['order_ts'], errors='coerce')
    _max = _ts.max()
    assert _max is not pd.NaT and (pd.Timestamp.utcnow().tz_localize(None) - _max).total_seconds() <= 86400, 'dataset is stale relative to its declared SLA'
