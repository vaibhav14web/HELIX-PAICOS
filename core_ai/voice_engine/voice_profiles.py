"""
core_ai/voice_engine/voice_profiles.py

Validated Voice Profile Configurations and Registry for HELIX.
Supports genuinely distinct vocal identities, prosody parameters,
locale assignments, and speech preview configurations.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("helix.voice_profiles")


class VoiceProfile(BaseModel):
    model_config = {"protected_namespaces": ()}

    id: str
    name: str
    desc: str
    locale: str
    gender: str
    speed: float = 1.0
    pitch: float = 1.0
    model_file: str = "en_US-lessac-medium.onnx"
    style: str = "professional"
    preview_text: str
    pronunciation_profile: dict[str, str] = Field(default_factory=dict)
    fallback_voice_names: list[str] = Field(default_factory=list)
    version: str = "1.0.0"
    is_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


import threading


class VoiceProfileRegistry:
    """
    Central registry for HELIX vocal personalities.
    Enforces strict single-active-personality invariant, asset detection,
    session isolation, and thread-safe atomic switching.
    """

    DEFAULT_PROFILE_ID = "aria"

    def __init__(self, models_dir: Optional[Path] = None, config_path: Optional[str] = None):
        self._lock = threading.RLock()
        self._models_dir = models_dir
        self._config_path = Path(config_path) if config_path else None
        self._profiles: dict[str, VoiceProfile] = {}
        self._session_profiles: dict[str, str] = {}
        self._active_personality_id = self.DEFAULT_PROFILE_ID
        self._load_default_profiles()
        self._load_persisted_profile()

    @property
    def active_personality_id(self) -> str:
        """The single authoritative active personality identifier."""
        with self._lock:
            return self._active_personality_id

    @property
    def active_profile_id(self) -> str:
        """Alias for active_personality_id for backward compatibility."""
        return self.active_personality_id

    def _load_default_profiles(self) -> None:
        # 1. ARIA - Professional, precise, standard English
        aria = VoiceProfile(
            id="aria",
            name="ARIA",
            desc="Professional · English",
            locale="en-US",
            gender="female",
            speed=1.0,
            pitch=1.0,
            model_file="en_US-lessac-medium.onnx",
            style="professional",
            preview_text="Hello, I am ARIA. I provide clear, professional, and precise assistance for all your tasks.",
            pronunciation_profile={
                "HELIX": "Heelix",
            },
            fallback_voice_names=["Samantha", "Microsoft Zira", "Google US English", "en-US-Standard"],
            is_default=True,
        )

        # 2. NOVA - Warm, conversational British English
        nova = VoiceProfile(
            id="nova",
            name="NOVA",
            desc="Warm · English UK",
            locale="en-GB",
            gender="female",
            speed=0.95,
            pitch=1.05,
            model_file="en_GB-alba-medium.onnx",  # Verified distinct British female Piper model
            style="warm",
            preview_text="Hi there! I'm NOVA. Let's collaborate smoothly with a warm, conversational rhythm.",
            pronunciation_profile={
                "HELIX": "Heelix",
            },
            fallback_voice_names=["Victoria", "Microsoft Hazel", "Google UK English Female", "en-GB-Standard"],
        )

        # 3. ORION - Deep, authoritative, calm American English
        orion = VoiceProfile(
            id="orion",
            name="ORION",
            desc="Deep · English US",
            locale="en-US",
            gender="male",
            speed=0.90,
            pitch=0.85,
            model_file="en_US-ryan-medium.onnx",  # Verified distinct American baritone male Piper model
            style="deep",
            preview_text="Greetings. I am ORION. Operating systems, engineering tasks, and complex pipelines are ready for execution.",
            pronunciation_profile={
                "HELIX": "Heelix",
            },
            fallback_voice_names=["Alex", "Microsoft David", "Google US English Male", "en-US-Wavenet-D"],
        )

        self._profiles = {
            aria.id: aria,
            nova.id: nova,
            orion.id: orion,
        }

    def _get_config_path(self) -> Optional[Path]:
        if self._config_path:
            return self._config_path
        try:
            from foundation.paths import get_data_dir
            return get_data_dir() / "config.json"
        except Exception:
            return None

    def _load_persisted_profile(self) -> None:
        cfg_file = self._get_config_path()
        if cfg_file and cfg_file.is_file():
            try:
                import json
                data = json.loads(cfg_file.read_text(encoding="utf-8"))
                saved = (
                    data.get("voice_profile")
                    or data.get("active_voice_profile")
                    or (data.get("ui_settings", {}).get("voice_profile") if isinstance(data.get("ui_settings"), dict) else None)
                )
                if saved and isinstance(saved, str) and saved.lower().strip() in self._profiles:
                    self._active_personality_id = saved.lower().strip()
                else:
                    self._active_personality_id = self.DEFAULT_PROFILE_ID
            except Exception as e:
                logger.debug("Could not read persisted voice profile: %s; using default '%s'", e, self.DEFAULT_PROFILE_ID)
                self._active_personality_id = self.DEFAULT_PROFILE_ID
        else:
            self._active_personality_id = self.DEFAULT_PROFILE_ID

    def _persist_profile(self, profile_id: str) -> None:
        cfg_file = self._get_config_path()
        if not cfg_file:
            return
        try:
            import json
            data = {}
            if cfg_file.is_file():
                try:
                    data = json.loads(cfg_file.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data["voice_profile"] = profile_id
            if "ui_settings" not in data or not isinstance(data["ui_settings"], dict):
                data["ui_settings"] = {}
            data["ui_settings"]["voice_profile"] = profile_id
            cfg_file.parent.mkdir(parents=True, exist_ok=True)
            cfg_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to persist voice profile '%s': %s", profile_id, e)

    def list_profiles(self, session_id: Optional[str] = None) -> list[dict[str, Any]]:
        with self._lock:
            effective_active = self.get_effective_profile_id(session_id)
            result = []
            for p in self._profiles.values():
                d = p.to_dict()
                asset_info = self.check_asset_availability(p)
                is_active = (p.id == effective_active)
                d["is_active"] = is_active
                d["active"] = is_active
                d["asset_available"] = asset_info["installed"]
                d["asset_installed"] = asset_info["installed"]
                result.append(d)

            # Strict Single-Active Invariant enforcement
            active_count = sum(1 for p in result if p["is_active"])
            if active_count != 1:
                logger.error(
                    "Single-active personality invariant violated (active_count=%d). Correcting to default '%s'.",
                    active_count,
                    self.DEFAULT_PROFILE_ID,
                )
                self._active_personality_id = self.DEFAULT_PROFILE_ID
                for p in result:
                    p["is_active"] = (p["id"] == self.DEFAULT_PROFILE_ID)
                    p["active"] = p["is_active"]

            return result

    def get_effective_profile_id(self, session_id: Optional[str] = None) -> str:
        with self._lock:
            if session_id and session_id != "default" and session_id in self._session_profiles:
                target = self._session_profiles[session_id]
                if target in self._profiles:
                    return target
            return self._active_personality_id

    def get_profile(self, profile_id: Optional[str] = None, session_id: Optional[str] = None) -> VoiceProfile:
        with self._lock:
            effective_id = profile_id or self.get_effective_profile_id(session_id)
            clean_id = effective_id.strip().lower() if effective_id else self.DEFAULT_PROFILE_ID
            if clean_id in self._profiles:
                return self._profiles[clean_id]
            logger.warning(
                "Unknown voice profile '%s' requested. Safely falling back to default '%s'.",
                profile_id,
                self.DEFAULT_PROFILE_ID,
            )
            return self._profiles[self.DEFAULT_PROFILE_ID]

    def set_active_profile(
        self,
        profile_id: str,
        session_id: Optional[str] = None,
        persist: bool = True,
    ) -> VoiceProfile:
        """
        Atomically switches active personality to profile_id.
        Deactivates previous profile and enforces strict single-active invariant.
        Raises ValueError if profile_id is invalid.
        """
        clean_id = (profile_id or "").strip().lower()
        with self._lock:
            if clean_id not in self._profiles:
                raise ValueError(
                    f"Invalid voice personality ID: '{profile_id}'. Available personalities: {list(self._profiles.keys())}"
                )

            if session_id and session_id != "default":
                self._session_profiles[session_id] = clean_id
            else:
                self._active_personality_id = clean_id
                self._session_profiles["default"] = clean_id
                if persist:
                    self._persist_profile(clean_id)

            logger.info("Voice personality atomically activated: '%s' (session=%s)", clean_id, session_id or "default")
            return self._profiles[clean_id]

    def is_valid_profile_id(self, profile_id: str) -> bool:
        if not profile_id or not isinstance(profile_id, str):
            return False
        with self._lock:
            return profile_id.strip().lower() in self._profiles

    def check_asset_availability(self, profile: VoiceProfile) -> dict[str, Any]:
        """Verify whether the configured onnx model file exists on disk."""
        from foundation.paths import get_models_dir
        base = self._models_dir or (get_models_dir() / "PIPER")
        target = base / profile.model_file
        is_installed = target.is_file()
        return {
            "profile_id": profile.id,
            "model_file": profile.model_file,
            "installed": is_installed,
            "path": str(target),
        }


DEFAULT_PROFILES = list(VoiceProfileRegistry()._profiles.values())
