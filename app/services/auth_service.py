import uuid

from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import UserRegister


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: str | uuid.UUID) -> User | None:
    if isinstance(user_id, str):
        user_id = uuid.UUID(user_id)
    return db.query(User).filter(User.id == user_id).first()


def register_user(db: Session, data: UserRegister) -> User:
    existing = get_user_by_email(db, data.email)
    if existing:
        raise ValueError("Email already registered")

    onboarding_json = data.onboarding.model_dump() if data.onboarding else None

    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        full_name=data.full_name,
        onboarding_json=onboarding_json,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if not user or not user.hashed_password or not verify_password(password, user.hashed_password):
        return None
    return user


def create_user_token(user: User) -> str:
    return create_access_token(str(user.id))