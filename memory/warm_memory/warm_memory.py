import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Sequence

from foundation.event_bus.event_bus import EventBus, HelixEvent
from memory.warm_memory.models import WarmMemoryEntry
from memory.warm_memory.embedding_engine import EmbeddingEngine
from memory.warm_memory.vector_store import BaseVectorStore, create_vector_store

logger = logging.getLogger("helix.memory.warm_memory")


class WarmMemory:
    """Warm Memory Engine for HELIX (Phase 2 RAG & 8-30 day semantic retention).
    
    Operates the middle tier between Hot RAM (0-7d) and Cold Archive (30+d)
    as specified in ADR-019 and ADR-020.
    """

    def __init__(
        self,
        event_bus: EventBus,
        vector_store: BaseVectorStore | None = None,
        embedding_engine: EmbeddingEngine | None = None,
        storage_path: Path | str | None = None,
    ):
        self._event_bus = event_bus
        if storage_path is not None:
            self._storage_path = Path(storage_path)
        else:
            env_mem = os.getenv("HELIX_MEMORY_PATH")
            if env_mem and env_mem.strip():
                from foundation.paths import expand_path
                self._storage_path = expand_path(env_mem.strip()) or Path(env_mem.strip())
            else:
                from foundation.paths import get_runtime_dir
                self._storage_path = get_runtime_dir() / "memory"
        self._qdrant_dir = self._storage_path / "qdrant"

        self._embedding_engine = embedding_engine or EmbeddingEngine()
        self._vector_store = vector_store or create_vector_store(
            storage_path=self._qdrant_dir,
            dimension=self._embedding_engine.dimension,
        )

        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._is_running = False

    async def start(self) -> None:
        if self._is_running:
            return

        # Ensure embedding engine is ready
        self._embedding_engine.initialize()

        self._event_handler_map = {
            "memory.warm.index": self._handle_index,
            "memory.warm.search": self._handle_search,
            "memory.warm.delete": self._handle_delete,
            "memory.warm.rollover": self._handle_rollover,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())
        self._is_running = True
        logger.info("Warm Memory Engine started successfully")

    async def stop(self) -> None:
        if not self._is_running:
            return
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        await self._vector_store.close()
        self._is_running = False
        logger.info("Warm Memory Engine stopped")

    # ── Core Operations ───────────────────────────────────────────

    async def index_entry(self, entry: WarmMemoryEntry) -> str:
        """Embed and upsert a single warm memory entry."""
        async with self._lock:
            vector = self._embedding_engine.embed_query(entry.text)
            await self._vector_store.upsert([entry], [vector])
            return entry.id

    async def index_text(
        self,
        text: str,
        user_id: str = "default_owner",
        category: str = "conversation",
        metadata: dict[str, Any] | None = None,
        entry_id: str | None = None,
    ) -> WarmMemoryEntry:
        """Create, embed, and store text in Warm Memory with user ownership."""
        kwargs: dict[str, Any] = {
            "text": text.strip(),
            "user_id": user_id,
            "category": category,
            "metadata": metadata or {},
        }
        if entry_id:
            kwargs["id"] = entry_id
        entry = WarmMemoryEntry(**kwargs)
        await self.index_entry(entry)
        return entry

    async def index_batch(self, entries: Sequence[WarmMemoryEntry]) -> int:
        """Batch embed and upsert multiple entries."""
        if not entries:
            return 0
        async with self._lock:
            texts = [e.text for e in entries]
            vectors = self._embedding_engine.embed_documents(texts)
            await self._vector_store.upsert(entries, vectors)
            return len(entries)

    async def search(
        self,
        query: str,
        user_id: str | None = None,
        limit: int = 5,
        min_score: float = 0.3,
        category: str | None = None,
        project: str | None = None,
    ) -> list[WarmMemoryEntry]:
        """Perform semantic similarity search scoped strictly to the specified user_id."""
        if not query.strip():
            return []
        if user_id is None:
            user_id = "default_owner"
        elif not user_id:
            return []

        t0 = time.perf_counter()
        query_vector = self._embedding_engine.embed_query(query.strip())
        results = await self._vector_store.search(
            query_vector=query_vector,
            user_id=user_id,
            limit=limit,
            min_score=min_score,
            category=category,
            project=project,
        )
        dur_ms = (time.perf_counter() - t0) * 1000
        logger.debug("Warm search for '%s' (user=%s) returned %d results in %.2fms", query, user_id, len(results), dur_ms)
        return results

    async def delete(self, entry_id: str, user_id: str | None = None) -> bool:
        """Remove a vector entry by ID enforcing user ownership."""
        async with self._lock:
            return await self._vector_store.delete(entry_id, user_id=user_id)

    async def list_entries(
        self,
        user_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        category: str | None = None,
    ) -> tuple[list[WarmMemoryEntry], str | None]:
        """List one user's memory entries without exposing another tenant's records."""
        if user_id == "":
            return [], None
        return await self._vector_store.list_entries(
            user_id=user_id,
            limit=limit,
            cursor=cursor,
            category=category,
        )

    async def count_entries(self, user_id: str | None = None, category: str | None = None) -> int:
        """Count entries for one owner, defaulting to the local owner's partition."""
        if user_id == "":
            return 0
        return await self._vector_store.count(
            user_id=user_id or "default_owner",
            category=category,
        )

    async def get_stats(self, user_id: str | None = None) -> dict[str, Any]:
        """Return warm memory telemetry."""
        count = await self.count_entries(user_id=user_id)
        return {
            "tier": "warm_memory",
            "backend": self._vector_store.__class__.__name__,
            "vector_count": count,
            "dimension": self._embedding_engine.dimension,
            "storage_path": str(self._storage_path),
            "status": "ready" if self._is_running else "stopped",
        }

    # ── Rollover & 8-30 Day Retention ─────────────────────────────

    async def rollover_conversations(
        self,
        conversation_dir: Path | str | None = None,
        min_age_days: int = 7,
        max_age_days: int = 30,
    ) -> int:
        """Index conversation sessions that fall into the 8-30 day warm window."""
        conv_path = Path(conversation_dir or (self._storage_path / "conversations"))
        archived_path = self._storage_path / "archived" / "conversations"

        target_paths = list(conv_path.glob("*.jsonl"))
        if archived_path.exists():
            target_paths.extend(archived_path.glob("*.jsonl"))

        now = datetime.now(timezone.utc)
        min_cutoff = now - timedelta(days=min_age_days)
        max_cutoff = now - timedelta(days=max_age_days)

        entries_to_index: list[WarmMemoryEntry] = []

        for p in target_paths:
            parts = p.stem.split("_")
            date_str = parts[-1]
            try:
                file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                continue

            # Within warm memory window [8, 30] days
            if max_cutoff <= file_date <= min_cutoff:
                try:
                    from foundation.storage_manager.crypto import decrypt_string
                    raw_bytes = p.read_bytes()
                    try:
                        content = decrypt_string(raw_bytes)
                    except Exception:
                        content = raw_bytes.decode("utf-8", errors="replace")

                    for line in content.splitlines():
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        text = data.get("content", "").strip()
                        if len(text) > 5:
                            entry = WarmMemoryEntry(
                                text=text,
                                category="conversation",
                                metadata={
                                    "session_id": data.get("session_id", ""),
                                    "role": data.get("role", "user"),
                                    "date": date_str,
                                    "source_file": p.name,
                                },
                                timestamp=data.get("timestamp", file_date.isoformat()),
                            )
                            entries_to_index.append(entry)
                except Exception as e:
                    logger.warning("Error reading conversation file %s for warm rollover: %s", p.name, e)

        if entries_to_index:
            indexed_count = await self.index_batch(entries_to_index)
            logger.info("Rolled over %d conversation turns into Warm Memory vector store", indexed_count)
            return indexed_count
        return 0

    # ── EventBus Handlers ─────────────────────────────────────────

    async def _handle_index(self, event: HelixEvent) -> None:
        text = event.payload.get("text", "")
        if not text:
            return
        user_id = event.payload.get("user_id", "default_owner")
        category = event.payload.get("category", "conversation")
        metadata = event.payload.get("metadata", {})
        entry_id = event.payload.get("entry_id")

        entry = await self.index_text(text=text, user_id=user_id, category=category, metadata=metadata, entry_id=entry_id)
        await self._event_bus.publish_event(
            source="warm_memory",
            event_type="memory.warm.indexed",
            payload={"id": entry.id, "user_id": user_id, "category": category},
            correlation_id=event.correlation_id,
        )

    async def _handle_search(self, event: HelixEvent) -> None:
        query = event.payload.get("query", "")
        user_id = event.payload.get("user_id")
        limit = event.payload.get("limit", 5)
        min_score = event.payload.get("min_score", 0.3)
        category = event.payload.get("category")
        project = event.payload.get("project")

        results = await self.search(query=query, user_id=user_id, limit=limit, min_score=min_score, category=category, project=project)
        await self._event_bus.publish_event(
            source="warm_memory",
            event_type="memory.warm.searched",
            payload={
                "query": query,
                "user_id": user_id,
                "results": [r.model_dump() for r in results],
                "count": len(results),
            },
            correlation_id=event.correlation_id,
        )

    async def _handle_delete(self, event: HelixEvent) -> None:
        entry_id = event.payload.get("entry_id", "")
        user_id = event.payload.get("user_id")
        success = await self.delete(entry_id, user_id=user_id)
        await self._event_bus.publish_event(
            source="warm_memory",
            event_type="memory.warm.deleted",
            payload={"entry_id": entry_id, "user_id": user_id, "success": success},
            correlation_id=event.correlation_id,
        )

    async def _handle_rollover(self, event: HelixEvent) -> None:
        count = await self.rollover_conversations()
        await self._event_bus.publish_event(
            source="warm_memory",
            event_type="memory.warm.rollover_completed",
            payload={"indexed_count": count},
            correlation_id=event.correlation_id,
        )
