from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.social_account import OAuthConnectResponse, SocialAccountResponse
from app.services import instagram_oauth_service, linkedin_oauth_service, meta_oauth_service

router = APIRouter(prefix="/social-accounts", tags=["social-accounts"])


def _frontend_redirect(platform: str, error: str | None = None) -> RedirectResponse:
    """Build the redirect sent to the user's browser after an OAuth callback.

    On success -> /dashboard?connected=<platform>
    On failure -> /dashboard?connect_error=<platform>&message=<detail>
    """
    base = settings.FRONTEND_URL.rstrip("/")
    if error:
        params = urlencode({"connect_error": platform, "message": error})
    else:
        params = urlencode({"connected": platform})
    return RedirectResponse(url=f"{base}/dashboard?{params}", status_code=status.HTTP_302_FOUND)


@router.get("", response_model=list[SocialAccountResponse])
def list_social_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[SocialAccountResponse]:
    return current_user.social_accounts


@router.get("/meta/connect", response_model=OAuthConnectResponse)
def meta_connect(current_user: User = Depends(get_current_user)) -> OAuthConnectResponse:
    url = meta_oauth_service.build_connect_url(current_user)
    return OAuthConnectResponse(authorization_url=url)


@router.get("/meta/callback")
async def meta_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        await meta_oauth_service.handle_callback(db, code, state)
    except meta_oauth_service.MetaOAuthError as exc:
        return _frontend_redirect("facebook", error=str(exc))
    return _frontend_redirect("facebook")


@router.get("/instagram/connect", response_model=OAuthConnectResponse)
def instagram_connect(current_user: User = Depends(get_current_user)) -> OAuthConnectResponse:
    url = instagram_oauth_service.build_connect_url(current_user)
    return OAuthConnectResponse(authorization_url=url)


@router.get("/instagram/callback")
async def instagram_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        await instagram_oauth_service.handle_callback(db, code, state)
    except instagram_oauth_service.InstagramOAuthError as exc:
        return _frontend_redirect("instagram", error=str(exc))
    return _frontend_redirect("instagram")


@router.get("/linkedin/connect", response_model=OAuthConnectResponse)
def linkedin_connect(current_user: User = Depends(get_current_user)) -> OAuthConnectResponse:
    url = linkedin_oauth_service.build_connect_url(current_user)
    return OAuthConnectResponse(authorization_url=url)


@router.get("/linkedin/callback")
async def linkedin_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        await linkedin_oauth_service.handle_callback(db, code, state)
    except linkedin_oauth_service.LinkedInOAuthError as exc:
        return _frontend_redirect("linkedin", error=str(exc))
    return _frontend_redirect("linkedin")