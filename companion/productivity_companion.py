import logging
from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.productivity_companion")

class ProductivityCompanion:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}

    async def start(self) -> None:
        self._event_handler_map = {
            "automation.completed": self._on_automation_completed,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())
        logger.info("Productivity Companion started")

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        logger.info("Productivity Companion stopped")

    async def _on_automation_completed(self, event: HelixEvent) -> None:
        action = event.payload.get("action", "")
        # Track automation completions and check if we can suggest shortcuts to reduce manual steps
        if action:
            logger.info("Productivity Companion tracking action: %s", action)
from typing import Any
