import os
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from foundation.event_bus.event_bus import EventBus, HelixEvent
from foundation.storage_manager.crypto import encrypt_string, decrypt_string

logger = logging.getLogger("helix.user_model")

# Bayesian evidence accumulation constants
_ALPHA_REINFORCE = 0.25   # Step size towards 1.0 on corroborating evidence
_BETA_CONTRADICT = 0.30   # Penalty step on contradictory observation
_INITIAL_HYPOTHESIS = 0.35 # Confidence for first observation
_CONTRADICTION_THRESHOLD = 0.20 # If confidence drops below this, replace value


class TraitEvidence(BaseModel):
    """Bayesian evidence-weighted representation of a single user trait."""
    value: Any = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_count: int = 0
    last_observed: str | None = None
    source: str = "unobserved"  # "unobserved" | "observed" | "inferred" | "explicit"

    def reinforce(self, new_value: Any, source: str = "observed") -> None:
        """Reinforce existing value or handle contradiction."""
        now = datetime.now(timezone.utc).isoformat()
        self.last_observed = now

        if source == "explicit":
            self.value = new_value
            self.confidence = 1.0
            self.evidence_count += 1
            self.source = "explicit"
            return

        if self.source == "unobserved" or self.value is None or self.confidence == 0.0:
            self.value = new_value
            self.confidence = _INITIAL_HYPOTHESIS
            self.evidence_count = 1
            self.source = source
            return

        if self.value == new_value:
            # Corroborating evidence: Asymptotic increase toward 1.0
            self.confidence = min(1.0, round(self.confidence + _ALPHA_REINFORCE * (1.0 - self.confidence), 4))
            self.evidence_count += 1
        else:
            # Contradictory evidence: Decrease confidence
            self.confidence = max(0.0, round(self.confidence - _BETA_CONTRADICT, 4))
            if self.confidence < _CONTRADICTION_THRESHOLD:
                # Value switches to newly observed trait with hypothesis confidence
                self.value = new_value
                self.confidence = _INITIAL_HYPOTHESIS
                self.evidence_count = 1
                self.source = source


class CommunicationStyle(BaseModel):
    """User preferences regarding dialogue, verbosity, and tone."""
    verbosity: TraitEvidence = Field(default_factory=TraitEvidence)
    tone: TraitEvidence = Field(default_factory=TraitEvidence)
    formatting: TraitEvidence = Field(default_factory=TraitEvidence)
    dialogue_style: TraitEvidence = Field(default_factory=TraitEvidence)
    voice_profile: TraitEvidence = Field(default_factory=TraitEvidence)


class WorkingStyle(BaseModel):
    """User preferences regarding tools, workflow pacing, and autonomy."""
    preferred_browser: TraitEvidence = Field(default_factory=TraitEvidence)
    preferred_terminal: TraitEvidence = Field(default_factory=TraitEvidence)
    preferred_editor: TraitEvidence = Field(default_factory=TraitEvidence)
    autonomy_level: TraitEvidence = Field(default_factory=TraitEvidence)


class DecisionPatterns(BaseModel):
    """Patterns of user approvals, risk appetite, and verification."""
    risk_tolerance: TraitEvidence = Field(default_factory=TraitEvidence)
    auto_approve_safe_actions: TraitEvidence = Field(default_factory=TraitEvidence)
    confirmation_frequency: TraitEvidence = Field(default_factory=TraitEvidence)


class ActiveContext(BaseModel):
    """Current working state, focus goals, and active projects."""
    current_goal: str | None = None
    active_project: str | None = None
    recent_topics: list[str] = Field(default_factory=list)
    dialogue_mode: str | None = None
    evaluation_contract: dict[str, Any] = Field(default_factory=dict)
    last_active_time: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class UserModel(BaseModel):
    """Complete user cognitive profile."""
    username: str
    display_name: str
    user_id: str = "default_owner"
    communication_style: CommunicationStyle = Field(default_factory=CommunicationStyle)
    working_style: WorkingStyle = Field(default_factory=WorkingStyle)
    decision_patterns: DecisionPatterns = Field(default_factory=DecisionPatterns)
    skills: dict[str, TraitEvidence] = Field(default_factory=dict)
    active_context: ActiveContext = Field(default_factory=ActiveContext)
    version: int = 1


class UserModelEngine:
    """
    Core engine managing the dynamic, probabilistic User Model for HELIX.
    Persists profile encrypted at rest and updates traits using evidence-based accumulation,
    partitioned safely per user.
    """

    def __init__(self, event_bus: EventBus, storage_path: str | None = None):
        self._event_bus = event_bus
        from foundation.paths import get_system_username, get_system_display_name
        username = get_system_username()
        display_name = get_system_display_name()
        from foundation.paths import get_helix_home

        self._storage_dir = Path(storage_path or os.getenv(
            "HELIX_USER_MODEL_PATH",
            str(get_helix_home() / "user_model"),
        ))
        self._file_path = self._storage_dir / "user_profile.enc"

        # Keyed by user_id -> UserModel
        self._models: dict[str, UserModel] = {
            "default_owner": UserModel(username=username, display_name=display_name, user_id="default_owner")
        }
        self._dirty_users: set[str] = set()
        self._subscriptions: list[str] = []

    def _get_user_file_path(self, user_id: str) -> Path:
        if not user_id or user_id in ("default", "default_owner", "global"):
            return self._file_path
        try:
            from foundation.storage_manager.tenant_storage import get_tenant_storage_manager
            return get_tenant_storage_manager().get_user_model_dir(user_id) / "user_profile.enc"
        except Exception:
            return self._file_path

    async def start(self) -> None:
        self._load_from_disk("default_owner")
        handlers = {
            "user_model.trait.observe": self._handle_observe_trait,
            "user_model.trait.set": self._handle_set_trait,
            "user_model.get": self._handle_get_model,
            "user_model.context.update": self._handle_update_context,
        }
        for event_type, handler in handlers.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(handlers.keys())
        logger.info("UserModelEngine started successfully")

    async def stop(self) -> None:
        for uid in list(self._dirty_users):
            self.save_to_disk(uid)
        for event_type in self._subscriptions:
            self._event_bus.unsubscribe(event_type, None)
        self._subscriptions.clear()
        logger.info("UserModelEngine stopped")

    @property
    def model(self) -> UserModel:
        return self.get_model("default_owner")

    def get_model(self, user_id: str = "default_owner") -> UserModel:
        if user_id not in self._models:
            self._load_from_disk(user_id)
        if user_id not in self._models:
            clean_display = user_id.capitalize()
            self._models[user_id] = UserModel(username=user_id, display_name=clean_display, user_id=user_id)
        return self._models[user_id]

    def observe_trait(
        self,
        category: str,
        trait_name: str,
        value: Any,
        source: str = "observed",
        user_id: str = "default_owner",
    ) -> TraitEvidence:
        """
        Record a new observation for a trait.
        Increases confidence if corroborating, decreases if contradicting.
        """
        trait = self._get_or_create_trait(category, trait_name, value, user_id=user_id)
        trait.reinforce(new_value=value, source=source)
        self._dirty_users.add(user_id)
        return trait

    def set_explicit_trait(
        self,
        category: str,
        trait_name: str,
        value: Any,
        confidence: float = 1.0,
        user_id: str = "default_owner",
    ) -> TraitEvidence:
        """Record an explicit user declaration (confidence default 1.0)."""
        trait = self._get_or_create_trait(category, trait_name, value, user_id=user_id)
        trait.value = value
        trait.confidence = max(0.0, min(1.0, float(confidence)))
        trait.source = "explicit"
        trait.evidence_count += 1
        self._dirty_users.add(user_id)
        return trait

    def decay_trait(self, category: str, trait_name: str, factor: float = 0.9, user_id: str = "default_owner") -> None:
        """Decay confidence of a trait over time when unobserved."""
        trait = self._get_trait(category, trait_name, user_id=user_id)
        if trait and trait.source != "explicit":
            trait.confidence = max(0.05, round(trait.confidence * factor, 4))
            self._dirty_users.add(user_id)

    def decay_confidence(self, hours_elapsed: float = 24.0, user_id: str = "default_owner") -> None:
        """Apply confidence decay across all non-explicit traits."""
        model = self.get_model(user_id)
        decay_factor = max(0.5, 1.0 - (0.05 * (hours_elapsed / 24.0)))
        for cat in [model.communication_style, model.working_style, model.decision_patterns]:
            for field_name in type(cat).model_fields:
                trait: TraitEvidence = getattr(cat, field_name)
                if trait.source != "explicit":
                    trait.confidence = max(0.05, round(trait.confidence * decay_factor, 4))
        for skill in model.skills.values():
            if skill.source != "explicit":
                skill.confidence = max(0.05, round(skill.confidence * decay_factor, 4))
        self._dirty_users.add(user_id)

    def update_active_context(
        self,
        current_goal: str | None = None,
        active_project: str | None = None,
        topic: str | None = None,
        dialogue_mode: str | None = None,
        evaluation_contract: dict[str, Any] | None = None,
        user_id: str = "default_owner",
    ) -> ActiveContext:
        """Update active session context (current goal, active project, recent topics, dialogue mode)."""
        model = self.get_model(user_id)
        ctx = model.active_context
        if current_goal is not None:
            ctx.current_goal = current_goal
        if active_project is not None:
            ctx.active_project = active_project
        if dialogue_mode is not None:
            if dialogue_mode in ("clear", "none", ""):
                ctx.dialogue_mode = None
                ctx.evaluation_contract = {}
            else:
                ctx.dialogue_mode = dialogue_mode
        if evaluation_contract is not None:
            ctx.evaluation_contract = evaluation_contract
        if topic:
            if topic in ctx.recent_topics:
                ctx.recent_topics.remove(topic)
            ctx.recent_topics.insert(0, topic)
            ctx.recent_topics = ctx.recent_topics[:10]
        ctx.last_active_time = datetime.now(timezone.utc).isoformat()
        self._dirty_users.add(user_id)
        return ctx

    def to_prompt_context(self, user_id: str = "default_owner") -> str:
        """
        Generate a high-density, prompt-ready User Profile block
        for injecting into LLM system prompts and planning decisions.
        """
        model = self.get_model(user_id)
        comm = model.communication_style
        work = model.working_style
        dec = model.decision_patterns
        ctx = model.active_context

        lines = [
            "=== USER COGNITIVE PROFILE & WORKING STYLE ===",
            f"- User: {model.display_name} (system username: {model.username})",
        ]

        comm_items = []
        if comm.verbosity.value is not None and comm.verbosity.confidence > 0.0:
            comm_items.append(f"verbosity='{comm.verbosity.value}' (conf: {comm.verbosity.confidence:.2f})")
        if comm.tone.value is not None and comm.tone.confidence > 0.0:
            comm_items.append(f"tone='{comm.tone.value}'")
        if comm.formatting.value is not None and comm.formatting.confidence > 0.0:
            comm_items.append(f"format='{comm.formatting.value}'")
        if comm.dialogue_style.value is not None and comm.dialogue_style.confidence > 0.0:
            comm_items.append(f"dialogue_style='{comm.dialogue_style.value}' (conf: {comm.dialogue_style.confidence:.2f})")
        if hasattr(comm, "voice_profile") and comm.voice_profile.value is not None and comm.voice_profile.confidence > 0.0:
            comm_items.append(f"voice_profile='{comm.voice_profile.value}'")
        if comm_items:
            lines.append(f"- Communication Style: {', '.join(comm_items)}")

        tool_items = []
        if work.preferred_browser.value is not None and work.preferred_browser.confidence > 0.0:
            tool_items.append(f"browser='{work.preferred_browser.value}'")
        if work.preferred_terminal.value is not None and work.preferred_terminal.confidence > 0.0:
            tool_items.append(f"terminal='{work.preferred_terminal.value}'")
        if work.preferred_editor.value is not None and work.preferred_editor.confidence > 0.0:
            tool_items.append(f"editor='{work.preferred_editor.value}'")
        if tool_items:
            lines.append(f"- Preferred Toolchain: {', '.join(tool_items)}")

        if work.autonomy_level.value is not None and work.autonomy_level.confidence > 0.0:
            lines.append(f"- Working Autonomy: '{work.autonomy_level.value}'")

        dec_items = []
        if dec.risk_tolerance.value is not None and dec.risk_tolerance.confidence > 0.0:
            dec_items.append(f"risk_tolerance='{dec.risk_tolerance.value}'")
        if dec.auto_approve_safe_actions.value is not None and dec.auto_approve_safe_actions.confidence > 0.0:
            dec_items.append(f"auto_approve_safe={dec.auto_approve_safe_actions.value}")
        if dec_items:
            lines.append(f"- Decision Patterns: {', '.join(dec_items)}")

        observed_skills = [
            f"{k}='{v.value}' (conf: {v.confidence:.2f})"
            for k, v in model.skills.items()
            if v.value is not None and v.confidence > 0.0
        ]
        if observed_skills:
            lines.append(f"- Known Proficiencies: {', '.join(observed_skills[:5])}")

        if ctx.active_project or ctx.current_goal or ctx.dialogue_mode:
            active_info = []
            if ctx.active_project:
                active_info.append(f"project='{ctx.active_project}'")
            if ctx.current_goal:
                active_info.append(f"goal='{ctx.current_goal}'")
            if ctx.dialogue_mode:
                active_info.append(f"mode='{ctx.dialogue_mode}'")
            lines.append(f"- Active Focus: {', '.join(active_info)}")

        if len(lines) == 2:
            lines.append("- Profile Status: Fresh / Observing. No preferences or tools have been hardcoded; preferences, tools, and workflows are learned dynamically through ongoing interactions.")

        return "\n".join(lines)

    # ── Internal Storage ──────────────────────────────────────────

    def _load_from_disk(self, user_id: str = "default_owner") -> None:
        path = self._get_user_file_path(user_id)
        if not path.exists():
            return
        try:
            raw_bytes = path.read_bytes()
            decompressed = decrypt_string(raw_bytes)
            data = json.loads(decompressed)
            self._models[user_id] = UserModel(**data)
            self._dirty_users.discard(user_id)
        except Exception as e:
            logger.warning("Could not load user profile from disk for user %s (%s)", user_id, e)

    def save_to_disk(self, user_id: str = "default_owner") -> None:
        try:
            path = self._get_user_file_path(user_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            model = self.get_model(user_id)
            content = json.dumps(model.model_dump(), indent=2)
            encrypted = encrypt_string(content)
            path.write_bytes(encrypted)
            self._dirty_users.discard(user_id)
        except Exception as e:
            logger.error("Failed to save user profile to disk for user %s: %s", user_id, e)

    # ── Trait Resolution Helpers ─────────────────────────────────

    def _get_trait(self, category: str, trait_name: str, user_id: str = "default_owner") -> TraitEvidence | None:
        model = self.get_model(user_id)
        cat_lower = category.lower()
        if cat_lower == "communication_style":
            return getattr(model.communication_style, trait_name, None)
        elif cat_lower == "working_style":
            return getattr(model.working_style, trait_name, None)
        elif cat_lower == "decision_patterns":
            return getattr(model.decision_patterns, trait_name, None)
        elif cat_lower == "skills":
            return model.skills.get(trait_name)
        return None

    def _get_or_create_trait(self, category: str, trait_name: str, default_val: Any, user_id: str = "default_owner") -> TraitEvidence:
        existing = self._get_trait(category, trait_name, user_id=user_id)
        if existing is not None:
            return existing

        model = self.get_model(user_id)
        cat_lower = category.lower()
        new_trait = TraitEvidence(value=default_val, confidence=_INITIAL_HYPOTHESIS, source="observed")

        if cat_lower == "communication_style" and hasattr(model.communication_style, trait_name):
            setattr(model.communication_style, trait_name, new_trait)
        elif cat_lower == "working_style" and hasattr(model.working_style, trait_name):
            setattr(model.working_style, trait_name, new_trait)
        elif cat_lower == "decision_patterns" and hasattr(model.decision_patterns, trait_name):
            setattr(model.decision_patterns, trait_name, new_trait)
        elif cat_lower == "skills":
            model.skills[trait_name] = new_trait
        else:
            model.skills[f"{category}.{trait_name}"] = new_trait

        return new_trait

    # ── EventBus Handlers ─────────────────────────────────────────

    async def _handle_observe_trait(self, event: HelixEvent) -> None:
        category = event.payload.get("category", "skills")
        trait_name = event.payload.get("trait", "")
        value = event.payload.get("value")
        source = event.payload.get("source", "observed")
        user_id = event.payload.get("user_id", "default_owner")
        if not trait_name or value is None:
            return

        trait = self.observe_trait(category, trait_name, value, source=source, user_id=user_id)
        if user_id in self._dirty_users:
            self.save_to_disk(user_id)

        await self._event_bus.publish_event(
            source="user_model",
            event_type="user_model.updated",
            payload={
                "category": category,
                "trait": trait_name,
                "value": trait.value,
                "confidence": trait.confidence,
                "evidence_count": trait.evidence_count,
                "user_id": user_id,
            },
            correlation_id=event.correlation_id,
        )

    async def _handle_set_trait(self, event: HelixEvent) -> None:
        category = event.payload.get("category", "skills")
        trait_name = event.payload.get("trait", "")
        value = event.payload.get("value")
        user_id = event.payload.get("user_id", "default_owner")
        if not trait_name or value is None:
            return

        trait = self.set_explicit_trait(category, trait_name, value, user_id=user_id)
        if user_id in self._dirty_users:
            self.save_to_disk(user_id)

        await self._event_bus.publish_event(
            source="user_model",
            event_type="user_model.updated",
            payload={
                "category": category,
                "trait": trait_name,
                "value": trait.value,
                "confidence": trait.confidence,
                "source": "explicit",
                "user_id": user_id,
            },
            correlation_id=event.correlation_id,
        )

    async def _handle_get_model(self, event: HelixEvent) -> None:
        user_id = event.payload.get("user_id", "default_owner")
        model = self.get_model(user_id)
        prompt_ctx = self.to_prompt_context(user_id)
        await self._event_bus.publish_event(
            source="user_model",
            event_type="user_model.retrieved",
            payload={"model": model.model_dump(), "prompt_context": prompt_ctx, "user_id": user_id},
            correlation_id=event.correlation_id,
        )

    async def _handle_update_context(self, event: HelixEvent) -> None:
        goal = event.payload.get("current_goal")
        proj = event.payload.get("active_project")
        topic = event.payload.get("topic")
        dialogue_mode = event.payload.get("dialogue_mode")
        evaluation_contract = event.payload.get("evaluation_contract")
        user_id = event.payload.get("user_id", "default_owner")
        self.update_active_context(
            current_goal=goal,
            active_project=proj,
            topic=topic,
            dialogue_mode=dialogue_mode,
            evaluation_contract=evaluation_contract,
            user_id=user_id,
        )
        if user_id in self._dirty_users:
            self.save_to_disk(user_id)
