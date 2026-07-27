from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.config import get_settings
from app.core.dependencies import DBSession
from app.core.ratelimit import RateLimiter
from app.modules.auth.dependencies import CurrentUser
from app.modules.auth.schemas import (
    ChangePasswordRequest,
    EmailVerifyRequest,
    LoginRequest,
    MessageResponse,
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetRequestResponse,
    RefreshRequest,
    ResendVerificationRequest,
    TokenPair,
    UserCreate,
    UserRead,
    UserUpdate,
)
from app.modules.auth.service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])

# Throttle unauthenticated, abuse-prone endpoints (brute force / spam signup).
login_limit = RateLimiter(times=10, seconds=60, scope="auth:login")
register_limit = RateLimiter(times=5, seconds=60, scope="auth:register")
refresh_limit = RateLimiter(times=20, seconds=60, scope="auth:refresh")
reset_limit = RateLimiter(times=5, seconds=60, scope="auth:reset")
password_limit = RateLimiter(times=5, seconds=60, scope="auth:change-password")
verify_limit = RateLimiter(times=10, seconds=60, scope="auth:verify")


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
    token = await AuthService(session).request_password_reset(data.email, data.locale)
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


@router.post(
    "/verify-email",
    response_model=MessageResponse,
    dependencies=[Depends(verify_limit)],
)
async def verify_email(data: EmailVerifyRequest, session: DBSession) -> MessageResponse:
    await AuthService(session).verify_email(data.token)
    return MessageResponse(detail="Email verified.")


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(verify_limit)],
)
async def resend_verification(
    data: ResendVerificationRequest, session: DBSession
) -> MessageResponse:
    # Always the same 202 body so callers cannot probe who is registered.
    await AuthService(session).resend_verification(data.email, data.locale)
    return MessageResponse(
        detail="If the account exists and is unverified, an email has been sent."
    )


@router.get("/me", response_model=UserRead)
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.patch("/me", response_model=UserRead)
async def update_me(
    data: UserUpdate, user: CurrentUser, session: DBSession
) -> UserRead:
    updated = await AuthService(session).update_profile(user, data)
    return UserRead.model_validate(updated)


@router.post(
    "/change-password",
    response_model=MessageResponse,
    dependencies=[Depends(password_limit)],
)
async def change_password(
    data: ChangePasswordRequest, user: CurrentUser, session: DBSession
) -> MessageResponse:
    await AuthService(session).change_password(
        user, data.current_password, data.new_password
    )
    return MessageResponse(detail="Password updated.")
