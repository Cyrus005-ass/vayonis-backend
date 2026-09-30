import secrets
import uuid
from urllib.parse import urlencode

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.social_account import SocialAccount
from app.models.user import User

YOUTUBE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

_oauth_states: dict[str, uuid.UUID] = {}


class YouTubeOAuthError(Exception):
    pass


def build_connect_url(user: User) -> str:
    if not settings.ENABLE_YOUTUBE:
        raise YouTubeOAuthError("L'intégration YouTube n'est pas activée")

    if not settings.YOUTUBE_CLIENT_ID:
        raise YouTubeOAuthError("La configuration YouTube est incomplète")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = user.id
    params = {
        "client_id": settings.YOUTUBE_CLIENT_ID,
        "redirect_uri": settings.YOUTUBE_REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(YOUTUBE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{YOUTUBE_AUTH_URL}?{urlencode(params)}"


def _resolve_user_from_state(state: str) -> uuid.UUID:
    user_id = _oauth_states.pop(state, None)
    if user_id is None:
        raise YouTubeOAuthError("État OAuth YouTube invalide ou expiré")
    return user_id


async def handle_callback(
    db: Session,
    code: str,
    state: str,
    client=None,
) -> list[SocialAccount]:
    _resolve_user_from_state(state)
    raise YouTubeOAuthError("Le callback OAuth YouTube n'est pas encore implémenté")


def refresh_account_token(db: Session, account: SocialAccount) -> SocialAccount:
    raise YouTubeOAuthError("Le rafraîchissement des tokens YouTube n'est pas encore implémenté")
