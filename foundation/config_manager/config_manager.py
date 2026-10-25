import os
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


from foundation.paths import get_config_dir


class ConfigManager:
    def __init__(self):
        self._config_path = get_config_dir()
        self._cache: dict[str, Any] = {}
        self._loaded = False

    async def start(self) -> None:
        env_file = self._config_path / ".env"
        if env_file.exists():
            load_dotenv(env_file)

        config_file = self._config_path / "config.json"
        if config_file.exists():
            with open(config_file, encoding="utf-8") as f:
                self._cache.update(json.load(f))

        config_yaml = self._config_path / "config.yaml"
        if config_yaml.exists():
            try:
                import yaml
                with open(config_yaml, encoding="utf-8") as f:
                    self._cache.update(yaml.safe_load(f) or {})
            except ImportError:
                pass

        self._loaded = True

    async def stop(self) -> None:
        pass

    def get(self, key: str, default: Any = None) -> Any:
        val = os.getenv(key)
        if val is not None:
            return self._coerce(val)
        return self._cache.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        val = self.get(key, default)
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        val = self.get(key, default)
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = self.get(key, None)
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "1", "yes", "on")

    def get_path(self, key: str, default: str = "") -> Path:
        return Path(str(self.get(key, default)))

    def _coerce(self, val: str) -> Any:
        if val.lower() in ("true", "false"):
            return val.lower() == "true"
        try:
            return int(val)
        except ValueError:
            pass
        try:
            return float(val)
        except ValueError:
            pass
        return val
