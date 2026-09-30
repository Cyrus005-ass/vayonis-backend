import uuid

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.post_target import PostTarget


class TikTokPublishError(Exception):
    pass


async def publish_post_target(
    db: Session,
    post_target_id: uuid.UUID,
    client: httpx.AsyncClient | None = None,
) -> PostTarget:
    if not settings.ENABLE_TIKTOK:
        raise TikTokPublishError("La publication TikTok n'est pas encore disponible")

    post_target = db.query(PostTarget).filter(PostTarget.id == post_target_id).first()
    if post_target is None:
        raise TikTokPublishError(f"PostTarget {post_target_id} not found")

    post_target.status = "failed"
    post_target.error_message = "La publication TikTok reste à implémenter"
    db.commit()
    db.refresh(post_target)
    return post_target
