from __future__ import annotations

from typing import Any

from dhqa.dataset_model import Constraint, DatasetContract
from dhqa.mcp_client import ColumnSpec, DatasetSnapshot


def snapshot_from_metadata(metadata: dict[str, Any]) -> DatasetSnapshot:
    return DatasetSnapshot(
        urn=metadata["urn"],
        name=metadata["name"],
        columns=[
            ColumnSpec(
                name=column["name"],
                type=column.get("type", "string"),
                nullable=column.get("nullable", True),
                description=column.get("description"),
            )
            for column in metadata.get("columns", [])
        ],
        owners=metadata.get("owners", []),
        upstream_urns=metadata.get("upstream_urns", []),
        glossary_terms=metadata.get("glossary_terms", []),
    )


def contract_to_dict(contract: DatasetContract) -> dict[str, Any]:
    return {
        "urn": contract.urn,
        "name": contract.name,
        "owners": contract.owners,
        "upstream_urns": contract.upstream_urns,
        "columns": [
            {
                "name": column.name,
                "type": column.type,
                "nullable": column.nullable,
                "description": column.description,
            }
            for column in contract.columns
        ],
        "constraints": [
            {
                "kind": constraint.kind,
                "column": constraint.column,
                "ref_urn": constraint.ref_urn,
                "max_staleness_hours": constraint.max_staleness_hours,
            }
            for constraint in contract.constraints
        ],
        "primary_key_columns": contract.primary_key_columns,
        "timestamp_column": contract.timestamp_column,
        "max_staleness_hours": contract.max_staleness_hours,
    }


def contract_from_metadata(metadata: dict[str, Any]) -> DatasetContract:
    return DatasetContract.from_snapshot(snapshot_from_metadata(metadata))


def contract_from_dict(payload: dict[str, Any]) -> DatasetContract:
    snapshot = snapshot_from_metadata(payload)
    if "constraints" in payload:
        constraints = [
            Constraint(
                kind=item["kind"],
                column=item.get("column"),
                ref_urn=item.get("ref_urn"),
                max_staleness_hours=item.get("max_staleness_hours"),
            )
            for item in payload.get("constraints", [])
        ]
    else:
        constraints = DatasetContract.from_snapshot(snapshot).constraints
    return DatasetContract(
        urn=snapshot.urn,
        name=snapshot.name,
        columns=snapshot.columns,
        owners=snapshot.owners,
        upstream_urns=snapshot.upstream_urns,
        constraints=constraints,
        primary_key_columns=payload.get("primary_key_columns", []),
        timestamp_column=payload.get("timestamp_column"),
        max_staleness_hours=int(payload.get("max_staleness_hours", 24)),
    )
