from ak5.routers.actors import router as actors_router
from ak5.routers.auth import router as auth_router
from ak5.routers.boards import router as boards_router
from ak5.routers.events import router as events_router
from ak5.routers.tickets import router as tickets_router

__all__ = [
    "actors_router",
    "auth_router",
    "boards_router",
    "events_router",
    "tickets_router",
]
