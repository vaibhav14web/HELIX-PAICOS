import os
import asyncio
import json
import logging
import shutil
import sqlite3
import tempfile
import webbrowser
from pathlib import Path
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.browser_engine")

# Browser process names to match against window titles
_BROWSER_KEYWORDS = {
    "chrome": "Chrome",
    "msedge": "Edge",
    "firefox": "Firefox",
    "opera": "Opera",
    "brave": "Brave",
}

# Known browser profile paths (Windows)
_BROWSER_HISTORY_PATHS: dict[str, list[Path]] = {
    "Chrome": [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "Default" / "History",
    ],
    "Edge": [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data" / "Default" / "History",
    ],
}


class BrowserEngine:
    def __init__(
        self,
        event_bus: EventBus,
        browser_history_enabled: bool | None = None,
        config_path: Path | None = None,
    ):
        self._event_bus = event_bus
        self._history_enabled = browser_history_enabled
        self._config_path = config_path
        self._profile_path = os.getenv("HELIX_BROWSER_PROFILE_PATH", "")
        self._history_limit = int(os.getenv("HELIX_BROWSER_HISTORY_LIMIT", "20"))
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}

    def _load_config_safely(self) -> dict[str, Any]:
        """Safely loads config.json, failing closed on any error."""
        try:
            cfg_file = self._config_path
            if cfg_file is None:
                from foundation.paths import get_config_dir
                cfg_file = get_config_dir() / "config.json"
            if not cfg_file.is_file():
                return {}
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.debug("Failed to read configuration from disk: %s", e)
            return {}

    def is_history_enabled(self) -> bool:
        """Check if browser history access is explicitly enabled.

        Defaults strictly to False (fail-closed).
        Requires explicit user opt-in via:
        1. Explicit constructor/instance override `_history_enabled`
        2. Environment variable HELIX_BROWSER_HISTORY_ENABLED
        3. Persistent configuration in config.json (root, ui_settings, or privacy keys)

        Truthy non-boolean strings (except 'true' and '1'), missing values,
        invalid JSON, or any parsing errors resolve strictly to False.
        """
        try:
            # 1. Direct programmatic override
            if self._history_enabled is not None:
                if isinstance(self._history_enabled, bool):
                    return self._history_enabled
                if isinstance(self._history_enabled, str):
                    return self._history_enabled.strip().lower() in ("true", "1")
                return False

            # 2. Environment variable override
            env_val = os.getenv("HELIX_BROWSER_HISTORY_ENABLED")
            if env_val is not None:
                cleaned = env_val.strip().lower()
                # Strict opt-in: only 'true' or '1' enables the gate.
                # Any other value fails closed to False.
                return cleaned in ("true", "1")

            # 3. Persistent config file (config.json)
            config_data = self._load_config_safely()
            if not config_data:
                return False

            # Check root-level key
            val = config_data.get("browser_history_enabled")
            # Fall back to ui_settings or privacy if not at root
            if val is None and isinstance(config_data.get("ui_settings"), dict):
                ui = config_data["ui_settings"]
                val = ui.get("browser_history_enabled")
                if val is None:
                    val = ui.get("browserHistoryEnabled")
            if val is None and isinstance(config_data.get("privacy"), dict):
                val = config_data["privacy"].get("browser_history_enabled")

            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.strip().lower() in ("true", "1")
            if isinstance(val, (int, float)):
                return val == 1

            return False
        except Exception as e:
            logger.warning("Error checking browser history permission; failing closed: %s", e)
            return False

    async def start(self) -> None:
        self._event_handler_map = {
            "context.browser.request": self._handle_request,
            "context.browser.history": self._handle_history,
            "context.browser.open_url": self._handle_open_url,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())
        logger.info("Browser Engine started")

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        logger.info("Browser Engine stopped")

    # ── Event Handlers ─────────────────────────────────────────

    async def _handle_request(self, event: HelixEvent) -> None:
        """Return currently visible browser tabs via Win32 window titles."""
        try:
            loop = asyncio.get_event_loop()
            tabs = await loop.run_in_executor(None, self._get_active_tabs)
            await self._event_bus.publish_event(
                source="browser_engine",
                event_type="context.browser.provided",
                payload={"tabs": tabs},
                correlation_id=event.correlation_id,
            )
        except Exception as e:
            logger.exception("Browser context fetch failed")
            await self._event_bus.publish_event(
                source="browser_engine",
                event_type="context.browser.error",
                payload={"error": str(e)},
                correlation_id=event.correlation_id,
            )

    async def _handle_history(self, event: HelixEvent) -> None:
        """Return recent browser history entries."""
        if not self.is_history_enabled():
            logger.info("Browser history access requested but disabled by policy; explicit opt-in required")
            payload = {
                "history": [],
                "count": 0,
                "status": "disabled",
                "error": "Browser-history access is disabled by default. Explicit opt-in is required in settings.",
                "browser_history_enabled": False,
            }
            await self._event_bus.publish_event(
                source="browser_engine",
                event_type="context.browser.history.disabled",
                payload=payload,
                correlation_id=event.correlation_id,
            )
            await self._event_bus.publish_event(
                source="browser_engine",
                event_type="context.browser.history.provided",
                payload=payload,
                correlation_id=event.correlation_id,
            )
            return

        limit = event.payload.get("limit", self._history_limit)
        browser = event.payload.get("browser", "")
        try:
            loop = asyncio.get_event_loop()
            history = await loop.run_in_executor(
                None, self._get_browser_history, browser, limit
            )
            await self._event_bus.publish_event(
                source="browser_engine",
                event_type="context.browser.history.provided",
                payload={"history": history, "count": len(history)},
                correlation_id=event.correlation_id,
            )
        except Exception as e:
            logger.exception("Browser history fetch failed")
            await self._event_bus.publish_event(
                source="browser_engine",
                event_type="context.browser.error",
                payload={"error": str(e)},
                correlation_id=event.correlation_id,
            )

    async def _handle_open_url(self, event: HelixEvent) -> None:
        """Open a URL in the default browser."""
        url = event.payload.get("url", "")
        if not url:
            return
        try:
            webbrowser.open(url)
            await self._event_bus.publish_event(
                source="browser_engine",
                event_type="context.browser.url_opened",
                payload={"url": url},
                correlation_id=event.correlation_id,
            )
        except Exception as e:
            logger.exception("Failed to open URL: %s", url)
            await self._event_bus.publish_event(
                source="browser_engine",
                event_type="context.browser.error",
                payload={"error": str(e), "url": url},
                correlation_id=event.correlation_id,
            )

    # ── Win32 Window Title Enumeration ─────────────────────────

    def _get_active_tabs(self) -> list[dict[str, str]]:
        """Enumerate visible windows and extract browser tab titles.

        Uses Win32 EnumWindows via ctypes — no external dependency.
        Returns a list of dicts with 'title' and 'browser' keys.
        """
        tabs: list[dict[str, str]] = []
        try:
            import ctypes
            import ctypes.wintypes

            user32 = ctypes.windll.user32  # type: ignore[attr-defined]

            EnumWindowsProc = ctypes.WINFUNCTYPE(
                ctypes.wintypes.BOOL,
                ctypes.wintypes.HWND,
                ctypes.wintypes.LPARAM,
            )

            def callback(hwnd: int, _lparam: int) -> bool:
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True

                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value

                # Check if this window belongs to a known browser
                # Get process name via GetWindowThreadProcessId + OpenProcess
                browser_name = self._identify_browser_from_title(title)
                if browser_name:
                    # Strip browser name suffix from title (e.g. "Page - Google Chrome" → "Page")
                    clean_title = self._clean_tab_title(title, browser_name)
                    tabs.append({"title": clean_title, "browser": browser_name})

                return True

            user32.EnumWindows(EnumWindowsProc(callback), 0)
        except (OSError, AttributeError) as e:
            logger.debug("Win32 window enumeration unavailable: %s", e)
        return tabs

    def _identify_browser_from_title(self, title: str) -> str:
        """Check if a window title belongs to a known browser or editor."""
        title_lower = title.lower()
        if "google chrome" in title_lower or title_lower.endswith("- chrome"):
            return "Chrome"
        if "microsoft\u200b edge" in title_lower or "- microsoft edge" in title_lower:
            return "Edge"
        if "mozilla firefox" in title_lower or "- firefox" in title_lower:
            return "Firefox"
        if "- opera" in title_lower:
            return "Opera"
        if "- brave" in title_lower:
            return "Brave"
        if "visual studio code" in title_lower or title_lower.endswith("- code"):
            return "VSCode"
        if title_lower.endswith("- notepad"):
            return "Notepad"
        return ""

    def _clean_tab_title(self, title: str, browser: str) -> str:
        """Remove browser/editor name suffix from window title."""
        suffixes = [
            f" - Google Chrome",
            f" - Microsoft\u200b Edge",
            f" - Microsoft Edge",
            f" - Firefox",
            f" - Opera",
            f" - Brave",
            f" — Mozilla Firefox",
            f" - Visual Studio Code",
            f" - Code",
            f" - Notepad",
        ]
        for suffix in suffixes:
            if title.endswith(suffix):
                return title[: -len(suffix)].strip()
        return title.strip()

    # ── Browser History via SQLite ─────────────────────────────

    def _get_browser_history(self, browser: str, limit: int) -> list[dict[str, Any]]:
        """Read recent entries from browser's History SQLite database.

        The History file is locked while the browser is running, so we
        copy it to a temp file first (read-only, non-destructive).
        """
        if not self.is_history_enabled():
            logger.info("Browser history access blocked: feature is disabled")
            return []

        # Bounded history window: cap limit to max 50 entries
        limit = min(max(1, limit), 50)
        # If user specified a custom profile path, use it
        if self._profile_path:
            history_path = Path(self._profile_path) / "History"
            if history_path.exists():
                return self._read_history_db(history_path, limit)
            return []

        # Auto-detect from known browser paths
        browsers_to_try = (
            [browser] if browser in _BROWSER_HISTORY_PATHS
            else list(_BROWSER_HISTORY_PATHS.keys())
        )

        for b in browsers_to_try:
            for path in _BROWSER_HISTORY_PATHS.get(b, []):
                if path.exists():
                    entries = self._read_history_db(path, limit)
                    if entries:
                        return entries
        return []

    def _read_history_db(self, history_path: Path, limit: int) -> list[dict[str, Any]]:
        """Read URLs from a Chromium-based History SQLite file.

        Copies the database to avoid lock conflicts with the running browser.
        """
        if not self.is_history_enabled():
            logger.info("Browser history DB read blocked: feature is disabled")
            return []

        limit = min(max(1, limit), 50)
        entries: list[dict[str, Any]] = []
        tmp_path = None
        try:
            # Copy to temp file to avoid SQLite lock from running browser
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=".sqlite")
            os.close(tmp_fd)
            shutil.copy2(str(history_path), tmp_path)

            conn = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                """
                SELECT url, title, last_visit_time, visit_count
                FROM urls
                ORDER BY last_visit_time DESC
                LIMIT ?
                """,
                (limit,),
            )
            for row in cursor:
                entries.append({
                    "url": row["url"],
                    "title": row["title"],
                    "visit_count": row["visit_count"],
                })
            conn.close()
        except Exception as e:
            logger.debug("Failed to read browser history from %s: %s", history_path, e)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        return entries
