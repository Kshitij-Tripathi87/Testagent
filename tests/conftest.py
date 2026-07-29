"""Shared fixtures for the local (offline) dhqa test path.

Builds an ephemeral fixture directory under ``tmp_path`` using the real
``ingest_local_fixtures`` generator so tests exercise the genuine end-to-end
path: manifest + CSVs -> LocalFixtureStore -> contract -> checks -> trace.
No live DataHub, no network.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ingest_local_fixtures as _gen  # noqa: E402

from dhqa.local_fixture import LocalFixtureStore  # noqa: E402

RAW_URN = _gen.RAW_URN
STG_URN = _gen.STG_URN
FACT_URN = _gen.FACT_URN


@pytest.fixture
def fixture_dir(tmp_path) -> Path:
    """Produce a populated fixtures directory in tmp_path.

    Returns a directory containing:
      - ``manifest.yaml``
      - ``orders.raw_orders.csv``
      - ``orders.stg_orders.csv``
      - ``orders.fact_orders.csv``
      - ``customer_ltv_features.csv``

    The dataset chain has deliberate defects so the same files drive both
    the trace and check tests below.
    """
    out = tmp_path / "fixtures"
    out.mkdir(parents=True, exist_ok=True)

    # Build deterministic data using the generator's exports.
    raw = _gen.generate_raw_orders(100)
    stg = _gen.generate_stg_orders(raw, null_pct=0.10)
    fact = _gen.generate_fact_orders(stg)
    ml = _gen.generate_customer_ltv_features(123)

    raw.to_csv(out / "orders.raw_orders.csv", index=False)
    stg.to_csv(out / "orders.stg_orders.csv", index=False)
    fact.to_csv(out / "orders.fact_orders.csv", index=False)
    ml.to_csv(out / "customer_ltv_features.csv", index=False)

    manifest = _gen.build_manifest()
    import yaml  # Local import — already a hard dependency of dhqa.
    with (out / "manifest.yaml").open("w") as f:
        yaml.safe_dump(manifest, f, default_flow_style=False, sort_keys=False)

    return out


@pytest.fixture
def store(fixture_dir) -> LocalFixtureStore:
    return LocalFixtureStore(fixture_dir)
