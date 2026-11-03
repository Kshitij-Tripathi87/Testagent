from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)


class ConnectionCreate(BaseModel):
    name: str = Field(default="DataHub", min_length=2, max_length=100)
    gms_url: str = Field(min_length=8, max_length=1000)
    token: str = Field(default="", max_length=4000)

    @field_validator("gms_url")
    @classmethod
    def validate_gms_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("GMS URL must start with http:// or https://")
        return value


class ConstraintInput(BaseModel):
    kind: Literal["not_null", "unique", "referential", "freshness"]
    column: str | None = Field(default=None, max_length=255)
    ref_urn: str | None = Field(default=None, max_length=2000)
    max_staleness_hours: int | None = Field(default=None, ge=1, le=720)


class ContractUpdate(BaseModel):
    constraints: list[ConstraintInput] = Field(max_length=500)
    max_staleness_hours: int = Field(default=24, ge=1, le=720)


class RunCreate(BaseModel):
    dataset_id: str
    upload_id: str


class IncidentUpdate(BaseModel):
    status: Literal["open", "acknowledged", "resolved"]
