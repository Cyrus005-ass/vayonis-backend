"""Facebook Page content publishing service.

Handles publishing a Post to a Facebook Page via the Graph API.
Supports:
- text-only posts (message on /{page_id}/feed)
- single image post (/{page_id}/photos)
- single video post (/{page_id}/videos)

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

META_GRAPH_URL = "https://graph.facebook.com/v21.0"

IMAGE_URL_TTL_SECONDS = 900  # 15 min
VIDEO_URL_TTL_SECONDS = 1800  # 30 min


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
        client = httpx.AsyncClient(timeout=60.0)

    try:
        if not media_items:
            external_post_id = await _post_text(client, page_id, access_token, caption)
        else:
            asset = media_items[0].media_asset
            media_url = _media_url_for_asset(asset)
            if _is_video(asset.content_type):
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