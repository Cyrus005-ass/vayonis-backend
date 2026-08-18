"""Quick HTTP smoke test for auth and LinkedIn connect route."""

import os
import sys

from cryptography.fernet import Fernet

os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["DATABASE_URL"] = "sqlite:///./test_http.db"
os.environ["LINKEDIN_CLIENT_ID"] = "test-client-id"
os.environ["LINKEDIN_CLIENT_SECRET"] = "test-client-secret"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app.core.database import Base, engine
from app.main import app

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

client = TestClient(app)

register = client.post(
    "/api/v1/auth/register",
    json={"email": "http@test.com", "password": "password123", "full_name": "HTTP Tester"},
)
assert register.status_code == 201, register.text
token = register.json()["access_token"]

me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
assert me.status_code == 200
assert me.json()["email"] == "http@test.com"

connect = client.get(
    "/api/v1/social-accounts/linkedin/connect",
    headers={"Authorization": f"Bearer {token}"},
)
assert connect.status_code == 200
assert "linkedin.com" in connect.json()["authorization_url"]
assert "w_member_social" in connect.json()["authorization_url"]

print("HTTP smoke test: OK")
