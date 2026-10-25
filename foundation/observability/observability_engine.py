import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent

import threading

logger = logging.getLogger("helix.observability")


@dataclass
class TelemetryTrace:
    trace_id: str
    component: str
    action: str
    duration_ms: float
    status: str = "ok"  # ok | error
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ObservabilityEngine:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._traces: list[TelemetryTrace] = []
        self._max_traces = 500
        self._trace_seq = 0
        self._lock = threading.Lock()

    async def start(self) -> None:
        self._event_bus.subscribe("action.verified", self._handle_action_metric)
        self._event_bus.subscribe("voice.tts.complete", self._handle_tts_metric)
        logger.info("ObservabilityEngine started successfully")

    def record_trace(self, component: str, action: str, duration_ms: float, status: str = "ok", metadata: dict[str, Any] | None = None) -> TelemetryTrace:
        with self._lock:
            self._trace_seq += 1
            trace_id = f"trc-{self._trace_seq}"
            trace = TelemetryTrace(
                trace_id=trace_id,
                component=component,
                action=action,
                duration_ms=round(duration_ms, 2),
                status=status,
                metadata=metadata or {},
            )
            self._traces.append(trace)
            if len(self._traces) > self._max_traces:
                self._traces = self._traces[-self._max_traces:]
            return trace

    async def _handle_action_metric(self, event: HelixEvent) -> None:
        latency = float(event.payload.get("latency_ms", 0.0))
        action_id = event.payload.get("action_id", "unknown")
        self.record_trace("ActionExecutor", f"action_{action_id}", latency, "ok", event.payload)

    async def _handle_tts_metric(self, event: HelixEvent) -> None:
        duration_ms = float(event.payload.get("duration_ms") or event.payload.get("latency_ms") or 180.0)
        self.record_trace("VoiceEngine", "tts_synthesis", duration_ms, "ok", event.payload)

    async def stop(self) -> None:
        self._event_bus.unsubscribe("action.verified", self._handle_action_metric)
        self._event_bus.unsubscribe("voice.tts.complete", self._handle_tts_metric)
        logger.info("ObservabilityEngine stopped")

    def get_recent_traces(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return [t.to_dict() for t in self._traces[-limit:]]
