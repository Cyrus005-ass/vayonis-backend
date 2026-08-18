import secrets
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import decrypt_token, encrypt_token
from app.models.social_account import SocialAccount
from app.models.user import User

LINKEDIN_AUTH_URL = "https://www.linkedin.com/oauth/v2/authorization"
LINKEDIN_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
LINKEDIN_API_URL = "https://api.linkedin.com/v2"

LINKEDIN_SCOPES = ["w_member_social", "openid", "profile", "email"]

_oauth_states: dict[str, uuid.UUID] = {}
_recent_results: dict[str, tuple[datetime, list[uuid.UUID]]] = {}
_STATE_RESULT_TTL = timedelta(seconds=60)


class LinkedInOAuthError(Exception):
    pass


def _get_cached_result(state: str) -> list[uuid.UUID] | None:
    """Return account ids from a recently-completed callback for this state, if any.

    Browsers sometimes fire the OAuth redirect twice (reload, warning page,
    etc). The first call consumes the state and connects the account;
    without this cache the second call would find the state gone and raise
    an error even though the account is already connected.
    """
    entry = _recent_results.get(state)
    if entry is None:
        return None
    timestamp, account_ids = entry
    if datetime.now(UTC) - timestamp > _STATE_RESULT_TTL:
        _recent_results.pop(state, None)
        return None
    return account_ids


def _store_result(state: str, account_ids: list[uuid.UUID]) -> None:
    _recent_results[state] = (datetime.now(UTC), account_ids)
    _cleanup_expired_results()


def _cleanup_expired_results() -> None:
    now = datetime.now(UTC)
    expired = [s for s, (ts, _) in _recent_results.items() if now - ts > _STATE_RESULT_TTL]
    for s in expired:
        _recent_results.pop(s, None)


def build_connect_url(user: User) -> str:
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = user.id
    params = {
        "response_type": "code",
        "client_id": settings.LINKEDIN_CLIENT_ID,
        "redirect_uri": settings.LINKEDIN_REDIRECT_URI,
        "scope": " ".join(LINKEDIN_SCOPES),
        "state": state,
    }
    return f"{LINKEDIN_AUTH_URL}?{urlencode(params)}"


def _resolve_user_from_state(state: str) -> uuid.UUID:
    user_id = _oauth_states.pop(state, None)
    if user_id is None:
        raise LinkedInOAuthError("Invalid or expired OAuth state")
    return user_id


async def _exchange_code_for_token(client: httpx.AsyncClient, code: str) -> dict:
    response = await client.post(
        LINKEDIN_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.LINKEDIN_REDIRECT_URI,
            "client_id": settings.LINKEDIN_CLIENT_ID,
            "client_secret": settings.LINKEDIN_CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    response.raise_for_status()
    return response.json()


async def _fetch_user_profile(client: httpx.AsyncClient, access_token: str) -> dict:
    response = await client.get(
        f"{LINKEDIN_API_URL}/userinfo",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    response.raise_for_status()
    return response.json()


def _upsert_social_account(
    db: Session,
    user_id: uuid.UUID,
    external_id: str,
    display_name: str,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
    metadata: dict | None = None,
) -> SocialAccount:
    account = (
        db.query(SocialAccount)
        .filter(
            SocialAccount.user_id == user_id,
            SocialAccount.platform == "linkedin",
            SocialAccount.external_id == external_id,
        )
        .first()
    )
    encrypted_access = encrypt_token(access_token)
    encrypted_refresh = encrypt_token(refresh_token) if refresh_token else None

    if account:
        account.display_name = display_name
        account.access_token_encrypted = encrypted_access
        account.refresh_token_encrypted = encrypted_refresh
        account.token_expires_at = expires_at
        account.metadata_json = metadata
    else:
        account = SocialAccount(
            user_id=user_id,
            platform="linkedin",
            external_id=external_id,
            display_name=display_name,
            access_token_encrypted=encrypted_access,
            refresh_token_encrypted=encrypted_refresh,
            token_expires_at=expires_at,
            metadata_json=metadata,
        )
        db.add(account)
    db.commit()
    db.refresh(account)
    return account


async def handle_callback(
    db: Session,
    code: str,
    state: str,
    client: httpx.AsyncClient | None = None,
) -> list[SocialAccount]:
    cached_ids = _get_cached_result(state)
    if cached_ids is not None:
        accounts = db.query(SocialAccount).filter(SocialAccount.id.in_(cached_ids)).all()
        if accounts:
            return accounts

    user_id = _resolve_user_from_state(state)
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient()

    try:
        token_data = await _exchange_code_for_token(client, code)
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 5184000)
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

        profile = await _fetch_user_profile(client, access_token)
        external_id = profile.get("sub") or profile.get("id")
        if not external_id:
            raise LinkedInOAuthError("Could not resolve LinkedIn user id")

        display_name = profile.get("name") or profile.get("email") or external_id
        account = _upsert_social_account(
            db=db,
            user_id=user_id,
            external_id=external_id,
            display_name=display_name,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            metadata={"email": profile.get("email")},
        )

        _store_result(state, [account.id])

        return [account]
    finally:
        if owns_client:
            await client.aclose()


def get_decrypted_access_token(account: SocialAccount) -> str:
    return decrypt_token(account.access_token_encrypted)