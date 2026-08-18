from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.social_account import OAuthCallbackResponse, OAuthConnectResponse, SocialAccountResponse
from app.services import instagram_oauth_service, linkedin_oauth_service, meta_oauth_service

router = APIRouter(prefix="/social-accounts", tags=["social-accounts"])


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


@router.get("/meta/callback", response_model=OAuthCallbackResponse)
async def meta_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
) -> OAuthCallbackResponse:
    try:
        accounts = await meta_oauth_service.handle_callback(db, code, state)
    except meta_oauth_service.MetaOAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return OAuthCallbackResponse(connected_accounts=accounts)

@router.get("/instagram/connect", response_model=OAuthConnectResponse)
def instagram_connect(current_user: User = Depends(get_current_user)) -> OAuthConnectResponse:
    url = instagram_oauth_service.build_connect_url(current_user)
    return OAuthConnectResponse(authorization_url=url)


@router.get("/instagram/callback", response_model=OAuthCallbackResponse)
async def instagram_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
) -> OAuthCallbackResponse:
    try:
        accounts = await instagram_oauth_service.handle_callback(db, code, state)
    except instagram_oauth_service.InstagramOAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return OAuthCallbackResponse(connected_accounts=accounts)


@router.get("/linkedin/connect", response_model=OAuthConnectResponse)
def linkedin_connect(current_user: User = Depends(get_current_user)) -> OAuthConnectResponse:
    url = linkedin_oauth_service.build_connect_url(current_user)
    return OAuthConnectResponse(authorization_url=url)


@router.get("/linkedin/callback", response_model=OAuthCallbackResponse)
async def linkedin_callback(
    code: str = Query(...),
    state: str = Query(...),
    db: Session = Depends(get_db),
) -> OAuthCallbackResponse:
    try:
        accounts = await linkedin_oauth_service.handle_callback(db, code, state)
    except linkedin_oauth_service.LinkedInOAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return OAuthCallbackResponse(connected_accounts=accounts)
