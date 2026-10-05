from __future__ import annotations

from typing import Literal

from pydantic import EmailStr, Field, field_validator

from app.core.schemas import BaseSchema

Topic = Literal["question", "personal_data", "problem", "partnership", "other"]


class ContactCreate(BaseSchema):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    topic: Topic = "question"
    message: str = Field(min_length=10, max_length=5000)
    locale: Literal["fr", "en"] = "fr"
    # Honeypot: hidden from people in the form, so only bots fill it in.
    website: str | None = Field(default=None, max_length=200)

    @field_validator("name", "message")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class ContactResponse(BaseSchema):
    detail: str
