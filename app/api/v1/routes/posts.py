import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from datetime import datetime, UTC
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.media_asset import MediaAsset
from app.models.post import Post
from app.models.post_media import PostMedia
from app.models.post_target import PostTarget
from app.models.social_account import SocialAccount
from app.schemas.post import (
    PostCreate,
    PostMediaResponse,
    PostResponse,
    PostTargetCreate,
    PostTargetResponse,
)
from app.services import publish_dispatcher, storage_service
from app.services.storage_service import StorageError

router = APIRouter(prefix="/posts", tags=["posts"])


@router.post("", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
def create_post(
    payload: PostCreate,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Post:
    post = Post(
        user_id=current_user.id,
        caption=payload.caption,
        content_type=payload.content_type,
        scheduled_at=payload.scheduled_at,
        status="scheduled" if payload.scheduled_at else "draft",
    )
    db.add(post)
    db.commit()
    db.refresh(post)
    return post


@router.post(
    "/{post_id}/media",
    response_model=PostMediaResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_media_to_post(
    post_id: str,
    file: UploadFile = File(...),
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostMedia:
    post = (
        db.query(Post)
        .filter(Post.id == post_id, Post.user_id == current_user.id)
        .first()
    )
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    content_type = file.content_type or "application/octet-stream"
    file.file.seek(0, 2)
    size_bytes = file.file.tell()
    file.file.seek(0)

    storage_key = storage_service.build_storage_key(current_user.id, file.filename or "upload")
    storage_service.upload_file(file.file, storage_key, content_type)

    asset = MediaAsset(
        user_id=current_user.id,
        filename=file.filename or storage_key.split("/")[-1],
        content_type=content_type,
        size_bytes=size_bytes,
        storage_key=storage_key,
    )
    db.add(asset)
    db.flush()

    sort_order = db.query(PostMedia).filter(PostMedia.post_id == post.id).count()
    post_media = PostMedia(
        post_id=post.id,
        media_asset_id=asset.id,
        sort_order=sort_order,
    )
    db.add(post_media)
    db.commit()
    db.refresh(post_media)
    return post_media


@router.post(
    "/{post_id}/targets",
    response_model=PostTargetResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_post_target(
    post_id: str,
    payload: PostTargetCreate,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostTarget:
    post = (
        db.query(Post)
        .filter(Post.id == post_id, Post.user_id == current_user.id)
        .first()
    )
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    social_account = (
        db.query(SocialAccount)
        .filter(
            SocialAccount.id == payload.social_account_id,
            SocialAccount.user_id == current_user.id,
        )
        .first()
    )
    if social_account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Social account not found"
        )

    target = PostTarget(
        post_id=post.id,
        social_account_id=social_account.id,
        platform=social_account.platform,
    )
    db.add(target)
    db.commit()
    db.refresh(target)
    return target


@router.post(
    "/post-targets/{post_target_id}/publish",
    response_model=PostTargetResponse,
)
async def publish_post_target(
    post_target_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PostTarget:
    target = (
        db.query(PostTarget)
        .filter(PostTarget.id == post_target_id)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PostTarget not found")

    if target.post.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")

    try:
        return await publish_dispatcher.publish_target(db, target)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post(
    "/{post_id}/publish",
    response_model=list[PostTargetResponse],
)
async def publish_post(
    post_id: str,
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[PostTarget]:
    """Publish a post to ALL of its targets simultaneously (Facebook, Instagram, LinkedIn, ...).

    Each target is published independently: a failure on one platform does
    not block the others. Every target's final status/error is returned so
    the caller can see exactly what happened on each platform.
    """
    post = (
        db.query(Post)
        .filter(Post.id == post_id, Post.user_id == current_user.id)
        .first()
    )
    if post is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    targets = db.query(PostTarget).filter(PostTarget.post_id == post.id).all()
    if not targets:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Post has no targets to publish to",
        )

    async def _publish_one(target: PostTarget) -> PostTarget:
        try:
            return await publish_dispatcher.publish_target(db, target)
        except Exception as exc:  # noqa: BLE001
            target.status = "failed"
            target.error_message = str(exc)
            db.commit()
            db.refresh(target)
            return target

    results = await asyncio.gather(*(_publish_one(target) for target in targets))

    post.status = "published" if all(t.status == "published" for t in results) else "partially_failed"
    db.commit()

    return list(results)