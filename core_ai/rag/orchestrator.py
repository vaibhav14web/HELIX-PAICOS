"""
HELIX — Context-Aware Production RAG Retrieval Orchestrator for Personal AI Agent Platform.

Coordinates evidence retrieval across:
- Authoritative workspace repository code & tests
- Semantic Warm Memory (Qdrant & FastEmbed ONNX)
- Episodic Behavioral Memory
- Procedural Workflow Memory
- Working Conversation Context
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from core_ai.rag.models import (
    EvidenceCandidate,
    EvidencePackage,
    MemoryTier,
    RetrievalDiagnostics,
    RetrievalIntent,
    RetrievalPlan,
    RetrievalScope,
    SourceAuthority,
)
from core_ai.rag.policy import (
    apply_hard_filters,
    classify_retrieval_intent,
    detect_conflicts_and_superseded,
    evaluate_evidence_sufficiency,
    get_intent_retrieval_policy,
    sanitize_untrusted_evidence,
)
from core_ai.verification.evidence_classifier import EvidenceClassifier
from core_ai.verification.models import EvidenceRequirement, EvidenceType
from foundation.event_bus.event_bus import EventBus

logger = logging.getLogger("helix.rag.orchestrator")


class RAGRetrievalOrchestrator:
    """
    Dedicated Retrieval Orchestrator for HELIX PCOS.
    Coordinates deterministic context resolution, intent routing, multi-tier evidence
    retrieval, hard scope isolation, source-authority ranking, and evidence sufficiency validation.
    """

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        warm_memory: Any | None = None,
        episodic_memory: Any | None = None,
        procedural_memory: Any | None = None,
        work_memory: Any | None = None,
        goal_manager: Any | None = None,
        productivity_engine: Any | None = None,
        workspace_root: Optional[Path | str] = None,
    ):
        self._event_bus = event_bus
        self.warm_memory = warm_memory
        self.episodic_memory = episodic_memory
        self.procedural_memory = procedural_memory
        self.work_memory = work_memory
        self.goal_manager = goal_manager
        self.productivity_engine = productivity_engine
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()
        self.classifier = EvidenceClassifier(workspace_root=self.workspace_root)

    async def orchestrate_retrieval(
        self,
        query: str,
        session_id: str = "default",
        conversation_history: Optional[list[dict[str, str]]] = None,
        context: Optional[dict[str, Any]] = None,
        explicit_project: Optional[str] = None,
        **kwargs: Any,
    ) -> EvidencePackage:
        """
        Executes the 6-stage RAG Retrieval Pipeline:
        Stage A: Query preparation & contextual reference resolution
        Stage B: Multi-tier candidate generation
        Stage C: Hard filtering (strict project and component isolation)
        Stage D: Relevance & source authority ranking
        Stage E: Deduplication, contradiction and superseded resolution
        Stage F: Evidence sufficiency determination & package assembly
        """
        req_id = str(uuid.uuid4())[:8]
        start_time = time.perf_counter()
        latencies: dict[str, float] = {}
        history = conversation_history or []
        ctx = dict(context or {})
        if explicit_project:
            ctx["project"] = explicit_project

        # ── Stage A: Query Preparation & Context Resolution ───────────────────
        t0 = time.perf_counter()
        intent, scope, subject, is_ambiguous = classify_retrieval_intent(query, context=ctx, history=history)
        latencies["stage_a_context_resolution_ms"] = round((time.perf_counter() - t0) * 1000, 2)

        # Handle Ambiguous Queries: Immediately ask for clarification
        if is_ambiguous:
            return EvidencePackage(
                resolved_question=query,
                resolved_subject=None,
                scope=scope,
                intent=intent,
                conversation_context=history[-4:],
                retrieved_evidence=[],
                conflicting_evidence=[],
                unresolved_questions=[query],
                is_sufficient=False,
                missing_evidence_notes="Ambiguous reference with no clear conversational antecedent.",
                authorization_passed=True,
                abstention_required=True,
                clarification_prompt=(
                    "Could you please clarify which component or topic you are asking about? "
                    "For example, are you referring to HELIX's Planner Engine, Permission Manager, or Event Bus?"
                ),
                diagnostics={"request_id": req_id, "is_ambiguous": True},
            )

        plan = get_intent_retrieval_policy(intent, scope, query)

        # ── Stage B: Multi-Tier Candidate Generation ──────────────────────────
        t1 = time.perf_counter()
        raw_candidates: list[EvidenceCandidate] = []
        fallback_reason: Optional[str] = None

        candidate_tasks = []

        # 1. Authoritative Workspace / Codebase Inspection
        if MemoryTier.AUTHORITATIVE_CODE in plan.permitted_tiers:
            candidate_tasks.append(self._gather_code_evidence(plan.resolved_query, subject, scope.project))

        # 2. Semantic Warm Memory (Qdrant / FastEmbed)
        if MemoryTier.WARM_SEMANTIC in plan.permitted_tiers and (self.warm_memory or self._event_bus):
            candidate_tasks.append(self._gather_warm_memory_evidence(plan.resolved_query, scope.project, plan.min_similarity))

        # 3. Episodic Memory
        if MemoryTier.EPISODIC in plan.permitted_tiers and self.episodic_memory:
            candidate_tasks.append(self._gather_episodic_evidence(plan.resolved_query))

        # 4. Procedural Memory
        if MemoryTier.PROCEDURAL in plan.permitted_tiers and self.procedural_memory:
            candidate_tasks.append(self._gather_procedural_evidence(plan.resolved_query, scope.project))

        # 5. Working Memory / Work Sessions
        if MemoryTier.WORKING in plan.permitted_tiers and self.work_memory:
            candidate_tasks.append(self._gather_work_memory_evidence(plan.resolved_query, scope.project, session_id))

        # 6. Long-Running Goals (System State / Working)
        if (MemoryTier.WORKING in plan.permitted_tiers or MemoryTier.SYSTEM_STATE in plan.permitted_tiers) and self.goal_manager:
            candidate_tasks.append(self._gather_goal_evidence(plan.resolved_query, scope.project))

        # 7. Productivity Engine / Repetitive Patterns
        if (MemoryTier.PROCEDURAL in plan.permitted_tiers or MemoryTier.WORKING in plan.permitted_tiers) and self.productivity_engine:
            candidate_tasks.append(self._gather_productivity_evidence(plan.resolved_query))

        try:
            tier_results = await asyncio.gather(*candidate_tasks, return_exceptions=True)
            for res in tier_results:
                if isinstance(res, list):
                    raw_candidates.extend(res)
                elif isinstance(res, Exception):
                    logger.warning("Tier retrieval exception: %s", res)
                    fallback_reason = str(res)
        except Exception as e:
            logger.warning("Gathering candidates failed: %s", e)
            fallback_reason = str(e)

        # Validate candidate provenance and sanitize content (fail safe)
        safe_candidates: list[EvidenceCandidate] = []
        for cand in raw_candidates:
            if not isinstance(cand, EvidenceCandidate):
                continue
            if not isinstance(cand.authority, SourceAuthority):
                try:
                    if isinstance(cand.authority, int):
                        cand.authority = SourceAuthority(cand.authority)
                    else:
                        cand.authority = SourceAuthority.UNVERIFIED
                except Exception:
                    cand.authority = SourceAuthority.UNVERIFIED
            if not isinstance(cand.tier, MemoryTier):
                try:
                    cand.tier = MemoryTier(cand.tier)
                except Exception:
                    cand.tier = MemoryTier.WARM_SEMANTIC
            cand.content = sanitize_untrusted_evidence(str(cand.content))
            safe_candidates.append(cand)
        raw_candidates = safe_candidates

        latencies["stage_b_candidate_generation_ms"] = round((time.perf_counter() - t1) * 1000, 2)
        total_retrieved = len(raw_candidates)

        # ── Stage C: Hard Filtering ───────────────────────────────────────────
        t2 = time.perf_counter()
        admitted, filtered_count = apply_hard_filters(raw_candidates, scope)
        latencies["stage_c_hard_filtering_ms"] = round((time.perf_counter() - t2) * 1000, 2)

        # ── Stage D: Relevance & Source Authority Ranking ─────────────────────
        t3 = time.perf_counter()
        # Compute composite ranking score:
        # Final Score = (0.5 * Semantic Score) + (0.4 * Authority Score) + (0.1 * Lexical Match)
        query_terms = set(re.findall(r"\b[a-zA-Z0-9_]+\b", plan.resolved_query.lower()))
        for cand in admitted:
            cand_text_terms = set(re.findall(r"\b[a-zA-Z0-9_]+\b", cand.content.lower()))
            lexical_overlap = len(query_terms.intersection(cand_text_terms)) / max(len(query_terms), 1)
            authority_normalized = cand.authority.value / 100.0
            semantic_normalized = min(max(cand.score, 0.0), 1.0)
            composite_score = (0.45 * semantic_normalized) + (0.40 * authority_normalized) + (0.15 * lexical_overlap)
            cand.score = round(composite_score, 4)

        admitted.sort(key=lambda c: c.score, reverse=True)
        admitted = admitted[: plan.max_candidates]
        latencies["stage_d_relevance_ranking_ms"] = round((time.perf_counter() - t3) * 1000, 2)

        # ── Stage E: Deduplication, Contradiction & Superseded Resolution ─────
        t4 = time.perf_counter()
        deduped, conflicts, dedup_count = detect_conflicts_and_superseded(admitted)
        latencies["stage_e_dedup_and_conflicts_ms"] = round((time.perf_counter() - t4) * 1000, 2)

        # ── Stage F: Evidence Sufficiency Evaluation & Assembly ───────────────
        t5 = time.perf_counter()
        is_sufficient, missing_notes = evaluate_evidence_sufficiency(intent, query, deduped, subject=subject)
        latencies["stage_f_sufficiency_check_ms"] = round((time.perf_counter() - t5) * 1000, 2)

        abstention_required = not is_sufficient
        total_pipeline_ms = round((time.perf_counter() - start_time) * 1000, 2)

        # Build diagnostics
        diagnostics = RetrievalDiagnostics(
            request_id=req_id,
            raw_query=query,
            resolved_query=plan.resolved_query,
            intent=intent.value,
            project=scope.project,
            component=scope.component,
            stage_latencies_ms={**latencies, "total_pipeline_ms": total_pipeline_ms},
            candidates_retrieved=total_retrieved,
            candidates_admitted=len(deduped),
            hard_filtered_count=filtered_count,
            deduplicated_count=dedup_count,
            conflicts_detected=len(conflicts),
            is_sufficient=is_sufficient,
            fallback_reason=fallback_reason,
        )

        return EvidencePackage(
            resolved_question=plan.resolved_query,
            resolved_subject=subject,
            scope=scope,
            intent=intent,
            conversation_context=history[-4:],
            retrieved_evidence=deduped,
            conflicting_evidence=conflicts,
            unresolved_questions=[missing_notes] if missing_notes else [],
            is_sufficient=is_sufficient,
            missing_evidence_notes=missing_notes,
            authorization_passed=True,
            abstention_required=abstention_required,
            diagnostics=diagnostics.to_dict(),
        )

    # ── Internal Tier Evidence Collectors ─────────────────────────────────────

    async def _gather_code_evidence(self, query: str, subject: Optional[str], project: str) -> list[EvidenceCandidate]:
        candidates: list[EvidenceCandidate] = []
        now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        # If project is AI-HMS and workspace is HELIX, do not read local HELIX files as AI-HMS files
        if project.lower() == "ai-hms":
            # Search if indexed AI-HMS references exist in scripts or external directories
            return []

        # Run EvidenceClassifier repository inspection
        req = EvidenceRequirement(
            primary_type=EvidenceType.REPOSITORY_INSPECTION,
            secondary_types=[],
            target_entities=[subject] if subject else [],
        )
        ctx = {"active_component": subject, "project": project}
        items = await self.classifier.acquire_evidence(req, query, context=ctx)
        for item in items:
            sanitized = sanitize_untrusted_evidence(item.content)
            candidates.append(
                EvidenceCandidate(
                    candidate_id=f"code_{uuid.uuid4().hex[:6]}",
                    content=sanitized,
                    source_id=item.source_id,
                    tier=MemoryTier.AUTHORITATIVE_CODE,
                    authority=SourceAuthority.AUTHORITATIVE_CODE,
                    project=project,
                    component=subject,
                    timestamp=item.timestamp or now_iso,
                    score=0.95,
                    metadata=item.metadata,
                )
            )
        return candidates

    async def _gather_warm_memory_evidence(self, query: str, project: str, min_score: float) -> list[EvidenceCandidate]:
        candidates: list[EvidenceCandidate] = []
        hits = []
        if self.warm_memory and hasattr(self.warm_memory, "search"):
            try:
                hits = await self.warm_memory.search(
                    query=query,
                    limit=6,
                    min_score=min_score,
                    project=project,
                )
            except Exception as e:
                logger.warning("Direct warm memory search failed: %s", e)
        elif self._event_bus:
            # Fallback to EventBus search
            corr_id = str(uuid.uuid4())
            loop = asyncio.get_running_loop()
            future: asyncio.Future[list[dict]] = loop.create_future()

            async def _on_searched(event):
                if event.correlation_id == corr_id and not future.done():
                    res = event.payload.get("results", [])
                    future.set_result(res)

            self._event_bus.subscribe("memory.warm.searched", _on_searched)
            await self._event_bus.publish_event(
                source="rag_orchestrator",
                event_type="memory.warm.search",
                payload={"query": query, "limit": 6, "min_score": min_score, "project": project},
                correlation_id=corr_id,
            )
            try:
                raw_hits = await asyncio.wait_for(future, timeout=2.0)
                from memory.warm_memory.models import WarmMemoryEntry
                for r in raw_hits:
                    if isinstance(r, dict):
                        hits.append(WarmMemoryEntry(**r))
                    elif isinstance(r, WarmMemoryEntry):
                        hits.append(r)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            except Exception as e:
                logger.warning("EventBus warm memory search error: %s", e)
            finally:
                self._event_bus.unsubscribe("memory.warm.searched", _on_searched)

        for hit in hits:
            sanitized = sanitize_untrusted_evidence(hit.text)
            hit_meta = hit.metadata or {}
            hit_proj = hit_meta.get("project", project)
            candidates.append(
                EvidenceCandidate(
                    candidate_id=f"warm_{hit.id}",
                    content=sanitized,
                    source_id=str(hit_meta.get("source_id") or hit_meta.get("file") or hit.id),
                    tier=MemoryTier.WARM_SEMANTIC,
                    authority=SourceAuthority.STORED_MEMORY,
                    project=hit_proj,
                    component=hit_meta.get("component"),
                    timestamp=hit.timestamp,
                    score=float(hit.score or 0.8),
                    metadata=hit_meta,
                    is_stale=bool(hit_meta.get("is_stale", False)),
                    superseded_by=hit_meta.get("supersedes"),
                )
            )
        return candidates

    async def _gather_episodic_evidence(self, query: str) -> list[EvidenceCandidate]:
        candidates: list[EvidenceCandidate] = []
        try:
            episodes = self.episodic_memory.search_episodes(query, limit=3)
            for ep in episodes:
                content = f"Past Interaction Episode ({ep.intent}): Outcome={ep.outcome}. Lessons: {ep.reflection_notes}"
                candidates.append(
                    EvidenceCandidate(
                        candidate_id=f"ep_{ep.episode_id}",
                        content=sanitize_untrusted_evidence(content),
                        source_id=f"episode:{ep.episode_id}",
                        tier=MemoryTier.EPISODIC,
                        authority=SourceAuthority.STORED_MEMORY,
                        project=ep.context.get("project", "helix"),
                        timestamp=ep.timestamp,
                        score=0.75,
                        metadata={"intent": ep.intent, "outcome": ep.outcome},
                    )
                )
        except Exception as e:
            logger.debug("Episodic retrieval failed: %s", e)
        return candidates

    async def _gather_procedural_evidence(self, query: str, project: str) -> list[EvidenceCandidate]:
        candidates: list[EvidenceCandidate] = []
        try:
            workflow = self.procedural_memory.find_matching_workflow(query, project=project)
            if workflow:
                step_descriptions = [f"{s.step_index+1}. {s.action}({s.target or ''})" for s in workflow.steps]
                content = f"Established Procedure for '{workflow.name}': " + " -> ".join(step_descriptions)
                candidates.append(
                    EvidenceCandidate(
                        candidate_id=f"proc_{workflow.workflow_id}",
                        content=content,
                        source_id=f"workflow:{workflow.name}",
                        tier=MemoryTier.PROCEDURAL,
                        authority=SourceAuthority.VERIFIED_ACTION if workflow.user_verified else SourceAuthority.STORED_MEMORY,
                        project=workflow.project_scope or project,
                        timestamp=workflow.created_at,
                        score=float(workflow.confidence),
                        metadata={"verified": workflow.user_verified, "success_count": workflow.success_count},
                    )
                )
        except Exception as e:
            logger.debug("Procedural retrieval failed: %s", e)
        return candidates

    async def _gather_work_memory_evidence(self, query: str, project: str, session_id: str) -> list[EvidenceCandidate]:
        candidates: list[EvidenceCandidate] = []
        try:
            sessions = []
            if hasattr(self.work_memory, "get_all_sessions"):
                sessions = self.work_memory.get_all_sessions()
            elif hasattr(self.work_memory, "get_active_session"):
                active = self.work_memory.get_active_session()
                if active:
                    sessions = [active]

            for s in sessions:
                proj_desc = f"Project: {s.active_project}" if s.active_project else "No active project"
                tasks_desc = f"Tasks: {', '.join(s.open_tasks)}" if s.open_tasks else "No open tasks"
                content = f"Work Session ({s.session_id}) - Started: {s.started_at}, Last updated: {s.last_updated}. {proj_desc}. {tasks_desc}."
                candidates.append(
                    EvidenceCandidate(
                        candidate_id=f"work_{s.session_id}",
                        content=sanitize_untrusted_evidence(content),
                        source_id=f"work_session:{s.session_id}",
                        tier=MemoryTier.WORKING,
                        authority=SourceAuthority.OBSERVED_STATE,
                        project=s.active_project or project,
                        timestamp=s.last_updated or s.started_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        score=0.88,
                        metadata={"session_id": s.session_id, "tasks": s.open_tasks, "active_project": s.active_project},
                    )
                )
        except Exception as e:
            logger.debug("Work memory retrieval error: %s", e)
        return candidates

    async def _gather_goal_evidence(self, query: str, project: str) -> list[EvidenceCandidate]:
        candidates: list[EvidenceCandidate] = []
        try:
            goals = []
            if hasattr(self.goal_manager, "find_matching_goals"):
                goals = self.goal_manager.find_matching_goals(query)
            if not goals and hasattr(self.goal_manager, "list_goals"):
                goals = self.goal_manager.list_goals()

            for g in goals:
                tasks_summary = ", ".join(f"{t.title} ({t.status})" for t in g.tasks) if g.tasks else "No subtasks"
                content = (
                    f"Goal '{g.title}' [Status: {g.status}, Priority: {g.priority}, Progress: {g.progress_percent:.0f}%]: "
                    f"{g.description}. Tasks: {tasks_summary}."
                )
                candidates.append(
                    EvidenceCandidate(
                        candidate_id=f"goal_{g.goal_id}",
                        content=sanitize_untrusted_evidence(content),
                        source_id=f"goal:{g.goal_id}",
                        tier=MemoryTier.WORKING,
                        authority=SourceAuthority.USER_STATED,
                        project=project,
                        timestamp=g.updated_at or g.created_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        score=0.90,
                        metadata={"goal_id": g.goal_id, "status": g.status, "progress": g.progress_percent},
                    )
                )
        except Exception as e:
            logger.debug("Goal evidence retrieval error: %s", e)
        return candidates

    async def _gather_productivity_evidence(self, query: str) -> list[EvidenceCandidate]:
        candidates: list[EvidenceCandidate] = []
        try:
            patterns = []
            if hasattr(self.productivity_engine, "get_patterns"):
                patterns = self.productivity_engine.get_patterns()

            for p in patterns:
                content = (
                    f"Productivity Pattern: action='{p.action}', category='{p.category}', "
                    f"frequency={p.frequency}, automated={p.automated}, suggested={p.suggested}. "
                    f"Description: {p.description or 'Observed user routine'}."
                )
                candidates.append(
                    EvidenceCandidate(
                        candidate_id=f"prod_{uuid.uuid4().hex[:6]}",
                        content=sanitize_untrusted_evidence(content),
                        source_id=f"productivity_pattern:{p.action}",
                        tier=MemoryTier.PROCEDURAL,
                        authority=SourceAuthority.VERIFIED_ACTION,
                        project="helix",
                        timestamp=p.last_observed or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        score=0.85,
                        metadata={"action": p.action, "frequency": p.frequency, "category": p.category},
                    )
                )
        except Exception as e:
            logger.debug("Productivity evidence retrieval error: %s", e)
        return candidates

