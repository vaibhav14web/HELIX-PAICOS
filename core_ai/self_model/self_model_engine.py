import asyncio
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.self_model")


@dataclass
class SystemResourceState:
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_used_mb: float = 0.0
    gpu_percent: float = 0.0
    gpu_memory_used_mb: float = 0.0
    battery_percent: float | None = None
    is_charging: bool = True
    internet_connected: bool = True


@dataclass
class EngineStatus:
    llm_backend: str = "ollama"
    llm_loaded_model: str = "qwen2.5-coder-3b-instruct:latest"
    llm_status: str = "ready"  # idle | loading | generating | ready | error
    stt_status: str = "ready"
    tts_status: str = "ready"
    active_session_id: str = "default"
    listening_state: bool = False
    speaking_state: bool = False


@dataclass
class SelfStateVector:
    identity: str = "HELIX PAIOS"
    version: str = "2.0.0"
    health_status: str = "healthy"  # healthy | degraded | critical
    resources: SystemResourceState = field(default_factory=SystemResourceState)
    engines: EngineStatus = field(default_factory=EngineStatus)
    active_goals_count: int = 0
    active_plan_id: str | None = None
    active_running_action_id: str | None = None
    last_updated: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SelfModelEngine:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._state = SelfStateVector()
        self._monitor_task: asyncio.Task | None = None
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}

    async def start(self) -> None:
        self._event_handler_map = {
            "llm.generation.started": self._handle_llm_started,
            "llm.generation.complete": self._handle_llm_complete,
            "llm.generation.error": self._handle_llm_error,
            "voice.wake": self._handle_voice_wake,
            "voice.listen.start": self._handle_listen_start,
            "voice.listen.complete": self._handle_listen_complete,
            "voice.tts.start": self._handle_tts_start,
            "voice.tts.complete": self._handle_tts_complete,
            "plan.created": self._handle_plan_created,
            "plan.completed": self._handle_plan_completed,
            "action.started": self._handle_action_started,
            "action.completed": self._handle_action_completed,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())

        self._monitor_task = asyncio.create_task(self._periodic_state_broadcast())
        logger.info("SelfModelEngine started successfully")

    async def stop(self) -> None:
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            self._monitor_task = None

        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()

        logger.info("SelfModelEngine stopped")

    def get_state(self) -> SelfStateVector:
        return self._state

    def update_resources(
        self,
        cpu_percent: float,
        memory_percent: float,
        gpu_percent: float = 0.0,
        battery_percent: float | None = None,
    ) -> None:
        self._state.resources.cpu_percent = cpu_percent
        self._state.resources.memory_percent = memory_percent
        self._state.resources.gpu_percent = gpu_percent
        self._state.resources.battery_percent = battery_percent
        self._state.last_updated = datetime.now(timezone.utc).isoformat()

        if cpu_percent > 90.0 or memory_percent > 90.0:
            self._state.health_status = "degraded"
        else:
            self._state.health_status = "healthy"

    # ── Event Handlers ─────────────────────────────────────────────

    async def _handle_llm_started(self, event: HelixEvent) -> None:
        self._state.engines.llm_status = "generating"
        await self.broadcast_state()

    async def _handle_llm_complete(self, event: HelixEvent) -> None:
        self._state.engines.llm_status = "ready"
        await self.broadcast_state()

    async def _handle_llm_error(self, event: HelixEvent) -> None:
        self._state.engines.llm_status = "error"
        await self.broadcast_state()

    async def _handle_voice_wake(self, event: HelixEvent) -> None:
        self._state.engines.listening_state = True
        await self.broadcast_state()

    async def _handle_listen_start(self, event: HelixEvent) -> None:
        self._state.engines.listening_state = True
        await self.broadcast_state()

    async def _handle_listen_complete(self, event: HelixEvent) -> None:
        self._state.engines.listening_state = False
        await self.broadcast_state()

    async def _handle_tts_start(self, event: HelixEvent) -> None:
        self._state.engines.speaking_state = True
        await self.broadcast_state()

    async def _handle_tts_complete(self, event: HelixEvent) -> None:
        self._state.engines.speaking_state = False
        await self.broadcast_state()

    async def _handle_plan_created(self, event: HelixEvent) -> None:
        self._state.active_plan_id = event.payload.get("plan_id")
        await self.broadcast_state()

    async def _handle_plan_completed(self, event: HelixEvent) -> None:
        if self._state.active_plan_id == event.payload.get("plan_id"):
            self._state.active_plan_id = None
        await self.broadcast_state()

    async def _handle_action_started(self, event: HelixEvent) -> None:
        self._state.active_running_action_id = event.payload.get("action_id")
        await self.broadcast_state()

    async def _handle_action_completed(self, event: HelixEvent) -> None:
        if self._state.active_running_action_id == event.payload.get("action_id"):
            self._state.active_running_action_id = None
        await self.broadcast_state()

    async def broadcast_state(self) -> None:
        self._state.last_updated = datetime.now(timezone.utc).isoformat()
        await self._event_bus.publish_event(
            source="self_model",
            event_type="self.state.updated",
            payload=self._state.to_dict(),
        )

    async def _periodic_state_broadcast(self) -> None:
        try:
            while True:
                await asyncio.sleep(15)
                await self.broadcast_state()
        except asyncio.CancelledError:
            pass
