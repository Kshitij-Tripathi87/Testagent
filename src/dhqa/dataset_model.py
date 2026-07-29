"""Dataset Object Model (DOM).

One canonical, typed batch of data per DataHub dataset. Codegen and test
generation both read from this instead of re-deriving assumptions from raw
DataHub payloads — the same discipline as a Page Object Model in UI test
automation: one source of truth per entity, everything else references it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dhqa.mcp_client import DatasetSnapshot

if TYPE_CHECKING:
    from dhqa.local_fixture import LocalDatasetSnapshot


@dataclass
class Constraint:
    kind: str  # "not_null" | "unique" | "referential" | "freshness"
    column: str | None = None
    ref_urn: str | None = None  # for referential constraints
    max_staleness_hours: int | None = None  # for freshness constraints


@dataclass
class DatasetContract:
    urn: str
    name: str
    columns: list
    owners: list[str]
    upstream_urns: list[str]
    constraints: list[Constraint] = field(default_factory=list)
    # Optional metadata threading through to richer checks.
    primary_key_columns: list[str] = field(default_factory=list)
    timestamp_column: str | None = None
    max_staleness_hours: int = 24

    @classmethod
    def from_snapshot(cls, snap: DatasetSnapshot) -> "DatasetContract":
        constraints: list[Constraint] = []
        pks: list[str] = []
        ts_col: str | None = None
        seen_pk: set[str] = set()
        for col in snap.columns:
            if not col.nullable:
                constraints.append(Constraint(kind="not_null", column=col.name))
            col_name = (col.name or "").lower()
            # Heuristic: ``id`` is the dataset primary key.  Foreign keys
            # ending in ``_id`` are kept out of the uniqueness constraint
            # so we don't false-alarm on legitimately duplicate
            # foreign-key values.
            if col_name == "id" and col.name not in seen_pk:
                seen_pk.add(col.name)
                pks.append(col.name)
            if ts_col is None and (
                col.type.lower() in ("timestamp", "datetime")
                or col_name.endswith(("_ts", "_at", "_date"))
            ):
                ts_col = col.name

        # Unique constraints for primary-key columns.
        for pk in pks:
            constraints.append(Constraint(kind="unique", column=pk))

        for upstream in snap.upstream_urns:
            # Use ``id`` as the default join key; configurable later.
            constraints.append(
                Constraint(
                    kind="referential",
                    ref_urn=upstream,
                    column="id",
                )
            )
        if ts_col is not None:
            constraints.append(
                Constraint(
                    kind="freshness",
                    column=ts_col,
                    max_staleness_hours=24,
                )
            )

        return cls(
            urn=snap.urn,
            name=snap.name,
            columns=snap.columns,
            owners=snap.owners,
            upstream_urns=snap.upstream_urns,
            constraints=constraints,
            primary_key_columns=pks,
            timestamp_column=ts_col,
            max_staleness_hours=24,
        )

    @classmethod
    def from_local_snapshot(cls, local_snapshot: "LocalDatasetSnapshot") -> "DatasetContract":
        """Build a contract from an offline ``LocalDatasetSnapshot``.

        Converts the local column specs (LocalColumnSpec) into the standard
        ``ColumnSpec``, creates a ``DatasetSnapshot``, then delegates to
        :meth:`from_snapshot` so the offline path derives the same constraint
        set as the live path.
        """
        from dhqa.mcp_client import ColumnSpec

        columns = [
            ColumnSpec(
                name=c.name,
                type=c.type,
                nullable=c.nullable,
                description=c.description,
            )
            for c in local_snapshot.columns
        ]
        snap = DatasetSnapshot(
            urn=local_snapshot.urn,
            name=local_snapshot.name,
            columns=columns,
            owners=local_snapshot.owners,
            upstream_urns=local_snapshot.upstream_urns,
            glossary_terms=local_snapshot.glossary_terms,
        )
        return cls.from_snapshot(snap)
