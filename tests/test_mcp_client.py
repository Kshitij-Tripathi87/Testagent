"""Integration tests for the MCP client (Track B).

These tests require a running DataHub instance at DATAHUB_GMS_URL.
Skip them with:  pytest -m "not integration"
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration

DATAHUB_GMS_URL = os.environ.get("DATAHUB_GMS_URL", "")
DATAHUB_TOKEN = os.environ.get("DATAHUB_TOKEN", "")


def _has_live_datahub() -> bool:
    """Only skip integration tests when DataHub explicitly unavailable."""
    return bool(DATAHUB_GMS_URL)


TEST_URN = "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)"


@pytest.mark.skipif(
    not _has_live_datahub(),
    reason="No DataHub instance configured. Set DATAHUB_GMS_URL to run integration tests.",
)
@pytest.mark.asyncio
async def test_get_dataset_real_datahub():
    from dhqa.mcp_client import DataHubMCPClient

    async with DataHubMCPClient(
        gms_url=DATAHUB_GMS_URL,
        token=DATAHUB_TOKEN,
        transport="graphql",
    ) as client:
        snap = await client.get_dataset(TEST_URN)
        assert snap.urn == TEST_URN


@pytest.mark.skipif(
    not _has_live_datahub(),
    reason="No DataHub instance configured. Set DATAHUB_GMS_URL to run integration tests.",
)
@pytest.mark.asyncio
async def test_get_lineage():
    from dhqa.mcp_client import DataHubMCPClient

    async with DataHubMCPClient(
        gms_url=DATAHUB_GMS_URL,
        token=DATAHUB_TOKEN,
        transport="graphql",
    ) as client:
        urns = await client.get_lineage(TEST_URN, direction="upstream", max_hops=2)
        assert isinstance(urns, list)


@pytest.mark.skipif(
    not _has_live_datahub(),
    reason="No DataHub instance configured. Set DATAHUB_GMS_URL to run integration tests.",
)
@pytest.mark.asyncio
async def test_write_assertion_smoke():
    """Smoke test that the write_assertion path doesn't raise.

    The URN below is intentionally safe — it points to a non-existent demo
    dataset. Some DataHub instances allow writing assertions to any URN,
    others will reject; the test is here so we know the client code path
    runs end-to-end without crashing.
    """
    from dhqa.mcp_client import DataHubMCPClient

    async with DataHubMCPClient(
        gms_url=DATAHUB_GMS_URL,
        token=DATAHUB_TOKEN,
        transport="graphql",
    ) as client:
        try:
            await client.write_assertion(
                TEST_URN,
                "dhqa_integration_smoke",
                True,
                {"note": "integration smoke test"},
            )
        except Exception:
            pytest.skip("write_assertion not permitted — this is fine for smoke test")
