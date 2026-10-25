"""
Long-Running Goal Manager for HELIX Personal AI Agent Platform.

Provides full goal lifecycle management, state transition validation,
atomic JSON persistence with corruption recovery, Conversation-to-Goal resolution,
and Goal-to-Plan execution tracking.
"""
from __future__ import annotations

import asyncio
import enum
import json
import logging
import os
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.goal_manager")


class GoalStatus(str, enum.Enum):
    CREATED = "created"
    CLARIFYING = "clarifying"
    READY = "ready"
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


_VALID_TRANSITIONS: dict[GoalStatus, set[GoalStatus]] = {
    GoalStatus.CREATED: {GoalStatus.CLARIFYING, GoalStatus.READY, GoalStatus.CANCELLED, GoalStatus.ARCHIVED},
    GoalStatus.CLARIFYING: {GoalStatus.READY, GoalStatus.CANCELLED, GoalStatus.FAILED, GoalStatus.ARCHIVED},
    GoalStatus.READY: {GoalStatus.PLANNED, GoalStatus.IN_PROGRESS, GoalStatus.CANCELLED, GoalStatus.ARCHIVED},
    GoalStatus.PLANNED: {GoalStatus.IN_PROGRESS, GoalStatus.COMPLETED, GoalStatus.BLOCKED, GoalStatus.PAUSED, GoalStatus.CANCELLED, GoalStatus.ARCHIVED},
    GoalStatus.IN_PROGRESS: {GoalStatus.BLOCKED, GoalStatus.PAUSED, GoalStatus.COMPLETED, GoalStatus.FAILED, GoalStatus.CANCELLED, GoalStatus.PLANNED, GoalStatus.ARCHIVED},
    GoalStatus.BLOCKED: {GoalStatus.IN_PROGRESS, GoalStatus.PAUSED, GoalStatus.FAILED, GoalStatus.CANCELLED, GoalStatus.PLANNED, GoalStatus.ARCHIVED},
    GoalStatus.PAUSED: {GoalStatus.IN_PROGRESS, GoalStatus.CANCELLED, GoalStatus.READY, GoalStatus.ARCHIVED},
    GoalStatus.COMPLETED: {GoalStatus.READY, GoalStatus.ARCHIVED},  # Explicit reopen or archive
    GoalStatus.FAILED: {GoalStatus.READY, GoalStatus.CANCELLED, GoalStatus.ARCHIVED},
    GoalStatus.CANCELLED: {GoalStatus.READY, GoalStatus.ARCHIVED},
    GoalStatus.ARCHIVED: {GoalStatus.READY, GoalStatus.CANCELLED},
}


@dataclass
class GoalTaskNode:
    task_id: str
    title: str
    description: str
    status: str = "pending"  # pending | in_progress | completed | failed | blocked | skipped
    depends_on: list[str] = field(default_factory=list)
    action: Optional[str] = None
    params: dict[str, Any] = field(default_factory=dict)
    completion_evidence: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LongRunningGoal:
    goal_id: str
    title: str
    description: str
    user_id: str = "default_owner"
    intent: str = ""
    success_criteria: list[str] = field(default_factory=list)
    priority: str = "medium"  # low | medium | high | urgent
    progress_percent: float = 0.0
    tasks: list[GoalTaskNode] = field(default_factory=list)
    status: str = GoalStatus.IN_PROGRESS.value
    plan_id: Optional[str] = None
    deadline: Optional[str] = None
    constraints: list[str] = field(default_factory=list)
    completion_evidence: list[str] = field(default_factory=list)
    clarification_questions: list[str] = field(default_factory=list)
    clarification_answers: list[str] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["tasks"] = [t.to_dict() if isinstance(t, GoalTaskNode) else t for t in self.tasks]
        return d


class GoalManager:
    """
    Manages long-running goals across conversations, plans, and autonomous execution,
    partitioned and authorized strictly per authenticated user.
    """

    def __init__(self, event_bus: EventBus, data_dir: str | Path | None = None):
        self._event_bus = event_bus
        if data_dir is not None:
            self._data_dir = Path(data_dir)
        else:
            env_goals = os.getenv("HELIX_GOALS_PATH")
            if env_goals and env_goals.strip():
                self._data_dir = Path(env_goals.strip())
            else:
                env_data = os.getenv("HELIX_DATA_DIR")
                if env_data and env_data.strip():
                    self._data_dir = Path(env_data.strip()) / "goals"
                else:
                    self._data_dir = Path("./data/goals")
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._goals_file = self._data_dir / "goals.json"
        self._bak_file = self._data_dir / "goals.json.bak"
        self._goals: dict[str, LongRunningGoal] = {}
        self._subscriptions: list[str] = []
        self._handler_map: dict[str, Any] = {}
        self._started: bool = False
        self._load_goals()

    @property
    def storage_path(self) -> Path:
        return self._data_dir

    async def start(self) -> None:
        if self._started:
            return
        self._handler_map = {
            "automation.result": self._handle_automation_result,
            "automation.failed": self._handle_automation_failed,
            "action.verified": self._handle_action_verified,
            "planner.plan_ready": self._handle_plan_ready,
        }
        for event_type, handler in self._handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._handler_map.keys())
        self._started = True
        logger.info("GoalManager started with event bus subscriptions")

    async def stop(self) -> None:
        if not self._started:
            return
        for event_type, handler in self._handler_map.items():
            try:
                self._event_bus.unsubscribe(event_type, handler)
            except Exception as e:
                logger.debug("Error unsubscribing %s: %s", event_type, e)
        self._handler_map.clear()
        self._subscriptions.clear()
        self._started = False
        logger.info("GoalManager stopped")

    def _load_goals(self) -> None:
        """Load goals with crash recovery and fallback backup parsing."""
        if not self._goals_file.exists():
            default_goal = LongRunningGoal(
                goal_id="goal-1",
                user_id="default_owner",
                title="Evolve HELIX into PAIOS",
                description="Transform HELIX into a production-grade Personal AI Operating System.",
                intent="Develop PCOS architecture with verification and long-running goals",
                success_criteria=["Subsystems integrated", "Full test coverage", "Zero false abstentions"],
                priority="high",
                progress_percent=85.0,
                status=GoalStatus.IN_PROGRESS.value,
                tasks=[
                    GoalTaskNode("t-1", "Real-Time Streaming Voice Engine", "Implement TTS sentence streaming", "completed"),
                    GoalTaskNode("t-2", "Self & World Model Subsystems", "Add state vector perception", "completed"),
                    GoalTaskNode("t-3", "Application Discovery & Project Registry", "Fuzzy index Windows software & dev workspaces", "completed"),
                    GoalTaskNode("t-4", "Action Verification & Goal Canvas UI", "Validate execution & build goals UI", "in_progress", ["t-1", "t-2", "t-3"]),
                ],
            )
            self._goals[default_goal.goal_id] = default_goal
            self._save_goals()
            return

        target_file = self._goals_file
        data: Optional[dict[str, Any]] = None

        try:
            with open(target_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning("Primary goals file corrupted or unreadable: %s. Attempting backup recovery.", e)
            if self._bak_file.exists():
                try:
                    with open(self._bak_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    logger.info("Successfully recovered goals from backup file %s", self._bak_file)
                except Exception as bak_err:
                    logger.exception("Backup recovery also failed: %s", bak_err)

        if not data or not isinstance(data, dict):
            logger.error("Unable to load any valid goals data. Initializing empty storage.")
            return

        for item in data.get("goals", []):
            try:
                tasks_raw = item.get("tasks", [])
                tasks = [
                    GoalTaskNode(
                        task_id=t.get("task_id", f"t-{i+1}"),
                        title=t.get("title", ""),
                        description=t.get("description", ""),
                        status=t.get("status", "pending"),
                        depends_on=t.get("depends_on", []),
                        action=t.get("action"),
                        params=t.get("params", {}),
                        completion_evidence=t.get("completion_evidence"),
                        started_at=t.get("started_at"),
                        completed_at=t.get("completed_at"),
                    )
                    for i, t in enumerate(tasks_raw)
                ]
                item["tasks"] = tasks
                item.setdefault("user_id", "default_owner")
                item.setdefault("intent", "")
                item.setdefault("success_criteria", [])
                item.setdefault("constraints", [])
                item.setdefault("completion_evidence", [])
                item.setdefault("clarification_questions", [])
                item.setdefault("clarification_answers", [])
                item.setdefault("provenance", [])
                item.setdefault("updated_at", item.get("created_at", datetime.now(timezone.utc).isoformat()))

                goal = LongRunningGoal(**item)
                self._goals[goal.goal_id] = goal
            except Exception as parse_err:
                logger.warning("Skipping malformed goal record: %s", parse_err)

    def _save_goals(self) -> None:
        """Atomic write with backup for crash-resistant persistence."""
        tmp_file = self._data_dir / f"goals.json.tmp.{os.getpid()}_{uuid.uuid4().hex[:6]}"
        try:
            payload = {
                "version": "1.0",
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "goals": [g.to_dict() for g in self._goals.values()],
            }
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            # Keep backup of previous state
            if self._goals_file.exists():
                try:
                    shutil.copy2(self._goals_file, self._bak_file)
                except Exception as bak_err:
                    logger.debug("Failed updating backup: %s", bak_err)

            # Atomic replace
            os.replace(tmp_file, self._goals_file)
            # Maintain updated backup reflecting the latest verified write
            try:
                shutil.copy2(self._goals_file, self._bak_file)
            except Exception as bak_err:
                logger.debug("Failed updating backup: %s", bak_err)
        except Exception as e:
            logger.exception("Failed atomic save to %s", self._goals_file)
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except Exception:
                    pass
            raise

    def create_goal(
        self,
        title: str,
        description: str = "",
        user_id: str = "default_owner",
        priority: str = "medium",
        tasks: list[dict] | None = None,
        intent: str = "",
        success_criteria: list[str] | None = None,
        constraints: list[str] | None = None,
        status: GoalStatus | str = GoalStatus.READY,
    ) -> LongRunningGoal:
        goal_id = f"goal-{len(self._goals) + 1}"
        task_nodes = []
        if tasks:
            for idx, t in enumerate(tasks):
                task_nodes.append(
                    GoalTaskNode(
                        task_id=t.get("task_id", f"t-{idx+1}"),
                        title=t.get("title", f"Task {idx+1}"),
                        description=t.get("description", ""),
                        status=t.get("status", "pending"),
                        depends_on=t.get("depends_on", []),
                        action=t.get("action"),
                        params=t.get("params", {}),
                    )
                )

        status_val = status.value if isinstance(status, GoalStatus) else str(status)
        goal = LongRunningGoal(
            goal_id=goal_id,
            user_id=user_id,
            title=title,
            description=description,
            intent=intent or description,
            success_criteria=success_criteria or [],
            priority=priority,
            tasks=task_nodes,
            status=status_val,
            constraints=constraints or [],
            provenance=[{
                "from_status": None,
                "to_status": status_val,
                "reason": "Goal created",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        )
        self._goals[goal_id] = goal
        self._save_goals()
        self._publish_event_safe("goal.created", goal.to_dict())
        return goal

    def get_goal(self, goal_id: str, user_id: str | None = None) -> LongRunningGoal | None:
        goal = self._goals.get(goal_id)
        if not goal:
            return None
        if user_id and goal.user_id != user_id:
            return None  # Strict tenant isolation boundary
        return goal

    def list_goals(
        self,
        user_id: str | None = None,
        include_archived: bool = False,
        archived_only: bool = False,
    ) -> list[dict[str, Any]]:
        goals = list(self._goals.values())
        if user_id:
            goals = [g for g in goals if g.user_id == user_id]

        if archived_only:
            goals = [g for g in goals if g.status == GoalStatus.ARCHIVED.value]
        elif not include_archived:
            goals = [g for g in goals if g.status != GoalStatus.ARCHIVED.value]

        return [g.to_dict() for g in goals]

    def transition_goal(
        self,
        goal_id: str,
        target_status: GoalStatus | str,
        reason: str = "",
        evidence: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> tuple[bool, str]:
        """
        Validate and apply a lifecycle transition.
        Enforces valid state machines and rejects unauthorized cross-user modifications.
        """
        goal = self._goals.get(goal_id)
        if not goal:
            return False, f"Goal '{goal_id}' not found."

        if user_id and goal.user_id != user_id:
            return False, f"Unauthorized: Goal '{goal_id}' does not belong to user '{user_id}'."

        if isinstance(target_status, str):
            try:
                target_status = GoalStatus(target_status.lower())
            except ValueError:
                return False, f"Unknown goal status: '{target_status}'"

        try:
            curr_status = GoalStatus(goal.status)
        except ValueError:
            curr_status = GoalStatus.IN_PROGRESS

        allowed = _VALID_TRANSITIONS.get(curr_status, set())
        if target_status not in allowed and target_status != curr_status:
            return False, f"Illegal state transition from '{curr_status.value}' to '{target_status.value}'."

        goal.status = target_status.value
        goal.updated_at = datetime.now(timezone.utc).isoformat()
        event_entry = {
            "from_status": curr_status.value,
            "to_status": target_status.value,
            "reason": reason,
            "evidence": evidence,
            "timestamp": goal.updated_at,
        }
        goal.provenance.append(event_entry)

        if evidence and target_status == GoalStatus.COMPLETED:
            goal.completion_evidence.append(evidence)
            goal.progress_percent = 100.0

        self._save_goals()
        self._publish_event_safe("goal.transitioned", {"goal_id": goal_id, "user_id": goal.user_id, **event_entry})
        return True, f"Transitioned to {target_status.value}"

    def ask_clarification(self, goal_id: str, question: str, user_id: Optional[str] = None) -> bool:
        """Place goal into CLARIFYING state awaiting user response."""
        goal = self.get_goal(goal_id, user_id=user_id)
        if not goal:
            return False
        goal.clarification_questions.append(question)
        success, _ = self.transition_goal(goal_id, GoalStatus.CLARIFYING, reason=f"Question: {question}", user_id=user_id)
        return success

    def answer_clarification(self, goal_id: str, answer: str, user_id: Optional[str] = None) -> bool:
        """Provide user answer to clarification and transition goal back to READY."""
        goal = self.get_goal(goal_id, user_id=user_id)
        if not goal:
            return False
        goal.clarification_answers.append(answer)
        goal.description = f"{goal.description}\n[Clarification: {answer}]"
        success, _ = self.transition_goal(goal_id, GoalStatus.READY, reason="Clarification answered by user", user_id=user_id)
        return success

    def pause_goal(self, goal_id: str, reason: str = "", user_id: Optional[str] = None) -> bool:
        success, _ = self.transition_goal(goal_id, GoalStatus.PAUSED, reason=reason or "User requested pause", user_id=user_id)
        return success

    def resume_goal(self, goal_id: str, user_id: Optional[str] = None) -> bool:
        success, _ = self.transition_goal(goal_id, GoalStatus.IN_PROGRESS, reason="User resumed goal", user_id=user_id)
        return success

    def cancel_goal(self, goal_id: str, reason: str = "", user_id: Optional[str] = None) -> bool:
        success, _ = self.transition_goal(goal_id, GoalStatus.CANCELLED, reason=reason or "User cancelled goal", user_id=user_id)
        return success

    def archive_goal(self, goal_id: str, reason: str = "", user_id: Optional[str] = None) -> tuple[bool, str]:
        """
        Archive a goal, preserving its state and history while removing it from default active lists.
        Enforces tenant authorization.
        """
        goal = self._goals.get(goal_id)
        if not goal:
            return False, f"Goal '{goal_id}' not found."
        if user_id and goal.user_id != user_id:
            return False, f"Unauthorized: Goal '{goal_id}' does not belong to user '{user_id}'."

        success, msg = self.transition_goal(
            goal_id,
            GoalStatus.ARCHIVED,
            reason=reason or "User archived goal",
            user_id=user_id,
        )
        if success:
            self._publish_event_safe("goal.archived", {"goal_id": goal_id, "user_id": goal.user_id, "title": goal.title})
        return success, msg

    def restore_goal(self, goal_id: str, user_id: Optional[str] = None) -> tuple[bool, str]:
        """
        Restore an archived goal back to active READY state.
        Enforces tenant authorization.
        """
        goal = self._goals.get(goal_id)
        if not goal:
            return False, f"Goal '{goal_id}' not found."
        if user_id and goal.user_id != user_id:
            return False, f"Unauthorized: Goal '{goal_id}' does not belong to user '{user_id}'."

        if goal.status != GoalStatus.ARCHIVED.value:
            return False, f"Goal '{goal_id}' is not archived (current status: {goal.status})."

        success, msg = self.transition_goal(
            goal_id,
            GoalStatus.READY,
            reason="User restored goal from archive",
            user_id=user_id,
        )
        if success:
            self._publish_event_safe("goal.restored", {"goal_id": goal_id, "user_id": goal.user_id, "title": goal.title})
        return success, msg

    def delete_goal(self, goal_id: str, user_id: Optional[str] = None) -> tuple[bool, str]:
        """
        Permanently delete a goal record from authoritative persistence.
        Revalidates ownership and goal existence.
        Safely cancels tasks and emits goal.deleted event for cleanup of dependent listeners.
        """
        goal = self._goals.get(goal_id)
        if not goal:
            return False, f"Goal '{goal_id}' not found."
        if user_id and goal.user_id != user_id:
            return False, f"Unauthorized: Goal '{goal_id}' does not belong to user '{user_id}'."

        # Policy: Clean up associated plan links and tasks safely
        for task in goal.tasks:
            task.status = "cancelled"

        deleted_goal = self._goals.pop(goal_id, None)
        if not deleted_goal:
            return False, f"Goal '{goal_id}' could not be deleted."

        try:
            self._save_goals()
        except Exception as e:
            # Rollback in-memory map if disk save fails
            self._goals[goal_id] = deleted_goal
            logger.exception("Failed to persist goal deletion to disk: %s", e)
            return False, f"Persistence failure: {str(e)}"

        # Verify removal
        if goal_id in self._goals:
            return False, f"Verification failed: goal '{goal_id}' remained after deletion."

        self._publish_event_safe("goal.deleted", {
            "goal_id": goal_id,
            "user_id": deleted_goal.user_id,
            "title": deleted_goal.title,
            "plan_id": deleted_goal.plan_id,
        })
        return True, f"Goal '{deleted_goal.title}' permanently deleted."

    def find_matching_goals(
        self,
        query: str,
        user_id: str | None = None,
        include_archived: bool = True,
    ) -> list[LongRunningGoal]:
        """
        Find matching goals for a user query.
        Returns a list of matching goals within the user's authorized scope.
        Supports exact ID match, exact title match, and substring keyword matching.
        """
        q_lower = query.lower().strip()
        if not q_lower:
            return []

        candidates = [
            g for g in self._goals.values()
            if not user_id or g.user_id == user_id
        ]
        if not include_archived:
            candidates = [g for g in candidates if g.status != GoalStatus.ARCHIVED.value]

        # 1. Exact ID match
        exact_id = [g for g in candidates if g.goal_id.lower() == q_lower]
        if exact_id:
            return exact_id

        # 2. Check for goal-N in text
        for g in candidates:
            if g.goal_id.lower() in q_lower:
                return [g]

        # 3. Exact Title match
        exact_title = [g for g in candidates if g.title.lower() == q_lower]
        if exact_title:
            return exact_title

        # 4. Substring match in title
        sub_matches = [g for g in candidates if q_lower in g.title.lower() or g.title.lower() in q_lower]
        return sub_matches

    def resolve_goal_reference(self, query: str, user_id: str | None = None) -> Optional[LongRunningGoal]:
        """
        Resolve a user query or keyword reference to an existing active goal belonging to user_id.
        Avoids creating duplicate goals.
        """
        q_lower = query.lower().strip()
        goals = [g for g in self._goals.values() if not user_id or g.user_id == user_id]

        # Direct ID match
        for g in goals:
            if g.goal_id.lower() == q_lower:
                return g

        # Check for goal-N in text
        for g in goals:
            if g.goal_id.lower() in q_lower:
                return g

        # Title or intent exact match
        for g in goals:
            if g.title.lower() == q_lower or (g.intent and g.intent.lower() == q_lower):
                return g

        # Keyword match for active non-completed goals
        for g in goals:
            if g.status not in (GoalStatus.COMPLETED.value, GoalStatus.CANCELLED.value):
                if g.title.lower() in q_lower or q_lower in g.title.lower():
                    return g

        return None

    def create_or_update_goal_from_intent(
        self,
        intent: str,
        title: str,
        description: str = "",
        priority: str = "medium",
        success_criteria: list[str] | None = None,
        user_id: str = "default_owner",
        tasks: list[dict] | None = None,
    ) -> tuple[LongRunningGoal, bool]:
        """
        Create a new goal or update an existing active goal if matching intent found.
        Returns (goal, created: bool).
        """
        existing = self.resolve_goal_reference(title, user_id=user_id) or self.resolve_goal_reference(intent, user_id=user_id)
        if existing and existing.status not in (GoalStatus.COMPLETED.value, GoalStatus.CANCELLED.value):
            # Update existing goal
            if description:
                existing.description = f"{existing.description}\nUpdate: {description}".strip() if existing.description else description
            if tasks:
                existing_titles = {t.title.lower() for t in existing.tasks}
                for idx, t in enumerate(tasks):
                    t_title = t.get("title", "").strip() if isinstance(t, dict) else str(t).strip()
                    if t_title and t_title.lower() not in existing_titles:
                        new_node = GoalTaskNode(
                            task_id=t.get("task_id", f"t-{len(existing.tasks) + 1}") if isinstance(t, dict) else f"t-{len(existing.tasks) + 1}",
                            title=t_title,
                            description=t.get("description", "") if isinstance(t, dict) else "",
                            status=t.get("status", "pending") if isinstance(t, dict) else "pending",
                            depends_on=t.get("depends_on", []) if isinstance(t, dict) else [],
                            action=t.get("action") if isinstance(t, dict) else None,
                            params=t.get("params", {}) if isinstance(t, dict) else {},
                        )
                        existing.tasks.append(new_node)
                        existing_titles.add(t_title.lower())
            existing.updated_at = datetime.now(timezone.utc).isoformat()
            existing.provenance.append({
                "from_status": existing.status,
                "to_status": existing.status,
                "reason": "Intent/subgoals updated via conversation",
                "timestamp": existing.updated_at,
            })
            self._save_goals()
            self._publish_event_safe("goal.updated", existing.to_dict())
            return existing, False

        new_goal = self.create_goal(
            title=title,
            description=description,
            user_id=user_id,
            priority=priority,
            intent=intent or title,
            success_criteria=success_criteria,
            tasks=tasks,
            status=GoalStatus.READY,
        )
        return new_goal, True

    def add_subtask(
        self,
        goal_id_or_title: str,
        title: str,
        description: str = "",
        user_id: str | None = None,
    ) -> tuple[bool, str, LongRunningGoal | None]:
        """
        Add a subtask or subgoal milestone to an existing active goal.
        """
        goal = self.resolve_goal_reference(goal_id_or_title, user_id=user_id)
        if not goal:
            return False, f"Goal '{goal_id_or_title}' not found.", None

        existing_titles = {t.title.lower() for t in goal.tasks}
        clean_title = title.strip()
        if clean_title.lower() in existing_titles:
            return True, f"Subtask '{clean_title}' is already part of goal '{goal.title}'.", goal

        task_id = f"t-{len(goal.tasks) + 1}"
        new_node = GoalTaskNode(
            task_id=task_id,
            title=clean_title,
            description=description.strip(),
            status="pending",
        )
        goal.tasks.append(new_node)
        goal.updated_at = datetime.now(timezone.utc).isoformat()
        goal.provenance.append({
            "from_status": goal.status,
            "to_status": goal.status,
            "reason": f"Subtask '{clean_title}' added",
            "timestamp": goal.updated_at,
        })
        self._save_goals()
        self._publish_event_safe("goal.updated", goal.to_dict())
        return True, f"Subtask '{clean_title}' added to goal '{goal.title}'.", goal

    def link_plan(self, goal_id: str, plan_id: str, steps: list[dict[str, Any]], user_id: str | None = None) -> bool:
        """
        Link a generated plan from the Planner Engine to a goal.
        Transforms plan steps into goal tasks and transitions status to PLANNED.
        Validates the complete proposed DAG for cycles, self-dependencies, and malformed references before mutating state.
        """
        goal = self._goals.get(goal_id)
        if not goal:
            return False
        if user_id and goal.user_id != user_id:
            raise PermissionError(f"Unauthorized: Goal '{goal_id}' does not belong to user '{user_id}'.")

        # 1. Collect all step IDs
        step_ids = set()
        for idx, s in enumerate(steps):
            task_id = s.get("step_id") or f"step-{idx+1}"
            step_ids.add(task_id)

        # 2. Build dependency graph and validate dependencies
        graph: dict[str, list[str]] = {}
        for idx, s in enumerate(steps):
            task_id = s.get("step_id") or f"step-{idx+1}"
            deps = s.get("depends_on", [])
            if not isinstance(deps, list):
                raise ValueError(f"Malformed dependency specification in step '{task_id}' for plan '{plan_id}'.")
            for dep in deps:
                if dep == task_id:
                    raise ValueError(f"Self-dependency detected in step '{task_id}' for plan '{plan_id}'.")
                if dep not in step_ids:
                    raise ValueError(f"Malformed dependency: step '{task_id}' references non-existent step '{dep}' in plan '{plan_id}'.")
            graph[task_id] = list(deps)

        # 3. Cycle detection using DFS with 3-coloring (0=unvisited, 1=visiting, 2=visited)
        visited: dict[str, int] = {node: 0 for node in graph}

        def find_cycle(node: str, current_path: list[str]) -> list[str] | None:
            visited[node] = 1
            for neighbor in graph.get(node, []):
                if visited.get(neighbor) == 1:
                    return current_path + [neighbor]
                if visited.get(neighbor) == 0:
                    c = find_cycle(neighbor, current_path + [neighbor])
                    if c:
                        return c
            visited[node] = 2
            return None

        for node in graph:
            if visited[node] == 0:
                cycle = find_cycle(node, [node])
                if cycle:
                    cycle_str = " -> ".join(cycle)
                    raise ValueError(f"Cyclic dependency detected in plan '{plan_id}': {cycle_str}")

        # DAG validation passed: now instantiate tasks and link atomically
        tasks = []
        for idx, s in enumerate(steps):
            task_id = s.get("step_id") or f"step-{idx+1}"
            tasks.append(
                GoalTaskNode(
                    task_id=task_id,
                    title=s.get("action") or f"Step {idx+1}",
                    description=s.get("description") or str(s.get("params", "")),
                    status="pending",
                    depends_on=s.get("depends_on", []),
                    action=s.get("action"),
                    params=s.get("params", {}),
                )
            )
        goal.plan_id = plan_id
        goal.tasks = tasks
        goal.progress_percent = 0.0
        self.transition_goal(goal_id, GoalStatus.PLANNED, reason=f"Plan '{plan_id}' linked with {len(tasks)} steps.", user_id=user_id)
        return True

    def update_task_progress(self, goal_id: str, task_id: str, status: str, evidence: str = "", user_id: str | None = None) -> bool:
        """
        Update a task's status and recalculate progress percentage from verified evidence.
        Enforces tenant authorization, terminal state preservation, and safe progress calculation.
        """
        goal = self._goals.get(goal_id)
        if not goal:
            return False
        if user_id and goal.user_id != user_id:
            logger.warning("Unauthorized task progress update for goal %s by user %s", goal_id, user_id)
            return False

        task = next((t for t in goal.tasks if t.task_id == task_id or t.title == task_id), None)
        if not task:
            return False

        # Terminal state protection: terminal tasks cannot be re-completed or re-failed
        terminal_statuses = {"completed", "failed", "cancelled"}
        if task.status in terminal_statuses and status in terminal_statuses:
            logger.warning("Task '%s' in goal '%s' is already in terminal state '%s'; ignoring transition to '%s'",
                           task_id, goal_id, task.status, status)
            return False

        now = datetime.now(timezone.utc).isoformat()
        task.status = status
        if status == "in_progress" and not task.started_at:
            task.started_at = now
        elif status == "completed":
            task.completed_at = now
            task.completion_evidence = evidence
            if evidence:
                goal.completion_evidence.append(evidence)

        # Recalculate progress percent
        total_tasks = len(goal.tasks)
        if total_tasks > 0:
            completed_count = sum(1 for t in goal.tasks if t.status == "completed")
            goal.progress_percent = round((completed_count / total_tasks) * 100.0, 1)

            if completed_count == total_tasks:
                if goal.status == GoalStatus.PLANNED.value:
                    self.transition_goal(goal_id, GoalStatus.IN_PROGRESS, reason="Executing plan steps.", user_id=user_id)
                self.transition_goal(goal_id, GoalStatus.COMPLETED, reason="All plan tasks completed successfully.", evidence=evidence, user_id=user_id)
            elif completed_count > 0 and goal.status in (GoalStatus.READY.value, GoalStatus.PLANNED.value):
                self.transition_goal(goal_id, GoalStatus.IN_PROGRESS, reason="Plan step execution in progress.", user_id=user_id)
            elif status == "in_progress" and goal.status in (GoalStatus.READY.value, GoalStatus.PLANNED.value):
                self.transition_goal(goal_id, GoalStatus.IN_PROGRESS, reason=f"Task '{task_id}' started.", user_id=user_id)
        elif status == "completed":
            goal.progress_percent = 100.0
            self.transition_goal(goal_id, GoalStatus.COMPLETED, reason="Task completed.", evidence=evidence, user_id=user_id)

        if status == "failed":
            self.transition_goal(goal_id, GoalStatus.BLOCKED, reason=f"Task '{task_id}' failed: {evidence}", user_id=user_id)

        self._save_goals()
        self._publish_event_safe("goal.progress_updated", {
            "goal_id": goal_id,
            "user_id": goal.user_id,
            "task_id": task_id,
            "task_status": status,
            "progress_percent": goal.progress_percent,
        })
        return True

    async def _handle_automation_result(self, event: HelixEvent) -> None:
        payload = getattr(event, "payload", {}) if not isinstance(event, dict) else event.get("payload", {})
        plan_id = payload.get("plan_id")
        step_id = payload.get("step_id") or payload.get("task_id")
        goal_id = payload.get("goal_id")
        user_id = payload.get("user_id")

        if not (plan_id or goal_id) or not step_id:
            logger.warning("EventBus rejected: missing plan_id/goal_id or step_id")
            return

        matching_goal = None
        if goal_id:
            matching_goal = self._goals.get(goal_id)
        elif plan_id:
            matching_goal = next((g for g in self._goals.values() if g.plan_id == plan_id), None)

        if not matching_goal:
            logger.warning("EventBus rejected: matching goal not found")
            return

        # Tenant isolation validation: reject cross-tenant event injection
        if user_id and matching_goal.user_id != user_id:
            logger.error("Security violation: cross-tenant event attempt on goal %s (owner=%s) by user %s",
                         matching_goal.goal_id, matching_goal.user_id, user_id)
            return

        # Verify task belongs to goal
        task = next((t for t in matching_goal.tasks if t.task_id == step_id or t.title == step_id), None)
        if not task:
            logger.warning("EventBus rejected: task %s does not belong to goal %s", step_id, matching_goal.goal_id)
            return

        evidence = str(payload.get("result", ""))
        self.update_task_progress(matching_goal.goal_id, task.task_id, "completed", evidence, user_id=user_id)

    async def _handle_action_verified(self, event: HelixEvent) -> None:
        payload = getattr(event, "payload", {}) if not isinstance(event, dict) else event.get("payload", {})
        action_id = payload.get("action_id", "")
        details = payload.get("details", "")
        user_id = payload.get("user_id")
        if not action_id:
            return
        # Find any goal task with matching action_id
        for goal in self._goals.values():
            if user_id and goal.user_id != user_id:
                continue
            for t in goal.tasks:
                if t.task_id == action_id or t.title == action_id:
                    self.update_task_progress(goal.goal_id, t.task_id, "completed", details, user_id=user_id)
                    return

    async def _handle_automation_failed(self, event: HelixEvent) -> None:
        payload = getattr(event, "payload", {}) if not isinstance(event, dict) else event.get("payload", {})
        plan_id = payload.get("plan_id")
        step_id = payload.get("step_id") or payload.get("task_id")
        goal_id = payload.get("goal_id")
        user_id = payload.get("user_id")
        error = payload.get("error", "Unknown execution error")

        if not (plan_id or goal_id) or not step_id:
            logger.warning("EventBus rejected: missing plan_id/goal_id or step_id in failure event")
            return

        matching_goal = None
        if goal_id:
            matching_goal = self._goals.get(goal_id)
        elif plan_id:
            matching_goal = next((g for g in self._goals.values() if g.plan_id == plan_id), None)

        if not matching_goal:
            return

        if user_id and matching_goal.user_id != user_id:
            logger.error("Security violation: cross-tenant failure event attempt on goal %s by user %s",
                         matching_goal.goal_id, user_id)
            return

        task = next((t for t in matching_goal.tasks if t.task_id == step_id or t.title == step_id), None)
        if not task:
            logger.warning("EventBus rejected: task %s does not belong to goal %s", step_id, matching_goal.goal_id)
            return

        self.update_task_progress(matching_goal.goal_id, task.task_id, "failed", str(error), user_id=user_id)

    async def _handle_plan_ready(self, event: HelixEvent) -> None:
        payload = getattr(event, "payload", {}) if not isinstance(event, dict) else event.get("payload", {})
        goal_id = payload.get("goal_id")
        plan_id = payload.get("plan_id")
        steps = payload.get("steps", [])
        user_id = payload.get("user_id")
        if goal_id and plan_id:
            try:
                self.link_plan(goal_id, plan_id, steps, user_id=user_id)
            except Exception as e:
                logger.error("Failed to link plan from plan.ready event: %s", e)

    def _publish_event_safe(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._event_bus.publish_event(
                source="goal_manager",
                event_type=event_type,
                payload=payload,
            ))
        except RuntimeError:
            pass
