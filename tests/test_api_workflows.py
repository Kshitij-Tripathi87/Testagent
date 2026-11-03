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
from dhqa_api.models import ContractVersion, DataConnection, Dataset, DatasetUpload


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


def test_catalog_sync_preserves_custom_rules_and_versions_schema_changes(api_env, monkeypatch):
    client, session_factory = api_env
    workspace_id = _create_workspace(client)
    headers = {"X-Dev-User": "qa-user"}

    connection_response = client.post(
        f"/api/v1/workspaces/{workspace_id}/connections",
        headers=headers,
        json={
            "name": "Mock DataHub",
            "gms_url": "http://datahub.local:8080",
            "token": "",
        },
    )
    assert connection_response.status_code == 201, connection_response.text
    connection_id = connection_response.json()["id"]

    metadata = {
        "urn": "urn:li:dataset:(urn:li:dataPlatform:snowflake,orders.fact_orders,PROD)",
        "name": "fact_orders",
        "columns": [
            {"name": "id", "type": "string", "nullable": False, "description": "Primary key"},
            {"name": "email", "type": "string", "nullable": True, "description": "Customer email"},
        ],
        "owners": ["urn:li:corpuser:data-team"],
        "upstream_urns": [],
        "glossary_terms": [],
    }

    class FakeDataHubClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_datasets(self, limit=100):
            assert limit == 100
            return [metadata]

    monkeypatch.setattr(api, "_connection_client", lambda _connection: FakeDataHubClient())

    sync_path = (
        f"/api/v1/workspaces/{workspace_id}/connections/{connection_id}/sync"
    )
    first_sync = client.post(sync_path, headers=headers)
    assert first_sync.status_code == 200, first_sync.text
    assert first_sync.json()["synced"] == 1

    with session_factory() as db:
        dataset = db.scalar(
            select(Dataset).where(
                Dataset.workspace_id == workspace_id,
                Dataset.urn == metadata["urn"],
            )
        )
        assert dataset is not None
        dataset_id = dataset.id
        assert dataset.contract_version == 1
        assert any(
            rule["kind"] == "not_null" and rule["column"] == "id"
            for rule in dataset.contract_json["constraints"]
        )

    edited = client.put(
        f"/api/v1/workspaces/{workspace_id}/datasets/{dataset_id}/contract",
        headers=headers,
        json={
            "constraints": [{"kind": "not_null", "column": "email"}],
            "max_staleness_hours": 12,
        },
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 2

    metadata["columns"].append(
        {"name": "status", "type": "string", "nullable": True, "description": "Order status"}
    )
    second_sync = client.post(sync_path, headers=headers)
    assert second_sync.status_code == 200, second_sync.text

    detail = client.get(
        f"/api/v1/workspaces/{workspace_id}/datasets/{dataset_id}",
        headers=headers,
    )
    assert detail.status_code == 200
    body = detail.json()
    assert body["contract_version"] == 3
    assert [column["name"] for column in body["metadata"]["columns"]] == [
        "id", "email", "status"
    ]
    assert body["contract"]["constraints"] == [
        {
            "kind": "not_null",
            "column": "email",
            "ref_urn": None,
            "max_staleness_hours": None,
        }
    ]
    assert body["contract"]["max_staleness_hours"] == 12

    history = client.get(
        f"/api/v1/workspaces/{workspace_id}/datasets/{dataset_id}/contract/versions",
        headers=headers,
    )
    assert history.status_code == 200
    assert [version["version"] for version in history.json()] == [3, 2, 1]

    with session_factory() as db:
        snapshots = db.scalars(
            select(ContractVersion)
            .where(ContractVersion.dataset_id == dataset_id)
            .order_by(ContractVersion.version)
        ).all()
        assert len(snapshots) == 3
        assert snapshots[-1].contract_json["columns"][-1]["name"] == "status"
        assert snapshots[-1].contract_json["constraints"][0]["column"] == "email"
