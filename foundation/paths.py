"""
Centralized, Environment-Agnostic Path Resolution for HELIX.

Enforces deterministic precedence rules across environment variables,
explicit configuration files, legacy discovery, and secure per-user defaults
under `%USERPROFILE%\\.helix`.
"""
from __future__ import annotations

import getpass
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("helix.foundation.paths")


def get_user_home() -> Path:
    """
    Safely resolves the user home directory, robust even when USERPROFILE or HOME
    is temporarily stripped in restricted test/service environments.
    """
    for var in ("USERPROFILE", "HOME"):
        val = os.getenv(var)
        if val and val.strip():
            return Path(val.strip())
    try:
        return Path.home()
    except (RuntimeError, KeyError):
        pass
    return Path(r"C:\Users\Default") if sys.platform == "win32" else Path("/tmp")


def expand_path(raw_path: str | Path | None) -> Path | None:
    """
    Expands environment variables (e.g., %USERPROFILE%, $HOME) and user home (~)
    in a given path string or Path object, returning a resolved Path.
    """
    if raw_path is None:
        return None
    raw_str = str(raw_path)
    # Manual fallback expansion for %USERPROFILE% if not in env
    if "%USERPROFILE%" in raw_str.upper() and "USERPROFILE" not in os.environ:
        import re
        home_str = str(get_user_home())
        raw_str = re.sub(r"(?i)%userprofile%", lambda _: home_str, raw_str)

    expanded = os.path.expandvars(raw_str)
    try:
        expanded = os.path.expanduser(expanded)
    except Exception:
        pass
    return Path(expanded)


def get_helix_home() -> Path:
    """
    Returns the root directory for HELIX per-user files.
    Precedence:
    1. HELIX_HOME environment variable
    2. %USERPROFILE%\\.helix (Windows) or ~/.helix (POSIX)
    """
    env_home = os.getenv("HELIX_HOME")
    if env_home and env_home.strip():
        expanded = expand_path(env_home.strip())
        if expanded:
            return expanded
    return get_user_home() / ".helix"


def get_runtime_dir() -> Path:
    """
    Returns the runtime data directory.
    Precedence:
    1. HELIX_RUNTIME_PATH environment variable
    2. get_helix_home() (default: ~/.helix)
    """
    env_runtime = os.getenv("HELIX_RUNTIME_PATH")
    if env_runtime and env_runtime.strip():
        expanded = expand_path(env_runtime.strip())
        if expanded:
            return expanded
    return get_helix_home()


def get_cold_dir() -> Path:
    """
    Returns the cold data directory.
    Precedence:
    1. HELIX_COLD_PATH environment variable
    2. get_helix_home() / "cold"
    """
    env_cold = os.getenv("HELIX_COLD_PATH")
    if env_cold and env_cold.strip():
        expanded = expand_path(env_cold.strip())
        if expanded:
            return expanded
    return get_helix_home() / "cold"


def get_log_dir() -> Path:
    """
    Returns the log directory.
    Precedence:
    1. HELIX_LOG_PATH environment variable
    2. get_helix_home() / "logs" (default: %USERPROFILE%\\.helix\\logs)
    """
    env_log = os.getenv("HELIX_LOG_PATH")
    if env_log and env_log.strip():
        expanded = expand_path(env_log.strip())
        if expanded:
            return expanded
    return get_helix_home() / "logs"


def get_config_dir() -> Path:
    """
    Returns the directory containing HELIX configuration files.
    Precedence:
    1. HELIX_CONFIG_PATH environment variable
    2. Project repository 'config/' directory if present
    3. get_helix_home()
    """
    env_config = os.getenv("HELIX_CONFIG_PATH")
    if env_config and env_config.strip():
        expanded = expand_path(env_config.strip())
        if expanded:
            return expanded

    project_config = Path(__file__).resolve().parents[1] / "config"
    if project_config.is_dir():
        return project_config

    return get_helix_home()


def _read_config_json() -> dict[str, Any]:
    """Safely loads config.json from get_config_dir() if it exists."""
    cfg_file = get_config_dir() / "config.json"
    if cfg_file.is_file():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.debug("Could not read config from %s: %s", cfg_file, e)
    return {}


def get_models_dir() -> Path:
    """
    Returns the base directory for local models.
    Precedence:
    1. HELIX_MODELS_PATH environment variable
    2. Explicit configuration in config.json ('local_models.models_dir' if non-default)
    3. Legacy existing directory fallback (preserves existing installations on ~/Documents/HELIX_MODELS or F:/helix/models)
    4. Default per-user directory: %USERPROFILE%\\.helix\\models
    """
    # 1. Environment variable override
    env_models = os.getenv("HELIX_MODELS_PATH")
    if env_models and env_models.strip():
        expanded = expand_path(env_models.strip())
        if expanded:
            return expanded

    # 2. Explicit custom configuration in config.json
    cfg = _read_config_json()
    local_models_cfg = cfg.get("local_models", {})
    cfg_models_dir = local_models_cfg.get("models_dir")
    default_target = get_helix_home() / "models"
    if cfg_models_dir and isinstance(cfg_models_dir, str) and cfg_models_dir.strip():
        expanded = expand_path(cfg_models_dir.strip())
        # If explicitly pointing to a custom directory (not just the default template)
        if expanded and expanded != default_target and "%userprofile%" not in cfg_models_dir.lower():
            return expanded

    # 3. Legacy discovery fallback: check if existing installation exists
    legacy_candidates = [
        get_user_home() / "Documents" / "HELIX_MODELS",
        get_user_home() / "OneDrive" / "Documents" / "HELIX_MODELS",
        Path("F:/helix/models"),
        Path("C:/HELIX_MODELS"),
    ]
    for cand in legacy_candidates:
        try:
            if cand.is_dir():
                logger.info("Discovered existing models directory at %s", cand)
                return cand
        except OSError:
            pass

    # 4. Default per-user directory
    return default_target


def get_model_path(model_type: str, filename: str | None = None) -> Path:
    """
    Resolves the path to a specific model.
    model_type: "orchestrator", "coder", "llm", "embedding", "tts"
    """
    base_dir = get_models_dir()

    # Model specific environment variables
    env_mapping = {
        "orchestrator": ("HELIX_LLM_ORCHESTRATOR_MODEL_PATH", "Qwen3-4B-Q8_0.gguf", "LLM"),
        "coder": ("HELIX_LLM_CODER_MODEL_PATH", "qwen2.5-coder-3b-instruct-q8_0.gguf", "LLM"),
        "llm": ("HELIX_LLM_MODEL_PATH", "qwen2.5-coder-3b-instruct-q8_0.gguf", "LLM"),
        "embedding": ("HELIX_EMBEDDING_MODEL_PATH", "LFM2.5-Embedding-350M-Q8_0.gguf", "Embeddings"),
        "tts": ("HELIX_VOICE_TTS_MODEL_PATH", "en_US-lessac-medium.onnx", "PIPER"),
    }

    env_var, default_name, subdir = env_mapping.get(
        model_type.lower(),
        (f"HELIX_{model_type.upper()}_MODEL_PATH", filename or f"{model_type}.bin", ""),
    )

    # 1. Environment variable
    env_val = os.getenv(env_var)
    if env_val and env_val.strip():
        expanded = expand_path(env_val.strip())
        if expanded:
            return expanded

    # 2. Config file lookup
    cfg = _read_config_json()
    local_models_cfg = cfg.get("local_models", {})
    cfg_key_map = {
        "orchestrator": "orchestrator_model",
        "coder": "coder_model",
        "llm": "llm_model",
        "embedding": "embedding_model",
        "tts": "tts_model",
    }
    cfg_key = cfg_key_map.get(model_type.lower())
    if cfg_key and cfg_key in local_models_cfg:
        val = local_models_cfg[cfg_key]
        if val and isinstance(val, str) and val.strip():
            expanded = expand_path(val.strip())
            if expanded:
                if not expanded.is_absolute():
                    expanded = base_dir / expanded
                if expanded.is_file():
                    return expanded

    # 3. Search in resolved models_dir
    target_name = filename or default_name
    candidates = [
        base_dir / subdir / target_name,
        base_dir / target_name,
    ]

    # Also search legacy candidates
    for legacy_base in [
        get_user_home() / "Documents" / "HELIX_MODELS",
        get_user_home() / "OneDrive" / "Documents" / "HELIX_MODELS",
        Path("F:/helix/models"),
    ]:
        candidates.extend([
            legacy_base / subdir / target_name,
            legacy_base / target_name,
        ])

    for cand in candidates:
        try:
            if cand.is_file():
                return cand
        except OSError:
            pass

    # Default fallback path in base_dir
    return base_dir / subdir / target_name


def get_orchestrator_model_path() -> Path:
    return get_model_path("orchestrator")


def get_coder_model_path() -> Path:
    return get_model_path("coder")


def get_voice_model_path() -> Path:
    return get_model_path("tts")


def get_qdrant_path() -> Path:
    """
    Returns path for the local Qdrant vector database.
    Precedence:
    1. HELIX_QDRANT_PATH environment variable
    2. get_runtime_dir() / "memory" / "qdrant"
    """
    env_qdrant = os.getenv("HELIX_QDRANT_PATH")
    if env_qdrant and env_qdrant.strip():
        expanded = expand_path(env_qdrant.strip())
        if expanded:
            return expanded
    return get_runtime_dir() / "memory" / "qdrant"


def get_system_username() -> str:
    """
    Resolves the current OS username safely across Windows, POSIX, headless,
    service, and scheduled-task environments. Never crashes and never returns
    a hardcoded developer identity.
    NOTE: Never treat the returned username as proof of authentication.
    """
    for var in ("USERNAME", "USER", "LOGNAME"):
        val = os.getenv(var)
        if val and val.strip():
            return val.strip()

    try:
        val = getpass.getuser()
        if val and val.strip():
            return val.strip()
    except Exception:
        pass

    return "helix_user"


def get_system_display_name() -> str:
    """
    Resolves a friendly display name for unauthenticated presentation.
    """
    explicit = os.getenv("HELIX_USER_DISPLAY_NAME") or os.getenv("HELIX_USER_NAME")
    if explicit and explicit.strip():
        return explicit.strip()

    uname = get_system_username()
    if uname.lower() in ("helix_user", "default_owner", "root", "system"):
        return "Helix User"
    return uname.capitalize()


def get_discovered_app_roots() -> dict[str, Path]:
    """
    Resolves standard application installation directories dynamically
    based on host environment variables and OS defaults.
    """
    local_app_data_env = os.getenv("LOCALAPPDATA")
    if local_app_data_env and local_app_data_env.strip():
        local_app_data = Path(os.path.expandvars(os.path.expanduser(local_app_data_env.strip())))
    else:
        local_app_data = get_user_home() / "AppData" / "Local"

    program_files_env = os.getenv("ProgramFiles")
    if program_files_env and program_files_env.strip():
        program_files = Path(os.path.expandvars(os.path.expanduser(program_files_env.strip())))
    else:
        program_files = Path(r"C:\Program Files")

    program_files_x86_env = os.getenv("ProgramFiles(x86)")
    if program_files_x86_env and program_files_x86_env.strip():
        program_files_x86 = Path(os.path.expandvars(os.path.expanduser(program_files_x86_env.strip())))
    else:
        program_files_x86 = Path(r"C:\Program Files (x86)")

    system_root_env = os.getenv("SystemRoot") or os.getenv("WINDIR")
    if system_root_env and system_root_env.strip():
        system_root = Path(os.path.expandvars(os.path.expanduser(system_root_env.strip())))
    else:
        system_root = Path(r"C:\Windows")

    return {
        "local_app_data": local_app_data,
        "local_programs": local_app_data / "Programs",
        "windows_apps": local_app_data / "Microsoft" / "WindowsApps",
        "program_files": program_files,
        "program_files_x86": program_files_x86,
        "system_root": system_root,
        "system32": system_root / "System32",
    }
