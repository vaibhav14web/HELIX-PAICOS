import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from foundation.event_bus.event_bus import EventBus, HelixEvent


from foundation.storage_manager.crypto import encrypt_string, decrypt_string


class ExplanationRecord(BaseModel):
    decision_id: str = Field(default_factory=lambda: os.urandom(16).hex())
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    action: str
    reasoning: str
    benefits: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    rationale: str = ""
    source_module: str = "unknown"
    outcome: str = "pending"


class ExplainabilityEngine:
    def __init__(self, event_bus: EventBus, storage_path: str | None = None):
        self._event_bus = event_bus
        from foundation.paths import get_helix_home
        self._storage_path = Path(storage_path or os.getenv(
            "HELIX_MEMORY_PATH",
            str(get_helix_home() / "memory"),
        ))
        self._explanations_path = self._storage_path / "explanations.jsonl"
        self._explanations_path.parent.mkdir(parents=True, exist_ok=True)
        self._subscriptions: list[str] = []

    async def start(self) -> None:
        self._event_bus.subscribe("memory.explain.log", self._handle_log)
        self._event_bus.subscribe("memory.explain.get", self._handle_get)
        self._subscriptions = ["memory.explain.log", "memory.explain.get"]

    async def stop(self) -> None:
        self._subscriptions.clear()

    async def _handle_log(self, event: HelixEvent) -> None:
        record = ExplanationRecord(
            action=event.payload.get("action", ""),
            reasoning=event.payload.get("reasoning", ""),
            benefits=event.payload.get("benefits", []),
            risks=event.payload.get("risks", []),
            alternatives=event.payload.get("alternatives", []),
            rationale=event.payload.get("rationale", ""),
            source_module=event.payload.get("source_module", "unknown"),
            outcome=event.payload.get("outcome", "pending"),
        )
        self._append_to_log(record)
        await self._event_bus.publish_event(
            source="explainability_engine",
            event_type="memory.explain.logged",
            payload={"decision_id": record.decision_id, "action": record.action},
            correlation_id=event.correlation_id,
        )

    async def _handle_get(self, event: HelixEvent) -> None:
        decision_id = event.payload.get("decision_id", "")
        if decision_id:
            record = self.find_by_id(decision_id)
            await self._event_bus.publish_event(
                source="explainability_engine",
                event_type="memory.explain.retrieved",
                payload={"decision_id": decision_id, "record": record.model_dump() if record else None},
                correlation_id=event.correlation_id,
            )
            return
        action = event.payload.get("action", "")
        limit = event.payload.get("limit", 10)
        if action:
            records = self.find_by_action(action, limit)
        else:
            records = self.get_recent(limit)
        await self._event_bus.publish_event(
            source="explainability_engine",
            event_type="memory.explain.retrieved",
            payload={"records": [r.model_dump() for r in records]},
            correlation_id=event.correlation_id,
        )

    def _read_lines(self) -> list[str]:
        if not self._explanations_path.exists():
            return []
        try:
            raw_bytes = self._explanations_path.read_bytes()
            content = decrypt_string(raw_bytes)
            return [line for line in content.splitlines() if line.strip()]
        except Exception:
            return []

    def _append_to_log(self, record: ExplanationRecord) -> None:
        lines = self._read_lines()
        lines.append(record.model_dump_json())
        text = "\n".join(lines) + "\n"
        self._explanations_path.write_bytes(encrypt_string(text))

    def find_by_id(self, decision_id: str) -> ExplanationRecord | None:
        for line in self._read_lines():
            try:
                record = ExplanationRecord(**json.loads(line))
                if record.decision_id == decision_id:
                    return record
            except (json.JSONDecodeError, ValueError):
                continue
        return None

    def find_by_action(self, action: str, limit: int = 10) -> list[ExplanationRecord]:
        results: list[ExplanationRecord] = []
        action_lower = action.lower()
        for line in self._read_lines():
            try:
                record = ExplanationRecord(**json.loads(line))
                if action_lower in record.action.lower():
                    results.append(record)
                    if len(results) >= limit:
                        break
            except (json.JSONDecodeError, ValueError):
                continue
        return results

    def get_recent(self, limit: int = 10) -> list[ExplanationRecord]:
        results: list[ExplanationRecord] = []
        for line in self._read_lines():
            try:
                results.append(ExplanationRecord(**json.loads(line)))
            except (json.JSONDecodeError, ValueError):
                continue
        return results[-limit:]
