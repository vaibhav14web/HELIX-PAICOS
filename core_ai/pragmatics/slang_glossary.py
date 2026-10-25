"""
core_ai/pragmatics/slang_glossary.py

Curated, versioned, offline glossary of slang, catchphrases, internet expressions,
abbreviations, ASR variants, and Indian English / Hinglish idioms.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional

from core_ai.pragmatics.models import (
    RegisterTone,
    SlangContextRule,
    SlangEntry,
    SpeakerIntent,
)

logger = logging.getLogger("helix.pragmatics.glossary")


class SlangGlossary:
    """
    Offline, thread-safe, bounded, versioned slang glossary.
    Can load from an external JSON file or use comprehensive default built-in entries.
    """

    DEFAULT_VERSION = "1.0.0"

    def __init__(self, glossary_path: Optional[str | Path] = None):
        self._version = self.DEFAULT_VERSION
        self._entries: dict[str, SlangEntry] = {}
        self._alias_map: dict[str, str] = {}  # alias/normalized_variant -> canonical expression
        self._asr_variant_map: dict[str, str] = {}  # asr error variant -> canonical expression
        self._compiled_regexes: list[tuple[re.Pattern, SlangEntry]] = []

        if glossary_path and Path(glossary_path).exists():
            self.load_from_file(glossary_path)
        else:
            self._load_default_entries()

    @property
    def version(self) -> str:
        return self._version

    @property
    def entries(self) -> dict[str, SlangEntry]:
        return self._entries

    def get_entry(self, expression: str) -> Optional[SlangEntry]:
        key = expression.lower().strip()
        canonical = self._alias_map.get(key, key)
        return self._entries.get(canonical)

    def add_entry(self, entry: SlangEntry) -> None:
        expr_key = entry.expression.lower().strip()
        self._entries[expr_key] = entry
        self._alias_map[expr_key] = expr_key

        for v in entry.normalized_variants:
            self._alias_map[v.lower().strip()] = expr_key

        for a in entry.aliases:
            self._alias_map[a.lower().strip()] = expr_key

        for asr in entry.asr_phonetic_variants:
            self._asr_variant_map[asr.lower().strip()] = expr_key

        self._recompile_patterns()

    def normalize_asr_transcript(self, text: str) -> str:
        """
        Replaces common speech-recognition phonetic errors with canonical slang terms.
        E.g., "no cup" -> "no cap", "touch glass" -> "touch grass", "bye kya scene hai" -> "bhai kya scene hai".
        """
        if not text:
            return ""

        result = text
        # Match longest phonetic variants first
        for asr_variant, canonical in sorted(self._asr_variant_map.items(), key=lambda x: len(x[0]), reverse=True):
            pattern = re.compile(rf"\b{re.escape(asr_variant)}\b", re.IGNORECASE)
            result = pattern.sub(canonical, result)

        return result

    def _recompile_patterns(self) -> None:
        """Compiles regex patterns for all registered entries for fast matching."""
        compiled = []
        # Sort entries so multi-word expressions match before single words
        sorted_entries = sorted(self._entries.values(), key=lambda e: len(e.expression), reverse=True)

        for entry in sorted_entries:
            all_patterns = [entry.expression] + entry.normalized_variants + entry.aliases
            # Deduplicate
            unique_patterns = list(dict.fromkeys(p.lower().strip() for p in all_patterns if p.strip()))
            for pat in unique_patterns:
                # Use word boundaries; handle punctuation like "ain't" or "it's" cleanly
                escaped = re.escape(pat)
                # Allow apostrophe flexibility
                escaped = escaped.replace(r"\'", r"['’]?")
                regex = re.compile(rf"(?:\b|^){escaped}(?:\b|$)", re.IGNORECASE)
                compiled.append((regex, entry))

        self._compiled_regexes = compiled

    def find_matches(self, text: str) -> list[tuple[SlangEntry, str, int, int]]:
        """
        Finds all slang matches in text.
        Returns list of (SlangEntry, matched_text, start_index, end_index).
        """
        matches = []
        occupied_spans: list[tuple[int, int]] = []

        for regex, entry in self._compiled_regexes:
            for match in regex.finditer(text):
                start, end = match.span()
                # Check for overlap with existing longer match
                if any(start < o_end and end > o_start for o_start, o_end in occupied_spans):
                    continue

                occupied_spans.append((start, end))
                matches.append((entry, match.group(0), start, end))

        # Sort matches by position in text
        matches.sort(key=lambda m: m[2])
        return matches

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self._version,
                "entries": [e.to_dict() for e in self._entries.values()],
            },
            indent=2,
            ensure_ascii=False,
        )

    def save_to_file(self, file_path: str | Path) -> None:
        path = Path(file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json())

    def load_from_file(self, file_path: str | Path) -> None:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._version = data.get("version", self.DEFAULT_VERSION)
        self._entries.clear()
        self._alias_map.clear()
        self._asr_variant_map.clear()
        for edata in data.get("entries", []):
            self.add_entry(SlangEntry.from_dict(edata))

    def _load_default_entries(self) -> None:
        """Loads canonical, curated baseline glossary entries."""
        defaults = [
            # ─────────────────────────────────────────────────────────────
            # 1. Gen Z Slang
            # ─────────────────────────────────────────────────────────────
            SlangEntry(
                expression="fire",
                normalized_variants=["that's fire", "thats fire", "is fire", "it's fire", "its fire"],
                primary_meaning="impressive, extremely good, or high quality",
                examples=["That project is fire", "The new UI is fire"],
                language_region="gen_z",
                register_tone=RegisterTone.HYPERBOLIC,
                ambiguity_notes="Literal fire vs metaphorical praise; determined by surrounding subject.",
            ),
            SlangEntry(
                expression="mid",
                normalized_variants=["pretty mid", "kind of mid", "super mid"],
                primary_meaning="mediocre, average, underwhelming, or unimpressive",
                examples=["That movie was mid", "The performance was mid"],
                language_region="gen_z",
                register_tone=RegisterTone.INFORMAL,
                ambiguity_notes="Mild criticism or indifference.",
            ),
            SlangEntry(
                expression="no cap",
                normalized_variants=["no-cap", "nocap"],
                primary_meaning="sincerely, genuinely, no lie, for real",
                examples=["No cap, I'm serious", "That was tough, no cap"],
                language_region="gen_z",
                register_tone=RegisterTone.INFORMAL,
                aliases=["fr fr"],
                asr_phonetic_variants=["no cup", "no cat", "know cap", "no carp"],
            ),
            SlangEntry(
                expression="lowkey",
                normalized_variants=["low-key", "low key"],
                primary_meaning="subtly, secretly, somewhat, or without drawing attention",
                examples=["I lowkey want to stay home", "That is lowkey brilliant"],
                language_region="gen_z",
                register_tone=RegisterTone.CASUAL,
                asr_phonetic_variants=["loki", "low key"],
            ),
            SlangEntry(
                expression="highkey",
                normalized_variants=["high-key", "high key"],
                primary_meaning="openly, intensely, unmistakably, or very much",
                examples=["I highkey love this feature"],
                language_region="gen_z",
                register_tone=RegisterTone.CASUAL,
            ),
            SlangEntry(
                expression="cooked",
                normalized_variants=["i'm cooked", "im cooked", "we're cooked", "were cooked", "you're cooked", "youre cooked", "is cooked"],
                primary_meaning="in severe trouble, doomed, or exhausted",
                contextual_rules=[
                    SlangContextRule(
                        trigger_words=["exam", "test", "quiz", "interview", "deadline", "audit", "tomorrow", "boss", "failing", "screwed"],
                        meaning="in trouble, unprepared, or likely to fail",
                        sentiment="negative",
                        intent=SpeakerIntent.EXPRESS_UNPREPAREDNESS,
                    ),
                    SlangContextRule(
                        trigger_words=["sleep", "slept", "tired", "awake", "hours", "night", "exhausted", "energy"],
                        meaning="extremely exhausted, worn out, or lacking energy",
                        sentiment="negative",
                        intent=SpeakerIntent.EXPRESS_EXHAUSTION,
                    ),
                    SlangContextRule(
                        trigger_words=["build", "pipeline", "server", "system", "code", "bug", "error", "crash"],
                        meaning="failed, broken, or suffering an outage",
                        sentiment="negative",
                        intent=SpeakerIntent.EXPRESS_FRUSTRATION,
                    ),
                ],
                examples=["I'm cooked for tomorrow's exam", "I'm cooked; I haven't slept"],
                language_region="gen_z",
                register_tone=RegisterTone.HYPERBOLIC,
                ambiguity_notes="Differentiate between academic/job trouble vs physical exhaustion vs broken system.",
            ),
            SlangEntry(
                expression="let him cook",
                normalized_variants=["let her cook", "let them cook", "let me cook", "hold on let him cook"],
                primary_meaning="give someone space and time to continue what they are doing / let someone execute their plan",
                examples=["Bro, let him cook", "Hold up, let them cook"],
                language_region="internet_culture",
                register_tone=RegisterTone.INFORMAL,
                ambiguity_notes="Metaphorical permission to proceed; not a culinary action.",
            ),
            SlangEntry(
                expression="ate",
                normalized_variants=["ate that", "ate and left no crumbs", "she ate", "he ate", "they ate"],
                primary_meaning="performed exceptionally well or flawlessly executed",
                examples=["They ate that presentation", "She totally ate"],
                language_region="gen_z",
                register_tone=RegisterTone.HYPERBOLIC,
            ),
            SlangEntry(
                expression="slay",
                normalized_variants=["slayed", "slaying"],
                primary_meaning="succeeded brilliantly or did something impressive",
                examples=["You slay", "That design slays"],
                language_region="gen_z",
                register_tone=RegisterTone.HYPERBOLIC,
                asr_phonetic_variants=["sleigh"],
            ),
            SlangEntry(
                expression="bet",
                normalized_variants=["aight bet", "alright bet", "say bet"],
                primary_meaning="affirmation, agreement, confirmation, or accepting a challenge ('you got it' / 'deal')",
                examples=["Want to grab food? Bet.", "Bet, I'll see you there."],
                language_region="gen_z",
                register_tone=RegisterTone.CASUAL,
            ),
            SlangEntry(
                expression="sus",
                normalized_variants=["suss", "suspicious", "kinda sus"],
                primary_meaning="suspicious, shady, or untrustworthy",
                examples=["That link looks sus", "Why is he being so sus?"],
                language_region="internet_culture",
                register_tone=RegisterTone.CASUAL,
                asr_phonetic_variants=["s us", "suss"],
            ),
            SlangEntry(
                expression="rizz",
                normalized_variants=["unspoken rizz", "rizzler"],
                primary_meaning="charisma, charm, or ability to appeal to others",
                examples=["He has crazy rizz"],
                language_region="gen_z",
                register_tone=RegisterTone.CASUAL,
            ),

            # ─────────────────────────────────────────────────────────────
            # 2. Internet Expressions
            # ─────────────────────────────────────────────────────────────
            SlangEntry(
                expression="touch grass",
                normalized_variants=["go touch grass", "needs to touch grass"],
                primary_meaning="disconnect from the internet, go outside, and reconnect with reality",
                examples=["Bro needs to touch grass"],
                language_region="internet_culture",
                register_tone=RegisterTone.SARCASTIC,
                asr_phonetic_variants=["touch glass"],
            ),
            SlangEntry(
                expression="skill issue",
                normalized_variants=["sounds like a skill issue", "pure skill issue"],
                primary_meaning="playfully attributing a failure or difficulty to the person's own lack of proficiency",
                examples=["Game crashed? Skill issue.", "Sounds like a skill issue"],
                language_region="internet_culture",
                register_tone=RegisterTone.SARCASTIC,
            ),
            SlangEntry(
                expression="rent-free",
                normalized_variants=["rent free", "living rent-free", "living rent free"],
                primary_meaning="occupying someone's thoughts persistently without their conscious control",
                examples=["That comment lives rent-free in my head"],
                language_region="internet_culture",
                register_tone=RegisterTone.INFORMAL,
            ),
            SlangEntry(
                expression="main character energy",
                normalized_variants=["main character", "acting like the main character"],
                primary_meaning="behaving as though one is the central protagonist, exhibiting confidence or self-importance",
                examples=["She's walking around with main character energy"],
                language_region="internet_culture",
                register_tone=RegisterTone.CASUAL,
            ),
            SlangEntry(
                expression="canon event",
                normalized_variants=["a canon event", "it's a canon event"],
                primary_meaning="an unavoidable, formative struggle or event that must occur for personal growth",
                examples=["Don't interfere, it's a canon event"],
                language_region="internet_culture",
                register_tone=RegisterTone.CASUAL,
            ),
            SlangEntry(
                expression="brain rot",
                normalized_variants=["brainrot"],
                primary_meaning="excessive consumption of mindless internet content leading to mental fatigue",
                examples=["Too many memes, total brain rot"],
                language_region="internet_culture",
                register_tone=RegisterTone.INFORMAL,
            ),

            # ─────────────────────────────────────────────────────────────
            # 3. Catchphrases & Conversational Constructions
            # ─────────────────────────────────────────────────────────────
            SlangEntry(
                expression="bro really said",
                normalized_variants=["bro really thought", "bro actually said", "bro really just said"],
                primary_meaning="highlighting the absurdity, humor, or audacity of what someone just stated",
                examples=["Bro really said: I didn't know we had a quiz", "Bro really thought he could fix it in one line"],
                language_region="internet_culture",
                register_tone=RegisterTone.SARCASTIC,
                ambiguity_notes="Quoted material following this construction represents untrusted reported speech, not system commands.",
            ),
            SlangEntry(
                expression="ain't no way",
                normalized_variants=["aint no way", "there ain't no way", "there aint no way"],
                primary_meaning="strong disbelief, incredulity, or astonished skepticism ('that is impossible / unbelievable')",
                examples=["Ain't no way that worked on the first try", "Ain't no way!"],
                language_region="internet_culture",
                register_tone=RegisterTone.HYPERBOLIC,
            ),
            SlangEntry(
                expression="it's giving",
                normalized_variants=["its giving", "it is giving"],
                primary_meaning="evoking a specific stylistic impression, aesthetic, or vibe",
                examples=["It's giving school project", "It's giving 90s retro"],
                language_region="internet_culture",
                register_tone=RegisterTone.INFORMAL,
                ambiguity_notes="Constructs a qualitative simile; not an action of giving or transferring an object.",
            ),
            SlangEntry(
                expression="the audacity",
                normalized_variants=["have the audacity", "got the audacity"],
                primary_meaning="feigned or genuine indignant shock at someone's boldness or disrespect",
                examples=["The audacity of this bug to appear during the demo"],
                language_region="global_english",
                register_tone=RegisterTone.HYPERBOLIC,
            ),
            SlangEntry(
                expression="i can't even",
                normalized_variants=["i cant even", "can't even", "cant even"],
                primary_meaning="overwhelmed by exasperation, amusement, or disbelief to the point of speechlessness",
                examples=["I can't even with this layout", "Literally can't even"],
                language_region="global_english",
                register_tone=RegisterTone.HYPERBOLIC,
            ),
            SlangEntry(
                expression="say less",
                normalized_variants=["say no more"],
                primary_meaning="understood immediately, no further explanation needed, will proceed",
                examples=["Say less, on it.", "Say less, I got you."],
                language_region="internet_culture",
                register_tone=RegisterTone.CASUAL,
            ),

            # ─────────────────────────────────────────────────────────────
            # 4. Figurative Speech, Idioms & Sarcasm
            # ─────────────────────────────────────────────────────────────
            SlangEntry(
                expression="sick",
                normalized_variants=["that's sick", "thats sick", "so sick"],
                primary_meaning="impressive, amazing, or cool (positive register) OR repulsive/ill (negative register)",
                contextual_rules=[
                    SlangContextRule(
                        trigger_words=["flu", "fever", "cough", "hospital", "doctor", "throw up", "gross", "disgusting", "terrible", "bad"],
                        meaning="unwell, ill, or repulsive",
                        sentiment="negative",
                        intent=SpeakerIntent.EXPRESS_FRUSTRATION,
                    ),
                    SlangContextRule(
                        trigger_words=["feature", "demo", "trick", "move", "design", "build", "fast", "cool", "fire", "awesome", "great"],
                        meaning="exceptionally cool, impressive, or outstanding",
                        sentiment="positive",
                        intent=SpeakerIntent.COMPLIMENT,
                    ),
                ],
                examples=["That's sick, how did you code that?", "I feel sick today"],
                language_region="global_english",
                register_tone=RegisterTone.INFORMAL,
                ambiguity_notes="Context-sensitive polarity; defaults to positive when referring to artifacts/skills.",
            ),
            SlangEntry(
                expression="killing it",
                normalized_variants=["crushing it", "nailed it"],
                primary_meaning="performing exceptionally well or achieving great success",
                examples=["You're killing it today"],
                language_region="global_english",
                register_tone=RegisterTone.HYPERBOLIC,
            ),
            SlangEntry(
                expression="i'm dead",
                normalized_variants=["im dead", "dead", "i am dead"],
                primary_meaning="finding something overwhelmingly humorous or expressing extreme fatigue",
                contextual_rules=[
                    SlangContextRule(
                        trigger_words=["funny", "meme", "joke", "lol", "lmao", "laughing", "hilarious"],
                        meaning="laughing uncontrollably at humor",
                        sentiment="positive",
                        intent=SpeakerIntent.CONVERSATIONAL,
                    ),
                    SlangContextRule(
                        trigger_words=["workout", "running", "stairs", "tired", "sleep", "walk"],
                        meaning="physically exhausted",
                        sentiment="negative",
                        intent=SpeakerIntent.EXPRESS_EXHAUSTION,
                    ),
                ],
                examples=["That joke, I'm dead", "10 miles later, I'm dead"],
                language_region="global_english",
                register_tone=RegisterTone.HYPERBOLIC,
            ),

            # ─────────────────────────────────────────────────────────────
            # 5. Spoken Abbreviations & ASR Phonetic Aliases
            # ─────────────────────────────────────────────────────────────
            SlangEntry(
                expression="tbh",
                normalized_variants=["to be honest"],
                primary_meaning="to be honest",
                examples=["Tbh I prefer the old version"],
                language_region="global_english",
                register_tone=RegisterTone.CASUAL,
            ),
            SlangEntry(
                expression="ngl",
                normalized_variants=["not gonna lie", "not going to lie"],
                primary_meaning="not gonna lie (candid admission)",
                examples=["Ngl that was surprisingly good"],
                language_region="global_english",
                register_tone=RegisterTone.CASUAL,
            ),
            SlangEntry(
                expression="idk",
                normalized_variants=["i don't know", "i dont know"],
                primary_meaning="I don't know",
                examples=["Idk what happened here"],
                language_region="global_english",
                register_tone=RegisterTone.CASUAL,
            ),
            SlangEntry(
                expression="fr",
                normalized_variants=["for real"],
                primary_meaning="for real / genuinely",
                examples=["Fr, that makes total sense"],
                language_region="global_english",
                register_tone=RegisterTone.CASUAL,
            ),
            SlangEntry(
                expression="rn",
                normalized_variants=["right now"],
                primary_meaning="right now",
                examples=["I am super busy rn"],
                language_region="global_english",
                register_tone=RegisterTone.CASUAL,
            ),
            SlangEntry(
                expression="gg",
                normalized_variants=["good game"],
                primary_meaning="good game / well done / admitting defeat humorously",
                examples=["GG, we solved it", "If this fails, GG"],
                language_region="internet_culture",
                register_tone=RegisterTone.CASUAL,
            ),

            # ─────────────────────────────────────────────────────────────
            # 6. Indian English & Hinglish
            # ─────────────────────────────────────────────────────────────
            SlangEntry(
                expression="bhai kya scene hai",
                normalized_variants=["kya scene hai", "kya scene hai bhai", "kya chal raha hai"],
                primary_meaning="asking what the situation, plan, or update is ('what is the scene / what is happening?')",
                examples=["Bhai, kya scene hai?", "Kal ka kya scene hai?"],
                language_region="hinglish",
                register_tone=RegisterTone.INFORMAL,
                asr_phonetic_variants=["bye kya scene hai", "bye case in hi", "kya seen hai", "bhai kya seen hai"],
            ),
            SlangEntry(
                expression="scene sorted",
                normalized_variants=["scene sorted hai", "sab sorted hai", "sorted hai"],
                primary_meaning="everything is under control, resolved, or properly arranged",
                examples=["Don't worry, scene sorted hai", "Sab sorted hai"],
                language_region="hinglish",
                register_tone=RegisterTone.INFORMAL,
            ),
            SlangEntry(
                expression="jugaad",
                normalized_variants=["jugaad lagao", "jugaad se"],
                primary_meaning="a clever, resourceful, improvised hack or non-conventional workaround",
                examples=["Koi jugaad nikaal", "That's a solid jugaad"],
                language_region="hinglish",
                register_tone=RegisterTone.INFORMAL,
            ),
            SlangEntry(
                expression="mast",
                normalized_variants=["ekdum mast", "bohot mast"],
                primary_meaning="awesome, fantastic, enjoyable, or very good",
                examples=["Code ekdum mast chal raha hai", "Mast feature hai"],
                language_region="hinglish",
                register_tone=RegisterTone.CASUAL,
            ),
            SlangEntry(
                expression="bakwaas",
                normalized_variants=["bakwas", "kya bakwaas hai"],
                primary_meaning="rubbish, nonsense, or very poor quality",
                examples=["Yeh idea bakwaas hai", "Full bakwas"],
                language_region="hinglish",
                register_tone=RegisterTone.INFORMAL,
            ),
            SlangEntry(
                expression="chalta hai",
                normalized_variants=["itna toh chalta hai"],
                primary_meaning="it is acceptable, normal, or tolerable ('it happens' / 'no big deal')",
                examples=["Minor bug hai, chalta hai", "Chalta hai yaar"],
                language_region="hinglish",
                register_tone=RegisterTone.CASUAL,
            ),
            SlangEntry(
                expression="tension mat le",
                normalized_variants=["tension mat lo", "load mat le"],
                primary_meaning="do not worry, stay calm, relax",
                examples=["Tension mat le, ho jayega"],
                language_region="hinglish",
                register_tone=RegisterTone.AFFECTIONATE,
            ),
            SlangEntry(
                expression="yaar",
                normalized_variants=["yar"],
                primary_meaning="friend, pal, colloquial buddy marker (used like 'man' or 'dude')",
                examples=["Suno yaar", "Arre yaar"],
                language_region="hinglish",
                register_tone=RegisterTone.CASUAL,
            ),
            SlangEntry(
                expression="badhiya",
                normalized_variants=["badiya", "ekdum badhiya"],
                primary_meaning="great, fine, well-done, or excellent",
                examples=["Sab badhiya hai", "Ekdum badhiya"],
                language_region="hinglish",
                register_tone=RegisterTone.CASUAL,
            ),
        ]

        for entry in defaults:
            self.add_entry(entry)
