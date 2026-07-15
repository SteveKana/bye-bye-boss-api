"""Shared FastAPI dependencies (annotated types) used across modules."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.pagination import PageParams

DBSession = Annotated[AsyncSession, Depends(get_session)]
Pagination = Annotated[PageParams, Depends()]
