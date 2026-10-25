import asyncio
import logging
import uuid
from datetime import datetime, timezone
import threading
from typing import Callable, Coroutine, Any

from pydantic import BaseModel, Field

logger = logging.getLogger("helix.event_bus")


class HelixEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str
    event_type: str
    priority: int = 1
    payload: dict = Field(default_factory=dict)
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


EventHandler = Callable[[HelixEvent], Coroutine[Any, Any, None]]


class EventBus:
    def __init__(self):
        self._subscribers: dict[str, list[EventHandler]] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            if handler not in self._subscribers[event_type]:
                self._subscribers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            if event_type in self._subscribers:
                self._subscribers[event_type] = [h for h in self._subscribers[event_type] if h is not handler]

    async def _safe_invoke(self, handler: EventHandler, event: HelixEvent) -> None:
        try:
            res = handler(event)
            if asyncio.iscoroutine(res) or asyncio.isfuture(res):
                await res
        except Exception as exc:
            logger.exception(
                "Error executing event handler %s for event '%s': %s",
                getattr(handler, "__name__", str(handler)),
                event.event_type,
                exc,
            )
            if event.event_type != "module.error":
                await self.publish_event(
                    source="event_bus",
                    event_type="module.error",
                    payload={
                        "handler": getattr(handler, "__name__", str(handler)),
                        "original_event_type": event.event_type,
                        "error": str(exc),
                        "exception_type": exc.__class__.__name__,
                    },
                    correlation_id=event.correlation_id,
                )

    async def publish(self, event: HelixEvent) -> None:
        with self._lock:
            handlers = list(self._subscribers.get(event.event_type, []))
        if not handlers:
            return
        await asyncio.gather(*[self._safe_invoke(h, event) for h in handlers], return_exceptions=True)

    async def publish_event(self, source: str, event_type: str, payload: dict, priority: int = 1, correlation_id: str | None = None) -> HelixEvent:
        event = HelixEvent(
            source=source,
            event_type=event_type,
            priority=priority,
            payload=payload,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
        await self.publish(event)
        return event

