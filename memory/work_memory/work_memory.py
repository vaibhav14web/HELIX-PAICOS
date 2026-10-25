import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from foundation.event_bus.event_bus import EventBus, HelixEvent


from foundation.storage_manager.crypto import encrypt_string, decrypt_string


class WorkSession(BaseModel):
    session_id: str
    user_id: str = "default_owner"
    active_project: str | None = None
    open_tasks: list[str] = Field(default_factory=list)
    current_context: dict[str, Any] = Field(default_factory=dict)
    started_at: str = ""
    last_updated: str = ""


class WorkMemory:
    def __init__(self, event_bus: EventBus, storage_path: str | None = None):
        self._event_bus = event_bus
        from foundation.paths import get_helix_home
        self._storage_path = Path(storage_path or os.getenv(
            "HELIX_MEMORY_PATH",
            str(get_helix_home() / "memory"),
        ))
        self._work_path = self._storage_path / "work_sessions.json"
        # Keyed by user_id -> active WorkSession
        self._active_sessions: dict[str, WorkSession] = {}
        # Keyed by (user_id, session_id) -> WorkSession
        self._sessions: dict[tuple[str, str], WorkSession] = {}
        self._max_sessions = int(os.getenv("HELIX_WORK_MAX_SESSIONS", "50"))
        self._dirty_users: set[str] = set()
        self._subscriptions: list[str] = []

    def _get_user_work_file(self, user_id: str) -> Path:
        if not user_id or user_id in ("default", "default_owner", "global"):
            return self._work_path
        try:
            from foundation.storage_manager.tenant_storage import get_tenant_storage_manager
            return get_tenant_storage_manager().get_user_work_dir(user_id) / "work_sessions.json"
        except Exception:
            return self._work_path

    async def start(self) -> None:
        self._load_from_disk()
        self._event_bus.subscribe("memory.work.set_project", self._handle_set_project)
        self._event_bus.subscribe("memory.work.add_task", self._handle_add_task)
        self._event_bus.subscribe("memory.work.get_context", self._handle_get_context)
        self._event_bus.subscribe("memory.work.update_context", self._handle_update_context)
        self._event_bus.subscribe("session.start", self._handle_session_start)
        self._event_bus.subscribe("session.end", self._handle_session_end)
        self._subscriptions = [
            "memory.work.set_project", "memory.work.add_task",
            "memory.work.get_context", "memory.work.update_context",
            "session.start", "session.end",
        ]

    async def stop(self) -> None:
        for uid in list(self._dirty_users):
            self._save_to_disk(uid)
        self._subscriptions.clear()

    @property
    def _active_session(self) -> WorkSession | None:
        """Backward compatibility property returning the active session for default owner."""
        return self._active_sessions.get("default_owner") or (next(iter(self._active_sessions.values())) if self._active_sessions else None)

    def _load_from_disk(self, user_id: str = "default_owner") -> None:
        path = self._get_user_work_file(user_id)
        if path.exists():
            try:
                raw_bytes = path.read_bytes()
                decompressed = decrypt_string(raw_bytes)
                data = json.loads(decompressed)
                if not isinstance(data, list):
                    return
                for s in data:
                    if not isinstance(s, dict):
                        continue
                    session = WorkSession(**s)
                    self._sessions[(session.user_id, session.session_id)] = session
                    if session.user_id == "default_owner":
                        self._sessions[session.session_id] = session
            except (json.JSONDecodeError, ValueError, TypeError, Exception):
                pass

    def _save_to_disk(self, user_id: str = "default_owner") -> None:
        path = self._get_user_work_file(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        seen_sessions: set[str] = set()
        user_sessions = []
        for k, s in list(self._sessions.items()):
            uid = k[0] if isinstance(k, tuple) else getattr(s, "user_id", "default_owner")
            if uid == user_id and s.session_id not in seen_sessions:
                seen_sessions.add(s.session_id)
                user_sessions.append(s.model_dump())
        content = json.dumps(user_sessions, indent=2)
        encrypted = encrypt_string(content)
        path.write_bytes(encrypted)
        self._dirty_users.discard(user_id)

    async def _handle_session_start(self, event: HelixEvent) -> None:
        session_id = event.payload.get("session_id", "")
        user_id = event.payload.get("user_id", "default_owner")
        if not session_id:
            return
        # Load user data if not loaded
        key = (user_id, session_id)
        if key not in self._sessions and session_id not in self._sessions:
            self._load_from_disk(user_id)

        existing = self._sessions.get(key) or (self._sessions.get(session_id) if user_id == "default_owner" else None)
        if existing:
            self._active_sessions[user_id] = existing
        else:
            active = WorkSession(
                session_id=session_id,
                user_id=user_id,
                started_at=datetime.now(timezone.utc).isoformat(),
                last_updated=datetime.now(timezone.utc).isoformat(),
            )
            self._sessions[key] = active
            if user_id == "default_owner":
                self._sessions[session_id] = active
            self._active_sessions[user_id] = active
            self._dirty_users.add(user_id)
            self._prune_sessions(user_id)
            self._save_to_disk(user_id)

    async def _handle_session_end(self, event: HelixEvent) -> None:
        user_id = event.payload.get("user_id", "default_owner")
        if user_id in self._active_sessions:
            del self._active_sessions[user_id]

    async def _handle_set_project(self, event: HelixEvent) -> None:
        user_id = event.payload.get("user_id", "default_owner")
        active = self._active_sessions.get(user_id)
        if not active:
            return
        active.active_project = event.payload.get("project", "")
        active.last_updated = datetime.now(timezone.utc).isoformat()
        self._dirty_users.add(user_id)
        self._save_to_disk(user_id)
        await self._event_bus.publish_event(
            source="work_memory",
            event_type="memory.work.project_set",
            payload={"session_id": active.session_id, "user_id": user_id, "project": active.active_project},
            correlation_id=event.correlation_id,
        )

    async def _handle_add_task(self, event: HelixEvent) -> None:
        user_id = event.payload.get("user_id", "default_owner")
        active = self._active_sessions.get(user_id)
        if not active:
            return
        task = event.payload.get("task", "")
        if task:
            active.open_tasks.append(task)
            active.last_updated = datetime.now(timezone.utc).isoformat()
            self._dirty_users.add(user_id)
            self._save_to_disk(user_id)

    async def _handle_get_context(self, event: HelixEvent) -> None:
        user_id = event.payload.get("user_id", "default_owner")
        active = self._active_sessions.get(user_id)
        if not active:
            await self._event_bus.publish_event(
                source="work_memory",
                event_type="memory.work.context_retrieved",
                payload={"context": None, "user_id": user_id},
                correlation_id=event.correlation_id,
            )
            return
        await self._event_bus.publish_event(
            source="work_memory",
            event_type="memory.work.context_retrieved",
            payload={"context": active.model_dump(), "user_id": user_id},
            correlation_id=event.correlation_id,
        )

    async def _handle_update_context(self, event: HelixEvent) -> None:
        user_id = event.payload.get("user_id", "default_owner")
        active = self._active_sessions.get(user_id)
        if not active:
            return
        updates = event.payload.get("updates", {})
        active.current_context.update(updates)
        active.last_updated = datetime.now(timezone.utc).isoformat()
        self._dirty_users.add(user_id)
        self._save_to_disk(user_id)

    def _prune_sessions(self, user_id: str) -> None:
        user_keys = [k for k in self._sessions.keys() if k[0] == user_id]
        if len(user_keys) > self._max_sessions:
            sorted_keys = sorted(
                user_keys,
                key=lambda k: self._sessions[k].last_updated or "",
            )
            for k in sorted_keys[:len(sorted_keys) - self._max_sessions]:
                del self._sessions[k]
            self._dirty_users.add(user_id)

    def get_active_session(self, user_id: str = "default_owner") -> WorkSession | None:
        return self._active_sessions.get(user_id)

    def get_session(self, session_id: str, user_id: str = "default_owner") -> WorkSession | None:
        return self._sessions.get((user_id, session_id))

    def get_all_sessions(self, user_id: str = "default_owner") -> list[WorkSession]:
        self._load_from_disk(user_id)
        seen: set[str] = set()
        result: list[WorkSession] = []
        for k, s in list(self._sessions.items()):
            if not isinstance(s, WorkSession):
                continue
            uid = k[0] if isinstance(k, tuple) else getattr(s, "user_id", "default_owner")
            if uid == user_id and s.session_id not in seen:
                seen.add(s.session_id)
                result.append(s)
        return result
