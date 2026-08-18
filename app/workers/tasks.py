import logging
from datetime import UTC, datetime, timedelta

from app.core.database import SessionLocal
from app.models.post import Post
from app.models.social_account import SocialAccount
from app.services import instagram_oauth_service, meta_oauth_service, publish_dispatcher
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)

REFRESH_WINDOW_DAYS = 7


@celery_app.task(name="refresh_expiring_tokens")
def refresh_expiring_tokens() -> dict:
    db = SessionLocal()
    refreshed = 0
    failed = 0

    try:
        threshold = datetime.now(UTC) + timedelta(days=REFRESH_WINDOW_DAYS)
        expiring_accounts = (
            db.query(SocialAccount)
            .filter(
                SocialAccount.platform.in_(["facebook", "instagram"]),
                SocialAccount.token_expires_at.isnot(None),
                SocialAccount.token_expires_at <= threshold,
            )
            .all()
        )

        for account in expiring_accounts:
            try:
                if account.platform == "instagram":
                    meta_oauth_service_or_ig = instagram_oauth_service
                else:
                    meta_oauth_service_or_ig = meta_oauth_service

                meta_oauth_service_or_ig.refresh_account_token(db, account)
                refreshed += 1
                logger.info(
                    "Refreshed token for %s account %s", account.platform, account.id
                )
            except Exception:
                failed += 1
                logger.exception(
                    "Failed to refresh token for %s account %s",
                    account.platform,
                    account.id,
                )

        return {"refreshed": refreshed, "failed": failed, "total": len(expiring_accounts)}
    finally:
        db.close()


@celery_app.task(name="publish_scheduled_post")
def publish_scheduled_post(post_id: str) -> dict:
    import asyncio

    db = SessionLocal()
    try:
        post = db.query(Post).filter(Post.id == post_id).first()
        if post is None:
            return {"error": "post not found"}

        async def _publish_all():
            for target in post.targets:
                try:
                    await publish_dispatcher.publish_target(db, target)
                except Exception as exc:
                    logger.exception("Failed to publish target %s: %s", target.id, exc)

        asyncio.run(_publish_all())

        db.refresh(post)
        statuses = {t.status for t in post.targets}
        post.status = "published" if statuses == {"published"} else "partial_failure"
        db.commit()

        return {"post_id": str(post.id), "status": post.status}
    finally:
        db.close()