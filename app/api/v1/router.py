from fastapi import APIRouter

api_router = APIRouter()

from app.api.v1.routes import auth, posts, social_accounts, users, internal

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(social_accounts.router)
api_router.include_router(posts.router)
api_router.include_router(internal.router)
