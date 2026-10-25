import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from foundation.event_bus.event_bus import EventBus, HelixEvent


from foundation.storage_manager.crypto import encrypt_string, decrypt_string


class ConversationEntry(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    session_id: str
    user_id: str = "default_owner"
    companion_id: str = "default"
    role: str
    content: str


class ConversationMemory:
    def __init__(self, event_bus: EventBus, storage_path: str | None = None):
        self._event_bus = event_bus
        from foundation.paths import get_helix_home
        canonical_mem = get_helix_home() / "memory"
        self._storage_path = Path(storage_path or os.getenv(
            "HELIX_MEMORY_PATH",
            str(canonical_mem),
        ))
        self._conversations_path = self._storage_path / "conversations"
        self._conversations_path.mkdir(parents=True, exist_ok=True)
        self._rolling_days = int(os.getenv("HELIX_MEMORY_ROLLING_DAYS", "7"))
        self._active: dict[str, list[ConversationEntry]] = {}
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, object] = {}
        self._file_locks: dict[str, object] = {}

    def _get_user_conversations_path(self, user_id: str | None = None) -> Path:
        if not user_id or user_id in ("default", "default_owner", "global"):
            return self._conversations_path
        from foundation.paths import get_helix_home
        canonical_mem = get_helix_home() / "memory"
        default_mem_dir = Path(os.getenv("HELIX_MEMORY_PATH", str(canonical_mem)))
        if self._storage_path.resolve() != default_mem_dir.resolve():
            p = self._storage_path / "users" / user_id / "conversations"
            p.mkdir(parents=True, exist_ok=True)
            return p
        try:
            from foundation.storage_manager.tenant_storage import get_tenant_storage_manager
            return get_tenant_storage_manager().get_user_conversations_dir(user_id)
        except Exception:
            return self._conversations_path

    def _read_file_lines(self, file_path: Path) -> list[str]:
        if not file_path.exists():
            return []
        try:
            raw_bytes = file_path.read_bytes()
            content = decrypt_string(raw_bytes)
            return [line for line in content.splitlines() if line.strip()]
        except Exception:
            return []

    def _append_file_line(self, file_path: Path, line: str) -> None:
        lines = self._read_file_lines(file_path)
        lines.append(line)
        text = "\n".join(lines) + "\n"
        file_path.write_bytes(encrypt_string(text))

    async def start(self) -> None:
        self._event_handler_map = {
            "memory.conversation.store": self._handle_store,
            "memory.conversation.retrieve": self._handle_retrieve,
            "memory.conversation.search": self._handle_search,
            "session.start": self._handle_session_start,
            "session.end": self._handle_session_end,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()

    async def _handle_store(self, event: HelixEvent) -> None:
        entry = ConversationEntry(
            session_id=event.payload.get("session_id", ""),
            user_id=event.payload.get("user_id", "default_owner"),
            companion_id=event.payload.get("companion_id", "default"),
            role=event.payload.get("role", "user"),
            content=event.payload.get("content", ""),
        )
        await self._store_entry(entry)

    async def _handle_retrieve(self, event: HelixEvent) -> None:
        session_id = event.payload.get("session_id", "")
        user_id = event.payload.get("user_id", "default_owner")
        limit = event.payload.get("limit", 50)
        entries = await self.get_session_history(session_id, limit=limit, user_id=user_id)
        await self._event_bus.publish_event(
            source="conversation_memory",
            event_type="memory.conversation.retrieved",
            payload={"session_id": session_id, "user_id": user_id, "entries": [e.model_dump() for e in entries]},
            correlation_id=event.correlation_id,
        )

    async def _handle_search(self, event: HelixEvent) -> None:
        query = event.payload.get("query", "")
        user_id = event.payload.get("user_id", "default_owner")
        limit = event.payload.get("limit", 10)
        results = await self.search_entries(query, limit=limit, user_id=user_id)
        await self._event_bus.publish_event(
            source="conversation_memory",
            event_type="memory.conversation.searched",
            payload={"query": query, "user_id": user_id, "results": [e.model_dump() for e in results]},
            correlation_id=event.correlation_id,
        )

    async def _handle_session_start(self, event: HelixEvent) -> None:
        session_id = event.payload.get("session_id", "")
        user_id = event.payload.get("user_id", "default_owner")
        if session_id:
            key = f"{user_id}:{session_id}"
            self._active[key] = []
            if user_id == "default_owner":
                self._active[session_id] = self._active[key]
            await self._load_recent(session_id, user_id=user_id)

    async def _handle_session_end(self, event: HelixEvent) -> None:
        session_id = event.payload.get("session_id", "")
        user_id = event.payload.get("user_id", "default_owner")
        key = f"{user_id}:{session_id}"
        if key in self._active:
            del self._active[key]
        if user_id == "default_owner":
            self._active.pop(session_id, None)

    async def _store_entry(self, entry: ConversationEntry) -> None:
        session_id = entry.session_id
        user_id = entry.user_id
        key = f"{user_id}:{session_id}"
        if key in self._active:
            self._active[key].append(entry)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        user_conv_dir = self._get_user_conversations_path(user_id)
        user_conv_dir.mkdir(parents=True, exist_ok=True)
        file_path = user_conv_dir / f"{session_id}_{date_str}.jsonl"
        async with self._get_file_lock(file_path):
            self._append_file_line(file_path, entry.model_dump_json())
        await self._enforce_rolling_window(session_id, user_id=user_id)
        await self._event_bus.publish_event(
            source="conversation_memory",
            event_type="memory.conversation.stored",
            payload={"session_id": session_id, "user_id": user_id, "role": entry.role},
        )

    async def store_entry(
        self,
        session_id: str,
        role: str,
        content: str,
        user_id: str = "default_owner",
        companion_id: str = "default",
    ) -> ConversationEntry:
        entry = ConversationEntry(
            session_id=session_id,
            user_id=user_id,
            companion_id=companion_id,
            role=role,
            content=content,
        )
        await self._store_entry(entry)
        return entry

    async def get_session_history(self, session_id: str, limit: int = 50, user_id: str | None = None) -> list[ConversationEntry]:
        effective_user = user_id or "default_owner"
        key = f"{effective_user}:{session_id}"
        if key in self._active and self._active[key]:
            return self._active[key][-limit:]
        return await self._load_from_disk(session_id, limit=limit, user_id=effective_user)

    async def search_entries(self, query: str, limit: int = 10, user_id: str | None = None) -> list[ConversationEntry]:
        results: list[ConversationEntry] = []
        query_lower = query.lower()
        conv_dir = self._get_user_conversations_path(user_id)
        if not conv_dir.exists():
            return []
        for file_path in conv_dir.glob("*.jsonl"):
            async with self._get_file_lock(file_path):
                for line in self._read_file_lines(file_path):
                    try:
                        entry = ConversationEntry(**json.loads(line))
                        # Server-side verification of user ownership
                        if user_id and entry.user_id != user_id and user_id not in ("default", "default_owner", "global"):
                            continue
                        if query_lower in entry.content.lower():
                            results.append(entry)
                            if len(results) >= limit:
                                return results
                    except (json.JSONDecodeError, ValueError):
                        continue
        return results

    async def get_all_sessions(self, user_id: str | None = None) -> list[str]:
        session_mtimes = {}
        conv_dir = self._get_user_conversations_path(user_id)
        if not conv_dir.exists():
            return []
        for file_path in conv_dir.glob("*.jsonl"):
            parts = file_path.stem.split("_")
            if len(parts) >= 2:
                session_id = "_".join(parts[:-1])
            else:
                session_id = file_path.stem

            try:
                mtime = file_path.stat().st_mtime
            except Exception:
                mtime = 0

            if session_id not in session_mtimes or mtime > session_mtimes[session_id]:
                session_mtimes[session_id] = mtime

        sorted_sessions = sorted(session_mtimes.keys(), key=lambda sid: session_mtimes[sid], reverse=True)
        return sorted_sessions

    async def _load_from_disk(self, session_id: str, limit: int = 50, user_id: str | None = None) -> list[ConversationEntry]:
        entries: list[ConversationEntry] = []
        conv_dir = self._get_user_conversations_path(user_id)
        if not conv_dir.exists():
            return []
        for file_path in sorted(conv_dir.glob(f"{session_id}_*.jsonl"), reverse=True):
            for line in self._read_file_lines(file_path):
                try:
                    entry = ConversationEntry(**json.loads(line))
                    if user_id and entry.user_id != user_id and user_id not in ("default", "default_owner", "global"):
                        continue
                    entries.append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue
        return entries[-limit:]

    async def _load_recent(self, session_id: str, user_id: str | None = None) -> None:
        effective_user = user_id or "default_owner"
        key = f"{effective_user}:{session_id}"
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._rolling_days)
        conv_dir = self._get_user_conversations_path(user_id)
        if not conv_dir.exists():
            return
        for file_path in sorted(conv_dir.glob(f"{session_id}_*.jsonl"), reverse=True):
            for line in self._read_file_lines(file_path):
                try:
                    entry = ConversationEntry(**json.loads(line))
                    entry_time = datetime.fromisoformat(entry.timestamp)
                    if entry_time > cutoff:
                        if key in self._active:
                            self._active[key].append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue

    async def _enforce_rolling_window(self, session_id: str, user_id: str | None = None) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._rolling_days)
        conv_dir = self._get_user_conversations_path(user_id)
        if not conv_dir.exists():
            return
        for file_path in conv_dir.glob(f"{session_id}_*.jsonl"):
            file_date_str = file_path.stem.split("_")[-1]
            try:
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if file_date < cutoff:
                    archive_path = conv_dir / "archived"
                    archive_path.mkdir(parents=True, exist_ok=True)
                    file_path.rename(archive_path / file_path.name)
            except (ValueError, IndexError):
                continue

    async def archive_old_data(self, days: int | None = None, user_id: str | None = None) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days or self._rolling_days)
        conv_dir = self._get_user_conversations_path(user_id)
        if not conv_dir.exists():
            return
        archive_path = conv_dir / "archived"
        archive_path.mkdir(parents=True, exist_ok=True)
        for file_path in conv_dir.glob("*.jsonl"):
            file_date_str = file_path.stem.split("_")[-1]
            try:
                file_date = datetime.strptime(file_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if file_date < cutoff:
                    file_path.rename(archive_path / file_path.name)
            except (ValueError, IndexError):
                continue

    def _get_file_lock(self, file_path: Path):
        return _FileLock(self._file_locks, str(file_path))


class _FileLock:
    def __init__(self, locks: dict[str, asyncio.Lock], path: str):
        self._locks = locks
        self._path = path
        self._lock = self._locks.setdefault(path, asyncio.Lock())

    async def __aenter__(self):
        await self._lock.acquire()
        return self

    async def __aexit__(self, *args):
        self._lock.release()
