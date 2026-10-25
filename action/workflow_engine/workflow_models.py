"""
Workflow Models for HELIX P0.2 Workflow Engine.
Defines canonical state enums, step/workflow dataclasses, and parameter hashing.
"""
from __future__ import annotations

import copy
import enum
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional


class WorkflowException(Exception):
    """Base exception for all workflow engine errors."""
    pass


class InvalidWorkflowDefinitionException(WorkflowException):
    """Raised when a workflow graph definition fails pre-admission structural checks."""
    pass


class ParameterHashingException(WorkflowException):
    """Raised when parameters cannot be deterministically serialized or hashed."""
    pass


class WorkflowStatus(str, enum.Enum):
    """Authoritative 9-state enumeration for Workflow lifecycle."""
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    FAILING = "FAILING"
    CANCELING = "CANCELING"
    PAUSED_UNCERTAIN = "PAUSED_UNCERTAIN"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class StepStatus(str, enum.Enum):
    """Authoritative 13-state enumeration for Step lifecycle."""
    PENDING = "PENDING"
    READY = "READY"
    CLAIMED = "CLAIMED"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DENIED = "DENIED"
    TIMED_OUT = "TIMED_OUT"
    CANCELED = "CANCELED"
    BLOCKED = "BLOCKED"
    UNCERTAIN = "UNCERTAIN"


def compute_step_params_hash(params: Optional[dict[str, Any]]) -> str:
    """
    Calculate deterministic 16-character SHA-256 hash of action parameters.
    Preserves exact compatibility with foundation.permission_manager.compute_params_hash.
    Fails closed by raising ParameterHashingException on non-serializable parameters.
    """
    if not params:
        return ""
    try:
        normalized = json.dumps(params, sort_keys=True, default=str)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    except (TypeError, ValueError, RecursionError) as e:
        raise ParameterHashingException(f"Failed to deterministically hash parameters: {e}") from e
    except Exception as e:
        raise ParameterHashingException(f"Unexpected hashing failure: {e}") from e


@dataclass(frozen=True)
class OutboxEvent:
    """Immutable outbox event representing external interaction intent or completion."""
    event_id: str
    sequence_num: int
    timestamp: float
    tenant_id: str
    workflow_id: str
    step_id: str
    attempt_id: int
    event_type: str  # INTENT_RECORDED | DISPATCHED | RESULT_RECEIVED | VERIFIED | ABORTED | UNCERTAIN
    payload: dict[str, Any] = field(default_factory=dict)
    payload_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "sequence_num": self.sequence_num,
            "timestamp": self.timestamp,
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
            "step_id": self.step_id,
            "attempt_id": self.attempt_id,
            "event_type": self.event_type,
            "payload": self.payload,
            "payload_hash": self.payload_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutboxEvent:
        return cls(
            event_id=data["event_id"],
            sequence_num=data["sequence_num"],
            timestamp=data["timestamp"],
            tenant_id=data["tenant_id"],
            workflow_id=data["workflow_id"],
            step_id=data["step_id"],
            attempt_id=data["attempt_id"],
            event_type=data["event_type"],
            payload=data.get("payload", {}),
            payload_hash=data.get("payload_hash", ""),
        )


@dataclass
class WorkflowStep:
    """Individual node within a Workflow DAG."""
    step_id: str
    action: str
    target: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    risk_level: str = "low"
    status: StepStatus = StepStatus.PENDING
    attempt_id: int = 0
    permission_request_id: Optional[str] = None
    params_hash: str = ""
    residual_uncertainty: bool = False
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None

    def __post_init__(self):
        if isinstance(self.status, str):
            self.status = StepStatus(self.status)
        if not self.params_hash and self.params:
            self.params_hash = compute_step_params_hash(self.params)

    def clone(self) -> WorkflowStep:
        return copy.deepcopy(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action": self.action,
            "target": self.target,
            "params": self.params,
            "depends_on": list(self.depends_on),
            "risk_level": self.risk_level,
            "status": self.status.value,
            "attempt_id": self.attempt_id,
            "permission_request_id": self.permission_request_id,
            "params_hash": self.params_hash,
            "residual_uncertainty": self.residual_uncertainty,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStep:
        return cls(
            step_id=data["step_id"],
            action=data["action"],
            target=data.get("target", ""),
            params=data.get("params", {}),
            depends_on=data.get("depends_on", []),
            risk_level=data.get("risk_level", "low"),
            status=StepStatus(data.get("status", StepStatus.PENDING.value)),
            attempt_id=data.get("attempt_id", 0),
            permission_request_id=data.get("permission_request_id"),
            params_hash=data.get("params_hash", ""),
            residual_uncertainty=data.get("residual_uncertainty", False),
            created_at=data.get("created_at", time.time()),
            completed_at=data.get("completed_at"),
        )


@dataclass
class Workflow:
    """Authoritative DAG Workflow state model."""
    workflow_id: str
    tenant_id: str
    description: str = ""
    status: WorkflowStatus = WorkflowStatus.CREATED
    state_revision: int = 1
    steps: dict[str, WorkflowStep] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self):
        if isinstance(self.status, str):
            self.status = WorkflowStatus(self.status)

    def clone(self) -> Workflow:
        return copy.deepcopy(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "tenant_id": self.tenant_id,
            "description": self.description,
            "status": self.status.value,
            "state_revision": self.state_revision,
            "steps": {sid: step.to_dict() for sid, step in self.steps.items()},
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workflow:
        raw_steps = data.get("steps", {})
        steps = {sid: WorkflowStep.from_dict(sdata) for sid, sdata in raw_steps.items()}
        return cls(
            workflow_id=data["workflow_id"],
            tenant_id=data["tenant_id"],
            description=data.get("description", ""),
            status=WorkflowStatus(data.get("status", WorkflowStatus.CREATED.value)),
            state_revision=data.get("state_revision", 1),
            steps=steps,
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
        )
