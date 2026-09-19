from ak5.routers.auth import router as auth_router
from ak5.routers.actors import router as actors_router
from ak5.routers.boards import router as boards_router
from ak5.routers.tickets import router as tickets_router
from ak5.routers.events import router as events_router

__all__ = [
    "auth_router",
    "actors_router",
    "boards_router",
    "tickets_router",
    "events_router",
]
