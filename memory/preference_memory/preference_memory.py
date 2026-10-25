import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from foundation.event_bus.event_bus import EventBus, HelixEvent


from foundation.storage_manager.crypto import encrypt_string, decrypt_string


from datetime import datetime, timezone
from pydantic import BaseModel, Field


class PreferenceEntry(BaseModel):
    key: str
    user_id: str = "default_owner"
    value: Any
    category: str = "general"
    source: str = "user"
    provenance: str = "user_stated"  # user_stated | observed_system | verified_action | inferred | unverified_model
    confidence: float = 1.0
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_verified: bool = True


class PreferenceMemory:
    def __init__(self, event_bus: EventBus, storage_path: str | None = None):
        self._event_bus = event_bus
        from foundation.paths import get_helix_home
        self._storage_path = Path(storage_path or os.getenv(
            "HELIX_MEMORY_PATH",
            str(get_helix_home() / "memory"),
        ))
        self._preferences_path = self._storage_path / "preferences.json"
        # Keyed by (user_id, key) -> PreferenceEntry
        self._cache: dict[tuple[str, str], PreferenceEntry] = {}
        self._loaded_users: set[str] = set()
        self._dirty_users: set[str] = set()
        self._subscriptions: list[str] = []

    def _get_user_preferences_file(self, user_id: str) -> Path:
        if not user_id or user_id in ("default", "default_owner", "global"):
            return self._preferences_path
        try:
            from foundation.storage_manager.tenant_storage import get_tenant_storage_manager
            return get_tenant_storage_manager().get_user_preferences_dir(user_id) / "preferences.json"
        except Exception:
            return self._preferences_path

    async def start(self) -> None:
        self._ensure_user_loaded("default_owner")
        self._event_bus.subscribe("memory.preference.store", self._handle_store)
        self._event_bus.subscribe("memory.preference.get", self._handle_get)
        self._event_bus.subscribe("memory.preference.get_all", self._handle_get_all)
        self._event_bus.subscribe("memory.preference.delete", self._handle_delete)
        self._subscriptions = ["memory.preference.store", "memory.preference.get",
                               "memory.preference.get_all", "memory.preference.delete"]

    async def stop(self) -> None:
        for uid in list(self._dirty_users):
            self._save_to_disk(uid)
        self._subscriptions.clear()

    def _ensure_user_loaded(self, user_id: str) -> None:
        if user_id in self._loaded_users:
            return
        path = self._get_user_preferences_file(user_id)
        if path.exists():
            try:
                raw_bytes = path.read_bytes()
                decompressed = decrypt_string(raw_bytes)
                data = json.loads(decompressed)
                if isinstance(data, list):
                    for entry_data in data:
                        if isinstance(entry_data, dict):
                            entry = PreferenceEntry(**entry_data)
                            self._cache[(entry.user_id, entry.key)] = entry
                            if entry.user_id == "default_owner":
                                self._cache[entry.key] = entry
            except (json.JSONDecodeError, ValueError, TypeError, Exception):
                pass
        self._loaded_users.add(user_id)

    def _load_from_disk(self, user_id: str = "default_owner") -> None:
        """Backward compatibility alias for _ensure_user_loaded."""
        self._ensure_user_loaded(user_id)

    def _save_to_disk(self, user_id: str = "default_owner") -> None:
        path = self._get_user_preferences_file(user_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        seen_keys: set[str] = set()
        user_entries = []
        for k, e in list(self._cache.items()):
            uid = k[0] if isinstance(k, tuple) else getattr(e, "user_id", "default_owner")
            if uid == user_id and e.key not in seen_keys:
                seen_keys.add(e.key)
                user_entries.append(e.model_dump())
        content = json.dumps(user_entries, indent=2)
        encrypted = encrypt_string(content)
        path.write_bytes(encrypted)
        self._dirty_users.discard(user_id)

    async def _handle_store(self, event: HelixEvent) -> None:
        key = event.payload.get("key", "")
        if not key:
            return
        user_id = event.payload.get("user_id", "default_owner")
        self._ensure_user_loaded(user_id)

        incoming_source = event.payload.get("source", "user")
        incoming_provenance = event.payload.get("provenance", "user_stated" if incoming_source in ("user", "user_stated") else "inferred")
        incoming_confidence = float(event.payload.get("confidence", 1.0 if incoming_provenance == "user_stated" else 0.6))
        is_verified = bool(event.payload.get("is_verified", incoming_provenance != "unverified_model"))

        existing = self._cache.get((user_id, key))
        if existing and existing.provenance == "user_stated" and incoming_provenance in ("inferred", "unverified_model"):
            return

        entry = PreferenceEntry(
            key=key,
            user_id=user_id,
            value=event.payload.get("value"),
            category=event.payload.get("category", "general"),
            source=incoming_source,
            provenance=incoming_provenance,
            confidence=incoming_confidence,
            is_verified=is_verified,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._cache[(user_id, entry.key)] = entry
        if user_id == "default_owner":
            self._cache[entry.key] = entry
        self._dirty_users.add(user_id)
        self._save_to_disk(user_id)
        await self._event_bus.publish_event(
            source="preference_memory",
            event_type="memory.preference.stored",
            payload={"key": entry.key, "user_id": user_id, "category": entry.category, "provenance": entry.provenance},
            correlation_id=event.correlation_id,
        )

    async def _handle_get(self, event: HelixEvent) -> None:
        key = event.payload.get("key", "")
        user_id = event.payload.get("user_id", "default_owner")
        self._ensure_user_loaded(user_id)
        entry = self._cache.get((user_id, key)) or (self._cache.get(key) if user_id == "default_owner" else None)
        await self._event_bus.publish_event(
            source="preference_memory",
            event_type="memory.preference.retrieved",
            payload={"key": key, "user_id": user_id, "entry": entry.model_dump() if entry else None},
            correlation_id=event.correlation_id,
        )

    async def _handle_get_all(self, event: HelixEvent) -> None:
        user_id = event.payload.get("user_id", "default_owner")
        self._ensure_user_loaded(user_id)
        category = event.payload.get("category", None)
        seen_keys: set[str] = set()
        entries = []
        for k, e in list(self._cache.items()):
            uid = k[0] if isinstance(k, tuple) else getattr(e, "user_id", "default_owner")
            if uid == user_id and e.key not in seen_keys:
                seen_keys.add(e.key)
                entries.append(e)
        if category:
            entries = [e for e in entries if e.category == category]
        await self._event_bus.publish_event(
            source="preference_memory",
            event_type="memory.preference.all_retrieved",
            payload={"category": category, "user_id": user_id, "entries": [e.model_dump() for e in entries]},
            correlation_id=event.correlation_id,
        )

    async def _handle_delete(self, event: HelixEvent) -> None:
        key = event.payload.get("key", "")
        user_id = event.payload.get("user_id", "default_owner")
        self._ensure_user_loaded(user_id)
        self._cache.pop((user_id, key), None)
        if user_id == "default_owner":
            self._cache.pop(key, None)
        self._dirty_users.add(user_id)
        self._save_to_disk(user_id)
        await self._event_bus.publish_event(
            source="preference_memory",
            event_type="memory.preference.deleted",
            payload={"key": key, "user_id": user_id},
            correlation_id=event.correlation_id,
        )

    def set(
        self,
        key: str,
        value: Any,
        category: str = "general",
        user_id: str = "default_owner",
        source: str = "user",
    ) -> PreferenceEntry:
        self._ensure_user_loaded(user_id)
        entry = PreferenceEntry(
            key=key,
            user_id=user_id,
            value=value,
            category=category,
            source=source,
            provenance="user_stated" if source in ("user", "user_stated") else "inferred",
            confidence=1.0,
            is_verified=True,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        self._cache[(user_id, key)] = entry
        if user_id == "default_owner":
            self._cache[key] = entry
        self._dirty_users.add(user_id)
        self._save_to_disk(user_id)
        return entry

    def delete(self, key: str, user_id: str = "default_owner") -> bool:
        self._ensure_user_loaded(user_id)
        deleted = False
        if (user_id, key) in self._cache:
            del self._cache[(user_id, key)]
            deleted = True
        if user_id == "default_owner" and key in self._cache:
            del self._cache[key]
            deleted = True
        if deleted:
            self._dirty_users.add(user_id)
            self._save_to_disk(user_id)
            return True
        return False

    def get(self, key: str, user_id: str = "default_owner") -> PreferenceEntry | None:
        self._ensure_user_loaded(user_id)
        return self._cache.get((user_id, key)) or (self._cache.get(key) if user_id == "default_owner" else None)

    def get_all(self, category: str | None = None, user_id: str = "default_owner") -> list[PreferenceEntry]:
        self._ensure_user_loaded(user_id)
        seen_keys: set[str] = set()
        entries = []
        for k, e in list(self._cache.items()):
            uid = k[0] if isinstance(k, tuple) else getattr(e, "user_id", "default_owner")
            if uid == user_id and e.key not in seen_keys:
                seen_keys.add(e.key)
                entries.append(e)
        if category:
            entries = [e for e in entries if e.category == category]
        return entries
