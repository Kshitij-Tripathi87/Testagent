from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select

from dhqa.codegen import to_dbt_model, to_schema_yml
from dhqa.dataset_model import DatasetContract
from dhqa.incident_report import render_incident_report
from dhqa.lineage_tracer import RootCauseReport
from dhqa.pr_description import render_pr_description
from dhqa.smart_codegen import to_smart_dbt_model
from dhqa.test_generator import CheckResult, generate_pytest_module, run_checks
from dhqa.urn_utils import safe_name
from dhqa_api.config import settings
from dhqa_api.encryption import decrypt_secret
from dhqa.mcp_client import DataHubMCPClient
from dhqa.writeback import write_check_result, write_incident
from dhqa_api.contracts import contract_from_dict
from dhqa_api.db import SessionLocal
from dhqa_api.models import (
    CheckResultRow,
    DataConnection,
    Dataset,
    DatasetUpload,
    GeneratedArtifact,
    Incident,
    QualityRun,
    utcnow,
)


def _latest_upload(db, dataset_id: str) -> DatasetUpload | None:
    return db.scalar(
        select(DatasetUpload)
        .where(DatasetUpload.dataset_id == dataset_id)
        .order_by(DatasetUpload.created_at.desc())
        .limit(1)
    )


def _load_upstream_data(db, dataset: Dataset, visited: set[str] | None = None) -> dict[str, pd.DataFrame]:
    visited = visited or set()
    result: dict[str, pd.DataFrame] = {}
    metadata = dataset.metadata_json or {}
    for urn in metadata.get("upstream_urns", []):
        upstream = db.scalar(
            select(Dataset).where(
                Dataset.workspace_id == dataset.workspace_id,
                Dataset.urn == urn,
            )
        )
        if not upstream or upstream.id in visited:
            continue
        upload = _latest_upload(db, upstream.id)
        if upload and Path(upload.storage_path).is_file():
            result[urn] = pd.read_csv(upload.storage_path)
    return result


def _recheck_ancestor(
    db,
    ancestor: Dataset,
    kind: str,
    column: str | None,
) -> CheckResult | None:
    upload = _latest_upload(db, ancestor.id)
    if not upload or not Path(upload.storage_path).is_file():
        return None
    contract = contract_from_dict(ancestor.contract_json)
    frame = pd.read_csv(upload.storage_path)
    upstream_frames = _load_upstream_data(db, ancestor)
    results = run_checks(contract, frame, upstream_frames)
    target_kind = "not_null" if kind == "referential" else kind
    return next(
        (
            result for result in results
            if result.kind == target_kind and (column is None or result.column == column)
        ),
        None,
    )


def _trace_failure(
    db,
    dataset: Dataset,
    failing: CheckResult,
    max_hops: int,
) -> dict[str, Any]:
    """Walk every upstream branch; unobserved ancestors are never called clean."""
    queue: list[tuple[Dataset, int, list[str]]] = [(dataset, 0, [dataset.urn])]
    visited = {dataset.id}
    trace: list[dict[str, Any]] = []
    origin = dataset.urn
    deepest_failure = 0

    while queue:
        current, depth, path = queue.pop(0)
        if depth >= max_hops:
            continue
        for upstream_urn in (current.metadata_json or {}).get("upstream_urns", []):
            ancestor = db.scalar(
                select(Dataset).where(
                    Dataset.workspace_id == dataset.workspace_id,
                    Dataset.urn == upstream_urn,
                )
            )
            if not ancestor or ancestor.id in visited:
                trace.append({
                    "urn": upstream_urn,
                    "checked": False,
                    "passed": None,
                    "detail": "Ancestor is not synced into this workspace or was already visited",
                    "path": path + [upstream_urn],
                })
                continue
            visited.add(ancestor.id)
            result = _recheck_ancestor(db, ancestor, failing.kind, failing.column)
            if result is None:
                trace.append({
                    "urn": ancestor.urn,
                    "checked": False,
                    "passed": None,
                    "detail": "No uploaded row data is available for this ancestor",
                    "path": path + [ancestor.urn],
                })
                continue
            trace.append({
                "urn": ancestor.urn,
                "checked": True,
                "passed": result.passed,
                "check_id": result.check_id,
                "detail": result.detail,
                "path": path + [ancestor.urn],
            })
            if not result.passed and depth + 1 >= deepest_failure:
                deepest_failure = depth + 1
                origin = ancestor.urn
                queue.append((ancestor, depth + 1, path + [ancestor.urn]))

    return {
        "failing_check": {
            "check_id": failing.check_id,
            "kind": failing.kind,
            "column": failing.column,
            "detail": failing.detail,
        },
        "origin_urn": origin,
        "hop_distance": deepest_failure,
        "trace": trace,
        "summary": (
            f"{failing.kind} check failed for {failing.column or 'dataset'}. "
            f"Deepest confirmed failing ancestor: {origin}. "
            "Ancestors without uploaded data are reported as unverified."
        ),
    }


def _save_artifact(db, run_id: str, kind: str, file_name: str, content: str) -> None:
    db.add(
        GeneratedArtifact(
            run_id=run_id,
            kind=kind,
            file_name=file_name,
            content=content,
        )
    )



def _writeback_to_datahub(
    connection: DataConnection,
    dataset: Dataset,
    results: list[CheckResult],
    reports: list[RootCauseReport],
) -> None:
    """Publish checks and traced incidents through DataHub's GraphQL API.

    This is deliberately opt-in. The local result is committed first so a
    missing DataHub write permission never discards the quality-run evidence.
    """
    async def _publish() -> None:
        async with DataHubMCPClient(
            gms_url=connection.gms_url,
            token=decrypt_secret(connection.token_ciphertext),
            transport="graphql",
        ) as client:
            for result in results:
                await write_check_result(client, dataset.urn, result)
            for report in reports:
                await write_incident(client, dataset.urn, report)

    asyncio.run(_publish())

def execute_quality_run(run_id: str) -> dict[str, Any]:
    """RQ entrypoint. All work is executed outside the HTTP request process."""
    with SessionLocal() as db:
        run = db.get(QualityRun, run_id)
        if not run:
            raise ValueError(f"Quality run not found: {run_id}")
        if run.status == "completed":
            return {"run_id": run_id, "status": "completed", "idempotent": True}
        run.status = "running"
        run.started_at = utcnow()
        run.error_message = None
        db.commit()

        try:
            dataset = db.get(Dataset, run.dataset_id)
            upload = db.get(DatasetUpload, run.upload_id)
            if not dataset or not upload:
                raise ValueError("Dataset or upload was deleted before execution")
            if not Path(upload.storage_path).is_file():
                raise FileNotFoundError("Uploaded CSV is not available to the worker")

            contract: DatasetContract = contract_from_dict(dataset.contract_json)
            frame = pd.read_csv(upload.storage_path)
            upstream_frames = _load_upstream_data(db, dataset)
            results = run_checks(contract, frame, upstream_frames)

            for result in results:
                db.add(
                    CheckResultRow(
                        run_id=run.id,
                        check_id=result.check_id,
                        kind=result.kind,
                        column_name=result.column,
                        passed=result.passed,
                        detail=result.detail,
                    )
                )

            incidents: list[dict[str, Any]] = []
            root_reports: list[RootCauseReport] = []
            for result in results:
                if result.passed:
                    continue
                report = _trace_failure(db, dataset, result, settings.max_lineage_hops)
                incidents.append(report)
                db.add(
                    Incident(
                        workspace_id=run.workspace_id,
                        dataset_id=dataset.id,
                        run_id=run.id,
                        title=f"{result.kind} check failed: {result.column or dataset.name}",
                        details_json=report,
                    )
                )
                root_report = RootCauseReport(
                    failing_check=result,
                    origin_urn=report["origin_urn"],
                    hop_distance=report["hop_distance"],
                    trace=[hop for hop in report["trace"] if hop.get("checked")],
                )
                root_reports.append(root_report)
                _save_artifact(
                    db,
                    run.id,
                    "incident_report",
                    f"incident_{result.check_id}.md",
                    render_incident_report(root_report, contract=contract, all_results=results),
                )

            artifact_model_name = safe_name(contract.name) or "dataset"
            generated = [
                ("dbt_sql", f"{artifact_model_name}.sql", to_dbt_model(contract)),
                ("dbt_schema", "schema.yml", to_schema_yml(contract)),
                ("smart_dbt_sql", f"{artifact_model_name}_smart.sql", to_smart_dbt_model(contract)),
                ("pytest", f"test_{artifact_model_name}.py", generate_pytest_module(contract)),
                ("pr_description", "PR_DESCRIPTION.md", render_pr_description(contract)),
            ]
            for kind, file_name, content in generated:
                _save_artifact(db, run.id, kind, file_name, content)

            passed = sum(1 for result in results if result.passed)
            failed = len(results) - passed
            run.passed = failed == 0
            run.status = "completed"
            run.summary_json = {
                "total_checks": len(results),
                "passed_checks": passed,
                "failed_checks": failed,
                "incidents": len(incidents),
                "row_count": len(frame),
                "column_count": len(frame.columns),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            run.finished_at = utcnow()
            run.summary_json = {**run.summary_json, "writeback": {"status": "disabled"}}
            db.commit()

            if settings.datahub_writeback_enabled:
                try:
                    connection = db.get(DataConnection, dataset.connection_id)
                    if not connection:
                        raise RuntimeError("Dataset DataHub connection is no longer available")
                    _writeback_to_datahub(connection, dataset, results, root_reports)
                    writeback_result = {"status": "succeeded"}
                except Exception as writeback_exc:
                    writeback_result = {
                        "status": "failed",
                        "error": str(writeback_exc)[:1000],
                    }
                run.summary_json = {**run.summary_json, "writeback": writeback_result}
                db.commit()

            return {"run_id": run.id, "status": run.status, **run.summary_json}
        except Exception as exc:
            db.rollback()
            failed_run = db.get(QualityRun, run_id)
            if failed_run:
                failed_run.status = "failed"
                failed_run.error_message = str(exc)[:2000]
                failed_run.finished_at = utcnow()
                db.commit()
            raise


if __name__ == "__main__":
    from rq import Worker
    from redis import Redis

    connection = Redis.from_url(settings.redis_url)
    Worker(["testagent"], connection=connection).work()
