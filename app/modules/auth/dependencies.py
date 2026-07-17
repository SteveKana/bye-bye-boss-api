from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.dependencies import DBSession
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.security import decode_token
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository

_bearer = HTTPBearer(auto_error=False, description="JWT access token")
BearerCreds = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)]


async def get_current_user(session: DBSession, creds: BearerCreds) -> User:
    if creds is None:
        raise UnauthorizedError("Authentication required.")
    payload = decode_token(creds.credentials, expected_type="access")
    user = await UserRepository(session).get(uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_current_admin(user: CurrentUser) -> User:
    if not user.isadmin:
        raise ForbiddenError("Admin privileges required.")
    return user


AdminUser = Annotated[User, Depends(get_current_admin)]
