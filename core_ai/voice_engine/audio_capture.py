import threading
import logging
import numpy as np
from typing import Callable

logger = logging.getLogger("helix.audio_capture")


class RingBuffer:
    def __init__(self, max_seconds: int = 3, sample_rate: int = 16000):
        self._sample_rate = sample_rate
        self._capacity = max_seconds * sample_rate
        self._buffer: np.ndarray = np.zeros(self._capacity, dtype=np.float32)
        self._write_pos = 0
        self._bytes_written = 0
        self._lock = threading.Lock()
        self._notify: list[Callable[[], None]] = []

    def write(self, data: np.ndarray) -> None:
        samples = data.ravel()
        with self._lock:
            n = len(samples)
            if n >= self._capacity:
                self._buffer[:] = samples[-self._capacity:]
                self._write_pos = 0
                self._bytes_written += n
            else:
                end = self._write_pos + n
                if end <= self._capacity:
                    self._buffer[self._write_pos:end] = samples
                else:
                    first = self._capacity - self._write_pos
                    self._buffer[self._write_pos:] = samples[:first]
                    self._buffer[:n - first] = samples[first:]
                self._write_pos = end % self._capacity
                self._bytes_written += n
        self._wake_readers()

    def read_last(self, duration_seconds: float) -> np.ndarray:
        n = int(duration_seconds * self._sample_rate)
        if n > self._capacity:
            n = self._capacity
        with self._lock:
            if self._bytes_written < self._capacity:
                available = min(n, self._bytes_written)
                if self._write_pos >= available:
                    return self._buffer[self._write_pos - available:self._write_pos].copy()
                tail = self._buffer[self._capacity - (available - self._write_pos):]
                head = self._buffer[:self._write_pos]
                return np.concatenate([tail, head])
            if self._write_pos >= n:
                return self._buffer[self._write_pos - n:self._write_pos].copy()
            tail = self._buffer[self._capacity - (n - self._write_pos):]
            head = self._buffer[:self._write_pos]
            return np.concatenate([tail, head])

    def read_from(self, marker: int, duration_seconds: float) -> np.ndarray:
        n = int(duration_seconds * self._sample_rate)
        with self._lock:
            if self._bytes_written <= marker:
                return np.array([], dtype=np.float32)
            start = marker % self._capacity
            end = start + n
            if end <= self._capacity:
                return self._buffer[start:end].copy()
            tail = self._buffer[start:]
            head = self._buffer[:end - self._capacity]
            return np.concatenate([tail, head])

    @property
    def current_marker(self) -> int:
        return self._bytes_written

    def notify_on_write(self, callback: Callable[[], None]) -> None:
        self._notify.append(callback)

    def remove_write_listener(self, callback: Callable[[], None]) -> None:
        try:
            self._notify.remove(callback)
        except ValueError:
            pass

    def _wake_readers(self) -> None:
        for cb in self._notify:
            try:
                cb()
            except Exception:
                pass

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def capacity_samples(self) -> int:
        return self._capacity


class AudioCapture:
    def __init__(
        self,
        ring_buffer: RingBuffer,
        device: str | None = None,
        channels: int = 1,
        chunk_duration: float = 0.1,
    ):
        self._ring = ring_buffer
        self._device = device
        self._channels = channels
        self._chunk_samples = int(chunk_duration * ring_buffer.sample_rate)
        self._running = False
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    @classmethod
    def is_available(cls, device: str | None = None) -> bool:
        """Fast check if audio input hardware is available and usable."""
        try:
            import sounddevice as sd
            devices = sd.query_devices()
            if not devices:
                return False
            if device is not None:
                dev_info = sd.query_devices(device, "input")
                return dev_info is not None and dev_info.get("max_input_channels", 0) > 0
            default_in = getattr(sd.default, "device", [-1, -1])[0]
            if default_in is not None and default_in >= 0:
                return True
            return any(d.get("max_input_channels", 0) > 0 for d in devices)
        except Exception:
            return False

    def start(self) -> None:
        if self._running:
            return
        if not self.is_available(self._device):
            logger.warning("No available audio input device found; audio capture will remain idle.")
            return
        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True, name="audio-capture")
        self._thread.start()
        logger.info("Audio capture started")

    def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
            self._thread.join(timeout=5)
            self._thread = None
        logger.info("Audio capture stopped")

    def _capture_loop(self) -> None:
        try:
            import sounddevice as sd
            with sd.InputStream(
                samplerate=self._ring.sample_rate,
                channels=self._channels,
                blocksize=self._chunk_samples,
                dtype="float32",
                callback=self._audio_callback,
                device=self._device,
            ):
                self._stop_event.wait()
        except ImportError:
            logger.warning("sounddevice not available, audio capture disabled")
        except Exception as e:
            logger.exception("Audio capture error: %s", e)
        finally:
            self._running = False

    def _audio_callback(self, indata: np.ndarray, frames: int, time_info, status) -> None:
        if status:
            logger.debug("Audio callback status: %s", status)
        if self._running:
            self._ring.write(indata)
