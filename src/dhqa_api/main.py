from __future__ import annotations

import hashlib
import io
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from redis import Redis
from rq import Queue
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from dhqa.dataset_model import DatasetContract
from dhqa.mcp_client import ColumnSpec, DatasetSnapshot
from dhqa_api.config import settings
from dhqa_api.contracts import contract_from_dict, contract_to_dict
from dhqa_api.db import Base, engine, get_db
from dhqa_api.encryption import decrypt_secret, encrypt_secret
from dhqa_api.integrations.datahub import DataHubGraphQL
from dhqa_api.models import (
    AuditEvent,
    CheckResultRow,
    ContractVersion,
    DataConnection,
    Dataset,
    DatasetUpload,
    GeneratedArtifact,
    Incident,
    QualityRun,
    Workspace,
    WorkspaceMember,
    utcnow,
)
from dhqa_api.schemas import (
    ConnectionCreate,
    ContractUpdate,
    IncidentUpdate,
    RunCreate,
    WorkspaceCreate,
)
from dhqa_api.security import current_identity

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Workspace API for DataHub-grounded data quality engineering.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Dev-User"],
)


@app.on_event("startup")
def startup() -> None:
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    if settings.auto_create_schema and settings.app_env == "development":
        Base.metadata.create_all(bind=engine)


def identity_subject(identity: dict[str, Any]) -> str:
    return str(identity["sub"])


def record_audit(
    db: Session,
    workspace_id: str,
    identity: dict[str, Any],
    action: str,
    target_type: str,
    target_id: str,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_subject=identity_subject(identity),
            action=action,
            target_type=target_type,
            target_id=target_id,
            details_json=details or {},
        )
    )


def ensure_workspace(db: Session, workspace_id: str, identity: dict[str, Any]) -> Workspace:
    subject = identity_subject(identity)
    member = db.scalar(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.subject == subject,
        )
    )
    if not member:
        raise HTTPException(status_code=404, detail="Workspace not found")
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


def _connection(db: Session, workspace_id: str, connection_id: str) -> DataConnection:
    connection = db.scalar(
        select(DataConnection).where(
            DataConnection.id == connection_id,
            DataConnection.workspace_id == workspace_id,
        )
    )
    if not connection:
        raise HTTPException(status_code=404, detail="DataHub connection not found")
    return connection


def _dataset(db: Session, workspace_id: str, dataset_id: str) -> Dataset:
    dataset = db.scalar(
        select(Dataset).where(
            Dataset.id == dataset_id,
            Dataset.workspace_id == workspace_id,
        )
    )
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset


def _validate_gms_url(gms_url: str) -> None:
    parsed = urlsplit(gms_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(status_code=422, detail="A valid HTTP(S) DataHub URL is required")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail="Do not embed credentials in the DataHub URL")
    if settings.app_env != "development" and parsed.hostname.lower() not in settings.allowed_datahub_hosts:
        raise HTTPException(
            status_code=422,
            detail="This DataHub host is not allowlisted. Configure DATAHUB_ALLOWED_HOSTS for the beta deployment.",
        )


def _connection_client(connection: DataConnection) -> DataHubGraphQL:
    return DataHubGraphQL(connection.gms_url, decrypt_secret(connection.token_ciphertext))


def _contract_response(dataset: Dataset) -> dict[str, Any]:
    return {
        "id": dataset.id,
        "urn": dataset.urn,
        "name": dataset.name,
        "metadata": dataset.metadata_json,
        "contract": dataset.contract_json,
        "contract_version": dataset.contract_version,
        "synced_at": dataset.synced_at.isoformat() if dataset.synced_at else None,
    }


def _run_response(db: Session, run: QualityRun, include_details: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "upload_id": run.upload_id,
        "status": run.status,
        "passed": run.passed,
        "summary": run.summary_json,
        "error_message": run.error_message,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
    if include_details:
        checks = db.scalars(
            select(CheckResultRow).where(CheckResultRow.run_id == run.id)
        ).all()
        incidents = db.scalars(select(Incident).where(Incident.run_id == run.id)).all()
        artifacts = db.scalars(
            select(GeneratedArtifact).where(GeneratedArtifact.run_id == run.id)
        ).all()
        payload["checks"] = [
            {
                "id": row.id,
                "check_id": row.check_id,
                "kind": row.kind,
                "column": row.column_name,
                "passed": row.passed,
                "detail": row.detail,
            }
            for row in checks
        ]
        payload["incidents"] = [
            {
                "id": incident.id,
                "title": incident.title,
                "status": incident.status,
                "details": incident.details_json,
                "created_at": incident.created_at.isoformat() if incident.created_at else None,
            }
            for incident in incidents
        ]
        payload["artifacts"] = [
            {"id": item.id, "kind": item.kind, "file_name": item.file_name}
            for item in artifacts
        ]
    return payload


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "testagent-api"}


@app.get("/readyz")
def readyz(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(select(1))
        Redis.from_url(settings.redis_url, socket_connect_timeout=2).ping()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database or queue is unavailable") from exc
    return {"status": "ready"}


@app.post("/api/v1/workspaces", status_code=status.HTTP_201_CREATED)
def create_workspace(
    body: WorkspaceCreate,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    subject = identity_subject(identity)
    workspace = Workspace(name=body.name.strip(), created_by=subject)
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            subject=subject,
            email=identity.get("email"),
            role="owner",
        )
    )
    record_audit(db, workspace.id, identity, "workspace.created", "workspace", workspace.id)
    db.commit()
    return {"id": workspace.id, "name": workspace.name, "role": "owner"}


@app.get("/api/v1/workspaces")
def list_workspaces(
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    subject = identity_subject(identity)
    rows = db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(WorkspaceMember.subject == subject)
        .order_by(Workspace.created_at.desc())
    ).all()
    return [
        {"id": workspace.id, "name": workspace.name, "role": role}
        for workspace, role in rows
    ]


@app.post("/api/v1/workspaces/{workspace_id}/connections", status_code=status.HTTP_201_CREATED)
def create_connection(
    workspace_id: str,
    body: ConnectionCreate,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_workspace(db, workspace_id, identity)
    _validate_gms_url(body.gms_url)
    try:
        encrypted_token = encrypt_secret(body.token)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    connection = DataConnection(
        workspace_id=workspace_id,
        name=body.name.strip(),
        gms_url=body.gms_url,
        token_ciphertext=encrypted_token,
    )
    db.add(connection)
    db.flush()
    record_audit(
        db, workspace_id, identity, "connection.created", "data_connection", connection.id,
        {"name": connection.name, "gms_host": urlsplit(connection.gms_url).hostname},
    )
    db.commit()
    return {
        "id": connection.id,
        "name": connection.name,
        "gms_url": connection.gms_url,
        "status": connection.status,
    }


@app.get("/api/v1/workspaces/{workspace_id}/connections")
def list_connections(
    workspace_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    ensure_workspace(db, workspace_id, identity)
    rows = db.scalars(
        select(DataConnection)
        .where(DataConnection.workspace_id == workspace_id)
        .order_by(DataConnection.created_at.desc())
    ).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "gms_url": row.gms_url,
            "status": row.status,
            "last_tested_at": row.last_tested_at.isoformat() if row.last_tested_at else None,
        }
        for row in rows
    ]


@app.post("/api/v1/workspaces/{workspace_id}/connections/{connection_id}/test")
def test_connection(
    workspace_id: str,
    connection_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_workspace(db, workspace_id, identity)
    connection = _connection(db, workspace_id, connection_id)
    try:
        with _connection_client(connection) as client:
            result = client.test_connection()
        connection.status = "connected"
        connection.last_tested_at = utcnow()
        db.commit()
        return result
    except Exception as exc:
        connection.status = "error"
        connection.last_tested_at = utcnow()
        db.commit()
        raise HTTPException(status_code=502, detail=f"DataHub connection failed: {exc}") from exc


@app.post("/api/v1/workspaces/{workspace_id}/connections/{connection_id}/sync")
def sync_catalog(
    workspace_id: str,
    connection_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_workspace(db, workspace_id, identity)
    connection = _connection(db, workspace_id, connection_id)
    from dhqa_api.contracts import contract_to_dict
    from dhqa.dataset_model import DatasetContract

    try:
        with _connection_client(connection) as client:
            metadata_items = client.list_datasets(limit=100)
        synced = 0
        for metadata in metadata_items:
            row = db.scalar(
                select(Dataset).where(
                    Dataset.workspace_id == workspace_id,
                    Dataset.urn == metadata["urn"],
                )
            )
            contract = DatasetContract.from_snapshot(
                DatasetSnapshot(
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
            )
            if row is None:
                row = Dataset(
                    workspace_id=workspace_id,
                    connection_id=connection_id,
                    urn=metadata["urn"],
                    name=metadata["name"],
                    metadata_json=metadata,
                    contract_json=contract_to_dict(contract),
                    contract_version=1,
                )
                db.add(row)
                db.flush()
                db.add(
                    ContractVersion(
                        dataset_id=row.id,
                        version=1,
                        contract_json=row.contract_json,
                        created_by=identity_subject(identity),
                    )
                )
            else:
                row.connection_id = connection_id
                row.name = metadata["name"]
                row.metadata_json = metadata
                # Version 1 is still the auto-inferred contract. Preserve edited
                # contracts (version 2+) and refresh the inferred baseline on sync.
                if row.contract_version <= 1:
                    row.contract_json = contract_to_dict(contract)
                    baseline = db.scalar(
                        select(ContractVersion).where(
                            ContractVersion.dataset_id == row.id,
                            ContractVersion.version == 1,
                        )
                    )
                    if baseline:
                        baseline.contract_json = row.contract_json
            row.synced_at = utcnow()
            synced += 1
        connection.status = "connected"
        connection.last_tested_at = utcnow()
        record_audit(
            db, workspace_id, identity, "catalog.synced", "data_connection", connection.id,
            {"dataset_count": synced},
        )
        db.commit()
        return {"synced": synced, "datasets": len(metadata_items)}
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Catalog sync failed: {exc}") from exc


@app.get("/api/v1/workspaces/{workspace_id}/datasets")
def list_datasets(
    workspace_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    ensure_workspace(db, workspace_id, identity)
    rows = db.scalars(
        select(Dataset).where(Dataset.workspace_id == workspace_id).order_by(Dataset.name)
    ).all()
    return [
        {
            "id": row.id,
            "urn": row.urn,
            "name": row.name,
            "column_count": len((row.metadata_json or {}).get("columns", [])),
            "upstream_count": len((row.metadata_json or {}).get("upstream_urns", [])),
            "contract_version": row.contract_version,
            "synced_at": row.synced_at.isoformat() if row.synced_at else None,
        }
        for row in rows
    ]


@app.get("/api/v1/workspaces/{workspace_id}/datasets/{dataset_id}")
def get_dataset(
    workspace_id: str,
    dataset_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_workspace(db, workspace_id, identity)
    dataset = _dataset(db, workspace_id, dataset_id)
    uploads = db.scalars(
        select(DatasetUpload)
        .where(DatasetUpload.dataset_id == dataset_id)
        .order_by(DatasetUpload.created_at.desc())
        .limit(10)
    ).all()
    payload = _contract_response(dataset)
    payload["uploads"] = [
        {
            "id": upload.id,
            "file_name": upload.file_name,
            "size_bytes": upload.size_bytes,
            "row_count": upload.row_count,
            "columns": upload.columns_json,
            "created_at": upload.created_at.isoformat() if upload.created_at else None,
        }
        for upload in uploads
    ]
    return payload


@app.put("/api/v1/workspaces/{workspace_id}/datasets/{dataset_id}/contract")
def update_contract(
    workspace_id: str,
    dataset_id: str,
    body: ContractUpdate,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_workspace(db, workspace_id, identity)
    dataset = _dataset(db, workspace_id, dataset_id)
    columns = {item["name"] for item in (dataset.metadata_json or {}).get("columns", [])}
    upstreams = set((dataset.metadata_json or {}).get("upstream_urns", []))
    normalized = []
    for constraint in body.constraints:
        if constraint.column and constraint.column not in columns:
            raise HTTPException(status_code=422, detail=f"Unknown dataset column: {constraint.column}")
        if constraint.kind == "referential" and constraint.ref_urn not in upstreams:
            raise HTTPException(status_code=422, detail="Referential constraint must target a declared upstream dataset")
        if constraint.kind == "freshness" and not constraint.max_staleness_hours:
            raise HTTPException(status_code=422, detail="Freshness constraints require max_staleness_hours")
        normalized.append(constraint.model_dump())
    payload = {
        **(dataset.contract_json or {}),
        "urn": dataset.urn,
        "name": dataset.name,
        "owners": (dataset.metadata_json or {}).get("owners", []),
        "upstream_urns": list(upstreams),
        "columns": (dataset.metadata_json or {}).get("columns", []),
        "constraints": normalized,
        "max_staleness_hours": body.max_staleness_hours,
    }
    dataset.contract_version += 1
    dataset.contract_json = payload
    db.add(
        ContractVersion(
            dataset_id=dataset.id,
            version=dataset.contract_version,
            contract_json=payload,
            created_by=identity_subject(identity),
        )
    )
    record_audit(
        db, workspace_id, identity, "contract.updated", "dataset", dataset.id,
        {"version": dataset.contract_version, "constraint_count": len(normalized)},
    )
    db.commit()
    return {"dataset_id": dataset.id, "version": dataset.contract_version, "contract": payload}


@app.post("/api/v1/workspaces/{workspace_id}/datasets/{dataset_id}/uploads", status_code=status.HTTP_201_CREATED)
async def upload_dataset_csv(
    workspace_id: str,
    dataset_id: str,
    file: UploadFile = File(...),
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_workspace(db, workspace_id, identity)
    dataset = _dataset(db, workspace_id, dataset_id)
    original_name = Path(file.filename or "dataset.csv").name
    if Path(original_name).suffix.lower() != ".csv":
        raise HTTPException(status_code=415, detail="Only CSV uploads are supported in the beta")
    content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(content) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File exceeds {settings.max_upload_mb} MB")
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    try:
        preview = pd.read_csv(io.BytesIO(content), nrows=settings.max_csv_rows + 1)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not parse CSV: {exc}") from exc
    if len(preview) > settings.max_csv_rows:
        raise HTTPException(status_code=413, detail=f"CSV exceeds {settings.max_csv_rows} rows")
    expected = {column["name"] for column in (dataset.metadata_json or {}).get("columns", [])}
    actual = set(str(column) for column in preview.columns)
    missing = sorted(expected - actual)
    if missing:
        raise HTTPException(status_code=422, detail={"message": "CSV is missing catalog columns", "missing": missing})
    upload_id = str(uuid.uuid4())
    target_dir = settings.upload_dir / workspace_id / dataset_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{upload_id}.csv"
    target_path.write_bytes(content)
    upload = DatasetUpload(
        id=upload_id,
        dataset_id=dataset_id,
        file_name=original_name[:255],
        storage_path=str(target_path),
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        row_count=len(preview),
        columns_json=[str(column) for column in preview.columns],
        created_by=identity_subject(identity),
    )
    db.add(upload)
    record_audit(
        db, workspace_id, identity, "dataset.uploaded", "dataset", dataset.id,
        {"upload_id": upload.id, "size_bytes": upload.size_bytes, "row_count": upload.row_count},
    )
    db.commit()
    return {
        "id": upload.id,
        "dataset_id": dataset_id,
        "file_name": upload.file_name,
        "size_bytes": upload.size_bytes,
        "row_count": upload.row_count,
        "columns": upload.columns_json,
    }


@app.post("/api/v1/workspaces/{workspace_id}/runs", status_code=status.HTTP_202_ACCEPTED)
def create_quality_run(
    workspace_id: str,
    body: RunCreate,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_workspace(db, workspace_id, identity)
    dataset = _dataset(db, workspace_id, body.dataset_id)
    upload = db.scalar(
        select(DatasetUpload).where(
            DatasetUpload.id == body.upload_id,
            DatasetUpload.dataset_id == dataset.id,
        )
    )
    if not upload:
        raise HTTPException(status_code=404, detail="Upload not found for this dataset")
    run = QualityRun(
        workspace_id=workspace_id,
        dataset_id=dataset.id,
        upload_id=upload.id,
        status="queued",
        created_by=identity_subject(identity),
    )
    db.add(run)
    db.flush()
    record_audit(
        db, workspace_id, identity, "quality_run.queued", "quality_run", run.id,
        {"dataset_id": dataset.id, "upload_id": upload.id},
    )
    db.commit()
    try:
        queue = Queue("testagent", connection=Redis.from_url(settings.redis_url))
        queue.enqueue(
            "dhqa_api.worker.execute_quality_run",
            run.id,
            job_timeout="30m",
            result_ttl=86400,
            failure_ttl=604800,
        )
    except Exception as exc:
        run.status = "failed"
        run.error_message = "Could not submit quality run to the background queue"
        run.finished_at = utcnow()
        db.commit()
        raise HTTPException(status_code=503, detail="Background worker queue is unavailable") from exc
    return _run_response(db, run, include_details=False)


@app.get("/api/v1/workspaces/{workspace_id}/runs")
def list_runs(
    workspace_id: str,
    limit: int = 50,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    ensure_workspace(db, workspace_id, identity)
    rows = db.scalars(
        select(QualityRun)
        .where(QualityRun.workspace_id == workspace_id)
        .order_by(QualityRun.created_at.desc())
        .limit(max(1, min(limit, 100)))
    ).all()
    return [_run_response(db, row, include_details=False) for row in rows]


@app.get("/api/v1/workspaces/{workspace_id}/runs/{run_id}")
def get_run(
    workspace_id: str,
    run_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_workspace(db, workspace_id, identity)
    run = db.scalar(
        select(QualityRun).where(
            QualityRun.id == run_id,
            QualityRun.workspace_id == workspace_id,
        )
    )
    if not run:
        raise HTTPException(status_code=404, detail="Quality run not found")
    return _run_response(db, run)


@app.get("/api/v1/workspaces/{workspace_id}/runs/{run_id}/artifacts/{artifact_id}")
def download_artifact(
    workspace_id: str,
    run_id: str,
    artifact_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> PlainTextResponse:
    ensure_workspace(db, workspace_id, identity)
    run = db.scalar(
        select(QualityRun).where(QualityRun.id == run_id, QualityRun.workspace_id == workspace_id)
    )
    artifact = db.scalar(
        select(GeneratedArtifact).where(
            GeneratedArtifact.id == artifact_id,
            GeneratedArtifact.run_id == run_id,
        )
    )
    if not run or not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return PlainTextResponse(
        artifact.content,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{artifact.file_name}"'},
    )


@app.get("/api/v1/workspaces/{workspace_id}/incidents")
def list_incidents(
    workspace_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    ensure_workspace(db, workspace_id, identity)
    rows = db.scalars(
        select(Incident)
        .where(Incident.workspace_id == workspace_id)
        .order_by(Incident.created_at.desc())
        .limit(100)
    ).all()
    return [
        {
            "id": row.id,
            "run_id": row.run_id,
            "dataset_id": row.dataset_id,
            "title": row.title,
            "status": row.status,
            "details": row.details_json,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


@app.patch("/api/v1/workspaces/{workspace_id}/incidents/{incident_id}")
def update_incident(
    workspace_id: str,
    incident_id: str,
    body: IncidentUpdate,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    ensure_workspace(db, workspace_id, identity)
    incident = db.scalar(
        select(Incident).where(
            Incident.id == incident_id,
            Incident.workspace_id == workspace_id,
        )
    )
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident.status = body.status
    record_audit(
        db, workspace_id, identity, "incident.status_changed", "incident", incident.id,
        {"status": incident.status},
    )
    db.commit()
    return {"id": incident.id, "status": incident.status}


@app.get("/api/v1/workspaces/{workspace_id}/overview")
def workspace_overview(
    workspace_id: str,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ensure_workspace(db, workspace_id, identity)
    dataset_count = db.scalar(
        select(func.count()).select_from(Dataset).where(Dataset.workspace_id == workspace_id)
    ) or 0
    run_count = db.scalar(
        select(func.count()).select_from(QualityRun).where(QualityRun.workspace_id == workspace_id)
    ) or 0
    open_incidents = db.scalar(
        select(func.count()).select_from(Incident).where(
            Incident.workspace_id == workspace_id,
            Incident.status != "resolved",
        )
    ) or 0
    latest = db.scalars(
        select(QualityRun)
        .where(QualityRun.workspace_id == workspace_id)
        .order_by(desc(QualityRun.created_at))
        .limit(5)
    ).all()
    return {
        "datasets": dataset_count,
        "runs": run_count,
        "open_incidents": open_incidents,
        "recent_runs": [_run_response(db, item, include_details=False) for item in latest],
    }


@app.get("/api/v1/workspaces/{workspace_id}/audit")
def list_audit_events(
    workspace_id: str,
    limit: int = 100,
    identity: dict[str, Any] = Depends(current_identity),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    ensure_workspace(db, workspace_id, identity)
    rows = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.workspace_id == workspace_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(max(1, min(limit, 200)))
    ).all()
    return [
        {
            "id": row.id,
            "actor_subject": row.actor_subject,
            "action": row.action,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "details": row.details_json,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
