"""
HELIX — Production-Grade RAG Data Models & Schemas for Personal AI Agent Platform.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from pydantic import BaseModel, Field


class RetrievalIntent(str, enum.Enum):
    FOLLOW_UP = "follow_up"
    CURRENT_IMPLEMENTATION = "current_implementation"
    HISTORICAL_EVENT = "historical_event"
    STABLE_FACT = "stable_fact"
    ESTABLISHED_PROCEDURE = "established_procedure"
    GENERAL_KNOWLEDGE = "general_knowledge"
    MULTI_PART = "multi_part"
    TROUBLESHOOTING = "troubleshooting"
    SYSTEM_STATE = "system_state"
    ACTION_REQUEST = "action_request"
    AMBIGUOUS = "ambiguous"
    UNSUPPORTED_FACTUAL = "unsupported_factual"
    COGNITIVE_INSIGHTS = "cognitive_insights"


class MemoryTier(str, enum.Enum):
    WORKING = "working"                    # Current conversation, active task context
    EPISODIC = "episodic"                  # Past episodes, interaction friction, lessons
    WARM_SEMANTIC = "warm_semantic"        # Qdrant/FastEmbed semantic vector memories
    PROCEDURAL = "procedural"              # Learned workflows, recurring steps
    AUTHORITATIVE_CODE = "authoritative_code" # Current workspace files, configs, tests
    SYSTEM_STATE = "system_state"          # Active windows, foreground process, telemetry


class SourceAuthority(int, enum.Enum):
    AUTHORITATIVE_CODE = 100   # Code, test assertions, config files
    DECISION_RECORD = 85      # Architectural decision records (ADR)
    VERIFIED_ACTION = 75      # Empirically verified OS/process state
    USER_STATED = 65          # Explicit user instruction or verified preference
    OBSERVED_STATE = 50       # Window titles, hardware metrics
    STORED_MEMORY = 40        # Semantic warm memories, episodic recall
    INFERRED = 20             # Heuristic deduction
    UNVERIFIED = 10           # Uncorroborated claim / raw generation


@dataclass
class RetrievalScope:
    project: str = "helix"
    component: Optional[str] = None
    user_id: Optional[str] = None
    sensitivity: str = "internal"          # public | internal | confidential | restricted
    min_freshness_seconds: Optional[int] = None
    strict_project_isolation: bool = True
    allowed_categories: list[str] = field(default_factory=list)


@dataclass
class RetrievalPlan:
    intent: RetrievalIntent
    resolved_query: str
    scope: RetrievalScope
    permitted_tiers: list[MemoryTier]
    preferred_sources: list[str] = field(default_factory=list)
    hard_filters: dict[str, Any] = field(default_factory=dict)
    max_candidates: int = 6
    min_similarity: float = 0.25
    timeout_seconds: float = 2.5
    require_corroboration: bool = False


@dataclass
class EvidenceCandidate:
    candidate_id: str
    content: str
    source_id: str
    tier: MemoryTier
    authority: SourceAuthority
    project: Optional[str] = None
    component: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    score: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    is_stale: bool = False
    superseded_by: Optional[str] = None
    citation: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "content": self.content,
            "source_id": self.source_id,
            "tier": self.tier.value,
            "authority": self.authority.value,
            "project": self.project,
            "component": self.component,
            "timestamp": self.timestamp,
            "score": self.score,
            "metadata": self.metadata,
            "is_stale": self.is_stale,
            "superseded_by": self.superseded_by,
            "citation": self.citation,
        }


@dataclass
class EvidencePackage:
    resolved_question: str
    resolved_subject: Optional[str]
    scope: RetrievalScope
    intent: RetrievalIntent
    conversation_context: list[dict[str, str]]
    retrieved_evidence: list[EvidenceCandidate]
    conflicting_evidence: list[dict[str, Any]] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)
    is_sufficient: bool = True
    missing_evidence_notes: str = ""
    authorization_passed: bool = True
    abstention_required: bool = False
    clarification_prompt: Optional[str] = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_prompt_context(self) -> str:
        """Format verified evidence into sanitized LLM context."""
        if not self.retrieved_evidence:
            if self.abstention_required:
                return "<|evidence_gap|>\nNo authoritative evidence supports answering this specific question with certainty.</s>"
            return ""

        lines = [f"<|context:retrieved_evidence project='{self.scope.project.upper()}'>"]
        for idx, ev in enumerate(self.retrieved_evidence, 1):
            src = ev.source_id or ev.tier.value
            lines.append(f"[{idx}] (Source: {src} | Authority: {ev.authority.name} | Score: {ev.score:.2f})")
            lines.append(ev.content.strip())
            lines.append("")
        lines.append("</context:retrieved_evidence>")
        return "\n".join(lines)


@dataclass
class RetrievalDiagnostics:
    request_id: str
    raw_query: str
    resolved_query: str
    intent: str
    project: str
    component: Optional[str]
    stage_latencies_ms: dict[str, float] = field(default_factory=dict)
    candidates_retrieved: int = 0
    candidates_admitted: int = 0
    hard_filtered_count: int = 0
    deduplicated_count: int = 0
    conflicts_detected: int = 0
    is_sufficient: bool = True
    fallback_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "raw_query": self.raw_query,
            "resolved_query": self.resolved_query,
            "intent": self.intent,
            "project": self.project,
            "component": self.component,
            "stage_latencies_ms": self.stage_latencies_ms,
            "candidates_retrieved": self.candidates_retrieved,
            "candidates_admitted": self.candidates_admitted,
            "hard_filtered_count": self.hard_filtered_count,
            "deduplicated_count": self.deduplicated_count,
            "conflicts_detected": self.conflicts_detected,
            "is_sufficient": self.is_sufficient,
            "fallback_reason": self.fallback_reason,
        }
