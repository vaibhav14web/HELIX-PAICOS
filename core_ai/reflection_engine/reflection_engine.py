import asyncio
import logging
import re
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.reflection_engine")


class ReflectionEngine:
    """
    Closed-loop Reflection & Learning Engine for HELIX.
    Observes interaction outcomes (plan/automation completions, failures, permission decisions,
    user corrections), extracts learning signals, updates the User Model, records Episodic Memory,
    extracts Procedural Workflows, and governs the ECS LEARNING state.
    """

    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._lock = asyncio.Lock()
        self._subscriptions: list[str] = []
        self._last_plan_info: dict[str, Any] = {}
        self._pending_interactions: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        handlers = {
            "plan.completed": self._on_plan_completed,
            "plan.failed": self._on_plan_failed,
            "automation.completed": self._on_automation_completed,
            "automation.failed": self._on_automation_failed,
            "permission.granted": self._on_permission_granted,
            "permission.denied": self._on_permission_denied,
            "conversation.user_message": self._on_user_message,
            "conversation.user_input": self._on_user_message,
            "reflection.trigger": self._on_reflection_trigger,
        }
        for event_type, handler in handlers.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(handlers.keys())
        logger.info("ReflectionEngine started successfully")

    async def stop(self) -> None:
        for event_type in self._subscriptions:
            self._event_bus.unsubscribe(event_type, None)
        self._subscriptions.clear()
        self._last_plan_info.clear()
        self._pending_interactions.clear()
        logger.info("ReflectionEngine stopped")

    # ── Feedback Extraction ───────────────────────────────────────

    def analyze_feedback(self, text: str) -> list[dict[str, Any]]:
        """
        Analyze user message for implicit or explicit feedback regarding
        communication style, tool preferences, or corrections.
        """
        lower = text.lower().strip()
        updates: list[dict[str, Any]] = []

        # 1. Verbosity preferences
        concise_triggers = ("too long", "keep it concise", "be brief", "make it short", "shorter", "tldr", "too wordy", "less text")
        detailed_triggers = ("more detail", "elaborate", "explain further", "too short", "tell me more", "in depth", "deeper")

        if any(t in lower for t in concise_triggers):
            updates.append({"category": "communication_style", "trait": "verbosity", "value": "concise"})
        elif any(t in lower for t in detailed_triggers):
            updates.append({"category": "communication_style", "trait": "verbosity", "value": "detailed"})

        # 2. Formatting preferences
        code_first_triggers = ("just the code", "code only", "only code", "no explanation", "give me code")
        bullet_triggers = ("use bullets", "in bullet points", "bullet list", "bullet points")

        if any(t in lower for t in code_first_triggers):
            updates.append({"category": "communication_style", "trait": "formatting", "value": "code_first"})
        elif any(t in lower for t in bullet_triggers):
            updates.append({"category": "communication_style", "trait": "formatting", "value": "bullet_points"})

        # 3. Tone preferences
        if "be technical" in lower or "more technical" in lower:
            updates.append({"category": "communication_style", "trait": "tone", "value": "technical"})
        elif "be casual" in lower or "keep it simple" in lower or "plain english" in lower:
            updates.append({"category": "communication_style", "trait": "tone", "value": "casual"})

        # 4. Tool preferences
        if re.search(r"\b(?:use|prefer|open in)\s+brave\b", lower):
            updates.append({"category": "working_style", "trait": "preferred_browser", "value": "brave"})
        elif re.search(r"\b(?:use|prefer|open in)\s+chrome\b", lower):
            updates.append({"category": "working_style", "trait": "preferred_browser", "value": "chrome"})
        elif re.search(r"\b(?:use|prefer|open in)\s+edge\b", lower):
            updates.append({"category": "working_style", "trait": "preferred_browser", "value": "edge"})
        elif re.search(r"\b(?:use|prefer|open in)\s+firefox\b", lower):
            updates.append({"category": "working_style", "trait": "preferred_browser", "value": "firefox"})

        if re.search(r"\b(?:use|prefer)\s+powershell\b", lower):
            updates.append({"category": "working_style", "trait": "preferred_terminal", "value": "powershell"})
        elif re.search(r"\b(?:use|prefer)\s+(?:bash|wt|terminal)\b", lower):
            updates.append({"category": "working_style", "trait": "preferred_terminal", "value": "wt"})

        if re.search(r"\b(?:use|prefer)\s+cursor\b", lower):
            updates.append({"category": "working_style", "trait": "preferred_editor", "value": "cursor"})
        elif re.search(r"\b(?:use|prefer)\s+vscode\b", lower):
            updates.append({"category": "working_style", "trait": "preferred_editor", "value": "vscode"})

        return updates

    def is_user_correction(self, text: str) -> bool:
        """Detect if the message represents user correction or dissatisfaction."""
        lower = text.lower().strip()
        correction_patterns = (
            "no that's wrong", "that is wrong", "not what i asked", "don't do that",
            "do not do that", "stop doing that", "incorrect", "you misunderstood",
            "that didn't work", "that failed", "wrong command"
        )
        return any(p in lower for p in correction_patterns)

    # ── Core Reflection Process ───────────────────────────────────

    async def reflect_on_interaction(
        self,
        intent: str,
        actions_taken: list[dict[str, Any]],
        outcome: str,
        user_reaction: str = "neutral",
        context: dict[str, Any] | None = None,
        notes: str = "",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute full cognitive reflection:
        1. Transitions ECS into LEARNING state.
        2. Encodes Episode into Episodic Memory.
        3. Extracts and updates Procedural Memory if multi-step workflow succeeded.
        4. Transitions ECS back to IDLE.
        """
        async with self._lock:
            # Step 1: Signal ECS to transition into LEARNING state
            await self._event_bus.publish_event(
                source="reflection_engine",
                event_type="learning.start",
                payload={"reason": f"Reflecting on '{intent}' (outcome: {outcome})"},
                correlation_id=correlation_id,
            )

            tags = []
            if context and context.get("project"):
                tags.append(str(context["project"]))
            for act in actions_taken:
                action_name = act.get("action") or act.get("name") or ""
                if action_name and action_name not in tags:
                    tags.append(action_name)

            if not notes:
                if outcome == "success":
                    notes = f"Successfully accomplished intent '{intent}' with {len(actions_taken)} action(s)."
                else:
                    notes = f"Encountered friction/failure performing '{intent}'. User reaction: {user_reaction}."

            # Step 2: Record Episode
            episode_payload = {
                "intent": intent,
                "context": context or {},
                "decision_mode": "plan_execution" if len(actions_taken) > 1 else "direct_answer",
                "actions_taken": actions_taken,
                "user_reaction": user_reaction,
                "outcome": outcome,
                "reflection_notes": notes,
                "tags": tags,
            }
            await self._event_bus.publish_event(
                source="reflection_engine",
                event_type="memory.episodic.record",
                payload=episode_payload,
                correlation_id=correlation_id,
            )

            # Step 3: Extract Procedural Workflow if successful multi-step sequence
            workflow_extracted = None
            if outcome == "success" and len(actions_taken) >= 2:
                workflow_name = f"Workflow: {intent.title()}"
                steps_for_wf = []
                for idx, a in enumerate(actions_taken):
                    steps_for_wf.append({
                        "step_index": idx,
                        "action": a.get("action") or a.get("name") or "unknown",
                        "target": a.get("target"),
                        "params": a.get("params") or a.get("arguments") or {},
                        "depends_on": [idx - 1] if idx > 0 else [],
                        "notes": "",
                    })

                project_scope = (context or {}).get("project")
                wf_payload = {
                    "name": workflow_name,
                    "trigger_intent": intent,
                    "keywords": [w for w in intent.lower().split() if len(w) > 2],
                    "project_scope": project_scope,
                    "steps": steps_for_wf,
                    "confidence": 0.6,
                    "user_verified": False,
                }
                await self._event_bus.publish_event(
                    source="reflection_engine",
                    event_type="memory.procedural.record",
                    payload=wf_payload,
                    correlation_id=correlation_id,
                )
                workflow_extracted = workflow_name

            # Step 4: Signal ECS that reflection is complete -> transitions to IDLE
            await self._event_bus.publish_event(
                source="reflection_engine",
                event_type="learning.complete",
                payload={"intent": intent, "outcome": outcome},
                correlation_id=correlation_id,
            )

            summary = {
                "intent": intent,
                "outcome": outcome,
                "episode_recorded": True,
                "workflow_extracted": workflow_extracted,
                "user_reaction": user_reaction,
            }

            await self._event_bus.publish_event(
                source="reflection_engine",
                event_type="reflection.completed",
                payload=summary,
                correlation_id=correlation_id,
            )
            return summary

    # ── EventBus Handlers ─────────────────────────────────────────

    async def _on_plan_completed(self, event: HelixEvent) -> None:
        task = event.payload.get("task", "Completed task")
        plan_id = event.payload.get("plan_id", "")
        steps = event.payload.get("steps", [])

        actions = [
            {"action": s.get("action", ""), "target": s.get("target"), "params": s.get("params", {})}
            for s in steps if isinstance(s, dict)
        ]
        context = event.payload.get("context", {})

        await self.reflect_on_interaction(
            intent=task,
            actions_taken=actions,
            outcome="success",
            user_reaction="accepted",
            context=context,
            notes=f"Plan {plan_id} completed successfully without errors.",
            correlation_id=event.correlation_id,
        )

    async def _on_plan_failed(self, event: HelixEvent) -> None:
        task = event.payload.get("task", "Failed task")
        plan_id = event.payload.get("plan_id", "")
        error = event.payload.get("error", "Unknown error")
        step_id = event.payload.get("step_id", "")

        await self.reflect_on_interaction(
            intent=task,
            actions_taken=[{"plan_id": plan_id, "failed_step": step_id}],
            outcome="failure",
            user_reaction="neutral",
            context=event.payload.get("context", {}),
            notes=f"Plan {plan_id} failed at step {step_id}: {error}",
            correlation_id=event.correlation_id,
        )

    async def _on_automation_completed(self, event: HelixEvent) -> None:
        action = event.payload.get("action", "")
        if not action:
            return
        await self.reflect_on_interaction(
            intent=f"Automated {action}",
            actions_taken=[{"action": action, "result": event.payload.get("result")}],
            outcome="success",
            user_reaction="accepted",
            correlation_id=event.correlation_id,
        )

    async def _on_automation_failed(self, event: HelixEvent) -> None:
        action = event.payload.get("action", "")
        error = event.payload.get("error", "Automation execution error")
        await self.reflect_on_interaction(
            intent=f"Automated {action}",
            actions_taken=[{"action": action}],
            outcome="failure",
            notes=f"Automation failed: {error}",
            correlation_id=event.correlation_id,
        )

    async def _on_permission_granted(self, event: HelixEvent) -> None:
        action = event.payload.get("action", "unknown_action")
        risk = event.payload.get("risk_level", "medium")
        logger.info("Reflecting on granted permission: %s (risk: %s)", action, risk)
        # Record positive decision pattern
        await self._event_bus.publish_event(
            source="reflection_engine",
            event_type="user_model.trait.observe",
            payload={
                "category": "decision_patterns",
                "trait": f"approved_{action}",
                "value": True,
            },
            correlation_id=event.correlation_id,
        )

    async def _on_permission_denied(self, event: HelixEvent) -> None:
        action = event.payload.get("action", "unknown_action")
        risk = event.payload.get("risk_level", "medium")
        logger.info("Reflecting on denied permission: %s (risk: %s)", action, risk)

        # Record negative decision pattern
        await self._event_bus.publish_event(
            source="reflection_engine",
            event_type="user_model.trait.observe",
            payload={
                "category": "decision_patterns",
                "trait": f"denied_{action}",
                "value": True,
            },
            correlation_id=event.correlation_id,
        )

        # Record episode noting user rejection
        await self.reflect_on_interaction(
            intent=f"Execute {action}",
            actions_taken=[{"action": action, "risk_level": risk}],
            outcome="failure",
            user_reaction="rejected",
            notes=f"User denied permission to execute {action} at risk level {risk}.",
            correlation_id=event.correlation_id,
        )

    async def _on_user_message(self, event: HelixEvent) -> None:
        text = event.payload.get("content", "")
        if not text:
            return

        # Check for feedback traits
        feedback_updates = self.analyze_feedback(text)
        for update in feedback_updates:
            logger.info("Feedback trait detected from user: %s.%s = %s", update["category"], update["trait"], update["value"])
            await self._event_bus.publish_event(
                source="reflection_engine",
                event_type="user_model.trait.observe",
                payload=update,
                correlation_id=event.correlation_id,
            )

        # Check if user message was an explicit correction
        if self.is_user_correction(text):
            logger.info("User correction detected: '%s'", text)
            await self.reflect_on_interaction(
                intent="Previous interaction",
                actions_taken=[],
                outcome="failure",
                user_reaction="corrected",
                notes=f"User provided correction: '{text}'",
                correlation_id=event.correlation_id,
            )

    async def _on_reflection_trigger(self, event: HelixEvent) -> None:
        payload = event.payload
        await self.reflect_on_interaction(
            intent=payload.get("intent", "Manual reflection"),
            actions_taken=payload.get("actions_taken", []),
            outcome=payload.get("outcome", "success"),
            user_reaction=payload.get("user_reaction", "neutral"),
            context=payload.get("context"),
            notes=payload.get("notes", ""),
            correlation_id=event.correlation_id,
        )
