"""Unit tests for MCP client GraphQL response parsing paths.

These tests mock the HTTP session so they run without a live DataHub
instance (the integration tests remain in test_mcp_client.py and are
skipped by default). We test the internal GraphQL response handler
directly so we can validate error paths safely.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from dhqa.mcp_client import DataHubMCPClient, DatasetSnapshot


@pytest.mark.asyncio
async def test_execute_graphql_handles_errors():
    """When the GMS returns HTTP 200 but with a GraphQL errors block, the
    method surfaces the error (not silently unpacking empty data)."""
    client = DataHubMCPClient(gms_url="http://example.com", transport="graphql")

    resp = AsyncMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={
        "errors": [{"message": "Dataset not found"}],
        "data": None,
    })

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=None)

    client._http = MagicMock()
    client._http.post = MagicMock(return_value=ctx)

    result = await client._execute_graphql("query {}", {})
    assert "errors" in result
    assert result["errors"][0]["message"] == "Dataset not found"


@pytest.mark.asyncio
async def test_get_dataset_graphql_returns_snapshot():
    """Happy path: a full-shaped graphql response becomes a DatasetSnapshot."""
    client = DataHubMCPClient(gms_url="http://example.com", transport="graphql")
    client._execute_graphql = AsyncMock(return_value={
        "data": {
            "dataset": {
                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,x.y,PROD)",
                "name": "y",
                "schemaMetadata": {
                    "fields": [
                        {"fieldPath": "id", "type": "string", "nullable": False,
                         "description": "PK"},
                    ]
                },
                "ownership": {"owners": [{"owner": {"urn": "urn:bn:corper:team"}}]},
                "upstreamLineage": {
                    "edges": [{"destination": {"urn": "urn:up"}}],
                },
                "downstreamLineage": {"edges": []},
                "glossaryTerms": {"terms": []},
                "assertions": {"assertions": []},
            }
        }
    })

    snap = await client._get_dataset_graphql("urn:x:y")
    assert isinstance(snap, DatasetSnapshot)
    assert snap.name == "y"
    assert snap.columns[0].name == "id"
    assert not snap.columns[0].nullable
    assert snap.upstream_urns == ["urn:up"]
    assert len(snap.owners) == 1


@pytest.mark.asyncio
async def test_get_lineage_graphql_extracts_edges():
    """Lineage edge parsing extracts destination URNs correctly."""
    client = DataHubMCPClient(gms_url="http://example.com", transport="graphql")
    client._execute_graphql = AsyncMock(return_value={
        "data": {
            "entity": {
                "upstreamLineage": {
                    "edges": [
                        {"destination": {"urn": "a"}},
                        {"destination": {"urn": "b"}},
                    ]
                }
            }
        }
    })
    result = await client._get_lineage_graphql("urn:x", "upstream")
    assert result == ["a", "b"]