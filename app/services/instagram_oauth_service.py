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

INSTAGRAM_AUTH_URL = "https://www.instagram.com/oauth/authorize"
INSTAGRAM_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
INSTAGRAM_GRAPH_URL = "https://graph.instagram.com"

INSTAGRAM_SCOPES = [
    "instagram_business_basic",
    "instagram_business_content_publish",
]

_oauth_states: dict[str, uuid.UUID] = {}
_recent_results: dict[str, tuple[datetime, list[uuid.UUID]]] = {}
_STATE_RESULT_TTL = timedelta(seconds=60)


class InstagramOAuthError(Exception):
    pass


def _get_cached_result(state: str) -> list[uuid.UUID] | None:
    """Return account ids from a recently-completed callback for this state, if any.

    Browsers sometimes fire the OAuth redirect twice (reload, ngrok warning
    page, etc). The first call consumes the state and connects the account;
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
        "client_id": settings.INSTAGRAM_APP_ID,
        "redirect_uri": settings.INSTAGRAM_REDIRECT_URI,
        "scope": ",".join(INSTAGRAM_SCOPES),
        "state": state,
        "response_type": "code",
    }
    return f"{INSTAGRAM_AUTH_URL}?{urlencode(params)}"


def _resolve_user_from_state(state: str) -> uuid.UUID:
    user_id = _oauth_states.pop(state, None)
    if user_id is None:
        raise InstagramOAuthError("Invalid or expired OAuth state")
    return user_id


async def _exchange_code_for_token(client: httpx.AsyncClient, code: str) -> dict:
    response = await client.post(
        INSTAGRAM_TOKEN_URL,
        data={
            "client_id": settings.INSTAGRAM_APP_ID,
            "client_secret": settings.INSTAGRAM_APP_SECRET,
            "grant_type": "authorization_code",
            "redirect_uri": settings.INSTAGRAM_REDIRECT_URI,
            "code": code,
        },
    )
    response.raise_for_status()
    return response.json()


async def _get_long_lived_token(client: httpx.AsyncClient, short_token: str) -> dict:
    response = await client.get(
        f"{INSTAGRAM_GRAPH_URL}/access_token",
        params={
            "grant_type": "ig_exchange_token",
            "client_secret": settings.INSTAGRAM_APP_SECRET,
            "access_token": short_token,
        },
    )
    response.raise_for_status()
    return response.json()


async def _fetch_instagram_profile(client: httpx.AsyncClient, access_token: str) -> dict:
    response = await client.get(
        f"{INSTAGRAM_GRAPH_URL}/me",
        params={"fields": "id,username", "access_token": access_token},
    )
    response.raise_for_status()
    return response.json()


def _upsert_social_account(
    db: Session,
    user_id: uuid.UUID,
    external_id: str,
    display_name: str,
    access_token: str,
    expires_at: datetime | None,
    metadata: dict | None = None,
) -> SocialAccount:
    account = (
        db.query(SocialAccount)
        .filter(
            SocialAccount.user_id == user_id,
            SocialAccount.platform == "instagram",
            SocialAccount.external_id == external_id,
        )
        .first()
    )
    encrypted = encrypt_token(access_token)
    if account:
        account.display_name = display_name
        account.access_token_encrypted = encrypted
        account.token_expires_at = expires_at
        account.metadata_json = metadata
    else:
        account = SocialAccount(
            user_id=user_id,
            platform="instagram",
            external_id=external_id,
            display_name=display_name,
            access_token_encrypted=encrypted,
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
        short_token = token_data["access_token"]

        long_lived = await _get_long_lived_token(client, short_token)
        access_token = long_lived["access_token"]
        expires_in = long_lived.get("expires_in", 5184000)
        expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

        profile = await _fetch_instagram_profile(client, access_token)

        account = _upsert_social_account(
            db=db,
            user_id=user_id,
            external_id=profile["id"],
            display_name=profile.get("username", profile["id"]),
            access_token=access_token,
            expires_at=expires_at,
            metadata={"instagram_id": profile["id"]},
        )

        _store_result(state, [account.id])

        return [account]
    finally:
        if owns_client:
            await client.aclose()

from app.core.security import decrypt_token, encrypt_token  # ajoute decrypt_token à l'import existant


async def _refresh_long_lived_token(client: httpx.AsyncClient, current_token: str) -> dict:
    response = await client.get(
        f"{INSTAGRAM_GRAPH_URL}/refresh_access_token",
        params={
            "grant_type": "ig_refresh_token",
            "access_token": current_token,
        },
    )
    response.raise_for_status()
    return response.json()


def refresh_account_token(db: Session, account: SocialAccount) -> SocialAccount:
    current_token = decrypt_token(account.access_token_encrypted)

    async def _do_refresh() -> dict:
        async with httpx.AsyncClient() as client:
            return await _refresh_long_lived_token(client, current_token)

    import asyncio

    refreshed = asyncio.run(_do_refresh())

    new_token = refreshed["access_token"]
    expires_in = refreshed.get("expires_in", 5184000)
    new_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    account.access_token_encrypted = encrypt_token(new_token)
    account.token_expires_at = new_expires_at
    db.commit()
    db.refresh(account)
    return account