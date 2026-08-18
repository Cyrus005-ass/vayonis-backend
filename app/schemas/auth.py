from pydantic import BaseModel, EmailStr, Field


class OnboardingAnswers(BaseModel):
    profile_type: str | None = None
    age_range: str | None = None
    goal: str | None = None
    current_platforms: list[str] = Field(default_factory=list)


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str | None = None
    onboarding: OnboardingAnswers | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    # Access token obtained client-side via Google Identity Services
    # (google.accounts.oauth2.initTokenClient) - NOT an ID token.
    access_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"