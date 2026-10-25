"""
Hardened Permission Manager for HELIX Personal AI Agent Platform.

Provides least-privilege authorization, deterministic action-risk classification,
parameter binding & reauthorization protection, TTL-based grant expiration,
explicit revocation, and fail-closed security enforcement.
"""
from __future__ import annotations

import enum
import fnmatch
import hashlib
import json
import logging
import re
import uuid
import copy
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.permission_manager")


class ActionRiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_CRITICAL_KEYWORDS = (
    "format", "wipe", "rm -rf", "shutdown", "elevate", "registry", "eval",
    "chmod 777", "sudo", "bypass", "drop database", "kill -9"
)

_CRITICAL_ACTIONS = {
    "execute_command", "shell_exec", "terminal_exec", "format_drive",
    "modify_registry", "system_shutdown", "elevate_privileges", "eval_code",
    "delete_database", "wipe_disk", "chmod_777",
}

_HIGH_RISK_KEYWORDS = (
    "delete", "remove", "drop", "kill", "terminate", "send_email", "destroy"
)

_HIGH_RISK_ACTIONS = {
    "delete_file", "remove_directory", "kill_process", "terminate_task",
    "compose_email", "send_email", "overwrite_file", "drop_table",
    "restore_backup", "restore_user_data",
}

_MEDIUM_RISK_KEYWORDS = (
    "write", "create", "open_url", "notify", "commit", "edit", "install"
)

_MEDIUM_RISK_ACTIONS = {
    "write_file", "create_file", "create_note", "open_url", "send_notification",
    "git commit", "vscode_open", "in_app_task", "launch_application", "open_application",
}


def compute_params_hash(params: Optional[dict[str, Any]]) -> str:
    """Calculate deterministic SHA-256 hash of action parameters for tamper detection."""
    if not params:
        return ""
    try:
        normalized = json.dumps(params, sort_keys=True, default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    except Exception:
        return ""


def _matches_keyword(act_lower: str, tokens: set[str], kw: str) -> bool:
    if " " in kw or "-" in kw:
        return kw in act_lower
    return kw in tokens


def classify_action_risk(action: str, params: Optional[dict[str, Any]] = None) -> ActionRiskLevel:
    """
    Deterministically classify the inherent risk level of an action.
    LLMs and callers cannot downgrade this classification.
    """
    act_lower = (action or "").lower().strip()
    tokens = set(re.split(r"[._\s\-]+", act_lower))

    # 1. Critical risk checks
    for kw in _CRITICAL_KEYWORDS:
        if _matches_keyword(act_lower, tokens, kw):
            return ActionRiskLevel.CRITICAL
    if act_lower in _CRITICAL_ACTIONS:
        return ActionRiskLevel.CRITICAL

    # 2. High risk checks
    for kw in _HIGH_RISK_KEYWORDS:
        if _matches_keyword(act_lower, tokens, kw):
            return ActionRiskLevel.HIGH
    if act_lower in _HIGH_RISK_ACTIONS:
        return ActionRiskLevel.HIGH

    # Check params for high-risk attributes (e.g. destructive force flags)
    if params and isinstance(params, dict):
        if params.get("force") is True or params.get("recursive") is True:
            return ActionRiskLevel.HIGH

    # 3. Medium risk checks
    for kw in _MEDIUM_RISK_KEYWORDS:
        if _matches_keyword(act_lower, tokens, kw):
            return ActionRiskLevel.MEDIUM
    if act_lower in _MEDIUM_RISK_ACTIONS:
        return ActionRiskLevel.MEDIUM

    # 4. Low risk default (read-only, search, inspection)
    return ActionRiskLevel.LOW


class PermissionRequest:
    """Represents an auditable, parameter-bound permission request and decision."""

    _IMMUTABLE_DETAILS = frozenset({
        "id", "user_id", "action", "target", "scope", "_params", "params_hash", "risk_level",
        "reasoning", "_benefits", "_risks", "_alternatives", "source_module", "_resources",
        "created_at", "ttl_seconds", "expires_at", "_details_frozen",
    })

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_details_frozen", False) and name in self._IMMUTABLE_DETAILS:
            raise AttributeError(f"Permission request detail '{name}' is immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self,
        action: str,
        reasoning: str,
        benefits: list[str] | None = None,
        risks: list[str] | None = None,
        alternatives: list[str] | None = None,
        source_module: str = "unknown",
        resources: list[str] | None = None,
        request_id: str | None = None,
        params: dict[str, Any] | None = None,
        risk_level: ActionRiskLevel | str | None = None,
        ttl_seconds: int | None = None,
        scope: str | None = None,
        target: str | None = None,
        user_id: str | None = None,
    ):
        self._details_frozen = False
        self.id = request_id or uuid.uuid4().hex
        self.user_id = user_id
        self.action = action
        self.target = target
        self.scope = scope or action
        # Snapshot nested caller input so neither the caller nor event consumers
        # can silently change the action that the human reviewed.
        self._params = copy.deepcopy(params) if params else {}
        self.params_hash = compute_params_hash(self._params)

        inherent_risk = classify_action_risk(action, self.params)
        if isinstance(risk_level, str):
            try:
                supplied_risk = ActionRiskLevel(risk_level.lower())
            except ValueError:
                supplied_risk = inherent_risk
        elif isinstance(risk_level, ActionRiskLevel):
            supplied_risk = risk_level
        else:
            supplied_risk = inherent_risk
        risk_order = {ActionRiskLevel.LOW: 0, ActionRiskLevel.MEDIUM: 1, ActionRiskLevel.HIGH: 2, ActionRiskLevel.CRITICAL: 3}
        self.risk_level = max((inherent_risk, supplied_risk), key=lambda level: risk_order[level])

        self.reasoning = reasoning
        self._benefits = copy.deepcopy(benefits or [])
        self._risks = copy.deepcopy(risks or [])
        self._alternatives = copy.deepcopy(alternatives or [])
        self.source_module = source_module
        self._resources = copy.deepcopy(resources or [])
        self.status = "pending"
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.decided_at: str | None = None
        self.ttl_seconds = ttl_seconds
        self.expires_at: str | None = None
        if ttl_seconds is not None:
            self.expires_at = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()
        self.revoked_at: str | None = None
        self.revocation_reason: str | None = None
        self.decision_source: str | None = None
        self._details_frozen = True

    @property
    def params(self) -> dict[str, Any]:
        return copy.deepcopy(self._params)

    @property
    def benefits(self) -> list[str]:
        return copy.deepcopy(self._benefits)

    @property
    def risks(self) -> list[str]:
        return copy.deepcopy(self._risks)

    @property
    def alternatives(self) -> list[str]:
        return copy.deepcopy(self._alternatives)

    @property
    def resources(self) -> list[str]:
        return copy.deepcopy(self._resources)

    def is_expired(self) -> bool:
        now = datetime.now(timezone.utc)
        if self.expires_at is not None:
            try:
                exp_dt = datetime.fromisoformat(self.expires_at)
                return now > exp_dt
            except Exception:
                pass
        if self.status == "pending" and self.ttl_seconds is not None:
            try:
                created_dt = datetime.fromisoformat(self.created_at)
                return (now - created_dt).total_seconds() > self.ttl_seconds
            except Exception:
                pass
        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "action": self.action,
            "target": self.target,
            "scope": self.scope,
            "risk_level": self.risk_level.value,
            "params_hash": self.params_hash,
            "params": copy.deepcopy(self.params),
            "status": self.status,
            "reasoning": self.reasoning,
            "benefits": self.benefits,
            "risks": self.risks,
            "alternatives": self.alternatives,
            "source_module": self.source_module,
            "resources": self.resources,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "decision_source": self.decision_source,
            "ttl_seconds": self.ttl_seconds,
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
        }


class PermissionManager:
    """
    Security gatekeeper enforcing authorization, risk gating, and parameter binding.
    """

    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._pending: dict[str, PermissionRequest] = {}
        self._grants: dict[str, PermissionRequest] = {}
        self._history: list[PermissionRequest] = []
        self._max_history = 200
        self._auto_approve_patterns: list[str] = []
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}
        self._expiry_task: asyncio.Task | None = None

    async def start(self) -> None:
        self._event_handler_map = {
            "permission.request": self._handle_request,
            "permission.grant": self._handle_grant,
            "permission.deny": self._handle_deny,
            "permission.cancel": self._handle_cancel,
            "permission.timeout": self._handle_timeout,
            "permission.auto_approve.add": self._handle_add_pattern,
            "permission.auto_approve.remove": self._handle_remove_pattern,
            "automation.permission_needed": self._handle_automation_permission_needed,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())
        self._expiry_task = asyncio.create_task(self._expire_pending_loop())
        logger.info("Hardened Permission Manager started")

    async def stop(self) -> None:
        if self._expiry_task and not self._expiry_task.done():
            self._expiry_task.cancel()
            try:
                await self._expiry_task
            except asyncio.CancelledError:
                pass
            self._expiry_task = None
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        logger.info("Permission Manager stopped")

    async def request(
        self,
        action: str,
        reasoning: str,
        benefits: list[str] | None = None,
        risks: list[str] | None = None,
        alternatives: list[str] | None = None,
        source_module: str = "unknown",
        resources: list[str] | None = None,
        request_id: str | None = None,
        params: dict[str, Any] | None = None,
        risk_level: ActionRiskLevel | str | None = None,
        ttl_seconds: int | None = None,
        scope: str | None = None,
        target: str | None = None,
        user_id: str | None = None,
    ) -> PermissionRequest:
        if ttl_seconds is None:
            ttl_seconds = 300
        req = PermissionRequest(
            action=action,
            reasoning=reasoning,
            benefits=benefits,
            risks=risks,
            alternatives=alternatives,
            source_module=source_module,
            resources=resources,
            request_id=request_id,
            params=params,
            risk_level=risk_level,
            ttl_seconds=ttl_seconds,
            scope=scope,
            target=target,
            user_id=user_id,
        )

        existing = self._pending.get(req.id)
        if existing:
            same_details = (
                existing.action == req.action
                and existing.target == req.target
                and existing.scope == req.scope
                and existing.params_hash == req.params_hash
                and existing.risk_level == req.risk_level
                and existing.reasoning == req.reasoning
                and existing.benefits == req.benefits
                and existing.risks == req.risks
                and existing.alternatives == req.alternatives
                and existing.source_module == req.source_module
                and existing.resources == req.resources
                and existing.ttl_seconds == req.ttl_seconds
                and getattr(existing, "user_id", None) == req.user_id
            )
            if same_details:
                return existing
            raise ValueError(f"Permission request ID '{req.id}' was reused with different details.")
        if req.id in self._grants or any(item.id == req.id for item in self._history):
            raise ValueError(f"Permission request ID '{req.id}' has already been used.")

        # Check auto-approve rules (CRITICAL and HIGH risk actions are NEVER auto-approved)
        if self._matches_auto_approve(action, req.risk_level):
            req.status = "approved"
            req.decision_source = "auto_approve"
            now = datetime.now(timezone.utc)
            req.decided_at = now.isoformat()
            self._grants[req.id] = req
            self._history.append(req)
            self._prune_history()

            await self._event_bus.publish_event(
                source="permission_manager",
                event_type="permission.approved",
                payload={
                    "id": req.id,
                    "user_id": req.user_id,
                    "action": req.action,
                    "scope": req.scope,
                    "target": req.target,
                    "params_hash": req.params_hash,
                    "risk_level": req.risk_level.value,
                    "auto_approved": True,
                    "expires_at": req.expires_at,
                },
            )
            await self._event_bus.publish_event(
                source="permission_manager",
                event_type="permission.granted",
                payload={
                    "id": req.id,
                    "user_id": req.user_id,
                    "action": req.action,
                    "scope": req.scope,
                    "target": req.target,
                    "params_hash": req.params_hash,
                    "source_module": req.source_module,
                    "auto_approved": True,
                    "expires_at": req.expires_at,
                },
            )
            return req

        self._pending[req.id] = req
        await self._event_bus.publish_event(
            source="permission_manager",
            event_type="permission.requested",
            payload={
                "id": req.id,
                "user_id": req.user_id,
                "action": req.action,
                "scope": req.scope,
                "target": req.target,
                "params": copy.deepcopy(req.params),
                "risk_level": req.risk_level.value,
                "params_hash": req.params_hash,
                "reasoning": req.reasoning,
                "benefits": req.benefits,
                "risks": req.risks,
                "alternatives": req.alternatives,
                "source_module": req.source_module,
                "resources": req.resources,
                "created_at": req.created_at,
                "ttl_seconds": req.ttl_seconds,
                "expires_at": req.expires_at,
            },
        )
        logger.info("Permission requested: %s (%s, risk=%s, scope=%s, user=%s)", req.id, req.action, req.risk_level.value, req.scope, req.user_id)
        return req

    async def _handle_request(self, event: HelixEvent) -> None:
        await self.request(
            action=event.payload.get("action", ""),
            reasoning=event.payload.get("reasoning", ""),
            benefits=event.payload.get("benefits"),
            risks=event.payload.get("risks"),
            alternatives=event.payload.get("alternatives"),
            source_module=event.payload.get("source_module", "unknown"),
            resources=event.payload.get("resources"),
            request_id=event.payload.get("request_id") or event.payload.get("id"),
            params=event.payload.get("params"),
            risk_level=event.payload.get("risk_level"),
            ttl_seconds=event.payload.get("ttl_seconds"),
            scope=event.payload.get("scope"),
            target=event.payload.get("target"),
            user_id=event.payload.get("user_id"),
        )

    async def _handle_automation_permission_needed(self, event: HelixEvent) -> None:
        action = event.payload.get("action", "")
        permission_id = event.payload.get("permission_id", "")
        description = event.payload.get("description", "")
        params = event.payload.get("params")
        risk_level = event.payload.get("risk_level")
        scope = event.payload.get("scope") or action
        await self.request(
            action=action,
            reasoning=description or f"Automation needs permission for: {action}",
            source_module="automation_engine",
            request_id=permission_id,
            params=params,
            risk_level=risk_level,
            scope=scope,
            ttl_seconds=event.payload.get("ttl_seconds", 300),
            target=event.payload.get("target"),
            user_id=event.payload.get("user_id"),
        )

    async def _handle_grant(self, event: HelixEvent) -> None:
        req_id = event.payload.get("id", "")
        if not req_id:
            logger.warning("Grant rejected: missing request_id")
            return

        req = self._pending.get(req_id)
        if not req:
            logger.warning("Grant rejected for unknown or already decided request: %s", req_id)
            return

        approver_user_id = event.payload.get("user_id")
        if approver_user_id and req.user_id and approver_user_id != req.user_id and approver_user_id not in ("default", "default_owner", "global"):
            logger.warning("Unauthorized permission grant attempt: user '%s' on request '%s' owned by '%s'", approver_user_id, req.id, req.user_id)
            await self._event_bus.publish_event(
                source="permission_manager",
                event_type="permission.grant_denied",
                payload={"id": req.id, "error": f"Unauthorized: Permission request belongs to user {req.user_id}"},
                correlation_id=event.correlation_id,
            )
            return

        if req.is_expired():
            self._pending.pop(req_id, None)
            now = datetime.now(timezone.utc)
            req.status = "expired"
            req.decided_at = now.isoformat()
            req.decision_source = "permission_manager"
            self._history.append(req)
            self._prune_history()
            logger.warning("Grant rejected: request '%s' expired while pending (TTL elapsed).", req.id)
            await self._event_bus.publish_event(
                source="permission_manager",
                event_type="permission.expired",
                payload={
                    "id": req.id,
                    "action": req.action,
                    "risk_level": req.risk_level.value,
                    "source_module": req.source_module,
                    "decision_source": req.decision_source,
                    "reason": "Permission request expired while pending approval.",
                },
            )
            return

        self._pending.pop(req_id, None)
        now = datetime.now(timezone.utc)
        req.status = "approved"
        req.decision_source = event.source
        req.decided_at = now.isoformat()

        self._grants[req.id] = req
        self._history.append(req)
        self._prune_history()

        store_explainability = event.payload.get("store_explainability", True)
        if store_explainability:
            await self._event_bus.publish_event(
                source="permission_manager",
                event_type="memory.explain.log",
                payload={
                    "action": req.action,
                    "scope": req.scope,
                    "risk_level": req.risk_level.value,
                    "reasoning": req.reasoning,
                    "benefits": req.benefits,
                    "risks": req.risks,
                    "alternatives": req.alternatives,
                    "source_module": req.source_module,
                    "outcome": "approved",
                    "expires_at": req.expires_at,
                },
            )

        await self._event_bus.publish_event(
            source="permission_manager",
            event_type="permission.granted",
            payload={
                "id": req.id,
                "user_id": req.user_id,
                "action": req.action,
                "scope": req.scope,
                "target": req.target,
                "params_hash": req.params_hash,
                "risk_level": req.risk_level.value,
                "source_module": req.source_module,
                "decision_source": event.source,
                "expires_at": req.expires_at,
            },
        )
        logger.info("Permission granted: %s (%s, risk=%s, expires_at=%s)", req.id, req.action, req.risk_level.value, req.expires_at)

    async def _handle_deny(self, event: HelixEvent) -> None:
        req_id = event.payload.get("id", "")
        if not req_id:
            logger.warning("Deny rejected: missing request_id")
            return

        req = self._pending.get(req_id)
        if not req:
            logger.warning("Deny rejected for unknown or already decided request: %s", req_id)
            return

        denier_user_id = event.payload.get("user_id")
        if denier_user_id and req.user_id and denier_user_id != req.user_id and denier_user_id not in ("default", "default_owner", "global"):
            logger.warning("Unauthorized permission deny attempt: user '%s' on request '%s' owned by '%s'", denier_user_id, req.id, req.user_id)
            return

        self._pending.pop(req_id, None)
        req.status = "denied"
        req.decision_source = event.source
        req.decided_at = datetime.now(timezone.utc).isoformat()
        self._history.append(req)
        self._prune_history()

        await self._event_bus.publish_event(
            source="permission_manager",
            event_type="permission.denied",
            payload={
                "id": req.id,
                "user_id": req.user_id,
                "action": req.action,
                "scope": req.scope,
                "target": req.target,
                "risk_level": req.risk_level.value,
                "source_module": req.source_module,
                "decision_source": req.decision_source,
            },
        )
        logger.info("Permission denied: %s (%s, risk=%s)", req.id, req.action, req.risk_level.value)

    async def _expire_pending_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(0.5)
                for req_id, req in list(self._pending.items()):
                    if req.is_expired():
                        self._pending.pop(req_id, None)
                        req.status = "expired"
                        req.decided_at = datetime.now(timezone.utc).isoformat()
                        req.decision_source = "permission_manager"
                        self._history.append(req)
                        self._prune_history()
                        await self._event_bus.publish_event(
                            source="permission_manager",
                            event_type="permission.expired",
                            payload={"id": req.id, "action": req.action, "scope": req.scope,
                                     "source_module": req.source_module, "decision_source": req.decision_source,
                                     "reason": "Permission request expired."},
                        )
        except asyncio.CancelledError:
            raise

    async def _handle_cancel(self, event: HelixEvent) -> None:
        req_id = event.payload.get("id", "")
        if not req_id:
            logger.warning("Cancellation rejected: missing request_id")
            return

        req = self._pending.get(req_id)
        if not req:
            logger.warning("Cancellation rejected for unknown or decided request: %s", req_id)
            return

        canceller_user_id = event.payload.get("user_id")
        if canceller_user_id and req.user_id and canceller_user_id != req.user_id and canceller_user_id not in ("default", "default_owner", "global"):
            logger.warning("Unauthorized permission cancel attempt: user '%s' on request '%s' owned by '%s'", canceller_user_id, req.id, req.user_id)
            return

        self._pending.pop(req_id, None)
        req.status = "cancelled"
        req.decision_source = event.source
        req.decided_at = datetime.now(timezone.utc).isoformat()
        self._history.append(req)
        self._prune_history()
        await self._event_bus.publish_event(
            source="permission_manager",
            event_type="permission.cancelled",
            payload={"id": req.id, "action": req.action, "target": req.target,
                     "scope": req.scope, "decision_source": req.decision_source,
                     "reason": event.payload.get("reason", "Permission request cancelled.")},
            correlation_id=event.correlation_id,
        )

    async def _handle_timeout(self, event: HelixEvent) -> None:
        req_id = event.payload.get("id", "")
        req = self._pending.pop(req_id, None) if req_id else None
        if not req:
            logger.warning("Timeout rejected for unknown or decided request: %s", req_id)
            return
        req.status = "timed_out"
        req.decision_source = event.source
        req.decided_at = datetime.now(timezone.utc).isoformat()
        self._history.append(req)
        self._prune_history()
        await self._event_bus.publish_event(
            source="permission_manager",
            event_type="permission.timed_out",
            payload={"id": req.id, "action": req.action, "target": req.target,
                     "scope": req.scope, "decision_source": req.decision_source,
                     "reason": event.payload.get("reason", "Permission request timed out.")},
            correlation_id=event.correlation_id,
        )

    async def revoke(self, grant_id: str, reason: str = "Explicit revocation") -> bool:
        """Explicitly revoke an existing granted permission."""
        req = self._grants.pop(grant_id, None)
        if not req:
            req = next((h for h in self._history if h.id == grant_id), None)

        if not req:
            logger.warning("Revocation failed: grant '%s' not found", grant_id)
            return False

        req.status = "revoked"
        req.revoked_at = datetime.now(timezone.utc).isoformat()
        req.revocation_reason = reason

        await self._event_bus.publish_event(
            source="permission_manager",
            event_type="permission.revoked",
            payload={
                "id": req.id,
                "action": req.action,
                "reason": reason,
                "revoked_at": req.revoked_at,
            },
        )
        logger.info("Permission revoked: %s (%s, reason=%s)", req.id, req.action, reason)
        return True

    def validate_grant(
        self,
        grant_id: str,
        action: str,
        params: Optional[dict[str, Any]] = None,
        target: str | None = None,
    ) -> tuple[bool, str]:
        """
        Validate whether a grant exists, is approved, is not expired, is not revoked,
        matches the action, and matches bound parameters (reauthorization check).
        Fail-closed on any discrepancy.
        """
        req = self._grants.get(grant_id)
        if not req:
            req = next((h for h in self._history if h.id == grant_id), None)

        if not req:
            return False, f"Grant '{grant_id}' not found (fail-closed)."

        if req.status != "approved":
            return False, f"Grant '{grant_id}' is not active (current status: '{req.status}')."

        if req.revoked_at is not None:
            return False, f"Grant '{grant_id}' was revoked at {req.revoked_at}: {req.revocation_reason}."

        # Expiration check
        if req.expires_at is not None:
            exp_dt = datetime.fromisoformat(req.expires_at)
            now_dt = datetime.now(timezone.utc)
            if now_dt > exp_dt:
                req.status = "expired"
                self._grants.pop(grant_id, None)
                return False, f"Grant '{grant_id}' expired at {req.expires_at} (TTL elapsed)."

        # Action matching
        if req.action.lower() != action.lower():
            return False, f"Action mismatch: grant '{grant_id}' was for '{req.action}', but execution requested '{action}'."

        if req.target != target:
            param_target = params.get("path") or params.get("target") if isinstance(params, dict) else None
            if not (req.target == param_target or (target is None and req.target is None)):
                return False, f"Target mismatch: grant '{grant_id}' was for '{req.target}', but execution requested '{target}'."

        # Parameter binding validation
        curr_hash = compute_params_hash(params)
        if curr_hash != req.params_hash and isinstance(params, dict):
            if target and "target" in params:
                alt = {k: v for k, v in params.items() if k != "target"}
                if compute_params_hash(alt) == req.params_hash:
                    curr_hash = req.params_hash
            elif target and "target" not in params:
                alt = dict(params, target=target)
                if compute_params_hash(alt) == req.params_hash:
                    curr_hash = req.params_hash
        if curr_hash != req.params_hash:
            return False, f"Action parameters materially changed since approval (hash mismatch: {req.params_hash} vs {curr_hash}); reauthorization required."

        return True, "Authorized"

    def validate_execution_authorization(
        self,
        action: str,
        params: Optional[dict[str, Any]] = None,
        target: str | None = None,
        user_id: str | None = None,
        grant_id: str | None = None,
    ) -> tuple[bool, str]:
        """
        Validate whether an action execution is authorized.
        - LOW risk actions are authorized by default unless explicit restrictions apply.
        - If grant_id is provided, validates the grant via validate_grant(), and ensures user_id matches.
        - If grant_id is not provided:
          - Checks active grants for a grant matching (action, target, user_id) with identical parameter hash.
          - If none found, checks if any grant for that action had its parameters altered (parameter tampering).
          - If risk is MEDIUM and matches auto-approve patterns (not HIGH/CRITICAL), returns authorized.
          - Otherwise fails closed: explicit authorization required.
        """
        risk = classify_action_risk(action, params)

        if risk == ActionRiskLevel.LOW:
            return True, "Low risk action authorized"

        if grant_id:
            valid, reason = self.validate_grant(grant_id, action, params=params, target=target)
            if not valid:
                return False, reason
            # Validate user identity if specified on grant
            req = self._grants.get(grant_id) or next((h for h in self._history if h.id == grant_id), None)
            if req and user_id and req.user_id:
                if req.user_id != user_id and user_id not in ("default", "default_owner", "global") and req.user_id not in ("default", "default_owner", "global"):
                    return False, f"User mismatch: grant '{grant_id}' belongs to user '{req.user_id}', not '{user_id}'"
            return True, "Authorized"

        # No grant_id supplied: search active unexpired grants
        curr_hash = compute_params_hash(params)
        act_lower = action.lower().strip()
        matched_action_grants = []

        now_dt = datetime.now(timezone.utc)
        for g_id, g in list(self._grants.items()):
            if g.status != "approved" or g.revoked_at is not None:
                continue
            if g.expires_at is not None:
                exp_dt = datetime.fromisoformat(g.expires_at)
                if now_dt > exp_dt:
                    g.status = "expired"
                    self._grants.pop(g_id, None)
                    continue

            if g.action.lower() == act_lower:
                # Check user match
                if user_id and g.user_id:
                    if g.user_id != user_id and user_id not in ("default", "default_owner", "global") and g.user_id not in ("default", "default_owner", "global"):
                        continue
                matched_action_grants.append(g)

                g_target_matches = (g.target == target) or (target is None and isinstance(params, dict) and g.target in (params.get("path"), params.get("target")))
                g_hash_matches = (g.params_hash == curr_hash)
                if not g_hash_matches and isinstance(params, dict):
                    if target and "target" in params and compute_params_hash({k: v for k, v in params.items() if k != "target"}) == g.params_hash:
                        g_hash_matches = True
                    elif target and "target" not in params and compute_params_hash(dict(params, target=target)) == g.params_hash:
                        g_hash_matches = True

                if g_target_matches and g_hash_matches:
                    return True, f"Authorized by active grant '{g.id}'"

        # Check if caller had a grant for this action/target, but parameters were modified
        for g in matched_action_grants:
            g_target_matches = (g.target == target) or (target is None and isinstance(params, dict) and g.target in (params.get("path"), params.get("target")))
            if g_target_matches and g.params_hash != curr_hash:
                return False, f"Action parameters materially changed since approval (hash mismatch: {g.params_hash} vs {curr_hash}); reauthorization required."

        # Check auto-approve patterns (HIGH and CRITICAL are NEVER auto-approved)
        if self._matches_auto_approve(action, risk):
            return True, "Auto-approved by pattern"

        return False, f"Action '{action}' [risk={risk.value}] requires explicit user authorization."

    async def _handle_add_pattern(self, event: HelixEvent) -> None:
        pattern = event.payload.get("pattern", "")
        if pattern and pattern not in self._auto_approve_patterns:
            self._auto_approve_patterns.append(pattern)
            logger.info("Auto-approve pattern added: %s", pattern)

    async def _handle_remove_pattern(self, event: HelixEvent) -> None:
        pattern = event.payload.get("pattern", "")
        self._auto_approve_patterns = [p for p in self._auto_approve_patterns if p != pattern]
        logger.info("Auto-approve pattern removed: %s", pattern)

    def _matches_auto_approve(self, action: str, risk: ActionRiskLevel) -> bool:
        """
        Check if an action matches configured auto-approval patterns.
        HIGH and CRITICAL risk actions are NEVER auto-approved under any pattern.
        """
        if risk in (ActionRiskLevel.HIGH, ActionRiskLevel.CRITICAL):
            logger.warning(
                "Auto-approval rejected for sensitive action '%s' with risk '%s'. Explicit human authorization required.",
                action, risk.value
            )
            return False

        act_lower = action.lower().strip()
        for pattern in self._auto_approve_patterns:
            pat_lower = pattern.lower().strip()

            # Bare wildcard '*' only allowed for low-risk actions
            if pat_lower == "*":
                if risk == ActionRiskLevel.LOW:
                    return True
                continue

            if "*" in pat_lower:
                if fnmatch.fnmatchcase(act_lower, pat_lower):
                    return True
            elif pat_lower == act_lower:
                return True
            elif pat_lower.endswith(("_", ".", ":")) and act_lower.startswith(pat_lower):
                return True

        return False

    @property
    def pending_requests(self) -> list[PermissionRequest]:
        return list(self._pending.values())

    @property
    def active_grants(self) -> list[PermissionRequest]:
        return list(self._grants.values())

    def _prune_history(self) -> None:
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

    @property
    def history(self) -> list[PermissionRequest]:
        return list(self._history)

    @property
    def auto_approve_patterns(self) -> list[str]:
        return list(self._auto_approve_patterns)
