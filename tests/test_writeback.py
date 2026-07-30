"""Tests for the live DataHub write-back path.

The writeback.py module is async (calls into the async DataHubMCPClient).
These tests use Mockito-style fakes so they run without a live DataHub
instance, while exercising the real await path that was previously broken
(sync functions calling async methods).
"""

from __future__ import annotations

import asyncio

from dhqa.lineage_tracer import RootCauseReport
from dhqa.test_generator import CheckResult
from dhqa.writeback import write_check_result, write_incident


class _FakeClient:
    """Records every call so we can assert without hitting the network."""

    def __init__(self) -> None:
        self.assertion_calls: list[dict] = []
        self.incident_calls: list[dict] = []

    async def write_assertion(self, **kwargs) -> None:
        self.assertion_calls.append(kwargs)

    async def write_incident(self, **kwargs) -> None:
        self.incident_calls.append(kwargs)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_write_check_result_awaits_client():
    fake = _FakeClient()
    result = CheckResult(
        check_id="not_null_1", kind="not_null", column="customer_id",
        passed=False, detail="10 null values",
    )
    _run(write_check_result(fake, "urn:li:dataset:(...)", result))

    assert len(fake.assertion_calls) == 1
    call = fake.assertion_calls[0]
    assert call["assertion_id"] == "not_null_1"
    assert call["passed"] is False
    assert call["details"]["kind"] == "not_null"
    assert call["details"]["column"] == "customer_id"


def test_write_incident_awaits_client_and_packages_report():
    fake = _FakeClient()
    failing = CheckResult(
        check_id="referential_4", kind="referential", column="id",
        passed=False, detail="3 orphaned keys",
    )
    report = RootCauseReport(
        failing_check=failing,
        origin_urn="urn:li:dataset:(...fact_orders,PROD)",
        hop_distance=2,
        trace=[{"urn": "x", "checked": "c", "passed": True, "detail": "ok"}],
    )
    _run(write_incident(fake, "urn:li:dataset:(...)", report))

    assert len(fake.incident_calls) == 1
    call = fake.incident_calls[0]
    assert "Contract check" in call["title"]
    rc = call["root_cause"]
    assert rc["hop_distance"] == 2
    assert rc["origin_urn"].endswith("fact_orders,PROD)")
    assert "summary" in rc and rc["trace"]
