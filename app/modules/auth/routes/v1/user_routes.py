from __future__ import annotations

from fastapi import APIRouter

from app.core.dependencies import DBSession, Pagination
from app.core.pagination import Page
from app.modules.auth.dependencies import AdminUser
from app.modules.auth.models import User
from app.modules.auth.repository import UserRepository
from app.modules.auth.schemas import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=Page[UserRead])
async def list_users(
    session: DBSession,
    params: Pagination,
    _: AdminUser,
) -> Page[UserRead]:
    page = await UserRepository(session).paginate(
        params, order_by=User.created_at.desc()
    )
    return Page[UserRead].create(
        [UserRead.model_validate(u) for u in page.items], page.total, params
    )
