"""Instagram content publishing service.

Handles the container -> publish flow of the Instagram Graph API
(graph.instagram.com), automatically adapting to the post's media:
- 1 image  -> simple image post
- 1 video  -> Reel
- 2+ media -> carousel (mixing images and videos is supported by Instagram)

Docs: https://developers.facebook.com/docs/instagram-platform/content-publishing/
"""

import asyncio
import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session, joinedload

from app.core.security import decrypt_token
from app.models.post_target import PostTarget
from app.services import storage_service

INSTAGRAM_GRAPH_URL = "https://graph.instagram.com/v21.0"

# How long a presigned media URL stays valid. Videos can take a while to be
# fetched and processed by Meta, so we give more headroom than for images.
IMAGE_URL_TTL_SECONDS = 900  # 15 min
VIDEO_URL_TTL_SECONDS = 1800  # 30 min

# Polling for video/reel container processing (Meta processes async).
CONTAINER_POLL_INTERVAL_SECONDS = 5
CONTAINER_POLL_MAX_ATTEMPTS = 36  # ~3 minutes


class InstagramPublishError(Exception):
    pass


def _is_video(content_type: str) -> bool:
    return content_type.startswith("video/")


def _media_url_for_asset(asset) -> str:
    ttl = VIDEO_URL_TTL_SECONDS if _is_video(asset.content_type) else IMAGE_URL_TTL_SECONDS
    return storage_service.generate_presigned_url(asset.storage_key, expires_in=ttl)


async def _create_container(
    client: httpx.AsyncClient,
    ig_user_id: str,
    access_token: str,
    params: dict,
) -> str:
    response = await client.post(
        f"{INSTAGRAM_GRAPH_URL}/{ig_user_id}/media",
        data={**params, "access_token": access_token},
    )
    if response.status_code >= 400:
        raise InstagramPublishError(f"Failed to create media container: {response.text}")
    data = response.json()
    container_id = data.get("id")
    if not container_id:
        raise InstagramPublishError(f"No container id returned: {data}")
    return container_id


async def _wait_for_container_ready(
    client: httpx.AsyncClient,
    container_id: str,
    access_token: str,
) -> None:
    """Poll a container's status until Meta finishes processing it.

    Only strictly necessary for video/reel containers, but harmless to call
    for images (usually returns FINISHED immediately).
    """
    for _ in range(CONTAINER_POLL_MAX_ATTEMPTS):
        response = await client.get(
            f"{INSTAGRAM_GRAPH_URL}/{container_id}",
            params={"fields": "status_code", "access_token": access_token},
        )
        response.raise_for_status()
        status_code = response.json().get("status_code")

        if status_code == "FINISHED":
            return
        if status_code == "ERROR":
            raise InstagramPublishError(f"Container {container_id} failed processing")
        # EXPIRED, IN_PROGRESS, PUBLISHED -> keep waiting on IN_PROGRESS only
        if status_code == "EXPIRED":
            raise InstagramPublishError(f"Container {container_id} expired before publishing")

        await asyncio.sleep(CONTAINER_POLL_INTERVAL_SECONDS)

    raise InstagramPublishError(f"Container {container_id} timed out while processing")


async def _publish_container(
    client: httpx.AsyncClient,
    ig_user_id: str,
    access_token: str,
    creation_id: str,
    retries: int = 3,
    retry_delay_seconds: float = 5.0,
) -> str:
    """Publish a container, retrying on Meta's transient "not ready yet" error.

    Even after a container reports status_code=FINISHED, Meta can briefly
    return error code 9007 / subcode 2207027 ("Media ID is not available")
    on media_publish - most often seen on multi-image carousels. A short
    retry with backoff absorbs this without failing the whole post.
    """
    last_error: str | None = None
    for attempt in range(retries):
        response = await client.post(
            f"{INSTAGRAM_GRAPH_URL}/{ig_user_id}/media_publish",
            data={"creation_id": creation_id, "access_token": access_token},
        )
        if response.status_code < 400:
            data = response.json()
            media_id = data.get("id")
            if not media_id:
                raise InstagramPublishError(f"No media id returned on publish: {data}")
            return media_id

        last_error = response.text
        is_not_ready = "2207027" in last_error or "Media ID is not available" in last_error
        if is_not_ready and attempt < retries - 1:
            await asyncio.sleep(retry_delay_seconds)
            continue
        raise InstagramPublishError(f"Failed to publish container: {last_error}")

    raise InstagramPublishError(f"Failed to publish container: {last_error}")


async def publish_post_target(
    db: Session,
    post_target_id: uuid.UUID,
    client: httpx.AsyncClient | None = None,
) -> PostTarget:
    """Publish a Post to Instagram for the given PostTarget.

    Updates the PostTarget's status, external_post_id, error_message and
    published_at fields in place and returns it.
    """
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
        raise InstagramPublishError(f"PostTarget {post_target_id} not found")

    post = post_target.post
    social_account = post_target.social_account
    ig_user_id = social_account.external_id
    access_token = decrypt_token(social_account.access_token_encrypted)

    # media_items is expected to be preloaded with .media_asset via relationship;
    # sort by sort_order to respect the order the user set in the UI.
    media_items = sorted(post.media_items, key=lambda item: item.sort_order)
    if not media_items:
        post_target.status = "failed"
        post_target.error_message = "Post has no media attached"
        db.commit()
        db.refresh(post_target)
        return post_target

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=60.0)

    try:
        if len(media_items) == 1:
            asset = media_items[0].media_asset
            media_url = _media_url_for_asset(asset)
            is_video = _is_video(asset.content_type)

            params = {"caption": post.caption or ""}
            if is_video:
                params["media_type"] = "REELS"
                params["video_url"] = media_url
            else:
                params["image_url"] = media_url

            container_id = await _create_container(client, ig_user_id, access_token, params)
            await _wait_for_container_ready(client, container_id, access_token)
            external_post_id = await _publish_container(client, ig_user_id, access_token, container_id)

        else:
            # Carousel: create a child container per media item, then a
            # parent CAROUSEL container referencing all children.
            child_ids: list[str] = []
            for item in media_items:
                asset = item.media_asset
                media_url = _media_url_for_asset(asset)
                is_video = _is_video(asset.content_type)

                child_params = {"is_carousel_item": "true"}
                if is_video:
                    child_params["media_type"] = "VIDEO"
                    child_params["video_url"] = media_url
                else:
                    child_params["image_url"] = media_url

                child_id = await _create_container(client, ig_user_id, access_token, child_params)
                await _wait_for_container_ready(client, child_id, access_token)
                child_ids.append(child_id)

            parent_params = {
                "media_type": "CAROUSEL",
                "caption": post.caption or "",
                "children": ",".join(child_ids),
            }
            parent_id = await _create_container(client, ig_user_id, access_token, parent_params)
            # The parent CAROUSEL container needs its own readiness check -
            # even once every child image is FINISHED, Meta can take a few
            # extra seconds to assemble the parent before it accepts a
            # media_publish call. Skipping this wait is what causes the
            # "Media ID is not available" error on multi-image carousels.
            await _wait_for_container_ready(client, parent_id, access_token)
            external_post_id = await _publish_container(client, ig_user_id, access_token, parent_id)

        post_target.status = "published"
        post_target.external_post_id = external_post_id
        post_target.error_message = None
        post_target.published_at = datetime.now(UTC)

    except InstagramPublishError as exc:
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