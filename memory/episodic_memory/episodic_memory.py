import os
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from foundation.event_bus.event_bus import EventBus, HelixEvent
from foundation.storage_manager.crypto import encrypt_string, decrypt_string

logger = logging.getLogger("helix.episodic_memory")


class Episode(BaseModel):
    """
    Structured record of a single cognitive interaction episode.
    Captures intent, situational context, actions executed, user reaction, outcome, and lessons.
    """
    episode_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_id: str = "default_owner"
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    intent: str
    context: dict[str, Any] = Field(default_factory=dict)
    decision_mode: str = "direct_answer"  # direct_answer | plan_execution | suggestion | clarification
    actions_taken: list[dict[str, Any]] = Field(default_factory=list)
    user_reaction: str = "neutral"        # accepted | modified | rejected | corrected | neutral
    outcome: str = "success"              # success | failure | partial
    reflection_notes: str = ""
    tags: list[str] = Field(default_factory=list)


class EpisodicMemory:
    """
    Episodic & Behavioral Memory Engine for HELIX.
    Maintains a rolling, searchable timeline of past interactions, decisions, and outcomes,
    partitioned and encrypted at rest per user.
    """

    def __init__(self, event_bus: EventBus, storage_path: str | None = None, max_episodes: int = 500):
        self._event_bus = event_bus
        self._max_episodes = max_episodes
        from foundation.paths import get_helix_home
        self._storage_dir = Path(storage_path or os.getenv(
            "HELIX_MEMORY_PATH",
            str(get_helix_home() / "memory"),
        ))
        self._file_path = self._storage_dir / "episodes.enc"

        # Keyed by user_id -> list[Episode]
        self._episodes: dict[str, list[Episode]] = {}
        self._by_id: dict[str, Episode] = {}
        self._loaded_users: set[str] = set()
        self._dirty_users: set[str] = set()
        self._subscriptions: list[str] = []

    def _get_user_file_path(self, user_id: str) -> Path:
        if not user_id or user_id in ("default", "default_owner", "global"):
            return self._file_path
        try:
            from foundation.storage_manager.tenant_storage import get_tenant_storage_manager
            return get_tenant_storage_manager().get_user_episodes_dir(user_id) / "episodes.enc"
        except Exception:
            return self._file_path

    async def start(self) -> None:
        self._ensure_user_loaded("default_owner")
        handlers = {
            "memory.episodic.record": self._handle_record,
            "memory.episodic.get": self._handle_get,
            "memory.episodic.search": self._handle_search,
            "memory.episodic.recent": self._handle_recent,
        }
        for event_type, handler in handlers.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(handlers.keys())
        logger.info("EpisodicMemory started successfully")

    async def stop(self) -> None:
        for uid in list(self._dirty_users):
            self.save_to_disk(uid)
        for event_type in self._subscriptions:
            self._event_bus.unsubscribe(event_type, None)
        self._subscriptions.clear()
        logger.info("EpisodicMemory stopped")

    def _ensure_user_loaded(self, user_id: str) -> None:
        if user_id in self._loaded_users:
            return
        self._load_from_disk(user_id)
        self._loaded_users.add(user_id)

    def record_episode(self, episode: Episode) -> Episode:
        """Record and index a new interaction episode."""
        uid = episode.user_id
        self._ensure_user_loaded(uid)
        if uid not in self._episodes:
            self._episodes[uid] = []

        self._by_id[episode.episode_id] = episode
        self._episodes[uid].append(episode)

        if len(self._episodes[uid]) > self._max_episodes:
            removed = self._episodes[uid].pop(0)
            self._by_id.pop(removed.episode_id, None)

        self._dirty_users.add(uid)
        return episode

    def get_episode(self, episode_id: str, user_id: str | None = None) -> Episode | None:
        ep = self._by_id.get(episode_id)
        if not ep:
            return None
        if user_id and ep.user_id != user_id and user_id not in ("default", "default_owner", "global"):
            return None  # Authorization check: do not leak across users
        return ep

    def get_recent(self, limit: int = 10, outcome: str | None = None, user_id: str = "default_owner") -> list[Episode]:
        """Retrieve the most recent episodes for a specific user, optionally filtered by outcome."""
        self._ensure_user_loaded(user_id)
        matched = self._episodes.get(user_id, [])
        if outcome:
            matched = [e for e in matched if e.outcome.lower() == outcome.lower()]
        return list(reversed(matched[-limit:]))

    def search_episodes(
        self,
        query: str,
        limit: int = 5,
        outcome: str | None = None,
        tag: str | None = None,
        user_id: str = "default_owner",
    ) -> list[Episode]:
        """Search episodes for a specific user matching intent, reflection notes, or tags."""
        self._ensure_user_loaded(user_id)
        q_lower = query.lower().strip()
        results: list[tuple[float, Episode]] = []

        for ep in reversed(self._episodes.get(user_id, [])):
            if outcome and ep.outcome.lower() != outcome.lower():
                continue
            if tag and tag.lower() not in [t.lower() for t in ep.tags]:
                continue

            score = 0.0
            intent_lower = ep.intent.lower()
            notes_lower = ep.reflection_notes.lower()

            if q_lower in intent_lower:
                score += 3.0
            if q_lower in notes_lower:
                score += 2.0
            for t in ep.tags:
                if q_lower in t.lower():
                    score += 1.5

            q_words = set(q_lower.split())
            if q_words:
                intent_words = set(intent_lower.split())
                overlap = len(q_words & intent_words)
                score += overlap * 0.5

            if score > 0.0:
                results.append((score, ep))

        results.sort(key=lambda r: r[0], reverse=True)
        return [r[1] for r in results[:limit]]

    def get_past_mistakes_for_intent(self, intent: str, limit: int = 3, user_id: str = "default_owner") -> list[Episode]:
        """Search past failed or corrected episodes matching the given intent for a user."""
        episodes = self.search_episodes(intent, limit=limit * 2, user_id=user_id)
        failures = [
            e for e in episodes
            if e.outcome in ("failure", "partial") or e.user_reaction in ("rejected", "corrected")
        ]
        return failures[:limit]

    # ── Internal Storage ──────────────────────────────────────────

    def _load_from_disk(self, user_id: str = "default_owner") -> None:
        path = self._get_user_file_path(user_id)
        if not path.exists():
            return
        try:
            raw_bytes = path.read_bytes()
            decompressed = decrypt_string(raw_bytes)
            data = json.loads(decompressed)
            if isinstance(data, list):
                episodes = [Episode(**d) for d in data if isinstance(d, dict)]
                self._episodes[user_id] = episodes
                for e in episodes:
                    self._by_id[e.episode_id] = e
            self._dirty_users.discard(user_id)
        except Exception as e:
            logger.warning("Could not load episodic memory for user %s (%s)", user_id, e)

    def save_to_disk(self, user_id: str = "default_owner") -> None:
        try:
            path = self._get_user_file_path(user_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            episodes = self._episodes.get(user_id, [])
            content = json.dumps([e.model_dump() for e in episodes], indent=2)
            encrypted = encrypt_string(content)
            path.write_bytes(encrypted)
            self._dirty_users.discard(user_id)
        except Exception as e:
            logger.error("Failed to save episodic memory for user %s: %s", user_id, e)

    # ── EventBus Handlers ─────────────────────────────────────────

    async def _handle_record(self, event: HelixEvent) -> None:
        payload = event.payload
        intent = payload.get("intent", "")
        if not intent:
            return
        user_id = payload.get("user_id", "default_owner")

        episode = Episode(
            intent=intent,
            user_id=user_id,
            context=payload.get("context", {}),
            decision_mode=payload.get("decision_mode", "direct_answer"),
            actions_taken=payload.get("actions_taken", []),
            user_reaction=payload.get("user_reaction", "neutral"),
            outcome=payload.get("outcome", "success"),
            reflection_notes=payload.get("reflection_notes", ""),
            tags=payload.get("tags", []),
        )
        self.record_episode(episode)
        if user_id in self._dirty_users:
            self.save_to_disk(user_id)

        await self._event_bus.publish_event(
            source="episodic_memory",
            event_type="memory.episodic.recorded",
            payload={"episode": episode.model_dump(), "user_id": user_id},
            correlation_id=event.correlation_id,
        )

    async def _handle_get(self, event: HelixEvent) -> None:
        ep_id = event.payload.get("episode_id", "")
        user_id = event.payload.get("user_id", "default_owner")
        ep = self.get_episode(ep_id, user_id=user_id)
        await self._event_bus.publish_event(
            source="episodic_memory",
            event_type="memory.episodic.retrieved",
            payload={"episode": ep.model_dump() if ep else None, "user_id": user_id},
            correlation_id=event.correlation_id,
        )

    async def _handle_search(self, event: HelixEvent) -> None:
        query = event.payload.get("query", "")
        limit = int(event.payload.get("limit", 5))
        outcome = event.payload.get("outcome")
        tag = event.payload.get("tag")
        user_id = event.payload.get("user_id", "default_owner")

        results = self.search_episodes(query, limit=limit, outcome=outcome, tag=tag, user_id=user_id)
        await self._event_bus.publish_event(
            source="episodic_memory",
            event_type="memory.episodic.searched",
            payload={"results": [e.model_dump() for e in results], "query": query, "user_id": user_id},
            correlation_id=event.correlation_id,
        )

    async def _handle_recent(self, event: HelixEvent) -> None:
        limit = int(event.payload.get("limit", 10))
        outcome = event.payload.get("outcome")
        user_id = event.payload.get("user_id", "default_owner")
        results = self.get_recent(limit=limit, outcome=outcome, user_id=user_id)
        await self._event_bus.publish_event(
            source="episodic_memory",
            event_type="memory.episodic.recent_retrieved",
            payload={"episodes": [e.model_dump() for e in results], "user_id": user_id},
            correlation_id=event.correlation_id,
        )
