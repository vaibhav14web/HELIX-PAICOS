"""
Reliability Pipeline for HELIX (Tasks 5, 6, 8 / I3, I5, I6, I7).
Enforces:
1. Pydantic schema validation per mode (RATE, QUIZ, GRADE)
2. Planner quarantine (rejects Plan <hex>, Sub Goal, Justification, Step 1:)
3. Praise phrase denylist (rejects Great, Absolutely, Definitely, You're correct, etc.)
4. Finish reason 'length' detection and recovery
5. Multi-attempt retry loop (up to 3 retries) with corrective feedback
6. Deterministic clean fallbacks
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional, Tuple, Type
from pydantic import BaseModel, Field, ValidationError

from core_ai.conversation_engine.mode_state_machine import ConversationMode

logger = logging.getLogger("helix.reliability_pipeline")

# Planner quarantine patterns (I3)
PLANNER_LEAK_PATTERNS = [
    re.compile(r"\bPlan\s+[0-9a-fA-F]+\b", re.IGNORECASE),
    re.compile(r"\bSub\s*Goal\b", re.IGNORECASE),
    re.compile(r"\bJustification\b", re.IGNORECASE),
    re.compile(r"\bStep\s+1:\s*Define\s+the\s+Sub\s*Goal\b", re.IGNORECASE),
    re.compile(r"\bStep\s+1:\b", re.IGNORECASE),
    re.compile(r"\bnumerical\s+ability\b", re.IGNORECASE),
    re.compile(r"\bdoes\s+not\s+require\s+an\s+answer\b", re.IGNORECASE),
]

# Praise phrases denylist (I5 / T6)
PRAISE_PATTERNS = [
    re.compile(r"\bGreat\b", re.IGNORECASE),
    re.compile(r"\bAbsolutely\b", re.IGNORECASE),
    re.compile(r"\bDefinitely\b", re.IGNORECASE),
    re.compile(r"\bYou\'?re\s+correct\b", re.IGNORECASE),
    re.compile(r"\bWell\s+done\b", re.IGNORECASE),
    re.compile(r"\bAwesome\b", re.IGNORECASE),
    re.compile(r"\bGood\s+job\b", re.IGNORECASE),
    re.compile(r"\bExcellent\b", re.IGNORECASE),
]


class RateSchema(BaseModel):
    score: int = Field(ge=0, le=10, description="Score from 0 to 10")
    missing: list[str] = Field(default_factory=list, max_length=3, description="List of at most 3 missing key points")


class QuizSchema(BaseModel):
    question: str = Field(description="Exactly one sentence technical interview question")


class GradeSchema(BaseModel):
    score: int = Field(ge=0, le=10, description="Score from 0 to 10")
    covered: list[str] = Field(default_factory=list, description="Covered reference points")
    missing: list[str] = Field(default_factory=list, max_length=3, description="Missing reference points")
    next_question: str = Field(description="Next technical question")


MODE_SCHEMAS: dict[ConversationMode, Type[BaseModel]] = {
    ConversationMode.RATE: RateSchema,
    ConversationMode.QUIZ: QuizSchema,
    ConversationMode.GRADE: GradeSchema,
}


def check_planner_leakage(text: str) -> Optional[str]:
    """Return matching pattern name if internal planner text leaked into user output."""
    for pattern in PLANNER_LEAK_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def check_praise_words(text: str) -> Optional[str]:
    """Return matching praise word if banned praise word found in text."""
    for pattern in PRAISE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def _apply_anti_sycophancy(model: BaseModel) -> BaseModel:
    if isinstance(model, RateSchema):
        missing_str = " ".join(model.missing).lower()
        if model.missing and (len(model.missing) >= 2 or any(kw in missing_str for kw in ("overload", "overrid", "compile-time", "runtime", "access modifier", "private"))):
            if model.score > 6:
                model.score = 6
    elif isinstance(model, GradeSchema):
        missing_str = " ".join(model.missing).lower()
        if model.missing and (len(model.missing) >= 2 or any(kw in missing_str for kw in ("overload", "overrid", "compile-time", "runtime", "access modifier", "private"))):
            if model.score > 6:
                model.score = 6
    return model


def parse_and_validate_mode_output(
    mode: ConversationMode,
    raw_content: str,
) -> Tuple[bool, Optional[str], Optional[BaseModel]]:
    """
    Validate output against mode rules:
    - Must not contain planner leakage
    - Must not contain praise words
    - Must conform to Pydantic schema (JSON) or standard text format
    Returns (is_valid, error_reason, parsed_model).
    """
    if not raw_content or not raw_content.strip():
        return False, "Empty output", None

    # Check planner leakage
    leaked = check_planner_leakage(raw_content)
    if leaked:
        return False, f"Planner leakage detected: '{leaked}'", None

    # Check praise words
    praise = check_praise_words(raw_content)
    if praise:
        return False, f"Praise phrase detected: '{praise}'", None

    # Normal mode needs no strict schema
    if mode == ConversationMode.NORMAL or mode == ConversationMode.LISTEN:
        return True, None, None

    schema_cls = MODE_SCHEMAS.get(mode)
    if not schema_cls:
        return True, None, None

    # Attempt to parse as JSON first (Ollama structured format)
    try:
        clean_json = raw_content.strip()
        if clean_json.startswith("```"):
            clean_json = re.sub(r"^```(?:json)?\s*", "", clean_json)
            clean_json = re.sub(r"\s*```$", "", clean_json)
        data = json.loads(clean_json)
        model = schema_cls.model_validate(data)
        model = _apply_anti_sycophancy(model)
        return True, None, model
    except (json.JSONDecodeError, ValidationError):
        pass

    # If text format matches expected standard format lines (e.g. Score: N/10\nMissing: ...)
    if mode == ConversationMode.RATE:
        score_m = re.search(r"Score:\s*(\d+)/10", raw_content, re.IGNORECASE)
        missing_m = re.search(r"Missing:\s*(.*)", raw_content, re.IGNORECASE)
        if score_m:
            score = int(score_m.group(1))
            missing_items = [i.strip() for i in missing_m.group(1).split(";") if i.strip()] if missing_m else []
            model = RateSchema(score=score, missing=missing_items[:3])
            model = _apply_anti_sycophancy(model)
            return True, None, model

    elif mode == ConversationMode.QUIZ:
        lines = [line.strip() for line in raw_content.splitlines() if line.strip()]
        if len(lines) == 1 and "?" in lines[0]:
            model = QuizSchema(question=lines[0])
            return True, None, model

    elif mode == ConversationMode.GRADE:
        score_m = re.search(r"Score:\s*(\d+)/10", raw_content, re.IGNORECASE)
        missing_m = re.search(r"Missing:\s*(.*)", raw_content, re.IGNORECASE)
        next_m = re.search(r"Next:\s*(.*)", raw_content, re.IGNORECASE)
        if score_m and next_m:
            score = int(score_m.group(1))
            missing_items = [i.strip() for i in missing_m.group(1).split(";") if i.strip()] if missing_m else []
            model = GradeSchema(score=score, covered=[], missing=missing_items[:3], next_question=next_m.group(1).strip())
            model = _apply_anti_sycophancy(model)
            return True, None, model

    return False, f"Output did not match {mode.value} schema or expected format lines", None


def format_model_to_display(mode: ConversationMode, model: Optional[BaseModel], fallback_text: str) -> str:
    """Format structured model to the exact two-line / three-line contracts required by prompt."""
    if isinstance(model, RateSchema):
        missing_str = "; ".join(model.missing) if model.missing else "none"
        return f"Score: {model.score}/10\nMissing: {missing_str}"
    elif isinstance(model, QuizSchema):
        return model.question.strip()
    elif isinstance(model, GradeSchema):
        missing_str = "; ".join(model.missing) if model.missing else "none"
        return f"Score: {model.score}/10\nMissing: {missing_str}\nNext: {model.next_question.strip()}"
    return fallback_text.strip()


def get_deterministic_fallback(mode: ConversationMode, topic: str = "Java OOP") -> str:
    """Fallback response emitted when all retry attempts fail."""
    if mode == ConversationMode.RATE:
        return "Score: 5/10\nMissing: core definition; essential mechanics; code application"
    elif mode == ConversationMode.QUIZ:
        return f"Explain the core difference between method overloading and method overriding in {topic}."
    elif mode == ConversationMode.GRADE:
        return f"Score: 6/10\nMissing: compile-time vs runtime binding\nNext: What is an abstract class in {topic}?"
    elif mode == ConversationMode.LISTEN:
        return "Ready. Send the first definition."
    return "Understood. Proceed with your practice."
