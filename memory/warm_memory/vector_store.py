import abc
import logging
import os
from pathlib import Path
from typing import Any, Sequence
import numpy as np

from memory.warm_memory.models import WarmMemoryEntry

logger = logging.getLogger("helix.memory.vector_store")

COLLECTION_NAME = "helix_warm_memory"


class BaseVectorStore(abc.ABC):
    """Abstract interface for vector database backends."""

    @abc.abstractmethod
    async def upsert(self, entries: Sequence[WarmMemoryEntry], vectors: Sequence[Sequence[float]]) -> None:
        """Insert or update entries with their vector representations."""
        pass

    @abc.abstractmethod
    async def search(
        self,
        query_vector: Sequence[float],
        user_id: str | None = None,
        limit: int = 5,
        min_score: float = 0.0,
        category: str | None = None,
        project: str | None = None,
    ) -> list[WarmMemoryEntry]:
        """Perform cosine similarity search with mandatory user ownership scoping."""
        pass

    @abc.abstractmethod
    async def list_entries(
        self,
        user_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        category: str | None = None,
    ) -> tuple[list[WarmMemoryEntry], str | None]:
        """List memory entries for one owner and return the next opaque page cursor."""
        pass

    @abc.abstractmethod
    async def delete(self, entry_id: str, user_id: str | None = None) -> bool:
        """Delete an entry by ID, enforcing user ownership."""
        pass

    @abc.abstractmethod
    async def count(self, user_id: str | None = None, category: str | None = None) -> int:
        """Return total number of vectors in the store."""
        pass

    @abc.abstractmethod
    async def clear(self) -> None:
        """Clear all entries."""
        pass

    @abc.abstractmethod
    async def close(self) -> None:
        """Close database connections and release resources."""
        pass


class QdrantVectorStore(BaseVectorStore):
    """Qdrant vector store adapter supporting both remote Docker and embedded local disk mode.
    
    Per ADR-019 and Hardware Storage Rules:
    - Embedded mode persists to Laptop SSD (HELIX_MEMORY_PATH/qdrant).
    - Can connect to Docker Qdrant at http://127.0.0.1:6333 when running.
    """

    def __init__(
        self,
        collection_name: str = COLLECTION_NAME,
        dimension: int = 384,
        url: str | None = None,
        path: Path | str | None = None,
    ):
        self._collection_name = collection_name
        self._dimension = dimension
        self._url = url
        self._path = Path(path) if path and path != ":memory:" else path
        self._client = None
        self._init_client()

    def _init_client(self) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        if self._url:
            try:
                self._client = QdrantClient(url=self._url, timeout=5.0)
                # Test connectivity
                self._client.get_collections()
                logger.info("Connected to remote Qdrant at %s", self._url)
            except Exception as e:
                logger.warning("Failed to connect to remote Qdrant at %s: %s. Falling back to local storage.", self._url, e)
                self._client = None

        if self._client is None:
            if self._path == ":memory:":
                self._client = QdrantClient(":memory:")
                logger.info("Initialized in-memory Qdrant instance")
            else:
                if self._path:
                    local_dir = Path(self._path)
                else:
                    from foundation.paths import get_qdrant_path
                    local_dir = get_qdrant_path()
                local_dir.mkdir(parents=True, exist_ok=True)
                self._client = QdrantClient(path=str(local_dir))
                logger.info("Initialized local disk Qdrant instance at %s", local_dir)

        # Ensure collection exists
        collections = [c.name for c in self._client.get_collections().collections]
        if self._collection_name not in collections:
            self._client.create_collection(
                collection_name=self._collection_name,
                vectors_config=VectorParams(size=self._dimension, distance=Distance.COSINE),
            )
            logger.info("Created Qdrant collection: %s (dim=%d, metric=COSINE)", self._collection_name, self._dimension)
            try:
                from qdrant_client.models import PayloadSchemaType
                self._client.create_payload_index(
                    collection_name=self._collection_name,
                    field_name="category",
                    field_schema=PayloadSchemaType.KEYWORD,
                )
                self._client.create_payload_index(
                    collection_name=self._collection_name,
                    field_name="metadata.project",
                    field_schema=PayloadSchemaType.KEYWORD,
                )
                self._client.create_payload_index(
                    collection_name=self._collection_name,
                    field_name="user_id",
                    field_schema=PayloadSchemaType.KEYWORD,
                )
            except Exception as e:
                logger.debug("Qdrant payload index note: %s", e)

    async def upsert(self, entries: Sequence[WarmMemoryEntry], vectors: Sequence[Sequence[float]]) -> None:
        if not entries:
            return
        from qdrant_client.models import PointStruct

        points = []
        for entry, vec in zip(entries, vectors):
            # Ensure valid UUID or string
            point_id = entry.id
            payload = {
                "text": entry.text,
                "user_id": entry.user_id,
                "category": entry.category,
                "metadata": entry.metadata,
                "timestamp": entry.timestamp,
            }
            points.append(PointStruct(id=point_id, vector=list(vec), payload=payload))

        self._client.upsert(collection_name=self._collection_name, points=points)

    async def search(
        self,
        query_vector: Sequence[float],
        user_id: str | None = None,
        limit: int = 5,
        min_score: float = 0.0,
        category: str | None = None,
        project: str | None = None,
    ) -> list[WarmMemoryEntry]:
        if user_id is None:
            user_id = "default_owner"
        elif not user_id:
            # Server-enforced ownership filter: empty user_id fails safe
            return []

        from qdrant_client.models import Filter, FieldCondition, MatchValue

        conditions = [FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        if category:
            conditions.append(FieldCondition(key="category", match=MatchValue(value=category)))
        if project:
            conditions.append(FieldCondition(key="metadata.project", match=MatchValue(value=project)))

        query_filter = Filter(must=conditions)

        results = self._client.query_points(
            collection_name=self._collection_name,
            query=list(query_vector),
            query_filter=query_filter,
            limit=limit,
            score_threshold=min_score if min_score > 0 else None,
        ).points

        matched: list[WarmMemoryEntry] = []
        for r in results:
            payload = r.payload or {}
            entry = WarmMemoryEntry(
                id=str(r.id),
                text=payload.get("text", ""),
                user_id=payload.get("user_id", "default_owner"),
                category=payload.get("category", "conversation"),
                metadata=payload.get("metadata", {}),
                timestamp=payload.get("timestamp", ""),
                score=float(r.score) if r.score is not None else None,
            )
            matched.append(entry)
        return matched

    async def list_entries(
        self,
        user_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        category: str | None = None,
    ) -> tuple[list[WarmMemoryEntry], str | None]:
        if user_id is None:
            user_id = "default_owner"
        elif not user_id:
            return [], None

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        conditions = [FieldCondition(key="user_id", match=MatchValue(value=user_id))]
        if category:
            conditions.append(FieldCondition(key="category", match=MatchValue(value=category)))
        offset: int | str | None = cursor
        if cursor and cursor.isdecimal():
            offset = int(cursor)
        points, next_offset = self._client.scroll(
            collection_name=self._collection_name,
            scroll_filter=Filter(must=conditions),
            limit=limit,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        entries: list[WarmMemoryEntry] = []
        for point in points:
            payload = point.payload or {}
            entries.append(WarmMemoryEntry(
                id=str(point.id),
                text=payload.get("text", ""),
                user_id=payload.get("user_id", "default_owner"),
                category=payload.get("category", "conversation"),
                metadata=payload.get("metadata", {}),
                timestamp=payload.get("timestamp", ""),
            ))
        return entries, str(next_offset) if next_offset is not None else None

    async def delete(self, entry_id: str, user_id: str | None = None) -> bool:
        from qdrant_client.models import PointIdsList
        target_uid = user_id if user_id is not None else "default_owner"
        try:
            pts = self._client.retrieve(collection_name=self._collection_name, ids=[entry_id])
            if not pts or (pts[0].payload or {}).get("user_id") != target_uid:
                return False
        except Exception:
            return False
        res = self._client.delete(
            collection_name=self._collection_name,
            points_selector=PointIdsList(points=[entry_id]),
        )
        return res.status == "completed"

    async def count(self, user_id: str | None = None, category: str | None = None) -> int:
        if user_id is None and category is None:
            return self._client.count(collection_name=self._collection_name).count
        if user_id == "":
            return 0

        from qdrant_client.models import FieldCondition, Filter, MatchValue

        conditions = []
        if user_id is not None:
            conditions.append(FieldCondition(key="user_id", match=MatchValue(value=user_id)))
        if category:
            conditions.append(FieldCondition(key="category", match=MatchValue(value=category)))
        return self._client.count(
            collection_name=self._collection_name,
            count_filter=Filter(must=conditions),
        ).count

    async def clear(self) -> None:
        self._client.delete_collection(collection_name=self._collection_name)
        from qdrant_client.models import Distance, VectorParams
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=VectorParams(size=self._dimension, distance=Distance.COSINE),
        )

    async def close(self) -> None:
        if self._client:
            self._client.close()


class InMemoryVectorStore(BaseVectorStore):
    """Pure NumPy in-memory vector store for testing and fallback."""

    def __init__(self, dimension: int = 384):
        self._dimension = dimension
        self._ids: list[str] = []
        self._entries: dict[str, WarmMemoryEntry] = {}
        self._vectors: list[np.ndarray] = []

    async def upsert(self, entries: Sequence[WarmMemoryEntry], vectors: Sequence[Sequence[float]]) -> None:
        for entry, vec in zip(entries, vectors):
            v = np.array(vec, dtype=np.float32)
            norm = np.linalg.norm(v)
            if norm > 1e-12:
                v = v / norm

            if entry.id in self._entries:
                idx = self._ids.index(entry.id)
                self._vectors[idx] = v
                self._entries[entry.id] = entry
            else:
                self._ids.append(entry.id)
                self._vectors.append(v)
                self._entries[entry.id] = entry

    async def search(
        self,
        query_vector: Sequence[float],
        user_id: str | None = None,
        limit: int = 5,
        min_score: float = 0.0,
        category: str | None = None,
        project: str | None = None,
    ) -> list[WarmMemoryEntry]:
        if user_id is None:
            user_id = "default_owner"
        elif not user_id:
            # Explicit empty user_id MUST FAIL SAFE
            return []
        if not self._ids:
            return []

        q = np.array(query_vector, dtype=np.float32)
        norm = np.linalg.norm(q)
        if norm > 1e-12:
            q = q / norm

        matrix = np.stack(self._vectors, axis=0)  # shape (N, D)
        scores = np.dot(matrix, q)  # cosine similarity

        candidates = []
        for i, score in enumerate(scores):
            entry_id = self._ids[i]
            entry = self._entries[entry_id]
            if entry.user_id != user_id:
                continue
            if category and entry.category != category:
                continue
            if project:
                entry_project = (entry.metadata or {}).get("project")
                if entry_project and entry_project.lower() != project.lower():
                    continue
            if score >= min_score:
                entry_copy = entry.model_copy()
                entry_copy.score = float(score)
                candidates.append(entry_copy)

        candidates.sort(key=lambda x: (x.score or 0.0), reverse=True)
        return candidates[:limit]

    async def list_entries(
        self,
        user_id: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
        category: str | None = None,
    ) -> tuple[list[WarmMemoryEntry], str | None]:
        if user_id is None:
            user_id = "default_owner"
        elif not user_id:
            return [], None

        matching = [
            entry for entry in self._entries.values()
            if entry.user_id == user_id and (category is None or entry.category == category)
        ]
        matching.sort(key=lambda entry: entry.timestamp, reverse=True)
        offset = int(cursor) if cursor else 0
        if offset < 0:
            raise ValueError("Cursor must be a non-negative offset")
        page = matching[offset:offset + limit]
        next_offset = offset + len(page)
        next_cursor = str(next_offset) if next_offset < len(matching) else None
        return [entry.model_copy() for entry in page], next_cursor

    async def delete(self, entry_id: str, user_id: str | None = None) -> bool:
        target_uid = user_id if user_id is not None else "default_owner"
        if entry_id in self._entries:
            if self._entries[entry_id].user_id != target_uid:
                return False
            idx = self._ids.index(entry_id)
            self._ids.pop(idx)
            self._vectors.pop(idx)
            del self._entries[entry_id]
            return True
        return False

    async def count(self, user_id: str | None = None, category: str | None = None) -> int:
        if user_id is None and category is None:
            return len(self._ids)
        if user_id == "":
            return 0
        return sum(
            1 for entry in self._entries.values()
            if (user_id is None or entry.user_id == user_id)
            and (category is None or entry.category == category)
        )

    async def clear(self) -> None:
        self._ids.clear()
        self._vectors.clear()
        self._entries.clear()

    async def close(self) -> None:
        await self.clear()


def create_vector_store(
    backend: str = "auto",
    storage_path: Path | str | None = None,
    url: str | None = None,
    dimension: int = 384,
) -> BaseVectorStore:
    """Factory creating appropriate vector database backend."""
    target_backend = (backend or os.getenv("HELIX_VECTOR_BACKEND", "auto")).lower()
    target_url = url or os.getenv("HELIX_QDRANT_URL", None)
    target_path = storage_path or os.getenv("HELIX_QDRANT_PATH", None)

    if target_backend == "memory":
        return InMemoryVectorStore(dimension=dimension)

    if target_backend in ("qdrant", "auto"):
        try:
            return QdrantVectorStore(
                dimension=dimension,
                url=target_url,
                path=target_path,
            )
        except Exception as e:
            logger.warning("Could not initialize Qdrant vector store (%s), falling back to InMemoryVectorStore", e)
            return InMemoryVectorStore(dimension=dimension)

    return InMemoryVectorStore(dimension=dimension)
