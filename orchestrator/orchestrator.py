import os
import re
import asyncio
import logging
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent
from orchestrator.ecs.executive_control_system import ExecutiveControlSystem, ExecutiveState

logger = logging.getLogger("helix.orchestrator")

_STOP_PATTERNS = [
    re.compile(r"^(stop|stop it|stop now|stop listening|stop right there)$", re.IGNORECASE),
    re.compile(r"^(cancel|cancel that|cancel the command)$", re.IGNORECASE),
    re.compile(r"^(shut up|shut up please|be quiet|quiet)$", re.IGNORECASE),
    re.compile(r"^(enough|that.s enough|never mind|nevermind)$", re.IGNORECASE),
    re.compile(r"^(go away|leave me alone|forget it)$", re.IGNORECASE),
]

_STATE_TO_EXECUTIVE: dict[str, ExecutiveState] = {
    "sleep": ExecutiveState.SLEEPING,
    "sleeping": ExecutiveState.SLEEPING,
    "idle": ExecutiveState.IDLE,
    "listening": ExecutiveState.LISTENING,
    "processing": ExecutiveState.THINKING,
    "thinking": ExecutiveState.THINKING,
    "planning": ExecutiveState.PLANNING,
    "awaiting_permission": ExecutiveState.AWAITING_PERMISSION,
    "executing": ExecutiveState.EXECUTING,
    "monitoring": ExecutiveState.MONITORING,
    "responding": ExecutiveState.SPEAKING,
    "speaking": ExecutiveState.SPEAKING,
    "learning": ExecutiveState.LEARNING,
    "recovering": ExecutiveState.RECOVERING,
    "paused": ExecutiveState.PAUSED,
    "conversation": ExecutiveState.LISTENING,
    "background": ExecutiveState.IDLE,
    "startup": ExecutiveState.INITIALIZING,
    "shutdown": ExecutiveState.SHUTDOWN,
}

_EXECUTIVE_TO_STATE: dict[ExecutiveState, str] = {
    ExecutiveState.BOOTING: "startup",
    ExecutiveState.INITIALIZING: "startup",
    ExecutiveState.SLEEPING: "sleep",
    ExecutiveState.IDLE: "idle",
    ExecutiveState.LISTENING: "listening",
    ExecutiveState.THINKING: "processing",
    ExecutiveState.PLANNING: "processing",
    ExecutiveState.AWAITING_PERMISSION: "processing",
    ExecutiveState.EXECUTING: "processing",
    ExecutiveState.MONITORING: "processing",
    ExecutiveState.SPEAKING: "responding",
    ExecutiveState.LEARNING: "processing",
    ExecutiveState.RECOVERING: "recovering",
    ExecutiveState.PAUSED: "paused",
    ExecutiveState.SHUTDOWN: "sleep",
}


def is_ac_power_connected() -> bool:
    """
    Check if the host machine is connected to AC power pursuant to ADR-027.
    Supports HELIX_POWER_PLUGGED environment override (for testing/mocking),
    psutil.sensors_battery(), and Windows GetSystemPowerStatus fallback.
    Returns True if plugged in or if power status cannot be determined (desktop).
    Returns False only if definitely running on battery power.
    """
    override = os.getenv("HELIX_POWER_PLUGGED")
    if override is not None:
        return override.lower() in ("true", "1", "yes")

    try:
        import psutil
        battery = psutil.sensors_battery()
        if battery is not None and battery.power_plugged is False:
            return False
    except Exception:
        pass

    if os.name == "nt":
        try:
            import ctypes

            class SYSTEM_POWER_STATUS(ctypes.Structure):
                _fields_ = [
                    ("ACLineStatus", ctypes.c_byte),
                    ("BatteryFlag", ctypes.c_byte),
                    ("BatteryLifePercent", ctypes.c_byte),
                    ("SystemStatusFlag", ctypes.c_byte),
                    ("BatteryLifeTime", ctypes.c_ulong),
                    ("BatteryFullLifeTime", ctypes.c_ulong),
                ]

            status = SYSTEM_POWER_STATUS()
            if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
                if status.ACLineStatus == 0:
                    return False
        except Exception:
            pass

    return True


class Orchestrator:
    """
    Orchestrator coordinates subsystem workflows, execution sequencing,
    conversation turn-taking, user interrupts, timeouts, and failure recovery.

    Cognitive and operational state is authoritatively owned and validated
    by the ExecutiveControlSystem (ECS). Orchestrator maintains state synchronization
    with ECS and fail-closed transition checks to guarantee that contradictory states
    cannot exist.
    """

    def __init__(self, event_bus: EventBus, ecs: ExecutiveControlSystem | None = None):
        self._event_bus = event_bus
        self._ecs: ExecutiveControlSystem = ecs or ExecutiveControlSystem(event_bus)
        self._owns_ecs: bool = ecs is None
        self._state = os.getenv("HELIX_SYSTEM_STATE", "sleep")
        self._conversation_timeout = int(os.getenv("HELIX_CONVERSATION_TIMEOUT", "60"))
        self._last_activity: float = 0
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}
        self._watchdog_task: asyncio.Task | None = None
        self._interrupt_cooldown: float = 0

    def set_ecs(self, ecs: ExecutiveControlSystem) -> None:
        """Inject or bind an external authoritative ECS instance."""
        self._ecs = ecs
        self._owns_ecs = False

    @property
    def ecs(self) -> ExecutiveControlSystem:
        return self._ecs

    @property
    def executive_state(self) -> ExecutiveState:
        return self._ecs.get_state().current_state

    @property
    def current_state(self) -> str:
        return self._state

    async def start(self) -> None:
        if self._owns_ecs:
            initial_exec = _STATE_TO_EXECUTIVE.get(self._state.lower(), ExecutiveState.SLEEPING)
            await self._ecs.start(initial_state=initial_exec)
        else:
            # Synchronize initial state with external ECS
            target_exec = _STATE_TO_EXECUTIVE.get(self._state.lower())
            if target_exec and target_exec != self._ecs.get_state().current_state:
                await self._ecs.transition_to(target_exec, reason=f"Initial orchestrator state: {self._state}")

        self._event_handler_map = {
            "voice.wake.confirmed": self._on_wake_confirmed,
            "voice.listen.start": self._on_listen_start,
            "voice.listen.complete": self._on_listen_complete,
            "llm.generation.start": self._on_llm_start,
            "llm.generation.complete": self._on_llm_complete,
            "voice.tts.start": self._on_tts_start,
            "voice.tts.complete": self._on_tts_complete,
            "voice.interrupt": self._on_interrupt,
            "voice.error": self._on_voice_error,
            "voice.model.error": self._on_voice_error,
            "llm.generation.error": self._on_llm_error,
            "llm.model.error": self._on_llm_error,
            "permission.granted": self._on_permission_granted,
            "permission.denied": self._on_permission_denied,
            "system.state.request": self._on_state_request,
            "ecs.state.changed": self._on_ecs_state_changed,
            "system.power.changed": self._on_power_changed,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())

        self._last_activity = asyncio.get_running_loop().time()
        self._watchdog_task = asyncio.create_task(self._watchdog())

        await self._publish_state("startup")
        logger.info("Orchestrator started (state=%s, ecs_state=%s)", self._state, self._ecs.get_state().current_state.value)

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()

        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            self._watchdog_task = None

        if self._owns_ecs:
            await self._ecs.stop()

        logger.info("Orchestrator stopped")

    async def _publish_state(self, reason: str, force_unload: bool = False) -> None:
        payload = {"state": self._state, "reason": reason}
        if self._state in ("sleep", "background") or force_unload:
            payload["force_unload"] = True
        await self._event_bus.publish_event(
            source="orchestrator",
            event_type="system.state.change",
            payload=payload,
        )

    async def _transition(self, new_state: str, reason: str) -> bool:
        if new_state == self._state:
            return True

        if new_state.lower() == "background" and not is_ac_power_connected():
            logger.warning(
                "Orchestrator transition to background rejected: power precondition failed (on battery power)"
            )
            rejected_payload = {
                "state": "background",
                "requested_state": "background",
                "current_state": self._state,
                "reason": "AC power not connected: on battery power (ADR-027)",
            }
            await self._event_bus.publish_event(
                source="orchestrator",
                event_type="orchestrator.transition.rejected",
                payload=rejected_payload,
            )
            await self._event_bus.publish_event(
                source="orchestrator",
                event_type="system.state.change.rejected",
                payload=rejected_payload,
            )
            return False

        target_exec = _STATE_TO_EXECUTIVE.get(new_state.lower())
        if target_exec is not None:
            success = await self._ecs.transition_to(target_exec, reason=reason)
            if not success:
                logger.warning(
                    "Orchestrator transition rejected by authoritative ECS: %s -> %s (reason: %s)",
                    self._ecs.get_state().current_state.value,
                    target_exec.value,
                    reason,
                )
                return False

        old_state = self._state
        self._state = new_state
        self._last_activity = asyncio.get_running_loop().time()
        await self._publish_state(reason)
        logger.info("Orchestrator state transition: %s -> %s (%s)", old_state, new_state, reason)
        return True

    async def _on_ecs_state_changed(self, event: HelixEvent) -> None:
        curr_val = event.payload.get("current_state")
        if not curr_val:
            return
        try:
            exec_state = ExecutiveState(curr_val)
        except ValueError:
            return

        mapped_state = _EXECUTIVE_TO_STATE.get(exec_state, "idle")
        if mapped_state != self._state:
            # Preserve specialized custom labels like background if compatible with idle
            if self._state == "background" and mapped_state == "idle":
                return
            old_state = self._state
            self._state = mapped_state
            self._last_activity = asyncio.get_running_loop().time()
            await self._publish_state(f"ecs_sync:{curr_val}")
            logger.info("Orchestrator synchronized from ECS: %s -> %s (%s)", old_state, self._state, curr_val)

    async def _on_wake_confirmed(self, event: HelixEvent) -> None:
        if self._state not in ("sleep", "idle", "sleeping", "background"):
            logger.debug("Wake ignored: in state %s", self._state)
            return
        await self._transition("listening", "wake_word_detected")

    async def _on_listen_start(self, event: HelixEvent) -> None:
        self._last_activity = asyncio.get_running_loop().time()

    async def _on_listen_complete(self, event: HelixEvent) -> None:
        self._last_activity = asyncio.get_running_loop().time()
        text = event.payload.get("text", "")
        session_id = event.payload.get("session_id", "default")
        if not text.strip():
            asyncio.create_task(self._delayed_return_to_sleep())
            return

        if self._is_stop_command(text):
            logger.info("Stop command detected locally: %r", text)
            await self._event_bus.publish_event(
                source="orchestrator",
                event_type="voice.interrupt",
                payload={"session_id": session_id},
            )
            await self._event_bus.publish_event(
                source="orchestrator",
                event_type="voice.shutdown",
                payload={"session_id": session_id},
            )
            await self._transition("sleep", "stop_command")
            return

        await self._transition("processing", "transcription_complete")
        await self._event_bus.publish_event(
            source="orchestrator",
            event_type="conversation.user_message",
            payload={"text": text, "session_id": session_id},
        )

    @staticmethod
    def _is_stop_command(text: str) -> bool:
        stripped = text.strip().lower().rstrip(".!?")
        return any(p.match(stripped) for p in _STOP_PATTERNS)

    async def _on_llm_start(self, event: HelixEvent) -> None:
        await self._transition("processing", "llm_generation_started")

    async def _on_llm_complete(self, event: HelixEvent) -> None:
        text = event.payload.get("response", "")
        session_id = event.payload.get("session_id", "default")
        self._last_activity = asyncio.get_running_loop().time()

        if text:
            await self._transition("responding", "llm_response_ready")
            await self._event_bus.publish_event(
                source="orchestrator",
                event_type="voice.tts",
                payload={"text": text, "session_id": session_id},
            )
        else:
            asyncio.create_task(self._delayed_return_to_sleep())

    async def _on_tts_start(self, event: HelixEvent) -> None:
        await self._transition("responding", "tts_started")

    async def _on_tts_complete(self, event: HelixEvent) -> None:
        self._last_activity = asyncio.get_running_loop().time()
        await self._transition("idle", "tts_completed")
        asyncio.create_task(self._delayed_return_to_sleep())

    async def _on_interrupt(self, event: HelixEvent) -> None:
        now = asyncio.get_running_loop().time()
        if now - self._interrupt_cooldown < 0.3:
            return
        self._interrupt_cooldown = now
        logger.info("Interrupt received")
        if self._state != "sleep":
            await self._transition("idle", "user_interrupt")

    async def _on_voice_error(self, event: HelixEvent) -> None:
        err = event.payload.get("error", "unknown")
        logger.warning("Voice error: %s", err)
        await self._transition("sleep", f"voice_error:{err}")

    async def _on_llm_error(self, event: HelixEvent) -> None:
        err = event.payload.get("error", "unknown")
        logger.warning("LLM error: %s", err)
        await self._transition("sleep", f"llm_error:{err}")

    async def _on_permission_granted(self, event: HelixEvent) -> None:
        self._last_activity = asyncio.get_running_loop().time()

    async def _on_permission_denied(self, event: HelixEvent) -> None:
        asyncio.create_task(self._delayed_return_to_sleep())

    async def _on_state_request(self, event: HelixEvent) -> None:
        requested = event.payload.get("state", "")
        reason = event.payload.get("reason", "external_request")
        if requested in ("sleep", "conversation", "background", "idle", "listening"):
            await self._transition(requested, reason)

    async def _delayed_return_to_sleep(self) -> None:
        await asyncio.sleep(5)
        if self._state in ("processing", "responding", "idle", "listening"):
            await self._transition("sleep", "idle_timeout")

    async def _on_power_changed(self, event: HelixEvent) -> None:
        plugged = event.payload.get("plugged")
        if (plugged is False or not is_ac_power_connected()) and self._state == "background":
            logger.warning("AC power lost while in background state. Aborting background and returning to sleep.")
            await self._event_bus.publish_event(
                source="orchestrator",
                event_type="background.abort",
                payload={"reason": "power_disconnected"},
            )
            await self._transition("sleep", "ac_power_disconnected")

    async def _check_ac_power(self) -> None:
        """Periodic AC power check for background state pursuant to ADR-027."""
        if self._state == "background" and not is_ac_power_connected():
            logger.warning("AC power disconnected in background state. Aborting background and returning to sleep.")
            await self._event_bus.publish_event(
                source="orchestrator",
                event_type="background.abort",
                payload={"reason": "power_disconnected"},
            )
            await self._transition("sleep", "ac_power_disconnected")

    async def _watchdog(self) -> None:
        watchdog_interval = float(os.getenv("HELIX_WATCHDOG_INTERVAL", "5.0"))
        try:
            while True:
                await asyncio.sleep(watchdog_interval)
                await self._check_ac_power()
                now = asyncio.get_running_loop().time()
                idle = now - self._last_activity

                if self._state in ("processing", "responding", "listening") and idle > self._conversation_timeout:
                    await self._transition("sleep", "conversation_timeout")
        except asyncio.CancelledError:
            pass

