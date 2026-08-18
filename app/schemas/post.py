import uuid
from datetime import datetime

from pydantic import BaseModel


class PostCreate(BaseModel):
    caption: str | None = None
    content_type: str = "post_classique"
    scheduled_at: datetime | None = None


class PostResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    caption: str | None
    content_type: str
    scheduled_at: datetime | None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PostMediaResponse(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID
    media_asset_id: uuid.UUID
    sort_order: int

    model_config = {"from_attributes": True}


class MediaAssetResponse(BaseModel):
    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    width: int | None
    height: int | None
    duration_seconds: float | None
    storage_key: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PostTargetCreate(BaseModel):
    social_account_id: uuid.UUID
    platform: str


class PostTargetResponse(BaseModel):
    id: uuid.UUID
    post_id: uuid.UUID
    social_account_id: uuid.UUID
    platform: str
    status: str
    external_post_id: str | None
    error_message: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
