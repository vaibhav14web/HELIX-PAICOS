"""
Verification Gate Engine for HELIX Fact-First Architecture.

Enforces application-level gatekeeper control over all final responses:
Request -> Classify Evidence -> Acquire Evidence -> Generate Candidate Answer ->
Verify Claims -> Resolve / Refine -> Re-Validate Final Response -> Return.

Ensures that unsupported claims cannot be silently presented as established facts,
enforces bounded retries, and formats calibrated uncertainty abstentions.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Coroutine, Optional

from core_ai.verification.claim_verifier import ClaimVerifier
from core_ai.verification.evidence_classifier import EvidenceClassifier
from core_ai.verification.models import (
    ClaimType,
    EvidenceItem,
    EvidenceRequirement,
    EvidenceType,
    ResponseVerificationReport,
    VerificationPolicy,
    VerificationStatus,
)
from foundation.event_bus.event_bus import EventBus

logger = logging.getLogger("helix.verification.verification_gate")


class VerificationGate:
    """Application-logic enforcement gate for verifiable responses."""

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        policy: Optional[VerificationPolicy] = None,
        classifier: Optional[EvidenceClassifier] = None,
        verifier: Optional[ClaimVerifier] = None,
        enabled: Optional[bool] = None,
    ):
        self._event_bus = event_bus
        self.policy = policy or VerificationPolicy()
        self.classifier = classifier or EvidenceClassifier()
        self.verifier = verifier or ClaimVerifier(self.policy)

        if enabled is not None:
            self.enabled = enabled
        elif policy is not None and policy.strict_mode:
            self.enabled = True
        else:
            env_val = os.getenv("HELIX_FACT_VERIFICATION_ENABLED", "").lower()
            if env_val:
                self.enabled = env_val in ("true", "1", "yes")
            else:
                self.enabled = False

    async def run_gated_workflow(
        self,
        user_message: str,
        generator_fn: Callable[[str, list[EvidenceItem], Optional[str]], Coroutine[Any, Any, str]],
        context: Optional[dict[str, Any]] = None,
    ) -> tuple[str, ResponseVerificationReport]:
        """
        Execute the full fact-first verification lifecycle:
        1. Classify evidence requirements
        2. Acquire evidence
        3. Generate candidate answer
        4. Verify important claims
        5. Refine if needed (bounded)
        6. Re-validate final response before returning
        """
        is_enabled = self.enabled or bool(context and (context.get("force_verification") or context.get("verify")))
        if not is_enabled:
            logger.info("Fact verification gate is disabled. Bypassing verification workflow.")
            candidate = await generator_fn(user_message, [], None)
            dummy_report = ResponseVerificationReport(
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
                uncertainties=[],
                sanitized_response=candidate,
            )
            return candidate, dummy_report
        # Step 1 & 2: Classify and acquire evidence
        requirement = self.classifier.classify(user_message, context)
        evidence = await self.classifier.acquire_evidence(requirement, user_message, context)
        if context and "evidence" in context and isinstance(context["evidence"], list):
            evidence = list(evidence) + list(context["evidence"])

        logger.info(
            "Evidence classified for prompt: type=%s, acquired=%d items",
            requirement.primary_type.value,
            len(evidence),
        )

        feedback: Optional[str] = None
        attempt = 0
        final_response = ""
        final_report: Optional[ResponseVerificationReport] = None

        while attempt <= self.policy.max_refinement_attempts:
            attempt += 1

            # Step 3: Generate candidate answer (with feedback if refining)
            candidate = await generator_fn(user_message, evidence, feedback)

            # Step 4: Verify claims
            report = self.verifier.verify_response(candidate, evidence)

            if self._event_bus:
                eval_payload = {
                    "attempt": attempt,
                    "passed": report.passed,
                    "total_claims": report.total_claims,
                    "support_rate": report.support_rate,
                    "unsupported_rate": report.unsupported_rate,
                }
                if context:
                    if "request_id" in context:
                        eval_payload["request_id"] = context["request_id"]
                    if "correlation_id" in context:
                        eval_payload["correlation_id"] = context["correlation_id"]
                await self._event_bus.publish_event(
                    source="verification_gate",
                    event_type="verification.evaluated",
                    payload=eval_payload,
                )

            # Check if candidate passed verification gate
            is_acceptable_stable = (
                not self.policy.strict_mode
                and requirement.primary_type == EvidenceType.STABLE_KNOWLEDGE
                and report.contradicted_count == 0
                and not any(cr.status == VerificationStatus.CONTRADICTED for cr in report.claim_results)
            )

            if is_acceptable_stable:
                report.passed = True
                report.needs_abstention = False

            if report.passed and not report.needs_abstention:
                # If there are unverified material claims, qualify them transparently
                unverified_material = [
                    cr.claim.text for cr in report.claim_results
                    if cr.status in (VerificationStatus.UNVERIFIED, VerificationStatus.STALE) and cr.is_material
                ]
                if unverified_material and self.policy.preserve_uncontradicted_explanations:
                    qual_note = (
                        "\n\n> [!NOTE]\n> **Factual Verification Notice:** The following claim(s) could not be verified against current repository or system evidence: "
                        + "; ".join(f"\"{u}\"" for u in unverified_material[:3])
                        + "."
                    )
                    final_response = candidate + qual_note
                else:
                    final_response = candidate
                final_report = report
                final_report.sanitized_response = final_response
                logger.info("Candidate passed verification gate on attempt %d (stable_pass=%s)", attempt, is_acceptable_stable)
                break

            # If failed and attempts remain, formulate bounded refinement feedback
            if attempt <= self.policy.max_refinement_attempts:
                unverified_list = [
                    f"'{cr.claim.text}' ({cr.contradiction_reason or 'unverified fact'})"
                    for cr in report.claim_results
                    if cr.status in (VerificationStatus.CONTRADICTED, VerificationStatus.UNVERIFIED, VerificationStatus.STALE)
                ]
                feedback = (
                    "CRITICAL VERIFICATION FEEDBACK: The following claims are unsupported or contradicted by verified evidence:\n"
                    + "\n".join(f"- {u}" for u in unverified_list[:4])
                    + "\nPlease revise your response. Remove or qualify unverified assertions, correct contradicted statements, and do not make claims not grounded in evidence."
                )
                logger.warning("Attempt %d failed verification gate. Requesting refinement.", attempt)
            else:
                final_report = report

        # Step 5: If still unverified or contradicted after bounded attempts, acknowledge uncertainty or qualify
        if final_report is None or not final_report.passed or final_report.needs_abstention:
            has_contradiction = bool(final_report and final_report.contradicted_count > 0)
            has_strict_violation = bool(final_report and any(
                r.claim.domain in self.policy.strict_domains and r.status in (VerificationStatus.UNVERIFIED, VerificationStatus.CONTRADICTED)
                for r in final_report.claim_results
            ))

            # Preserve useful explanations if uncontradicted and non-strict
            if (
                candidate
                and not has_contradiction
                and not has_strict_violation
                and self.policy.preserve_uncontradicted_explanations
                and final_report
                and any(
                    r.claim.claim_type in (ClaimType.CONCEPTUAL_EXPLANATION, ClaimType.RECOMMENDATION, ClaimType.CONVERSATIONAL, ClaimType.CREATIVE)
                    or r.status == VerificationStatus.SUPPORTED
                    for r in final_report.claim_results
                )
            ):
                unverified_claims = [
                    r.claim.text for r in final_report.claim_results
                    if r.status in (VerificationStatus.UNVERIFIED, VerificationStatus.STALE) and r.is_material
                ]
                if unverified_claims:
                    qual_note = (
                        "\n\n> [!NOTE]\n> **Factual Verification Notice:** The following claim(s) could not be verified against current repository or system evidence: "
                        + "; ".join(f"\"{u}\"" for u in unverified_claims[:3])
                        + "."
                    )
                    final_response = candidate + qual_note
                else:
                    final_response = candidate
                final_report.passed = True
                final_report.needs_abstention = False
                final_report.sanitized_response = final_response
            else:
                final_response = self._synthesize_calibrated_abstention(user_message, evidence, final_report)
                # Step 6: RE-VALIDATE the final abstention response so unsupported claims cannot silently reappear!
                final_report = self.verifier.verify_response(final_response, evidence)
                # Abstention messages by design contain transparent uncertainty and verified facts only
                final_report.passed = True
                final_report.needs_abstention = True

                if self._event_bus:
                    abstained_payload = {
                        "user_message": user_message[:80],
                        "uncertainties": final_report.uncertainties,
                        "reason": "CONTRADICTORY_EVIDENCE" if has_contradiction else "INSUFFICIENT_EVIDENCE",
                    }
                    if context:
                        if "request_id" in context:
                            abstained_payload["request_id"] = context["request_id"]
                        if "correlation_id" in context:
                            abstained_payload["correlation_id"] = context["correlation_id"]
                    await self._event_bus.publish_event(
                        source="verification_gate",
                        event_type="verification.abstained",
                        payload=abstained_payload,
                    )

        return final_response, final_report

    def _synthesize_calibrated_abstention(
        self,
        user_message: str,
        evidence: list[EvidenceItem],
        report: Optional[ResponseVerificationReport],
    ) -> str:
        """
        Formulate an honest, calibrated response acknowledging uncertainty
        when evidence is insufficient or contradicted.
        """
        lines = []

        # Mention verified facts if any exist
        verified_claims = [
            cr.claim.text
            for cr in (report.claim_results if report else [])
            if cr.status == VerificationStatus.SUPPORTED
        ]
        if verified_claims:
            lines.append("Based on verified system evidence:")
            for vc in verified_claims[:3]:
                lines.append(f"- {vc}")
            lines.append("")

        # Check for contradicted claims to deliver an evidence-backed correction
        contradicted_claims = [
            cr for cr in (report.claim_results if report else [])
            if cr.status == VerificationStatus.CONTRADICTED and cr.contradiction_reason
        ]
        if contradicted_claims:
            lines.append("Based on verified evidence, the following statement(s) are contradicted:")
            for cc in contradicted_claims[:3]:
                lines.append(f"- {cc.contradiction_reason}")
            lines.append("")
        else:
            lines.append("However, I cannot fully confirm all details regarding your request with certainty.")

        if report and report.uncertainties:
            lines.append("\nThe following aspects remain unverified or have conflicting evidence:")
            for unc in report.uncertainties[:4]:
                lines.append(f"- {unc}")

        if evidence:
            ev_sources = ", ".join(set(e.source_id for e in evidence))
            lines.append(f"\nAvailable verified sources inspected: {ev_sources}.")
        else:
            lines.append("\nNo relevant local repository files, system observations, or stored memory records were found to verify this claim.")

        lines.append("\nCould you please clarify or provide additional details or context?")
        return "\n".join(lines)
