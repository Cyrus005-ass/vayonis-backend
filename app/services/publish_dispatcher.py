import uuid

from sqlalchemy.orm import Session

from app.models.post_target import PostTarget
from app.services import instagram_publish_service, linkedin_publish_service, meta_publish_service

_SERVICES = {
    "instagram": instagram_publish_service,
    "facebook": meta_publish_service,
    "linkedin": linkedin_publish_service,
}


class UnsupportedPlatformError(Exception):
    pass


async def publish_target(db: Session, post_target: PostTarget) -> PostTarget:
    service = _SERVICES.get(post_target.platform)
    if service is None:
        raise UnsupportedPlatformError(f"Unsupported platform: {post_target.platform}")
    return await service.publish_post_target(db, post_target.id)