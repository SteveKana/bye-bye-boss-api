"""Auth module — public surface.

Other modules import ONLY from here (the module's `__init__`), never from its
internal files:

    from app.modules.auth import CurrentUser, AdminUser, AuthGateway, UserRegistered
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.module import Module

# Import side effects: register models (Alembic), event listeners.
from app.modules.auth import listeners as listeners  # noqa: F401
from app.modules.auth import models as models  # noqa: F401
from app.modules.auth.dependencies import AdminUser, CurrentUser, get_current_user
from app.modules.auth.events import UserDeleted, UserRegistered
from app.modules.auth.gateway import AuthGateway
from app.modules.auth.models import SubscriptionPlan
from app.modules.auth.routes.v1 import auth_routes, user_routes
from app.modules.auth.schemas import PublicUser
from app.modules.auth.seed import seed_admin

_router = APIRouter()
_router.include_router(auth_routes.router)
_router.include_router(user_routes.router)

module = Module(
    name="auth",
    router=_router,
    order=10,
    on_startup=seed_admin,
    tags=["auth"],
)

__all__ = [
    "module",
    "CurrentUser",
    "AdminUser",
    "get_current_user",
    "AuthGateway",
    "PublicUser",
    "SubscriptionPlan",
    "UserRegistered",
    "UserDeleted",
]
