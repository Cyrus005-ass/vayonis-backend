"""Internal endpoint for scheduled post publishing.

There is no paid background worker running in production (Render free tier
doesn't include one), so instead of Celery beat, this endpoint is meant to
be called periodically by a free external cron service (e.g. cron-job.org)
every 5-10 minutes. It finds posts whose scheduled_at has passed and are
still in "scheduled" status, and publishes them to all their targets -
reusing the exact same multi-target logic as the manual "publish now" flow.
"""

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.post import Post
from app.models.post_target import PostTarget
from app.services import publish_dispatcher

router = APIRouter(prefix="/internal", tags=["internal"])


def verify_cron_secret(x_internal_secret: str = Header(...)) -> None:
    if x_internal_secret != settings.INTERNAL_CRON_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid secret")


@router.post("/publish-scheduled")
async def publish_scheduled_posts(
    db: Session = Depends(get_db),
    _: None = Depends(verify_cron_secret),
) -> dict:
    now = datetime.now(UTC)
    due_posts = (
        db.query(Post)
        .filter(Post.status == "scheduled", Post.scheduled_at <= now)
        .all()
    )

    processed = []
    for post in due_posts:
        targets = db.query(PostTarget).filter(PostTarget.post_id == post.id).all()
        if not targets:
            continue

        async def _publish_one(target: PostTarget) -> PostTarget:
            try:
                return await publish_dispatcher.publish_target(db, target)
            except Exception as exc:  # noqa: BLE001
                target.status = "failed"
                target.error_message = str(exc)
                db.commit()
                db.refresh(target)
                return target

        results = await asyncio.gather(*(_publish_one(t) for t in targets))

        post.status = "published" if all(t.status == "published" for t in results) else "partially_failed"
        db.commit()

        processed.append({"post_id": str(post.id), "targets_published": len(results)})

    return {"processed_count": len(processed), "posts": processed}