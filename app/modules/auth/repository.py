from __future__ import annotations

from app.core.repository import BaseRepository
from app.modules.auth.models import User


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        return await self.find_one(email=email)
