"""
Fact-First Verification System Models for HELIX.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class EvidenceType(str, enum.Enum):
    STABLE_KNOWLEDGE = "stable_knowledge"        # Definitions, unchanging facts, general logic
    CURRENT_INFORMATION = "current_information"  # Real-time data, web facts, current time/dates
    REPOSITORY_INSPECTION = "repository_inspection"  # Local files, configs, git state, code
    MEMORY_RETRIEVAL = "memory_retrieval"        # Personal user context, stored preferences, episodes
    COMPUTATION = "computation"                  # Math, calculations, deterministic logic
    ACTION_EXECUTION = "action_execution"        # Execution of tools or system actions


class ClaimType(str, enum.Enum):
    FACTUAL_CLAIM = "factual_claim"  # Concrete verifiable statement of fact
    INFERENCE = "inference"          # Logical deduction from facts
    ASSUMPTION = "assumption"        # Working assumption (not confirmed)
    ESTIMATE = "estimate"            # Approximate figure or metric
    RECOMMENDATION = "recommendation"  # Subjective suggestion, guidance, or next step
    CONVERSATIONAL = "conversational"  # Greetings, pleasantries, questions, conversational framing
    OPINION = "opinion"              # Subjective perspective, sentiment, or personal impression
    CREATIVE = "creative"            # Creative writing, stories, poems, hypothetical concepts
    CONCEPTUAL_EXPLANATION = "conceptual_explanation"  # Educational, pedagogical, or general technical tutorials
    NOT_APPLICABLE = "not_applicable"  # Meta statements, acknowledgments, or non-verifiable content


class VerificationStatus(str, enum.Enum):
    SUPPORTED = "supported"                      # Directly confirmed by evidence
    PARTIALLY_SUPPORTED = "partially_supported"  # Some details confirmed, minor gaps
    CONTRADICTED = "contradicted"                # In direct conflict with trustworthy evidence
    UNVERIFIED = "unverified"                    # Lacks sufficient supporting evidence
    STALE = "stale"                              # Evidence exists but is outdated / expired
    NOT_APPLICABLE = "not_applicable"            # Claim is non-factual, creative, or out-of-scope for verification


class ProvenanceSource(str, enum.Enum):
    USER_STATED = "user_stated"          # Direct user input (highest priority for personal facts)
    OBSERVED_SYSTEM = "observed_system"  # Inspected from OS, filesystem, git, or process table
    VERIFIED_ACTION = "verified_action"  # Outcome of an action that passed postconditions
    INFERRED = "inferred"                # Inferred by LLM or heuristics
    UNVERIFIED_MODEL = "unverified_model"  # Raw model output, not independently confirmed


@dataclass
class EvidenceItem:
    """An atomic piece of evidence used for fact verification."""
    source_id: str
    source_type: EvidenceType
    content: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)
    provenance: ProvenanceSource = ProvenanceSource.OBSERVED_SYSTEM
    confidence: float = 1.0
    is_stale: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type.value,
            "content": self.content,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
            "provenance": self.provenance.value,
            "confidence": self.confidence,
            "is_stale": self.is_stale,
        }


@dataclass
class EvidenceRequirement:
    """Classified evidence requirements for a given query."""
    primary_type: EvidenceType
    secondary_types: list[EvidenceType] = field(default_factory=list)
    requires_freshness: bool = False
    max_evidence_age_seconds: Optional[int] = None
    target_entities: list[str] = field(default_factory=list)
    recommended_tools: list[str] = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class ExtractedClaim:
    """A single factual claim or proposition extracted from text."""
    claim_id: str
    text: str
    claim_type: ClaimType
    subject: Optional[str] = None
    predicate: Optional[str] = None
    object_value: Optional[str] = None
    citation: Optional[str] = None
    is_material: bool = True
    domain: str = "general"

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "text": self.text,
            "claim_type": self.claim_type.value,
            "subject": self.subject,
            "predicate": self.predicate,
            "object_value": self.object_value,
            "citation": self.citation,
            "is_material": self.is_material,
            "domain": self.domain,
        }


@dataclass
class ClaimVerificationResult:
    """The outcome of verifying an extracted claim against evidence."""
    claim: ExtractedClaim
    status: VerificationStatus
    supporting_evidence_ids: list[str] = field(default_factory=list)
    contradiction_reason: Optional[str] = None
    confidence_score: float = 0.0
    notes: str = ""
    is_material: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim.to_dict(),
            "status": self.status.value,
            "supporting_evidence_ids": self.supporting_evidence_ids,
            "contradiction_reason": self.contradiction_reason,
            "confidence_score": self.confidence_score,
            "notes": self.notes,
            "is_material": self.is_material,
        }


@dataclass
class VerificationPolicy:
    """Configurable policy governing claim verification and gating."""
    strict_mode: bool = False
    max_refinement_attempts: int = 2
    allow_unverified_recommendations: bool = True
    allow_unverified_inferences: bool = False
    allow_conceptual_explanations: bool = True
    preserve_uncontradicted_explanations: bool = True
    min_evidence_support_rate: float = 0.8
    enforce_citations: bool = True
    max_evidence_age_seconds: int = 86400 * 7  # 7 days default
    acknowledge_uncertainty_threshold: float = 0.5
    strict_domains: list[str] = field(
        default_factory=lambda: ["medical", "legal", "financial", "security", "credentials", "system_authorization"]
    )


@dataclass
class ResponseVerificationReport:
    """Summary report of the claim verification pass on a candidate response."""
    passed: bool
    total_claims: int
    supported_count: int
    partially_supported_count: int
    unverified_count: int
    contradicted_count: int
    stale_count: int
    claim_results: list[ClaimVerificationResult] = field(default_factory=list)
    support_rate: float = 0.0
    unsupported_rate: float = 0.0
    needs_abstention: bool = False
    uncertainties: list[str] = field(default_factory=list)
    sanitized_response: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "total_claims": self.total_claims,
            "supported_count": self.supported_count,
            "partially_supported_count": self.partially_supported_count,
            "unverified_count": self.unverified_count,
            "contradicted_count": self.contradicted_count,
            "stale_count": self.stale_count,
            "support_rate": self.support_rate,
            "unsupported_rate": self.unsupported_rate,
            "needs_abstention": self.needs_abstention,
            "uncertainties": self.uncertainties,
            "claim_results": [cr.to_dict() for cr in self.claim_results],
        }
