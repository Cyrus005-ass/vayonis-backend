from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql://vayonis:vayonis@localhost:5432/vayonis"
    REDIS_URL: str = "redis://localhost:6379/0"

    SECRET_KEY: str = "change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    TOKEN_ENCRYPTION_KEY: str = "change-me-32-byte-base64-fernet-key"

    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_REDIRECT_URI: str = "http://localhost:8000/api/v1/social-accounts/meta/callback"

    INSTAGRAM_APP_ID: str = ""
    INSTAGRAM_APP_SECRET: str = ""
    INSTAGRAM_REDIRECT_URI: str = "http://localhost:8000/api/v1/social-accounts/instagram/callback"

    LINKEDIN_CLIENT_ID: str = ""
    LINKEDIN_CLIENT_SECRET: str = ""
    LINKEDIN_REDIRECT_URI: str = "http://localhost:8000/api/v1/social-accounts/linkedin/callback"

    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_BUCKET_NAME: str = "vayonis-media"
    S3_REGION: str = "auto"
    S3_PUBLIC_BASE_URL: str = ""

    ENABLE_AI: bool = False
    ENABLE_ANALYTICS: bool = False
    ENABLE_BILLING: bool = False
    ENABLE_TIKTOK: bool = False
    ENABLE_YOUTUBE: bool = False

    FRONTEND_URL: str = "http://localhost:3000"

    @field_validator("DATABASE_URL", "REDIS_URL", "S3_ENDPOINT_URL", "S3_PUBLIC_BASE_URL", mode="before")
    @classmethod
    def _strip_quotes(cls, value: str) -> str:
        if not isinstance(value, str):
            return value
        value = value.strip()
        for prefix in ("DATABASE_URL=", "REDIS_URL=", "S3_ENDPOINT_URL=", "S3_PUBLIC_BASE_URL="):
            if value.startswith(prefix):
                value = value[len(prefix):]
                break
        return value.strip().strip('"').strip("'")


settings = Settings()
