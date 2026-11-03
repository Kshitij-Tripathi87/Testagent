import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("pandas")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dhqa_api.db import Base
from dhqa_api.models import (
    CheckResultRow,
    DataConnection,
    Dataset,
    DatasetUpload,
    GeneratedArtifact,
    Incident,
    QualityRun,
    Workspace,
)
from dhqa_api import worker


def _contract(urn: str, name: str, upstreams: list[str] | None = None):
    return {
        "urn": urn,
        "name": name,
        "columns": [{"name": "id", "type": "string", "nullable": False}],
        "owners": [],
        "upstream_urns": upstreams or [],
        "constraints": [{"kind": "not_null", "column": "id"}],
        "primary_key_columns": ["id"],
        "timestamp_column": None,
        "max_staleness_hours": 24,
    }


def test_worker_persists_failed_checks_incident_lineage_and_artifacts(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", TestingSession)
    monkeypatch.setattr(worker.settings, "max_lineage_hops", 5)

    workspace_urn = "urn:li:dataset:(urn:li:dataPlatform:snowflake,raw.parent,PROD)"
    child_urn = "urn:li:dataset:(urn:li:dataPlatform:snowflake,marts.child,PROD)"
    parent_csv = tmp_path / "parent.csv"
    child_csv = tmp_path / "child.csv"
    parent_csv.write_text("id\n\n2\n", encoding="utf-8")
    child_csv.write_text("id\n\n3\n", encoding="utf-8")

    with TestingSession() as db:
        workspace = Workspace(name="Worker test", created_by="tester")
        db.add(workspace)
        db.flush()
        connection = DataConnection(
            workspace_id=workspace.id,
            name="Fixture DataHub",
            gms_url="http://datahub.local:8080",
            token_ciphertext="",
        )
        db.add(connection)
        db.flush()

        parent = Dataset(
            workspace_id=workspace.id,
            connection_id=connection.id,
            urn=workspace_urn,
            name="raw_parent",
            metadata_json={
                "columns": [{"name": "id", "type": "string", "nullable": False}],
                "owners": [],
                "upstream_urns": [],
            },
            contract_json=_contract(workspace_urn, "raw_parent"),
        )
        child = Dataset(
            workspace_id=workspace.id,
            connection_id=connection.id,
            urn=child_urn,
            name="child",
            metadata_json={
                "columns": [{"name": "id", "type": "string", "nullable": False}],
                "owners": [],
                "upstream_urns": [workspace_urn],
            },
            contract_json=_contract(child_urn, "child", [workspace_urn]),
        )
        db.add_all([parent, child])
        db.flush()

        parent_upload = DatasetUpload(
            dataset_id=parent.id,
            file_name="parent.csv",
            storage_path=str(parent_csv),
            size_bytes=parent_csv.stat().st_size,
            sha256="a" * 64,
            row_count=2,
            columns_json=["id"],
            created_by="tester",
        )
        child_upload = DatasetUpload(
            dataset_id=child.id,
            file_name="child.csv",
            storage_path=str(child_csv),
            size_bytes=child_csv.stat().st_size,
            sha256="b" * 64,
            row_count=2,
            columns_json=["id"],
            created_by="tester",
        )
        db.add_all([parent_upload, child_upload])
        db.flush()

        run = QualityRun(
            workspace_id=workspace.id,
            dataset_id=child.id,
            upload_id=child_upload.id,
            status="queued",
            created_by="tester",
        )
        db.add(run)
        db.commit()
        run_id = run.id
        child_dataset_id = child.id

    try:
        result = worker.execute_quality_run(run_id)
        assert result["status"] == "completed"
        assert result["failed_checks"] == 1
        assert result["incidents"] == 1

        with TestingSession() as db:
            persisted_run = db.get(QualityRun, run_id)
            assert persisted_run is not None
            assert persisted_run.status == "completed"
            assert persisted_run.passed is False

            checks = db.scalars(
                select(CheckResultRow).where(CheckResultRow.run_id == run_id)
            ).all()
            assert len(checks) == 1
            assert checks[0].passed is False

            incidents = db.scalars(
                select(Incident).where(Incident.run_id == run_id)
            ).all()
            assert len(incidents) == 1
            assert incidents[0].details_json["origin_urn"] == workspace_urn
            assert incidents[0].details_json["trace"][0]["passed"] is False

            artifacts = db.scalars(
                select(GeneratedArtifact).where(GeneratedArtifact.run_id == run_id)
            ).all()
            assert len(artifacts) >= 6
            assert any(item.kind == "incident_report" for item in artifacts)
            assert any(item.kind == "dbt_sql" for item in artifacts)
            assert all(item.content for item in artifacts)

        repeated = worker.execute_quality_run(run_id)
        assert repeated["idempotent"] is True
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
