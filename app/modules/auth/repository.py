from __future__ import annotations

from sqlalchemy import func

from app.core.repository import BaseRepository
from app.modules.auth.models import User


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        return await self.find_one(email=email)

    async def get_by_email_ci(self, email: str) -> User | None:
        """Same, ignoring letter case (waitlist addresses are lower-cased)."""
        stmt = self._base_select(False).where(
            func.lower(User.email) == email.strip().lower()
        )
        return (await self.session.exec(stmt)).first()
