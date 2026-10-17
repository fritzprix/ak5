from ak5.server.routers.actors import router as actors_router
from ak5.server.routers.auth import router as auth_router
from ak5.server.routers.boards import router as boards_router
from ak5.server.routers.events import router as events_router
from ak5.server.routers.subscriptions import router as subscriptions_router
from ak5.server.routers.tickets import router as tickets_router

__all__ = [
    "actors_router",
    "auth_router",
    "boards_router",
    "events_router",
    "subscriptions_router",
    "tickets_router",
]

