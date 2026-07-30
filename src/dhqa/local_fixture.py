"""Offline data fixture layer — simulates DataHub reads for local/CI demos.

Provides LocalFixtureStore: a drop-in replacement for DataHubMCPClient
that reads from CSV/Parquet files and a manifest file on disk.

This allows `dhqa check` to run end-to-end without a live DataHub instance.
The interface intentionally mirrors DataHubMCPClient so trace_root_cause()
and other components work without modification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from dhqa.urn_utils import short_name as _short_name


@dataclass
class LocalColumnSpec:
    name: str
    type: str
    nullable: bool = True
    description: str | None = None


@dataclass
class LocalDatasetSnapshot:
    urn: str
    name: str
    file_path: Path
    columns: list[LocalColumnSpec] = field(default_factory=list)
    upstream_urns: list[str] = field(default_factory=list)
    owners: list[str] = field(default_factory=list)
    glossary_terms: list[str] = field(default_factory=list)
    existing_assertions: list[dict] = field(default_factory=list)
    _df: pd.DataFrame | None = field(default=None, repr=False)

    @property
    def df(self) -> pd.DataFrame:
        if self._df is None:
            self._df = pd.read_csv(self.file_path)
        return self._df


class LocalFixtureStore:
    """Reads datasets, lineage, and metadata from a local fixture directory.

    Directory layout expected:

        fixtures/
        ├── manifest.yaml          # urn → file, upstreams, column specs
        ├── orders.raw_orders.csv
        ├── orders.stg_orders.csv
        └── orders.fact_orders.csv

    The manifest maps URNs to file paths, column metadata, and upstream
    lineage edges.
    """

    def __init__(self, base_path: str | Path):
        self.base_path = Path(base_path)
        manifest_path = self.base_path / "manifest.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"Manifest not found at {manifest_path}. "
                "Run 'python scripts/ingest_local_fixtures.py' to generate it."
            )
        with open(manifest_path, "r") as f:
            raw = yaml.safe_load(f) or {}
        self._manifest: dict[str, dict[str, Any]] = raw
        self._datasets: dict[str, LocalDatasetSnapshot] = {}
        self._preload()

    def _preload(self) -> None:
        for urn, entry in self._manifest.items():
            file_path = self.base_path / entry["file"]
            if not file_path.exists():
                raise FileNotFoundError(
                    f"Fixture data file not found at {file_path} for URN {urn}"
                )
            name = entry.get("name", _short_name(urn))
            columns = [
                LocalColumnSpec(
                    name=c["name"],
                    type=c["type"],
                    nullable=c.get("nullable", True),
                    description=c.get("description"),
                )
                for c in entry.get("columns", [])
            ]
            self._datasets[urn] = LocalDatasetSnapshot(
                urn=urn,
                name=entry.get("name", name),
                columns=columns,
                upstream_urns=entry.get("upstreams", []),
                owners=entry.get("owners", []),
                glossary_terms=entry.get("glossary_terms", []),
                existing_assertions=entry.get("assertions", []),
                file_path=file_path,
            )

    def list_urns(self) -> list[str]:
        return list(self._datasets.keys())

    def get_dataset_snapshot(self, urn: str) -> LocalDatasetSnapshot:
        if urn not in self._datasets:
            available = "\n".join(f"  - {u}" for u in self.list_urns())
            raise KeyError(
                f"Dataset URN '{urn}' not found in manifest. "
                f"Available URNs:\n{available}"
            )
        return self._datasets[urn]

    def get_dataset(self, urn: str) -> LocalDatasetSnapshot:
        """Return the :class:`LocalDatasetSnapshot` for ``urn``.

        The snapshot carries metadata (columns, owners, upstreams) and lazy-
        loads row data via its ``.df`` property. Tests and external callers
        consume this for a single canonical access path.
        """
        return self.get_dataset_snapshot(urn)

    def load_dataframe(self, urn: str) -> pd.DataFrame:
        """Return only the row data for ``urn`` as a pandas DataFrame.

        Useful for callers that don't need the surrounding metadata and
        want to skip the snapshot wrapper.
        """
        snapshot = self.get_dataset_snapshot(urn)
        return pd.read_csv(snapshot.file_path)

    def get_lineage(self, urn: str, direction: str = "upstream", max_hops: int = 5) -> list[str]:
        if direction == "downstream":
            return self.get_lineage_downstream(urn, max_hops=max_hops)
        result: list[str] = []
        visited: set[str] = set()
        current = urn
        for _ in range(max_hops):
            if current not in self._datasets:
                break
            upstreams = self._datasets[current].upstream_urns
            if not upstreams:
                break
            next_urn = upstreams[0]
            if next_urn in visited:
                break
            result.append(next_urn)
            visited.add(next_urn)
            current = next_urn
        return result

    def get_lineage_downstream(self, urn: str, max_hops: int = 5) -> list[str]:
        result: list[str] = []
        for candidate_urn, ds in self._datasets.items():
            if urn in ds.upstream_urns:
                result.append(candidate_urn)
        return result[:max_hops]

    def get_upstream_dfs(self, urn: str) -> dict[str, "pd.DataFrame"]:
        """Return {upstream_urn: DataFrame} for all upstream datasets."""
        upstream_dfs: dict[str, pd.DataFrame] = {}
        for upstream_urn in self._datasets[urn].upstream_urns:
            if upstream_urn in self._datasets:
                upstream_dfs[upstream_urn] = self.load_dataframe(upstream_urn)
        return upstream_dfs