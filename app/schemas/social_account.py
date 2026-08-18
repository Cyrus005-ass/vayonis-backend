import uuid
from datetime import datetime

from pydantic import BaseModel


class SocialAccountResponse(BaseModel):
    id: uuid.UUID
    platform: str
    external_id: str
    display_name: str
    token_expires_at: datetime | None
    metadata_json: dict | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OAuthConnectResponse(BaseModel):
    authorization_url: str


class OAuthCallbackResponse(BaseModel):
    connected_accounts: list[SocialAccountResponse]
