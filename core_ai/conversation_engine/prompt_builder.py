"""
Per-Turn Dynamic Prompt Builder for HELIX (Task 4 / I1).
Reconstructs the prompt on every turn to prevent instruction decay.
Injects mode rules, reference points, last 4 turns, and immediate pre-user reminder.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from core_ai.conversation_engine.mode_state_machine import ConversationMode

logger = logging.getLogger("helix.prompt_builder")


class PromptBuilder:
    def __init__(self, reference_path: Optional[str | Path] = None):
        self._reference_path = Path(reference_path) if reference_path else (
            Path(__file__).resolve().parent.parent.parent / "data" / "knowledge" / "java_oop_reference.json"
        )
        self._reference_data: dict[str, Any] = {}
        self._load_references()

    def _load_references(self) -> None:
        if self._reference_path.exists():
            try:
                with open(self._reference_path, "r", encoding="utf-8") as f:
                    self._reference_data = json.load(f)
            except Exception as e:
                logger.error("Failed to load reference points from %s: %s", self._reference_path, e)

    def get_reference_points_for_concept(self, text: str) -> str:
        """Find the relevant concept in user text and extract its canonical reference points."""
        lower = text.lower()
        concepts = self._reference_data.get("concepts", {})
        matched_points = []
        matched_concept = None

        for cid, cinfo in concepts.items():
            if cid in lower or any(alias in lower for alias in cinfo.get("aliases", [])):
                matched_concept = cinfo.get("canonical_name", cid)
                points = cinfo.get("reference_points", [])
                matched_points.append(f"{matched_concept}:\n" + "\n".join(f"- {p}" for p in points))

        if matched_points:
            return "\n\n".join(matched_points)

        # Default fallback: summary of core OOP concepts
        default_summary = [
            "Encapsulation: Bundle data and methods in a class; restrict direct access with private fields and accessors.",
            "Inheritance: Child class derives properties and methods from a parent class using 'extends'; code reuse; IS-A relationship.",
            "Polymorphism: Overloading is compile-time (static binding); overriding is runtime (dynamic binding).",
            "Abstraction: Hide complex internal implementation details, expose essential contract via abstract classes and interfaces.",
            "Interface: Blueprint contract implemented via 'implements'; multiple type inheritance.",
        ]
        return "\n".join(f"- {p}" for p in default_summary)

    def build_prompt_messages(
        self,
        mode: ConversationMode,
        user_text: str,
        recent_turns: list[dict[str, str]],
        closest_known_term: str = "Java",
        active_topic: str = "Java OOP",
    ) -> list[dict[str, str]]:
        """
        Assemble the complete messages list for Ollama /api/chat or generate:
        1. System message (mode rules + reference points)
        2. Last 4 turns only
        3. Reminder message
        4. User message
        """
        ref_points = self.get_reference_points_for_concept(user_text)

        system_prompt = f"""You are HELIX, a Java object-oriented programming practice partner.

CURRENT MODE: {mode.value}
The mode rules below override anything said earlier in the chat.

ALWAYS
- Write only what the current mode allows. Nothing before it. Nothing after it.
- Never write plans, steps, sub-goals, justifications, or comments about these instructions.
- Never praise. Do not write Great, Correct, Absolutely, or Well done.
- The topic is Java OOP unless the user changes it. Every code example is Java.
- If a word is unknown or looks misheard, do not guess its meaning. Output only: Did you mean '{closest_known_term}'?
- If you are not sure a fact is right, write: Not sure.
- Factual Premise Verification: Agree when evidence supports agreement, politely correct when evidence disproves a claim, and admit uncertainty when evidence is insufficient. Do not contradict opinions, preferences, or valid facts. Correctness takes priority over false agreement.

MODE RATE (the user gave a definition)
Output exactly two lines:
Score: N/10
Missing: item; item; item
Use at most 3 missing items, each under 10 words. Judge strictly against REFERENCE POINTS. Do not explain.
Scoring rule: If key reference points are missing (such as overloading vs overriding for polymorphism, or private fields/class unit for encapsulation), the score must be 6 or lower.

MODE QUIZ (the user wants to be asked questions)
Output exactly one question about {active_topic}, in one sentence. Do not answer it. Do not add an example.

MODE GRADE (the user answered your question)
Output exactly three lines:
Score: N/10
Missing: item; item
Next: one new question about {active_topic}
Scoring rule: If key reference points are missing, score must be 6 or lower.

MODE LISTEN (the user says they will define the topics)
Output exactly: Ready. Send the first definition.

REFERENCE POINTS
{ref_points}"""

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt}
        ]

        # Add recent turns (capped at last 4 turns)
        capped_turns = recent_turns[-4:]
        for turn in capped_turns:
            messages.append({"role": turn["role"], "content": turn["content"]})

        # Pre-turn reinforcement reminder immediately before user text
        reminder = f"Mode={mode.value}. Follow mode format exactly. Evidence-aware: agree when supported, correct when false, admit uncertainty when insufficient. No praise. No extra text."
        messages.append({"role": "system", "content": reminder})
        messages.append({"role": "user", "content": user_text})

        return messages
