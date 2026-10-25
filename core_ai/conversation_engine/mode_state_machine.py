"""
Mode State Machine & Automatic Intent Classifier for HELIX (Task 3 / I4, I1).
Maintains active conversation mode in application state (RATE, QUIZ, GRADE, LISTEN, NORMAL).
Deterministic classification rules switch modes without hallucination.
"""
from __future__ import annotations

import enum
import logging
import re
from typing import Any, Optional

logger = logging.getLogger("helix.mode_state_machine")


class ConversationMode(str, enum.Enum):
    RATE = "RATE"
    QUIZ = "QUIZ"
    GRADE = "GRADE"
    LISTEN = "LISTEN"
    NORMAL = "NORMAL"


class ModeStateMachine:
    def __init__(self, initial_mode: ConversationMode = ConversationMode.NORMAL):
        self.active_mode: ConversationMode = initial_mode
        self.current_topic: str = "Java OOP"
        self.last_quiz_question: Optional[str] = None
        self.turn_history: list[dict[str, str]] = []

    def set_mode(self, mode: ConversationMode) -> None:
        logger.info("Mode changed: %s -> %s", self.active_mode, mode)
        self.active_mode = mode

    def classify_and_transition(self, user_text: str) -> ConversationMode:
        """
        Lightweight code router classifying user input into RATE, QUIZ, GRADE, LISTEN, or NORMAL.
        Transitions internal state deterministically.
        """
        lower = user_text.lower().strip()

        # 1. LISTEN mode detection (I4)
        listen_patterns = [
            r"\bi\s+will\s+define\s+(?:the\s+)?topics?[,\s]+you\s+just\s+read",
            r"\bi'll\s+define\s+(?:the\s+)?topics?[,\s]+you\s+just\s+read",
            r"\bi\s+will\s+define\b.*?\byou\s+just\s+read",
            r"\byou\s+just\s+read\b.*?\bi('ll|\s+will)\s+define",
            r"\bjust\s+read\s+(?:the\s+)?topics",
            r"^/listen\b",
        ]
        if any(re.search(p, lower) for p in listen_patterns):
            self.set_mode(ConversationMode.LISTEN)
            return self.active_mode

        # 2. QUIZ mode detection (requests to be quizzed / asked questions)
        quiz_patterns = [
            r"\b(?:quiz\s+me|ask\s+me\s+(?:a\s+)?question|test\s+(?:my\s+knowledge|me))\b",
            r"\b(?:give\s+me\s+a\s+question|practice\s+questions?|ask\s+a\s+question)\b",
            r"^/quiz\b",
        ]
        if any(re.search(p, lower) for p in quiz_patterns):
            self.set_mode(ConversationMode.QUIZ)
            return self.active_mode

        # 3. GRADE mode detection: user answering an outstanding quiz question
        if self.active_mode == ConversationMode.QUIZ and self.last_quiz_question:
            # If the user is responding to a previously asked question, route to GRADE
            self.set_mode(ConversationMode.GRADE)
            return self.active_mode

        # 4. RATE mode detection: user sending definitions or concept explanations
        rate_definition_patterns = [
            r"\b(?:is\s+defined\s+as|is\s+a\s+mechanism|refers\s+to|is\s+the\s+process\s+of|means\s+that)\b",
            r"\b(?:encapsulation|inheritance|polymorphism|abstraction|interface|class|object)\s+(?:is|means|bundles|allows|provides)\b",
            r"^rate\s+this\b",
            r"^/rate\b",
        ]
        if any(re.search(p, lower) for p in rate_definition_patterns):
            self.set_mode(ConversationMode.RATE)
            return self.active_mode

        # If currently in LISTEN mode, the very next definition switches to RATE or stays in guided review
        if self.active_mode == ConversationMode.LISTEN:
            # User sent their first definition
            self.set_mode(ConversationMode.RATE)
            return self.active_mode

        # Default fallback: maintain active mode if already in RATE/QUIZ, else NORMAL
        if self.active_mode in (ConversationMode.RATE, ConversationMode.QUIZ):
            return self.active_mode

        self.active_mode = ConversationMode.NORMAL
        return self.active_mode

    def get_fixed_reply(self) -> Optional[str]:
        """In LISTEN mode, replies come from fixed text in code, never model output (Task 3 / Section 4)."""
        if self.active_mode == ConversationMode.LISTEN:
            return "Ready. Send the first definition."
        return None

    def record_turn(self, role: str, content: str) -> None:
        """Record turn and keep bounded window for recent turns."""
        self.turn_history.append({"role": role, "content": content})
        if len(self.turn_history) > 12:
            self.turn_history = self.turn_history[-12:]

    def get_recent_turns(self, limit: int = 4) -> list[dict[str, str]]:
        """Return the last N turns only (per Task 4 to prevent instruction decay)."""
        return self.turn_history[-limit:]
