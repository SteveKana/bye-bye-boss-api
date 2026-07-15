"""Shared Pydantic schema (DTO) base classes for request/response bodies."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BaseSchema(BaseModel):
    """Base for API DTOs. Reads from ORM attributes and ignores extras."""

    model_config = ConfigDict(from_attributes=True)


class Message(BaseSchema):
    """Generic message envelope for simple responses."""

    detail: str
