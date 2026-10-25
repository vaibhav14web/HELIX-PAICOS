import os
import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.system_monitor_engine")


class SystemMonitorEngine:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._interval = int(os.getenv("HELIX_SYSTEM_MONITOR_INTERVAL", "30"))
        self._idle_cpu_threshold = float(os.getenv("HELIX_IDLE_CPU_THRESHOLD", "20"))
        self._idle_duration_required = float(os.getenv("HELIX_IDLE_DURATION_SECONDS", "30"))
        from foundation.paths import get_runtime_dir
        self._runtime_path = os.getenv("HELIX_RUNTIME_PATH", str(get_runtime_dir()))
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}
        self._monitor_task: asyncio.Task | None = None
        self._active = True
        self._last_low_cpu_time: float | None = None

    async def start(self) -> None:
        self._event_handler_map = {
            "system.monitor.request": self._handle_request,
            "system.state.change": self._handle_state_change,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())

        self._active = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("System Monitor Engine started")

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()

        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            self._monitor_task = None

        logger.info("System Monitor Engine stopped")

    async def _handle_request(self, event: HelixEvent) -> None:
        metrics = self._get_metrics()
        await self._event_bus.publish_event(
            source="system_monitor_engine",
            event_type="system.monitor.provided",
            payload=metrics,
            correlation_id=event.correlation_id,
        )

    async def _handle_state_change(self, event: HelixEvent) -> None:
        state = event.payload.get("state", "sleep")
        if state == "sleep":
            self._active = False
            logger.debug("System Monitor paused (sleep state)")
        else:
            if not self._active:
                self._active = True
                logger.debug("System Monitor resumed (state=%s)", state)
            if state == "background":
                await self._event_bus.publish_event(
                    source="system_monitor_engine",
                    event_type="system.monitor.provided",
                    payload=self._get_metrics(),
                )

    async def _monitor_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                if not self._active:
                    continue
                metrics = self._get_metrics()
                await self._event_bus.publish_event(
                    source="system_monitor_engine",
                    event_type="system.monitor.tick",
                    payload=metrics,
                )
                # Publish idle state change if system has been idle long enough
                if metrics.get("is_idle"):
                    await self._event_bus.publish_event(
                        source="system_monitor_engine",
                        event_type="system.idle.detected",
                        payload=metrics,
                    )
        except asyncio.CancelledError:
            pass

    def _get_metrics(self) -> dict[str, Any]:
        now = time.time()
        metrics: dict[str, Any] = {
            "timestamp": now,
        }

        # CPU + Memory
        try:
            import psutil
            cpu = psutil.cpu_percent(interval=0.1)
            metrics["cpu_percent"] = cpu
            vm = psutil.virtual_memory()
            metrics["memory"] = {
                "total": vm.total,
                "used": vm.used,
                "available": vm.available,
                "percent": vm.percent,
            }
            battery = psutil.sensors_battery()
            if battery:
                metrics["battery"] = {
                    "percent": battery.percent,
                    "plugged": battery.power_plugged,
                }

            # Network I/O
            try:
                net_io = psutil.net_io_counters()
                metrics["network"] = {
                    "bytes_sent": net_io.bytes_sent,
                    "bytes_recv": net_io.bytes_recv,
                    "packets_sent": net_io.packets_sent,
                    "packets_recv": net_io.packets_recv,
                }
            except Exception:
                pass

            # Idle detection
            if cpu < self._idle_cpu_threshold:
                if self._last_low_cpu_time is None:
                    self._last_low_cpu_time = now
                idle_duration = now - self._last_low_cpu_time
                metrics["is_idle"] = idle_duration >= self._idle_duration_required
                metrics["idle_seconds"] = idle_duration
            else:
                self._last_low_cpu_time = None
                metrics["is_idle"] = False
                metrics["idle_seconds"] = 0.0

        except ImportError:
            metrics["note"] = "psutil not installed"
            metrics["is_idle"] = False
            metrics["idle_seconds"] = 0.0

        # Disk usage for workspace/runtime path
        try:
            import psutil
            target_path = str(Path.cwd())
            if not os.path.exists(target_path):
                target_path = self._runtime_path
            disk = psutil.disk_usage(target_path)
            metrics["disk"] = {
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": disk.percent,
            }
        except Exception:
            try:
                disk = psutil.disk_usage(self._runtime_path)
                metrics["disk"] = {
                    "total": disk.total,
                    "used": disk.used,
                    "free": disk.free,
                    "percent": disk.percent,
                }
            except Exception:
                pass

        # GPU metrics via nvidia-smi (non-blocking, best effort)
        metrics["gpu"] = self._get_gpu_metrics()

        return metrics

    def _get_gpu_metrics(self) -> dict[str, Any]:
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = [p.strip() for p in result.stdout.strip().split(",")]
                if len(parts) >= 4:
                    return {
                        "utilization_percent": float(parts[0]),
                        "memory_used_mb": float(parts[1]),
                        "memory_total_mb": float(parts[2]),
                        "temperature_c": float(parts[3]),
                    }
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
            pass
        return {"available": False}
