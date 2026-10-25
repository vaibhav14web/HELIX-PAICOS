import os
import asyncio
import logging
import time
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.context_aggregator")


class ContextAggregator:
    """Orchestrates all context sub-engines and merges their responses.

    On ``context.request``, publishes requests to each sub-engine
    (browser, file, system, project) and waits for their responses.
    Maintains a cached context with configurable TTL to avoid redundant
    sub-engine calls.  Replaces the previous aggressive auto-refresh
    loop with event-driven updates only (per HSD §18, Agents §9).
    """

    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._cache_ttl = float(os.getenv("HELIX_CONTEXT_CACHE_TTL_SECONDS", "30"))
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}

        # Cached sub-engine responses
        self._cache: dict[str, Any] = {
            "browser": {},
            "files": {},
            "system": {},
            "project": {},
        }
        self._cache_timestamps: dict[str, float] = {}
        self._last_full_context_time: float = 0.0
        self._active = True

    async def start(self) -> None:
        self._event_handler_map = {
            # Incoming requests
            "context.request": self._handle_request,
            "context.update": self._handle_update,
            "system.state.change": self._handle_state_change,

            # Sub-engine responses (keep cache warm)
            "context.browser.provided": self._handle_browser_response,
            "context.browser.history.provided": self._handle_browser_history_response,
            "context.file.provided": self._handle_file_response,
            "context.file.recent.provided": self._handle_file_recent_response,
            "context.file.project_detected": self._handle_file_project_response,
            "system.monitor.provided": self._handle_system_response,
            "system.monitor.tick": self._handle_system_response,
            "project.opened": self._handle_project_opened,
            "project.switched": self._handle_project_switched,
            "project.closed": self._handle_project_closed,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())

        logger.info("Context Aggregator started")

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        logger.info("Context Aggregator stopped")

    # ── Primary Request Handler ────────────────────────────────

    async def _handle_request(self, event: HelixEvent) -> None:
        """Handle a full context request.

        If cached context is fresh (within TTL), return it immediately.
        Otherwise, fan-out requests to all sub-engines, wait briefly for
        responses, then return whatever we have.
        """
        session_id = event.payload.get("session_id", "default")
        force_refresh = event.payload.get("force", False)
        now = time.time()

        if not force_refresh and self.is_cache_fresh(now):
            # Return cached context immediately
            await self._publish_context(session_id, event.correlation_id)
            return

        # Fan-out: request fresh data from all sub-engines
        correlation = event.correlation_id
        project_path = self._cache.get("project", {}).get("path")
        if not project_path:
            from pathlib import Path
            project_path = str(Path(__file__).resolve().parents[2])

        await asyncio.gather(
            self._event_bus.publish_event(
                source="context_aggregator",
                event_type="context.browser.request",
                payload={},
                correlation_id=correlation,
            ),
            self._event_bus.publish_event(
                source="context_aggregator",
                event_type="system.monitor.request",
                payload={},
                correlation_id=correlation,
            ),
            self._event_bus.publish_event(
                source="context_aggregator",
                event_type="project.list",
                payload={},
                correlation_id=correlation,
            ),
            self._event_bus.publish_event(
                source="context_aggregator",
                event_type="context.file.recent",
                payload={"path": project_path},
                correlation_id=correlation,
            ),
            return_exceptions=True,
        )

        # Give sub-engines a brief window to respond (they run in-process
        # via the EventBus so responses should be near-instant, but we
        # yield to let the event loop deliver them).
        await asyncio.sleep(0.1)

        self._last_full_context_time = now
        await self._publish_context(session_id, event.correlation_id)

    async def _handle_update(self, event: HelixEvent) -> None:
        """Allow manual injection of context keys."""
        key = event.payload.get("key", "")
        value = event.payload.get("value")
        if key:
            self._cache[key] = value
            self._cache_timestamps[key] = time.time()

    async def _handle_state_change(self, event: HelixEvent) -> None:
        state = event.payload.get("state", "sleep")
        if state == "sleep":
            self._active = False
            logger.debug("Context Aggregator paused (sleep)")
        elif state == "conversation":
            self._active = True
            # Proactively refresh on conversation start
            await self._handle_request(event)
        else:
            self._active = True

    # ── Sub-Engine Response Handlers ───────────────────────────

    async def _handle_browser_response(self, event: HelixEvent) -> None:
        self._cache["browser"] = event.payload
        self._cache_timestamps["browser"] = time.time()

    async def _handle_browser_history_response(self, event: HelixEvent) -> None:
        self._cache["browser_history"] = event.payload
        self._cache_timestamps["browser_history"] = time.time()

    async def _handle_file_response(self, event: HelixEvent) -> None:
        self._cache["files"] = event.payload
        self._cache_timestamps["files"] = time.time()

    async def _handle_file_recent_response(self, event: HelixEvent) -> None:
        self._cache["recent_files"] = event.payload
        self._cache_timestamps["recent_files"] = time.time()

    async def _handle_file_project_response(self, event: HelixEvent) -> None:
        self._cache["file_project"] = event.payload
        self._cache_timestamps["file_project"] = time.time()

    async def _handle_system_response(self, event: HelixEvent) -> None:
        self._cache["system"] = event.payload
        self._cache_timestamps["system"] = time.time()

    async def _handle_project_opened(self, event: HelixEvent) -> None:
        self._cache["project"] = event.payload
        self._cache_timestamps["project"] = time.time()

    async def _handle_project_switched(self, event: HelixEvent) -> None:
        self._cache["project_switch"] = event.payload
        self._cache_timestamps["project_switch"] = time.time()
        # Invalidate file cache on project switch so next request gets fresh data
        self._cache_timestamps.pop("files", None)
        self._cache_timestamps.pop("recent_files", None)

    async def _handle_project_closed(self, event: HelixEvent) -> None:
        self._cache["project"] = {}
        self._cache_timestamps["project"] = time.time()

    # ── Helpers ────────────────────────────────────────────────

    def is_cache_fresh(self, now: float) -> bool:
        """Check if the full context cache is within TTL."""
        if self._last_full_context_time == 0.0:
            return False
        return (now - self._last_full_context_time) < self._cache_ttl

    async def _publish_context(self, session_id: str, correlation_id: str) -> None:
        """Merge all cached sub-engine data and publish the unified context."""
        context: dict[str, Any] = {
            "timestamp": time.time(),
            "session_id": session_id,
            "browser": self._cache.get("browser", {}),
            "browser_history": self._cache.get("browser_history", {}),
            "system": self._cache.get("system", {}),
            "project": self._cache.get("project", {}),
            "files": self._cache.get("files", {}),
            "recent_files": self._cache.get("recent_files", {}),
            "cache_ages": {
                k: time.time() - ts
                for k, ts in self._cache_timestamps.items()
            },
        }
        await self._event_bus.publish_event(
            source="context_aggregator",
            event_type="context.provided",
            payload={
                "session_id": session_id,
                "context": context,
            },
            correlation_id=correlation_id,
        )

    def get_consolidated_context(self, session_id: str = "default") -> dict[str, Any]:
        """Return the consolidated context directly from cache."""
        return {
            "timestamp": time.time(),
            "session_id": session_id,
            "browser": self._cache.get("browser", {}),
            "browser_history": self._cache.get("browser_history", {}),
            "system": self._cache.get("system", {}),
            "project": self._cache.get("project", {}),
            "files": self._cache.get("files", {}),
            "recent_files": self._cache.get("recent_files", {}),
            "cache_ages": {
                k: time.time() - ts
                for k, ts in self._cache_timestamps.items()
            },
        }
