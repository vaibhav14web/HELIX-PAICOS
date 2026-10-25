"""
core_ai/pragmatics/pragmatic_interpreter.py

Interprets Gen Z slang, catchphrases, internet culture, idioms, sarcasm,
and Hinglish with contextual awareness, safety verification, and sentiment resolution.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from core_ai.pragmatics.models import (
    PragmaticInterpretation,
    RegisterTone,
    SlangMatch,
    SpeakerIntent,
)
from core_ai.pragmatics.slang_glossary import SlangGlossary

logger = logging.getLogger("helix.pragmatics.interpreter")

# Common Hinglish and Hindi markers
_HINGLISH_TOKENS = {
    "bhai", "kya", "scene", "hai", "yaar", "jugaad", "mast", "bakwaas", "bakwas",
    "chalta", "tension", "mat", "le", "lo", "arre", "suno", "achha", "theek",
    "kaise", "kaisa", "ho", "gaya", "karo", "karna", "badhiya", "chal", "raha",
    "kuch", "nahi", "hota", "sab", "sorted", "bolo", "batao", "chalega", "dost"
}

# Sarcasm markers: positive words juxtaposed with failure/trouble words
_SARCASM_POSITIVE_WORDS = {
    "great", "wonderful", "amazing", "love", "fantastic", "perfect", "brilliant", "awesome"
}
_SARCASM_NEGATIVE_WORDS = {
    "crashed", "crash", "failed", "fail", "bug", "error", "broke", "broken", "lost",
    "ruined", "again", "died", "freeze", "frozen", "segfault", "exception", "typo", "typos",
    "waste", "wasted", "headache", "revert"
}

# Injection attack patterns commonly embedded in quotes/memes
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous\s+)?instructions", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+", re.IGNORECASE),
    re.compile(r"format\s+[a-z]:", re.IGNORECASE),
    re.compile(r"rm\s+-rf", re.IGNORECASE),
    re.compile(r"delete\s+(?:all\s+)?files", re.IGNORECASE),
    re.compile(r"drop\s+database", re.IGNORECASE),
]

# Genuine application action verbs and targets
_REAL_ACTION_PREFIXES = ("open ", "launch ", "start ", "search for ", "browse for ", "read file ", "list files", "scan files")
_KNOWN_APP_TARGETS = {"chrome", "notepad", "calculator", "calc", "paint", "terminal", "explorer", "edge", "firefox"}


class PragmaticInterpreter:
    """
    Evaluates informal utterances, resolves contextual meanings,
    disambiguates ambiguity, and guards against prompt injection and false actions.
    """

    def __init__(self, glossary: Optional[SlangGlossary] = None):
        self.glossary = glossary or SlangGlossary()

    def interpret(
        self,
        text: str,
        conversation_history: Optional[list[dict[str, str]]] = None,
    ) -> PragmaticInterpretation:
        if not text or not text.strip():
            return PragmaticInterpretation(
                original_text=text,
                normalized_query="",
                inferred_meaning="Empty message",
                confidence=1.0,
            )

        raw_text = text.strip()
        history = conversation_history or []

        # 1. ASR phonetic normalization (e.g. "no cup" -> "no cap", "touch glass" -> "touch grass")
        normalized_text = self.glossary.normalize_asr_transcript(raw_text)

        # 2. Language & dialect detection
        language = self._detect_language(normalized_text)

        # 3. Match slang expressions in normalized text
        raw_matches = self.glossary.find_matches(normalized_text)

        # 4. Context aggregation (extract recent turns for context resolution)
        prior_context_text = self._aggregate_prior_context(history)
        full_turn_context = f"{prior_context_text} {normalized_text}".lower()

        # 5. Contextual disambiguation of matched expressions
        detected_slang_matches: list[SlangMatch] = []
        for entry, matched_str, start, end in raw_matches:
            resolved_meaning, sentiment, intent = self._resolve_expression_context(
                entry, matched_str, normalized_text, full_turn_context
            )
            detected_slang_matches.append(
                SlangMatch(
                    entry=entry,
                    matched_text=matched_str,
                    start_pos=start,
                    end_pos=end,
                    resolved_meaning=resolved_meaning,
                    sentiment=sentiment,
                    intent=intent,
                )
            )

        # 6. Sarcasm & irony detection
        is_sarcastic = self._detect_sarcasm(normalized_text)

        # 7. Quoted catchphrase & prompt injection inspection
        is_untrusted_quote, sanitized_quote = self._inspect_untrusted_quotes(normalized_text)

        # 8. Genuine action detection vs conversational appraisal
        contains_genuine_action, genuine_action_text = self._detect_genuine_action(
            normalized_text, detected_slang_matches
        )

        # 9. Style and Topic Adaptation
        explicit_style = self._detect_explicit_style_request(normalized_text)
        topic_type = self._detect_topic_type(normalized_text)

        # Evaluation Contract Detection
        is_concept_eval, concise_feedback, contract_active = self._detect_evaluation_contract(
            normalized_text, history
        )
        if concise_feedback and not explicit_style:
            explicit_style = "concise"

        recommended_style = self._determine_recommended_style(
            explicit_style=explicit_style,
            topic_type=topic_type,
            language=language,
            has_slang=bool(detected_slang_matches),
            is_sarcastic=is_sarcastic,
        )
        if concise_feedback:
            recommended_style = "concise"

        # 10. Primary Speaker Intent & Sentiment Resolution
        speaker_intent, dominant_sentiment, inferred_meaning, is_uncertain, confidence = self._synthesize_meaning(
            normalized_text=normalized_text,
            matches=detected_slang_matches,
            is_sarcastic=is_sarcastic,
            is_untrusted_quote=is_untrusted_quote,
            sanitized_quote=sanitized_quote,
            language=language,
            contains_genuine_action=contains_genuine_action,
            genuine_action_text=genuine_action_text,
            full_context=full_turn_context,
        )

        if is_concept_eval or contract_active:
            if speaker_intent == SpeakerIntent.CONVERSATIONAL:
                speaker_intent = SpeakerIntent.CONCEPT_EVALUATION
            if not inferred_meaning or inferred_meaning == "Standard conversational utterance.":
                inferred_meaning = "The user is participating in a conceptual explanation or evaluation exercise."

        return PragmaticInterpretation(
            original_text=raw_text,
            normalized_query=normalized_text,
            detected_expressions=detected_slang_matches,
            speaker_intent=speaker_intent,
            inferred_meaning=inferred_meaning,
            sentiment=dominant_sentiment,
            is_sarcastic=is_sarcastic,
            contains_genuine_action=contains_genuine_action,
            genuine_action_text=genuine_action_text,
            language=language,
            confidence=confidence,
            is_uncertain=is_uncertain,
            is_untrusted_quote=is_untrusted_quote,
            sanitized_interpretation=sanitized_quote,
            explicit_style_request=explicit_style,
            contextual_topic_type=topic_type,
            recommended_response_style=recommended_style,
            is_concept_evaluation=is_concept_eval,
            concise_feedback=concise_feedback,
            evaluation_contract_active=contract_active,
        )

    def _detect_explicit_style_request(self, text: str) -> Optional[str]:
        lower = text.lower().strip()
        formal_patterns = [
            r"\b(?:be|speak|talk|sound|write|keep\s+it|switch\s+to|stay)\s+formal(?:ly)?\b",
            r"\b(?:be|stay|keep\s+it)\s+professional(?:ly)?\b",
            r"\bprofessional\s+tone\b",
            r"\bformal\s+(?:mode|style|tone)\b",
            r"\bno\s+slang\b",
            r"\b(?:stop|don'?t)\s+using\s+slang\b",
        ]
        if any(re.search(p, lower) for p in formal_patterns):
            return "formal"

        casual_patterns = [
            r"\b(?:be|speak|talk|sound|keep\s+it|switch\s+to)\s+casual(?:ly)?\b",
            r"\bcasual\s+(?:mode|style|tone)\b",
            r"\brelax\s+(?:your\s+)?tone\b",
            r"\bchill\s+out\b",
            r"\btalk\s+like\s+a\s+friend\b",
        ]
        if any(re.search(p, lower) for p in casual_patterns):
            return "casual"

        playful_patterns = [
            r"\b(?:be|sound|switch\s+to)\s+playful\b",
            r"\b(?:be|sound)\s+funny\b",
            r"\buse\s+humor\b",
            r"\bjoke\s+with\s+me\b",
            r"\bplayful\s+(?:mode|style|tone)\b",
            r"\btell\s+a\s+joke\b",
        ]
        if any(re.search(p, lower) for p in playful_patterns):
            return "playful"

        technical_patterns = [
            r"\b(?:keep\s+it|be|sound|speak|switch\s+to)\s+technical(?:ly)?\b",
            r"\btechnical\s+(?:mode|style|tone)\b",
            r"\bprecise\s+explanations\b",
            r"\bfocus\s+on\s+the\s+code\b",
        ]
        if any(re.search(p, lower) for p in technical_patterns):
            return "technical"

        hinglish_patterns = [
            r"\b(?:speak|talk|switch\s+to|use)\s+(?:in\s+)?hinglish\b",
            r"\bhinglish\s+(?:mode|style|tone)\b",
            r"\bhindi\s+english\s+mix\b",
        ]
        if any(re.search(p, lower) for p in hinglish_patterns):
            return "hinglish"

        slang_patterns = [
            r"\b(?:use|talk\s+in)\s+slang\b",
            r"\bslang\s+(?:is\s+fine|mode|friendly)\b",
            r"\byou\s+can\s+use\s+slang\b",
            r"\btalk\s+gen\s*z\b",
        ]
        if any(re.search(p, lower) for p in slang_patterns):
            return "slang_friendly"

        concise_patterns = [
            r"\b(?:keep\s+it|be|make\s+it)\s+(?:short|brief|concise)\b",
            r"\bshort\s+(?:responses?|answers?|feedback)\b",
            r"\bbrief\s+(?:responses?|answers?|feedback|explanation)\b",
            r"\bconcise\s+(?:mode|style|tone|feedback)\b",
            r"\bkeep\s+your\s+responses\s+short\b",
            r"\bnot\s+a\s+whole\s+paragraph\b",
        ]
        if any(re.search(p, lower) for p in concise_patterns):
            return "concise"

        return None

    def _detect_evaluation_contract(
        self, text: str, history: list[dict[str, str]]
    ) -> tuple[bool, bool, bool]:
        """
        Detects explicit requests to establish or continue a concept-evaluation session contract.
        Returns:
            (is_concept_evaluation, concise_feedback, evaluation_contract_active)
        """
        lower = text.lower().strip()

        # 1. Check for contract termination
        termination_patterns = [
            r"\b(?:stop|done\s+with|finish(?:ed)?|end)\s+(?:revising|revision|evaluation|quiz)\b",
            r"\b(?:let\'?s\s+move\s+on|change\s+the\s+topic|new\s+topic)\b",
        ]
        if any(re.search(p, lower) for p in termination_patterns):
            return False, False, False

        # 2. Check current turn for contract establishment
        contract_establishment_patterns = [
            r"\brevising\s+(?:[a-z0-9_-]+\s+)*concepts\b",
            r"\bi\s+(?:will|want\s+to)\s+explain\s+(?:them|concepts?|it)\s+to\s+you\b",
            r"\btell\s+me\s+if\s+(?:my\s+explanation|it|my\s+answer)\s+is\s+correct\b",
            r"\bjudge\s+(?:whether|if)?\s*(?:my\s+explanation|it)\b",
            r"\bevaluate\s+(?:my\s+explanation|my\s+answer|this\s+definition)\b",
            r"\bcorrect[,\s]+partially\s+correct[,\s]+(?:or\s+)?incorrect\b",
        ]
        is_establishing = any(re.search(p, lower) for p in contract_establishment_patterns)

        # 3. Check for concise feedback requests
        concise_patterns = [
            r"\b(?:keep\s+it|be|make\s+it)\s+(?:short|brief|concise)\b",
            r"\bshort\s+(?:responses?|answers?|feedback)\b",
            r"\bbrief\s+(?:responses?|answers?|feedback|explanation)\b",
            r"\bconcise\s+(?:mode|style|tone|feedback)\b",
            r"\bkeep\s+your\s+responses\s+short\b",
            r"\bnot\s+a\s+whole\s+paragraph\b",
        ]
        concise_requested = any(re.search(p, lower) for p in concise_patterns)

        # 4. Check conversation history for active contract
        history_has_contract = False
        history_concise = False
        for turn in reversed(history[-6:]):
            c = turn.get("content", "").lower()
            if any(re.search(p, c) for p in termination_patterns):
                break
            if any(re.search(p, c) for p in contract_establishment_patterns):
                history_has_contract = True
                if any(re.search(p, c) for p in concise_patterns):
                    history_concise = True
                break

        # 5. Check if current turn is a concept definition / explanation
        definition_patterns = [
            r"\b[a-z0-9_-]+\s+is\s+(?:the\s+)?(?:bundling|process|blueprint|concept|mechanism|ability|practice)\b",
            r"\b[a-z0-9_-]+\s+allows\s+(?:objects|classes|programs|functions)\b",
            r"\b[a-z0-9_-]+\s+means\s+(?:that|restricting|hiding|wrapping)\b",
            r"\b[a-z0-9_-]+\s+refers\s+to\b",
        ]
        is_def = any(re.search(p, lower) for p in definition_patterns)

        is_concept_eval = is_establishing or (history_has_contract and is_def) or (history_has_contract and not lower.startswith(("open ", "launch ", "how do i", "what is the weather")))
        concise_feedback = concise_requested or (history_has_contract and history_concise)
        contract_active = is_establishing or history_has_contract

        return is_concept_eval, concise_feedback, contract_active

    def _detect_topic_type(self, text: str) -> str:
        lower = text.lower()
        words = set(re.findall(r"\b[a-z]+\b", lower))
        security_words = {"permission", "permissions", "authorize", "grant", "deny", "secret", "auth", "encrypt", "dpapi", "hash", "token"}
        if bool(words & security_words):
            return "security"

        technical_words = {
            "code", "algorithm", "function", "class", "method", "bug", "crash", "exception",
            "error", "stacktrace", "traceback", "git", "database", "api", "endpoint", "architecture",
            "eventbus", "planner", "executor", "runtime", "pipeline", "schema"
        }
        if bool(words & technical_words) or "def " in text or "```" in text:
            return "technical"

        return "general"

    def _determine_recommended_style(
        self,
        explicit_style: Optional[str],
        topic_type: str,
        language: str,
        has_slang: bool,
        is_sarcastic: bool,
        user_dialogue_style: Optional[str] = None,
    ) -> str:
        if explicit_style:
            return explicit_style

        if topic_type in ("security", "technical"):
            if user_dialogue_style in ("formal", "technical"):
                return user_dialogue_style
            return "technical"

        if user_dialogue_style:
            return user_dialogue_style

        if language in ("hinglish", "mixed"):
            return "hinglish"

        if is_sarcastic:
            return "playful"

        if has_slang:
            return "casual"

        return "casual"

    def _detect_language(self, text: str) -> str:
        words = set(re.findall(r"\b[a-zA-Z]+\b", text.lower()))
        hinglish_count = len(words & _HINGLISH_TOKENS)
        if hinglish_count >= 2:
            return "hinglish"
        elif hinglish_count == 1:
            # Check if majority or significant part
            if len(words) <= 4:
                return "hinglish"
            return "mixed"
        return "english"

    def _aggregate_prior_context(self, history: list[dict[str, str]], turn_depth: int = 3) -> str:
        """Collects text from the last few conversation turns."""
        if not history:
            return ""
        recent = history[-turn_depth:]
        return " ".join(turn.get("content", "") for turn in recent)

    def _resolve_expression_context(
        self,
        entry: Any,
        matched_str: str,
        current_text: str,
        full_context: str,
    ) -> tuple[str, str, SpeakerIntent]:
        """Resolves context-dependent meaning, sentiment, and intent for a matched slang entry."""
        # Check if entry has contextual rules
        if entry.contextual_rules:
            # First check current turn words
            curr_lower = current_text.lower()
            curr_words = set(re.findall(r"\b[a-z0-9_]+\b", curr_lower))
            for rule in entry.contextual_rules:
                if any(trigger in curr_words for trigger in rule.trigger_words):
                    return rule.meaning, rule.sentiment, rule.intent

            # Next check full context (prior turns)
            full_words = set(re.findall(r"\b[a-z0-9_]+\b", full_context))
            for rule in entry.contextual_rules:
                if any(trigger in full_words for trigger in rule.trigger_words):
                    return rule.meaning, rule.sentiment, rule.intent

        # Default meaning
        meaning = entry.primary_meaning

        # Assign default sentiment based on entry tone / meaning
        expr = entry.expression.lower()
        if expr in ("fire", "slay", "ate", "killing it", "mast", "badhiya", "scene sorted"):
            sentiment = "positive"
            intent = SpeakerIntent.COMPLIMENT
        elif expr in ("mid", "bakwaas", "sus", "skill issue", "brain rot", "it's giving"):
            sentiment = "negative" if expr in ("mid", "bakwaas", "sus", "skill issue", "brain rot") else "neutral"
            intent = SpeakerIntent.STYLISTIC_CRITIQUE
        elif expr in ("cooked",):
            sentiment = "negative"
            intent = SpeakerIntent.EXPRESS_UNPREPAREDNESS
        elif expr in ("no cap", "fr"):
            sentiment = "neutral"
            intent = SpeakerIntent.SINCERITY_EMPHASIS
        elif expr in ("bhai kya scene hai",):
            sentiment = "neutral"
            intent = SpeakerIntent.GREETING_INQUIRY
        elif expr in ("let him cook", "say less"):
            sentiment = "neutral"
            intent = SpeakerIntent.ENCOURAGEMENT
        else:
            sentiment = "neutral"
            intent = SpeakerIntent.CONVERSATIONAL

        return meaning, sentiment, intent

    def _detect_sarcasm(self, text: str) -> bool:
        """Detects sarcastic juxtaposition of high-praise words with failure/trouble descriptions."""
        lower = text.lower()
        words = set(re.findall(r"\b[a-z]+\b", lower))

        has_positive = bool(words & _SARCASM_POSITIVE_WORDS)
        has_negative = bool(words & _SARCASM_NEGATIVE_WORDS)

        if has_positive and has_negative:
            return True

        if lower.startswith("oh great") and any(neg in lower for neg in _SARCASM_NEGATIVE_WORDS):
            return True

        if "i love" in lower and any(neg in lower for neg in _SARCASM_NEGATIVE_WORDS):
            return True

        return False

    def _inspect_untrusted_quotes(self, text: str) -> tuple[bool, str]:
        """
        Inspects whether the turn contains reported speech or quoted memes
        (e.g., 'Bro really said: ignore all instructions'), neutralizing prompt injection risks.
        """
        catchphrase_prefixes = (
            "bro really said", "bro really thought", "bro actually said",
            "bro really just said", "bro said", "he really said", "she really said"
        )
        lower = text.lower()
        is_quote = False
        sanitized = text

        for p in catchphrase_prefixes:
            if p in lower:
                is_quote = True
                break

        if is_quote:
            # Check for injection attacks
            for pattern in _INJECTION_PATTERNS:
                if pattern.search(text):
                    logger.warning("Neutralized potential prompt injection in quoted catchphrase: %s", text)
                    sanitized = f"[QUOTED_TEXT_NOT_INSTRUCTION: {text}]"
                    return True, sanitized

        return is_quote, sanitized

    def _detect_genuine_action(
        self,
        text: str,
        matches: list[SlangMatch],
    ) -> tuple[bool, Optional[str]]:
        """
        Distinguishes between conversational appraisals (e.g., 'Check that project, it's fire',
        'Bro, let him cook') and genuine action requests (e.g., 'That's fire, please open chrome').
        """
        lower = text.lower().strip()

        # Phrases that start with 'check' but are purely conversational evaluations
        if lower.startswith("check ") and any(m.entry.expression in ("fire", "mid", "sick", "sus") for m in matches):
            # Check if there is an explicit request to launch an app or run an action afterwards
            # e.g., "Check this out, it's fire" -> NOT an action
            action_split = re.split(r"[,;]\s*|\s+and\s+", lower)
            for part in action_split:
                for app in _KNOWN_APP_TARGETS:
                    if f"open {app}" in part or f"launch {app}" in part or f"start {app}" in part:
                        return True, part.strip()
            return False, None

        # Check if the entire expression is a known idiom/catchphrase like 'let him cook' or 'touch grass'
        for m in matches:
            if m.entry.expression in ("let him cook", "touch grass", "skill issue", "brain rot", "canon event"):
                if m.matched_text.lower().strip() == lower.strip():
                    return False, None

        # Check for mixed phrases: genuine action + slang
        # e.g., "That's sick, open chrome" or "Bro open notepad, no cap"
        for app in _KNOWN_APP_TARGETS:
            if f"open {app}" in lower:
                return True, f"open {app}"
            if f"launch {app}" in lower:
                return True, f"launch {app}"
            if f"start {app}" in lower:
                return True, f"start {app}"

        # Standard action prefixes if not part of slang
        for prefix in _REAL_ACTION_PREFIXES:
            if lower.startswith(prefix):
                # Ensure the target is not slang praise
                target = lower[len(prefix):].strip()
                if not any(m.matched_text.lower() in target for m in matches):
                    return True, lower

        return False, None

    def _synthesize_meaning(
        self,
        normalized_text: str,
        matches: list[SlangMatch],
        is_sarcastic: bool,
        is_untrusted_quote: bool,
        sanitized_quote: str,
        language: str,
        contains_genuine_action: bool,
        genuine_action_text: Optional[str],
        full_context: str,
    ) -> tuple[SpeakerIntent, str, str, bool, float]:
        """Synthesizes the overall pragmatic intent, sentiment, inferred meaning, and uncertainty."""
        if is_sarcastic:
            intent = SpeakerIntent.SARCASTIC_REMARK
            sentiment = "negative"
            meaning = "The user is expressing frustration or skepticism through irony, contrary to literal words."
            return intent, sentiment, meaning, False, 0.95

        if is_untrusted_quote:
            intent = SpeakerIntent.CONVERSATIONAL
            sentiment = "neutral"
            meaning = f"The user is humorously reporting or quoting a notable statement: '{sanitized_quote}'."
            return intent, sentiment, meaning, False, 0.95

        if contains_genuine_action and genuine_action_text:
            intent = SpeakerIntent.ACTION_REQUEST
            sentiment = "neutral" if not matches else matches[0].sentiment
            meaning = f"The user is requesting an authorized system action: '{genuine_action_text}' while conversing informally."
            return intent, sentiment, meaning, False, 0.95

        if matches:
            primary_match = matches[0]
            intent = primary_match.intent
            sentiment = primary_match.sentiment

            # Synthesize human-readable inferred meaning
            meanings = [f"'{m.matched_text}' signifies {m.resolved_meaning}" for m in matches]
            meaning_str = "; ".join(meanings)

            # Specific phrasing adjustments
            lower = normalized_text.lower()
            if "that project is fire" in lower or "feature is fire" in lower:
                meaning_str = "The user evaluates the project/feature as remarkably impressive and high quality."
            elif "i'm cooked for tomorrow's exam" in lower or "im cooked for tomorrow" in lower:
                meaning_str = "The user feels severely unprepared or worried about an upcoming exam/evaluation."
            elif "i'm cooked; i haven't slept" in lower or "im cooked i havent slept" in lower:
                meaning_str = "The user is suffering from severe physical exhaustion due to lack of sleep."
            elif "bro, let him cook" in lower or "let him cook" in lower:
                meaning_str = "The user is encouraging giving someone freedom and space to continue their activity or plan."
            elif "no cap, i'm serious" in lower or "no cap im serious" in lower:
                meaning_str = "The user is emphatically reinforcing their absolute sincerity and truthfulness."
            elif "it's giving school project" in lower or "its giving school project" in lower:
                meaning_str = "The user perceives the subject as having the amateur aesthetic or vibe of a school project."
            elif "bhai, kya scene hai" in lower or "bhai kya scene hai" in lower or "kya scene hai" in lower:
                meaning_str = "The user is casually inquiring in Hinglish about the current situation, plan, or status update."

            return intent, sentiment, meaning_str, False, 0.95

        # If no glossary matches: check for unknown/emerging informal speech
        words = set(re.findall(r"\b[a-z]+\b", normalized_text.lower()))
        unfamiliar_slang_markers = {"skibidi", "gyatt", "fanum", "mewing", "delulu", "solulu", "bop", "yeet"}
        if bool(words & unfamiliar_slang_markers):
            return (
                SpeakerIntent.UNKNOWN_INFORMAL,
                "neutral",
                "The utterance contains unfamiliar or emerging informal internet slang; meaning is unverified in glossary.",
                True,
                0.40,
            )

        # Normal conversational turn
        return (
            SpeakerIntent.CONVERSATIONAL,
            "neutral",
            "Standard conversational utterance.",
            False,
            1.0,
        )
