from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, computed_field

from app.core.remote_work import looks_hybrid


class JobOfferRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    source: str
    title: str
    company_name: str | None
    description: str | None
    location: str | None
    contract_type: str | None
    remote_policy: str | None
    salary_min: int | None
    salary_max: int | None
    salary_label: str | None
    daily_rate_min: int | None
    daily_rate_max: int | None
    url: str
    published_at: datetime | None
    is_full_remote: bool
    region: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_hybrid(self) -> bool:
        """Derived on the fly from the title/description (see
        core/remote_work.looks_hybrid) -- no stored column, so every existing
        offer is covered without a backfill."""
        return not self.is_full_remote and looks_hybrid(self.title, self.description)
