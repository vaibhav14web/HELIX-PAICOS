"""
core_ai/voice_engine/pronunciation_pipeline.py

Dedicated Pronunciation & Speech-Preparation Pipeline for HELIX TTS.
Transforms raw assistant responses into a speech-ready representation
optimized for intelligibility, correct cadence, and natural phonetics,
while keeping the original chat text completely pristine.
"""
from __future__ import annotations

import re
import logging
from typing import Optional, Dict, Any

logger = logging.getLogger("helix.pronunciation_pipeline")

# Emojis regex pattern for speech stripping
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U000024C2-\U0001F251"  # enclosed characters
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero-width joiner
    "]+",
    re.UNICODE,
)

_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_MULTI_SPACE_PATTERN = re.compile(r"\s+")


class PronunciationPipeline:
    """
    Modular pronunciation preparation pipeline executed immediately before TTS inference.
    """

    def __init__(self, custom_overrides: Optional[Dict[str, str]] = None):
        self._custom_overrides = custom_overrides or {}

        # Technical acronyms that must be spelled out phonetically for TTS clarity
        self._tech_acronyms: dict[str, str] = {
            r"\bAPI\b": "A P I",
            r"\bAPIs\b": "A P Is",
            r"\bGPU\b": "G P U",
            r"\bGPUs\b": "G P Us",
            r"\bCPU\b": "C P U",
            r"\bCPUs\b": "C P Us",
            r"\bSQL\b": "sequel",
            r"\bPostgreSQL\b": "Post-gres Q L",
            r"\bPostgres\b": "Post-gres",
            r"\bMySQL\b": "My-sequel",
            r"\bSQLite\b": "sequel-ite",
            r"\bNoSQL\b": "No-sequel",
            r"\bURL\b": "U-R-L",
            r"\bURLs\b": "U-R-Ls",
            r"\bURI\b": "U-R-I",
            r"\bUI\b": "U-I",
            r"\bCLI\b": "C-L-I",
            r"\bGUI\b": "G-U-I",
            r"\bSDK\b": "S-D-K",
            r"\bSDKs\b": "S-D-Ks",
            r"\bIDE\b": "I-D-E",
            r"\bIDEs\b": "I-D-Es",
            r"\bOS\b": "O-S",
            r"\bSSH\b": "S-S-H",
            r"\bSSL\b": "S-S-L",
            r"\bTLS\b": "T-L-S",
            r"\bHTTP\b": "H-T-T-P",
            r"\bHTTPS\b": "H-T-T-P-S",
            r"\bJSON\b": "Jason",
            r"\bYAML\b": "Yam-el",
            r"\bHTML\b": "H-T-M-L",
            r"\bCSS\b": "C-S-S",
            r"\bDOM\b": "D-O-M",
            r"\bDNS\b": "D-N-S",
            r"\bIP\b": "I-P",
            r"\bLLM\b": "L-L-M",
            r"\bLLMs\b": "L-L-Ms",
            r"\bTTS\b": "T-T-S",
            r"\bSTT\b": "S-T-T",
            r"\bASR\b": "A-S-R",
            r"\bQdrant\b": "Queue-drant",
            r"\bFastAPI\b": "Fast A-P-I",
            r"\bPyTorch\b": "Pie-Torch",
            r"\basyncio\b": "async-I-O",
        }

        # Common spoken conversational abbreviations
        self._spoken_abbreviations: dict[str, str] = {
            r"\btbh\b": "to be honest",
            r"\bngl\b": "not gonna lie",
            r"\bidk\b": "I don't know",
            r"\bfr\b": "for real",
            r"\brn\b": "right now",
            r"\bgg\b": "good game",
            r"\bw/e\b": "whatever",
            r"\bw/\b": "with ",
            r"\bbc\b": "because",
            r"\bb/c\b": "because",
            r"\bimo\b": "in my opinion",
            r"\bimho\b": "in my humble opinion",
            r"\bbtw\b": "by the way",
            r"\be\.g\.,?\b": "for example,",
            r"\bi\.e\.,?\b": "that is,",
            r"\betc\.\b": "etcetera.",
            r"\bvs\.\b": "versus",
        }

    def prepare_for_speech(
        self,
        text: str,
        profile_overrides: Optional[Dict[str, str]] = None,
        language_hint: Optional[str] = None,
    ) -> str:
        """
        Execute full multi-stage pronunciation normalization on input text.
        Returns speech-ready string for TTS synthesis.
        """
        if not text or not text.strip():
            return ""

        s = text

        # Stage 1: Strip code block fences and convert raw JSON
        s = self._clean_code_and_structured_data(s)

        # Stage 2: Markdown formatting and URL stripping
        s = self._clean_markdown_and_links(s)

        # Stage 3: Normalize contractions and curly apostrophes
        s = self._normalize_contractions(s)

        # Stage 4: Punctuation and natural pause boundaries
        s = self._normalize_punctuation_boundaries(s)

        # Stage 5: Units, numbers, versions, and currency
        s = self._normalize_numbers_and_symbols(s)

        # Stage 6: Technical acronyms and vocabulary
        s = self._normalize_technical_acronyms(s)

        # Stage 7: Spoken abbreviations
        s = self._normalize_abbreviations(s)

        # Stage 8: Disambiguate Hindi / Hinglish transliterations (e.g. 'ko' vs 'K.O.')
        s = self._disambiguate_mixed_language(s, language_hint=language_hint)

        # Stage 9: Strip emojis and clean spacing
        s = self._finalize_spacing_and_emojis(s)

        # Stage 10: Apply profile-specific overrides
        combined_overrides = {**self._custom_overrides, **(profile_overrides or {})}
        for term, replacement in combined_overrides.items():
            pattern = rf"\b{re.escape(term)}\b"
            s = re.sub(pattern, replacement, s, flags=re.IGNORECASE)

        return s.strip()

    def prepare_for_tts(
        self,
        text: str,
        profile_overrides: Optional[Dict[str, str]] = None,
        language_hint: Optional[str] = None,
    ) -> str:
        """Alias for prepare_for_speech for seamless pipeline integration."""
        return self.prepare_for_speech(
            text=text,
            profile_overrides=profile_overrides,
            language_hint=language_hint,
        )

    # ── Stage Implementations ────────────────────────────────────

    def _clean_code_and_structured_data(self, text: str) -> str:
        # Strip triple backtick code blocks while preserving content
        text = re.sub(r"```(?:\w+)?\n?([\s\S]*?)```", r" \1 ", text)
        # Strip single backticks
        text = re.sub(r"`([^`]+)`", r" \1 ", text)

        # Convert raw JSON blocks
        trimmed = text.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                import json
                data = json.loads(trimmed)
                if isinstance(data, dict):
                    if "message" in data:
                        text = str(data["message"])
                    elif "name" in data:
                        tool = data.get("name", "").replace("_", " ")
                        text = f"Running {tool}."
                    else:
                        text = "I have processed the request."
            except Exception:
                text = re.sub(r'[{}\[\]"]', " ", text)
        return text

    def _clean_markdown_and_links(self, text: str) -> str:
        # [Title](url) -> Title
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
        # Raw URLs -> remove
        text = re.sub(r"https?://\S+", "", text)
        # HTML tags
        text = _HTML_TAG_PATTERN.sub(" ", text)
        # Plural markers: file(s) -> files
        text = re.sub(r"(\w+)\(s\)", r"\1s", text, flags=re.IGNORECASE)
        text = re.sub(r"(\w+)\(es\)", r"\1es", text, flags=re.IGNORECASE)
        # Windows paths in parentheses: (`C:\path\file.exe`) -> ""
        text = re.sub(r"\([`'\"]?[A-Za-z]:\\[^)]+[`'\"]?\)", "", text)
        # Redundant bullet paths: "- **name** (size) — `C:\path`" -> "- **name** (size)"
        text = re.sub(r"—\s*[`'\"]?[A-Za-z]:\\[^`'\n]+[`'\"]?", "", text)
        # Standalone Windows paths: C:\Users\vaibh\Documents -> Documents folder
        def _simplify_path(m: re.Match) -> str:
            raw = m.group(0).strip("`'\"")
            parts = [p for p in raw.replace("/", "\\").split("\\") if p]
            return f" {parts[-1]} " if len(parts) > 1 else (parts[0] if parts else "")
        text = re.sub(r"[A-Za-z]:\\[\w\s.\-\\]+", _simplify_path, text)

        # Eliminate backslashes and slashes cleanly
        text = text.replace("\\", " ")
        text = re.sub(r"(?<=\w)/(?=\w)", " and ", text)
        text = text.replace("/", " ")

        # Headers
        text = re.sub(r"^\s*#{1,6}\s*(.+)$", r"\1.", text, flags=re.MULTILINE)
        # Underscores in identifiers
        text = re.sub(r"(?<=\w)_(?=\w)", " ", text)
        # Markdown symbols (bold, italic, strikethrough, blockquotes)
        text = re.sub(r"[*_~`>|#]", " ", text)
        return text

    def _normalize_contractions(self, text: str) -> str:
        # Normalize curly apostrophes to straight ASCII apostrophe
        text = text.replace("’", "'").replace("‘", "'")
        text = text.replace("“", '"').replace("”", '"')

        # Fix any accidentally split contractions: "don t" -> "don't"
        split_fixes = [
            (r"\bdon\s+t\b", "don't"),
            (r"\bcan\s+t\b", "can't"),
            (r"\bwon\s+t\b", "won't"),
            (r"\bit\s+s\b", "it's"),
            (r"\bI\s+m\b", "I'm"),
            (r"\byou\s+re\b", "you're"),
            (r"\bwe\s+re\b", "we're"),
            (r"\bthey\s+re\b", "they're"),
            (r"\bdoesn\s+t\b", "doesn't"),
            (r"\bdidn\s+t\b", "didn't"),
            (r"\bwasn\s+t\b", "wasn't"),
            (r"\bweren\s+t\b", "weren't"),
            (r"\bhasn\s+t\b", "hasn't"),
            (r"\bhaven\s+t\b", "haven't"),
            (r"\bshouldn\s+t\b", "shouldn't"),
            (r"\bcouldn\s+t\b", "couldn't"),
            (r"\bwouldn\s+t\b", "wouldn't"),
        ]
        for pattern, replacement in split_fixes:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        # Strip surrounding quotes without breaking contractions
        text = re.sub(r"(?<![A-Za-z0-9])'|'(?![A-Za-z0-9])", " ", text)
        return text

    def _normalize_punctuation_boundaries(self, text: str) -> str:
        # Convert semicolons to pause commas
        text = re.sub(r";\s*", ", ", text)
        # Convert em dashes / en dashes / double hyphens to pause commas
        text = re.sub(r"\s*(?:—|–|--)\s*", ", ", text)
        # Convert mid-sentence colons to natural commas (e.g., "Note: this works" -> "Note, this works")
        text = re.sub(r"(?<=\w):\s+(?=[a-z])", ", ", text)
        # Ellipses to period pause
        text = re.sub(r"\.{2,}", ". ", text)
        # Normalize list bullets to comma pauses
        text = re.sub(r"^\s*[-*•]\s+", ", ", text, flags=re.MULTILINE)
        text = re.sub(r"^\s*\d+\.\s+", ", ", text, flags=re.MULTILINE)
        return text

    def _normalize_numbers_and_symbols(self, text: str) -> str:
        # Currency: $100 -> 100 dollars
        text = re.sub(r"\$(\d+(?:\.\d+)?)", r"\1 dollars", text)
        text = re.sub(r"€(\d+(?:\.\d+)?)", r"\1 euros", text)
        text = re.sub(r"£(\d+(?:\.\d+)?)", r"\1 pounds", text)
        text = re.sub(r"₹(\d+(?:\.\d+)?)", r"\1 rupees", text)

        # Versions: v1.2 -> version 1.2
        text = re.sub(r"\bv(\d+(?:\.\d+)+)\b", r"version \1", text, flags=re.IGNORECASE)

        # Percentages: 50% -> 50 percent
        text = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"\1 percent", text)

        # File sizes: (0.4 KB) -> 0.4 kilobytes
        text = re.sub(r"\(\s*(\d+(?:\.\d+)?)\s*KB\s*\)", r", \1 kilobytes", text, flags=re.IGNORECASE)
        text = re.sub(r"\(\s*(\d+(?:\.\d+)?)\s*MB\s*\)", r", \1 megabytes", text, flags=re.IGNORECASE)
        text = re.sub(r"\(\s*(\d+(?:\.\d+)?)\s*GB\s*\)", r", \1 gigabytes", text, flags=re.IGNORECASE)
        return text

    def _normalize_technical_acronyms(self, text: str) -> str:
        for pattern, replacement in self._tech_acronyms.items():
            text = re.sub(pattern, replacement, text)
        return text

    def _normalize_abbreviations(self, text: str) -> str:
        for pattern, replacement in self._spoken_abbreviations.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text

    def _disambiguate_mixed_language(self, text: str, language_hint: Optional[str] = None) -> str:
        """
        Disambiguate Hindi / Hinglish grammatical particles vs English abbreviations.
        Rule: 'ko' in Hindi/Hinglish (e.g. 'file ko', 'user ko', 'isko') is the accusative postposition,
        pronounced as 'koh'. In English boxing/gaming slang, 'K.O.' is 'knockout'.
        Never blindly replace 'ko' with 'okay'.
        """
        # 1. English combat/boxing: 'K.O.' or "knockout / KO'd"
        text = re.sub(r"\bK\.O\.?\b", "K-O", text)
        text = re.sub(r"\bKO['’]?d\b", "K-O'd", text, flags=re.IGNORECASE)

        # 2. Hindi postposition: check if 'ko' is preceded by common nouns/pronouns or in Hinglish context
        hindi_markers = {
            "hai", "ho", "karo", "karna", "dekh", "dekho", "batao", "bhai",
            "yaar", "kya", "sab", "scene", "jugaad", "kaise", "nahi", "mat",
            "wala", "wali", "wale", "kuch", "apna", "apne", "mera", "meri"
        }
        words = set(re.findall(r"\b[a-z]+\b", text.lower()))
        is_hinglish_context = bool(words & hindi_markers) or (language_hint in ("hinglish", "hindi"))

        if is_hinglish_context:
            # Map Hindi 'ko' phonetically to 'koh' so English TTS engines pronounce it smoothly as [koʊ]
            # rather than stumbling or misinterpreting it as 'K.O.'
            text = re.sub(r"\bko\b", "koh", text, flags=re.IGNORECASE)
        else:
            # Standalone English check: if preceded by a word like 'file', 'app', 'user' followed by a hindi verb
            text = re.sub(r"\b(\w+)\s+ko\s+(karo|dekho|batao|chalao|rakho|bhejo)\b", r"\1 koh \2", text, flags=re.IGNORECASE)

        return text

    def _finalize_spacing_and_emojis(self, text: str) -> str:
        # Strip emojis cleanly
        text = _EMOJI_PATTERN.sub("", text)
        # Strip any dangling brackets
        text = re.sub(r'[\[\]{}()^@$%&+=~"]', " ", text)
        # Normalize punctuation spacing
        text = re.sub(r"\s+([,.:;?!])", r"\1", text)
        text = re.sub(r",\s*,+", ",", text)
        text = _MULTI_SPACE_PATTERN.sub(" ", text)

        # Multi-item list summarization for speech (if more than 4 items)
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if len(lines) > 4:
            summary_intro = lines[0]
            sample_items = [l.lstrip(",. ") for l in lines[1:4]]
            text = f"{summary_intro}: {', '.join(sample_items)}, and more shown on your screen."

        return text.strip()
