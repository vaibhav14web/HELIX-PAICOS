import os
import asyncio
import logging
import time
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.wake_word_engine")


class WakeWordEngine:
    def __init__(self, event_bus: EventBus, ring_buffer=None):
        self._event_bus = event_bus
        self._ring = ring_buffer
        self._backend = os.getenv("HELIX_WAKE_WORD_BACKEND", "mock")
        self._wake_phrase = os.getenv("HELIX_WAKE_WORD_PHRASE", "Hola Helix")
        from foundation.paths import get_models_dir
        self._model_path = os.getenv(
            "HELIX_WAKE_WORD_MODEL_PATH",
            str(get_models_dir() / "openwakeword"),
        )
        self._threshold = float(os.getenv("HELIX_WAKE_WORD_THRESHOLD", "0.5"))
        self._sample_rate = int(os.getenv("HELIX_WAKE_WORD_SAMPLE_RATE", "16000"))
        self._enabled = (self._backend not in ("disabled", "off", "none")) and (
            os.getenv("HELIX_WAKE_WORD_ENABLED", "true").lower() in ("true", "1", "yes")
        )

        self._loaded = False
        self._listening = False
        self._state_before_listen: str | None = None
        self._load_lock = asyncio.Lock()
        self._listen_task: asyncio.Task | None = None
        self._model: Any = None
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}
        self._last_marker: int = 0
        self._simulated_triggers: list[dict[str, Any]] = []

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_listening(self) -> bool:
        return self._listening and self._enabled

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def wake_phrase(self) -> str:
        return self._wake_phrase

    @property
    def has_ring_buffer(self) -> bool:
        return self._ring is not None

    @property
    def current_marker(self) -> int:
        return self._last_marker

    @property
    def ring_buffer(self) -> Any:
        return self._ring

    def set_ring_buffer(self, ring_buffer: Any) -> None:
        self._ring = ring_buffer
        if ring_buffer is not None:
            self._last_marker = getattr(ring_buffer, "current_marker", 0)

    async def start(self) -> None:
        self._event_handler_map = {
            "system.state.change": self._handle_state_change,
            "voice.listen.start": self._handle_listen_start,
            "voice.listen.complete": self._handle_listen_complete,
            "ecs.state.changed": self._handle_ecs_state_changed,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())

        if self._ring is not None and self._enabled:
            self._last_marker = getattr(self._ring, "current_marker", 0)

        await self._event_bus.publish_event(
            source="wake_word_engine",
            event_type="voice.ready",
            payload={
                "backend": self._backend,
                "wake_phrase": self._wake_phrase,
                "has_ring_buffer": (self._ring is not None and self._enabled),
            },
        )
        logger.info(
            "Wake Word Engine started (backend=%s, phrase=%s, ring=%s, enabled=%s)",
            self._backend,
            self._wake_phrase,
            "connected" if (self._ring is not None and self._enabled) else "none",
            self._enabled,
        )

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()

        self._listening = False
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            self._listen_task = None
        self._model = None
        self._loaded = False
        logger.info("Wake Word Engine stopped")

    async def simulate_wake(self, phrase: str | None = None, confidence: float = 0.95) -> None:
        if not self._enabled or self._backend in ("disabled", "off", "none"):
            logger.info("Wake Word Engine is disabled; ignoring wake trigger.")
            return
        target_phrase = phrase or self._wake_phrase
        logger.info("Simulating wake word trigger: %s (confidence=%.2f)", target_phrase, confidence)
        await self._event_bus.publish_event(
            source="wake_word_engine",
            event_type="voice.wake",
            payload={
                "phrase": target_phrase,
                "confidence": confidence,
                "timestamp": time.time(),
                "simulated": True,
            },
        )

    async def _handle_state_change(self, event: HelixEvent) -> None:
        if not self._enabled or self._backend in ("disabled", "off", "none"):
            return
        state = event.payload.get("state", "sleep").lower()
        self._state_before_listen = state
        if state in ("sleep", "idle", "sleeping"):
            if self._listen_task and not self._listen_task.done():
                return
            self._listening = True
            self._listen_task = asyncio.create_task(self._listen_loop())
        else:
            self._listening = False

    async def _handle_ecs_state_changed(self, event: HelixEvent) -> None:
        if not self._enabled or self._backend in ("disabled", "off", "none"):
            return
        curr = event.payload.get("current_state", "").lower()
        if curr in ("idle", "sleeping", "booting"):
            if self._state_before_listen is None:
                self._state_before_listen = curr
            if not self._listening:
                self._listening = True
                if not self._listen_task or self._listen_task.done():
                    self._listen_task = asyncio.create_task(self._listen_loop())
        elif curr in ("listening", "thinking", "speaking", "executing"):
            self._listening = False

    async def _handle_listen_start(self, event: HelixEvent) -> None:
        self._listening = False

    async def _handle_listen_complete(self, event: HelixEvent) -> None:
        if not self._enabled or self._backend in ("disabled", "off", "none"):
            return
        if self._state_before_listen in ("sleep", "idle", "sleeping", None):
            if self._listen_task and not self._listen_task.done():
                return
            self._listening = True
            self._listen_task = asyncio.create_task(self._listen_loop())

    async def _listen_loop(self) -> None:
        await self._load_model_if_needed()

        while self._listening:
            try:
                if self._ring is not None:
                    marker = getattr(self._ring, "current_marker", self._last_marker)
                    if marker == self._last_marker:
                        await asyncio.sleep(0.05)
                        continue
                    audio_data = self._ring.read_from(self._last_marker, self._chunk_duration)
                    self._last_marker = marker
                    if len(audio_data) == 0:
                        await asyncio.sleep(0.05)
                        continue
                else:
                    await asyncio.sleep(0.05)
                    continue

                detected = await self._detect(audio_data)
                if detected:
                    conf = getattr(detected, "confidence", 0.9)
                    phrase = getattr(detected, "name", self._wake_phrase)
                    logger.info("Wake word detected via %s: %s (confidence=%.2f)", self._backend, phrase, conf)
                    await self._event_bus.publish_event(
                        source="wake_word_engine",
                        event_type="voice.wake",
                        payload={
                            "phrase": phrase,
                            "confidence": conf,
                            "timestamp": time.time(),
                            "backend": self._backend,
                        },
                    )
                    self._listening = False
                    break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Wake word listen error: %s", e)
                await asyncio.sleep(0.1)

    async def _load_model_if_needed(self) -> None:
        async with self._load_lock:
            if self._loaded:
                return

            await self._event_bus.publish_event(
                source="wake_word_engine",
                event_type="voice.model.loading",
                payload={"type": "wake_word", "model": self._wake_phrase},
            )

            if self._backend == "openwakeword":
                try:
                    from openwakeword.model import Model
                    loop = asyncio.get_event_loop()
                    self._model = await loop.run_in_executor(
                        None,
                        lambda: Model(
                            wakeword_models=[self._wake_phrase.replace(" ", "_")],
                            inference_framework="onnx",
                        ),
                    )
                except (ImportError, Exception) as exc:
                    logger.warning("openwakeword unavailable (%s), falling back to energy detector", exc)
                    self._backend = "energy_vad"
                    self._model = None
            elif self._backend in ("mock", "energy", "energy_vad"):
                self._model = None
            else:
                self._model = None

            self._loaded = True
            await self._event_bus.publish_event(
                source="wake_word_engine",
                event_type="voice.model.loaded",
                payload={"type": "wake_word", "model": self._wake_phrase},
            )
            logger.info("Wake word model loaded (backend=%s)", self._backend)

    async def _detect(self, audio_data: Any) -> Any:
        if self._backend == "mock":
            await asyncio.sleep(0.02)
            return None

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self._detect_blocking,
            audio_data,
        )

    def _detect_blocking(self, audio_data: Any) -> Any:
        if self._backend == "openwakeword" and self._model:
            try:
                import numpy as np
                audio_array = np.asarray(audio_data, dtype=np.float32)
                prediction = self._model.predict(audio_array)
                for name, score in prediction.items():
                    if score >= self._threshold:
                        return type("Detection", (), {"confidence": float(score), "name": name})()
            except Exception as e:
                logger.debug("openwakeword prediction error: %s", e)

        elif self._backend in ("energy", "energy_vad"):
            try:
                import numpy as np
                arr = np.asarray(audio_data, dtype=np.float32)
                if len(arr) > 0:
                    rms = float(np.sqrt(np.mean(arr ** 2)))
                    if rms >= self._threshold:
                        conf = min(1.0, float(rms * 2.0))
                        return type("Detection", (), {"confidence": conf, "name": self._wake_phrase})()
            except Exception as e:
                logger.debug("energy detection error: %s", e)

        return None
