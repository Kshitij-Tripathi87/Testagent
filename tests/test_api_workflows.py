import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic_settings")
pytest.importorskip("cryptography")
pytest.importorskip("redis")
pytest.importorskip("rq")

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from dhqa_api import main as api
from dhqa_api.config import settings
from dhqa_api.db import Base, get_db
from dhqa_api.encryption import decrypt_secret
from dhqa_api.models import DataConnection, Dataset, DatasetUpload


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(
        bind=test_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "dev_auth_enabled", True)
    monkeypatch.setattr(settings, "app_encryption_key", Fernet.generate_key().decode())
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    api.app.dependency_overrides[get_db] = override_get_db

    with TestClient(api.app) as client:
        yield client, TestingSession

    api.app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=test_engine)
    test_engine.dispose()


def _create_workspace(client, user="qa-user"):
    response = client.post(
        "/api/v1/workspaces",
        headers={"X-Dev-User": user},
        json={"name": "QA workspace"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_workspace_isolation_and_connection_secret_encryption(api_env):
    client, session_factory = api_env
    workspace_id = _create_workspace(client)

    own = client.get(
        "/api/v1/workspaces",
        headers={"X-Dev-User": "qa-user"},
    )
    other = client.get(
        "/api/v1/workspaces",
        headers={"X-Dev-User": "other-user"},
    )
    assert len(own.json()) == 1
    assert other.json() == []

    headers = {"X-Dev-User": "qa-user"}
    created = client.post(
        f"/api/v1/workspaces/{workspace_id}/connections",
        headers=headers,
        json={
            "name": "Local DataHub",
            "gms_url": "http://datahub.local:8080",
            "token": "sensitive-test-token",
        },
    )
    assert created.status_code == 201, created.text
    connection_id = created.json()["id"]

    listed = client.get(
        f"/api/v1/workspaces/{workspace_id}/connections",
        headers=headers,
    )
    assert listed.status_code == 200
    assert "sensitive-test-token" not in listed.text

    with session_factory() as db:
        connection = db.scalar(select(DataConnection).where(DataConnection.id == connection_id))
        assert connection is not None
        assert connection.token_ciphertext != "sensitive-test-token"
        assert decrypt_secret(connection.token_ciphertext) == "sensitive-test-token"

    hidden = client.get(
        f"/api/v1/workspaces/{workspace_id}/connections",
        headers={"X-Dev-User": "other-user"},
    )
    assert hidden.status_code == 404


def test_csv_upload_and_quality_run_queue(api_env, monkeypatch):
    client, session_factory = api_env
    workspace_id = _create_workspace(client)
    headers = {"X-Dev-User": "qa-user"}

    with session_factory() as db:
        connection = DataConnection(
            workspace_id=workspace_id,
            name="Fixture DataHub",
            gms_url="http://datahub.local:8080",
            token_ciphertext="",
        )
        db.add(connection)
        db.flush()
        dataset = Dataset(
            workspace_id=workspace_id,
            connection_id=connection.id,
            urn="urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.raw_orders,PROD)",
            name="raw_orders",
            metadata_json={
                "columns": [
                    {"name": "id", "type": "string", "nullable": False},
                    {"name": "amount", "type": "float", "nullable": True},
                ],
                "upstream_urns": [],
                "owners": [],
            },
            contract_json={
                "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.raw_orders,PROD)",
                "name": "raw_orders",
                "columns": [
                    {"name": "id", "type": "string", "nullable": False},
                    {"name": "amount", "type": "float", "nullable": True},
                ],
                "owners": [],
                "upstream_urns": [],
                "constraints": [{"kind": "not_null", "column": "id"}],
                "primary_key_columns": [],
                "timestamp_column": None,
                "max_staleness_hours": 24,
            },
        )
        db.add(dataset)
        db.commit()
        dataset_id = dataset.id

    uploaded = client.post(
        f"/api/v1/workspaces/{workspace_id}/datasets/{dataset_id}/uploads",
        headers=headers,
        files={"file": ("orders.csv", b"id,amount\n1,9.5\n2,4.2\n", "text/csv")},
    )
    assert uploaded.status_code == 201, uploaded.text
    upload_id = uploaded.json()["id"]
    assert uploaded.json()["row_count"] == 2

    with session_factory() as db:
        row = db.get(DatasetUpload, upload_id)
        assert row is not None
        assert row.sha256
        assert row.size_bytes > 0

    queued = []

    class FakeQueue:
        def __init__(self, *_args, **_kwargs):
            pass

        def enqueue(self, *args, **kwargs):
            queued.append((args, kwargs))

    monkeypatch.setattr(api, "Queue", FakeQueue)
    result = client.post(
        f"/api/v1/workspaces/{workspace_id}/runs",
        headers=headers,
        json={"dataset_id": dataset_id, "upload_id": upload_id},
    )
    assert result.status_code == 202, result.text
    assert result.json()["status"] == "queued"
    assert len(queued) == 1
