from __future__ import annotations

import os
import sys
import time
import uuid
import asyncio
import logging
import urllib.parse
from typing import Any

from foundation.event_bus.event_bus import EventBus
from core_ai.tool_calling.models import ToolCall, ToolResult, ToolDefinition
from core_ai.tool_calling.tool_registry import ToolRegistry

logger = logging.getLogger("helix.tool_executor")

_BLOCKED_PATH_TOKENS = {"systemroot", "programdata", "program files"}
_BLOCKED_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".psm1", ".psd1",
    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".scr", ".pif",
    ".gadget", ".cpl", ".scf", ".lnk", ".inf", ".reg"
}
_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


class ToolExecutor:
    """Validates, security-checks, and executes tool calls within HELIX."""

    def __init__(self, event_bus: EventBus, registry: ToolRegistry | None = None, goal_manager: Any = None):
        self._event_bus = event_bus
        self._registry = registry or ToolRegistry()
        self._goal_manager = goal_manager
        self._pending_tool_futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._pending_permission_futures: dict[str, asyncio.Future[str]] = {}
        self._pending_permission_details: dict[str, dict[str, Any]] = {}
        self._subscriptions: list[str] = []

    def set_goal_manager(self, goal_manager: Any) -> None:
        self._goal_manager = goal_manager

    async def start(self) -> None:
        self._event_bus.subscribe("automation.result", self._handle_automation_result)
        self._event_bus.subscribe("automation.failed", self._handle_automation_failed)
        self._event_bus.subscribe("permission.granted", self._handle_permission_granted)
        self._event_bus.subscribe("permission.denied", self._handle_permission_denied)
        self._event_bus.subscribe("permission.expired", self._handle_permission_denied)
        self._event_bus.subscribe("permission.cancelled", self._handle_permission_denied)
        self._event_bus.subscribe("permission.timed_out", self._handle_permission_denied)
        self._subscriptions = [
            "automation.result",
            "automation.failed",
            "permission.granted",
            "permission.denied",
            "permission.expired",
            "permission.cancelled",
            "permission.timed_out",
        ]

    async def stop(self) -> None:
        for ev in self._subscriptions:
            if ev == "automation.result":
                self._event_bus.unsubscribe(ev, self._handle_automation_result)
            elif ev == "automation.failed":
                self._event_bus.unsubscribe(ev, self._handle_automation_failed)
            elif ev == "permission.granted":
                self._event_bus.unsubscribe(ev, self._handle_permission_granted)
            elif ev in ("permission.denied", "permission.expired", "permission.cancelled", "permission.timed_out"):
                self._event_bus.unsubscribe(ev, self._handle_permission_denied)
        self._subscriptions.clear()
        for fut in self._pending_tool_futures.values():
            if not fut.done():
                fut.cancel()
        self._pending_tool_futures.clear()
        for fut in self._pending_permission_futures.values():
            if not fut.done():
                fut.cancel()
        self._pending_permission_futures.clear()
        self._pending_permission_details.clear()

    async def execute(self, tool_call: ToolCall, session_id: str = "default", user_id: str = "default_owner") -> ToolResult:
        """Execute a validated ToolCall and return a structured ToolResult."""
        start_time = time.time()

        _ALIASES = {
            "read_docx": "read_document",
            "read_pdf": "read_document",
            "create_docx": "edit_docx",
            "write_docx": "edit_docx",
            "create_pdf": "generate_pdf",
            "make_pdf": "generate_pdf",
            "edit_document": "edit_docx",
            "search_apps": "search_installed_apps",
            "scan_files": "scan_laptop_files",
            "find_files": "scan_laptop_files",
            "explore_directory": "inspect_directory",
            "scan_directory": "inspect_directory",
            "understand_directory": "inspect_directory",
            "analyze_directory": "inspect_directory",
            "inspect_folder": "inspect_directory",
        }
        if tool_call.name in _ALIASES:
            tool_call.name = _ALIASES[tool_call.name]

        # Handle hallucinated tool calls from local models (e.g. calculator_open, open_calculator, chrome_open)
        clean_tool = tool_call.name.lower().strip()
        if clean_tool.endswith("_open") or clean_tool.startswith("open_"):
            app_target = clean_tool.replace("_open", "").replace("open_", "").strip()
            tool_call.name = "launch_application"
            if "application" not in tool_call.arguments:
                tool_call.arguments["application"] = app_target
        elif clean_tool in ("calculator", "calc", "notepad", "chrome", "paint", "mspaint", "terminal", "wt", "explorer"):
            tool_call.name = "launch_application"
            if "application" not in tool_call.arguments:
                tool_call.arguments["application"] = clean_tool

        tool_def = self._registry.get(tool_call.name)

        if not tool_def:
            import difflib
            registered_names = [t.name for t in self._registry.list_all()]
            closest = difflib.get_close_matches(tool_call.name, registered_names, n=3, cutoff=0.3)
            suggestion_txt = f" Did you mean: {', '.join(closest)}?" if closest else f" Registered tools are: {', '.join(registered_names[:6])}."
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=False,
                error=f"Unknown tool '{tool_call.name}'. Never execute an invented tool name.{suggestion_txt}",
                duration_ms=(time.time() - start_time) * 1000,
            )

        # 1. Parameter Validation
        val_error = self._validate_parameters(tool_def, tool_call.arguments)
        if val_error:
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=False,
                error=val_error,
                duration_ms=(time.time() - start_time) * 1000,
            )

        # 2. Safety and Security Sandboxing Checks
        safety_error = self._validate_safety(tool_call.name, tool_call.arguments)
        if safety_error:
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=False,
                error=safety_error,
                duration_ms=(time.time() - start_time) * 1000,
            )

        # 2.5 Permission Gating for Laptop Resources
        if tool_def.requires_permission:
            perm_id = f"perm_{uuid.uuid4().hex}"
            loop = asyncio.get_running_loop()
            perm_future: asyncio.Future[str] = loop.create_future()
            self._pending_permission_futures[perm_id] = perm_future
            self._pending_permission_details[perm_id] = {
                "id": perm_id,
                "tool_name": tool_call.name,
                "scope": tool_call.name,
                "target": tool_call.arguments.get("target") or tool_call.arguments.get("path") or tool_call.arguments.get("application"),
                "arguments": dict(tool_call.arguments),
                "created_at": time.time(),
                "ttl_seconds": 60.0,
            }

            reasoning, benefits, risks = self._build_permission_context(tool_call)

            await self._event_bus.publish_event(
                source="tool_executor",
                event_type="permission.request",
                payload={
                    "id": perm_id,
                    "request_id": perm_id,
                    "action": tool_call.name,
                    "scope": tool_call.name,
                    "target": tool_call.arguments.get("target") or tool_call.arguments.get("path") or tool_call.arguments.get("application"),
                    "params": dict(tool_call.arguments),
                    "ttl_seconds": 60,
                    "reasoning": reasoning,
                    "benefits": benefits,
                    "risks": risks,
                    "source_module": "laptop_guard",
                    "resources": [f"{k}={v}" for k, v in tool_call.arguments.items()],
                },
                correlation_id=tool_call.call_id,
            )

            try:
                decision = await asyncio.wait_for(perm_future, timeout=60.0)
                if decision != "approved":
                    reason = {
                        "denied": "Permission denied by user.",
                        "expired": "Permission request expired before approval.",
                        "cancelled": "Permission request was cancelled.",
                        "timed_out": "Permission request timed out waiting for approval in the HELIX UI.",
                    }.get(decision, "Permission was not granted.")
                    return ToolResult(
                        call_id=tool_call.call_id,
                        name=tool_call.name,
                        success=False,
                        error=f"{reason} Access to laptop action '{tool_call.name}' was not authorized.",
                        duration_ms=(time.time() - start_time) * 1000,
                    )
            except asyncio.TimeoutError:
                await self._event_bus.publish_event(
                    source="tool_executor",
                    event_type="permission.timeout",
                    payload={"id": perm_id, "reason": "Tool approval wait timed out."},
                    correlation_id=tool_call.call_id,
                )
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error=f"Permission request timed out waiting for your approval in the HELIX UI.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            except asyncio.CancelledError:
                await self._event_bus.publish_event(
                    source="tool_executor",
                    event_type="permission.cancel",
                    payload={"id": perm_id, "reason": "Tool request cancelled."},
                    correlation_id=tool_call.call_id,
                )
                raise
            finally:
                self._pending_permission_futures.pop(perm_id, None)
                self._pending_permission_details.pop(perm_id, None)

            # Re-validate parameters and safety immediately before execution to prevent parameter tampering
            val_error_post = self._validate_parameters(tool_def, tool_call.arguments)
            if val_error_post:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error=val_error_post,
                    duration_ms=(time.time() - start_time) * 1000,
                )
            safety_error_post = self._validate_safety(tool_call.name, tool_call.arguments)
            if safety_error_post:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error=safety_error_post,
                    duration_ms=(time.time() - start_time) * 1000,
                )

        # 3. Check for Custom In-Engine Tools
        if tool_call.name == "list_open_applications":
            desktop_res = self._list_open_applications()
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=desktop_res.get("status") != "error",
                result=desktop_res,
                error=desktop_res.get("error"),
                duration_ms=(time.time() - start_time) * 1000,
            )

        if tool_call.name == "get_system_state":
            state = self._get_system_state()
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=True,
                result=state,
                duration_ms=(time.time() - start_time) * 1000,
            )

        if tool_call.name == "create_goal":
            title = str(tool_call.arguments.get("title") or "").strip()
            if not title:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error="Goal title cannot be empty.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            if self._goal_manager is None:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error="Goal persistence failed: GoalManager is not initialized or unavailable.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            try:
                desc = str(tool_call.arguments.get("description") or "").strip()
                priority = str(tool_call.arguments.get("priority") or "medium").strip()
                tasks_arg = tool_call.arguments.get("tasks") or tool_call.arguments.get("subgoals")
                tasks = []
                if isinstance(tasks_arg, list):
                    for idx, t in enumerate(tasks_arg):
                        if isinstance(t, str) and t.strip():
                            tasks.append({"task_id": f"t-{idx+1}", "title": t.strip(), "status": "pending"})
                        elif isinstance(t, dict) and t.get("title"):
                            tasks.append(t)
                goal, created = self._goal_manager.create_or_update_goal_from_intent(
                    intent=title,
                    title=title,
                    description=desc,
                    priority=priority,
                    user_id=user_id,
                    tasks=tasks,
                )
                persisted_goal = self._goal_manager.get_goal(goal.goal_id, user_id=user_id)
                if not persisted_goal:
                    return ToolResult(
                        call_id=tool_call.call_id,
                        name=tool_call.name,
                        success=False,
                        error="Goal persistence verification failed: record not found after write.",
                        duration_ms=(time.time() - start_time) * 1000,
                    )
                goal_dict = persisted_goal.to_dict()
                goal_dict["created"] = created
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=True,
                    result=goal_dict,
                    verified=True,
                    lifecycle="verified",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            except Exception as e:
                logger.exception("Failed to execute create_goal tool: %s", e)
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error=f"Goal persistence error: {str(e)}",
                    duration_ms=(time.time() - start_time) * 1000,
                )

        if tool_call.name == "add_subgoal":
            goal_title = str(tool_call.arguments.get("goal_title") or tool_call.arguments.get("goal_id") or "").strip()
            subgoal_title = str(tool_call.arguments.get("subgoal_title") or tool_call.arguments.get("title") or "").strip()
            subgoal_desc = str(tool_call.arguments.get("description") or "").strip()
            if not goal_title or not subgoal_title:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error="Both goal_title and subgoal_title are required.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            if self._goal_manager is None:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error="Goal persistence failed: GoalManager is not initialized or unavailable.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            try:
                success, msg, goal = self._goal_manager.add_subtask(
                    goal_id_or_title=goal_title,
                    title=subgoal_title,
                    description=subgoal_desc,
                    user_id=user_id,
                )
                if not success:
                    return ToolResult(
                        call_id=tool_call.call_id,
                        name=tool_call.name,
                        success=False,
                        error=msg,
                        duration_ms=(time.time() - start_time) * 1000,
                    )
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=True,
                    result={"message": msg, "goal": goal.to_dict() if goal else {}},
                    verified=True,
                    lifecycle="verified",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            except Exception as e:
                logger.exception("Failed to execute add_subgoal tool: %s", e)
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error=f"Subgoal addition error: {str(e)}",
                    duration_ms=(time.time() - start_time) * 1000,
                )

        if tool_call.name == "list_goals":
            if self._goal_manager is None:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error="GoalManager is not initialized or unavailable.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            include_archived = bool(tool_call.arguments.get("include_archived", False))
            archived_only = bool(tool_call.arguments.get("archived_only", False))
            goals = self._goal_manager.list_goals(
                user_id=user_id,
                include_archived=include_archived,
                archived_only=archived_only,
            )
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=True,
                result={"goals": goals, "count": len(goals)},
                duration_ms=(time.time() - start_time) * 1000,
            )

        if tool_call.name == "archive_goal":
            goal_id = str(tool_call.arguments.get("goal_id") or "").strip()
            if not goal_id:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error="Missing goal_id parameter.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            if self._goal_manager is None:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error="GoalManager is not initialized or unavailable.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            success, msg = self._goal_manager.archive_goal(goal_id, user_id=user_id)
            if not success:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error=msg,
                    duration_ms=(time.time() - start_time) * 1000,
                )
            goal = self._goal_manager.get_goal(goal_id, user_id=user_id)
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=True,
                result={"goal_id": goal_id, "title": goal.title if goal else "", "status": "archived"},
                verified=True,
                duration_ms=(time.time() - start_time) * 1000,
            )

        if tool_call.name == "restore_goal":
            goal_id = str(tool_call.arguments.get("goal_id") or "").strip()
            if not goal_id:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error="Missing goal_id parameter.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            if self._goal_manager is None:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error="GoalManager is not initialized or unavailable.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            success, msg = self._goal_manager.restore_goal(goal_id, user_id=user_id)
            if not success:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error=msg,
                    duration_ms=(time.time() - start_time) * 1000,
                )
            goal = self._goal_manager.get_goal(goal_id, user_id=user_id)
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=True,
                result={"goal_id": goal_id, "title": goal.title if goal else "", "status": "ready"},
                verified=True,
                duration_ms=(time.time() - start_time) * 1000,
            )

        if tool_call.name == "delete_goal":
            goal_id = str(tool_call.arguments.get("goal_id") or "").strip()
            confirmed = bool(tool_call.arguments.get("confirmed", False))
            if not goal_id:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error="Missing goal_id parameter.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            if not confirmed:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error="Confirmation required: permanent deletion must be explicitly confirmed.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            if self._goal_manager is None:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error="GoalManager is not initialized or unavailable.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            goal = self._goal_manager.get_goal(goal_id, user_id=user_id)
            if not goal:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error=f"Goal '{goal_id}' not found or unauthorized.",
                    duration_ms=(time.time() - start_time) * 1000,
                )
            title = goal.title
            success, msg = self._goal_manager.delete_goal(goal_id, user_id=user_id)
            if not success:
                return ToolResult(
                    call_id=tool_call.call_id,
                    name=tool_call.name,
                    success=False,
                    error=msg,
                    duration_ms=(time.time() - start_time) * 1000,
                )
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=True,
                result={"goal_id": goal_id, "title": title, "status": "deleted"},
                verified=True,
                duration_ms=(time.time() - start_time) * 1000,
            )

        # 4. Dispatch via EventBus to ActionExecutor
        subscribers = getattr(self._event_bus, "_subscribers", {}).get("action.execute", [])
        if not subscribers:
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=False,
                error="Action was not executed because no action executor is available.",
                duration_ms=(time.time() - start_time) * 1000,
            )

        action_id = f"act_{uuid.uuid4().hex}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_tool_futures[action_id] = future

        await self._event_bus.publish_event(
            source="planner_engine",  # trusted source for ActionExecutor
            event_type="action.execute",
            payload={
                "action": tool_call.name,
                "action_id": action_id,
                "target": tool_call.arguments.get("target") or tool_call.arguments.get("path") or tool_call.arguments.get("application"),
                "params": tool_call.arguments,
            },
            correlation_id=tool_call.call_id,
        )

        try:
            # 15 second timeout for tool execution
            res = await asyncio.wait_for(future, timeout=15.0)
            success = res.get("status") != "failed" and "error" not in res
            verified = res.get("verified", False) or (isinstance(res.get("result"), dict) and res.get("result", {}).get("verified", False))
            lifecycle = res.get("lifecycle", "verified" if success and verified else ("completed" if success else "failed"))
            details = res.get("verification_details") or (res.get("result", {}).get("verification_details") if isinstance(res.get("result"), dict) else None)
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=success,
                result=res.get("result", res),
                error=res.get("error"),
                duration_ms=(time.time() - start_time) * 1000,
                verified=verified,
                lifecycle=lifecycle,
                verification_details=details,
            )
        except asyncio.TimeoutError:
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=False,
                error=f"Tool execution for '{tool_call.name}' timed out after 15 seconds.",
                duration_ms=(time.time() - start_time) * 1000,
            )
        except Exception as e:
            return ToolResult(
                call_id=tool_call.call_id,
                name=tool_call.name,
                success=False,
                error=str(e),
                duration_ms=(time.time() - start_time) * 1000,
            )
        finally:
            self._pending_tool_futures.pop(action_id, None)

    async def _handle_automation_result(self, event: Any) -> None:
        action_id = event.payload.get("action_id")
        if action_id in self._pending_tool_futures:
            fut = self._pending_tool_futures[action_id]
            if not fut.done():
                fut.set_result(event.payload.get("result", event.payload))

    async def _handle_automation_failed(self, event: Any) -> None:
        action_id = event.payload.get("action_id")
        if action_id in self._pending_tool_futures:
            fut = self._pending_tool_futures[action_id]
            if not fut.done():
                fut.set_result({"status": "failed", "error": event.payload.get("error", "Action failed")})

    async def _handle_permission_granted(self, event: Any) -> None:
        req_id = event.payload.get("id") or event.payload.get("request_id")
        if not req_id or req_id not in self._pending_permission_futures:
            logger.warning("permission.granted rejected: unknown, unmatched, or stale request_id '%s'", req_id)
            return

        perm_info = self._pending_permission_details.get(req_id, {})
        # Only the PermissionManager's decision event is authoritative, and it
        # must describe the same action and validated parameters requested here.
        if event.source != "permission_manager":
            logger.warning("permission.granted rejected from untrusted source '%s'", event.source)
            return
        from foundation.permission_manager.permission_manager import compute_params_hash
        if (event.payload.get("action") != perm_info.get("tool_name")
                or event.payload.get("scope") != perm_info.get("scope")
                or event.payload.get("target") != perm_info.get("target")
                or event.payload.get("params_hash") != compute_params_hash(perm_info.get("arguments"))):
            logger.warning("permission.granted rejected: request details mismatch for '%s'", req_id)
            fut = self._pending_permission_futures.get(req_id)
            if fut and not fut.done():
                fut.set_result("denied")
            return
        created_at = perm_info.get("created_at", 0)
        ttl = perm_info.get("ttl_seconds", 60.0)
        if time.time() - created_at > ttl:
            logger.warning("permission.granted rejected: request '%s' expired while waiting for approval", req_id)
            fut = self._pending_permission_futures.pop(req_id, None)
            self._pending_permission_details.pop(req_id, None)
            if fut and not fut.done():
                fut.set_result("expired")
            return

        fut = self._pending_permission_futures.get(req_id)
        if fut and not fut.done():
                fut.set_result("approved")

    async def _handle_permission_denied(self, event: Any) -> None:
        req_id = event.payload.get("id") or event.payload.get("request_id")
        if not req_id or req_id not in self._pending_permission_futures:
            logger.warning("permission.denied ignored: unknown or unmatched request_id '%s'", req_id)
            return
        fut = self._pending_permission_futures.get(req_id)
        if fut and not fut.done():
            state = {
                "permission.expired": "expired",
                "permission.cancelled": "cancelled",
                "permission.timed_out": "timed_out",
            }.get(event.event_type, "denied")
            fut.set_result(state)

    def _build_permission_context(self, tool_call: ToolCall) -> tuple[str, list[str], list[str]]:
        name = tool_call.name
        args = tool_call.arguments
        if name == "scan_laptop_files":
            folder = args.get("folder", "documents")
            q = args.get("query", "")
            reasoning = f"HELIX requests permission to scan and locate files in your '{folder}' directory"
            if q:
                reasoning += f" matching keyword '{q}'."
            else:
                reasoning += "."
            benefits = [
                f"Enables HELIX to find relevant files in '{folder}'",
                "Provides accurate answers based on your laptop files",
            ]
            risks = ["Allows HELIX to inspect file names and metadata on your laptop"]
        elif name == "read_file":
            path = args.get("path", "")
            reasoning = f"HELIX requests permission to read content from file '{path}' on your laptop."
            benefits = [
                "Allows HELIX to analyze and summarize the requested file content",
                "Assists you with your documents directly",
            ]
            risks = [f"Reads file content from '{path}' into HELIX context"]
        elif name == "search_installed_apps":
            q = args.get("query", "")
            reasoning = f"HELIX requests permission to search your laptop's installed applications and Start Menu programs for '{q}'."
            benefits = [
                "Locates software installed on your machine",
                "Helps launch or interact with apps",
            ]
            risks = ["Inspects installed program registry and Start Menu shortcuts"]
        elif name == "launch_application":
            app = args.get("application", "")
            reasoning = f"HELIX requests permission to launch application '{app}' on your Windows desktop."
            benefits = [
                f"Opens '{app}' on your screen as requested",
            ]
            risks = [f"Starts process '{app}' on your operating system"]
        elif name == "search_projects":
            q = args.get("query", "")
            reasoning = f"HELIX requests permission to search your local codebase projects and git repositories for '{q}'."
            benefits = [
                "Finds development codebases on your laptop",
                "Enables project context awareness",
            ]
            risks = ["Reads repository directories on your laptop"]
        elif name == "vscode_open":
            path = args.get("path", "")
            reasoning = f"HELIX requests permission to open '{path}' in Visual Studio Code."
            benefits = ["Opens project or file in your VS Code workspace"]
            risks = [f"Launches VS Code targeting '{path}'"]
        elif name == "read_document":
            path = args.get("path", "")
            reasoning = f"HELIX requests permission to read and extract content from document '{path}'."
            benefits = [
                "Extracts readable text and tables from Word (.docx) or PDF (.pdf) documents",
                "Allows the assistant to analyze and summarize your document",
            ]
            risks = [f"Reads document data from '{path}' into HELIX context"]
        elif name == "edit_docx":
            path = args.get("path", "")
            action = args.get("action", "modify")
            reasoning = f"HELIX requests permission to {action} Word document '{path}' on your laptop."
            benefits = [
                f"Modifies or creates Microsoft Word document '{path}' per your instructions",
                "Preserves document styling and layout",
            ]
            risks = [f"Writes or modifies file on your disk: '{path}'"]
        elif name == "generate_pdf":
            path = args.get("path", "")
            title = args.get("title", "Document")
            reasoning = f"HELIX requests permission to generate PDF document '{title}' at '{path}'."
            benefits = [
                f"Compiles your text or report into a clean, formatted PDF document at '{path}'",
            ]
            risks = [f"Creates new PDF file on your laptop: '{path}'"]
        elif name == "inspect_directory":
            path = args.get("path", "")
            reasoning = f"HELIX requests permission to inspect and analyze directory '{path}' on your laptop."
            benefits = [
                f"Maps the file structure, tech stack, and documentation of '{path}'",
                "Indexes the directory into HELIX cognitive memory so HELIX can reason about it",
            ]
            risks = [f"Inspects folder layout and metadata under '{path}'"]
        else:
            reasoning = f"HELIX requests your permission to execute laptop action '{name}'."
            benefits = ["Fulfills the requested assistant workflow on your machine"]
            risks = [f"Runs privileged action '{name}'"]
        return reasoning, benefits, risks

    def _validate_parameters(self, tool_def: ToolDefinition, args: dict[str, Any]) -> str | None:
        # Normalize parameter aliases for goal operations
        if tool_def.name in ("create_goal", "add_subgoal"):
            if "title" not in args or not args.get("title"):
                for alias in ("name", "goal_name", "goal_title", "goal"):
                    if args.get(alias):
                        args["title"] = str(args[alias]).strip()
                        break
            if tool_def.name == "add_subgoal":
                if "goal_title" not in args or not args.get("goal_title"):
                    for alias in ("goal", "parent_goal", "goal_id"):
                        if args.get(alias):
                            args["goal_title"] = str(args[alias]).strip()
                            break
                if "subgoal_title" not in args or not args.get("subgoal_title"):
                    for alias in ("subgoal", "task", "subtask"):
                        if args.get(alias):
                            args["subgoal_title"] = str(args[alias]).strip()
                            break

        for param in tool_def.parameters:
            if param.required and (param.name not in args or args[param.name] is None):
                return f"Missing required parameter '{param.name}' for tool '{tool_def.name}'."
            if param.name in args and args[param.name] is not None:
                val = args[param.name]
                if param.type == "string" and not isinstance(val, str):
                    args[param.name] = str(val)
                elif param.type == "integer" and not isinstance(val, int):
                    try:
                        args[param.name] = int(val)
                    except ValueError:
                        return f"Parameter '{param.name}' must be an integer, got '{val}'."
        return None

    def _validate_safety(self, name: str, args: dict[str, Any]) -> str | None:
        path = args.get("path") or args.get("target")
        if path and isinstance(path, str):
            path_lower = path.lower()
            if any(token in path_lower for token in _BLOCKED_PATH_TOKENS):
                return f"Access denied: system path token in '{path}'."
            ext = os.path.splitext(path)[1].lower()
            if ext in _BLOCKED_EXTENSIONS:
                return f"Access denied: blocked file extension '{ext}'."

        url = args.get("url")
        if url and isinstance(url, str):
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme and parsed.scheme.lower() not in _ALLOWED_URL_SCHEMES:
                return f"Blocked URL scheme: '{parsed.scheme}'. Only http, https, and mailto are allowed."

        return None

    def _list_open_applications(self) -> dict[str, Any]:
        """Enumerate live visible desktop application windows without guessing or disk inspection."""
        windows: list[dict[str, Any]] = []
        focused_window: dict[str, Any] | None = None

        if sys.platform == "win32":
            try:
                import ctypes
                import ctypes.wintypes

                user32 = ctypes.windll.user32
                hwnd_fg = user32.GetForegroundWindow()

                EnumWindowsProc = ctypes.WINFUNCTYPE(
                    ctypes.wintypes.BOOL,
                    ctypes.wintypes.HWND,
                    ctypes.wintypes.LPARAM,
                )

                ignored_titles = {
                    "Default IME", "MSCTFIME UI", "Program Manager", "Settings",
                    "Windows Shell Experience Host", "Task Switching",
                }

                def enum_cb(hwnd: int, _lparam: int) -> bool:
                    if not user32.IsWindowVisible(hwnd):
                        return True
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length == 0:
                        return True
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value.strip()
                    if not title or title in ignored_titles:
                        return True

                    pid = ctypes.wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

                    proc_name = "unknown"
                    try:
                        import psutil
                        proc = psutil.Process(pid.value)
                        proc_name = proc.name()
                    except Exception:
                        pass

                    is_focused = (hwnd == hwnd_fg)
                    entry = {
                        "window_title": title,
                        "process_name": proc_name,
                        "pid": pid.value,
                        "is_focused": is_focused,
                    }
                    if is_focused:
                        nonlocal focused_window
                        focused_window = entry
                    windows.append(entry)
                    return True

                user32.EnumWindows(EnumWindowsProc(enum_cb), 0)
            except Exception as e:
                logger.warning("EnumWindows desktop inspection failed: %s", e)
                return {
                    "status": "error",
                    "error": f"Failed to retrieve desktop windows: {e}",
                    "open_applications": [],
                    "focused_window": None,
                }
        else:
            return {
                "status": "unsupported",
                "message": "Desktop inspection is only supported on Windows platforms.",
                "open_applications": [],
                "focused_window": None,
            }

        return {
            "status": "success",
            "count": len(windows),
            "open_applications": windows,
            "focused_window": focused_window,
        }

    def _get_system_state(self) -> dict[str, Any]:
        """Fetch basic system perception state with focused window information."""
        focused_title = "Unknown"
        focused_proc = "Unknown"
        if sys.platform == "win32":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                hwnd = user32.GetForegroundWindow()
                if hwnd:
                    length = user32.GetWindowTextLengthW(hwnd)
                    if length > 0:
                        buf = ctypes.create_unicode_buffer(length + 1)
                        user32.GetWindowTextW(hwnd, buf, length + 1)
                        focused_title = buf.value.strip() or "Desktop"
            except Exception:
                pass
        return {
            "os": "Windows 11",
            "active_companion": "HELIX PAIOS",
            "time_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
            "status": "operational",
            "focused_window": {
                "title": focused_title,
                "application": focused_proc,
            },
        }
