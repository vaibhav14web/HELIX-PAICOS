import asyncio
import logging
from enum import Enum
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.ecs")


class ExecutiveState(str, Enum):
    BOOTING = "booting"
    INITIALIZING = "initializing"
    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    PLANNING = "planning"
    AWAITING_PERMISSION = "awaiting_permission"
    EXECUTING = "executing"
    MONITORING = "monitoring"
    SPEAKING = "speaking"
    LEARNING = "learning"
    RECOVERING = "recovering"
    PAUSED = "paused"
    SLEEPING = "sleeping"
    SHUTDOWN = "shutdown"


@dataclass
class HealthStatusReport:
    overall_score: float = 100.0  # 0 to 100
    status: str = "healthy"  # healthy | degraded | critical
    subsystem_health: dict[str, str] = field(default_factory=lambda: {
        "event_bus": "ok",
        "llm_engine": "ok",
        "voice_engine": "ok",
        "planner_engine": "ok",
        "action_executor": "ok",
        "permission_manager": "ok",
        "api_gateway": "ok",
        "memory": "ok",
    })
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class InterruptEvent:
    interrupt_id: str
    priority: int  # 1 (critical/user speech) to 5 (background event)
    source: str
    reason: str
    action_required: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ECSStateVector:
    current_state: ExecutiveState = ExecutiveState.BOOTING
    previous_state: ExecutiveState = ExecutiveState.BOOTING
    active_goal_id: str | None = "goal-1"
    active_plan_id: str | None = None
    active_action_id: str | None = None
    health: HealthStatusReport = field(default_factory=HealthStatusReport)
    pending_interrupts: list[dict[str, Any]] = field(default_factory=list)
    recent_transitions: list[dict[str, Any]] = field(default_factory=list)
    last_transition_time: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["current_state"] = self.current_state.value
        d["previous_state"] = self.previous_state.value
        return d


# State Transition Rules Matrix
_VALID_TRANSITIONS: dict[ExecutiveState, set[ExecutiveState]] = {
    ExecutiveState.BOOTING: {ExecutiveState.INITIALIZING, ExecutiveState.RECOVERING, ExecutiveState.SHUTDOWN},
    ExecutiveState.INITIALIZING: {ExecutiveState.IDLE, ExecutiveState.SLEEPING, ExecutiveState.RECOVERING, ExecutiveState.SHUTDOWN},
    ExecutiveState.IDLE: {
        ExecutiveState.LISTENING,
        ExecutiveState.THINKING,
        ExecutiveState.PLANNING,
        ExecutiveState.SLEEPING,
        ExecutiveState.PAUSED,
        ExecutiveState.LEARNING,
        ExecutiveState.RECOVERING,
        ExecutiveState.SHUTDOWN,
    },
    ExecutiveState.LISTENING: {
        ExecutiveState.THINKING,
        ExecutiveState.IDLE,
        ExecutiveState.SLEEPING,
        ExecutiveState.RECOVERING,
        ExecutiveState.SHUTDOWN,
    },
    ExecutiveState.THINKING: {
        ExecutiveState.PLANNING,
        ExecutiveState.SPEAKING,
        ExecutiveState.EXECUTING,
        ExecutiveState.IDLE,
        ExecutiveState.LISTENING,
        ExecutiveState.SLEEPING,
        ExecutiveState.RECOVERING,
        ExecutiveState.SHUTDOWN,
    },
    ExecutiveState.PLANNING: {
        ExecutiveState.AWAITING_PERMISSION,
        ExecutiveState.EXECUTING,
        ExecutiveState.THINKING,
        ExecutiveState.IDLE,
        ExecutiveState.RECOVERING,
        ExecutiveState.SHUTDOWN,
    },
    ExecutiveState.AWAITING_PERMISSION: {
        ExecutiveState.EXECUTING,
        ExecutiveState.IDLE,
        ExecutiveState.RECOVERING,
        ExecutiveState.SHUTDOWN,
    },
    ExecutiveState.EXECUTING: {
        ExecutiveState.MONITORING,
        ExecutiveState.SPEAKING,
        ExecutiveState.IDLE,
        ExecutiveState.AWAITING_PERMISSION,
        ExecutiveState.PAUSED,
        ExecutiveState.RECOVERING,
        ExecutiveState.LEARNING,
        ExecutiveState.SHUTDOWN,
    },
    ExecutiveState.MONITORING: {
        ExecutiveState.SPEAKING,
        ExecutiveState.IDLE,
        ExecutiveState.RECOVERING,
        ExecutiveState.LEARNING,
        ExecutiveState.SHUTDOWN,
    },
    ExecutiveState.SPEAKING: {
        ExecutiveState.LISTENING,
        ExecutiveState.IDLE,
        ExecutiveState.EXECUTING,
        ExecutiveState.LEARNING,
        ExecutiveState.RECOVERING,
        ExecutiveState.SLEEPING,
        ExecutiveState.SHUTDOWN,
    },
    ExecutiveState.LEARNING: {
        ExecutiveState.IDLE,
        ExecutiveState.SLEEPING,
        ExecutiveState.RECOVERING,
        ExecutiveState.SHUTDOWN,
    },
    ExecutiveState.RECOVERING: {
        ExecutiveState.IDLE,
        ExecutiveState.SLEEPING,
        ExecutiveState.INITIALIZING,
        ExecutiveState.SHUTDOWN,
    },
    ExecutiveState.PAUSED: {
        ExecutiveState.IDLE,
        ExecutiveState.SLEEPING,
        ExecutiveState.EXECUTING,
        ExecutiveState.PLANNING,
        ExecutiveState.SHUTDOWN,
    },
    ExecutiveState.SLEEPING: {
        ExecutiveState.INITIALIZING,
        ExecutiveState.IDLE,
        ExecutiveState.LISTENING,
        ExecutiveState.LEARNING,
        ExecutiveState.RECOVERING,
        ExecutiveState.SHUTDOWN,
    },
    ExecutiveState.SHUTDOWN: set(),
}


class ExecutiveControlSystem:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._state = ECSStateVector()
        self._supervisor_task: asyncio.Task | None = None
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}
        self._transition_lock = asyncio.Lock()

    async def start(self, initial_state: ExecutiveState = ExecutiveState.IDLE) -> None:
        # Subscribe to all subsystem events
        self._event_handler_map = {
            "voice.wake": self._on_voice_wake,
            "voice.wake.confirmed": self._on_voice_wake,
            "voice.listen.start": self._on_listen_start,
            "voice.listen.complete": self._on_listen_complete,
            "llm.generation.start": self._on_llm_start,
            "llm.generation.started": self._on_llm_start,
            "llm.generation.complete": self._on_llm_complete,
            "llm.generation.error": self._on_llm_error,
            "voice.tts.start": self._on_tts_start,
            "voice.tts.complete": self._on_tts_complete,
            "voice.error": self._on_voice_error,
            "plan.created": self._on_plan_created,
            "plan.completed": self._on_plan_completed,
            "permission.requested": self._on_permission_requested,
            "permission.granted": self._on_permission_granted,
            "permission.denied": self._on_permission_denied,
            "action.started": self._on_action_started,
            "action.completed": self._on_action_completed,
            "action.failed_verification": self._on_action_failed,
            "voice.interrupt": self._on_user_interrupt,
            "self.health.degraded": self._on_health_degraded,
            "learning.start": self._on_learning_start,
            "learning.complete": self._on_learning_complete,
            "ecs.transition.request": self._on_transition_request,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())

        # Transition to INITIALIZING -> initial_state
        await self.transition_to(ExecutiveState.INITIALIZING, reason="System boot completed")
        await self.transition_to(initial_state, reason=f"Initial engines ready ({initial_state.value})")

        self._supervisor_task = asyncio.create_task(self._supervision_loop())
        logger.info("ExecutiveControlSystem (ECS) started successfully (state=%s)", self._state.current_state.value)

    async def stop(self) -> None:
        if self._supervisor_task and not self._supervisor_task.done():
            self._supervisor_task.cancel()
            self._supervisor_task = None

        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()

        await self.transition_to(ExecutiveState.SHUTDOWN, reason="System stopping")
        logger.info("ExecutiveControlSystem stopped")

    def get_state(self) -> ECSStateVector:
        return self._state

    def get_health(self) -> HealthStatusReport:
        return self._state.health

    async def transition_to(self, target_state: ExecutiveState, reason: str = "") -> bool:
        async with self._transition_lock:
            current = self._state.current_state
            if target_state != current and target_state != ExecutiveState.SHUTDOWN and target_state not in _VALID_TRANSITIONS.get(current, set()):
                logger.warning("Invalid ECS transition attempted: %s -> %s (reason: %s)", current.value, target_state.value, reason)
                # Allow recovery override
                if target_state != ExecutiveState.RECOVERING:
                    return False

            self._state.previous_state = current
            self._state.current_state = target_state
            self._state.last_transition_time = datetime.now(timezone.utc).isoformat()

            record = {
                "from": current.value,
                "to": target_state.value,
                "reason": reason,
                "timestamp": self._state.last_transition_time,
            }
            self._state.recent_transitions.append(record)
            if len(self._state.recent_transitions) > 50:
                self._state.recent_transitions = self._state.recent_transitions[-50:]

            logger.info("ECS State Transition: %s -> %s (%s)", current.value, target_state.value, reason)

            await self._event_bus.publish_event(
                source="ecs",
                event_type="ecs.state.changed",
                payload=self._state.to_dict(),
            )
            return True

    async def inject_interrupt(self, priority: int, source: str, reason: str, action_required: str) -> None:
        interrupt = InterruptEvent(
            interrupt_id=f"int-{len(self._state.pending_interrupts) + 1}",
            priority=priority,
            source=source,
            reason=reason,
            action_required=action_required,
        )
        self._state.pending_interrupts.append(interrupt.__dict__)
        logger.info("ECS Interrupt injected [P%d] %s: %s", priority, source, reason)

        if priority == 1:
            # Immediate priority override (e.g. user speaking or critical failure)
            await self.transition_to(
                ExecutiveState.LISTENING if action_required == "listen" else ExecutiveState.RECOVERING,
                reason=f"P1 Interrupt from {source}: {reason}",
            )

    # ── Subsystem Event Handlers ─────────────────────────────────────

    async def _on_voice_wake(self, event: HelixEvent) -> None:
        phrase = event.payload.get("phrase") or event.payload.get("wake_word", "wake word")
        logger.info("Wake word detected by ECS: %s", phrase)
        if self._state.current_state in (ExecutiveState.SLEEPING, ExecutiveState.IDLE, ExecutiveState.BOOTING):
            await self.transition_to(ExecutiveState.LISTENING, reason=f"Wake phrase recognized: {phrase}")

    async def _on_listen_start(self, event: HelixEvent) -> None:
        await self.transition_to(ExecutiveState.LISTENING, reason="Speech capture started")

    async def _on_listen_complete(self, event: HelixEvent) -> None:
        await self.transition_to(ExecutiveState.THINKING, reason="Speech captured")

    async def _on_llm_start(self, event: HelixEvent) -> None:
        if self._state.current_state not in (ExecutiveState.THINKING, ExecutiveState.PLANNING):
            await self.transition_to(ExecutiveState.THINKING, reason="LLM inference started")

    async def _on_llm_complete(self, event: HelixEvent) -> None:
        if self._state.current_state == ExecutiveState.THINKING:
            await self.transition_to(ExecutiveState.IDLE, reason="LLM generation finished")

    async def _on_llm_error(self, event: HelixEvent) -> None:
        logger.warning("LLM error caught by ECS: %s", event.payload.get("error"))
        if self._state.current_state in (ExecutiveState.THINKING, ExecutiveState.PLANNING):
            await self.transition_to(ExecutiveState.RECOVERING, reason="LLM generation error")
            await self.transition_to(ExecutiveState.IDLE, reason="Recovered from LLM error")

    async def _on_tts_start(self, event: HelixEvent) -> None:
        await self.transition_to(ExecutiveState.SPEAKING, reason="Audio output playback")

    async def _on_tts_complete(self, event: HelixEvent) -> None:
        if self._state.current_state == ExecutiveState.SPEAKING:
            await self.transition_to(ExecutiveState.IDLE, reason="Audio output completed")

    async def _on_voice_error(self, event: HelixEvent) -> None:
        logger.warning("Voice error caught by ECS: %s", event.payload.get("error"))
        if self._state.current_state in (ExecutiveState.LISTENING, ExecutiveState.SPEAKING):
            await self.transition_to(ExecutiveState.RECOVERING, reason="Voice engine error")
            await self.transition_to(ExecutiveState.IDLE, reason="Recovered from voice error")

    async def _on_plan_created(self, event: HelixEvent) -> None:
        self._state.active_plan_id = event.payload.get("plan_id")
        await self.transition_to(ExecutiveState.PLANNING, reason="Plan DAG generated")

    async def _on_plan_completed(self, event: HelixEvent) -> None:
        self._state.active_plan_id = None
        await self.transition_to(ExecutiveState.IDLE, reason="Plan execution completed")

    async def _on_permission_requested(self, event: HelixEvent) -> None:
        await self.transition_to(ExecutiveState.AWAITING_PERMISSION, reason="User permission required")

    async def _on_permission_granted(self, event: HelixEvent) -> None:
        if self._state.current_state == ExecutiveState.AWAITING_PERMISSION:
            await self.transition_to(ExecutiveState.EXECUTING, reason="User permission granted")

    async def _on_permission_denied(self, event: HelixEvent) -> None:
        if self._state.current_state == ExecutiveState.AWAITING_PERMISSION:
            await self.transition_to(ExecutiveState.IDLE, reason="User permission denied")

    async def _on_action_started(self, event: HelixEvent) -> None:
        self._state.active_action_id = event.payload.get("action_id")
        await self.transition_to(ExecutiveState.EXECUTING, reason="Action execution dispatched")

    async def _on_action_completed(self, event: HelixEvent) -> None:
        self._state.active_action_id = None
        await self.transition_to(ExecutiveState.MONITORING, reason="Verifying action outcome")

    async def _on_action_failed(self, event: HelixEvent) -> None:
        await self.transition_to(ExecutiveState.RECOVERING, reason="Action verification failed")
        await self.inject_interrupt(2, "action_verification", "Verification failed", "retry")

    async def _on_user_interrupt(self, event: HelixEvent) -> None:
        await self.inject_interrupt(1, "voice_engine", "User requested stop", "listen")

    async def _on_health_degraded(self, event: HelixEvent) -> None:
        self._state.health.status = "degraded"
        self._state.health.overall_score = 75.0
        self._state.health.diagnostics.append(event.payload.get("reason", "Resource degradation"))

    async def _on_learning_start(self, event: HelixEvent) -> None:
        reason = event.payload.get("reason", "Cognitive reflection in progress")
        await self.transition_to(ExecutiveState.LEARNING, reason=reason)

    async def _on_learning_complete(self, event: HelixEvent) -> None:
        if self._state.current_state == ExecutiveState.LEARNING:
            await self.transition_to(ExecutiveState.IDLE, reason="Cognitive reflection completed")

    async def _on_transition_request(self, event: HelixEvent) -> None:
        req_state = event.payload.get("target_state") or event.payload.get("state")
        reason = event.payload.get("reason", "External request")
        if not req_state:
            return
        try:
            target = ExecutiveState(req_state)
            await self.transition_to(target, reason=reason)
        except ValueError:
            logger.warning("Invalid target state in ecs.transition.request: %s", req_state)

    # ── Background Supervision Loop ─────────────────────────────────

    async def _supervision_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(10)
                # Heartbeat and status check
                if self._state.current_state == ExecutiveState.MONITORING:
                    await self.transition_to(ExecutiveState.IDLE, reason="Monitoring check ok")
        except asyncio.CancelledError:
            pass
