"""Generic async repository: CRUD + pagination over a SQLModel table.

Subclass and set `model`. Soft-delete is automatic when the model uses
`SoftDeleteMixin`: reads exclude deleted rows and `delete()` stamps
`deleted_at` instead of issuing a DELETE.

    class ItemRepository(BaseRepository[Item]):
        model = Item
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.exceptions import NotFoundError
from app.core.models import SoftDeleteMixin, utcnow
from app.core.pagination import Page, PageParams

ModelT = TypeVar("ModelT")


class BaseRepository(Generic[ModelT]):
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---- internal helpers ------------------------------------------------
    @property
    def _soft_delete(self) -> bool:
        return issubclass(self.model, SoftDeleteMixin)

    def _base_select(self, include_deleted: bool = False):
        stmt = select(self.model)
        if self._soft_delete and not include_deleted:
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return stmt

    def _apply_filters(self, stmt, filters: dict[str, Any] | None):
        for field, value in (filters or {}).items():
            stmt = stmt.where(getattr(self.model, field) == value)
        return stmt

    # ---- reads -----------------------------------------------------------
    async def get(
        self, id: uuid.UUID, *, include_deleted: bool = False
    ) -> ModelT | None:
        stmt = self._base_select(include_deleted).where(self.model.id == id)  # type: ignore[attr-defined]
        return (await self.session.exec(stmt)).first()

    async def get_or_404(self, id: uuid.UUID, **filters: Any) -> ModelT:
        stmt = self._apply_filters(
            self._base_select().where(self.model.id == id),
            filters,  # type: ignore[attr-defined]
        )
        obj = (await self.session.exec(stmt)).first()
        if obj is None:
            raise NotFoundError(f"{self.model.__name__} not found.")
        return obj

    async def find_one(self, **filters: Any) -> ModelT | None:
        stmt = self._apply_filters(self._base_select(), filters)
        return (await self.session.exec(stmt)).first()

    async def list(
        self,
        *,
        filters: dict[str, Any] | None = None,
        order_by: Any = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Sequence[ModelT]:
        stmt = self._apply_filters(self._base_select(), filters)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        if offset is not None:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return (await self.session.exec(stmt)).all()

    async def count(self, *, filters: dict[str, Any] | None = None) -> int:
        stmt = self._apply_filters(self._base_select(), filters)
        count_stmt = select(func.count()).select_from(stmt.subquery())
        return (await self.session.exec(count_stmt)).one()

    async def paginate(
        self,
        params: PageParams,
        *,
        filters: dict[str, Any] | None = None,
        order_by: Any = None,
    ) -> Page[ModelT]:
        total = await self.count(filters=filters)
        items = await self.list(
            filters=filters,
            order_by=order_by,
            limit=params.limit,
            offset=params.offset,
        )
        return Page.create(items, total, params)

    # ---- writes ----------------------------------------------------------
    async def create(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def update(self, obj: ModelT, data: dict[str, Any]) -> ModelT:
        for field, value in data.items():
            setattr(obj, field, value)
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        if self._soft_delete:
            obj.deleted_at = utcnow()  # type: ignore[attr-defined]
            self.session.add(obj)
            await self.session.flush()
        else:
            await self.session.delete(obj)
            await self.session.flush()
