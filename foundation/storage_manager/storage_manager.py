import os
import logging
from pathlib import Path

logger = logging.getLogger("helix.storage_manager")


from foundation.paths import get_runtime_dir, get_cold_dir


class StorageManager:
    def __init__(self):
        self._runtime_path = get_runtime_dir()
        self._cold_path = get_cold_dir()
        self._cold_available = os.getenv("HELIX_COLD_AVAILABLE", "false").lower() in ("true", "1", "yes")

    async def start(self) -> None:
        try:
            self._runtime_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            import tempfile
            fallback = Path(tempfile.gettempdir()) / "helix" / "runtime"
            logger.warning(
                "Runtime path %s is unavailable (%s); using %s",
                self._runtime_path,
                exc,
                fallback,
            )
            fallback.mkdir(parents=True, exist_ok=True)
            self._runtime_path = fallback
        if self._cold_available:
            try:
                self._cold_path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                logger.warning("Cold storage path %s is unavailable: %s", self._cold_path, exc)
                self._cold_available = False

    async def stop(self) -> None:
        pass

    def runtime(self, *subdirs: str) -> Path:
        path = self._runtime_path.joinpath(*subdirs)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cold(self, *subdirs: str) -> Path:
        path = self._cold_path.joinpath(*subdirs)
        if self._cold_available:
            path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def cold_available(self) -> bool:
        return self._cold_available
