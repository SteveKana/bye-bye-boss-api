from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


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
    url: str
    published_at: datetime | None
