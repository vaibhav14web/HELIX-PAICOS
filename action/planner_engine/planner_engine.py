"""
action/planner_engine/planner_engine.py

HELIX Planner Engine.
Implements a deterministic, rule-based, and procedural-memory-first planning algorithm.
Decomposes user tasks into multi-step execution plans using:
1. Procedural memory matching (find_matching_workflow)
2. Deterministic regex & keyword pattern matching
3. Explicit safety risk classification for action execution.
"""
import os
import re
import asyncio
import logging
import uuid
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent
from foundation.storage_manager.crypto import encrypt_string, decrypt_string

logger = logging.getLogger("helix.planner_engine")


class PlanStep:
    def __init__(
        self,
        step_id: str,
        action: str,
        target: str | None = None,
        params: dict[str, Any] | None = None,
        depends_on: list[str] | None = None,
        risk_level: str = "low",
        status: str = "pending",
        preconditions: list[str] | None = None,
        postconditions: list[str] | None = None,
    ):
        self.step_id = step_id
        self.action = action
        self.target = target
        self.params = params or {}
        self.depends_on = depends_on or []
        self.status = status
        self.risk_level = risk_level
        self.preconditions = preconditions or []
        self.postconditions = postconditions or []

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "target": self.target,
            "params": self.params,
            "depends_on": self.depends_on,
            "status": self.status,
            "risk_level": self.risk_level,
            "preconditions": self.preconditions,
            "postconditions": self.postconditions,
        }


def validate_plan_dag(steps: list[PlanStep]) -> tuple[bool, str | None]:
    """
    Validate that plan steps form a valid Directed Acyclic Graph (DAG):
    - No self-dependencies
    - No unknown or missing dependencies
    - No circular dependency chains
    """
    step_ids = {s.step_id for s in steps}

    for s in steps:
        for dep in s.depends_on:
            if dep == s.step_id:
                return False, f"Self-dependency detected: Step '{s.step_id}' depends on itself."
            if dep not in step_ids:
                return False, f"Missing dependency: Step '{s.step_id}' depends on non-existent step '{dep}'."

    adj: dict[str, list[str]] = {s.step_id: list(s.depends_on) for s in steps}
    visited: dict[str, int] = {}  # 0: unvisited, 1: visiting, 2: visited

    def has_cycle(u: str) -> bool:
        visited[u] = 1
        for v in adj.get(u, []):
            if visited.get(v, 0) == 1:
                return True
            if visited.get(v, 0) == 0:
                if has_cycle(v):
                    return True
        visited[u] = 2
        return False

    for s in steps:
        if visited.get(s.step_id, 0) == 0:
            if has_cycle(s.step_id):
                return False, f"Circular dependency cycle detected involving step '{s.step_id}'."

    return True, None


class Plan:
    def __init__(
        self,
        plan_id: str,
        task: str,
        steps: list[PlanStep],
        context: dict[str, Any] | None = None,
        goal_id: str | None = None,
        user_id: str = "default_owner",
    ):
        self.plan_id = plan_id
        self.task = task
        self.steps = steps
        self.context = context or {}
        self.goal_id = goal_id or (context or {}).get("goal_id")
        self.user_id = user_id or (context or {}).get("user_id", "default_owner")
        self.status = "created"
        self.replan_count: int = (context or {}).get("replan_count", 0)
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task": self.task,
            "goal_id": self.goal_id,
            "user_id": self.user_id,
            "steps": [s.to_dict() for s in self.steps],
            "context": self.context,
            "status": self.status,
            "replan_count": self.replan_count,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


_KNOWN_APPS = {
    "chrome", "google chrome", "edge", "microsoft edge", "firefox", "notepad",
    "calc", "calculator", "mspaint", "paint", "wt", "terminal", "explorer", "file explorer",
    "browser", "a browser", "the browser", "web browser"
}

_APP_DISPLAY_NAMES = {
    "chrome": "Google Chrome",
    "google chrome": "Google Chrome",
    "edge": "Microsoft Edge",
    "microsoft edge": "Microsoft Edge",
    "firefox": "Firefox",
    "notepad": "Notepad",
    "calc": "Calculator",
    "calculator": "Calculator",
    "mspaint": "Paint",
    "paint": "Paint",
    "wt": "Terminal",
    "terminal": "Terminal",
    "explorer": "File Explorer",
    "file explorer": "File Explorer",
}

_LOW_RISK_ACTIONS = {"browser_search", "read_file", "read_browser_page"}

_MEDIUM_RISK_ACTIONS = {"compose_email", "send_notification", "launch_application", "in_app_task", "vscode_open", "create_note"}

_BLOCKED_PATH_TOKENS = {"systemroot", "programdata", "program files"}

_BLOCKED_PATH_PREFIXES = [
    p.lower().rstrip("\\") + "\\"
    for p in [
        os.environ.get("SystemRoot", "C:\\Windows"),
        os.environ.get("ProgramData", "C:\\ProgramData"),
        os.environ.get("ALLUSERSPROFILE", "C:\\ProgramData"),
        os.environ.get("ProgramFiles", "C:\\Program Files"),
        os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"),
    ]
]

_BLOCKED_EXTENSIONS = {".exe", ".bat", ".cmd", ".com", ".msi", ".ps1", ".psm1", ".psd1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".scr", ".pif", ".gadget", ".cpl", ".scf", ".lnk", ".inf", ".reg"}

_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


class PlannerEngine:
    def __init__(self, event_bus: EventBus, procedural_memory: Any | None = None):
        self._event_bus = event_bus
        self._procedural_memory = procedural_memory
        self._lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._max_plans = int(os.getenv("HELIX_PLANNER_MAX_PLANS", "50"))
        self._state_path = os.getenv("HELIX_STATE_DIR", os.path.join(os.getenv("HELIX_DATA_DIR", "./data"), "state"))
        self._plans: dict[str, Plan] = {}
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}

    async def start(self) -> None:
        self._event_handler_map = {
            "plan.request": self._handle_request,
            "plan.step.completed": self._handle_step_completed,
            "plan.step.failed": self._handle_step_failed,
            "plan.cancel": self._handle_cancel,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())
        await self.load_state()
        logger.info("Planner Engine started")

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        self._plans.clear()
        logger.info("Planner Engine stopped")

    async def _handle_request(self, event: HelixEvent) -> None:
        task = event.payload.get("task", "")
        if not task.strip():
            await self._event_bus.publish_event(
                source="planner_engine",
                event_type="plan.failed",
                payload={"error": "Task cannot be empty", "correlation_id": event.correlation_id},
                correlation_id=event.correlation_id,
            )
            return

        context = event.payload.get("context", {})

        try:
            steps = self._build_steps(task, context)
            for step in steps:
                self._validate_task_safety(step.action, step.target, step.params)
            # Validate Directed Acyclic Graph (DAG) dependencies
            dag_valid, dag_err = validate_plan_dag(steps)
            if not dag_valid:
                raise ValueError(f"Plan validation failed: {dag_err}")
        except ValueError as e:
            await self._event_bus.publish_event(
                source="planner_engine",
                event_type="plan.failed",
                payload={"error": str(e), "task": task},
                correlation_id=event.correlation_id,
            )
            return

        plan_id = uuid.uuid4().hex[:12]
        goal_id = context.get("goal_id")
        user_id = event.payload.get("user_id", context.get("user_id", "default_owner"))

        async with self._lock:
            plan = Plan(plan_id=plan_id, task=task, steps=steps, context=context, goal_id=goal_id, user_id=user_id)
            self._plans[plan_id] = plan
            self._prune_plans()

        logger.info("Plan created: %s (%d steps, goal_id=%s, user_id=%s)", plan_id, len(steps), goal_id, user_id)
        try:
            await self.save_state()
        except Exception as e:
            logger.error("Failed to save planner state for plan %s: %s", plan_id, e)
            async with self._lock:
                self._plans.pop(plan_id, None)
            await self._event_bus.publish_event(
                source="planner_engine",
                event_type="plan.failed",
                payload={
                    "plan_id": plan_id,
                    "error": f"State persistence failure: {e}",
                    "task": task,
                    "user_id": user_id,
                },
                correlation_id=event.correlation_id,
            )
            return

        payload = {
            "plan_id": plan_id,
            "task": task,
            "goal_id": goal_id,
            "user_id": user_id,
            "steps": [s.to_dict() for s in steps],
            "context": context,
        }
        await self._event_bus.publish_event(
            source="planner_engine",
            event_type="plan.created",
            payload=payload,
            priority=2,
            correlation_id=event.correlation_id,
        )
        if goal_id:
            await self._event_bus.publish_event(
                source="planner_engine",
                event_type="planner.plan_ready",
                payload={
                    "goal_id": goal_id,
                    "plan_id": plan_id,
                    "steps": [s.to_dict() for s in steps],
                },
                correlation_id=event.correlation_id,
            )

    async def _handle_step_completed(self, event: HelixEvent) -> None:
        plan_id = event.payload.get("plan_id", "")
        step_id = event.payload.get("step_id", "")

        async with self._lock:
            plan = self._plans.get(plan_id)
            if not plan:
                logger.warning("Step completed for unknown plan: %s", plan_id)
                return

            for step in plan.steps:
                if step.step_id == step_id:
                    step.status = "completed"
                    break

            all_completed = all(s.status == "completed" for s in plan.steps)
            if all_completed:
                plan.status = "completed"
                plan.completed_at = datetime.now(timezone.utc).isoformat()

        await self.save_state()
        if all_completed:
            logger.info("Plan completed: %s", plan_id)
            wf_id = (plan.context or {}).get("personalized_workflow_id")
            if wf_id:
                await self._event_bus.publish_event(
                    source="planner_engine",
                    event_type="memory.procedural.execution_result",
                    payload={"workflow_id": wf_id, "success": True},
                    correlation_id=event.correlation_id,
                )
            await self._event_bus.publish_event(
                source="planner_engine",
                event_type="plan.completed",
                payload={"plan_id": plan_id, "task": plan.task, "steps": [s.to_dict() for s in plan.steps], "context": plan.context},
                correlation_id=event.correlation_id,
            )

    async def _handle_step_failed(self, event: HelixEvent) -> None:
        plan_id = event.payload.get("plan_id", "")
        step_id = event.payload.get("step_id", "")
        error = event.payload.get("error", "Unknown error")

        async with self._lock:
            plan = self._plans.get(plan_id)
            if not plan:
                return

            for step in plan.steps:
                if step.step_id == step_id:
                    step.status = "failed"
                    break

            plan.status = "failed"

        await self.save_state()
        logger.warning("Plan step failed: %s / %s (%s)", plan_id, step_id, error)
        wf_id = (plan.context or {}).get("personalized_workflow_id")
        if wf_id:
            await self._event_bus.publish_event(
                source="planner_engine",
                event_type="memory.procedural.execution_result",
                payload={"workflow_id": wf_id, "success": False},
                correlation_id=event.correlation_id,
            )
        await self._event_bus.publish_event(
            source="planner_engine",
            event_type="plan.failed",
            payload={"plan_id": plan_id, "step_id": step_id, "error": error, "task": plan.task},
            correlation_id=event.correlation_id,
        )

    async def _handle_cancel(self, event: HelixEvent) -> None:
        plan_id = event.payload.get("plan_id", "")
        user_id = event.payload.get("user_id")

        async with self._lock:
            plan = self._plans.get(plan_id)
            if not plan:
                return
            if user_id and plan.user_id != user_id and user_id not in ("default", "default_owner", "global"):
                logger.warning("Unauthorized plan cancellation attempt: user '%s' on plan '%s' owned by '%s'", user_id, plan_id, plan.user_id)
                await self._event_bus.publish_event(
                    source="planner_engine",
                    event_type="plan.cancel_denied",
                    payload={"plan_id": plan_id, "error": f"Unauthorized: Plan belongs to {plan.user_id}"},
                    correlation_id=event.correlation_id,
                )
                return
            self._plans.pop(plan_id, None)

        plan.status = "cancelled"
        await self.save_state()
        logger.info("Plan cancelled: %s", plan_id)
        await self._event_bus.publish_event(
            source="planner_engine",
            event_type="plan.cancelled",
            payload={"plan_id": plan_id, "user_id": plan.user_id, "task": plan.task},
            correlation_id=event.correlation_id,
        )

    def get_plan(self, plan_id: str, user_id: str | None = None) -> Plan | None:
        plan = self._plans.get(plan_id)
        if not plan:
            return None
        if user_id and plan.user_id != user_id and user_id not in ("default", "default_owner", "global"):
            return None  # Authorization fail-closed
        return plan

    def list_plans(self, user_id: str | None = None) -> list[dict[str, Any]]:
        if user_id and user_id not in ("default", "default_owner", "global"):
            return [p.to_dict() for p in self._plans.values() if p.user_id == user_id]
        return [p.to_dict() for p in self._plans.values()]

    def _classify_risk(self, action: str, target: str | None, params: dict[str, Any]) -> str:
        if action == "browser_search":
            return "low"
        if action == "read_file":
            path = target or params.get("path", "")
            if any(token in path.lower() for token in _BLOCKED_PATH_TOKENS):
                return "high"
            ext = os.path.splitext(path)[1].lower()
            if ext in _BLOCKED_EXTENSIONS:
                return "high"
            return "medium"
        if action == "launch_application":
            return "medium"
        if action == "compose_email":
            return "medium"
        if action == "send_notification":
            return "low"
        if action == "open_url":
            url = target or params.get("url", "")
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in _ALLOWED_URL_SCHEMES:
                return "high"
            return "low"
        return "medium"

    def _validate_task_safety(self, action: str, target: str | None, params: dict[str, Any]) -> None:
        path = target or params.get("path", "")
        if path:
            path_lower = path.lower()
            if any(token in path_lower for token in _BLOCKED_PATH_TOKENS):
                raise ValueError(f"Access denied: blocked system path in request: {path}")
            resolved = os.path.abspath(path).lower()
            for prefix in _BLOCKED_PATH_PREFIXES:
                if resolved.startswith(prefix):
                    raise ValueError(f"Access denied: blocked system path in request: {path}")
            ext = os.path.splitext(path)[1].lower()
            if ext in _BLOCKED_EXTENSIONS:
                raise ValueError(f"Access denied: blocked file extension: {ext}")

        url = target or params.get("url", "")
        if url:
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme and parsed.scheme not in _ALLOWED_URL_SCHEMES:
                raise ValueError(f"Blocked URL scheme: '{parsed.scheme}'. Only http, https, and mailto are allowed.")

    def _build_steps(self, task: str, context: dict[str, Any] | None) -> list[PlanStep]:
        steps: list[PlanStep] = []
        lower = task.lower().strip()
        words = set(lower.split())

        # 1. Consult Procedural Memory for learned, personalized workflows
        if self._procedural_memory:
            project_scope = (context or {}).get("project")
            matched_wf = self._procedural_memory.find_matching_workflow(task, project_scope=project_scope)
            if matched_wf and matched_wf.steps:
                logger.info(
                    "Personalizing plan for '%s' using learned procedural workflow '%s' (conf: %.2f)",
                    task, matched_wf.name, matched_wf.confidence
                )
                if context is not None:
                    context["personalized_workflow_id"] = matched_wf.workflow_id
                    context["workflow_name"] = matched_wf.name

                step_id_map: dict[int, str] = {
                    s.step_index: uuid.uuid4().hex[:8] for s in matched_wf.steps
                }
                personalized_steps: list[PlanStep] = []
                for s in matched_wf.steps:
                    sid = step_id_map[s.step_index]
                    dep_sids = [step_id_map[d] for d in s.depends_on if d in step_id_map]
                    pstep = PlanStep(
                        step_id=sid,
                        action=s.action,
                        target=s.target,
                        params=dict(s.params),
                        depends_on=dep_sids,
                    )
                    pstep.risk_level = self._classify_risk(pstep.action, pstep.target, pstep.params)
                    personalized_steps.append(pstep)
                return personalized_steps

        if lower.startswith("open file"):
            file_path = task[len("open file "):].strip() if len(lower) > 9 else ""
            step = PlanStep(
                step_id=uuid.uuid4().hex[:8],
                action="read_file",
                params={"path": file_path},
            )
            step.risk_level = self._classify_risk(step.action, step.target, step.params)
            steps.append(step)
            return steps

        # If user expresses negation, feedback, or complaints, do not create a launch step
        negation_markers = (
            "did not", "didn't", "not open", "won't open", "haven't", "hasn't",
            "why didn't", "you did not", "could not", "repeat", "repeating",
            "repeats", "problem is", "issue is", "broken", "not work", "not working", "fails"
        )
        if any(neg in lower for neg in negation_markers):
            return []

        app_request_words = {"open", "launch", "start", "run"}
        has_action_prefix = any(lower.startswith(p) for p in ("open ", "launch ", "start ", "run ", "check "))
        known_app_found = self._extract_known_app(task)
        check_known_app = "check" in words and known_app_found is not None

        if has_action_prefix or (known_app_found and (app_request_words & words)) or check_known_app:
            raw_target = task
            for prefix in ("open ", "launch ", "start ", "check ", "run "):
                if lower.startswith(prefix):
                    raw_target = task[len(prefix):].strip().rstrip(" ?.! ")
                    break

            app_target = self._extract_known_app(raw_target) or known_app_found
            if not app_target and has_action_prefix:
                raw_lower = raw_target.lower().strip()
                vague_targets = {"it", "this", "that", "them", "something", "anything", "app", "an app", "the app", "application", "the application", "program", "the program"}
                # Filter out conversational slang / appraisals that should never be launched as an app
                slang_eval_words = {"fire", "mid", "sick", "sus", "cooked", "slay", "bet", "no cap", "grass", "brain rot", "scene", "jugaad", "mast"}
                is_slang_eval = any(w in raw_lower for w in slang_eval_words)
                if is_slang_eval or raw_lower.startswith(("this out", "that out", "it out", "out ")):
                    app_target = None
                elif raw_lower in vague_targets:
                    # Check if previous context has a known target
                    if context and isinstance(context, dict):
                        app_target = context.get("last_application") or context.get("last_target") or context.get("target")
                    else:
                        app_target = None
                else:
                    if len(raw_lower.split()) <= 3 and not any(ch in raw_lower for ch in (",", ";", "!", "?")):
                        app_target = raw_target
                    else:
                        app_target = None

            if app_target:
                # If compound command like "open google chrome and search for computer"
                if " and search" in app_target.lower() or " to search" in app_target.lower():
                    app_target = app_target.split(" and ")[0].split(" to ")[0].strip()

                if app_target.lower().strip() in ("browser", "a browser", "the browser", "web browser"):
                    app_target = "chrome"

                params: dict[str, Any] = {"application": app_target}
                if "search" in lower or "browse" in lower or "look" in lower or "find" in lower:
                    match = re.search(r"(?:search\s+for|search|browse\s+for|browse|look\s+for|find)\s+(.+)", task, re.IGNORECASE)
                    if match:
                        search_query = match.group(1).strip()
                        params["url"] = f"https://www.google.com/search?q={urllib.parse.quote_plus(search_query)}"

                step = PlanStep(
                    step_id=uuid.uuid4().hex[:8],
                    action="launch_application",
                    target=app_target,
                    params=params,
                )
                step.risk_level = self._classify_risk(step.action, step.target, step.params)
                steps.append(step)
                return steps

        if {"search", "browse", "look", "find"} & words or lower.startswith("look up"):
            query = task
            for prefix in ("search ", "browse ", "look up ", "find "):
                if lower.startswith(prefix):
                    query = task[len(prefix):].strip()
                    break
            step = PlanStep(
                step_id=uuid.uuid4().hex[:8],
                action="browser_search",
                target="browser",
                params={"query": query},
            )
            step.risk_level = self._classify_risk(step.action, step.target, step.params)
            steps.append(step)
            return steps

        if "send" in words and ({"email", "mail"} & words):
            step = PlanStep(
                step_id=uuid.uuid4().hex[:8],
                action="compose_email",
                params={"subject": task},
            )
            step.risk_level = self._classify_risk(step.action, step.target, step.params)
            steps.append(step)
            return steps

        if {"notify", "notification", "alert", "remind", "reminder"} & words:
            message = task
            for prefix in ("notify ", "alert ", "remind "):
                if lower.startswith(prefix):
                    message = task[len(prefix):].strip()
                    break
            step = PlanStep(
                step_id=uuid.uuid4().hex[:8],
                action="send_notification",
                params={"message": message},
            )
            step.risk_level = self._classify_risk(step.action, step.target, step.params)
            steps.append(step)
            return steps

        if "read" in words:
            file_path = task
            if lower.startswith("read "):
                file_path = task[5:].strip()
            step = PlanStep(
                step_id=uuid.uuid4().hex[:8],
                action="read_file",
                params={"path": file_path},
            )
            step.risk_level = self._classify_risk(step.action, step.target, step.params)
            steps.append(step)
            return steps

        step = PlanStep(
            step_id=uuid.uuid4().hex[:8],
            action="unknown",
            params={"task": task},
        )
        step.risk_level = self._classify_risk(step.action, step.target, step.params)
        steps.append(step)
        return steps

    def _extract_raw_app(self, task: str) -> str | None:
        lower = task.lower()
        for app in sorted(_KNOWN_APPS, key=len, reverse=True):
            if app in lower:
                return app
        return None

    def _extract_known_app(self, task: str) -> str | None:
        lower = task.lower()
        for app in sorted(_KNOWN_APPS, key=len, reverse=True):
            if app in lower:
                return _APP_DISPLAY_NAMES.get(app, app.title())
        return None

    def get_plan(self, plan_id: str) -> Plan | None:
        return self._plans.get(plan_id)

    def get_plans(self, status: str | None = None) -> list[Plan]:
        plans = list(self._plans.values())
        if status:
            plans = [p for p in plans if p.status == status]
        return plans

    async def replan(self, plan_id: str, reason: str, failed_step_id: str | None = None) -> Plan | None:
        """
        Bounded replanning: replan remaining tasks if execution failed or conditions changed.
        Preserves original goal and already completed steps up to 3 replanning attempts.
        """
        async with self._lock:
            original_plan = self._plans.get(plan_id)
            if not original_plan:
                return None

            if original_plan.replan_count >= 3:
                logger.warning("Replan limit exceeded (max 3) for plan %s", plan_id)
                return None

            completed_steps = [s for s in original_plan.steps if s.status == "completed"]
            new_context = dict(original_plan.context)
            new_context["replan_count"] = original_plan.replan_count + 1
            new_context["replan_reason"] = reason

            new_steps = self._build_steps(original_plan.task, new_context)
            completed_actions = {s.action for s in completed_steps}
            remaining_steps = [s for s in new_steps if s.action not in completed_actions]
            if not remaining_steps:
                remaining_steps = new_steps

            combined_steps = completed_steps + remaining_steps
            dag_valid, dag_err = validate_plan_dag(combined_steps)
            if not dag_valid:
                logger.warning("Replan DAG validation error: %s", dag_err)
                return None

            new_plan_id = uuid.uuid4().hex[:12]
            new_plan = Plan(
                plan_id=new_plan_id,
                task=original_plan.task,
                steps=combined_steps,
                context=new_context,
                goal_id=original_plan.goal_id,
            )
            self._plans[new_plan_id] = new_plan
            self._prune_plans()

        await self.save_state()
        await self._event_bus.publish_event(
            source="planner_engine",
            event_type="plan.replanned",
            payload={
                "original_plan_id": plan_id,
                "new_plan_id": new_plan_id,
                "goal_id": new_plan.goal_id,
                "replan_count": new_plan.replan_count,
                "reason": reason,
                "steps": [s.to_dict() for s in combined_steps],
            },
        )
        return new_plan

    async def save_state(self) -> None:
        import json
        from pathlib import Path

        async with self._save_lock:
            dest_dir = Path(self._state_path)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / "planner_state.enc"
            temp_path = dest_dir / f"planner_state.enc.tmp.{uuid.uuid4().hex}"

            async with self._lock:
                data = {pid: plan.to_dict() for pid, plan in self._plans.items()}
            json_str = json.dumps(data, indent=2, default=str)
            enc_bytes = encrypt_string(json_str)

            try:
                def _write_and_replace() -> None:
                    with open(temp_path, "wb") as f:
                        f.write(enc_bytes)
                        f.flush()
                        os.fsync(f.fileno())

                    if temp_path.stat().st_size != len(enc_bytes):
                        raise IOError(f"Temporary file size mismatch: expected {len(enc_bytes)}, got {temp_path.stat().st_size}")

                    with open(temp_path, "rb") as f:
                        written_bytes = f.read()
                    decrypted_str = decrypt_string(written_bytes)
                    validated_data = json.loads(decrypted_str)
                    if not isinstance(validated_data, dict):
                        raise ValueError("Validated planner temporary state missing expected dict structure")

                    os.replace(temp_path, dest_path)

                await asyncio.to_thread(_write_and_replace)
            except Exception as e:
                logger.error("Failed to save planner state: %s", e)
                raise
            finally:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

    async def load_state(self) -> None:
        import json
        enc_path = os.path.join(self._state_path, "planner_state.enc")
        legacy_path = os.path.join(self._state_path, "planner_state.json")
        path_to_read = enc_path if os.path.exists(enc_path) else (legacy_path if os.path.exists(legacy_path) else None)
        if not path_to_read:
            return

        try:
            with open(path_to_read, "rb") as f:
                raw_bytes = f.read()
            json_str = decrypt_string(raw_bytes)
            data = json.loads(json_str)

            recovered_plans: list[Plan] = []
            async with self._lock:
                for pid, plan_data in data.items():
                    steps = [PlanStep(**s) for s in plan_data.get("steps", [])]
                    plan = Plan(
                        plan_id=pid,
                        task=plan_data.get("task", ""),
                        steps=steps,
                        context=plan_data.get("context", {}),
                        goal_id=plan_data.get("goal_id"),
                        user_id=plan_data.get("user_id", "default_owner"),
                    )
                    plan.status = plan_data.get("status", "created")
                    plan.created_at = plan_data.get("created_at", "")
                    plan.completed_at = plan_data.get("completed_at")

                    # Crash recovery: if plan was left in-flight on process restart
                    if plan.status in ("created", "in_progress", "pending"):
                        has_interrupted = False
                        for step in plan.steps:
                            if step.status in ("pending", "executing"):
                                step.status = "failed"
                                has_interrupted = True
                        if has_interrupted or plan.status != "completed":
                            plan.status = "failed"
                            recovered_plans.append(plan)

                    self._plans[pid] = plan

            logger.info("Loaded %d plans from state", len(data))
            for r_plan in recovered_plans:
                logger.warning("Recovered interrupted plan on startup: %s (%s)", r_plan.plan_id, r_plan.task)
                await self._event_bus.publish_event(
                    source="planner_engine",
                    event_type="plan.failed",
                    payload={
                        "plan_id": r_plan.plan_id,
                        "error": "Interrupted by system restart",
                        "task": r_plan.task,
                        "user_id": r_plan.user_id,
                    },
                )
            if recovered_plans:
                try:
                    await self.save_state()
                except Exception as e:
                    logger.error("Failed to save planner state after recovering plans on startup: %s", e)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Failed to load planner state: %s", e)

    def _prune_plans(self) -> None:
        if len(self._plans) > self._max_plans:
            sorted_ids = sorted(self._plans.keys(), key=lambda pid: self._plans[pid].created_at)
            for pid in sorted_ids[:len(sorted_ids) - self._max_plans]:
                del self._plans[pid]
