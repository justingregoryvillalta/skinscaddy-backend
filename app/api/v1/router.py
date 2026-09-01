from fastapi import APIRouter

from app.api.v1 import (
    admin,
    auth,
    challenges,
    chats,
    feed,
    friends,
    honor,
    photos,
    rounds,
    scrambles,
    users,
    wallet,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(admin.router)
api_router.include_router(friends.router)
api_router.include_router(honor.router)
api_router.include_router(chats.router)
api_router.include_router(wallet.router)
api_router.include_router(rounds.router)
api_router.include_router(challenges.router)
api_router.include_router(feed.router)
api_router.include_router(photos.router)
api_router.include_router(scrambles.router)
