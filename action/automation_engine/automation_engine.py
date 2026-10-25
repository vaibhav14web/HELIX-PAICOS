import os
import asyncio
import logging
import uuid
import time
from datetime import datetime, timezone
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent
from foundation.storage_manager.crypto import encrypt_string, decrypt_string
from foundation.permission_manager.permission_manager import ActionRiskLevel, classify_action_risk

logger = logging.getLogger("helix.automation_engine")

_PERMISSION_TIMEOUT = 300


class ActionRecord:
    def __init__(
        self,
        action_id: str,
        action: str,
        permission_id: str | None = None,
        target: str | None = None,
        params: dict[str, Any] | None = None,
        risk_level: str = "low",
        plan_id: str | None = None,
        step_id: str | None = None,
        user_id: str | None = None,
    ):
        self.action_id = action_id
        self.action = action
        self.permission_id = permission_id
        self.target = target
        self.params = params or {}
        self.risk_level = risk_level
        self.plan_id = plan_id
        self.step_id = step_id
        self.user_id = user_id
        self.status = "pending"
        self.result: dict[str, Any] | None = None
        self.error: str | None = None
        self.created_at = time.time()
        self.created_at_iso = datetime.now(timezone.utc).isoformat()
        self.completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action": self.action,
            "permission_id": self.permission_id,
            "target": self.target,
            "params": self.params,
            "risk_level": self.risk_level,
            "plan_id": self.plan_id,
            "step_id": self.step_id,
            "user_id": self.user_id,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at_iso,
            "completed_at": self.completed_at,
        }


class AutomationEngine:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._max_history = int(os.getenv("HELIX_AUTOMATION_MAX_HISTORY", "200"))
        self._permission_timeout = int(os.getenv("HELIX_AUTOMATION_PERMISSION_TIMEOUT", str(_PERMISSION_TIMEOUT)))
        state_dir_env = os.getenv("HELIX_STATE_DIR")
        data_dir_env = os.getenv("HELIX_DATA_DIR")
        if state_dir_env and state_dir_env.strip():
            from foundation.paths import expand_path
            self._state_path = str(expand_path(state_dir_env.strip()) or state_dir_env.strip())
        elif data_dir_env and data_dir_env.strip():
            from foundation.paths import expand_path
            data_dir = expand_path(data_dir_env.strip()) or Path(data_dir_env.strip())
            self._state_path = str(data_dir / "state")
        else:
            from foundation.paths import get_runtime_dir
            self._state_path = str(get_runtime_dir() / "state")
        self._actions: dict[str, ActionRecord] = {}
        self._actions_by_permission: dict[str, str] = {}
        self._actions_waiting_confirmation: dict[str, ActionRecord] = {}
        self._history: list[ActionRecord] = []
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}
        self._watchdog_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._event_handler_map = {
            "automation.execute": self._handle_execute,
            "automation.execute_step": self._handle_execute_step,
            "automation.result": self._handle_result,
            "automation.failed": self._handle_failed,
            "automation.cancel": self._handle_cancel,
            "automation.confirm.response": self._handle_confirm_response,
            "permission.granted": self._handle_permission_granted,
            "permission.denied": self._handle_permission_denied,
            "permission.expired": self._handle_permission_denied,
            "permission.cancelled": self._handle_permission_denied,
            "permission.timed_out": self._handle_permission_denied,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())
        await self.load_state()
        self._watchdog_task = asyncio.create_task(self._watchdog())
        logger.info("Automation Engine started")

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        if self._watchdog_task and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            self._watchdog_task = None
        logger.info("Automation Engine stopped")

    async def _handle_execute(self, event: HelixEvent) -> None:
        user_id = event.payload.get("user_id")
        action = event.payload.get("action", "")

        if not user_id or not isinstance(user_id, str) or not user_id.strip():
            logger.warning("Rejecting execute request without valid authenticated user_id")
            await self._event_bus.publish_event(
                source="automation_engine",
                event_type="automation.failed",
                payload={"action_id": "", "action": action, "error": "User identity missing or invalid: cannot execute action without authenticated user_id"},
                correlation_id=event.correlation_id,
            )
            return

        user_id = user_id.strip()

        if not action.strip():
            await self._event_bus.publish_event(
                source="automation_engine",
                event_type="automation.failed",
                payload={"action_id": "", "action": "", "error": "Action cannot be empty", "user_id": user_id},
                correlation_id=event.correlation_id,
            )
            return

        action_id = uuid.uuid4().hex[:12]
        target = event.payload.get("target")
        params = event.payload.get("params", {})
        plan_id = event.payload.get("plan_id")
        step_id = event.payload.get("step_id")
        permission_id = event.payload.get("permission_id")
        risk_level = self._effective_risk(action, params, event.payload.get("risk_level", "low"))

        async with self._lock:
            record = ActionRecord(
                action_id=action_id,
                action=action,
                permission_id=permission_id,
                target=target,
                params=params,
                risk_level=risk_level,
                plan_id=plan_id,
                step_id=step_id,
                user_id=user_id,
            )
            self._actions[action_id] = record

            if permission_id:
                self._actions_by_permission[permission_id] = action_id
                record.status = "waiting_permission"
                logger.info("Action awaiting permission: %s (%s, perm=%s, user=%s)", action_id, action, permission_id, user_id)
                return

        if risk_level != "low":
            await self._request_permission(action_id, record, action, target, params, plan_id, step_id, event.correlation_id)
            return

        await self._execute_action(action_id, record, action, target, params, plan_id, step_id, event.correlation_id)

    async def _handle_execute_step(self, event: HelixEvent) -> None:
        user_id = event.payload.get("user_id")
        plan_id = event.payload.get("plan_id", "")
        step = event.payload.get("step", {})
        action = step.get("action", "")
        target = step.get("target")
        params = step.get("params", {})
        step_id = step.get("step_id", "")
        risk_level = self._effective_risk(action, params, event.payload.get("risk_level") or step.get("risk_level", "low"))

        if not user_id or not isinstance(user_id, str) or not user_id.strip():
            logger.warning("Rejecting execute_step request without valid authenticated user_id")
            await self._event_bus.publish_event(
                source="automation_engine",
                event_type="automation.failed",
                payload={"action_id": "", "action": action, "error": "User identity missing or invalid: cannot execute step without authenticated user_id", "plan_id": plan_id, "step_id": step_id},
                correlation_id=event.correlation_id,
            )
            return

        user_id = user_id.strip()

        if not action.strip():
            await self._event_bus.publish_event(
                source="automation_engine",
                event_type="automation.failed",
                payload={"action_id": "", "action": "", "error": "Step action cannot be empty", "plan_id": plan_id, "step_id": step_id, "user_id": user_id},
                correlation_id=event.correlation_id,
            )
            return

        permission_id = event.payload.get("permission_id")
        action_id = uuid.uuid4().hex[:12]

        async with self._lock:
            record = ActionRecord(
                action_id=action_id,
                action=action,
                permission_id=permission_id,
                target=target,
                params=params,
                risk_level=risk_level,
                plan_id=plan_id,
                step_id=step_id,
                user_id=user_id,
            )
            self._actions[action_id] = record
            if permission_id:
                self._actions_by_permission[permission_id] = action_id
                record.status = "waiting_permission"
                logger.info("Step awaiting permission: %s (%s, perm=%s, user=%s)", action_id, action, permission_id, user_id)
                return

        if risk_level != "low":
            await self._request_permission(action_id, record, action, target, params, plan_id, step_id, event.correlation_id)
            return

        await self._execute_action(action_id, record, action, target, params, plan_id, step_id, event.correlation_id)

    async def _handle_result(self, event: HelixEvent) -> None:
        action_id = event.payload.get("action_id", "")
        result = event.payload.get("result", {})

        async with self._lock:
            record = self._actions.pop(action_id, None)
            if not record:
                return
            self._actions_by_permission.pop(record.permission_id, None)
            record.status = "completed"
            record.result = result
            record.completed_at = datetime.now(timezone.utc).isoformat()
            self._history.append(record)
            self._prune_history()

        try:
            await self.save_state()
        except Exception as e:
            logger.error("State persistence failed on action completion for %s: %s", action_id, e)
            async with self._lock:
                record.status = "failed"
                record.error = f"State persistence failure: {e}"
                record.result = None
            await self._event_bus.publish_event(
                source="automation_engine",
                event_type="automation.failed",
                payload={
                    "action_id": action_id,
                    "action": record.action,
                    "error": f"State persistence failure: {e}",
                    "plan_id": event.payload.get("plan_id"),
                    "step_id": event.payload.get("step_id"),
                    "user_id": record.user_id,
                },
                correlation_id=event.correlation_id,
            )
            return

        await self._event_bus.publish_event(
            source="automation_engine",
            event_type="automation.completed",
            payload={
                "action_id": action_id,
                "action": record.action,
                "result": result,
                "plan_id": event.payload.get("plan_id"),
                "step_id": event.payload.get("step_id"),
                "user_id": record.user_id,
            },
            correlation_id=event.correlation_id,
        )
        logger.info("Action completed: %s (%s, user=%s)", action_id, record.action, record.user_id)

    async def _handle_failed(self, event: HelixEvent) -> None:
        action_id = event.payload.get("action_id", "")
        error = event.payload.get("error", "Unknown error")

        async with self._lock:
            record = self._actions.pop(action_id, None)
            if not record:
                return
            self._actions_by_permission.pop(record.permission_id, None)
            record.status = "failed"
            record.error = error
            record.completed_at = datetime.now(timezone.utc).isoformat()
            self._history.append(record)
            self._prune_history()

        try:
            await self.save_state()
        except Exception as e:
            logger.error("Failed to save state on action failure: %s", e)
        logger.warning("Action failed: %s (%s): %s", action_id, record.action, error)

    async def _handle_cancel(self, event: HelixEvent) -> None:
        action_id = event.payload.get("action_id", "")
        user_id = event.payload.get("user_id")

        if not user_id or not isinstance(user_id, str) or not user_id.strip():
            logger.warning("Rejecting cancellation without valid user_id")
            await self._event_bus.publish_event(
                source="automation_engine",
                event_type="automation.cancel_denied",
                payload={"action_id": action_id, "error": "Missing user identity"},
                correlation_id=event.correlation_id,
            )
            return

        user_id = user_id.strip()
        permission_id = None

        async with self._lock:
            record = self._actions.get(action_id)
            if not record:
                record = self._actions_waiting_confirmation.get(action_id)

            if not record:
                logger.warning("Cancellation requested for unknown action: %s", action_id)
                return

            if record.user_id != user_id:
                logger.warning("Unauthorized cancellation attempt: user '%s' on action '%s' owned by '%s'", user_id, action_id, record.user_id)
                await self._event_bus.publish_event(
                    source="automation_engine",
                    event_type="automation.cancel_denied",
                    payload={"action_id": action_id, "error": "Unauthorized: Cannot cancel action belonging to another user"},
                    correlation_id=event.correlation_id,
                )
                return

            self._actions.pop(action_id, None)
            self._actions_waiting_confirmation.pop(action_id, None)
            self._actions_by_permission.pop(record.permission_id, None)
            permission_id = record.permission_id
            record.status = "cancelled"
            self._history.append(record)
            self._prune_history()

        try:
            await self.save_state()
        except Exception as e:
            logger.error("Failed to save state on action cancellation: %s", e)

        logger.info("Action cancelled: %s by user %s", action_id, user_id)
        if permission_id:
            await self._event_bus.publish_event(
                source="automation_engine",
                event_type="permission.cancel",
                payload={"id": permission_id, "user_id": user_id, "reason": "Automation action cancelled."},
                correlation_id=event.correlation_id,
            )

    async def _handle_permission_granted(self, event: HelixEvent) -> None:
        if event.source != "permission_manager":
            logger.warning("Ignoring permission grant from untrusted source '%s'", event.source)
            return
        permission_id = event.payload.get("id", "")
        mismatch_error: str | None = None

        async with self._lock:
            action_id = self._actions_by_permission.pop(permission_id, None)
            if not action_id:
                return
            record = self._actions.get(action_id)
            if not record or record.status != "waiting_permission":
                return
            from foundation.permission_manager.permission_manager import compute_params_hash
            grant_user = event.payload.get("user_id")
            if (event.payload.get("action") != record.action
                    or event.payload.get("scope") != record.action
                    or event.payload.get("target") != record.target
                    or event.payload.get("params_hash") != compute_params_hash(record.params)
                    or (grant_user and record.user_id and grant_user != record.user_id)):
                logger.warning("Ignoring permission grant with mismatched action details or owner for %s (grant_user=%s, record_user=%s)", permission_id, grant_user, record.user_id)
                mismatch_error = "Permission decision details no longer match the pending action."
                record.status = "failed"
                record.error = mismatch_error
                record.completed_at = datetime.now(timezone.utc).isoformat()
                self._history.append(record)
                self._prune_history()
            else:
                record.status = "executing"

        if mismatch_error:
            try:
                await self.save_state()
            except Exception as e:
                logger.error("Failed to save state on permission mismatch: %s", e)
            await self._event_bus.publish_event(
                source="automation_engine",
                event_type="automation.failed",
                payload={"action_id": action_id, "action": record.action, "error": mismatch_error,
                         "plan_id": record.plan_id, "step_id": record.step_id, "user_id": record.user_id},
                correlation_id=event.correlation_id,
            )
            return

        action = record.action
        target = record.target
        params = record.params
        plan_id = event.payload.get("plan_id") or record.plan_id
        step_id = event.payload.get("step_id") or record.step_id
        await self._execute_action(action_id, record, action, target, params, plan_id, step_id, event.correlation_id)

    async def _handle_permission_denied(self, event: HelixEvent) -> None:
        permission_id = event.payload.get("id", "")
        deny_user = event.payload.get("user_id")

        async with self._lock:
            action_id = self._actions_by_permission.get(permission_id)
            if not action_id:
                return
            record = self._actions.get(action_id)
            if not record:
                return
            if deny_user and record.user_id and deny_user != record.user_id:
                logger.warning("Ignoring unauthorized permission denial from user '%s' for action owned by '%s'", deny_user, record.user_id)
                return

            self._actions_by_permission.pop(permission_id, None)
            self._actions.pop(action_id, None)
            if event.event_type == "permission.expired":
                record.status, default_error = "expired", "Permission request expired"
            elif event.event_type == "permission.cancelled":
                record.status, default_error = "cancelled", "Permission request cancelled"
            elif event.event_type == "permission.timed_out":
                record.status, default_error = "timed_out", "Permission request timed out"
            else:
                record.status, default_error = "denied", "Permission denied by user"
            record.error = event.payload.get("reason") or default_error
            record.completed_at = datetime.now(timezone.utc).isoformat()
            self._history.append(record)
            self._prune_history()

        try:
            await self.save_state()
        except Exception as e:
            logger.error("Failed to save state on permission denial: %s", e)
        logger.info("Action denied: %s (%s)", action_id, record.action)

    async def _request_permission(
        self,
        action_id: str,
        record: ActionRecord,
        action: str,
        target: str | None,
        params: dict[str, Any],
        plan_id: str | None,
        step_id: str | None,
        correlation_id: str,
    ) -> None:
        permission_id = uuid.uuid4().hex

        async with self._lock:
            record.permission_id = permission_id
            self._actions_by_permission[permission_id] = action_id
            record.status = "waiting_permission"
            logger.info("Action requesting permission: %s (%s, perm=%s, user=%s)", action_id, action, permission_id, record.user_id)

        description = f"Allow Helix to {action}"
        if target:
            description += f" on {target}"
        if params.get("url"):
            description += f" ({params['url']})"

        await self._event_bus.publish_event(
            source="automation_engine",
            event_type="automation.permission_needed",
            payload={
                "action_id": action_id,
                "permission_id": permission_id,
                "action": action,
                "target": target,
                "params": params,
                "plan_id": plan_id,
                "step_id": step_id,
                "risk_level": record.risk_level,
                "ttl_seconds": self._permission_timeout,
                "description": description,
                "user_id": record.user_id,
            },
            correlation_id=correlation_id,
        )

        await self._event_bus.publish_event(
            source="automation_engine",
            event_type="action.execute",
            payload={
                "action_id": f"notify-{permission_id}",
                "action": "send_notification",
                "params": {
                    "title": "HELIX permission needed",
                    "message": f"{description}. Open HELIX to grant or deny.",
                },
                "risk_level": "low",
                "user_id": record.user_id,
            },
            correlation_id=correlation_id,
        )

    @staticmethod
    def _effective_risk(action: str, params: dict[str, Any], requested_risk: str | ActionRiskLevel) -> str:
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        inherent = classify_action_risk(action, params).value
        requested = requested_risk.value if isinstance(requested_risk, ActionRiskLevel) else str(requested_risk).lower()
        if requested not in risk_order:
            requested = inherent
        return max((inherent, requested), key=lambda risk: risk_order[risk])

    async def _request_confirmation(
        self,
        action_id: str,
        record: ActionRecord,
        action: str,
        target: str | None,
        params: dict[str, Any],
        plan_id: str | None,
        step_id: str | None,
        correlation_id: str,
    ) -> None:
        async with self._lock:
            record.status = "waiting_confirmation"
            self._actions_waiting_confirmation[action_id] = record
            logger.info("Action awaiting confirmation: %s (%s, user=%s)", action_id, action, record.user_id)

        description = f"Are you sure you want to {action}"
        if target:
            description += f" {target}"
        if params.get("url"):
            description += f" ({params['url']})"
        if params.get("path"):
            description += f" ({params['path']})"
        description += "?"

        await self._event_bus.publish_event(
            source="automation_engine",
            event_type="automation.confirm.request",
            payload={
                "action_id": action_id,
                "action": action,
                "target": target,
                "params": params,
                "plan_id": plan_id,
                "step_id": step_id,
                "risk_level": "high",
                "description": description,
                "confirm_prompt": f"High-risk action: {description} Please confirm verbally or in text.",
                "user_id": record.user_id,
            },
            correlation_id=correlation_id,
        )

    async def _handle_confirm_response(self, event: HelixEvent) -> None:
        action_id = event.payload.get("action_id", "")
        confirmed = event.payload.get("confirmed", False)
        user_id = event.payload.get("user_id")

        if not user_id or not isinstance(user_id, str) or not user_id.strip():
            logger.warning("Rejecting confirmation response without valid user_id for action '%s'", action_id)
            return

        user_id = user_id.strip()

        async with self._lock:
            record = self._actions_waiting_confirmation.get(action_id)
            if not record:
                logger.warning("Confirmation response for unknown action: %s", action_id)
                return

            if record.status != "waiting_confirmation":
                logger.warning("Confirmation response rejected: action '%s' is not in waiting_confirmation status (current=%s)", action_id, record.status)
                return

            if record.user_id != user_id:
                logger.warning("Unauthorized confirm response from user '%s' for action owned by '%s'", user_id, record.user_id)
                return

            self._actions_waiting_confirmation.pop(action_id, None)

            if not confirmed:
                self._actions.pop(action_id, None)
                record.status = "denied"
                record.error = "User declined high-risk confirmation"
                record.completed_at = datetime.now(timezone.utc).isoformat()
                self._history.append(record)
                self._prune_history()
                logger.info("High-risk action denied by user: %s (%s)", action_id, record.action)
                try:
                    await self.save_state()
                except Exception as e:
                    logger.error("Failed to save state on confirm denial: %s", e)
                return

            logger.info("High-risk action confirmed: %s (%s)", action_id, record.action)

        plan_id = event.payload.get("plan_id") or record.plan_id
        step_id = event.payload.get("step_id") or record.step_id
        correlation_id = event.correlation_id
        target = record.target
        params = record.params

        await self._request_permission(action_id, record, record.action, target, params, plan_id, step_id, correlation_id)

    async def _execute_action(
        self,
        action_id: str,
        record: ActionRecord,
        action: str,
        target: str | None,
        params: dict[str, Any],
        plan_id: str | None,
        step_id: str | None,
        correlation_id: str,
    ) -> None:
        record.status = "executing"
        try:
            await self.save_state()
        except Exception as e:
            logger.error("Failed to persist state before executing action %s: %s", action_id, e)
            async with self._lock:
                self._actions.pop(action_id, None)
                record.status = "failed"
                record.error = f"State persistence failure before execution: {e}"
                record.completed_at = datetime.now(timezone.utc).isoformat()
                self._history.append(record)
                self._prune_history()
            await self._event_bus.publish_event(
                source="automation_engine",
                event_type="automation.failed",
                payload={
                    "action_id": action_id,
                    "action": action,
                    "error": f"State persistence failure before execution: {e}",
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "user_id": record.user_id,
                },
                correlation_id=correlation_id,
            )
            return

        logger.info("Executing action: %s (%s, user=%s)", action_id, action, record.user_id)

        await self._event_bus.publish_event(
            source="automation_engine",
            event_type="action.execute",
            payload={
                "action_id": action_id,
                "action": action,
                "target": target,
                "params": params,
                "plan_id": plan_id,
                "step_id": step_id,
                "risk_level": record.risk_level,
                "user_id": record.user_id,
                "permission_id": record.permission_id,
                "grant_id": record.permission_id,
            },
            priority=2,
            correlation_id=correlation_id,
        )

    async def _watchdog(self) -> None:
        try:
            while True:
                await asyncio.sleep(30)
                now = time.time()
                async with self._lock:
                    expired_perm = [
                        aid for aid, rec in self._actions.items()
                        if rec.status == "waiting_permission" and now - rec.created_at > self._permission_timeout
                    ]
                    for aid in expired_perm:
                        record = self._actions.pop(aid, None)
                        if record:
                            self._actions_by_permission.pop(record.permission_id, None)
                            record.status = "timeout"
                            record.error = "Permission request timed out"
                            record.completed_at = datetime.now(timezone.utc).isoformat()
                            self._history.append(record)
                            logger.warning("Permission timeout: %s (%s)", aid, record.action)

                    expired_conf = [
                        aid for aid, rec in self._actions_waiting_confirmation.items()
                        if now - rec.created_at > self._permission_timeout
                    ]
                    for aid in expired_conf:
                        record = self._actions_waiting_confirmation.pop(aid, None)
                        if record:
                            self._actions.pop(aid, None)
                            record.status = "timeout"
                            record.error = "Confirmation request timed out"
                            record.completed_at = datetime.now(timezone.utc).isoformat()
                            self._history.append(record)
                            logger.warning("Confirmation timeout: %s (%s)", aid, record.action)

                    self._prune_history()
        except asyncio.CancelledError:
            pass

    async def save_state(self) -> None:
        import json
        from pathlib import Path

        async with self._save_lock:
            dest_dir = Path(self._state_path)
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = dest_dir / "automation_state.enc"
            temp_path = dest_dir / f"automation_state.enc.tmp.{uuid.uuid4().hex}"

            async with self._lock:
                data = {
                    "actions": {aid: rec.to_dict() for aid, rec in self._actions.items()},
                    "actions_by_permission": dict(self._actions_by_permission),
                    "actions_waiting_confirmation": {aid: rec.to_dict() for aid, rec in self._actions_waiting_confirmation.items()},
                    "history": [r.to_dict() for r in self._history],
                }
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
                    if not isinstance(validated_data, dict) or "history" not in validated_data or "actions" not in validated_data:
                        raise ValueError("Validated temporary state missing expected structure")

                    os.replace(temp_path, dest_path)

                await asyncio.to_thread(_write_and_replace)
            except Exception as e:
                logger.error("Failed to save automation state: %s", e)
                raise
            finally:
                if temp_path.exists():
                    try:
                        temp_path.unlink()
                    except OSError:
                        pass

    async def load_state(self) -> None:
        import json
        enc_path = os.path.join(self._state_path, "automation_state.enc")
        legacy_path = os.path.join(self._state_path, "automation_state.json")
        path_to_read = enc_path if os.path.exists(enc_path) else (legacy_path if os.path.exists(legacy_path) else None)
        if not path_to_read:
            return

        try:
            with open(path_to_read, "rb") as f:
                raw_bytes = f.read()
            json_str = decrypt_string(raw_bytes)
            data = json.loads(json_str)

            recovered_actions: list[ActionRecord] = []
            async with self._lock:
                for entry in data.get("history", []):
                    entry_uid = entry.get("user_id")
                    if not entry_uid or not isinstance(entry_uid, str) or not entry_uid.strip():
                        entry_uid = None
                    record = ActionRecord(
                        action_id=entry.get("action_id", ""),
                        action=entry.get("action", ""),
                        permission_id=entry.get("permission_id"),
                        target=entry.get("target"),
                        params=entry.get("params", {}),
                        risk_level=entry.get("risk_level", "low"),
                        plan_id=entry.get("plan_id"),
                        step_id=entry.get("step_id"),
                        user_id=entry_uid,
                    )
                    record.status = entry.get("status", "completed")
                    record.result = entry.get("result")
                    record.error = entry.get("error")
                    record.created_at_iso = entry.get("created_at", "")
                    record.completed_at = entry.get("completed_at")
                    self._history.append(record)

                active_dict = data.get("actions", {})
                for aid, entry in active_dict.items():
                    entry_uid = entry.get("user_id")
                    if not entry_uid or not isinstance(entry_uid, str) or not entry_uid.strip():
                        entry_uid = None
                    record = ActionRecord(
                        action_id=entry.get("action_id", aid),
                        action=entry.get("action", ""),
                        permission_id=entry.get("permission_id"),
                        target=entry.get("target"),
                        params=entry.get("params", {}),
                        risk_level=entry.get("risk_level", "low"),
                        plan_id=entry.get("plan_id"),
                        step_id=entry.get("step_id"),
                        user_id=entry_uid,
                    )
                    st = entry.get("status", "executing")
                    if st in ("executing", "waiting_permission", "waiting_confirmation", "pending"):
                        record.status = "failed"
                        record.error = "Interrupted by system restart"
                        record.completed_at = datetime.now(timezone.utc).isoformat()
                        self._history.append(record)
                        recovered_actions.append(record)

                self._prune_history()

            logger.info("Loaded %d history records from automation state", len(self._history))
            for r_act in recovered_actions:
                logger.warning("Recovered interrupted action on startup: %s (%s)", r_act.action_id, r_act.action)
                await self._event_bus.publish_event(
                    source="automation_engine",
                    event_type="automation.failed",
                    payload={
                        "action_id": r_act.action_id,
                        "action": r_act.action,
                        "error": "Interrupted by system restart",
                        "user_id": r_act.user_id,
                    },
                )
            if recovered_actions:
                try:
                    await self.save_state()
                except Exception as e:
                    logger.error("Failed to save state after recovering actions on startup: %s", e)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("Failed to load automation state: %s", e)

    def get_action(self, action_id: str, user_id: str | None = None) -> ActionRecord | None:
        if not user_id or not isinstance(user_id, str) or not user_id.strip():
            return None
        target_uid = user_id.strip()
        record = self._actions.get(action_id)
        if not record:
            record = self._actions_waiting_confirmation.get(action_id)
        if not record:
            for r in reversed(self._history):
                if r.action_id == action_id:
                    record = r
                    break
        if not record:
            return None
        if record.user_id != target_uid:
            return None
        return record

    def get_history(self, user_id: str | None = None, limit: int = 50) -> list[ActionRecord]:
        if not user_id or not isinstance(user_id, str) or not user_id.strip():
            return []
        if limit <= 0:
            return []
        target_uid = user_id.strip()
        user_history = [r for r in self._history if r.user_id == target_uid]
        return user_history[-limit:]

    def _prune_history(self) -> None:
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
