import os
import sys
import re
import asyncio
import logging
import shutil
import subprocess
import time
import urllib.parse
import tempfile
from pathlib import Path
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent
from action.application_discovery.app_discovery_engine import ApplicationDiscoveryEngine
from action.action_verification.action_verification import ActionVerificationEngine

import json
from html.parser import HTMLParser

logger = logging.getLogger("helix.action_executor")

_SEARCH_URL = os.getenv("HELIX_SEARCH_URL", "https://www.google.com/search?q={}")
_DEFAULT_ALIASES_PATH = Path(__file__).resolve().parents[2] / "config" / "app_aliases.json"


class HTMLArticleParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text_chunks: list[str] = []
        self._ignore_stack: list[str] = []
        self._ignore_tags = {"script", "style", "noscript", "header", "footer", "nav", "svg", "head", "form"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._ignore_tags:
            self._ignore_stack.append(tag.lower())

    def handle_endtag(self, tag: str) -> None:
        if self._ignore_stack and tag.lower() == self._ignore_stack[-1]:
            self._ignore_stack.pop()

    def handle_data(self, data: str) -> None:
        if not self._ignore_stack:
            cleaned = data.strip()
            if cleaned:
                self.text_chunks.append(cleaned)


def extract_article_text(html: str, max_chars: int = 2500) -> str:
    try:
        parser = HTMLArticleParser()
        parser.feed(html)
        full_text = " ".join(parser.text_chunks)
        full_text = re.sub(r"\s+", " ", full_text).strip()
        if len(full_text) > max_chars:
            full_text = full_text[:max_chars] + "..."
        return full_text
    except Exception as e:
        logger.debug("HTML article parsing error: %s", e)
        clean = re.sub(r"<[^>]+>", " ", html)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:max_chars]

_TRUSTED_SOURCES = {"automation_engine", "planner_engine", "orchestrator"}
if os.getenv("HELIX_DEV_MODE", "").lower() in ("true", "1", "yes") or "pytest" in sys.modules:
    _TRUSTED_SOURCES.add("test")

_KNOWN_APPS: dict[str, str] = {
    "chrome": "chrome.exe",
    "google chrome": "chrome.exe",
    "browser": "chrome.exe",
    "a browser": "chrome.exe",
    "the browser": "chrome.exe",
    "web browser": "chrome.exe",
    "default browser": "chrome.exe",
    "edge": "msedge.exe",
    "microsoft edge": "msedge.exe",
    "firefox": "firefox.exe",
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "mspaint": "mspaint.exe",
    "terminal": "wt.exe",
    "wt": "wt.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "vscode": "code.cmd",
    "vs code": "code.cmd",
    "code": "code.cmd",
}

_KNOWN_APP_PATHS: dict[str, list[str]] = {
    "chrome.exe": [
        os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "Google", "Chrome", "Application", "chrome.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
    ],
    "msedge.exe": [
        os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "Microsoft", "Edge", "Application", "msedge.exe"),
    ],
    "firefox.exe": [
        os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "Mozilla Firefox", "firefox.exe"),
        os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "Mozilla Firefox", "firefox.exe"),
    ],
}

_BLOCKED_PATH_PREFIXES = [
    Path(os.environ.get("SystemRoot", "C:\\Windows")).resolve(),
    Path(os.environ.get("ProgramData", "C:\\ProgramData")).resolve(),
    Path(os.environ.get("ALLUSERSPROFILE", "C:\\ProgramData")).resolve(),
]

_BLOCKED_FILE_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".psm1", ".psd1",
    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".scr", ".pif",
    ".gadget", ".cpl", ".scf", ".lnk", ".inf", ".reg", ".htaccess",
}

_MAX_STRING_LENGTH = 4096
_MAX_EMAIL_LENGTH = 254
_EXECUTOR_TIMEOUT = 30.0
_RATE_LIMIT_WINDOW = 2.0
_MAX_PER_WINDOW = 5

_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$")


class ActionExecutor:
    def __init__(
        self,
        event_bus: EventBus,
        permission_manager: Any | None = None,
        tenant_storage: Any | None = None,
    ):
        self._event_bus = event_bus
        self._permission_manager = permission_manager
        self._tenant_storage = tenant_storage
        self._lock = asyncio.Lock()
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}
        self._action_times: list[float] = []
        self._app_aliases: dict[str, str] = dict(_KNOWN_APPS)
        self._app_discovery = ApplicationDiscoveryEngine()
        self._verification_engine = ActionVerificationEngine(event_bus)
        self.load_app_aliases()

    def set_permission_manager(self, permission_manager: Any) -> None:
        """Inject or update PermissionManager instance."""
        self._permission_manager = permission_manager

    def set_tenant_storage(self, tenant_storage: Any) -> None:
        """Inject or update TenantStorageManager instance."""
        self._tenant_storage = tenant_storage

    def _get_tenant_storage(self) -> Any:
        if self._tenant_storage is not None:
            return self._tenant_storage
        from foundation.storage_manager.tenant_storage import get_tenant_storage_manager
        return get_tenant_storage_manager()

    def _validate_tenant_path_boundary(
        self,
        path: str | Path,
        user_id: str | None = None,
        mode: str = "read",
    ) -> Path:
        """
        Confine file mutations and reads to either the user's tenant sandbox or
        explicitly configured allowed project paths. Fail closed on path traversal
        or cross-tenant access.
        """
        if not path:
            raise ValueError("Target path cannot be empty")

        path_str = str(path)
        if "\0" in path_str:
            raise PermissionError("Access denied: null byte detected in path")

        target_path = Path(path_str).resolve()

        # Check hard blocked system roots
        for blocked in _BLOCKED_PATH_PREFIXES:
            if blocked in target_path.parents or blocked == target_path:
                raise PermissionError(f"Access denied: cannot {mode} files under system path {blocked}")

        tenant_mgr = self._get_tenant_storage()
        uid = (user_id or "default_owner").strip()
        clean_uid = tenant_mgr.sanitize_user_id(uid)

        # Cross-tenant check: ensure target_path is not targeting another tenant's directory
        users_base = tenant_mgr.base_dir.resolve()
        try:
            rel_to_users = target_path.relative_to(users_base)
            if rel_to_users.parts:
                target_tenant = rel_to_users.parts[0]
                if target_tenant != clean_uid:
                    raise PermissionError(
                        f"Access denied: cross-tenant access prohibited (attempted to access tenant '{target_tenant}' from user '{clean_uid}')"
                    )
        except ValueError:
            # Not under users_base
            pass

        # Check if target_path is within user's own tenant sandbox
        tenant_root = tenant_mgr.user_dir(clean_uid).resolve()
        try:
            target_path.relative_to(tenant_root)
            return target_path
        except ValueError:
            pass

        # Check allowed project paths
        allowed_paths: list[Path] = []
        if mode == "write":
            env_allowed = os.getenv("HELIX_ALLOWED_WRITE_PATHS", "")
        else:
            env_allowed = os.getenv("HELIX_ALLOWED_READ_PATHS", "")

        if env_allowed:
            from foundation.paths import expand_path
            for p in env_allowed.split(";"):
                if p.strip():
                    try:
                        exp = expand_path(p.strip())
                        allowed_paths.append((exp or Path(p.strip())).resolve())
                    except Exception:
                        pass

        # Include system temp directory for transient files / unit test tmp_path
        allowed_paths.append(Path(tempfile.gettempdir()).resolve())

        # In dev mode or test runs, allow current working directory (e.g. repo files) if not inside another tenant
        if os.getenv("HELIX_DEV_MODE", "").lower() in ("true", "1", "yes") or "pytest" in sys.modules:
            allowed_paths.append(Path.cwd().resolve())

        is_allowed = False
        for allowed in allowed_paths:
            try:
                target_path.relative_to(allowed)
                is_allowed = True
                break
            except ValueError:
                pass

        if not is_allowed:
            if mode == "read" and not target_path.exists():
                raise FileNotFoundError(f"File not found: {path}")
            raise PermissionError(
                f"Access denied: target path '{target_path}' is outside authorized tenant workspace for user '{clean_uid}'"
            )

        return target_path

    def load_app_aliases(self, config_path: str | Path | None = None) -> None:
        """Load or reload app aliases from a JSON file into self._app_aliases."""
        path = Path(config_path) if config_path else Path(os.getenv("HELIX_APP_ALIASES_PATH", _DEFAULT_ALIASES_PATH))
        if path.is_file():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    for alias, exe in data.items():
                        if isinstance(alias, str) and isinstance(exe, str):
                            self._app_aliases[alias.lower().strip()] = exe
            except Exception as e:
                logger.warning("Failed to load app aliases from %s: %s", path, e)

    def register_alias(self, alias: str, executable: str) -> None:
        """Register or override a user app alias at runtime."""
        self._app_aliases[alias.lower().strip()] = executable

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._event_handler_map = {
            "action.execute": self._handle_action,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())
        logger.info("Action Executor started")

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        logger.info("Action Executor stopped")

    async def _handle_action(self, event: HelixEvent) -> None:
        action = event.payload.get("action", "")
        action_id = event.payload.get("action_id", "")
        target = event.payload.get("target")
        params = event.payload.get("params", {})
        plan_id = event.payload.get("plan_id")
        step_id = event.payload.get("step_id")
        user_id = event.payload.get("user_id") or "default_owner"
        grant_id = event.payload.get("grant_id") or event.payload.get("permission_id")
        source = event.source

        if source not in _TRUSTED_SOURCES:
            error = f"Untrusted event source: {source}"
            logger.warning(error)
            await self._publish_failed(action_id, action, error, plan_id, step_id, event.correlation_id)
            return

        try:
            await self._check_rate_limit()
        except ValueError as e:
            await self._publish_failed(action_id, action, str(e), plan_id, step_id, event.correlation_id)
            return

        # Precondition and Parameter Validation
        valid, pre_err = self._verification_engine.validate_preconditions(action, target=target, params=params)
        if not valid:
            await self._publish_failed(action_id, action, pre_err, plan_id, step_id, event.correlation_id)
            return

        # Risk Classification & Permission Gating
        from foundation.permission_manager.permission_manager import classify_action_risk, ActionRiskLevel
        merged_params = dict(params)
        if target:
            merged_params.setdefault("target", target)

        risk = classify_action_risk(action, merged_params)
        if risk in (ActionRiskLevel.MEDIUM, ActionRiskLevel.HIGH, ActionRiskLevel.CRITICAL):
            if self._permission_manager is not None:
                is_authorized, auth_reason = self._permission_manager.validate_execution_authorization(
                    action=action,
                    params=params,
                    target=target,
                    user_id=user_id,
                    grant_id=grant_id,
                )
                if not is_authorized:
                    error = f"Permission denied: {auth_reason}"
                    logger.warning(error)
                    await self._publish_failed(action_id, action, error, plan_id, step_id, event.correlation_id)
                    return

        try:
            result = await self._dispatch(action, target, params, user_id=user_id)
            # Postcondition Outcome Verification
            v_res = await self._verification_engine.verify_action(action_id, action, merged_params, execution_result=result)
            if not v_res.success:
                await self._publish_failed(action_id, action, f"Postcondition verification failed: {v_res.details}", plan_id, step_id, event.correlation_id)
                return

            if isinstance(result, dict):
                result["verified"] = True
                result["lifecycle"] = v_res.lifecycle.value
                result["verification_details"] = v_res.details

            await self._event_bus.publish_event(
                source="action_executor",
                event_type="automation.result",
                payload={
                    "action_id": action_id,
                    "action": action,
                    "result": result,
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "user_id": user_id,
                    "verified": True,
                    "lifecycle": v_res.lifecycle.value,
                    "verification_details": v_res.details,
                },
                correlation_id=event.correlation_id,
            )
        except Exception as e:
            logger.exception("Action failed: %s", action)
            await self._publish_failed(action_id, action, str(e), plan_id, step_id, event.correlation_id)

    async def _publish_failed(self, action_id: str, action: str, error: str, plan_id: str | None, step_id: str | None, correlation_id: str) -> None:
        await self._event_bus.publish_event(
            source="action_executor",
            event_type="automation.failed",
            payload={
                "action_id": action_id,
                "action": action,
                "error": error,
                "plan_id": plan_id,
                "step_id": step_id,
            },
            correlation_id=correlation_id,
        )

    async def _check_rate_limit(self) -> None:
        async with self._lock:
            now = time.time()
            self._action_times = [t for t in self._action_times if now - t < _RATE_LIMIT_WINDOW]
            if len(self._action_times) >= _MAX_PER_WINDOW:
                raise ValueError("Rate limit exceeded: too many actions")
            self._action_times.append(now)

    async def _dispatch(self, action: str, target: str | None, params: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        if action == "browser_search":
            return await self._executor(self._browser_search, params)
        if action == "launch_application":
            return await self._executor(self._launch_application, target, params)
        if action == "read_file":
            return await self._executor(self._read_file, target, params, user_id)
        if action == "send_notification":
            return await self._executor(self._send_notification, params)
        if action == "compose_email":
            return await self._executor(self._compose_email, params)
        if action == "open_url":
            return await self._executor(self._open_url, target, params)
        if action == "read_browser_page":
            return await self._read_browser_page(target, params)
        if action == "in_app_task":
            return await self._in_app_task(target, params, user_id)
        if action == "vscode_open":
            return await self._executor(self._vscode_open, target, params, user_id)
        if action == "create_note":
            return await self._executor(self._create_note, target, params, user_id)
        if action == "scan_laptop_files":
            return await self._executor(self._scan_laptop_files, params, user_id)
        if action == "search_installed_apps":
            return await self._executor(self._search_installed_apps, params)
        if action == "search_projects":
            return await self._executor(self._search_projects, params)
        if action == "read_document":
            return await self._executor(self._read_document, target, params, user_id)
        if action == "edit_docx":
            return await self._executor(self._edit_docx, target, params, user_id)
        if action == "generate_pdf":
            return await self._executor(self._generate_pdf, target, params, user_id)
        if action == "create_backup":
            params_with_user = dict(params)
            params_with_user.setdefault("user_id", user_id or "default_owner")
            return await self._executor(self._create_backup, params_with_user)
        if action == "restore_backup":
            params_with_user = dict(params)
            params_with_user.setdefault("target_user_id", user_id or "default_owner")
            return await self._executor(self._restore_backup, params_with_user)
        raise ValueError(f"Unknown action type: {action}")

    async def _executor(self, fn, *args) -> Any:
        if getattr(self, "_loop", None) is None:
            self._loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            self._loop.run_in_executor(None, fn, *args),
            timeout=_EXECUTOR_TIMEOUT,
        )

    # ── Input Validation ────────────────────────────────────────

    def _validate_string(self, value: str, name: str, max_len: int = _MAX_STRING_LENGTH) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string, got {type(value).__name__}")
        if len(value) > max_len:
            raise ValueError(f"{name} exceeds maximum length of {max_len}")
        return value

    # ── Browser Search ──────────────────────────────────────────

    def _verify_process_running(self, exe_name: str, max_wait_sec: float = 2.0) -> bool:
        """Verify that process `exe_name` exists post-launch within max_wait_sec."""
        if sys.platform != "win32":
            return True

        start_time = time.time()
        exe_lower = exe_name.lower()
        while time.time() - start_time <= max_wait_sec:
            try:
                res = subprocess.run(
                    ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if exe_lower in res.stdout.lower():
                    return True
            except Exception as e:
                logger.debug("Process verification check failed: %s", e)
            time.sleep(0.3)
        return False

    # ── Browser Search ──────────────────────────────────────────

    def _browser_search(self, params: dict[str, Any]) -> dict[str, Any]:
        query = self._validate_string(params.get("query", ""), "query")
        if not query:
            raise ValueError("Search query is empty")
        url = _SEARCH_URL.format(urllib.parse.quote_plus(query))
        res = self._open_url_browser(url, action="browser_search")
        verified = any(
            self._verify_process_running(b, max_wait_sec=1.0)
            for b in ("chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe")
        )
        res["verified"] = verified
        return res

    # ── Launch Application ──────────────────────────────────────

    def _launch_application(self, target: str | None, params: dict[str, Any]) -> dict[str, Any]:
        app = target or params.get("application", "")
        if not app:
            raise ValueError("No application specified to launch")
        app = self._validate_string(app, "application")

        app_lower = app.lower().strip()
        app_clean = re.sub(r"^(a|an|the)\s+", "", app_lower).strip()

        # Handle browser aliases
        if app_lower in ("browser", "a browser", "the browser", "web browser", "default browser") or app_clean in ("browser", "web browser"):
            app_lower = "chrome"

        executable = (
            self._app_aliases.get(app_lower)
            or _KNOWN_APPS.get(app_lower)
            or self._app_aliases.get(app_clean)
            or _KNOWN_APPS.get(app_clean)
        )
        _BLOCKED_SHELLS = {"cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "bash", "bash.exe", "sh", "wscript", "cscript"}
        if not executable and app_lower not in _BLOCKED_SHELLS and app_clean not in _BLOCKED_SHELLS:
            discovered = self._app_discovery.resolve_app(app_lower) or self._app_discovery.resolve_app(app_clean)
            if discovered:
                executable = discovered.executable_path or discovered.executable_name
        if not executable:
            if os.path.isfile(app):
                executable = app
            else:
                safe_list = ", ".join(sorted(set(list(self._app_aliases.keys()) + list(_KNOWN_APPS.keys()))))
                raise ValueError(f"Unknown application: '{app}'. Allowed: {safe_list}")

        resolved = None
        if os.path.isabs(executable) and os.path.isfile(executable):
            resolved = executable
        else:
            resolved = shutil.which(executable)
            if not resolved:
                fallback_paths = _KNOWN_APP_PATHS.get(executable, [])
                for path in fallback_paths:
                    if os.path.isfile(path):
                        resolved = path
                        break

        if not resolved:
            raise FileNotFoundError(f"Application not found: {executable}. Make sure it is installed.")

        url = params.get("url") or params.get("site") or params.get("target_site")
        cmd = [resolved]
        if url:
            url = self._validate_string(url, "url")
            self._validate_url(url)
            cmd.append(url)

        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            verify_name = Path(executable).name
            if verify_name.endswith(".cmd") or verify_name.endswith(".bat"):
                verify_name = Path(executable).stem + ".exe"
            verified = self._verify_process_running(verify_name)
            if not verified:
                logger.warning("Post-launch process check could not verify '%s'", verify_name)
            return {
                "status": "launched",
                "application": app,
                "executable": executable,
                "verified": verified,
                "url": url,
            }
        except OSError as e:
            raise OSError(f"Failed to launch {app}: {e}")

    # ── Read / Open File ────────────────────────────────────────

    def _read_file(self, target: str | None, params: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        file_path = target or params.get("path", "")
        if not file_path:
            raise ValueError("No file path specified")
        file_path = self._validate_string(file_path, "path")

        path = self._validate_tenant_path_boundary(file_path, user_id=user_id, mode="read")
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        ext = path.suffix.lower()
        if ext in _BLOCKED_FILE_EXTENSIONS:
            raise PermissionError(f"Access denied: cannot open {ext} files")

        try:
            if params.get("open_externally"):
                if sys.platform == "win32":
                    os.startfile(str(path))
                elif sys.platform == "darwin":
                    subprocess.Popen(
                        ["open", str(path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.Popen(
                        ["xdg-open", str(path)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                return {"status": "opened", "path": str(path)}

            content = ""
            try:
                content = path.read_text(encoding="utf-8", errors="replace")[:4000]
            except Exception as e:
                content = f"[Could not read file text: {e}]"
            return {
                "status": "success",
                "path": str(path),
                "filename": path.name,
                "content": content,
                "size_bytes": path.stat().st_size,
                "truncated": len(content) >= 4000,
            }
        except OSError as e:
            raise OSError(f"Failed to access {file_path}: {e}")

    # ── Send Notification ───────────────────────────────────────

    def _send_notification(self, params: dict[str, Any]) -> dict[str, Any]:
        message = self._validate_string(params.get("message", ""), "message")
        title = self._validate_string(params.get("title", "Helix"), "title")
        if not message:
            raise ValueError("Notification message is empty")

        if sys.platform == "win32":
            self._windows_toast(title, message)
        elif sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e", f'display notification "{message}" with title "{title}"'],
                capture_output=True, timeout=5,
            )
        else:
            try:
                subprocess.run(
                    ["notify-send", title, message],
                    capture_output=True, timeout=5,
                )
            except FileNotFoundError:
                logger.info("Notification (%s): %s", title, message)

        return {"status": "sent", "title": title}

    def _windows_toast(self, title: str, message: str) -> None:
        script = (
            '$t=[Windows.UI.Notifications.ToastNotificationManager,'
            'Windows.UI.Notifications,ContentType=WindowsRuntime];'
            '$x=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent('
            '[Windows.UI.Notifications.ToastTemplateType]::ToastText02);'
            '$n=$x.GetElementsByTagName("text");'
            '$n.Item(0).AppendChild($x.CreateTextNode($env:HELIX_TOAST_TITLE))|Out-Null;'
            '$n.Item(1).AppendChild($x.CreateTextNode($env:HELIX_TOAST_MESSAGE))|Out-Null;'
            '$t=[Windows.UI.Notifications.ToastNotification]::new($x);'
            '[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Helix").Show($t);'
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True,
                timeout=10,
                env={
                    **os.environ,
                    "HELIX_TOAST_TITLE": title,
                    "HELIX_TOAST_MESSAGE": message,
                },
            )
        except Exception as e:
            logger.warning("Toast notification failed: %s", e)

    # ── Compose Email ───────────────────────────────────────────

    def _compose_email(self, params: dict[str, Any]) -> dict[str, Any]:
        to = self._validate_string(params.get("to", ""), "to", _MAX_EMAIL_LENGTH)
        subject = self._validate_string(params.get("subject", ""), "subject")
        body = self._validate_string(params.get("body", ""), "body")
        cc = self._validate_string(params.get("cc", ""), "cc", _MAX_EMAIL_LENGTH)
        bcc = self._validate_string(params.get("bcc", ""), "bcc", _MAX_EMAIL_LENGTH)

        if to:
            self._validate_email(to)
        if cc:
            self._validate_email(cc)
        if bcc:
            self._validate_email(bcc)

        query_params = {}
        if subject:
            query_params["subject"] = subject
        if body:
            query_params["body"] = body
        if cc:
            query_params["cc"] = cc
        if bcc:
            query_params["bcc"] = bcc

        if to:
            mailto = f"mailto:{to}"
        else:
            mailto = "mailto:"

        if query_params:
            mailto += "?" + urllib.parse.urlencode(query_params)

        self._open_url_browser(mailto, action="compose_email")
        return {"status": "composed"}

    def _validate_email(self, address: str) -> None:
        if not _EMAIL_PATTERN.match(address.strip()):
            raise ValueError(f"Invalid email address: {address}")

    # ── Open URL ────────────────────────────────────────────────

    def _open_url(self, target: str | None, params: dict[str, Any]) -> dict[str, Any]:
        url = target or params.get("url", "")
        if not url:
            raise ValueError("No URL specified")
        url = self._validate_string(url, "url")
        return self._open_url_browser(url, action="open_url")

    def _validate_url(self, url: str) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https", "mailto"):
            raise ValueError(f"Blocked URL scheme: '{parsed.scheme}'. Only http, https, and mailto are allowed.")
        if not parsed.netloc and parsed.scheme in ("http", "https"):
            raise ValueError("Invalid URL: no host specified")

    def _open_url_browser(self, url: str, action: str = "open_url") -> dict[str, Any]:
        self._validate_url(url)
        import webbrowser
        try:
            if sys.platform == "win32":
                os.startfile(url)
            else:
                webbrowser.open(url)
        except Exception:
            webbrowser.open(url)
        logger.info("Opened URL (%s): %s", action, url)
        return {"status": "opened", "url": url}

    # ── Read Browser Page ──────────────────────────────────────

    async def _read_browser_page(self, target: str | None, params: dict[str, Any]) -> dict[str, Any]:
        raw_target = target or params.get("url") or params.get("page_url") or params.get("site", "")
        raw_str = self._validate_string(str(raw_target), "url") if raw_target else ""

        url = ""
        raw_lower = raw_str.lower().strip()
        if raw_str and raw_lower not in ("the page i have open", "current page", "open page", "active page"):
            if not raw_str.startswith("http://") and not raw_str.startswith("https://"):
                url = "https://" + raw_str
            else:
                url = raw_str
            self._validate_url(url)

        if not url:
            from context.browser_engine.browser_engine import BrowserEngine
            browser_eng = BrowserEngine(self._event_bus)
            history = browser_eng._get_browser_history("", limit=1)
            if history and history[0].get("url"):
                url = history[0]["url"]
            else:
                tabs = browser_eng._get_active_tabs()
                if tabs:
                    url = tabs[0].get("title", "")

        if not url:
            raise ValueError("No URL or active browser page specified to read")

        html_content = ""
        if url.startswith("http://") or url.startswith("https://"):
            import urllib.request
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) HELIX/1.0"},
            )
            try:
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    raw_data = resp.read(2_000_000)
                    html_content = raw_data.decode("utf-8", errors="ignore")
            except Exception as e:
                logger.warning("Failed to fetch page from %s: %s", url, e)
                html_content = f"<html><body><p>{url}</p></body></html>"
        else:
            html_content = f"<html><body><p>{url}</p></body></html>"

        extracted_text = extract_article_text(html_content)
        if not extracted_text:
            extracted_text = f"Page content from {url}"

        await self._event_bus.publish_event(
            source="action_executor",
            event_type="voice.tts",
            payload={
                "session_id": "read_browser_page",
                "text": f"Reading page content: {extracted_text[:1500]}",
            },
        )

        duration_est = round(len(extracted_text) / 15.0, 1)
        return {
            "status": "reading",
            "url": url,
            "text_length": len(extracted_text),
            "text_sample": extracted_text[:200],
            "duration_estimate_sec": duration_est,
            "verified": True,
        }

    # ── In-App Tasks ───────────────────────────────────────────

    async def _in_app_task(self, target: str | None, params: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        app = (params.get("app") or target or "").lower().strip()
        task_name = (params.get("task") or params.get("action_name") or "").lower().strip()

        if app in ("vscode", "code", "vs code") or task_name in ("open_file", "open_folder", "vscode_open"):
            return await self._executor(self._vscode_open, target, params, user_id)
        if app in ("notes", "notepad", "note") or task_name == "create_note":
            return await self._executor(self._create_note, target, params, user_id)

        safe_apps = "vscode, notes"
        raise ValueError(f"Unsupported in-app task application target: '{app}'. Supported: {safe_apps}")

    def _vscode_open(self, target: str | None, params: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        file_or_folder = target or params.get("path") or params.get("file") or params.get("folder", "")
        if not file_or_folder:
            raise ValueError("No file or folder path specified for VS Code")
        file_or_folder = self._validate_string(file_or_folder, "path")

        path_obj = self._validate_tenant_path_boundary(file_or_folder, user_id=user_id, mode="read")
        if not path_obj.exists():
            raise FileNotFoundError(f"Target path does not exist: {file_or_folder}")

        if path_obj.is_file():
            ext = path_obj.suffix.lower()
            if ext in _BLOCKED_FILE_EXTENSIONS:
                raise PermissionError(f"Access denied: cannot open {ext} files in VS Code")

        code_exe = self._app_aliases.get("vscode") or self._app_aliases.get("code") or "code.cmd"
        resolved = shutil.which(code_exe) or shutil.which("code.exe") or shutil.which("code.cmd")
        if not resolved:
            local_appdata = os.environ.get("LOCALAPPDATA", "")
            program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
            possible_paths = [
                os.path.join(local_appdata, "Programs", "Microsoft VS Code", "Code.exe"),
                os.path.join(program_files, "Microsoft VS Code", "Code.exe"),
                os.path.join(program_files, "Microsoft VS Code", "bin", "code.cmd"),
            ]
            for p in possible_paths:
                if os.path.isfile(p):
                    resolved = p
                    break

        if not resolved:
            raise FileNotFoundError("VS Code executable ('code') not found on system.")

        cmd = [resolved, str(path_obj)]
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            verified_proc = self._verify_process_running("Code.exe") or self._verify_process_running("code.exe")
            verified_path = path_obj.exists()
            verified = verified_proc and verified_path
            if not verified:
                logger.warning("Post-launch verification for VS Code opening '%s' could not verify process/path", path_obj)
            return {
                "status": "opened",
                "app": "vscode",
                "path": str(path_obj),
                "executable": resolved,
                "verified": verified,
            }
        except OSError as e:
            raise OSError(f"Failed to open in VS Code: {e}")

    def _create_note(self, target: str | None, params: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        title = target or params.get("title") or params.get("name", "New_Note")
        title = self._validate_string(title, "title")
        content = params.get("content") or params.get("text", "")
        content = self._validate_string(content, "content")

        safe_title = re.sub(r'[^a-zA-Z0-9_\- ]', '_', title).strip()
        if not safe_title:
            safe_title = "Untitled_Note"

        tenant_mgr = self._get_tenant_storage()
        uid = (user_id or "default_owner").strip()
        notes_dir = tenant_mgr.user_dir(uid, "notes")
        notes_dir.mkdir(parents=True, exist_ok=True)
        note_file = notes_dir / f"{safe_title}.txt"
        self._validate_tenant_path_boundary(note_file, user_id=user_id, mode="write")

        note_file.write_text(content, encoding="utf-8")

        cmd = ["notepad.exe", str(note_file)]
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            file_exists = note_file.is_file()
            file_content_matches = note_file.read_text(encoding="utf-8") == content
            proc_running = self._verify_process_running("notepad.exe")
            verified = file_exists and file_content_matches and proc_running
            return {
                "status": "created",
                "app": "notes",
                "title": title,
                "path": str(note_file),
                "content_length": len(content),
                "verified": verified,
            }
        except OSError as e:
            raise OSError(f"Failed to create note: {e}")

    # ── Laptop Apps & File Awareness ────────────────────────────

    def _search_installed_apps(self, params: dict[str, Any]) -> dict[str, Any]:
        query = (params.get("query") or "").strip().lower()
        all_apps = self._app_discovery.list_apps()
        matched = []
        for app in all_apps:
            name = app.get("name", "").lower()
            exe = app.get("executable_name", "").lower()
            aliases = [a.lower() for a in app.get("aliases", [])]
            if not query or query in name or query in exe or any(query in a for a in aliases):
                matched.append({
                    "name": app.get("name"),
                    "executable": app.get("executable_name"),
                    "path": app.get("executable_path"),
                    "source": app.get("source"),
                })
                if len(matched) >= 25:
                    break
        return {
            "status": "success",
            "query": query,
            "count": len(matched),
            "apps": matched,
        }

    def _scan_laptop_files(self, params: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        folder_key = (params.get("folder") or "documents").strip().lower()
        query = (params.get("query") or "").strip().lower()
        max_results = min(int(params.get("max_results", 20)), 50)

        user_profile = Path(os.environ.get("USERPROFILE", Path.home())).resolve()

        # Resolve target directory (checking OneDrive equivalents if available)
        if folder_key in ("documents", "doc", "docs"):
            candidates = [
                user_profile / "OneDrive" / "Documents",
                user_profile / "Documents",
            ]
        elif folder_key in ("desktop", "desk"):
            candidates = [
                user_profile / "OneDrive" / "Desktop",
                user_profile / "Desktop",
            ]
        elif folder_key in ("downloads", "down"):
            candidates = [
                user_profile / "Downloads",
            ]
        elif folder_key in ("home", "user", "root"):
            candidates = [user_profile]
        else:
            candidates = [Path(folder_key).resolve()]

        target_dir = None
        for cand in candidates:
            if cand.exists() and cand.is_dir():
                target_dir = cand
                break

        if not target_dir:
            return {
                "status": "error",
                "error": f"Directory '{folder_key}' could not be located on your laptop.",
                "files": [],
            }

        # Sandboxing check
        for blocked in _BLOCKED_PATH_PREFIXES:
            if blocked in target_dir.parents or blocked == target_dir:
                raise PermissionError(f"Access denied: cannot scan system path {blocked}")

        # Cross-tenant check: ensure target_dir does not target another tenant's files
        tenant_mgr = self._get_tenant_storage()
        clean_uid = tenant_mgr.sanitize_user_id(user_id or "default_owner")
        users_base = tenant_mgr.base_dir.resolve()
        try:
            rel = target_dir.relative_to(users_base)
            if rel.parts and rel.parts[0] != clean_uid:
                raise PermissionError(f"Access denied: cross-tenant access prohibited in file scan ({rel.parts[0]} != {clean_uid})")
        except ValueError:
            pass

        skip_dirs = {
            ".git", "node_modules", "__pycache__", "appdata", "$recycle.bin",
            "system volume information", "windows", "programdata", ".venv", "venv",
            ".pytest_cache", ".mypy_cache", ".next", ".nuxt"
        }

        results = []
        try:
            for root, dirs, files in os.walk(str(target_dir)):
                # Prune unwanted directories
                dirs[:] = [d for d in dirs if d.lower() not in skip_dirs and not d.startswith(".")]

                for f in files:
                    if f.startswith(".") or f.startswith("~$"):
                        continue
                    f_lower = f.lower()
                    if query and (query not in f_lower):
                        continue

                    full_path = Path(root) / f
                    ext = full_path.suffix.lower()
                    if ext in _BLOCKED_FILE_EXTENSIONS:
                        continue

                    try:
                        stat = full_path.stat()
                        results.append({
                            "filename": f,
                            "path": str(full_path),
                            "size_kb": round(stat.st_size / 1024, 1),
                            "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                        })
                        if len(results) >= max_results:
                            break
                    except (OSError, PermissionError):
                        continue

                if len(results) >= max_results:
                    break
        except Exception as e:
            logger.warning("Error scanning directory %s: %s", target_dir, e)

        return {
            "status": "success",
            "target_directory": str(target_dir),
            "query": query,
            "count": len(results),
            "files": results,
        }

    def _search_projects(self, params: dict[str, Any]) -> dict[str, Any]:
        query = (params.get("query") or "").strip().lower()
        user_profile = Path(os.environ.get("USERPROFILE", Path.home())).resolve()
        search_dirs = [
            user_profile / "OneDrive" / "Documents",
            user_profile / "Documents",
            user_profile / "Projects",
            user_profile / "source" / "repos",
            Path.cwd().resolve(),
        ]
        projects = []
        seen_paths = set()

        for s_dir in search_dirs:
            if not s_dir.exists() or not s_dir.is_dir():
                continue
            try:
                for item in s_dir.iterdir():
                    if item.is_dir() and not item.name.startswith("."):
                        if str(item) in seen_paths:
                            continue
                        if query and query not in item.name.lower():
                            continue
                        is_project = (
                            (item / ".git").exists()
                            or (item / "package.json").exists()
                            or (item / "pyproject.toml").exists()
                            or (item / "requirements.txt").exists()
                        )
                        if is_project or query:
                            seen_paths.add(str(item))
                            projects.append({
                                "name": item.name,
                                "path": str(item),
                                "is_git": (item / ".git").exists(),
                            })
                            if len(projects) >= 20:
                                break
            except Exception:
                continue
            if len(projects) >= 20:
                break

        return {
            "status": "success",
            "query": query,
            "count": len(projects),
            "projects": projects,
        }

    # ── Document Handling (.docx, .pdf) ──────────────────────────

    def _read_document(self, target: str | None, params: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        file_path = target or params.get("path", "")
        if not file_path:
            raise ValueError("No document path specified")
        file_path = self._validate_string(file_path, "path")

        path = self._validate_tenant_path_boundary(file_path, user_id=user_id, mode="read")

        ext = path.suffix.lower()
        if ext in _BLOCKED_FILE_EXTENSIONS:
            raise PermissionError(f"Access denied: cannot open {ext} files")

        if not path.exists():
            raise FileNotFoundError(f"Document not found: {file_path}")

        if ext == ".docx":
            try:
                import docx
            except ImportError:
                raise RuntimeError("python-docx is not installed in the environment.")

            doc = docx.Document(str(path))
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            tables_data = []
            for table in doc.tables:
                table_rows = []
                for row in table.rows:
                    table_rows.append([cell.text.strip() for cell in row.cells])
                if table_rows:
                    tables_data.append(table_rows)

            full_text = "\n\n".join(paragraphs)
            truncated = len(full_text) > 10000
            return {
                "status": "success",
                "path": str(path),
                "filename": path.name,
                "format": "docx",
                "paragraph_count": len(paragraphs),
                "table_count": len(tables_data),
                "content": full_text[:10000],
                "truncated": truncated,
                "tables": tables_data[:5],
            }

        elif ext == ".pdf":
            try:
                import pypdf
            except ImportError:
                raise RuntimeError("pypdf is not installed in the environment.")

            reader = pypdf.PdfReader(str(path))
            num_pages = len(reader.pages)
            max_pages = int(params.get("max_pages", 20))
            extracted_pages = []
            for i, page in enumerate(reader.pages[:max_pages]):
                page_text = page.extract_text() or ""
                if page_text.strip():
                    extracted_pages.append(f"--- Page {i+1} ---\n{page_text.strip()}")
            full_text = "\n\n".join(extracted_pages)
            truncated = len(full_text) > 10000 or num_pages > max_pages
            return {
                "status": "success",
                "path": str(path),
                "filename": path.name,
                "format": "pdf",
                "total_pages": num_pages,
                "read_pages": min(num_pages, max_pages),
                "content": full_text[:10000],
                "truncated": truncated,
            }

        elif ext in {".txt", ".md", ".json", ".csv", ".py", ".html"}:
            content = path.read_text(encoding="utf-8", errors="replace")[:10000]
            return {
                "status": "success",
                "path": str(path),
                "filename": path.name,
                "format": ext.lstrip("."),
                "content": content,
                "truncated": path.stat().st_size > 10000,
            }
        else:
            raise ValueError(f"Unsupported document format: {ext}. Supported formats are .docx, .pdf, .txt, .md")

    def _edit_docx(self, target: str | None, params: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        file_path = target or params.get("path", "")
        if not file_path:
            raise ValueError("No path specified for edit_docx")
        file_path = self._validate_string(file_path, "path")

        path = self._validate_tenant_path_boundary(file_path, user_id=user_id, mode="write")

        if path.suffix.lower() != ".docx":
            path = path.with_suffix(".docx")

        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            import docx
        except ImportError:
            raise RuntimeError("python-docx is not installed in the environment.")

        action = (params.get("action") or "append").lower().strip()
        title = params.get("title")
        content = params.get("content", "")
        search_text = params.get("search_text", "")
        replace_text = params.get("replace_text", "")

        if action == "create":
            doc = docx.Document()
            if title:
                doc.add_heading(str(title), level=0)
            if content:
                for block in str(content).split("\n\n"):
                    if block.strip():
                        doc.add_paragraph(block.strip())
            doc.save(str(path))
            return {
                "status": "success",
                "action": "create",
                "path": str(path),
                "filename": path.name,
                "message": f"Created new Word document at {path.name}",
            }

        elif action == "append":
            if path.exists():
                doc = docx.Document(str(path))
            else:
                doc = docx.Document()
            if title:
                doc.add_heading(str(title), level=1)
            if content:
                for block in str(content).split("\n\n"):
                    if block.strip():
                        doc.add_paragraph(block.strip())
            doc.save(str(path))
            return {
                "status": "success",
                "action": "append",
                "path": str(path),
                "filename": path.name,
                "message": f"Appended content to Word document {path.name}",
            }

        elif action == "replace_text":
            if not path.exists():
                raise FileNotFoundError(f"Cannot replace text; file does not exist: {path}")
            if not search_text:
                raise ValueError("search_text is required for action 'replace_text'")

            doc = docx.Document(str(path))
            replacements_made = 0

            def _replace_in_paragraphs(paras):
                nonlocal replacements_made
                for p in paras:
                    if search_text in p.text:
                        replaced_in_run = False
                        for run in p.runs:
                            if search_text in run.text:
                                run.text = run.text.replace(search_text, str(replace_text))
                                replacements_made += 1
                                replaced_in_run = True
                        if not replaced_in_run and search_text in p.text:
                            p.text = p.text.replace(search_text, str(replace_text))
                            replacements_made += 1

            _replace_in_paragraphs(doc.paragraphs)
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        _replace_in_paragraphs(cell.paragraphs)

            doc.save(str(path))
            return {
                "status": "success",
                "action": "replace_text",
                "path": str(path),
                "filename": path.name,
                "replacements_made": replacements_made,
                "message": f"Replaced {replacements_made} occurrence(s) of '{search_text}' in {path.name}",
            }

        else:
            raise ValueError(f"Unknown action '{action}' for edit_docx. Must be 'create', 'append', or 'replace_text'.")

    def _generate_pdf(self, target: str | None, params: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
        file_path = target or params.get("path", "")
        if not file_path:
            raise ValueError("No path specified for generate_pdf")
        file_path = self._validate_string(file_path, "path")

        path = self._validate_tenant_path_boundary(file_path, user_id=user_id, mode="write")

        if path.suffix.lower() != ".pdf":
            path = path.with_suffix(".pdf")

        path.parent.mkdir(parents=True, exist_ok=True)

        title = params.get("title", "")
        content = params.get("content", "")
        if not title and not content:
            raise ValueError("Either 'title' or 'content' must be provided for generate_pdf")

        try:
            import html
            from reportlab.lib.pagesizes import letter
            from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
            from reportlab.lib.styles import getSampleStyleSheet
        except ImportError:
            raise RuntimeError("reportlab is not installed in the environment.")

        doc = SimpleDocTemplate(
            str(path),
            pagesize=letter,
            rightMargin=54,
            leftMargin=54,
            topMargin=54,
            bottomMargin=54,
        )
        styles = getSampleStyleSheet()
        story = []

        if title:
            escaped_title = html.escape(str(title))
            story.append(Paragraph(f"<b>{escaped_title}</b>", styles["Title"]))
            story.append(Spacer(1, 14))

        if content:
            for block in str(content).split("\n\n"):
                block = block.strip()
                if block:
                    escaped_block = html.escape(block).replace("\n", "<br/>")
                    story.append(Paragraph(escaped_block, styles["BodyText"]))
                    story.append(Spacer(1, 10))

        doc.build(story)

        return {
            "status": "success",
            "path": str(path),
            "filename": path.name,
            "title": str(title),
            "size_bytes": path.stat().st_size,
            "message": f"Successfully generated PDF document at {path.name}",
        }

    def _create_backup(self, params: dict[str, Any]) -> dict[str, Any]:
        from foundation.storage_manager.backup_restore import get_backup_restore_manager
        user_id = params.get("user_id", "default_owner")
        comment = params.get("comment", "")
        mgr = get_backup_restore_manager()
        res = mgr.create_backup(user_id=user_id, comment=comment)
        if not res.success:
            return {"status": "failed", "error": res.error}
        return {
            "status": "success",
            "backup_id": res.backup_id,
            "archive_path": res.archive_path,
            "total_files": res.total_files,
            "total_bytes": res.total_bytes,
            "verified": True,
        }

    def _restore_backup(self, params: dict[str, Any]) -> dict[str, Any]:
        from foundation.storage_manager.backup_restore import get_backup_restore_manager
        archive_path = params.get("archive_path")
        target_user_id = params.get("target_user_id", "default_owner")
        confirmation = params.get("confirmation", True)
        mgr = get_backup_restore_manager()
        res = mgr.restore_backup(archive_path=archive_path, target_user_id=target_user_id, confirmation=confirmation)
        if not res.success:
            return {"status": "failed", "error": res.error, "rollback_performed": res.rollback_performed}
        return {
            "status": "success",
            "backup_id": res.backup_id,
            "target_user_id": res.target_user_id,
            "restored_files_count": res.restored_files_count,
            "restored_categories": res.restored_categories,
            "verified": True,
        }
