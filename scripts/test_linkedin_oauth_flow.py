"""End-to-end LinkedIn OAuth flow test with mocked external API."""

import asyncio
import os
import sys

from cryptography.fernet import Fernet

FERNET_KEY = Fernet.generate_key().decode()
os.environ["TOKEN_ENCRYPTION_KEY"] = FERNET_KEY
os.environ["LINKEDIN_CLIENT_ID"] = "test-linkedin-client-id"
os.environ["LINKEDIN_CLIENT_SECRET"] = "test-linkedin-client-secret"
os.environ["DATABASE_URL"] = "sqlite:///./test_linkedin_oauth.db"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from urllib.parse import parse_qs, urlparse

import httpx
from sqlalchemy.orm import Session

from app.core.database import Base, SessionLocal, engine
from app.models.social_account import SocialAccount
from app.schemas.auth import UserRegister
from app.services import auth_service, linkedin_oauth_service


def _mock_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "accessToken" in url and request.method == "POST":
        return httpx.Response(
            200,
            json={
                "access_token": "linkedin-access-token",
                "expires_in": 5184000,
                "refresh_token": "linkedin-refresh-token",
            },
        )
    if url.endswith("/userinfo"):
        return httpx.Response(
            200,
            json={
                "sub": "linkedin-user-789",
                "name": "Jean Dupont",
                "email": "jean@example.com",
            },
        )
    return httpx.Response(404, json={"error": "not mocked"})


def setup_db() -> Session:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    return SessionLocal()


def test_linkedin_oauth_flow() -> None:
    db = setup_db()
    user = auth_service.register_user(
        db,
        UserRegister(email="linkedin@test.com", password="password123", full_name="LinkedIn Tester"),
    )

    connect_url = linkedin_oauth_service.build_connect_url(user)
    parsed = urlparse(connect_url)
    state = parse_qs(parsed.query)["state"][0]
    assert "linkedin.com" in connect_url
    assert "w_member_social" in connect_url

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(_mock_handler))

    async def run_callback() -> list[SocialAccount]:
        return await linkedin_oauth_service.handle_callback(
            db, code="test-auth-code", state=state, client=mock_client
        )

    accounts = asyncio.run(run_callback())
    assert len(accounts) == 1
    account = accounts[0]
    assert account.platform == "linkedin"
    assert account.external_id == "linkedin-user-789"
    assert account.display_name == "Jean Dupont"
    assert linkedin_oauth_service.get_decrypted_access_token(account) == "linkedin-access-token"

    linkedin_oauth_service._oauth_states[state] = user.id
    asyncio.run(
        linkedin_oauth_service.handle_callback(db, code="test-auth-code", state=state, client=mock_client)
    )
    db_count = db.query(SocialAccount).filter(SocialAccount.user_id == user.id).count()
    assert db_count == 1

    print("LinkedIn OAuth flow: OK")


if __name__ == "__main__":
    test_linkedin_oauth_flow()
