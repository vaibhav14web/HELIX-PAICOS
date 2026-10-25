"""
Claim Verification Layer for HELIX Fact-First Verification Architecture.

Extracts verifiable factual claims, distinguishes facts from inferences and recommendations,
matches claims to evidence, verifies citations, detects contradictions and staleness,
and assigns explicit VerificationStatus values.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from core_ai.verification.models import (
    ClaimType,
    ClaimVerificationResult,
    EvidenceItem,
    EvidenceType,
    ExtractedClaim,
    ProvenanceSource,
    ResponseVerificationReport,
    VerificationPolicy,
    VerificationStatus,
)

logger = logging.getLogger("helix.verification.claim_verifier")


# Lexical patterns for claim classification
_RECOMMENDATION_TRIGGERS = (
    "recommend", "suggest", "advisable", "should", "consider", "try to", "you could",
    "you can", "it is recommended", "best practice", "please ensure", "make sure to"
)
_ASSUMPTION_TRIGGERS = (
    "assuming", "presuming", "suppose", "if we assume", "hypothetically", "given that"
)
_ESTIMATE_TRIGGERS = (
    "approximately", "roughly", "around", "estimated", "about", "nearly", "close to", "~"
)
_INFERENCE_TRIGGERS = (
    "therefore", "thus", "consequently", "implies", "indicates that", "suggests that",
    "as a result", "we can infer", "leads to the conclusion"
)


_CONVERSATIONAL_TRIGGERS = (
    "hello", "hi", "hey", "sure", "certainly", "thank you", "thanks", "you're welcome",
    "i am helix", "i'm helix", "as your", "how can i", "what would you like",
    "let me know", "feel free", "i understand", "i can help", "regarding your",
    "no problem", "glad to help", "have a great", "could you", "would you like",
    "i apologize", "sorry", "here is", "here are", "good morning", "good evening"
)

_CREATIVE_TRIGGERS = (
    "once upon a time", "in a world where", "imagine that", "poem:", "story:",
    "haiku:", "let's pretend", "hypothetically speaking", "roleplay", "write a poem", "write a story"
)

_CONCEPTUAL_TRIGGERS = (
    "to optimize", "how to", "you can configure", "in python,", "in javascript,",
    "coroutine", "event loop", "caching works", "architecture pattern",
    "typically,", "generally,", "by default,", "a common approach", "for example,",
    "ctranslate2", "faster whisper", "faster-whisper", "pip install"
)

_UNCERTAINTY_TRIGGERS = (
    "cannot fully confirm", "cannot confirm", "remains unverified", "remain unverified",
    "cannot be verified", "could not be verified", "no verified evidence", "uncertain whether",
    "i do not have enough information", "i cannot verify", "not enough information",
    "factual verification notice", "uncertainty:", "uncertainties:"
)

_STRICT_DOMAINS = {
    "medical": ("diagnosis", "dosage", "prescribe", "treatment", "triage", "patient", "disease", "symptom", "clinical", "medication", "mg/kg", "mg daily"),
    "legal": ("lawsuit", "statute", "illegal", "copyright infringement", "liability", "attorney", "contractual obligation"),
    "financial": ("investment", "stock buy", "bank account", "transfer funds", "credit card", "crypto wallet", "roi"),
    "security": ("private key", "password", "auth token", "sudo", "bypass security", "exploit", "cve-"),
    "system_authorization": ("grant permission", "execute elevated", "format drive", "delete system"),
}


class ClaimVerifier:
    """Extracts and verifies factual claims against acquired evidence."""

    def __init__(self, policy: Optional[VerificationPolicy] = None):
        self.policy = policy or VerificationPolicy()

    def extract_claims(self, text: str) -> list[ExtractedClaim]:
        """
        Decompose text into distinct propositional sentences and classify their claim types.
        """
        if not text or not text.strip():
            return []

        # Split text into sentences
        raw_sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9`\"'])", text.strip())
        claims: list[ExtractedClaim] = []

        claim_idx = 1
        for sent in raw_sentences:
            s_clean = sent.strip()
            if not s_clean or len(s_clean) < 5:
                continue

            lower_s = s_clean.lower()

            # Detect Citation tags (e.g. [source: main.py], [pyproject.toml], (source: X))
            citation = None
            cite_match = re.search(r"\[(?:source:\s*)?([a-zA-Z0-9_\-\.\/]+)\]|\((?:source:\s*)([a-zA-Z0-9_\-\.\/]+)\)", s_clean)
            if cite_match:
                citation = cite_match.group(1) or cite_match.group(2)

            # Classify domain
            domain = "general"
            for d_name, d_keywords in _STRICT_DOMAINS.items():
                if any(kw in lower_s for kw in d_keywords):
                    domain = d_name
                    break

            # Classify claim type
            is_material = True
            if any(trig in lower_s for trig in _CREATIVE_TRIGGERS):
                ctype = ClaimType.CREATIVE
                is_material = False
            elif any(trig in lower_s for trig in _RECOMMENDATION_TRIGGERS):
                ctype = ClaimType.RECOMMENDATION
                is_material = False
            elif any(trig in lower_s for trig in _ASSUMPTION_TRIGGERS):
                ctype = ClaimType.ASSUMPTION
                is_material = False
            elif any(trig in lower_s for trig in _ESTIMATE_TRIGGERS):
                ctype = ClaimType.ESTIMATE
                is_material = False
            elif any(trig in lower_s for trig in _INFERENCE_TRIGGERS):
                ctype = ClaimType.INFERENCE
                is_material = False
            elif s_clean.endswith("?") or any(lower_s.startswith(trig) for trig in _CONVERSATIONAL_TRIGGERS):
                ctype = ClaimType.CONVERSATIONAL
                is_material = False
            elif any(trig in lower_s for trig in _CONCEPTUAL_TRIGGERS):
                ctype = ClaimType.CONCEPTUAL_EXPLANATION
                is_material = False
            elif any(trig in lower_s for trig in _UNCERTAINTY_TRIGGERS):
                ctype = ClaimType.NOT_APPLICABLE
                is_material = False
            else:
                ctype = ClaimType.FACTUAL_CLAIM
                is_version = bool(re.search(r"\bversion\s*(?:is|=|:)?\s*['\"]?\d+", lower_s))
                is_file = bool(re.search(r"\b[a-zA-Z0-9_\-\.\/]+\.(?:py|json|md|toml|txt|html|css|js|ts)\b", s_clean))
                is_strict_domain = domain in self.policy.strict_domains
                is_material = bool(citation or is_version or is_file or is_strict_domain or "helix" in lower_s)

            # Extract subject / predicate / object heuristics where possible
            subject = None
            predicate = None
            object_val = None

            # Simple entity-predicate parsing (e.g. "X is Y", "X version is Y", "X has Y")
            rel_match = re.search(r"^([^,]+?)\s+(is|are|was|were|has|contains|requires|uses|version is)\s+([^.]+)", s_clean, re.IGNORECASE)
            if rel_match:
                subject = rel_match.group(1).strip()
                predicate = rel_match.group(2).strip()
                object_val = rel_match.group(3).strip()

            claims.append(
                ExtractedClaim(
                    claim_id=f"claim_{claim_idx}",
                    text=s_clean,
                    claim_type=ctype,
                    subject=subject,
                    predicate=predicate,
                    object_value=object_val,
                    citation=citation,
                    is_material=is_material,
                    domain=domain,
                )
            )
            claim_idx += 1

        return claims

    def verify_claim(
        self,
        claim: ExtractedClaim,
        evidence: list[EvidenceItem],
    ) -> ClaimVerificationResult:
        """
        Verify a single extracted claim against acquired evidence items.
        """
        is_strict_domain = claim.domain in self.policy.strict_domains

        # 1. Non-verifiable conversational or creative dialogue
        if claim.claim_type in (ClaimType.CREATIVE, ClaimType.CONVERSATIONAL, ClaimType.NOT_APPLICABLE):
            return ClaimVerificationResult(
                claim=claim,
                status=VerificationStatus.NOT_APPLICABLE,
                supporting_evidence_ids=[],
                confidence_score=1.0,
                notes=f"Accepted as {claim.claim_type.value}.",
                is_material=False,
            )

        # 2. Recommendations, Assumptions, and Opinions
        if claim.claim_type in (ClaimType.RECOMMENDATION, ClaimType.ASSUMPTION, ClaimType.OPINION):
            if not is_strict_domain and self.policy.allow_unverified_recommendations:
                return ClaimVerificationResult(
                    claim=claim,
                    status=VerificationStatus.NOT_APPLICABLE,
                    supporting_evidence_ids=[],
                    confidence_score=0.9,
                    notes=f"Accepted as guidance {claim.claim_type.value}.",
                    is_material=False,
                )

        claim_text_lower = claim.text.lower()
        supporting_ids: list[str] = []
        contradiction_reason: Optional[str] = None
        is_stale = False

        # 1. Citation Verification (detect fabricated or misleading citations)
        if claim.citation:
            cited_evidence = [
                ev for ev in evidence
                if claim.citation.lower() in ev.source_id.lower() or ev.source_id.lower() in claim.citation.lower()
            ]
            if not cited_evidence:
                return ClaimVerificationResult(
                    claim=claim,
                    status=VerificationStatus.UNVERIFIED,
                    contradiction_reason=f"Fabricated or missing citation: source '{claim.citation}' was not found in acquired evidence.",
                    confidence_score=0.0,
                    notes="Citation does not match any known trustworthy evidence source.",
                )

        # 2. Check for Contradictions across evidence items
        for ev in evidence:
            ev_content_lower = ev.content.lower()

            # Check staleness
            if ev.is_stale:
                is_stale = True

            combined_ev_text = f"{ev.source_id} {ev_content_lower}".lower()

            # Direct contradiction heuristics:
            # 1. Version contradiction check
            if "version" in claim_text_lower and "version" in ev_content_lower:
                ver_claim = re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b", claim_text_lower)
                ver_ev = (
                    re.search(r"\bversion\s*=\s*['\"](\d+\.\d+(?:\.\d+)?)['\"]", ev_content_lower)
                    or re.search(r"\bversion\s*[:=]\s*(\d+\.\d+(?:\.\d+)?)", ev_content_lower)
                    or re.search(r"\bversion\s+is\s+(\d+\.\d+(?:\.\d+)?)", ev_content_lower)
                )
                if ver_claim and ver_ev and ver_claim.group(1) != ver_ev.group(1):
                    contradiction_reason = f"Contradiction with source {ev.source_id}: claims version {ver_claim.group(1)}, but evidence states {ver_ev.group(1)}."
                    return ClaimVerificationResult(
                        claim=claim,
                        status=VerificationStatus.CONTRADICTED,
                        contradiction_reason=contradiction_reason,
                        confidence_score=0.0,
                    )

            # 2. Boolean state conflict (e.g. "is True" vs "False")
            if claim.object_value:
                obj_lower = claim.object_value.lower()
                if ("true" in obj_lower and "false" in ev_content_lower) or ("false" in obj_lower and "true" in ev_content_lower):
                    contradiction_reason = f"Contradiction with source {ev.source_id}: boolean state mismatch."
                    return ClaimVerificationResult(
                        claim=claim,
                        status=VerificationStatus.CONTRADICTED,
                        contradiction_reason=contradiction_reason,
                        confidence_score=0.0,
                    )

            # 3. Check negation contradiction
            negation_pattern = rf"\bnot\s+{re.escape(claim.predicate or '')}" if claim.predicate else None
            if negation_pattern and re.search(negation_pattern, claim_text_lower) and not re.search(r"\bnot\b", ev_content_lower):
                if claim.subject and claim.object_value and claim.subject.lower() in combined_ev_text and claim.object_value.lower() in combined_ev_text:
                    contradiction_reason = f"Contradiction with source {ev.source_id}: claim asserts negative but evidence confirms positive."
                    return ClaimVerificationResult(
                        claim=claim,
                        status=VerificationStatus.CONTRADICTED,
                        contradiction_reason=contradiction_reason,
                        confidence_score=0.0,
                    )

            # 4. Mutability conflict check (e.g. lists immutable vs mutable)
            if "immutable" in claim_text_lower:
                for ent in ("list", "lists", "array", "arrays", "dict", "dicts", "dictionary", "set", "sets"):
                    if ent in claim_text_lower and re.search(rf"\b{ent}\s+(?:are|is)\s+mutable\b", ev_content_lower):
                        contradiction_reason = f"Contradiction with source {ev.source_id}: {ent} are mutable, but claim asserts immutable."
                        return ClaimVerificationResult(
                            claim=claim,
                            status=VerificationStatus.CONTRADICTED,
                            contradiction_reason=contradiction_reason,
                            confidence_score=0.0,
                        )
            elif "mutable" in claim_text_lower and "immutable" not in claim_text_lower:
                for ent in ("tuple", "tuples", "string", "strings", "int", "frozenset"):
                    if ent in claim_text_lower and re.search(rf"\b{ent}\s+(?:are|is)\s+immutable\b", ev_content_lower):
                        contradiction_reason = f"Contradiction with source {ev.source_id}: {ent} are immutable, but claim asserts mutable."
                        return ClaimVerificationResult(
                            claim=claim,
                            status=VerificationStatus.CONTRADICTED,
                            contradiction_reason=contradiction_reason,
                            confidence_score=0.0,
                        )

        # 3. Check for Evidence Support
        max_match_ratio = 0.0
        best_partial_source = None

        for ev in evidence:
            ev_content_lower = ev.content.lower()
            combined_ev_text = f"{ev.source_id} {ev_content_lower}".lower()

            # Deterministic math evidence check
            if ev.source_type.value == "computation":
                meta_res = str(ev.metadata.get("result", ""))
                if meta_res and meta_res in claim_text_lower:
                    supporting_ids.append(ev.source_id)
                    continue

            # Tokenize claim into significant terms including alphanumeric and dotted versions
            stop_words = {"the", "a", "an", "is", "are", "was", "were", "and", "or", "in", "on", "at", "to", "for", "of", "with", "this", "that", "it"}
            claim_tokens = [w for w in re.findall(r"\b[a-zA-Z0-9_\.]{2,}\b", claim_text_lower) if w not in stop_words]

            if not claim_tokens:
                continue

            matched_tokens = [t for t in claim_tokens if t in combined_ev_text]
            match_ratio = len(matched_tokens) / len(claim_tokens)
            if match_ratio > max_match_ratio:
                max_match_ratio = match_ratio
                best_partial_source = ev.source_id

            # If citation explicitly points to this source, threshold is more specific
            if claim.citation and (claim.citation.lower() in ev.source_id.lower() or ev.source_id.lower() in claim.citation.lower()):
                if match_ratio >= 0.4:
                    supporting_ids.append(ev.source_id)
            elif match_ratio >= 0.5:
                supporting_ids.append(ev.source_id)

        # 4. Synthesize final status
        if is_stale and supporting_ids:
            return ClaimVerificationResult(
                claim=claim,
                status=VerificationStatus.STALE,
                supporting_evidence_ids=supporting_ids,
                confidence_score=0.4,
                notes="Supporting evidence was retrieved but is considered stale.",
            )

        if len(supporting_ids) > 0:
            # Check evidential conflict / competing evidence items
            conflicting_evidence = [
                ev for ev in evidence
                if ev.metadata.get("conflicts_with") in supporting_ids
                or (ev.metadata.get("contradicts_claim") and claim.claim_id in str(ev.metadata.get("contradicts_claim")))
            ]
            if conflicting_evidence:
                conf_scores = [getattr(ev, "confidence", 1.0) for ev in conflicting_evidence]
                supp_scores = [getattr(ev, "confidence", 1.0) for ev in evidence if ev.source_id in supporting_ids]
                max_conf = max(conf_scores) if conf_scores else 1.0
                max_supp = max(supp_scores) if supp_scores else 1.0
                delta = abs(max_supp - max_conf)
                if delta < 0.20:
                    return ClaimVerificationResult(
                        claim=claim,
                        status=VerificationStatus.CONTRADICTED,
                        contradiction_reason=f"Ambiguous competing evidence: delta ({delta:.2f}) < 0.20 margin.",
                        confidence_score=0.0,
                        notes="Unresolved evidential contradiction detected between conflicting evidence items.",
                    )

            # Check whether supporting evidence is authoritative or solely from untrusted retrieved material
            has_authoritative = False
            ev_confidences = []
            for ev in evidence:
                if ev.source_id in supporting_ids:
                    ev_confidences.append(getattr(ev, "confidence", 1.0))
                    is_untrusted_retrieved = (
                        ev.provenance in (ProvenanceSource.UNVERIFIED_MODEL, ProvenanceSource.INFERRED)
                        or ev.source_type == EvidenceType.MEMORY_RETRIEVAL
                        or ev.metadata.get("source_type") in ("UNVERIFIED", "STORED_MEMORY", "untrusted_web", "untrusted_memory")
                        or ev.metadata.get("is_untrusted", False)
                    )
                    if not is_untrusted_retrieved:
                        has_authoritative = True

            base_confidence = max(ev_confidences) if ev_confidences else 1.0

            if not has_authoritative:
                return ClaimVerificationResult(
                    claim=claim,
                    status=VerificationStatus.PARTIALLY_SUPPORTED,
                    supporting_evidence_ids=supporting_ids,
                    confidence_score=min(0.50, base_confidence),
                    notes=f"Contained in untrusted retrieved material only ({', '.join(supporting_ids)}); not corroborated by authoritative application state.",
                    is_material=claim.is_material,
                )

            # Strict domain escalation: require confidence >= 0.85
            if is_strict_domain and base_confidence < 0.85:
                return ClaimVerificationResult(
                    claim=claim,
                    status=VerificationStatus.PARTIALLY_SUPPORTED,
                    supporting_evidence_ids=supporting_ids,
                    confidence_score=base_confidence,
                    notes=f"Strict domain '{claim.domain}' requires confidence >= 0.85, but evidence confidence is {base_confidence:.2f}.",
                    is_material=claim.is_material,
                )

            return ClaimVerificationResult(
                claim=claim,
                status=VerificationStatus.SUPPORTED,
                supporting_evidence_ids=supporting_ids,
                confidence_score=min(0.95, base_confidence),
                notes=f"Confirmed by authoritative evidence sources: {', '.join(supporting_ids)}",
            )

        # Partial support only when at least 35% of tokens are corroborated
        if max_match_ratio >= 0.35 and best_partial_source:
            return ClaimVerificationResult(
                claim=claim,
                status=VerificationStatus.PARTIALLY_SUPPORTED,
                supporting_evidence_ids=[best_partial_source],
                confidence_score=0.6,
                notes=f"Partially supported ({round(max_match_ratio*100)}% token match in {best_partial_source}).",
            )

        # Inferences that are allowed
        if claim.claim_type == ClaimType.INFERENCE and self.policy.allow_unverified_inferences:
            return ClaimVerificationResult(
                claim=claim,
                status=VerificationStatus.PARTIALLY_SUPPORTED,
                supporting_evidence_ids=[],
                confidence_score=0.5,
                notes="Accepted as an unverified logical inference per policy.",
                is_material=False,
            )

        # Conceptual explanations in non-strict domains: uncontradicted explanations are preserved
        if claim.claim_type == ClaimType.CONCEPTUAL_EXPLANATION and self.policy.allow_conceptual_explanations and not is_strict_domain:
            return ClaimVerificationResult(
                claim=claim,
                status=VerificationStatus.NOT_APPLICABLE,
                supporting_evidence_ids=[],
                confidence_score=0.85,
                notes="Conceptual explanation accepted without contradiction.",
                is_material=False,
            )

        return ClaimVerificationResult(
            claim=claim,
            status=VerificationStatus.UNVERIFIED,
            supporting_evidence_ids=[],
            confidence_score=0.0,
            notes="No sufficient supporting evidence found.",
            is_material=claim.is_material,
        )

    def verify_response(
        self,
        candidate_response: str,
        evidence: list[EvidenceItem],
    ) -> ResponseVerificationReport:
        """
        Verify all claims in a candidate response and produce an audit report.
        """
        claims = self.extract_claims(candidate_response)

        if not claims:
            return ResponseVerificationReport(
                passed=True,
                total_claims=0,
                supported_count=0,
                partially_supported_count=0,
                unverified_count=0,
                contradicted_count=0,
                stale_count=0,
                support_rate=1.0,
                unsupported_rate=0.0,
                needs_abstention=False,
                sanitized_response=candidate_response,
            )

        results = [self.verify_claim(c, evidence) for c in claims]

        supported_cnt = sum(1 for r in results if r.status == VerificationStatus.SUPPORTED)
        partially_cnt = sum(1 for r in results if r.status == VerificationStatus.PARTIALLY_SUPPORTED)
        unverified_cnt = sum(1 for r in results if r.status == VerificationStatus.UNVERIFIED)
        contradicted_cnt = sum(1 for r in results if r.status == VerificationStatus.CONTRADICTED)
        stale_cnt = sum(1 for r in results if r.status == VerificationStatus.STALE)

        total = len(claims)
        not_applicable_cnt = sum(1 for r in results if r.status == VerificationStatus.NOT_APPLICABLE)
        verifiable_total = total - not_applicable_cnt

        if verifiable_total == 0:
            support_rate = 1.0 if contradicted_cnt == 0 else 0.0
            unsupported_rate = 0.0 if contradicted_cnt == 0 else 1.0
        else:
            effective_supported = supported_cnt + 0.5 * partially_cnt
            support_rate = round(effective_supported / verifiable_total, 3)
            unsupported_rate = round((unverified_cnt + contradicted_cnt + stale_cnt) / verifiable_total, 3)

        # Check for strict domain violations (e.g. medical, legal, financial, security)
        has_strict_domain_violation = any(
            r.claim.domain in self.policy.strict_domains and r.status in (
                VerificationStatus.UNVERIFIED,
                VerificationStatus.CONTRADICTED,
                VerificationStatus.PARTIALLY_SUPPORTED,
                VerificationStatus.STALE,
            )
            for r in results
        )

        passed = (
            contradicted_cnt == 0
            and not has_strict_domain_violation
            and (
                (support_rate >= self.policy.min_evidence_support_rate if self.policy.strict_mode else unsupported_rate <= 0.4)
                if verifiable_total > 0 else True
            )
        )

        needs_abstention = (
            contradicted_cnt > 0
            or has_strict_domain_violation
            or (verifiable_total > 0 and support_rate < self.policy.acknowledge_uncertainty_threshold)
        )

        uncertainties: list[str] = []
        for r in results:
            if (
                r.status in (VerificationStatus.UNVERIFIED, VerificationStatus.CONTRADICTED, VerificationStatus.STALE)
                or (r.claim.domain in self.policy.strict_domains and r.status != VerificationStatus.SUPPORTED and r.status != VerificationStatus.NOT_APPLICABLE)
            ):
                reason = r.contradiction_reason or r.notes
                uncertainties.append(f"[{r.status.value.upper()}] \"{r.claim.text}\" ({reason})")

        return ResponseVerificationReport(
            passed=passed,
            total_claims=total,
            supported_count=supported_cnt,
            partially_supported_count=partially_cnt,
            unverified_count=unverified_cnt,
            contradicted_count=contradicted_cnt,
            stale_count=stale_cnt,
            claim_results=results,
            support_rate=support_rate,
            unsupported_rate=unsupported_rate,
            needs_abstention=needs_abstention,
            uncertainties=uncertainties,
            sanitized_response=candidate_response if passed else None,
        )
