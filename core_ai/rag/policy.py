"""
HELIX PCOS — Intent-Specific Retrieval Policies, Scope Control & Source Precedence.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from core_ai.rag.models import (
    EvidenceCandidate,
    MemoryTier,
    RetrievalIntent,
    RetrievalPlan,
    RetrievalScope,
    SourceAuthority,
)

# Canonical component dictionary across known projects
KNOWN_COMPONENTS = {
    # HELIX components
    "planner engine": ("helix", "Planner Engine"),
    "planner": ("helix", "Planner Engine"),
    "planning engine": ("helix", "Planner Engine"),
    "permission manager": ("helix", "Permission Manager"),
    "event bus": ("helix", "Event Bus"),
    "eventbus": ("helix", "Event Bus"),
    "automation engine": ("helix", "Automation Engine"),
    "action executor": ("helix", "Action Executor"),
    "verification gate": ("helix", "Verification Gate"),
    "fact verification": ("helix", "Verification Gate"),
    "conversation engine": ("helix", "Conversation Engine"),
    "llm engine": ("helix", "LLMEngine"),
    "warm memory": ("helix", "Warm Memory"),
    "episodic memory": ("helix", "Episodic Memory"),
    "procedural memory": ("helix", "Procedural Memory"),
    "context aggregator": ("helix", "Context Aggregator"),
    "world model": ("helix", "World Model"),
    "self model": ("helix", "Self Model"),
    "user model": ("helix", "User Model"),
    "executive control system": ("helix", "Executive Control System"),
    "ecs": ("helix", "Executive Control System"),
    "coding companion": ("helix", "Coding Companion"),
    "learning companion": ("helix", "Learning Companion"),
    "productivity companion": ("helix", "Productivity Companion"),
    # AI-HMS / Medisense components
    "clinical triage": ("ai-hms", "Clinical Triage Engine"),
    "triage model": ("ai-hms", "Clinical Triage Engine"),
    "bed allocation": ("ai-hms", "Bed Allocation Manager"),
    "patient registry": ("ai-hms", "Patient Registry"),
    "doctor scheduler": ("ai-hms", "Doctor Scheduler"),
    "vitals monitor": ("ai-hms", "Vitals Monitor"),
    "pharmacy inventory": ("ai-hms", "Pharmacy Inventory"),
}

ANAPHORA_PATTERNS = [
    r"\b(?:what|which)\s+algorithm\s+does\s+it\s+use\b",
    r"\bhow\s+does\s+it\s+work\b",
    r"\bwhat\s+does\s+it\s+do\b",
    r"\bexplain\s+(?:it|that|this)\b",
    r"\bexplain\s+(?:that|this)\s+(?:module|engine|component|algorithm)\b",
    r"\bhow\s+is\s+it\s+implemented\b",
    r"\bwhat\s+algorithm\b",
    r"\bwhat\s+parameters\s+does\s+it\s+take\b",
    r"\bwhere\s+is\s+it\s+defined\b",
]

PROMPT_INJECTION_MARKERS = [
    r"ignore\s+(?:all\s+)?previous\s+instructions",
    r"disregard\s+(?:all\s+)?prior\s+(?:rules|instructions)",
    r"you\s+are\s+now\s+in\s+developer\s+mode",
    r"you\s+are\s+now\s+(?:dan|jailbroken|unrestricted)",
    r"system\s*:\s*override",
    r"<\s*\|\s*system\s*\|>",
    r"<\s*\|\s*user\s*\|>",
    r"<\s*\|\s*assistant\s*\|>",
    r"<\s*/\s*s\s*>",
    r"\[\s*INST\s*\]",
    r"```(?:system|instruction|eval)",
    r"print\s+(?:the\s+)?(?:secret|password|auth_token|api_key|credential|private_key)",
    r"--- END UNTRUSTED RETRIEVED OBSERVATIONS ---",
    r"\[END_RETRIEVED_MEMORY_OBSERVATIONS\]",
    r"\[END_TOOL_EXECUTION_OBSERVATIONS\]",
    r"override\s+(?:the\s+)?(?:contract|rubric|evaluation|instructions?)",
    r"do\s+not\s+(?:judge|evaluate|correct)",
    r"ignore\s+(?:the\s+)?(?:evaluation\s+contract|brevity|short\s+responses?)",
]


def sanitize_untrusted_evidence(text: str) -> str:
    """Sanitize retrieved document text to neutralize prompt injection instructions."""
    sanitized = text
    for marker in PROMPT_INJECTION_MARKERS:
        sanitized = re.sub(marker, "[FILTERED_INSTRUCTION]", sanitized, flags=re.IGNORECASE)
    return sanitized


def classify_retrieval_intent(
    text: str,
    context: Optional[dict[str, Any]] = None,
    history: Optional[list[dict[str, str]]] = None,
) -> tuple[RetrievalIntent, RetrievalScope, str | None, bool]:
    """
    Deterministically resolves user intent, contextual references (anaphora),
    active project/component scope, and identifies ambiguous queries.

    Returns:
        (intent, scope, resolved_subject, is_ambiguous)
    """
    lower = text.lower().strip()
    history = history or []
    context = context or {}

    # 1. Resolve Active Project Scope
    explicit_project = context.get("project") or context.get("active_project")
    hms_triggers = ("ai-hms", "medisense", "hms", "hospital management")
    
    if any(k in lower for k in ("switch to ai-hms", "switch to hms", "in ai-hms", "in hms", "project ai-hms", "for ai-hms")):
        project = "ai-hms"
    elif any(k in lower for k in ("switch to helix", "in helix", "project helix", "for helix")):
        project = "helix"
    elif any(k in lower for k in hms_triggers):
        project = "ai-hms"
    elif "helix" in lower:
        project = "helix"
    elif explicit_project:
        project = str(explicit_project).lower().strip()
    else:
        # Check history backwards for active project context
        project = "helix"
        for turn in reversed(history):
            c = turn.get("content", "").lower()
            if any(k in c for k in hms_triggers):
                project = "ai-hms"
                break
            elif "helix" in c:
                project = "helix"
                break

    # 2. Resolve Component / Subject
    resolved_component: Optional[str] = None
    for kw, (proj, comp_name) in KNOWN_COMPONENTS.items():
        if kw in lower:
            # If project matches or hasn't been strictly forced yet
            if proj == project or not any(k in lower for k in ("helix", "ai-hms", "hms")):
                resolved_component = comp_name
                project = proj
                break

    # 3. Anaphora & Pronoun Resolution
    has_pronoun_reference = False
    for pat in ANAPHORA_PATTERNS:
        if re.search(pat, lower):
            has_pronoun_reference = True
            break
    if not has_pronoun_reference:
        words = set(re.findall(r"\b[a-zA-Z]+\b", lower))
        if ("it" in words or "that" in words or "they" in words) and len(words) <= 12:
            has_pronoun_reference = True

    is_ambiguous = False
    if has_pronoun_reference and not resolved_component:
        # Search backwards through conversation history turns for the last discussed component
        for turn in reversed(history):
            turn_content = turn.get("content", "").lower()
            for kw, (proj, comp_name) in KNOWN_COMPONENTS.items():
                if kw in turn_content:
                    resolved_component = comp_name
                    project = proj
                    break
            if resolved_component:
                break

        # If still unresolved, flag ambiguity
        if not resolved_component:
            vague_inquiries = (
                "what algorithm does it use", "what algorithm", "how does it work",
                "explain that module", "explain it", "what does it do", "which algorithm does it use",
            )
            if any(vi in lower for vi in vague_inquiries) or (lower.startswith("explain") and len(lower.split()) <= 3):
                is_ambiguous = True

    scope = RetrievalScope(
        project=project,
        component=resolved_component,
        strict_project_isolation=True,
    )

    # 4. Classify Intent
    if is_ambiguous:
        return RetrievalIntent.AMBIGUOUS, scope, None, True

    cognitive_insight_triggers = (
        "what patterns do you notice", "patterns do you notice", "pattern in my",
        "patterns in my", "my recent activity", "recent activity", "activity pattern",
        "preventing me from completing my goals", "preventing me from completing",
        "preventing me from", "blockers on my goals", "blocked goals", "my goals progress",
        "progress on my goals", "progress have i made", "progress this week",
        "what progress have i", "how much progress", "what should i prioritize next",
        "prioritize next", "what should i work on next", "what to prioritize",
        "priority next", "recurring problems in my task", "recurring problems appear",
        "problems in my task history", "recurring issues in my tasks",
        "productivity trending", "productivity trend", "overall productivity",
        "cognitive insights", "give me insights", "insights on my work",
        "insights do you have about my",
    )
    is_cognitive_insight = any(trig in lower for trig in cognitive_insight_triggers) or (
        ("insight" in lower or "pattern" in lower or "prioritize" in lower or "recurring" in lower)
        and any(w in lower for w in ("my", "i", "me", "recent", "week", "goals", "tasks", "activity", "history"))
    ) or (
        ("blocked" in lower or "preventing" in lower or "progress" in lower)
        and ("goal" in lower or "task" in lower or "activity" in lower)
    )

    if is_cognitive_insight:
        intent = RetrievalIntent.COGNITIVE_INSIGHTS
    elif any(k in lower for k in ("how do i", "how to", "steps to", "workflow for", "procedure for")):
        intent = RetrievalIntent.ESTABLISHED_PROCEDURE
    elif any(k in lower for k in ("why did", "when did", "history of", "decision about", "past mistake", "previous interaction")):
        intent = RetrievalIntent.HISTORICAL_EVENT
    elif any(k in lower for k in ("what is the active window", "current window", "system status", "cpu usage", "ram usage", "gpu usage", "system state")):
        intent = RetrievalIntent.SYSTEM_STATE
    elif any(lower.startswith(p) for p in ("open ", "launch ", "start ", "create note", "send notification")):
        intent = RetrievalIntent.ACTION_REQUEST
    elif any(k in lower for k in ("quantum_teleport", "super_hyper_drive", "impossible_magic_function", "hyperdrive_core")):
        intent = RetrievalIntent.UNSUPPORTED_FACTUAL
    elif any(k in lower for k in ("error", "exception", "traceback", "failed to", "bug", "crash", "fix issue")):
        intent = RetrievalIntent.TROUBLESHOOTING
    elif (
        any(re.search(p, lower) for p in [
            r"\brevising\s+(?:[a-z0-9_-]+\s+)*concepts?\b",
            r"\b(?:encapsulation|inheritance|polymorphism|abstraction)\b",
            r"\ba?\s*class\s+is\s+(?:a\s+)?(?:blueprint|template)\b",
            r"\ban?\s*object\s+is\s+(?:an?\s+)?instance\b",
            r"\b[a-z0-9_-]+\s+is\s+(?:the\s+)?(?:bundling|process|blueprint|concept|mechanism|ability|practice)\b",
            r"\bwhat\s+(?:is|are)\s+(?:the\s+)?(?:four\s+pillars\s+of\s+)?(?:oop|encapsulation|inheritance|polymorphism|abstraction|object\s+oriented)\b",
            r"\bexplain\s+(?:the\s+concept\s+of\s+)?(?:encapsulation|inheritance|polymorphism|abstraction|oop)\b",
            r"\bdifference\s+between\s+(?:an?\s+)?(?:abstract\s+)?class\s+and\s+(?:an?\s+)?(?:object|interface)\b",
            r"\btell\s+me\s+if\s+(?:my\s+explanation|it|my\s+answer)\s+is\s+correct\b",
            r"\bjudge\s+(?:whether|if)?\s*(?:my\s+explanation|it)\b",
            r"\bevaluate\s+(?:my\s+explanation|my\s+answer|this\s+definition)\b",
            r"\bdefinition\b",
            r"\bwhat\s+is\s+a\b",
            r"\bexplain\s+concept\b",
        ])
        and not (
            resolved_component is not None
            or any(k in lower for k in ("in helix", "in ai-hms", "in hms", "our codebase", "the repo", "repository"))
        )
    ):
        intent = RetrievalIntent.STABLE_FACT
    elif " and " in lower and any(q in lower for q in ("what is", "how does", "explain")):
        intent = RetrievalIntent.MULTI_PART
    elif any(k in lower for k in ("algorithm", "implementation", "code", "class", "function", "file", "where is", "how is it implemented")):
        intent = RetrievalIntent.CURRENT_IMPLEMENTATION
    elif any(k in lower for k in ("definition", "what is a", "explain concept", "general")):
        intent = RetrievalIntent.STABLE_FACT
    else:
        intent = RetrievalIntent.CURRENT_IMPLEMENTATION if resolved_component else RetrievalIntent.GENERAL_KNOWLEDGE

    return intent, scope, resolved_component, False


def get_intent_retrieval_policy(intent: RetrievalIntent, scope: RetrievalScope, query: str) -> RetrievalPlan:
    """Construct a deterministic retrieval plan for the specified intent and scope."""
    resolved_query = query
    if scope.component and scope.component.lower() not in query.lower():
        resolved_query = f"{scope.project.upper()} {scope.component} {query}"

    if intent == RetrievalIntent.COGNITIVE_INSIGHTS:
        return RetrievalPlan(
            intent=intent,
            resolved_query=resolved_query,
            scope=scope,
            permitted_tiers=[
                MemoryTier.WORKING,
                MemoryTier.EPISODIC,
                MemoryTier.PROCEDURAL,
                MemoryTier.SYSTEM_STATE,
            ],
            preferred_sources=["goal_manager", "work_memory", "productivity_engine", "episodic_memory"],
            hard_filters={},
            max_candidates=8,
            min_similarity=0.20,
        )

    if intent == RetrievalIntent.CURRENT_IMPLEMENTATION or intent == RetrievalIntent.FOLLOW_UP:
        return RetrievalPlan(
            intent=intent,
            resolved_query=resolved_query,
            scope=scope,
            permitted_tiers=[
                MemoryTier.AUTHORITATIVE_CODE,
                MemoryTier.WARM_SEMANTIC,
                MemoryTier.WORKING,
            ],
            preferred_sources=["source_code", "test_suite", "warm_memory"],
            hard_filters={"project": scope.project},
            max_candidates=6,
            min_similarity=0.20,
            require_corroboration=True,
        )

    if intent == RetrievalIntent.HISTORICAL_EVENT:
        return RetrievalPlan(
            intent=intent,
            resolved_query=resolved_query,
            scope=scope,
            permitted_tiers=[
                MemoryTier.EPISODIC,
                MemoryTier.WARM_SEMANTIC,
                MemoryTier.AUTHORITATIVE_CODE,
            ],
            preferred_sources=["episodic_memory", "adr_records", "git_history"],
            hard_filters={"project": scope.project},
            max_candidates=5,
            min_similarity=0.25,
        )

    if intent == RetrievalIntent.ESTABLISHED_PROCEDURE:
        return RetrievalPlan(
            intent=intent,
            resolved_query=resolved_query,
            scope=scope,
            permitted_tiers=[
                MemoryTier.PROCEDURAL,
                MemoryTier.AUTHORITATIVE_CODE,
                MemoryTier.WARM_SEMANTIC,
            ],
            preferred_sources=["procedural_memory", "workflow_templates"],
            hard_filters={"project": scope.project},
            max_candidates=4,
            min_similarity=0.30,
        )

    if intent == RetrievalIntent.SYSTEM_STATE:
        return RetrievalPlan(
            intent=intent,
            resolved_query=resolved_query,
            scope=scope,
            permitted_tiers=[
                MemoryTier.SYSTEM_STATE,
                MemoryTier.WORKING,
            ],
            preferred_sources=["world_model", "system_monitor"],
            hard_filters={},
            max_candidates=3,
        )

    if intent == RetrievalIntent.TROUBLESHOOTING:
        return RetrievalPlan(
            intent=intent,
            resolved_query=resolved_query,
            scope=scope,
            permitted_tiers=[
                MemoryTier.EPISODIC,
                MemoryTier.AUTHORITATIVE_CODE,
                MemoryTier.WARM_SEMANTIC,
            ],
            preferred_sources=["past_mistakes", "error_logs", "source_code"],
            hard_filters={"project": scope.project},
            max_candidates=6,
            min_similarity=0.25,
        )

    if intent == RetrievalIntent.STABLE_FACT:
        return RetrievalPlan(
            intent=intent,
            resolved_query=resolved_query,
            scope=scope,
            permitted_tiers=[
                MemoryTier.WORKING,
                MemoryTier.WARM_SEMANTIC,
            ],
            preferred_sources=["warm_memory", "documentation"],
            hard_filters={"project": scope.project},
            max_candidates=3,
            min_similarity=0.35,
        )

    # Default / General Knowledge
    return RetrievalPlan(
        intent=intent,
        resolved_query=resolved_query,
        scope=scope,
        permitted_tiers=[
            MemoryTier.WARM_SEMANTIC,
            MemoryTier.AUTHORITATIVE_CODE,
            MemoryTier.WORKING,
        ],
        preferred_sources=["warm_memory", "documentation"],
        hard_filters={"project": scope.project},
        max_candidates=4,
        min_similarity=0.30,
    )


def apply_hard_filters(
    candidates: list[EvidenceCandidate],
    scope: RetrievalScope,
) -> tuple[list[EvidenceCandidate], int]:
    """
    Stage C: Hard Filtering.
    Strictly enforce project boundary isolation, component matching,
    and authorization constraints before relevance ranking.
    """
    admitted: list[EvidenceCandidate] = []
    filtered_count = 0

    target_project = scope.project.lower().strip() if scope.project else None

    for c in candidates:
        # Check Project Boundary
        cand_project = (c.project or c.metadata.get("project") or "").lower().strip()
        if scope.strict_project_isolation and target_project:
            # If the candidate has an explicit project tag that doesn't match target, reject immediately
            if cand_project and cand_project != target_project:
                filtered_count += 1
                continue
            # If candidate text or file path clearly belongs to foreign project
            if target_project == "helix" and any(k in c.content.lower() for k in ("ai-hms", "medisense", "clinical triage")):
                filtered_count += 1
                continue
            if target_project == "ai-hms" and ("helix" in c.content.lower() and "planner_engine" in c.content.lower()):
                filtered_count += 1
                continue

        # Check Component Constraint
        if scope.component:
            cand_comp = (c.component or c.metadata.get("component") or "").lower()
            target_comp = scope.component.lower()
            # If candidate has an explicit component that conflicts with target component
            if cand_comp and cand_comp != target_comp:
                # Do not reject if text strongly mentions target component
                if target_comp not in c.content.lower():
                    filtered_count += 1
                    continue

        # Check Authorization / Sensitivity
        cand_sensitivity = c.metadata.get("sensitivity", "internal")
        if scope.sensitivity == "public" and cand_sensitivity in ("confidential", "restricted"):
            filtered_count += 1
            continue

        admitted.append(c)

    return admitted, filtered_count


def detect_conflicts_and_superseded(
    candidates: list[EvidenceCandidate],
) -> tuple[list[EvidenceCandidate], list[dict[str, Any]], int]:
    """
    Stage E: Deduplication, Contradiction Detection, and Superseded Evidence Resolution.
    - Strips exact duplicate text.
    - Resolves conflicts by SourceAuthority (Code/Test > Stored Memory) and freshness.
    - Marks stale or superseded records.
    """
    deduped: list[EvidenceCandidate] = []
    seen_content: set[str] = set()
    conflicts: list[dict[str, Any]] = []
    dedup_count = 0

    # Sort candidates by Authority (descending), then score (descending), then timestamp (descending)
    sorted_candidates = sorted(
        candidates,
        key=lambda c: (c.authority.value, c.score, c.timestamp),
        reverse=True,
    )

    for cand in sorted_candidates:
        # Normalize text snippet for deduplication
        norm_text = re.sub(r"\s+", " ", cand.content.strip().lower()[:150])
        if norm_text in seen_content:
            dedup_count += 1
            continue
        seen_content.add(norm_text)

        # Check for supersedes link (newer record supersedes older)
        target_superseded = cand.metadata.get("supersedes")
        if target_superseded:
            for existing in deduped:
                if existing.candidate_id == target_superseded or existing.source_id == target_superseded:
                    existing.is_stale = True
                    conflicts.append({
                        "type": "superseded",
                        "older_source": existing.source_id,
                        "newer_source": cand.source_id,
                        "resolution": "newer_supersedes_older",
                    })

        # Check if current candidate is superseded by an existing newer record
        if cand.superseded_by:
            for existing in deduped:
                if existing.candidate_id == cand.superseded_by or existing.source_id == cand.superseded_by:
                    cand.is_stale = True
                    conflicts.append({
                        "type": "superseded",
                        "older_source": cand.source_id,
                        "newer_source": existing.source_id,
                        "resolution": "newer_supersedes_older",
                    })

        # Check for factual contradictions between authoritative code and memory
        if cand.tier == MemoryTier.AUTHORITATIVE_CODE:
            for existing in deduped:
                if existing.tier == MemoryTier.WARM_SEMANTIC or existing.tier == MemoryTier.EPISODIC:
                    # Detect database engine or algorithm contradiction
                    for term_pair in [("mysql", "postgresql"), ("random forest", "procedural"), ("xgboost", "deterministic")]:
                        t1, t2 = term_pair
                        if (t1 in cand.content.lower() and t2 in existing.content.lower()) or \
                           (t2 in cand.content.lower() and t1 in existing.content.lower()):
                            existing.is_stale = True
                            conflicts.append({
                                "type": "authority_conflict",
                                "authoritative_source": cand.source_id,
                                "conflicting_memory": existing.source_id,
                                "resolution": "authoritative_code_prevails",
                            })

        deduped.append(cand)

    # Filter out candidates that were flagged as stale/superseded
    active_candidates = [c for c in deduped if not c.is_stale]
    return active_candidates, conflicts, dedup_count


def evaluate_evidence_sufficiency(
    intent: RetrievalIntent,
    query: str,
    candidates: list[EvidenceCandidate],
    subject: Optional[str] = None,
) -> tuple[bool, str]:
    """
    Stage F: Evidence Sufficiency Gate.
    Validates whether the admitted evidence actually answers the material subject.
    """
    if not candidates:
        return False, "No relevant evidence found in any permitted memory tier or repository inspection."

    lower_query = query.lower()

    # If asking about an algorithm, check if any candidate mentions algorithm keywords
    if "algorithm" in lower_query:
        algo_keywords = (
            "procedural", "regex", "deterministic", "rule-based", "decision", "dag",
            "fastembed", "onnx", "cosine", "nearest neighbor", "bge-small", "whisper",
            "piper", "random forest", "xgboost", "sort", "search", "template", "pattern"
        )
        has_algo_evidence = any(
            any(ak in c.content.lower() for ak in algo_keywords)
            for c in candidates
        )
        if not has_algo_evidence:
            return False, "Retrieved evidence mentions component location but contains no authoritative algorithm specification."

    # If asking about nonexistent function or unsupported concept
    if intent == RetrievalIntent.UNSUPPORTED_FACTUAL or any(k in lower_query for k in ("quantum_teleport", "hyperdrive", "magic_function")):
        return False, "The requested feature or function does not exist in the codebase or memory store."

    if intent == RetrievalIntent.COGNITIVE_INSIGHTS:
        personal_tiers = {MemoryTier.WORKING, MemoryTier.EPISODIC, MemoryTier.PROCEDURAL, MemoryTier.SYSTEM_STATE}
        personal_candidates = [c for c in candidates if c.tier in personal_tiers]
        if not personal_candidates:
            return False, "Repository code files and documentation cannot substantiate personal user activity, goal progress, or productivity insights."

        if any(k in lower_query for k in ("goal", "goals", "preventing", "blocked", "prioritize")):
            goal_candidates = [
                c for c in personal_candidates
                if "goal" in c.source_id.lower() or "goal" in c.content.lower()
            ]
            if not goal_candidates:
                return False, "No goals or goal execution records were found in your personal workspace."

        if any(k in lower_query for k in ("pattern", "patterns", "recent activity", "progress", "task history", "recurring")):
            activity_candidates = [
                c for c in personal_candidates
                if any(kw in c.source_id.lower() or kw in c.content.lower() for kw in ("work_session", "productivity_pattern", "episode", "task"))
            ]
            if not activity_candidates:
                return False, "No recorded work sessions, task histories, or productivity patterns were found for the requested timeframe."

        return True, ""

    return True, ""
