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

META_AUTH_URL = "https://www.facebook.com/v21.0/dialog/oauth"
META_TOKEN_URL = "https://graph.facebook.com/v21.0/oauth/access_token"
META_GRAPH_URL = "https://graph.facebook.com/v21.0"

META_SCOPES = [
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "business_management",
]

_oauth_states: dict[str, uuid.UUID] = {}
_recent_results: dict[str, tuple[datetime, list[uuid.UUID]]] = {}
_STATE_RESULT_TTL = timedelta(seconds=60)


class MetaOAuthError(Exception):
    pass


def _get_cached_result(state: str) -> list[uuid.UUID] | None:
    """Return account ids from a recently-completed callback for this state, if any.

    Browsers sometimes fire the OAuth redirect twice (reload, warning page,
    etc). The first call consumes the state and connects the account(s);
    without this cache the second call would find the state gone and raise
    an error even though the accounts are already connected.
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
        "client_id": settings.META_APP_ID,
        "redirect_uri": settings.META_REDIRECT_URI,
        "scope": ",".join(META_SCOPES),
        "state": state,
        "response_type": "code",
    }
    return f"{META_AUTH_URL}?{urlencode(params)}"


def _resolve_user_from_state(state: str) -> uuid.UUID:
    user_id = _oauth_states.pop(state, None)
    if user_id is None:
        raise MetaOAuthError("Invalid or expired OAuth state")
    return user_id


async def _exchange_code_for_token(client: httpx.AsyncClient, code: str) -> dict:
    response = await client.get(
        META_TOKEN_URL,
        params={
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "redirect_uri": settings.META_REDIRECT_URI,
            "code": code,
        },
    )
    response.raise_for_status()
    return response.json()


async def _get_long_lived_token(client: httpx.AsyncClient, short_token: str) -> dict:
    response = await client.get(
        META_TOKEN_URL,
        params={
            "grant_type": "fb_exchange_token",
            "client_id": settings.META_APP_ID,
            "client_secret": settings.META_APP_SECRET,
            "fb_exchange_token": short_token,
        },
    )
    response.raise_for_status()
    return response.json()


async def _fetch_pages(client: httpx.AsyncClient, access_token: str) -> list[dict]:
    response = await client.get(
        f"{META_GRAPH_URL}/me/accounts",
        params={"access_token": access_token, "fields": "id,name,access_token"},
    )
    response.raise_for_status()
    return response.json().get("data", [])


async def _fetch_instagram_account(
    client: httpx.AsyncClient, page_id: str, page_token: str
) -> dict | None:
    response = await client.get(
        f"{META_GRAPH_URL}/{page_id}",
        params={
            "fields": "instagram_business_account{id,username}",
            "access_token": page_token,
        },
    )
    response.raise_for_status()
    data = response.json()
    return data.get("instagram_business_account")


def _upsert_social_account(
    db: Session,
    user_id: uuid.UUID,
    platform: str,
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
            SocialAccount.platform == platform,
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
            platform=platform,
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

        pages = await _fetch_pages(client, access_token)
        connected: list[SocialAccount] = []

        for page in pages:
            page_token = page["access_token"]
            fb_account = _upsert_social_account(
                db=db,
                user_id=user_id,
                platform="facebook",
                external_id=page["id"],
                display_name=page["name"],
                access_token=page_token,
                expires_at=expires_at,
                metadata={"page_id": page["id"]},
            )
            connected.append(fb_account)

            ig = await _fetch_instagram_account(client, page["id"], page_token)
            if ig:
                ig_account = _upsert_social_account(
                    db=db,
                    user_id=user_id,
                    platform="instagram",
                    external_id=ig["id"],
                    display_name=ig.get("username", ig["id"]),
                    access_token=page_token,
                    expires_at=expires_at,
                    metadata={"page_id": page["id"], "instagram_id": ig["id"]},
                )
                connected.append(ig_account)

        if not connected:
            raise MetaOAuthError("No Facebook pages or Instagram accounts found")

        _store_result(state, [account.id for account in connected])

        return connected
    finally:
        if owns_client:
            await client.aclose()


def get_decrypted_access_token(account: SocialAccount) -> str:
    return decrypt_token(account.access_token_encrypted)

def refresh_account_token(db: Session, account: SocialAccount) -> SocialAccount:
    current_token = get_decrypted_access_token(account)

    async def _do_refresh() -> dict:
        async with httpx.AsyncClient() as client:
            return await _get_long_lived_token(client, current_token)

    import asyncio

    long_lived = asyncio.run(_do_refresh())

    new_token = long_lived["access_token"]
    expires_in = long_lived.get("expires_in", 5184000)
    new_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)

    account.access_token_encrypted = encrypt_token(new_token)
    account.token_expires_at = new_expires_at
    db.commit()
    db.refresh(account)
    return account