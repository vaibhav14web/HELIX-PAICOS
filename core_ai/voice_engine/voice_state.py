from enum import Enum
import logging

logger = logging.getLogger("helix.voice_state")


class VoiceState(Enum):
    IDLE = "idle"
    WAKE_DETECTED = "wake_detected"
    LISTENING = "listening"
    PROCESSING = "processing"
    RESPONDING = "responding"
    INTERRUPTED = "interrupted"


TRANSITIONS: dict[VoiceState, set[VoiceState]] = {
    VoiceState.IDLE: {VoiceState.WAKE_DETECTED},
    VoiceState.WAKE_DETECTED: {VoiceState.LISTENING, VoiceState.IDLE, VoiceState.INTERRUPTED},
    VoiceState.LISTENING: {VoiceState.PROCESSING, VoiceState.IDLE, VoiceState.INTERRUPTED},
    VoiceState.PROCESSING: {VoiceState.RESPONDING, VoiceState.LISTENING, VoiceState.INTERRUPTED},
    VoiceState.RESPONDING: {VoiceState.IDLE, VoiceState.LISTENING, VoiceState.INTERRUPTED},
    VoiceState.INTERRUPTED: {VoiceState.LISTENING, VoiceState.IDLE},
}


class VoiceStateMachine:
    def __init__(self, on_transition=None):
        self._state = VoiceState.IDLE
        self._listeners: list[callable] = []
        if on_transition:
            self._listeners.append(on_transition)

    @property
    def state(self) -> VoiceState:
        return self._state

    @property
    def state_name(self) -> str:
        return self._state.value

    def can_transition(self, new_state: VoiceState) -> bool:
        return new_state in TRANSITIONS.get(self._state, set())

    def transition(self, new_state: VoiceState, reason: str = "") -> bool:
        if new_state == self._state:
            return False
        if not self.can_transition(new_state):
            logger.warning(
                "Invalid state transition: %s -> %s",
                self._state.value, new_state.value,
            )
            return False
        old = self._state
        self._state = new_state
        logger.info("Voice state: %s -> %s (%s)", old.value, new_state.value, reason)
        for listener in self._listeners:
            try:
                listener(old, new_state, reason)
            except Exception:
                logger.exception("State listener failed")
        return True

    def reset(self, reason: str = "") -> None:
        self.transition(VoiceState.IDLE, reason)

    def on_transition(self, callback) -> None:
        self._listeners.append(callback)

    def remove_transition_listener(self, callback) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass
