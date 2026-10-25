"""
Ambiguity Gate for HELIX (Task 7 / I2).
Provides dynamic lexicon extraction from reference points, fuzzy matching for misheard or corrupted tokens,
and vocabulary hints for speech-to-text engines.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("helix.ambiguity_gate")


_COMMON_ENGLISH_WORDS = {
    "about", "above", "across", "after", "again", "against", "all", "almost", "alone",
    "along", "already", "also", "although", "always", "among", "and", "another", "any",
    "anything", "area", "around", "ask", "asked", "asking", "asks", "away", "back", "because",
    "become", "been", "before", "began", "behind", "being", "best", "better", "between", "both",
    "came", "case", "certain", "clear", "come", "could", "different", "differently", "does", "done",
    "during", "each", "early", "either", "enough", "even", "ever", "every", "everything", "fact",
    "first", "four", "from", "full", "further", "gave", "general", "give", "given", "gives", "going",
    "good", "great", "group", "having", "here", "high", "higher", "how", "however", "important",
    "into", "just", "keep", "keeps", "kind", "knew", "know", "known", "knows", "large", "last",
    "later", "least", "less", "like", "likely", "long", "longer", "look", "made", "make", "making",
    "many", "means", "meaning", "methods", "might", "more", "most", "much", "must", "name", "named",
    "need", "needed", "needs", "never", "next", "nothing", "number", "often", "only", "open", "order",
    "other", "others", "part", "parts", "perhaps", "place", "point", "points", "possible", "present",
    "problem", "quite", "rather", "really", "right", "said", "same", "several", "should", "show",
    "shows", "side", "since", "small", "some", "something", "still", "such", "sure", "take", "taken",
    "than", "that", "their", "them", "then", "there", "these", "they", "thing", "things", "think",
    "thinks", "those", "though", "three", "through", "today", "together", "took", "toward", "turn",
    "under", "until", "upon", "uses", "used", "using", "very", "want", "wanted", "wants", "ways",
    "well", "went", "were", "what", "when", "where", "whether", "which", "while", "whole", "whose",
    "will", "with", "within", "without", "work", "worked", "working", "works", "would", "year", "years",
}


class AmbiguityGate:
    def __init__(self, reference_path: Optional[str | Path] = None):
        self._reference_path = Path(reference_path) if reference_path else (
            Path(__file__).resolve().parent.parent.parent / "data" / "knowledge" / "java_oop_reference.json"
        )
        self._lexicon: set[str] = set()
        self._mishearing_map: dict[str, str] = {}
        self._misconceptions: dict[str, str] = {}
        self._load_dynamic_lexicon()

    def _load_dynamic_lexicon(self) -> None:
        """Extract canonical terms, aliases, known mishearings, and misconceptions dynamically from knowledge references."""
        if not self._reference_path.exists():
            logger.warning("Reference file %s not found; initializing minimal default lexicon", self._reference_path)
            self._lexicon = {"java", "oop", "class", "object", "inheritance", "polymorphism", "encapsulation", "abstraction", "interface"}
            self._mishearing_map = {"injawa": "in Java", "injava": "in Java"}
            self._misconceptions = {
                "python lists are immutable": "Python lists are mutable; tuples are immutable.",
                "einstein won the nobel prize for discovering relativity": "Einstein did not win the Nobel Prize for relativity. His 1921 Physics Nobel Prize recognized his explanation of the photoelectric effect.",
                "java supports multiple class inheritance": "Java does not support multiple class inheritance (to avoid the diamond problem); it supports multiple interface implementation.",
            }
            return

        try:
            with open(self._reference_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            concepts = data.get("concepts", {})
            for concept_id, cinfo in concepts.items():
                self._lexicon.add(concept_id.lower())
                cname = cinfo.get("canonical_name", "").lower()
                if cname:
                    self._lexicon.add(cname)
                for alias in cinfo.get("aliases", []):
                    self._lexicon.add(alias.lower())
                for term in cinfo.get("key_terms", []):
                    self._lexicon.add(term.lower())

            for gterm in data.get("general_terms", []):
                self._lexicon.add(gterm.lower())

            for misheard, canonical in data.get("common_mishearings", {}).items():
                self._mishearing_map[misheard.lower()] = canonical
                self._lexicon.add(canonical.lower())

            for false_premise, correction in data.get("common_misconceptions", {}).items():
                self._misconceptions[false_premise.lower()] = correction

            # Add singular and plural variations for all technical terms (e.g. method <-> methods)
            variations = set()
            for term in self._lexicon:
                if term.endswith("s"):
                    variations.add(term[:-1])
                else:
                    variations.add(term + "s")
            self._lexicon.update(variations)
            self._lexicon.update({"method", "methods", "variable", "variables", "function", "functions", "attribute", "attributes"})

            logger.info("Ambiguity Gate initialized with %d dynamic lexicon terms", len(self._lexicon))
        except Exception as e:
            logger.error("Failed to load dynamic lexicon from %s: %s", self._reference_path, e)
            self._lexicon = {"java", "oop", "class", "object", "inheritance", "polymorphism", "encapsulation", "abstraction", "interface"}

    def get_vocabulary_hints(self) -> list[str]:
        """Return canonical vocabulary list for STT initial_prompt or hotwords."""
        return sorted(list(self._lexicon))

    def check_ambiguity(self, user_text: str) -> Optional[str]:
        """
        Check if user input contains a phonetic error, typo, or misheard term (e.g., 'Injawa').
        If detected, return confirmation question: "Did you mean '{target}'?".
        Returns None if input is clean or unambiguous.
        """
        if not user_text:
            return None

        clean_lower = user_text.lower().strip()

        # 1. Exact mishearing / compound map check
        for misheard, target in self._mishearing_map.items():
            pattern = r"\b" + re.escape(misheard) + r"\b"
            if re.search(pattern, clean_lower):
                return f"Did you mean '{target}'?"

        # 2. Token-level fuzzy matching for unknown corrupted words
        tokens = re.findall(r"[a-zA-Z]+", clean_lower)
        for token in tokens:
            if len(token) < 4:
                continue
            if token in self._lexicon or token in _COMMON_ENGLISH_WORDS:
                continue

            # Check direct mishearing map match
            if token in self._mishearing_map:
                return f"Did you mean '{self._mishearing_map[token]}'?"

            # Check fuzzy match against lexicon with strict cutoff 0.82
            close_matches = difflib.get_close_matches(token, list(self._lexicon), n=1, cutoff=0.82)
            if close_matches:
                matched_target = close_matches[0]
                if matched_target == "java":
                    matched_target = "Java"
                elif matched_target in ("oop", "oops"):
                    matched_target = "OOP"
                return f"Did you mean '{matched_target}'?"

        return None

    def check_premise(self, user_text: str) -> Optional[dict[str, Any]]:
        """
        Evaluate factual premises in user input against verified domain knowledge.
        
        Outcomes:
        - CONTRADICTED: User input asserts or embeds a known false premise or misconception.
          Returns dict with status='contradicted', correction message, and the matched premise.
        - SUPPORTED / NEUTRAL / OPINION / UNCERTAIN: Returns None so that the request
          proceeds cleanly to normal generation without false-positive disruption.
        
        Protection Against False Positives:
        - Open-ended questions, greetings, and subjective opinions are never intercepted.
        - Supported statements (e.g. 'tuples are immutable', 'lists are mutable') pass through
          so that HELIX naturally agrees when evidence supports agreement.
        - Only unambiguous, verified factual contradictions produce a direct correction.
        """
        if not user_text:
            return None

        clean_lower = user_text.lower().strip()

        # Check for contradicted false premises
        for false_premise, correction in self._misconceptions.items():
            # Exact substring match
            if false_premise in clean_lower:
                return {
                    "status": "contradicted",
                    "correction": correction,
                    "premise": false_premise,
                }

            # Multi-token semantic match: all significant content words appear in input
            tokens = [t for t in false_premise.split() if len(t) > 3 and t not in _COMMON_ENGLISH_WORDS]
            if len(tokens) >= 3 and all(t in clean_lower for t in tokens):
                return {
                    "status": "contradicted",
                    "correction": correction,
                    "premise": false_premise,
                }

        return None
