import secrets
import uuid
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.social_account import SocialAccount
from app.models.user import User

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_SCOPES = ["user.info.basic", "video.publish"]

_oauth_states: dict[str, uuid.UUID] = {}


class TikTokOAuthError(Exception):
    pass


def build_connect_url(user: User) -> str:
    if not settings.ENABLE_TIKTOK:
        raise TikTokOAuthError("L'intégration TikTok n'est pas activée")

    if not settings.TIKTOK_CLIENT_KEY:
        raise TikTokOAuthError("La configuration TikTok est incomplète")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = user.id
    params = {
        "client_key": settings.TIKTOK_CLIENT_KEY,
        "redirect_uri": settings.TIKTOK_REDIRECT_URI,
        "response_type": "code",
        "scope": ",".join(TIKTOK_SCOPES),
        "state": state,
    }
    return f"{TIKTOK_AUTH_URL}?{urlencode(params)}"


def _resolve_user_from_state(state: str) -> uuid.UUID:
    user_id = _oauth_states.pop(state, None)
    if user_id is None:
        raise TikTokOAuthError("État OAuth TikTok invalide ou expiré")
    return user_id


async def handle_callback(
    db: Session,
    code: str,
    state: str,
    client=None,
) -> list[SocialAccount]:
    _resolve_user_from_state(state)
    raise TikTokOAuthError("Le callback OAuth TikTok n'est pas encore implémenté")


def refresh_account_token(db: Session, account: SocialAccount) -> SocialAccount:
    raise TikTokOAuthError("Le rafraîchissement des tokens TikTok n'est pas encore implémenté")
