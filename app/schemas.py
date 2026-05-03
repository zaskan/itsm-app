"""Pydantic models for API requests and responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Severity = Literal["low", "medium", "high", "critical"]
IncidentStatus = Literal["open", "closed"]


class IncidentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str = ""
    severity: Severity = "medium"
    created_at: datetime | None = None
    inventory_asset_id: int | None = None


class IncidentPatch(BaseModel):
    severity: Severity | None = None
    inventory_asset_id: int | None = None


class IncidentClose(BaseModel):
    kb_article_id: int | None = None


class CommentCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=10000)


class KBArticleCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    description: str = ""


class KBArticleUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = None


class WebhookSettings(BaseModel):
    webhook_url: str = ""


class AppSettings(BaseModel):
    app_title: str = Field(..., min_length=1, max_length=200)


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=1, max_length=500)
    role: Literal["admin", "user"] = "user"


class UserUpdate(BaseModel):
    username: str | None = Field(None, min_length=1, max_length=200)
    password: str | None = Field(None, min_length=1, max_length=500)
    role: Literal["admin", "user"] | None = None


class AssetTypeCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""


class AssetTypeUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None


class InventoryCreate(BaseModel):
    asset_type_id: int
    hostname: str = Field(..., min_length=1, max_length=500)
    ip_address: str = ""
    group_name: str = ""


class InventoryUpdate(BaseModel):
    asset_type_id: int | None = None
    hostname: str | None = Field(None, min_length=1, max_length=500)
    ip_address: str | None = None
    group_name: str | None = None


class IncidentOut(BaseModel):
    id: int
    public_id: str
    title: str
    description: str
    severity: Severity
    status: IncidentStatus
    created_at: str
    updated_at: str
    closed_at: str | None = None
    inventory_asset_id: int | None = None
    resolution_kb_article_id: int | None = None
    resolution_kb_title: str | None = None

    model_config = ConfigDict(from_attributes=True)


class CommentOut(BaseModel):
    id: int
    incident_id: int
    body: str
    author_username: str
    created_at: str


class EventOut(BaseModel):
    id: int
    event_type: str
    payload: dict[str, Any]
    actor_username: str
    created_at: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str


class KBArticleOut(BaseModel):
    id: int
    title: str
    description: str
    created_at: str
    updated_at: str


class LoginForm(BaseModel):
    username: str
    password: str


class AssetTypeOut(BaseModel):
    id: int
    name: str
    description: str
    created_at: str
    updated_at: str


class InventoryOut(BaseModel):
    id: int
    asset_type_id: int
    hostname: str
    ip_address: str
    group_name: str
    created_at: str
    updated_at: str
    asset_type_name: str | None = None
