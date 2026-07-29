"""Root-causes a failing contract check by walking the DataHub lineage graph.

This is the centerpiece of the demo: instead of stopping at "this check
failed," the tracer walks upstream hop by hop, re-running the same class
of check at each ancestor dataset, until it finds where the anomaly
actually originates (or exhausts max_hops).
"""

from __future__ import annotations

from dataclasses import dataclass

from dhqa.mcp_client import DataHubMCPClient
from dhqa.test_generator import CheckResult


@dataclass
class RootCauseReport:
    failing_check: CheckResult
    origin_urn: str
    hop_distance: int
    trace: list[dict]  # one entry per hop: {urn, checked, passed, detail}

    def summary(self) -> str:
        lines = [
            f"Check '{self.failing_check.check_id}' failed on {self.failing_check.detail}.",
            f"Traced {self.hop_distance} hop(s) upstream to origin: {self.origin_urn}",
            "",
            "Trace:",
        ]
        for hop in self.trace:
            status = "FAIL" if not hop["passed"] else "ok"
            lines.append(f"  [{status}] {hop['urn']} -- {hop['detail']}")
        return "\n".join(lines)


def trace_root_cause(
    client: DataHubMCPClient,
    failing: CheckResult,
    start_urn: str,
    recheck_fn,
    max_hops: int = 5,
) -> RootCauseReport:
    """Walk upstream from `start_urn`, calling `recheck_fn(urn)` -> CheckResult
    at each hop, until a hop passes (meaning the previous hop was the origin)
    or max_hops is exhausted.

    `recheck_fn` is injected so the tracer stays agnostic to *which* kind of
    check it's tracing (not_null, referential, etc.) — same walk, any check.
    """
    trace: list[dict] = []
    current_urn = start_urn
    last_failing_urn = start_urn

    for hop in range(max_hops):
        upstreams = client.get_lineage(current_urn, direction="upstream", max_hops=1)
        if not upstreams:
            break
        next_urn = upstreams[0]  # simplest case: single-parent chain; extend to
                                  # fan-in by recursing over all upstreams if needed
        result = recheck_fn(next_urn)
        trace.append({"urn": next_urn, "checked": result.check_id,
                       "passed": result.passed, "detail": result.detail})
        if result.passed:
            # This ancestor is clean -> the previous (failing) hop is the origin.
            break
        last_failing_urn = next_urn
        current_urn = next_urn

    return RootCauseReport(
        failing_check=failing,
        origin_urn=last_failing_urn,
        hop_distance=len(trace),
        trace=trace,
    )
