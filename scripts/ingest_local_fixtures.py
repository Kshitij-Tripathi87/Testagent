"""Generates local fixture data (CSV files + manifest.yaml) for the
orders.raw_orders → orders.stg_orders → orders.fact_orders sample dataset.

This script creates a deliberately broken dataset chain — stg_orders has
null customer_id values (10%), which causes fact_orders to have orphaned
keys — so that dhqa check's root-cause trace is demonstrated end-to-end.

Run this once:
    python scripts/ingest_local_fixtures.py

Output: data/fixtures/ with CSV files + manifest.yaml
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pandas as pd
import yaml

# ── fixture configuration ──────────────────────────────────────────────

RAW_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.raw_orders,PROD)"
STG_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.stg_orders,PROD)"
FACT_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)"
ML_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.customer_ltv_features,PROD)"

OUTPUT_DIR = Path("data/fixtures")

random.seed(42)


def generate_raw_orders(n: int = 100) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [f"ord_{i:04d}" for i in range(n)],
            "customer_id": [f"cust_{i % 50:04d}" for i in range(n)],
            "amount": [round(random.uniform(10, 500), 2) for _ in range(n)],
            "order_ts": pd.date_range("2025-01-01", periods=n, freq="h"),
        }
    )


def generate_stg_orders(raw: pd.DataFrame, null_pct: float = 0.10) -> pd.DataFrame:
    df = raw.copy()
    df["cleaned_amount"] = df["amount"].apply(lambda x: max(x, 0))
    n_nulls = int(len(df) * null_pct)
    null_indices = random.sample(range(len(df)), n_nulls)
    df.loc[null_indices, "customer_id"] = None
    return df


def generate_fact_orders(stg: pd.DataFrame) -> pd.DataFrame:
    fact = stg[["id", "customer_id", "order_ts"]].copy()
    fact["amount"] = stg["cleaned_amount"].values
    fact["tax"] = (fact["amount"] * 0.08).round(2)
    # Inject 3 fabricated orphaned keys (not present in any upstream) — these
    # are the "orphan" rows the referential integrity check catches.
    extra_rows = []
    for i in range(3):
        extra_rows.append({
            "id": f"ord_orphan_{i:03d}",
            "customer_id": f"cust_{i:04d}",
            "order_ts": pd.Timestamp("2025-06-01"),
            "amount": 99.99,
            "tax": 8.00,
        })
    fact = pd.concat([fact, pd.DataFrame(extra_rows)], ignore_index=True)
    return fact


def generate_customer_ltv_features(seed: int = 123) -> pd.DataFrame:
    random.seed(seed)
    n = 50
    return pd.DataFrame(
        {
            "customer_id": [f"cust_{i:04d}" for i in range(n)],
            "ltv_score": [round(random.uniform(100, 5000), 2) for _ in range(n)],
            "churn_risk": [round(random.uniform(0, 1), 2) for _ in range(n)],
        }
    )


def build_manifest() -> dict:
    raw_cols = [
        {"name": "id", "type": "string", "nullable": False},
        {"name": "customer_id", "type": "string", "nullable": False},
        {"name": "amount", "type": "float", "nullable": True},
        {"name": "order_ts", "type": "timestamp", "nullable": False},
    ]
    stg_cols = [
        {"name": "id", "type": "string", "nullable": False},
        {"name": "customer_id", "type": "string", "nullable": False},  # declared NOT NULL but data has nulls
        {"name": "amount", "type": "float", "nullable": False},
        {"name": "order_ts", "type": "timestamp", "nullable": False},
        {"name": "cleaned_amount", "type": "float", "nullable": True},
    ]
    fact_cols = [
        {"name": "id", "type": "string", "nullable": False},
        {"name": "customer_id", "type": "string", "nullable": False},
        {"name": "amount", "type": "float", "nullable": True},
        {"name": "order_ts", "type": "timestamp", "nullable": False},
        {"name": "tax", "type": "float", "nullable": True},
    ]
    ml_cols = [
        {"name": "customer_id", "type": "string", "nullable": False},
        {"name": "ltv_score", "type": "float", "nullable": True},
        {"name": "churn_risk", "type": "float", "nullable": True},
    ]

    return {
        RAW_URN: {
            "name": "raw_orders",
            "file": "orders.raw_orders.csv",
            "upstreams": [],
            "columns": raw_cols,
            "owners": ["urn:oli:corpuser:data-team"],
            "glossary_terms": ["RawData"],
            "assertions": [],
        },
        STG_URN: {
            "name": "stg_orders",
            "file": "orders.stg_orders.csv",
            "upstreams": [RAW_URN],
            "columns": stg_cols,
            "owners": ["urn:oli:corpuser:data-team"],
            "glossary_terms": ["Staging"],
            "assertions": [],
        },
        FACT_URN: {
            "name": "fact_orders",
            "file": "orders.fact_orders.csv",
            "upstreams": [STG_URN],
            "columns": fact_cols,
            "owners": ["urn:oli:corpuser:analytics-team"],
            "glossary_terms": ["FactTable", "Merchant"],
            "assertions": [],
        },
        ML_URN: {
            "name": "customer_ltv_features",
            "file": "customer_ltv_features.csv",
            "upstreams": [FACT_URN],
            "columns": ml_cols,
            "owners": ["urn:oli:corpuser:ml-team"],
            "glossary_terms": ["MLFeatureTable"],
            "assertions": [],
        },
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Generating raw_orders (100 rows, clean)...")
    raw = generate_raw_orders(100)
    raw.to_csv(OUTPUT_DIR / "orders.raw_orders.csv", index=False)

    print("Generating stg_orders (90 rows, 10% null customer_id for root-cause demo)...")
    stg = generate_stg_orders(raw, null_pct=0.10)
    stg.to_csv(OUTPUT_DIR / "orders.stg_orders.csv", index=False)

    print(f"Generating fact_orders ({len(stg[stg['customer_id'].notna()])} rows + 5 orphaned keys)...")
    fact = generate_fact_orders(stg)
    fact.to_csv(OUTPUT_DIR / "orders.fact_orders.csv", index=False)

    print("Generating customer_ltv_features (50 rows, ML feature table)...")
    ml = generate_customer_ltv_features(123)
    ml.to_csv(OUTPUT_DIR / "customer_ltv_features.csv", index=False)

    manifest = build_manifest()
    manifest_path = OUTPUT_DIR / "manifest.yaml"
    with open(manifest_path, "w") as f:
        yaml.safe_dump(manifest, f, default_flow_style=False, sort_keys=False)

    print(f"\nFixture written to {OUTPUT_DIR.resolve()}")
    print(f"  - {OUTPUT_DIR / 'manifest.yaml'}")
    for csv_file in sorted(OUTPUT_DIR.glob("*.csv")):
        print(f"  - {csv_file}")

    print(f"\nDataset chain: raw_orders -> stg_orders -> fact_orders -> customer_ltv_features")
    print(f"Deliberately broken: stg_orders has {int(len(stg) * 0.10)} null customer_id values")
    print(f"                   fact_orders has {100 - len(fact)} fewer rows (orphaned keys)")
    return 0


if __name__ == "__main__":
    sys.exit(main())