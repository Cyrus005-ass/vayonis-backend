"""End-to-end Meta OAuth flow test with mocked external API."""

import asyncio
import os
import sys

from cryptography.fernet import Fernet

FERNET_KEY = Fernet.generate_key().decode()
os.environ["TOKEN_ENCRYPTION_KEY"] = FERNET_KEY
os.environ["META_APP_ID"] = "test-meta-app-id"
os.environ["META_APP_SECRET"] = "test-meta-app-secret"
os.environ["DATABASE_URL"] = "sqlite:///./test_meta_oauth.db"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy.orm import Session

from app.core.database import Base, SessionLocal, engine
from app.models.social_account import SocialAccount
from app.schemas.auth import UserRegister
from app.services import auth_service, meta_oauth_service


def _mock_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "oauth/access_token" in url and "fb_exchange_token" not in url and request.method == "GET":
        return httpx.Response(200, json={"access_token": "short-lived-token"})
    if "fb_exchange_token" in url:
        return httpx.Response(200, json={"access_token": "long-lived-token", "expires_in": 5184000})
    if "/me/accounts" in url.split("?")[0]:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "page-123",
                        "name": "Test Page",
                        "access_token": "page-token-123",
                    }
                ]
            },
        )
    if "/page-123" in url:
        return httpx.Response(
            200,
            json={"instagram_business_account": {"id": "ig-456", "username": "test_ig"}},
        )
    return httpx.Response(404, json={"error": "not mocked"})


def setup_db() -> Session:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def test_meta_oauth_flow() -> None:
    db = setup_db()
    user = auth_service.register_user(
        db, UserRegister(email="meta@test.com", password="password123", full_name="Meta Tester")
    )

    connect_url = meta_oauth_service.build_connect_url(user)
    parsed = urlparse(connect_url)
    state = parse_qs(parsed.query)["state"][0]
    assert "facebook.com" in connect_url

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(_mock_handler))

    async def run_callback() -> list[SocialAccount]:
        return await meta_oauth_service.handle_callback(
            db, code="test-auth-code", state=state, client=mock_client
        )

    accounts = asyncio.run(run_callback())
    assert len(accounts) == 2
    platforms = {a.platform for a in accounts}
    assert platforms == {"facebook", "instagram"}

    fb = next(a for a in accounts if a.platform == "facebook")
    assert fb.external_id == "page-123"
    assert fb.display_name == "Test Page"
    assert meta_oauth_service.get_decrypted_access_token(fb) == "page-token-123"

    meta_oauth_service._oauth_states[state] = user.id
    asyncio.run(
        meta_oauth_service.handle_callback(db, code="test-auth-code", state=state, client=mock_client)
    )
    db_count = db.query(SocialAccount).filter(SocialAccount.user_id == user.id).count()
    assert db_count == 2

    print("Meta OAuth flow: OK")


if __name__ == "__main__":
    test_meta_oauth_flow()
