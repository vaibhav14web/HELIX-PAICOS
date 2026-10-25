import os
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
import numpy as np

from foundation.event_bus.event_bus import EventBus, HelixEvent
from foundation.storage_manager.crypto import encrypt_string, decrypt_string

logger = logging.getLogger("helix.procedural_memory")


class ProceduralStep(BaseModel):
    """An individual step in a learned procedural workflow."""
    step_index: int = 0
    action: str
    target: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[int] = Field(default_factory=list)
    notes: str = ""


class ProceduralWorkflow(BaseModel):
    """
    A learned, repeatable workflow describing how the user typically performs a recurring task.
    """
    workflow_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_id: str = "default_owner"
    name: str
    trigger_intent: str
    keywords: list[str] = Field(default_factory=list)
    project_scope: str | None = None  # None = global, or specific project identifier
    steps: list[ProceduralStep] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence_count: int = 1
    success_count: int = 0
    failure_count: int = 0
    user_verified: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_executed: str | None = None

    def record_run(self, success: bool) -> None:
        """Update metrics and confidence based on an execution outcome."""
        self.last_executed = datetime.now(timezone.utc).isoformat()
        if success:
            self.success_count += 1
            if not self.user_verified:
                self.confidence = min(1.0, round(self.confidence + 0.15 * (1.0 - self.confidence), 4))
        else:
            self.failure_count += 1
            if not self.user_verified:
                self.confidence = max(0.1, round(self.confidence - 0.25, 4))


class ProceduralMemory:
    """
    Procedural & Workflow Memory Engine for HELIX.
    Stores how the user typically structures tasks into execution steps,
    partitioned and encrypted at rest per user.
    """

    def __init__(self, event_bus: EventBus, storage_path: str | None = None, embedding_engine: Any | None = None):
        self._event_bus = event_bus
        from foundation.paths import get_helix_home
        self._storage_dir = Path(storage_path or os.getenv(
            "HELIX_MEMORY_PATH",
            str(get_helix_home() / "memory"),
        ))
        self._file_path = self._storage_dir / "workflows.enc"

        # Keyed by user_id -> {workflow_id: ProceduralWorkflow}
        self._workflows: dict[str, dict[str, ProceduralWorkflow]] = {}
        self._loaded_users: set[str] = set()
        self._dirty_users: set[str] = set()
        self._subscriptions: list[str] = []
        self._embedding_engine = embedding_engine

    def _get_user_file_path(self, user_id: str) -> Path:
        if not user_id or user_id in ("default", "default_owner", "global"):
            return self._file_path
        try:
            from foundation.storage_manager.tenant_storage import get_tenant_storage_manager
            return get_tenant_storage_manager().get_user_workflows_dir(user_id) / "workflows.enc"
        except Exception:
            return self._file_path

    def _get_embedding_engine(self) -> Any | None:
        if self._embedding_engine is not None:
            return self._embedding_engine
        try:
            from memory.warm_memory.embedding_engine import EmbeddingEngine
            self._embedding_engine = EmbeddingEngine()
            self._embedding_engine.initialize()
            return self._embedding_engine
        except Exception as e:
            logger.debug("EmbeddingEngine unavailable for procedural memory: %s", e)
            return None

    async def start(self) -> None:
        self._ensure_user_loaded("default_owner")
        handlers = {
            "memory.procedural.record": self._handle_record,
            "memory.procedural.get": self._handle_get,
            "memory.procedural.find": self._handle_find,
            "memory.procedural.execution_result": self._handle_execution_result,
            "memory.procedural.list": self._handle_list,
            "memory.procedural.delete": self._handle_delete,
        }
        for event_type, handler in handlers.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(handlers.keys())
        logger.info("ProceduralMemory started successfully")

    async def stop(self) -> None:
        for uid in list(self._dirty_users):
            self.save_to_disk(uid)
        for event_type in self._subscriptions:
            self._event_bus.unsubscribe(event_type, None)
        self._subscriptions.clear()
        logger.info("ProceduralMemory stopped")

    def _ensure_user_loaded(self, user_id: str) -> None:
        if user_id in self._loaded_users:
            return
        self._load_from_disk(user_id)
        self._loaded_users.add(user_id)

    def save_workflow(self, workflow: ProceduralWorkflow) -> ProceduralWorkflow:
        """Store or update a procedural workflow for its owner."""
        uid = workflow.user_id
        self._ensure_user_loaded(uid)
        if uid not in self._workflows:
            self._workflows[uid] = {}
        self._workflows[uid][workflow.workflow_id] = workflow
        self._dirty_users.add(uid)
        return workflow

    def get_workflow(self, workflow_id: str, user_id: str | None = None) -> ProceduralWorkflow | None:
        effective_user = user_id or "default_owner"
        self._ensure_user_loaded(effective_user)
        user_wfs = self._workflows.get(effective_user, {})
        wf = user_wfs.get(workflow_id)
        if wf:
            return wf
        # Fallback to scanning loaded users only if user_id was not explicitly specified
        if user_id is None:
            for uid, wfs in self._workflows.items():
                if workflow_id in wfs:
                    return wfs[workflow_id]
        return None

    def delete_workflow(self, workflow_id: str, user_id: str = "default_owner") -> bool:
        self._ensure_user_loaded(user_id)
        user_wfs = self._workflows.get(user_id, {})
        if workflow_id in user_wfs:
            del user_wfs[workflow_id]
            self._dirty_users.add(user_id)
            return True
        return False

    def list_workflows(self, project_scope: str | None = None, user_id: str = "default_owner") -> list[ProceduralWorkflow]:
        self._ensure_user_loaded(user_id)
        workflows = list(self._workflows.get(user_id, {}).values())
        if project_scope is not None:
            workflows = [w for w in workflows if w.project_scope in (None, project_scope)]
        return workflows

    def get_all(self, project_scope: str | None = None, user_id: str = "default_owner") -> list[ProceduralWorkflow]:
        return self.list_workflows(project_scope, user_id=user_id)

    def find_matching_workflow(
        self,
        intent: str,
        project_scope: str | None = None,
        min_confidence: float = 0.4,
        user_id: str = "default_owner",
    ) -> ProceduralWorkflow | None:
        """
        Find the most confident and relevant procedural workflow for an intent belonging to user_id.
        """
        self._ensure_user_loaded(user_id)
        intent_lower = intent.lower().strip()
        intent_words = set(intent_lower.split())

        engine = self._get_embedding_engine()
        query_vec = None
        if engine is not None:
            try:
                query_vec = engine.embed_query(intent)
            except Exception as e:
                logger.debug("Failed query embedding: %s", e)

        candidates: list[tuple[float, ProceduralWorkflow]] = []

        for wf in self._workflows.get(user_id, {}).values():
            if wf.confidence < min_confidence:
                continue

            scope_score = 0.0
            if project_scope and wf.project_scope:
                if wf.project_scope.lower() == project_scope.lower():
                    scope_score = 2.0
                else:
                    continue
            elif wf.project_scope is None:
                scope_score = 1.0

            lexical_score = 0.0
            wf_trigger = wf.trigger_intent.lower()
            if wf_trigger in intent_lower or intent_lower in wf_trigger:
                lexical_score += 4.0

            wf_keywords = {k.lower() for k in wf.keywords}
            overlap = len(intent_words & wf_keywords)
            lexical_score += overlap * 1.5

            semantic_score = 0.0
            if query_vec is not None and engine is not None:
                try:
                    wf_text = f"{wf.name} {wf.trigger_intent} {' '.join(wf.keywords)}"
                    wf_vec = engine.embed_query(wf_text)
                    cosine_sim = float(np.dot(query_vec, wf_vec))
                    if lexical_score > 0.0 and cosine_sim >= 0.40:
                        semantic_score = cosine_sim * 3.0
                    elif lexical_score == 0.0 and cosine_sim >= 0.60:
                        semantic_score = cosine_sim * 4.0
                except Exception as sim_err:
                    logger.debug("Semantic similarity calculation error: %s", sim_err)

            match_score = lexical_score + semantic_score

            if match_score > 0.0:
                total_score = (match_score + scope_score) * wf.confidence
                if wf.user_verified:
                    total_score *= 1.3
                candidates.append((total_score, wf))

        if not candidates:
            return None

        candidates.sort(key=lambda c: c[0], reverse=True)
        return candidates[0][1]

    def record_execution(self, workflow_id: str, success: bool, user_id: str = "default_owner") -> ProceduralWorkflow | None:
        """Record the outcome of running a workflow and update confidence."""
        self._ensure_user_loaded(user_id)
        user_wfs = self._workflows.get(user_id, {})
        wf = user_wfs.get(workflow_id)
        if wf:
            wf.record_run(success=success)
            self._dirty_users.add(user_id)
            return wf
        return None

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
                self._workflows[user_id] = {
                    d["workflow_id"]: ProceduralWorkflow(**d)
                    for d in data if isinstance(d, dict) and "workflow_id" in d
                }
            self._dirty_users.discard(user_id)
        except Exception as e:
            logger.warning("Could not load procedural memory for user %s (%s)", user_id, e)

    def save_to_disk(self, user_id: str = "default_owner") -> None:
        try:
            path = self._get_user_file_path(user_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            wfs = list(self._workflows.get(user_id, {}).values())
            content = json.dumps([w.model_dump() for w in wfs], indent=2)
            encrypted = encrypt_string(content)
            path.write_bytes(encrypted)
            self._dirty_users.discard(user_id)
        except Exception as e:
            logger.error("Failed to save procedural memory for user %s: %s", user_id, e)

    # ── EventBus Handlers ─────────────────────────────────────────

    async def _handle_record(self, event: HelixEvent) -> None:
        payload = event.payload
        user_id = payload.get("user_id", "default_owner")
        steps_raw = payload.get("steps", [])
        steps = [
            ProceduralStep(**s) if isinstance(s, dict) else s
            for s in steps_raw
        ]

        wf = ProceduralWorkflow(
            user_id=user_id,
            name=payload.get("name", "Unnamed Workflow"),
            trigger_intent=payload.get("trigger_intent", ""),
            keywords=payload.get("keywords", []),
            project_scope=payload.get("project_scope"),
            steps=steps,
            confidence=float(payload.get("confidence", 0.5)),
            user_verified=bool(payload.get("user_verified", False)),
        )
        self.save_workflow(wf)
        if user_id in self._dirty_users:
            self.save_to_disk(user_id)

        await self._event_bus.publish_event(
            source="procedural_memory",
            event_type="memory.procedural.recorded",
            payload={"workflow": wf.model_dump(), "user_id": user_id},
            correlation_id=event.correlation_id,
        )

    async def _handle_get(self, event: HelixEvent) -> None:
        wf_id = event.payload.get("workflow_id", "")
        user_id = event.payload.get("user_id", "default_owner")
        wf = self.get_workflow(wf_id, user_id=user_id)
        await self._event_bus.publish_event(
            source="procedural_memory",
            event_type="memory.procedural.retrieved",
            payload={"workflow": wf.model_dump() if wf else None, "user_id": user_id},
            correlation_id=event.correlation_id,
        )

    async def _handle_find(self, event: HelixEvent) -> None:
        intent = event.payload.get("intent", "")
        scope = event.payload.get("project_scope")
        min_conf = float(event.payload.get("min_confidence", 0.4))
        user_id = event.payload.get("user_id", "default_owner")

        wf = self.find_matching_workflow(intent=intent, project_scope=scope, min_confidence=min_conf, user_id=user_id)
        await self._event_bus.publish_event(
            source="procedural_memory",
            event_type="memory.procedural.matched",
            payload={"workflow": wf.model_dump() if wf else None, "intent": intent, "user_id": user_id},
            correlation_id=event.correlation_id,
        )

    async def _handle_execution_result(self, event: HelixEvent) -> None:
        wf_id = event.payload.get("workflow_id", "")
        success = bool(event.payload.get("success", True))
        user_id = event.payload.get("user_id", "default_owner")
        wf = self.record_execution(wf_id, success=success, user_id=user_id)
        if user_id in self._dirty_users:
            self.save_to_disk(user_id)

        await self._event_bus.publish_event(
            source="procedural_memory",
            event_type="memory.procedural.updated",
            payload={"workflow": wf.model_dump() if wf else None, "user_id": user_id},
            correlation_id=event.correlation_id,
        )

    async def _handle_list(self, event: HelixEvent) -> None:
        scope = event.payload.get("project_scope")
        user_id = event.payload.get("user_id", "default_owner")
        workflows = self.list_workflows(project_scope=scope, user_id=user_id)
        await self._event_bus.publish_event(
            source="procedural_memory",
            event_type="memory.procedural.all_retrieved",
            payload={"workflows": [w.model_dump() for w in workflows], "user_id": user_id},
            correlation_id=event.correlation_id,
        )

    async def _handle_delete(self, event: HelixEvent) -> None:
        wf_id = event.payload.get("workflow_id", "")
        user_id = event.payload.get("user_id", "default_owner")
        deleted = self.delete_workflow(wf_id, user_id=user_id)
        if user_id in self._dirty_users:
            self.save_to_disk(user_id)

        await self._event_bus.publish_event(
            source="procedural_memory",
            event_type="memory.procedural.deleted",
            payload={"workflow_id": wf_id, "deleted": deleted, "user_id": user_id},
            correlation_id=event.correlation_id,
        )
