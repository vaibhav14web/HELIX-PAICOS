import sys
import asyncio
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

from foundation.event_bus.event_bus import EventBus
from foundation.paths import get_discovered_app_roots

logger = logging.getLogger("helix.world_model")


def _get_default_explorer() -> str:
    try:
        return str(get_discovered_app_roots()["system_root"] / "explorer.exe")
    except Exception:
        return r"C:\Windows\explorer.exe" if sys.platform == "win32" else "/bin/sh"


def _get_default_executable(process_name: str, executable_path: str = "") -> str:
    if executable_path:
        return executable_path
    try:
        return str(get_discovered_app_roots()["system32"] / process_name)
    except Exception:
        return rf"C:\Windows\System32\{process_name}" if sys.platform == "win32" else f"/usr/bin/{process_name}"


@dataclass
class ActiveWindowContext:
    process_id: int = 0
    process_name: str = "explorer.exe"
    window_title: str = "Desktop"
    executable_path: str = field(default_factory=_get_default_explorer)
    hwnd: int = 0


@dataclass
class SystemEnvironmentState:
    active_window: ActiveWindowContext = field(default_factory=ActiveWindowContext)
    monitors_count: int = 1
    connected_audio_devices: list[str] = field(default_factory=lambda: ["Default Speakers", "Default Microphone"])
    recent_clipboard_type: str = "text"
    active_ide_workspace: str | None = None
    active_terminal_count: int = 0
    active_browser_title: str | None = None
    filesystem_watchers_active: bool = True
    last_perceived: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class WorldModelEngine:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._state = SystemEnvironmentState()
        self._perception_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._perception_task = asyncio.create_task(self._perception_loop())
        logger.info("WorldModelEngine started successfully")

    async def stop(self) -> None:
        if self._perception_task and not self._perception_task.done():
            self._perception_task.cancel()
            self._perception_task = None
        logger.info("WorldModelEngine stopped")

    def get_environment_state(self) -> SystemEnvironmentState:
        return self._state

    def update_active_window(
        self,
        process_name: str,
        window_title: str,
        executable_path: str = "",
        process_id: int = 0,
        hwnd: int = 0,
    ) -> None:
        self._state.active_window = ActiveWindowContext(
            process_id=process_id,
            process_name=process_name,
            window_title=window_title,
            executable_path=_get_default_executable(process_name, executable_path),
            hwnd=hwnd,
        )
        self._state.last_perceived = datetime.now(timezone.utc).isoformat()
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                loop.create_task(self._broadcast_window_change())
        except RuntimeError:
            # Called synchronously from non-async thread or before loop starts
            pass

    async def _broadcast_window_change(self) -> None:
        await self._event_bus.publish_event(
            source="world_model",
            event_type="world.window.changed",
            payload=self._state.active_window.__dict__,
        )

    async def _perception_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(10)
                await self._sample_active_window()
        except asyncio.CancelledError:
            pass

    async def _sample_active_window(self) -> None:
        if sys.platform == "win32":
            try:
                import os
                import ctypes
                from ctypes import wintypes

                hwnd = ctypes.windll.user32.GetForegroundWindow()
                if hwnd:
                    length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                    buff = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
                    title = buff.value

                    pid = wintypes.DWORD()
                    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    process_id = int(pid.value)

                    process_name = "active_app.exe"
                    executable_path = ""
                    # 0x1000 = PROCESS_QUERY_LIMITED_INFORMATION
                    h_process = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id)
                    if h_process:
                        try:
                            exe_buff = ctypes.create_unicode_buffer(1024)
                            size = wintypes.DWORD(1024)
                            if ctypes.windll.kernel32.QueryFullProcessImageNameW(h_process, 0, exe_buff, ctypes.byref(size)):
                                executable_path = exe_buff.value
                                process_name = os.path.basename(executable_path)
                        finally:
                            ctypes.windll.kernel32.CloseHandle(h_process)

                    if title and (
                        title != self._state.active_window.window_title
                        or (process_id and process_id != self._state.active_window.process_id)
                    ):
                        self.update_active_window(
                            process_name=process_name,
                            window_title=title,
                            executable_path=executable_path,
                            process_id=process_id,
                            hwnd=int(hwnd),
                        )
            except Exception as e:
                logger.debug("Win32 active window check skipped: %s", e)
