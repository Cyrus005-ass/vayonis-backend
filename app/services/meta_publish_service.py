"""Facebook Page content publishing service.

Handles publishing a Post to a Facebook Page via the Graph API.
Supports:
- text-only posts (message on /{page_id}/feed)
- single image post (/{page_id}/photos)
- single video post (/{page_id}/videos) - classic video post
- single video as a Reel (/{page_id}/video_reels) - separate 3-phase API

Which video path is used is driven by post.content_type: "reel" publishes
via the Reels API, anything else (default "post_classique") uses the
classic video post endpoint. These are genuinely different Meta APIs with
different behavior - a classic video post and a Reel are not the same
object, even though both are "just a video" to the end user.

Multi-media (carousel-style) Facebook posts are not yet supported here;
only the first media item is used if several are attached.
"""

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session, joinedload

from app.core.security import decrypt_token
from app.models.post_target import PostTarget
from app.services import storage_service

META_GRAPH_URL = "https://graph.facebook.com/v25.0"

IMAGE_URL_TTL_SECONDS = 900  # 15 min
VIDEO_URL_TTL_SECONDS = 1800  # 30 min

# Polling for Reels processing (Meta processes async, like Instagram).
REEL_POLL_INTERVAL_SECONDS = 5
REEL_POLL_MAX_ATTEMPTS = 36  # ~3 minutes


class MetaPublishError(Exception):
    pass


def _is_video(content_type: str) -> bool:
    return content_type.startswith("video/")


def _media_url_for_asset(asset) -> str:
    ttl = VIDEO_URL_TTL_SECONDS if _is_video(asset.content_type) else IMAGE_URL_TTL_SECONDS
    return storage_service.generate_presigned_url(asset.storage_key, expires_in=ttl)


async def _post_text(
    client: httpx.AsyncClient, page_id: str, access_token: str, caption: str
) -> str:
    response = await client.post(
        f"{META_GRAPH_URL}/{page_id}/feed",
        data={"message": caption, "access_token": access_token},
    )
    if response.status_code >= 400:
        raise MetaPublishError(f"Failed to publish text post: {response.text}")
    data = response.json()
    post_id = data.get("id")
    if not post_id:
        raise MetaPublishError(f"No post id returned: {data}")
    return post_id


async def _post_photo(
    client: httpx.AsyncClient, page_id: str, access_token: str, caption: str, image_url: str
) -> str:
    response = await client.post(
        f"{META_GRAPH_URL}/{page_id}/photos",
        data={
            "url": image_url,
            "caption": caption,
            "access_token": access_token,
        },
    )
    if response.status_code >= 400:
        raise MetaPublishError(f"Failed to publish photo post: {response.text}")
    data = response.json()
    post_id = data.get("post_id") or data.get("id")
    if not post_id:
        raise MetaPublishError(f"No post id returned: {data}")
    return post_id


async def _post_video(
    client: httpx.AsyncClient, page_id: str, access_token: str, caption: str, video_url: str
) -> str:
    """Classic Facebook video post (not a Reel)."""
    response = await client.post(
        f"{META_GRAPH_URL}/{page_id}/videos",
        data={
            "file_url": video_url,
            "description": caption,
            "access_token": access_token,
        },
    )
    if response.status_code >= 400:
        raise MetaPublishError(f"Failed to publish video post: {response.text}")
    data = response.json()
    post_id = data.get("id")
    if not post_id:
        raise MetaPublishError(f"No post id returned: {data}")
    return post_id


async def _post_reel(
    client: httpx.AsyncClient,
    page_id: str,
    access_token: str,
    caption: str,
    video_url: str,
) -> str:
    """Publish a Facebook Reel via the dedicated 3-phase video_reels API.

    This is a genuinely different Meta API from /videos - phases:
      1. start  -> get a video_id + upload_url
      2. transfer -> hand Meta the video (here: by URL, since our file is
         already hosted on R2; Meta fetches it server-side)
      3. finish -> mark the video as PUBLISHED with title/description
    """
    start_response = await client.post(
        f"{META_GRAPH_URL}/{page_id}/video_reels",
        data={"upload_phase": "start", "access_token": access_token},
    )
    if start_response.status_code >= 400:
        raise MetaPublishError(f"Failed to start Reel upload: {start_response.text}")
    start_data = start_response.json()
    video_id = start_data.get("video_id")
    upload_url = start_data.get("upload_url")
    if not video_id or not upload_url:
        raise MetaPublishError(f"Unexpected start response for Reel upload: {start_data}")

    transfer_response = await client.post(
        upload_url,
        headers={
            "Authorization": f"OAuth {access_token}",
            "file_url": video_url,
        },
    )
    if transfer_response.status_code >= 400:
        raise MetaPublishError(f"Failed to transfer Reel video: {transfer_response.text}")

    await _wait_for_reel_ready(client, video_id, access_token)

    finish_response = await client.post(
        f"{META_GRAPH_URL}/{page_id}/video_reels",
        data={
            "upload_phase": "finish",
            "video_id": video_id,
            "video_state": "PUBLISHED",
            "description": caption,
            "access_token": access_token,
        },
    )
    if finish_response.status_code >= 400:
        raise MetaPublishError(f"Failed to finish/publish Reel: {finish_response.text}")

    return video_id


async def _wait_for_reel_ready(
    client: httpx.AsyncClient,
    video_id: str,
    access_token: str,
) -> None:
    for _ in range(REEL_POLL_MAX_ATTEMPTS):
        response = await client.get(
            f"{META_GRAPH_URL}/{video_id}",
            params={"fields": "status", "access_token": access_token},
        )
        if response.status_code >= 400:
            # Not all accounts expose this field the same way; don't hard
            # fail the whole publish over a status check we can't make.
            return

        data = response.json()
        video_status = (data.get("status") or {}).get("video_status")

        if video_status == "ready":
            return
        if video_status == "error":
            raise MetaPublishError(f"Meta failed processing Reel {video_id}")

        import asyncio

        await asyncio.sleep(REEL_POLL_INTERVAL_SECONDS)

    raise MetaPublishError(f"Reel {video_id} timed out while processing")


def _upsert_social_account_unused() -> None:
    # placeholder to keep diff minimal - no-op
    return None


async def publish_post_target(
    db: Session,
    post_target_id: uuid.UUID,
    client: httpx.AsyncClient | None = None,
) -> PostTarget:
    post_target = (
        db.query(PostTarget)
        .options(
            joinedload(PostTarget.post),
            joinedload(PostTarget.social_account),
        )
        .filter(PostTarget.id == post_target_id)
        .first()
    )
    if post_target is None:
        raise MetaPublishError(f"PostTarget {post_target_id} not found")

    post = post_target.post
    social_account = post_target.social_account
    page_id = social_account.external_id
    access_token = decrypt_token(social_account.access_token_encrypted)
    caption = post.caption or ""

    media_items = sorted(post.media_items, key=lambda item: item.sort_order)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=120.0)

    try:
        if not media_items:
            external_post_id = await _post_text(client, page_id, access_token, caption)
        else:
            asset = media_items[0].media_asset
            media_url = _media_url_for_asset(asset)
            if _is_video(asset.content_type):
                if post.content_type == "reel":
                    external_post_id = await _post_reel(
                        client, page_id, access_token, caption, media_url
                    )
                else:
                    external_post_id = await _post_video(
                        client, page_id, access_token, caption, media_url
                    )
            else:
                external_post_id = await _post_photo(
                    client, page_id, access_token, caption, media_url
                )

        post_target.status = "published"
        post_target.external_post_id = external_post_id
        post_target.error_message = None
        post_target.published_at = datetime.now(UTC)

    except MetaPublishError as exc:
        post_target.status = "failed"
        post_target.error_message = str(exc)
    except httpx.HTTPError as exc:
        post_target.status = "failed"
        post_target.error_message = f"Network error while publishing: {exc}"
    finally:
        if owns_client:
            await client.aclose()

    db.commit()
    db.refresh(post_target)
    return post_target