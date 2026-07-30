"""Auto-generated conftest.py — wires the dhqa fixtures into the
`df` / `upstream_df_N` fixtures so these tests run standalone.

Generated alongside the test module by `dhqa generate`. Re-run
`dhqa generate --local-fixture <dir> ... -o <out>` to regenerate after
the contract changes.
"""
from pathlib import Path

import pandas as pd
import pytest

DATASET_URN = 'urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD)'
UPSTREAM_URNS = ['urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.raw_orders,PROD)']
FIXTURE_DIR = Path(__file__).resolve().parents[1] / './data/fixtures' if Path('./data/fixtures').is_absolute() else Path('./data/fixtures')


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
