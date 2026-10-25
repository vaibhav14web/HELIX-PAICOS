import logging
from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.coding_companion")


class CodingCompanion:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}

    async def start(self) -> None:
        self._event_handler_map = {
            "context.file.provided": self._on_file_provided,
            "project.opened": self._on_project_opened,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())
        logger.info("Coding Companion started")

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        logger.info("Coding Companion stopped")

    async def _on_file_provided(self, event: HelixEvent) -> None:
        path = event.payload.get("path", "")
        info = event.payload.get("info", {})
        if not path:
            return
            
        logger.debug("Coding Companion analyzing file: %s", path)
        content = info.get("content", "") or ""
        
        # Look for code smells, stuck indicators, or unresolved tasks
        if "TODO" in content or "FIXME" in content:
            await self._event_bus.publish_event(
                source="coding_companion",
                event_type="productivity.suggestion.create",
                payload={
                    "action": "code_review",
                    "title": "Resolve TODOs in Workspace",
                    "description": f"File '{Path(path).name}' has outstanding TODOs. Click to review and resolve.",
                    "workflow": {"action": "code_review", "params": {"path": path}},
                    "status": "detected",
                }
            )

    async def _on_project_opened(self, event: HelixEvent) -> None:
        name = event.payload.get("name", "")
        logger.info("Coding Companion active for opened project: %s", name)
from pathlib import Path
from typing import Any
