"""LinkedIn content publishing service.

Handles publishing a Post to LinkedIn via the UGC Posts API.

Text-only posts are published directly. Posts with media go through
LinkedIn's asset upload flow:
  1. Register an upload (POST /v2/assets?action=registerUpload) -> get an
     upload URL and an asset URN.
  2. Upload the raw file bytes to that URL.
  3. Create the UGC post referencing the asset URN(s).

LinkedIn does not allow mixing images and videos in the same post, and
multi-video posts aren't supported either - only a single video, or one to
several images.
"""

import uuid
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session, joinedload

from app.core.security import decrypt_token
from app.models.post_target import PostTarget
from app.services import storage_service

LINKEDIN_UGC_POSTS_URL = "https://api.linkedin.com/v2/ugcPosts"
LINKEDIN_ASSETS_URL = "https://api.linkedin.com/v2/assets"

IMAGE_RECIPE = "urn:li:digitalmediaRecipe:feedshare-image"
VIDEO_RECIPE = "urn:li:digitalmediaRecipe:feedshare-video"

# Media URL TTL: long enough to cover download + re-upload to LinkedIn.
MEDIA_URL_TTL_SECONDS = 1800  # 30 min

# Polling for video asset processing (LinkedIn processes video async).
ASSET_POLL_INTERVAL_SECONDS = 5
ASSET_POLL_MAX_ATTEMPTS = 36  # ~3 minutes


class LinkedInPublishError(Exception):
    pass


def _is_video(content_type: str) -> bool:
    return content_type.startswith("video/")


async def _register_upload(
    client: httpx.AsyncClient,
    member_id: str,
    access_token: str,
    recipe: str,
) -> tuple[str, str]:
    """Register a media upload and return (upload_url, asset_urn)."""
    body = {
        "registerUploadRequest": {
            "recipes": [recipe],
            "owner": f"urn:li:person:{member_id}",
            "serviceRelationships": [
                {
                    "relationshipType": "OWNER",
                    "identifier": "urn:li:userGeneratedContent",
                }
            ],
        }
    }
    response = await client.post(
        f"{LINKEDIN_ASSETS_URL}?action=registerUpload",
        json=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        },
    )
    if response.status_code >= 400:
        raise LinkedInPublishError(f"Failed to register LinkedIn upload: {response.text}")

    data = response.json()
    try:
        upload_mechanism = data["value"]["uploadMechanism"][
            "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest"
        ]
        upload_url = upload_mechanism["uploadUrl"]
        asset_urn = data["value"]["asset"]
    except KeyError as exc:
        raise LinkedInPublishError(f"Unexpected registerUpload response: {data}") from exc

    return upload_url, asset_urn


async def _upload_media_binary(
    client: httpx.AsyncClient,
    upload_url: str,
    access_token: str,
    content: bytes,
) -> None:
    response = await client.put(
        upload_url,
        content=content,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if response.status_code >= 400:
        raise LinkedInPublishError(f"Failed to upload media to LinkedIn: {response.text}")


async def _wait_for_asset_ready(
    client: httpx.AsyncClient,
    asset_urn: str,
    access_token: str,
) -> None:
    """Poll an asset until LinkedIn finishes processing it.

    Mainly relevant for video; images are usually available right away, but
    polling is harmless and keeps the video/image paths consistent.
    """
    asset_id = asset_urn.rsplit(":", 1)[-1]
    for _ in range(ASSET_POLL_MAX_ATTEMPTS):
        response = await client.get(
            f"{LINKEDIN_ASSETS_URL}/{asset_id}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Restli-Protocol-Version": "2.0.0",
            },
        )
        if response.status_code >= 400:
            # Some accounts/plans don't expose this GET; don't hard-fail the
            # whole publish over a status check we can't make.
            return

        data = response.json()
        recipes = data.get("recipes", [])
        statuses = [r.get("status") for r in recipes]

        if any(s == "AVAILABLE" for s in statuses):
            return
        if any(s == "ERROR" for s in statuses):
            raise LinkedInPublishError(f"LinkedIn failed processing asset {asset_urn}")

        await asyncio_sleep(ASSET_POLL_INTERVAL_SECONDS)

    raise LinkedInPublishError(f"Asset {asset_urn} timed out while processing")


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


async def _download_media_bytes(client: httpx.AsyncClient, asset) -> bytes:
    media_url = storage_service.generate_presigned_url(
        asset.storage_key, expires_in=MEDIA_URL_TTL_SECONDS
    )
    response = await client.get(media_url)
    if response.status_code >= 400:
        raise LinkedInPublishError(f"Failed to download media for upload: {response.text}")
    return response.content


async def _post_text(client: httpx.AsyncClient, member_id: str, access_token: str, caption: str) -> str:
    body = {
        "author": f"urn:li:person:{member_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": caption},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    return await _submit_ugc_post(client, access_token, body)


async def _post_with_media(
    client: httpx.AsyncClient,
    member_id: str,
    access_token: str,
    caption: str,
    media_category: str,
    asset_urns: list[str],
) -> str:
    body = {
        "author": f"urn:li:person:{member_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": caption},
                "shareMediaCategory": media_category,
                "media": [{"status": "READY", "media": urn} for urn in asset_urns],
            }
        },
        "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
    }
    return await _submit_ugc_post(client, access_token, body)


async def _submit_ugc_post(client: httpx.AsyncClient, access_token: str, body: dict) -> str:
    response = await client.post(
        LINKEDIN_UGC_POSTS_URL,
        json=body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        },
    )
    if response.status_code >= 400:
        raise LinkedInPublishError(f"Failed to publish LinkedIn post: {response.text}")

    post_id = response.headers.get("x-restli-id")
    if not post_id:
        data = response.json() if response.content else {}
        post_id = data.get("id")
    if not post_id:
        raise LinkedInPublishError("No post id returned by LinkedIn")
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
        raise LinkedInPublishError(f"PostTarget {post_target_id} not found")

    post = post_target.post
    social_account = post_target.social_account
    member_id = social_account.external_id
    access_token = decrypt_token(social_account.access_token_encrypted)
    caption = post.caption or ""

    media_items = sorted(post.media_items, key=lambda item: item.sort_order)

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=120.0)

    try:
        if not media_items:
            external_post_id = await _post_text(client, member_id, access_token, caption)

        else:
            assets = [item.media_asset for item in media_items]
            video_count = sum(1 for a in assets if _is_video(a.content_type))
            image_count = len(assets) - video_count

            if video_count and image_count:
                raise LinkedInPublishError(
                    "LinkedIn does not support mixing images and videos in the same post."
                )
            if video_count > 1:
                raise LinkedInPublishError(
                    "LinkedIn does not support multiple videos in the same post."
                )

            recipe = VIDEO_RECIPE if video_count else IMAGE_RECIPE
            media_category = "VIDEO" if video_count else "IMAGE"

            asset_urns: list[str] = []
            for asset in assets:
                upload_url, asset_urn = await _register_upload(client, member_id, access_token, recipe)
                content = await _download_media_bytes(client, asset)
                await _upload_media_binary(client, upload_url, access_token, content)
                await _wait_for_asset_ready(client, asset_urn, access_token)
                asset_urns.append(asset_urn)

            external_post_id = await _post_with_media(
                client, member_id, access_token, caption, media_category, asset_urns
            )

        post_target.status = "published"
        post_target.external_post_id = external_post_id
        post_target.error_message = None
        post_target.published_at = datetime.now(UTC)

    except LinkedInPublishError as exc:
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