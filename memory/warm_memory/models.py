import uuid
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


class WarmMemoryEntry(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    text: str
    user_id: str = "default_owner"
    category: str = "conversation"
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    score: float | None = None
    project: str | None = None
    component: str | None = None
    source_type: str | None = None
    source_uri: str | None = None
    source_version: str | None = None
    is_stale: bool = False
    supersedes: str | None = None
    sensitivity: str = "internal"

    def model_post_init(self, __context: Any) -> None:
        # Populate explicit fields from metadata if passed in metadata dict
        if self.metadata:
            if "user_id" in self.metadata and self.user_id == "default_owner":
                self.user_id = str(self.metadata["user_id"])
            if not self.project and "project" in self.metadata:
                self.project = self.metadata["project"]
            if not self.component and "component" in self.metadata:
                self.component = self.metadata["component"]
            if not self.source_type and "source_type" in self.metadata:
                self.source_type = self.metadata["source_type"]
            if not self.source_uri and "source_uri" in self.metadata:
                self.source_uri = self.metadata["source_uri"]
            if not self.supersedes and "supersedes" in self.metadata:
                self.supersedes = self.metadata["supersedes"]
            if "is_stale" in self.metadata:
                self.is_stale = bool(self.metadata["is_stale"])
            if "sensitivity" in self.metadata:
                self.sensitivity = str(self.metadata["sensitivity"])


class WarmIndexRequest(BaseModel):
    text: str
    user_id: str | None = None
    category: str = "conversation"
    metadata: dict[str, Any] = Field(default_factory=dict)
    entry_id: str | None = None


class WarmSearchRequest(BaseModel):
    query: str
    user_id: str | None = None
    limit: int = 5
    min_score: float = 0.3
    category: str | None = None


class WarmSearchResponse(BaseModel):
    query: str
    results: list[WarmMemoryEntry]
    count: int
    duration_ms: float


class WarmMemoryEntriesResponse(BaseModel):
    entries: list[WarmMemoryEntry]
    total_count: int
    next_cursor: str | None = None
