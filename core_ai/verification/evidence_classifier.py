"""
Evidence Classifier and Acquisition Router for HELIX Fact-First Verification.

Classifies incoming user requests according to their required evidence types:
- STABLE_KNOWLEDGE: General definitions, conceptual logic, standard libraries
- CURRENT_INFORMATION: Live data, current date/time, recent news or real-time web facts
- REPOSITORY_INSPECTION: Questions regarding project files, configs, codebase structure, symbols
- MEMORY_RETRIEVAL: Personal context, user preferences, past episodes, prior conversation
- COMPUTATION: Math, calculations, conversions, deterministic arithmetic
- ACTION_EXECUTION: System commands, tool invocations, file operations, desktop actions
"""
from __future__ import annotations

import ast
import logging
import operator
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core_ai.verification.models import (
    EvidenceItem,
    EvidenceRequirement,
    EvidenceType,
    ProvenanceSource,
)

logger = logging.getLogger("helix.verification.evidence_classifier")


# Safe Math Evaluator for Deterministic Computation
_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def safe_eval_math(expression: str) -> Optional[float | int]:
    """Safely evaluate simple arithmetic expressions without arbitrary code execution."""
    from foundation.security.ast_verifier import ASTSecurityPolicy, validate_python_ast

    clean_expr = expression.strip().rstrip("=? ")
    clean_expr = clean_expr.replace("^", "**").replace("×", "*").replace("÷", "/")
    # Reject characters that aren't math symbols or digits
    if not re.match(r"^[\d\s\+\-\*\/\(\)\.\%]+$", clean_expr):
        return None

    # Pre-eval AST security validation against AST bombs and unauthorized nodes
    valid, err = validate_python_ast(clean_expr, policy=ASTSecurityPolicy.strict_computation())
    if not valid:
        logger.warning("safe_eval_math rejected expression via AST validation: %s", err)
        return None

    def _eval(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        elif isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type in _SAFE_OPERATORS:
                left = _eval(node.left)
                right = _eval(node.right)
                return _SAFE_OPERATORS[op_type](left, right)
        elif isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type in _SAFE_OPERATORS:
                operand = _eval(node.operand)
                return _SAFE_OPERATORS[op_type](operand)
        raise ValueError("Unsupported operation")

    try:
        tree = ast.parse(clean_expr, mode="eval")
        result = _eval(tree.body)
        return result
    except Exception as e:
        logger.debug("Failed safe math evaluation for '%s': %s", expression, e)
        return None


class EvidenceClassifier:
    """Classifies queries and routes to minimal reliable evidence sources."""

    def __init__(self, workspace_root: Optional[Path | str] = None):
        self.workspace_root = Path(workspace_root or Path.cwd()).resolve()

    def classify(self, text: str, context: Optional[dict[str, Any]] = None) -> EvidenceRequirement:
        """Classify the evidence requirement for a user prompt."""
        lower = text.lower().strip()

        # 1. Computation / Math
        math_match = re.search(r"(\d+[\s\+\-\*\/\^%]+\d+[\s\d\+\-\*\/\^%]*)", text)
        is_calc_query = any(k in lower for k in ("calculate", "compute", "what is", "how much is", "solve", "math")) and math_match
        if is_calc_query or re.match(r"^[\d\s\+\-\*\/\(\)\.\%\^]+=?$", text.strip()):
            return EvidenceRequirement(
                primary_type=EvidenceType.COMPUTATION,
                secondary_types=[],
                requires_freshness=False,
                confidence=0.95,
            )

        # 2. Action Execution
        action_triggers = (
            "open ", "launch ", "start ", "create ", "edit ", "write ",
            "delete ", "remove ", "send ", "compose ", "install ", "execute ",
            "run ", "shutdown ", "restart "
        )
        if any(lower.startswith(act) for act in action_triggers) or any(f" {act}" in lower for act in action_triggers):
            # Check if it's asking how to do it rather than requesting execution
            if not lower.startswith(("how to", "how do i", "can you explain")):
                return EvidenceRequirement(
                    primary_type=EvidenceType.ACTION_EXECUTION,
                    secondary_types=[EvidenceType.REPOSITORY_INSPECTION],
                    requires_freshness=True,
                    recommended_tools=["tool_executor", "action_executor"],
                    confidence=0.9,
                )

        # 3. Repository / Codebase Inspection
        repo_triggers = (
            "in this repo", "in this project", "in this codebase", "repository", "codebase",
            "which file", "what file", "where is", "how is", "check file", "read file",
            "pyproject.toml", "requirements.txt", "package.json", "dockerfile", "readme",
            "function", "class", "method", "module", "imports", "git branch", "git status",
            "tests/", "action/", "core_ai/", "foundation/", "orchestrator/", "api/",
            "version", "helix version", "current version",
            "algorithm", "algorithms", "planner", "planner engine", "permission manager",
            "event bus", "automation engine", "action executor", "verification gate",
            "subsystem", "architecture", "how does it work", "implementation", "engine",
            "procedural memory", "episodic memory", "warm memory", "user model", "world model",
            "self model", "how is it implemented", "what does it use"
        )
        has_component_context = bool(context and (context.get("active_component") or context.get("referenced_component")))
        if any(trig in lower for trig in repo_triggers) or has_component_context or re.search(r"\b(?:[a-zA-Z0-9_\-\.\/]+\.(?:py|json|md|toml|txt|html|css|js|ts))\b", text):
            return EvidenceRequirement(
                primary_type=EvidenceType.REPOSITORY_INSPECTION,
                secondary_types=[EvidenceType.STABLE_KNOWLEDGE],
                requires_freshness=False,
                recommended_tools=["inspect_directory", "read_file", "scan_laptop_files"],
                confidence=0.9,
            )

        # 4. Personal Context / Memory Retrieval
        memory_triggers = (
            "my name", "my preference", "my project", "i like", "i prefer", "what did i say",
            "last time", "remember", "my workflow", "my favorite", "who am i", "my settings"
        )
        if any(trig in lower for trig in memory_triggers):
            return EvidenceRequirement(
                primary_type=EvidenceType.MEMORY_RETRIEVAL,
                secondary_types=[EvidenceType.STABLE_KNOWLEDGE],
                requires_freshness=False,
                recommended_tools=["preference_memory", "episodic_memory"],
                confidence=0.9,
            )

        # 5. Current Information (Date, Time, Live Web Facts)
        current_triggers = (
            "today", "now", "current time", "current date", "what time", "what day",
            "latest news", "weather", "stock price", "who won", "current president"
        )
        if any(trig in lower for trig in current_triggers):
            return EvidenceRequirement(
                primary_type=EvidenceType.CURRENT_INFORMATION,
                secondary_types=[],
                requires_freshness=True,
                max_evidence_age_seconds=3600,
                recommended_tools=["browser_search", "system_clock"],
                confidence=0.85,
            )

        # 6. Default to Stable Knowledge (Standard facts, explanations)
        return EvidenceRequirement(
            primary_type=EvidenceType.STABLE_KNOWLEDGE,
            secondary_types=[],
            requires_freshness=False,
            confidence=0.8,
        )

    async def acquire_evidence(
        self,
        requirement: EvidenceRequirement,
        text: str,
        context: Optional[dict[str, Any]] = None,
    ) -> list[EvidenceItem]:
        """
        Acquire minimal, reliable evidence matching the requirement.
        Avoids unnecessary web searches for every question.
        """
        evidence_items: list[EvidenceItem] = []
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Computation Evidence
        if requirement.primary_type == EvidenceType.COMPUTATION:
            # Extract arithmetic expression
            match = re.search(r"([\d\s\+\-\*\/\(\)\.\%\^]{3,})", text)
            if match:
                expr = match.group(1).strip()
                val = safe_eval_math(expr)
                if val is not None:
                    evidence_items.append(
                        EvidenceItem(
                            source_id="deterministic_math_evaluator",
                            source_type=EvidenceType.COMPUTATION,
                            content=f"Deterministic calculation: {expr} = {val}",
                            timestamp=now_iso,
                            metadata={"expression": expr, "result": val},
                            provenance=ProvenanceSource.OBSERVED_SYSTEM,
                            confidence=1.0,
                        )
                    )

        # 2. Repository Inspection Evidence
        elif requirement.primary_type == EvidenceType.REPOSITORY_INSPECTION:
            # Component-to-file mapping for architectural & algorithmic queries
            component_file_map = {
                "planner": "action/planner_engine/planner_engine.py",
                "planning": "action/planner_engine/planner_engine.py",
                "permission": "foundation/permission_manager/permission_manager.py",
                "automation": "action/automation_engine/automation_engine.py",
                "executor": "action/action_executor/action_executor.py",
                "event_bus": "foundation/event_bus/event_bus.py",
                "event bus": "foundation/event_bus/event_bus.py",
                "verification": "core_ai/verification/verification_gate.py",
                "tool": "core_ai/tool_calling/tool_registry.py",
                "tool_executor": "core_ai/tool_calling/tool_executor.py",
                "conversation": "core_ai/conversation_engine/conversation_engine.py",
                "warm_memory": "memory/warm_memory/warm_memory.py",
                "procedural": "memory/procedural_memory/procedural_memory.py",
                "episodic": "memory/episodic_memory/episodic_memory.py",
                "user_model": "core_ai/user_model/user_model_engine.py",
                "self_model": "core_ai/self_model/self_model_engine.py",
                "world_model": "foundation/world_model/world_model_engine.py",
                "ecs": "orchestrator/ecs/executive_control_system.py",
            }

            target_files = []
            lower_text = text.lower()

            # Check context for active or referenced component
            active_comp = (context or {}).get("active_component") or (context or {}).get("referenced_component")
            if active_comp:
                for k, rel in component_file_map.items():
                    if k in str(active_comp).lower():
                        p = (self.workspace_root / rel).resolve()
                        if p.exists() and p not in target_files:
                            target_files.append(p)

            # Check for specific files mentioned in query
            file_matches = re.findall(r"\b([a-zA-Z0-9_\-\.\/\\]+\.(?:py|json|md|toml|txt|html|css|js|ts))\b", text)
            for fm in file_matches:
                p = (self.workspace_root / fm).resolve()
                if p.exists() and p.is_file() and p not in target_files:
                    target_files.append(p)
                else:
                    candidates = list(self.workspace_root.rglob(Path(fm).name))
                    for c in candidates[:2]:
                        if c not in target_files:
                            target_files.append(c)

            # Match keywords in query to known component files
            for k, rel in component_file_map.items():
                if k in lower_text:
                    p = (self.workspace_root / rel).resolve()
                    if p.exists() and p not in target_files:
                        target_files.append(p)

            if target_files:
                for tf in target_files[:4]:
                    try:
                        content_sample = tf.read_text(encoding="utf-8", errors="ignore")[:10000]
                        rel_id = str(tf.relative_to(self.workspace_root) if tf.is_relative_to(self.workspace_root) else tf).replace("\\", "/")
                        evidence_items.append(
                            EvidenceItem(
                                source_id=rel_id,
                                source_type=EvidenceType.REPOSITORY_INSPECTION,
                                content=f"Source file {tf.name} ({rel_id}):\n{content_sample}",
                                timestamp=now_iso,
                                metadata={"path": str(tf), "size": tf.stat().st_size},
                                provenance=ProvenanceSource.OBSERVED_SYSTEM,
                                confidence=1.0,
                            )
                        )
                    except Exception as e:
                        logger.debug("Failed reading file %s for evidence: %s", tf, e)

            # If no specific files or components identified, inspect pyproject.toml as fallback
            if not evidence_items:
                pyproj = self.workspace_root / "pyproject.toml"
                if pyproj.exists():
                    try:
                        snippet = pyproj.read_text(encoding="utf-8", errors="ignore")[:1000]
                        evidence_items.append(
                            EvidenceItem(
                                source_id="pyproject.toml",
                                source_type=EvidenceType.REPOSITORY_INSPECTION,
                                content=f"Project manifest (pyproject.toml):\n{snippet}",
                                timestamp=now_iso,
                                metadata={"path": str(pyproj)},
                                provenance=ProvenanceSource.OBSERVED_SYSTEM,
                                confidence=1.0,
                            )
                        )
                    except Exception as e:
                        logger.debug("Failed reading pyproject.toml: %s", e)

        # 3. Current Information (Date / Time / Real-time)
        elif requirement.primary_type == EvidenceType.CURRENT_INFORMATION:
            evidence_items.append(
                EvidenceItem(
                    source_id="system_clock",
                    source_type=EvidenceType.CURRENT_INFORMATION,
                    content=f"Current ISO Timestamp: {now_iso}. Local Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                    timestamp=now_iso,
                    metadata={"system_time": now_iso},
                    provenance=ProvenanceSource.OBSERVED_SYSTEM,
                    confidence=1.0,
                )
            )

        # 4. Personal Context / Memory Retrieval
        elif requirement.primary_type == EvidenceType.MEMORY_RETRIEVAL and context:
            user_pref = context.get("preferences", {})
            user_traits = context.get("traits", {})
            if user_pref or user_traits:
                mem_content = f"User Stored Context: Preferences={user_pref}, Traits={user_traits}"
                evidence_items.append(
                    EvidenceItem(
                        source_id="memory_subsystem",
                        source_type=EvidenceType.MEMORY_RETRIEVAL,
                        content=mem_content,
                        timestamp=now_iso,
                        metadata={"preferences": user_pref, "traits": user_traits},
                        provenance=ProvenanceSource.USER_STATED,
                        confidence=0.95,
                    )
                )

        # Ingest verified RAG memories from context if provided
        if context and "rag_memories" in context and isinstance(context["rag_memories"], list):
            for rm in context["rag_memories"]:
                rm_text = rm.get("text", "")
                rm_meta = rm.get("metadata", {})
                rm_src = rm_meta.get("file") or rm_meta.get("source_id") or rm.get("category", "rag_memory")
                if rm_text:
                    evidence_items.append(
                        EvidenceItem(
                            source_id=str(rm_src),
                            source_type=EvidenceType.MEMORY_RETRIEVAL,
                            content=rm_text,
                            timestamp=rm.get("timestamp", now_iso),
                            metadata=rm_meta,
                            provenance=ProvenanceSource.OBSERVED_SYSTEM,
                            confidence=float(rm.get("score") or 0.85),
                        )
                    )

        return evidence_items
