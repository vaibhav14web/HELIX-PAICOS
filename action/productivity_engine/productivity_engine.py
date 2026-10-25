import os
import asyncio
import logging
import uuid
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.productivity_engine")


class PatternObservation:
    def __init__(self, action: str, category: str, description: str, frequency: int):
        self.action = action
        self.category = category
        self.description = description
        self.frequency = frequency
        self.first_observed = datetime.now(timezone.utc).isoformat()
        self.last_observed = datetime.now(timezone.utc).isoformat()
        self.suggested = False
        self.automated = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "category": self.category,
            "description": self.description,
            "frequency": self.frequency,
            "first_observed": self.first_observed,
            "last_observed": self.last_observed,
            "suggested": self.suggested,
            "automated": self.automated,
        }


class Suggestion:
    def __init__(self, suggestion_id: str, action: str, title: str, description: str, workflow: dict[str, Any]):
        self.suggestion_id = suggestion_id
        self.action = action
        self.title = title
        self.description = description
        self.workflow = workflow
        self.status = "pending"
        self.created_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "action": self.action,
            "title": self.title,
            "description": self.description,
            "workflow": self.workflow,
            "status": self.status,
            "created_at": self.created_at,
        }


class ProductivityEngine:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._lock = asyncio.Lock()
        self._min_frequency = int(os.getenv("HELIX_PRODUCTIVITY_MIN_FREQUENCY", "3"))
        self._max_patterns = int(os.getenv("HELIX_PRODUCTIVITY_MAX_PATTERNS", "50"))
        self._max_suggestions = int(os.getenv("HELIX_PRODUCTIVITY_MAX_SUGGESTIONS", "20"))
        self._active = True

        self._action_counts: dict[str, int] = defaultdict(int)
        self._action_timestamps: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=10000))
        self._patterns: dict[str, PatternObservation] = {}
        self._suggestions: dict[str, Suggestion] = {}

        self._observation_window = float(os.getenv("HELIX_PRODUCTIVITY_OBSERVATION_WINDOW", "3600"))
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}

    async def start(self) -> None:
        self._event_handler_map = {
            "automation.completed": self._handle_action_completed,
            "productivity.suggestion.accept": self._handle_suggestion_accept,
            "productivity.suggestion.dismiss": self._handle_suggestion_dismiss,
            "productivity.suggestion.create": self._handle_suggestion_create,
            "system.state.change": self._handle_state_change,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())
        logger.info("Productivity Engine started")

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        self._action_counts.clear()
        self._action_timestamps.clear()
        self._patterns.clear()
        self._suggestions.clear()
        logger.info("Productivity Engine stopped")

    async def _handle_action_completed(self, event: HelixEvent) -> None:
        action = event.payload.get("action", "")
        if not action:
            return
        await self._record_action(action)

    async def _handle_suggestion_accept(self, event: HelixEvent) -> None:
        suggestion_id = event.payload.get("suggestion_id", "")
        suggestion = self._suggestions.get(suggestion_id)
        if not suggestion:
            return
        suggestion.status = "accepted"
        pattern = self._patterns.get(suggestion.action)
        if pattern:
            pattern.automated = True

        await self._event_bus.publish_event(
            source="productivity_engine",
            event_type="productivity.workflow.create",
            payload={
                "suggestion_id": suggestion_id,
                "workflow": suggestion.workflow,
                "title": suggestion.title,
            },
            correlation_id=event.correlation_id,
        )
        logger.info("Suggestion accepted: %s", suggestion_id)

    async def _handle_suggestion_dismiss(self, event: HelixEvent) -> None:
        suggestion_id = event.payload.get("suggestion_id", "")
        suggestion = self._suggestions.get(suggestion_id)
        if suggestion:
            suggestion.status = "dismissed"
            pattern = self._patterns.get(suggestion.action)
            if pattern:
                pattern.suggested = False
            logger.info("Suggestion dismissed: %s", suggestion_id)

    async def _handle_suggestion_create(self, event: HelixEvent) -> None:
        action = event.payload.get("action", "")
        title = event.payload.get("title", "")
        description = event.payload.get("description", "")
        workflow = event.payload.get("workflow", {})
        if not action or not title:
            return
        
        async with self._lock:
            s_id = str(uuid.uuid4())[:8]
            suggestion = Suggestion(
                suggestion_id=s_id,
                action=action,
                title=title,
                description=description,
                workflow=workflow
            )
            self._suggestions[s_id] = suggestion
            
            if len(self._suggestions) > self._max_suggestions:
                sorted_sids = sorted(
                    self._suggestions.keys(),
                    key=lambda k: self._suggestions[k].created_at
                )
                for k in sorted_sids[:len(sorted_sids) - self._max_suggestions]:
                    del self._suggestions[k]
                    
            await self._event_bus.publish_event(
                source="productivity_engine",
                event_type="productivity.suggestion.created",
                payload=suggestion.to_dict(),
                correlation_id=event.correlation_id,
            )
            logger.info("Custom suggestion created: %s (%s)", s_id, title)

    async def _handle_state_change(self, event: HelixEvent) -> None:
        state = event.payload.get("state", "sleep")
        self._active = state not in ("sleep", "off")

    async def _record_action(self, action: str) -> None:
        async with self._lock:
            now = time.time()
            self._action_counts[action] += 1
            timestamps = self._action_timestamps[action]
            cutoff = now - self._observation_window
            while timestamps and timestamps[0] < cutoff:
                timestamps.popleft()
            timestamps.append(now)

            count = len(timestamps)
            if count >= self._min_frequency and action not in self._patterns:
                await self._create_pattern(action, count)

    async def _create_pattern(self, action: str, frequency: int) -> None:
        category, description = self._categorize(action)

        pattern = PatternObservation(
            action=action,
            category=category,
            description=description,
            frequency=frequency,
        )
        self._patterns[action] = pattern
        self._prune_patterns()

        suggestion = self._build_suggestion(pattern)
        if suggestion:
            await self._publish_suggestion(suggestion)

    def _categorize(self, action: str) -> tuple[str, str]:
        if action in ("browser_search",) or "browser" in action or "search" in action:
            return "browsing", f"Frequent browser action: {action}"
        if "file" in action or "read" in action:
            return "file_access", f"Repeated file operation: {action}"
        if "launch" in action or "open" in action:
            return "application", f"Frequently launched application: {action}"
        if "email" in action or "mail" in action:
            return "communication", f"Repeated email action: {action}"
        if "notif" in action:
            return "notification", f"Frequent notification: {action}"
        return "general", f"Repeated action detected: {action}"

    def _build_suggestion(self, pattern: PatternObservation) -> Suggestion | None:
        if pattern.suggested:
            return None

        if pattern.category == "browsing":
            title = "Automate browser search?"
            description = f"You have performed '{pattern.action}' {pattern.frequency} times recently. Create a shortcut?"
            workflow = {"action": "browser_search", "params": {}}
        elif pattern.category == "application":
            action_name = pattern.action
            title = f"Quick-launch {action_name}?"
            description = f"You open {action_name} frequently. Add a one-click launcher?"
            workflow = {"action": "launch_application", "params": {"application": action_name}}
        elif pattern.category == "file_access":
            title = "Create file access shortcut?"
            description = "You access certain files repeatedly. Create a quick-access menu?"
            workflow = {"action": "read_file", "params": {}}
        elif pattern.category == "communication":
            title = "Automate email task?"
            description = "You perform this email action often. Create a template?"
            workflow = {"action": "compose_email", "params": {}}
        else:
            title = "Automate repetitive task?"
            description = f"Pattern detected: {pattern.action}. Create automation?"
            workflow = {"action": "automate", "params": {"pattern": pattern.action}}

        suggestion = Suggestion(
            suggestion_id=uuid.uuid4().hex[:8],
            action=pattern.action,
            title=title,
            description=description,
            workflow=workflow,
        )
        pattern.suggested = True
        self._suggestions[suggestion.suggestion_id] = suggestion
        self._prune_suggestions()
        return suggestion

    async def _publish_suggestion(self, suggestion: Suggestion) -> None:
        logger.info("Productivity suggestion: %s", suggestion.title)
        await self._event_bus.publish_event(
            source="productivity_engine",
            event_type="productivity.suggestion",
            payload=suggestion.to_dict(),
        )

    def get_patterns(self) -> list[PatternObservation]:
        return list(self._patterns.values())

    def get_suggestions(self, status: str | None = None) -> list[Suggestion]:
        suggestions = list(self._suggestions.values())
        if status:
            suggestions = [s for s in suggestions if s.status == status]
        return suggestions

    def _prune_patterns(self) -> None:
        if len(self._patterns) > self._max_patterns:
            sorted_p = sorted(self._patterns.values(), key=lambda p: p.last_observed)
            for p in sorted_p[:len(sorted_p) - self._max_patterns]:
                del self._patterns[p.action]

    def _prune_suggestions(self) -> None:
        if len(self._suggestions) > self._max_suggestions:
            sorted_s = sorted(self._suggestions.values(), key=lambda s: s.created_at)
            for s in sorted_s[:len(sorted_s) - self._max_suggestions]:
                del self._suggestions[s.suggestion_id]

    async def save_state(self) -> None:
        import json
        from foundation.paths import get_runtime_dir
        path = os.path.join(str(get_runtime_dir()), "productivity_state.json")
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            async with self._lock:
                data = {
                    "patterns": {k: v.to_dict() for k, v in self._patterns.items()},
                }
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            logger.warning("Failed to save productivity state: %s", e)

    async def load_state(self) -> None:
        import json
        from foundation.paths import get_runtime_dir
        path = os.path.join(str(get_runtime_dir()), "productivity_state.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            async with self._lock:
                for action_key, pdata in data.get("patterns", {}).items():
                    pattern = PatternObservation(
                        action=action_key,
                        category=pdata.get("category", "general"),
                        description=pdata.get("description", ""),
                        frequency=pdata.get("frequency", 0),
                    )
                    pattern.first_observed = pdata.get("first_observed", "")
                    pattern.last_observed = pdata.get("last_observed", "")
                    pattern.suggested = pdata.get("suggested", False)
                    pattern.automated = pdata.get("automated", False)
                    self._patterns[action_key] = pattern
            logger.info("Loaded %d patterns from state", len(data.get("patterns", {})))
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Failed to load productivity state: %s", e)
