import pytest

pytest.importorskip("pydantic_settings")

from fastapi import HTTPException

from dhqa_api.config import settings
from dhqa_api.security import current_identity


def test_dev_identity_header_is_accepted_only_in_development(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "dev_auth_enabled", True)
    identity = current_identity(credentials=None, x_dev_user="qa-tester")
    assert identity["sub"] == "qa-tester"


def test_dev_identity_header_is_rejected_outside_development(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "dev_auth_enabled", True)
    with pytest.raises(HTTPException) as exc:
        current_identity(credentials=None, x_dev_user="qa-tester")
    assert exc.value.status_code == 401


def test_empty_dev_identity_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "dev_auth_enabled", True)
    with pytest.raises(HTTPException) as exc:
        current_identity(credentials=None, x_dev_user=" ")
    assert exc.value.status_code == 400
