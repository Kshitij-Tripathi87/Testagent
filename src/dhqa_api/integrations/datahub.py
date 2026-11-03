from __future__ import annotations

from typing import Any

import httpx

_SEARCH = """
query SearchDatasets($input: SearchInput!) {
  search(input: $input) {
    total
    searchResults { entity { urn type } }
  }
}
"""

_DATASET = """
query DatasetByUrn($urn: String!) {
  dataset(urn: $urn) {
    urn
    name
    schemaMetadata {
      fields { fieldPath type nullable description }
    }
    ownership { owners { owner { urn } } }
    upstreamLineage { edges { destination { urn } } }
    glossaryTerms { terms { term { urn name } } }
  }
}
"""


class DataHubGraphQL:
    """Small synchronous GraphQL adapter for DataHub metadata.

    It intentionally handles metadata only. Row-level checks use uploaded CSVs
    until a warehouse execution adapter is implemented.
    """

    def __init__(self, gms_url: str, token: str = "", timeout: float = 20.0):
        self.gms_url = gms_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = httpx.Client(headers=headers, timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def _execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.client.post(
            f"{self.gms_url}/api/graphql",
            json={"query": query, "variables": variables or {}},
        )
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("errors") or []
        if errors:
            message = "; ".join(str(item.get("message", "GraphQL error")) for item in errors)
            raise RuntimeError(f"DataHub GraphQL error: {message}")
        return payload.get("data") or {}

    def test_connection(self) -> dict[str, Any]:
        data = self._execute(
            _SEARCH,
            {"input": {"type": "DATASET", "query": "*", "start": 0, "count": 1}},
        )
        search = data.get("search") or {}
        return {"ok": True, "dataset_count": int(search.get("total") or 0)}

    def list_datasets(self, limit: int = 100) -> list[dict[str, Any]]:
        count = max(1, min(limit, 500))
        data = self._execute(
            _SEARCH,
            {"input": {"type": "DATASET", "query": "*", "start": 0, "count": count}},
        )
        search = data.get("search") or {}
        results = search.get("searchResults") or []
        output: list[dict[str, Any]] = []
        for item in results:
            entity = item.get("entity") or {}
            urn = entity.get("urn")
            if urn:
                output.append(self.get_dataset(urn))
        return output

    def get_dataset(self, urn: str) -> dict[str, Any]:
        data = self._execute(_DATASET, {"urn": urn})
        dataset = data.get("dataset")
        if not dataset:
            raise KeyError(f"Dataset not found in DataHub: {urn}")
        schema = dataset.get("schemaMetadata") or {}
        fields = schema.get("fields") or []
        ownership = (dataset.get("ownership") or {}).get("owners") or []
        lineage = (dataset.get("upstreamLineage") or {}).get("edges") or []
        glossary = (dataset.get("glossaryTerms") or {}).get("terms") or []
        return {
            "urn": dataset["urn"],
            "name": dataset.get("name") or urn.rsplit(",", 2)[-2].split(".")[-1].rstrip(")"),
            "columns": [
                {
                    "name": field.get("fieldPath", ""),
                    "type": field.get("type") or "string",
                    "nullable": bool(field.get("nullable", True)),
                    "description": field.get("description"),
                }
                for field in fields
            ],
            "owners": [
                owner.get("owner", {}).get("urn", "")
                for owner in ownership
                if owner.get("owner", {}).get("urn")
            ],
            "upstream_urns": [
                edge.get("destination", {}).get("urn", "")
                for edge in lineage
                if edge.get("destination", {}).get("urn")
            ],
            "glossary_terms": [
                term.get("term", {}).get("name", "")
                for term in glossary
                if term.get("term", {}).get("name")
            ],
        }

    def __enter__(self) -> "DataHubGraphQL":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
