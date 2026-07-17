from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.config import get_settings
from app.core.dependencies import DBSession
from app.core.ratelimit import RateLimiter
from app.modules.auth.dependencies import CurrentUser
from app.modules.auth.schemas import (
    LoginRequest,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetRequestResponse,
    RefreshRequest,
    TokenPair,
    UserCreate,
    UserRead,
)
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

# Throttle unauthenticated, abuse-prone endpoints (brute force / spam signup).
login_limit = RateLimiter(times=10, seconds=60, scope="auth:login")
register_limit = RateLimiter(times=5, seconds=60, scope="auth:register")
refresh_limit = RateLimiter(times=20, seconds=60, scope="auth:refresh")
reset_limit = RateLimiter(times=5, seconds=60, scope="auth:reset")


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(register_limit)],
)
async def register(data: UserCreate, session: DBSession) -> UserRead:
    user = await AuthService(session).register(data)
    return UserRead.model_validate(user)


@router.post("/login", response_model=TokenPair, dependencies=[Depends(login_limit)])
async def login(data: LoginRequest, session: DBSession) -> TokenPair:
    return await AuthService(session).login(data.email, data.password)


@router.post(
    "/refresh", response_model=TokenPair, dependencies=[Depends(refresh_limit)]
)
async def refresh(data: RefreshRequest, session: DBSession) -> TokenPair:
    return await AuthService(session).refresh(data.refresh_token)


@router.post(
    "/reset-password/request",
    response_model=PasswordResetRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(reset_limit)],
)
async def request_password_reset(
    data: PasswordResetRequest, session: DBSession
) -> PasswordResetRequestResponse:
    # Always return 202 with the same body so callers can't enumerate accounts.
    token = await AuthService(session).request_password_reset(data.email)
    detail = "If the account exists, a reset link has been sent."
    if get_settings().DEBUG:
        return PasswordResetRequestResponse(detail=detail, reset_token=token)
    return PasswordResetRequestResponse(detail=detail)


@router.post(
    "/reset-password/confirm",
    response_model=MessageResponse,
    dependencies=[Depends(reset_limit)],
)
async def confirm_password_reset(
    data: PasswordResetConfirm, session: DBSession
) -> MessageResponse:
    await AuthService(session).confirm_password_reset(data.token, data.new_password)
    return MessageResponse(detail="Password updated.")


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)
