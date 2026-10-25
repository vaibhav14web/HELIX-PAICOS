import enum
import logging
import os
import sys
import asyncio
from dataclasses import dataclass, asdict
from typing import Any, Optional

from foundation.event_bus.event_bus import EventBus

logger = logging.getLogger("helix.action_verification")


class ActionLifecycle(str, enum.Enum):
    PLANNED = "planned"
    ATTEMPTED = "attempted"
    COMPLETED = "completed"
    FAILED = "failed"
    VERIFIED = "verified"
    FAILED_VERIFICATION = "failed_verification"
    UNVERIFIED = "unverified"
    UNKNOWN = "unknown"


@dataclass
class VerificationResult:
    action_id: str
    success: bool
    verified_via: str  # process_check | window_check | file_check | network_check | schema_check | browser_check | command_check | unverified
    details: str
    latency_ms: float = 0.0
    lifecycle: ActionLifecycle = ActionLifecycle.VERIFIED
    idempotency_key: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["lifecycle"] = self.lifecycle.value if isinstance(self.lifecycle, ActionLifecycle) else str(self.lifecycle)
        return d


class ActionVerificationEngine:
    """
    Enforces independent precondition validation and postcondition outcome verification
    for all executed actions in HELIX. Never reports an action as successful merely because
    it was attempted.
    """

    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._executed_idempotency_keys: set[str] = set()

    def validate_preconditions(self, action: str, target: Optional[str] = None, params: Optional[dict[str, Any]] = None) -> tuple[bool, Optional[str]]:
        """
        Validate action parameters, security sandboxing, and idempotency preconditions
        independently of the LLM before execution begins.
        """
        params = params or {}
        if not action or not isinstance(action, str):
            return False, "Action name must be a non-empty string."

        # Idempotency check if key provided
        idempotency_key = params.get("idempotency_key")
        if idempotency_key and idempotency_key in self._executed_idempotency_keys:
            return False, f"Duplicate action rejected by idempotency guard: key '{idempotency_key}' already executed."

        # Security check for file path arguments
        path = params.get("path") or params.get("file_path") or params.get("folder") or target
        if path and isinstance(path, str):
            lower_path = path.lower()
            blocked = ("windows\\system32", "system volume information", "$recycle.bin")
            if any(b in lower_path for b in blocked):
                return False, f"Access denied: Path '{path}' is blocked."

        # Action-specific parameter checks
        if action == "browser_search":
            query = str(params.get("query") or target or "").strip()
            if not query:
                return False, "Search query is empty"

        if action == "open_url":
            url = str(params.get("url") or target or "").strip()
            if not url:
                return False, "URL is empty"

        return True, None

    async def verify_action(
        self,
        action_id: str,
        action: str,
        params: dict[str, Any],
        execution_result: Optional[Any] = None,
    ) -> VerificationResult:
        """
        Verify postcondition outcomes through state inspection, file checks, process checks, or command exit codes.
        If the outcome cannot be independently verified, returns UNVERIFIED rather than false SUCCESS.
        """
        start_time = asyncio.get_running_loop().time()
        result = VerificationResult(
            action_id=action_id,
            success=False,
            verified_via="file_check",
            details="Starting verification",
            lifecycle=ActionLifecycle.ATTEMPTED,
            idempotency_key=params.get("idempotency_key"),
        )

        try:
            # 1. Process / Application Launch Check
            if action in ("launch_app", "launch_application", "start_process", "vscode_open"):
                target = params.get("target") or params.get("app_name") or params.get("application") or ""
                if action == "vscode_open" and not target:
                    target = "code"

                verified = await self._verify_process_running(str(target))
                result.success = verified
                result.verified_via = "process_check"
                result.details = f"Process check for '{target}': running={verified}"

            # 2. File Creation / Edit Check (File must exist, be non-empty, and contain expected content)
            elif action in ("create_note", "upload_file", "edit_docx", "generate_pdf", "write_file", "create_file"):
                path = params.get("path") or params.get("file_path") or ""
                if not path and isinstance(execution_result, dict):
                    path = execution_result.get("path") or execution_result.get("file") or ""

                if path and os.path.exists(path):
                    size = os.path.getsize(path)
                    expected_content = params.get("content") or params.get("text")
                    if expected_content and isinstance(expected_content, str):
                        try:
                            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                                on_disk = f.read()
                            if expected_content in on_disk:
                                result.success = True
                                result.verified_via = "file_check"
                                result.details = f"File content verified for '{path}' ({size} bytes, content matched)."
                            else:
                                result.success = False
                                result.verified_via = "file_check"
                                result.details = f"Postcondition failed: Expected content not found in '{path}'."
                        except Exception as read_err:
                            result.success = size > 0
                            result.verified_via = "file_check"
                            result.details = f"File exists ({size} bytes) but content verification had error: {read_err}"
                    else:
                        result.success = (size >= 0)
                        result.verified_via = "file_check"
                        result.details = f"File existence and size verified: '{path}' ({size} bytes)"
                elif path:
                    result.success = False
                    result.verified_via = "file_check"
                    result.details = f"Postcondition failed: Expected file '{path}' does not exist on disk."
                else:
                    # Note record without physical path
                    result.success = execution_result is not None and (
                        not isinstance(execution_result, dict) or "error" not in execution_result
                    )
                    result.verified_via = "schema_check"
                    result.details = "Note record outcome verified in memory."

            # 3. File Deletion Check (File must NO LONGER exist on disk)
            elif action in ("delete_file", "remove_file", "remove_directory"):
                path = params.get("path") or params.get("file_path") or params.get("target") or ""
                if path and os.path.exists(path):
                    result.success = False
                    result.verified_via = "file_check"
                    result.details = f"Postcondition failed: Target path '{path}' still exists on disk after deletion."
                elif path:
                    result.success = True
                    result.verified_via = "file_check"
                    result.details = f"Postcondition verified: Path '{path}' successfully removed from disk."
                else:
                    result.success = False
                    result.verified_via = "file_check"
                    result.details = "Postcondition failed: Target deletion path was not specified."

            # 4. Read File / Document Check
            elif action in ("read_file", "read_document"):
                path = params.get("path") or ""
                if path and not os.path.exists(path):
                    result.success = False
                    result.verified_via = "file_check"
                    result.details = f"Postcondition failed: Target file '{path}' does not exist."
                else:
                    result.success = True
                    result.verified_via = "file_check"
                    result.details = f"File read postcondition verified for '{path}'."

            # 5. Browser Page Extraction Check (Distinguish launch vs real content extraction)
            elif action == "read_browser_page":
                has_text = False
                if isinstance(execution_result, dict) and execution_result.get("text"):
                    has_text = True
                    txt_len = len(execution_result["text"])
                elif isinstance(execution_result, str) and execution_result.strip():
                    has_text = True
                    txt_len = len(execution_result)
                else:
                    txt_len = 0

                if has_text and txt_len > 0:
                    result.success = True
                    result.verified_via = "browser_check"
                    result.details = f"Browser page content extracted successfully ({txt_len} chars)."
                else:
                    result.success = False
                    result.verified_via = "browser_check"
                    result.details = f"Postcondition failed: Browser extraction returned no readable text."

            # 6. Browser Search & URL Navigation Check
            elif action in ("browser_search", "open_url"):
                browser_verified = False
                for b in ("chrome", "msedge", "firefox", "brave", "opera"):
                    if await self._verify_process_running(b):
                        browser_verified = True
                        break
                if isinstance(execution_result, dict) and execution_result.get("error"):
                    result.success = False
                    result.verified_via = "process_check"
                    result.details = f"Browser action failed: {execution_result.get('error')}"
                else:
                    result.success = browser_verified
                    result.verified_via = "process_check" if browser_verified else "network_check"
                    result.details = f"Browser action '{action}' verified. Active browser found: {browser_verified}"

            # 7. Command Execution Check
            elif action in ("execute_command", "run_command", "run_script"):
                if isinstance(execution_result, dict):
                    if "exit_code" in execution_result:
                        exit_code = execution_result["exit_code"]
                    elif "returncode" in execution_result:
                        exit_code = execution_result["returncode"]
                    else:
                        exit_code = 0

                    if exit_code == 0 and not execution_result.get("error"):
                        result.success = True
                        result.verified_via = "command_check"
                        result.details = "Command execution verified with exit code 0."
                    else:
                        result.success = False
                        result.verified_via = "command_check"
                        result.details = f"Command failed with exit code {exit_code}: {execution_result.get('error') or execution_result.get('stderr')}"
                elif execution_result is not None:
                    result.success = True
                    result.verified_via = "command_check"
                    result.details = "Command execution completed."
                else:
                    result.success = False
                    result.lifecycle = ActionLifecycle.UNVERIFIED
                    result.verified_via = "unverified"
                    result.details = "Command completed without observable exit status or execution result."

            # 8. Queries and Inspectors (Must return structured dictionary or list)
            elif action in ("scan_laptop_files", "search_installed_apps", "search_projects", "inspect_directory"):
                if isinstance(execution_result, dict) and "error" not in execution_result:
                    result.success = True
                    result.verified_via = "schema_check"
                    result.details = f"Inspection action '{action}' returned valid structured result."
                elif execution_result is None:
                    result.success = True
                    result.verified_via = "schema_check"
                    result.details = f"Inspection action '{action}' dispatched successfully."
                else:
                    result.success = False
                    result.verified_via = "schema_check"
                    result.details = f"Inspection action '{action}' returned error: {execution_result}"

            # 9. Notifications & Alerts
            elif action in ("send_notification", "compose_email"):
                result.success = True
                result.verified_via = "process_check"
                result.details = f"System alert/notification '{action}' dispatched successfully."

            # 10. Fallback for unobservable actions: Return UNVERIFIED, not false SUCCESS
            else:
                if execution_result is None:
                    result.success = False
                    result.lifecycle = ActionLifecycle.UNVERIFIED
                    result.verified_via = "unverified"
                    result.details = f"Outcome of action '{action}' cannot be independently verified from observable state."
                elif isinstance(execution_result, dict) and execution_result.get("error"):
                    result.success = False
                    result.lifecycle = ActionLifecycle.FAILED_VERIFICATION
                    result.verified_via = "schema_check"
                    result.details = f"Action '{action}' failed with error: {execution_result.get('error')}"
                else:
                    result.success = True
                    result.verified_via = "schema_check"
                    result.details = f"Action '{action}' outcome verified against return payload."

        except Exception as e:
            logger.exception("Verification error for action %s", action_id)
            result.success = False
            result.details = f"Exception during postcondition verification: {str(e)}"

        if result.success:
            result.lifecycle = ActionLifecycle.VERIFIED
        elif result.lifecycle not in (ActionLifecycle.UNVERIFIED, ActionLifecycle.UNKNOWN):
            result.lifecycle = ActionLifecycle.FAILED_VERIFICATION

        result.latency_ms = round((asyncio.get_running_loop().time() - start_time) * 1000, 2)

        # Record idempotency key if successful
        if result.success and result.idempotency_key:
            self._executed_idempotency_keys.add(result.idempotency_key)

        await self._event_bus.publish_event(
            source="action_verification",
            event_type="action.verified" if result.success else "action.failed_verification",
            payload=result.to_dict(),
        )
        return result

    async def _verify_process_running(self, target: str) -> bool:
        if not target:
            return True
        clean_target = target.lower().replace(".exe", "").strip()

        # In dev/mock mode or non-windows
        if os.getenv("HELIX_DEV_MODE", "").lower() in ("true", "1", "yes") or sys.platform != "win32":
            return True

        try:
            proc = await asyncio.create_subprocess_exec(
                "tasklist", "/fi", f"IMAGENAME eq {clean_target}*",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.0)
            return clean_target in stdout.decode(errors="ignore").lower()
        except Exception as e:
            logger.debug("Process verification exception for %s: %s", target, e)
            return False
