"""Pydantic models for API requests and responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


class WebhookOut(BaseModel):
    id: int
    url: str
    label: str = ""
    enabled: bool
    created_at: str

    @field_validator("enabled", mode="before")
    @classmethod
    def coerce_enabled(cls, v: Any) -> bool:
        return bool(v)


class WebhookCreate(BaseModel):
    url: str = Field(..., min_length=1, max_length=4096)
    label: str = Field("", max_length=200)
    enabled: bool = True


class WebhookPatch(BaseModel):
    url: str | None = Field(None, min_length=1, max_length=4096)
    label: str | None = Field(None, max_length=200)
    enabled: bool | None = None


class AppSettings(BaseModel):
    app_title: str = Field(..., min_length=1, max_length=200)


class BrandingOut(BaseModel):
    app_title: str
    logo_mode: str
    logo_url: str
    sidebar_background: str
    sidebar_text: str
    presets_supported: list[str] = Field(
        default_factory=lambda: ["navy", "slate", "forest", "wine", "bronze", "light"]
    )


class BrandingPatch(BaseModel):
    app_title: str | None = Field(None, min_length=1, max_length=200)
    logo_mode: Literal["builtin", "custom"] | None = None
    sidebar_background: str | None = None
    sidebar_text: str | None = None
    preset: str | None = None


class PurgeDataBody(BaseModel):
    confirm: str


class PurgeDataOut(BaseModel):
    deleted: dict[str, int] = Field(default_factory=dict)


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
    name: str = Field(..., min_length=1, max_length=500)
    description: str = Field(..., min_length=1, max_length=2000)
    asset_type_id: int | None = None
    parent_asset_id: int | None = None
    assigned_user_id: int | None = None
    external_inventory: bool = False
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class InventoryUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = Field(None, min_length=1, max_length=2000)
    asset_type_id: int | None = None
    parent_asset_id: int | None = None
    clear_parent: bool = False
    assigned_user_id: int | None = None
    clear_assigned_user: bool = False
    external_inventory: bool | None = None
    custom_fields: dict[str, Any] | None = None


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
    asset_type_id: int | None = None
    name: str
    description: str
    parent_asset_id: int | None = None
    parent_name: str | None = None
    assigned_user_id: int | None = None
    assigned_username: str | None = None
    external_inventory: bool = False
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    asset_type_name: str | None = None
    children_count: int = 0


ChangeType = Literal["standard", "normal"]
RequestStatus = Literal["draft", "submitted", "in_progress", "fulfilled", "closed", "cancelled"]
RitmStatus = Literal["draft", "submitted", "in_progress", "fulfilled", "closed"]
ChangeStatus = Literal["draft", "pending_approval", "approved", "implementing", "completed", "cancelled"]
CtaskStatus = Literal["blocked", "pending", "in_progress", "completed"]


FieldType = Literal["text", "textarea", "number", "boolean", "select", "date"]
ScopeType = Literal["asset_type", "request_template", "change_template", "task_template"]


class CustomFieldDefinitionCreate(BaseModel):
    field_key: str = Field(..., min_length=1, max_length=64)
    label: str = Field(..., min_length=1, max_length=200)
    field_type: FieldType = "text"
    required: bool = False
    options: list[str] = Field(default_factory=list)
    sort_order: int = 0


class CustomFieldDefinitionUpdate(BaseModel):
    field_key: str | None = Field(None, min_length=1, max_length=64)
    label: str | None = Field(None, min_length=1, max_length=200)
    field_type: FieldType | None = None
    required: bool | None = None
    options: list[str] | None = None
    sort_order: int | None = None


class CustomFieldDefinitionOut(BaseModel):
    id: int
    scope_type: str
    scope_id: int
    field_key: str
    label: str
    field_type: FieldType
    required: bool
    options: list[str]
    sort_order: int


class TaskTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    title: str = Field(..., min_length=1, max_length=500)
    description: str = ""
    assigned_user_id: int | None = None
    kb_article_id: int | None = None


class TaskTemplateUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    title: str | None = Field(None, min_length=1, max_length=500)
    description: str | None = None
    assigned_user_id: int | None = None
    clear_assigned_user: bool = False
    kb_article_id: int | None = None
    clear_kb: bool = False


class TaskTemplateOut(BaseModel):
    id: int
    name: str
    title: str
    description: str
    assigned_user_id: int | None = None
    assigned_username: str | None = None
    kb_article_id: int | None = None
    kb_article_title: str | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    field_definitions: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ChangeTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    change_type: ChangeType = "standard"
    task_template_ids: list[int] = Field(default_factory=list)


class ChangeTemplateUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    change_type: ChangeType | None = None
    task_template_ids: list[int] | None = None


class ChangeTemplateOut(BaseModel):
    id: int
    name: str
    description: str
    change_type: ChangeType
    task_template_ids: list[int]
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    field_definitions: list[dict[str, Any]] = Field(default_factory=list)
    task_templates: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str


class RequestTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=1, max_length=2000)
    change_template_id: int | None = None
    require_standard_change: bool = True


class RequestTemplateUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, min_length=1, max_length=2000)
    change_template_id: int | None = None
    require_standard_change: bool | None = None
    clear_change_template: bool = False


class RequestTemplateOut(BaseModel):
    id: int
    name: str
    description: str
    change_template_id: int | None = None
    change_template_name: str | None = None
    require_standard_change: bool
    field_definitions: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str
    updated_at: str


class RequestCreate(BaseModel):
    name: str
    description: str
    request_template_id: int | None = None
    specifications: dict[str, Any] = Field(default_factory=dict)


class RequestPatch(BaseModel):
    name: str | None = None
    description: str | None = None


class RequestOut(BaseModel):
    id: int
    public_id: str
    requester_user_id: int
    requester_username: str | None = None
    name: str
    description: str
    status: RequestStatus
    created_at: str
    updated_at: str
    submitted_at: str | None = None


class RitmCreate(BaseModel):
    request_template_id: int | None = None
    catalog_item_id: int | None = None
    item_type: str = ""
    specifications: dict[str, Any] = Field(default_factory=dict)
    packages: list[str] = Field(default_factory=list)
    application: str = ""


class RitmPatch(BaseModel):
    item_type: str | None = None
    specifications: dict[str, Any] | None = None
    packages: list[str] | None = None
    application: str | None = None


class RitmOut(BaseModel):
    id: int
    public_id: str
    request_id: int
    request_template_id: int | None = None
    catalog_item_id: int | None = None
    item_type: str
    specifications: dict[str, Any]
    packages: list[str]
    application: str
    status: RitmStatus
    change_public_id: str | None = None
    change_status: str | None = None
    request_template_name: str | None = None
    catalog_item_name: str | None = None
    created_at: str
    updated_at: str


class ChangeCreate(BaseModel):
    change_template_id: int
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class ChangePatch(BaseModel):
    risk_assessment: str | None = None
    implementation_plan: str | None = None
    test_plan: str | None = None
    backout_plan: str | None = None


class ChangeOut(BaseModel):
    id: int
    public_id: str
    ritm_id: int | None = None
    change_template_id: int | None = None
    change_type: ChangeType
    risk_assessment: str
    implementation_plan: str
    test_plan: str
    backout_plan: str
    status: ChangeStatus
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    approved_at: str | None = None
    approved_by_user_id: int | None = None
    created_at: str
    updated_at: str
    ritm_public_id: str | None = None
    request_public_id: str | None = None


class CtaskOut(BaseModel):
    id: int
    public_id: str
    change_id: int | None = None
    sequence_order: int
    title: str
    description: str
    assigned_user_id: int | None = None
    assigned_username: str | None = None
    kb_article_id: int | None = None
    kb_article_title: str | None = None
    status: CtaskStatus
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    completed_at: str | None = None
    completion_comment: str = ""
    created_at: str
    updated_at: str
    change_public_id: str | None = None
    change_status: str | None = None


class TaskCreate(BaseModel):
    change_ref: str | None = None
    title: str = ""
    description: str = ""
    assigned_user_id: int | None = None
    kb_article_id: int | None = None
    task_template_id: int | None = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class TaskCompleteBody(BaseModel):
    comment: str = ""
