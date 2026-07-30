"""Writes dhqa's verdicts back into DataHub.

This is what separates the agent from a read-only demo: the next person
(or agent) to look at this dataset in DataHub sees the assertion result
and, on failure, the traced root cause — not just a green/red build badge
in a CI tool they may never open.
"""

from __future__ import annotations

from dhqa.lineage_tracer import RootCauseReport
from dhqa.mcp_client import DataHubMCPClient
from dhqa.test_generator import CheckResult


async def write_check_result(client: DataHubMCPClient, dataset_urn: str, result: CheckResult) -> None:
    await client.write_assertion(
        urn=dataset_urn,
        assertion_id=result.check_id,
        passed=result.passed,
        details={"kind": result.kind, "column": result.column, "detail": result.detail},
    )


async def write_incident(client: DataHubMCPClient, dataset_urn: str, report: RootCauseReport) -> None:
    await client.write_incident(
        urn=dataset_urn,
        title=f"Contract check '{report.failing_check.check_id}' failed — root cause traced",
        root_cause={
            "origin_urn": report.origin_urn,
            "hop_distance": report.hop_distance,
            "trace": report.trace,
            "summary": report.summary(),
        },
    )
