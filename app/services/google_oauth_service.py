"""Google sign-in service.

The frontend obtains an OAuth access token client-side via Google Identity
Services (google.accounts.oauth2.initTokenClient) and sends it to us. We
verify it by calling Google's userinfo endpoint directly - no separate
google-auth library needed, consistent with how the other OAuth services
in this project work.
"""

import uuid

import httpx
from sqlalchemy.orm import Session

from app.models.user import User
from app.services.auth_service import create_user_token, get_user_by_email

GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


class GoogleAuthError(Exception):
    pass


async def _fetch_google_profile(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if response.status_code >= 400:
        raise GoogleAuthError("Invalid or expired Google access token")

    data = response.json()
    if not data.get("email"):
        raise GoogleAuthError("Google account has no email")
    if not data.get("email_verified", True):
        raise GoogleAuthError("Google email is not verified")
    return data


def _get_or_create_user(db: Session, profile: dict) -> User:
    google_id = profile["sub"]
    email = profile["email"]

    # Already linked via google_id -> just log in.
    user = db.query(User).filter(User.google_id == google_id).first()
    if user:
        return user

    # Existing email/password account -> link Google to it.
    existing = get_user_by_email(db, email)
    if existing:
        existing.google_id = google_id
        db.commit()
        db.refresh(existing)
        return existing

    # Brand new account, created via Google, no password set.
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password=None,
        full_name=profile.get("name"),
        google_id=google_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


async def login_with_google(db: Session, access_token: str) -> str:
    """Verify the Google access token, get-or-create the user, return a JWT."""
    profile = await _fetch_google_profile(access_token)
    user = _get_or_create_user(db, profile)
    return create_user_token(user)