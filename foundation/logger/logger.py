import os
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from foundation.paths import get_log_dir


class HelixLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        s = super().format(record)
        meta = getattr(record, "_meta", None)
        if meta and isinstance(meta, dict):
            meta_str = " ".join(f"{k}={v}" for k, v in meta.items())
            s = f"{s} | {meta_str}"
        return s


class HelixLogger:
    def __init__(self, storage_manager: Any = None):
        self._log_path = get_log_dir()
        self._level = getattr(logging, os.getenv("HELIX_LOG_LEVEL", "INFO").upper(), logging.INFO)
        self._max_size_mb = int(os.getenv("HELIX_LOG_MAX_SIZE_MB", "10"))
        self._retention_days = int(os.getenv("HELIX_LOG_RETENTION_DAYS", "30"))
        self._storage = storage_manager
        self._file: Path | None = None
        self._python_logger = logging.getLogger("helix")
        self._python_logger.setLevel(self._level)
        self._jsonl_count = 0

    async def start(self) -> None:
        try:
            self._log_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            import tempfile
            fallback = Path(tempfile.gettempdir()) / "helix" / "logs"
            self._python_logger.warning(
                "Log path %s is unavailable (%s); using %s",
                self._log_path,
                exc,
                fallback,
            )
            fallback.mkdir(parents=True, exist_ok=True)
            self._log_path = fallback
        self._rotate_if_needed()

        # Prevent duplicate handlers on multiple start() calls
        for h in self._python_logger.handlers[:]:
            h.close()
            self._python_logger.removeHandler(h)

        formatter = HelixLogFormatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )

        handler = logging.FileHandler(
            self._log_path / "helix.log", encoding="utf-8"
        )
        handler.setFormatter(formatter)
        self._python_logger.addHandler(handler)

        console = logging.StreamHandler()
        console.setFormatter(formatter)
        self._python_logger.addHandler(console)

        self._python_logger.info("Logger initialized: %s", self._log_path)

    async def stop(self) -> None:
        for handler in self._python_logger.handlers[:]:
            handler.close()
            self._python_logger.removeHandler(handler)

    def debug(self, msg: str, **meta: Any) -> None:
        self._python_logger.debug(msg, extra=self._meta(meta))

    def info(self, msg: str, **meta: Any) -> None:
        self._python_logger.info(msg, extra=self._meta(meta))

    def warning(self, msg: str, **meta: Any) -> None:
        self._python_logger.warning(msg, extra=self._meta(meta))

    def error(self, msg: str, **meta: Any) -> None:
        self._python_logger.error(msg, extra=self._meta(meta))

    def exception(self, msg: str, **meta: Any) -> None:
        self._python_logger.exception(msg, extra=self._meta(meta))

    def jsonl(self, event: str, **data: Any) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **data,
        }
        log_file = self._log_path / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.jsonl"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        self._jsonl_count += 1
        if self._jsonl_count % 50 == 0:
            self._rotate_if_needed()

    def _meta(self, extra: dict) -> dict:
        return {"_meta": extra}

    def _rotate_if_needed(self) -> None:
        active_log = self._log_path / "helix.log"
        try:
            if active_log.exists() and active_log.stat().st_size > self._max_size_mb * 1024 * 1024:
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
                archive_name = f"{active_log.stem}.{timestamp}.{uuid.uuid4().hex[:8]}{active_log.suffix}"
                # Close file handler before renaming on Windows to avoid sharing violation
                for h in self._python_logger.handlers:
                    if isinstance(h, logging.FileHandler):
                        try:
                            h.close()
                        except Exception:
                            pass
                active_log.replace(active_log.with_name(archive_name))
        except OSError as exc:
            self._python_logger.warning("Unable to rotate active log %s: %s", active_log, exc)

        cutoff = datetime.now(timezone.utc).timestamp() - self._retention_days * 86400
        for pattern in ("*.log*", "*.jsonl"):
            for f in self._log_path.glob(pattern):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink(missing_ok=True)
                except OSError as exc:
                    self._python_logger.warning("Unable to inspect or remove log archive %s: %s", f, exc)
