"""
Pre-admission DAG validation for HELIX P0.2 Workflow Engine.
Enforces structural invariants: non-empty steps, non-blank IDs, unique IDs,
valid dependency references, no self-dependencies, and acyclicity.
"""
from __future__ import annotations

from typing import Any, Union
from action.workflow_engine.workflow_models import (
    WorkflowStep,
    InvalidWorkflowDefinitionException,
)


def validate_workflow_definition(
    steps: list[Union[dict[str, Any], WorkflowStep]]
) -> tuple[bool, str | None]:
    """
    Validate that plan/workflow steps satisfy all structural DAG invariants:
    1. Steps list is non-empty.
    2. Every step_id is a non-empty, non-whitespace string.
    3. Every step_id is unique within the workflow.
    4. All dependencies reference declared step_ids.
    5. No step depends on itself.
    6. No circular dependency chains exist.
    """
    if not steps:
        return False, "Workflow must contain at least one step."

    declared_step_ids: set[str] = set()
    step_records: list[tuple[str, list[str]]] = []

    for i, s in enumerate(steps):
        if isinstance(s, WorkflowStep):
            step_id = s.step_id
            depends_on = list(s.depends_on)
        elif isinstance(s, dict):
            step_id = s.get("step_id")
            depends_on = list(s.get("depends_on", []))
        else:
            return False, f"Invalid step item type at index {i}: expected dict or WorkflowStep."

        if not isinstance(step_id, str) or not step_id.strip():
            return False, f"Step ID cannot be blank at index {i}."

        clean_id = step_id.strip()
        if clean_id in declared_step_ids:
            return False, f"Duplicate step_id detected: '{clean_id}'."

        declared_step_ids.add(clean_id)
        step_records.append((clean_id, depends_on))

    # Dependency existence and self-dependency checks
    for sid, deps in step_records:
        for dep in deps:
            if not isinstance(dep, str) or not dep.strip():
                return False, f"Step '{sid}' has invalid empty dependency reference."
            clean_dep = dep.strip()
            if clean_dep == sid:
                return False, f"Self-dependency detected: Step '{sid}' depends on itself."
            if clean_dep not in declared_step_ids:
                return False, f"Missing dependency: Step '{sid}' depends on non-existent step '{clean_dep}'."

    # Cycle detection via Depth-First Search with 3-state coloring
    # 0 = unvisited, 1 = visiting (in active call stack), 2 = visited
    adj: dict[str, list[str]] = {sid: list(deps) for sid, deps in step_records}
    visited: dict[str, int] = {}

    def has_cycle(u: str) -> bool:
        visited[u] = 1
        for v in adj.get(u, []):
            clean_v = v.strip()
            if visited.get(clean_v, 0) == 1:
                return True
            if visited.get(clean_v, 0) == 0:
                if has_cycle(clean_v):
                    return True
        visited[u] = 2
        return False

    for sid, _ in step_records:
        if visited.get(sid, 0) == 0:
            if has_cycle(sid):
                return False, f"Circular dependency cycle detected involving step '{sid}'."

    return True, None


class WorkflowValidator:
    """Pre-admission validator interface."""

    @staticmethod
    def validate(steps: list[Union[dict[str, Any], WorkflowStep]]) -> tuple[bool, str | None]:
        return validate_workflow_definition(steps)

    @classmethod
    def validate_or_raise(cls, steps: list[Union[dict[str, Any], WorkflowStep]]) -> None:
        valid, error = cls.validate(steps)
        if not valid:
            raise InvalidWorkflowDefinitionException(error or "Invalid workflow definition.")
