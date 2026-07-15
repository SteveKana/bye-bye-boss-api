"""Lightweight in-process async event bus for cross-module communication.

This is the *asynchronous* half of inter-module comms (the synchronous half is
the Gateway pattern — see `app/modules/*/gateway.py`). A module emits an event;
zero or more listeners in *other* modules react, without importing each other.

Define events:

    class UserRegistered(Event):
        user_id: uuid.UUID
        email: str

Subscribe (in a module's `listeners.py`, imported at startup):

    @on(UserRegistered)
    async def send_welcome(event: UserRegistered) -> None:
        ...

Emit (from a service):

    await event_bus.emit(UserRegistered(user_id=user.id, email=user.email))

Listeners run concurrently; a failing listener is logged and never breaks the
emitter. Handlers are decoupled from the DB transaction — emit *after* commit
for side effects that must only happen on success.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel

from app.core.logging import get_logger

logger = get_logger("app.events")


class Event(BaseModel):
    """Base class for domain events (immutable-ish payloads)."""


E = TypeVar("E", bound=Event)
Handler = Callable[[Event], Awaitable[None]]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[type[Event], list[Handler]] = defaultdict(list)

    def subscribe(
        self, event_type: type[E], handler: Callable[[E], Awaitable[None]]
    ) -> None:
        self._handlers[event_type].append(handler)  # type: ignore[arg-type]

    def on(
        self, event_type: type[E]
    ) -> Callable[[Callable[[E], Awaitable[None]]], Callable[[E], Awaitable[None]]]:
        def decorator(
            handler: Callable[[E], Awaitable[None]],
        ) -> Callable[[E], Awaitable[None]]:
            self.subscribe(event_type, handler)
            return handler

        return decorator

    async def emit(self, event: Event) -> None:
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(event) for h in handlers), return_exceptions=True
        )
        for handler, result in zip(handlers, results, strict=True):
            if isinstance(result, Exception):
                logger.error(
                    "event_listener_failed",
                    event=type(event).__name__,
                    handler=getattr(handler, "__qualname__", str(handler)),
                    error=str(result),
                )


event_bus = EventBus()

# Module-level decorator convenience: `from app.core.events import on`
on = event_bus.on
