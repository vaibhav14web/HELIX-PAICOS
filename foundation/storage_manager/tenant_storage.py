"""
Tenant and Multi-User Storage Isolation Manager for HELIX.

Enforces physical and logical storage boundaries between independent users,
preventing cross-user file access, path traversal, or resource leakage.
Includes safe legacy data migration with automated backups.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("helix.tenant_storage")


def _atomic_write_bytes(dest_path: Path, data: bytes) -> None:
    """
    Atomically writes `data` to `dest_path` via a temporary file in the destination directory,
    ensuring flush and fsync before replacing destination.
    Preserves existing destination file if any step fails.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_name(f"{dest_path.name}.tmp.{uuid.uuid4().hex}")
    try:
        with open(temp_path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if temp_path.stat().st_size != len(data):
            raise IOError(f"Temporary file size mismatch: expected {len(data)}, got {temp_path.stat().st_size}")
        os.replace(temp_path, dest_path)
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


def _atomic_copy_file(src_path: Path, dest_path: Path) -> None:
    """
    Atomically copies `src_path` to `dest_path` via a temporary file in the destination directory,
    ensuring flush and fsync before replacing destination.
    Preserves existing destination file if copy fails.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_name(f"{dest_path.name}.tmp.{uuid.uuid4().hex}")
    try:
        shutil.copy2(src_path, temp_path)
        with open(temp_path, "rb+") as f:
            f.flush()
            os.fsync(f.fileno())
        if temp_path.stat().st_size != src_path.stat().st_size:
            raise IOError(f"Temporary file size mismatch: expected {src_path.stat().st_size}, got {temp_path.stat().st_size}")
        os.replace(temp_path, dest_path)
    except Exception:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise


class TenantStorageManager:
    """
    Manages isolated filesystem directories and data access for individual users.
    Each user receives a segregated sandbox under `data/users/<sanitized_user_id>/`.
    """

    def __init__(self, base_dir: Path | str | None = None):
        if base_dir is not None:
            target_base = Path(base_dir)
        else:
            env_user_data = os.getenv("HELIX_USER_DATA_PATH")
            if env_user_data and env_user_data.strip():
                from foundation.paths import expand_path
                target_base = expand_path(env_user_data.strip()) or Path(env_user_data.strip())
            else:
                from foundation.paths import get_runtime_dir
                target_base = get_runtime_dir() / "users"
        self._base_dir = target_base.resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def sanitize_user_id(self, user_id: str) -> str:
        """Sanitizes user_id to prevent directory traversal attacks."""
        if not user_id or not isinstance(user_id, str):
            raise ValueError("user_id must be a non-empty string")
        clean = re.sub(r"[^a-zA-Z0-9_\-]", "_", user_id.strip()).strip("_")
        if not clean or clean in (".", ".."):
            raise ValueError(f"Invalid or unsafe user_id: '{user_id}'")
        base_device = clean.split(".")[0].upper()
        if base_device in ("CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"):
            raise ValueError(f"Reserved device name '{clean}' cannot be used as user_id")
        return clean

    def user_dir(self, user_id: str, *subdirs: str) -> Path:
        """
        Resolves a user-isolated path, ensuring it cannot escape the user's directory sandbox.
        """
        clean_uid = self.sanitize_user_id(user_id)
        user_root = (self._base_dir / clean_uid).resolve()

        # Build requested sub-path
        target = user_root
        for part in subdirs:
            clean_part = re.sub(r"[\\/]", "", str(part)).strip()
            if clean_part in (".", "..", ""):
                continue
            base_device = clean_part.split(".")[0].upper()
            if base_device in ("CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"):
                raise ValueError(f"Reserved device name '{clean_part}' cannot be used as a directory component")
            target = target / clean_part

        target_resolved = target.resolve()
        # Enforce sandbox invariant
        try:
            target_resolved.relative_to(user_root)
        except ValueError:
            raise PermissionError(f"Path traversal detected: {target_resolved} is outside user sandbox {user_root}")

        target_resolved.mkdir(parents=True, exist_ok=True)
        return target_resolved

    def get_user_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id)

    def get_user_conversations_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id, "conversations")

    def get_user_work_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id, "work")

    def get_user_preferences_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id, "preferences")

    def get_user_model_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id, "user_model")

    def get_user_episodes_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id, "episodes")

    def get_user_workflows_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id, "workflows")

    def get_user_goals_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id, "goals")

    def get_user_plans_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id, "plans")

    def get_user_uploads_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id, "uploads")

    def get_user_notes_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id, "notes")

    # ── Non-Destructive Legacy Data Migration ──────────────────────────────────

    def resolve_legacy_data_dir(self, legacy_data_dir: Path | str | None = None) -> Path:
        """
        Resolves the source directory for legacy single-user data migration.
        Precedence:
        1. Explicit legacy_data_dir function argument
        2. Explicit HELIX_LEGACY_DATA_DIR environment variable
        3. Canonical default: get_helix_home() / "legacy_data"
        """
        if legacy_data_dir is not None and str(legacy_data_dir).strip():
            from foundation.paths import expand_path
            expanded = expand_path(legacy_data_dir)
            return (expanded or Path(legacy_data_dir)).resolve()

        env_legacy = os.getenv("HELIX_LEGACY_DATA_DIR")
        if env_legacy and env_legacy.strip():
            from foundation.paths import expand_path
            expanded = expand_path(env_legacy.strip())
            return (expanded or Path(env_legacy.strip())).resolve()

        from foundation.paths import get_helix_home
        return (get_helix_home() / "legacy_data").resolve()

    def migrate_legacy_data(
        self,
        target_user_id: str,
        legacy_data_dir: Path | str | None = None,
    ) -> dict[str, Any]:
        """
        Migrates existing single-user data into the target user's isolated partition.
        Creates an immutable backup snapshot before migrating.
        """
        clean_uid = self.sanitize_user_id(target_user_id)
        source = self.resolve_legacy_data_dir(legacy_data_dir)
        backup_root = source / "backups"
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = backup_root / f"legacy_pre_migration_{timestamp}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        migrated_items: list[str] = []
        user_target_root = self.user_dir(clean_uid)

        logger.info(
            "Starting legacy data migration for user '%s' from %s",
            clean_uid,
            source,
        )

        # 1. Migrate Conversations
        legacy_convs = source / "memory" / "conversations"
        if legacy_convs.exists() and legacy_convs.is_dir():
            target_convs = self.get_user_conversations_dir(clean_uid)
            target_convs.mkdir(parents=True, exist_ok=True)
            for f in legacy_convs.glob("*.jsonl"):
                shutil.copy2(f, backup_dir / f"conv_{f.name}")
                dest = target_convs / f.name
                if not dest.exists():
                    try:
                        from foundation.storage_manager.crypto import decrypt_string, encrypt_string
                        raw_bytes = f.read_bytes()
                        content = decrypt_string(raw_bytes)
                        updated_lines = []
                        for line in content.splitlines():
                            if not line.strip():
                                continue
                            try:
                                d = json.loads(line)
                                if isinstance(d, dict) and d.get("user_id") in ("default_owner", "default", None):
                                    d["user_id"] = clean_uid
                                updated_lines.append(json.dumps(d))
                            except Exception:
                                updated_lines.append(line)
                        if updated_lines:
                            payload = encrypt_string("\n".join(updated_lines) + "\n")
                            _atomic_write_bytes(dest, payload)
                        else:
                            _atomic_copy_file(f, dest)
                        migrated_items.append(f"conversation:{f.name}")
                    except Exception as e:
                        logger.warning("Decryption/re-encryption failed for %s, falling back to direct atomic copy: %s", f.name, e)
                        try:
                            _atomic_copy_file(f, dest)
                            migrated_items.append(f"conversation:{f.name}")
                        except Exception as copy_err:
                            logger.error("Failed to migrate conversation %s: %s", f.name, copy_err)

        # 2. Migrate Preferences
        legacy_prefs = source / "memory" / "preferences.json"
        if legacy_prefs.exists() and legacy_prefs.is_file():
            shutil.copy2(legacy_prefs, backup_dir / "preferences.json")
            target_prefs = self.get_user_preferences_dir(clean_uid) / "preferences.json"
            if not target_prefs.exists():
                try:
                    _atomic_copy_file(legacy_prefs, target_prefs)
                    migrated_items.append("preferences.json")
                except Exception as e:
                    logger.error("Failed to migrate preferences: %s", e)

        # 3. Migrate Work Sessions
        legacy_work = source / "memory" / "work_sessions.json"
        if legacy_work.exists() and legacy_work.is_file():
            shutil.copy2(legacy_work, backup_dir / "work_sessions.json")
            target_work = self.get_user_work_dir(clean_uid) / "work_sessions.json"
            if not target_work.exists():
                try:
                    _atomic_copy_file(legacy_work, target_work)
                    migrated_items.append("work_sessions.json")
                except Exception as e:
                    logger.error("Failed to migrate work sessions: %s", e)

        # 4. Migrate User Model Profile
        legacy_profile = source / "user_model" / "user_profile.enc"
        if legacy_profile.exists() and legacy_profile.is_file():
            shutil.copy2(legacy_profile, backup_dir / "user_profile.enc")
            target_profile = self.get_user_model_dir(clean_uid) / "user_profile.enc"
            if not target_profile.exists():
                try:
                    _atomic_copy_file(legacy_profile, target_profile)
                    migrated_items.append("user_profile.enc")
                except Exception as e:
                    logger.error("Failed to migrate user profile: %s", e)

        # 5. Migrate Episodes
        legacy_episodes = source / "memory" / "episodes.enc"
        if legacy_episodes.exists() and legacy_episodes.is_file():
            shutil.copy2(legacy_episodes, backup_dir / "episodes.enc")
            target_episodes = self.get_user_episodes_dir(clean_uid) / "episodes.enc"
            if not target_episodes.exists():
                try:
                    _atomic_copy_file(legacy_episodes, target_episodes)
                    migrated_items.append("episodes.enc")
                except Exception as e:
                    logger.error("Failed to migrate episodes: %s", e)

        # 6. Migrate Workflows
        legacy_workflows = source / "memory" / "workflows.enc"
        if legacy_workflows.exists() and legacy_workflows.is_file():
            shutil.copy2(legacy_workflows, backup_dir / "workflows.enc")
            target_workflows = self.get_user_workflows_dir(clean_uid) / "workflows.enc"
            if not target_workflows.exists():
                try:
                    _atomic_copy_file(legacy_workflows, target_workflows)
                    migrated_items.append("workflows.enc")
                except Exception as e:
                    logger.error("Failed to migrate workflows: %s", e)

        # 7. Migrate Goals
        legacy_goals = source / "goals" / "goals.json"
        if legacy_goals.exists() and legacy_goals.is_file():
            shutil.copy2(legacy_goals, backup_dir / "goals.json")
            target_goals = self.get_user_goals_dir(clean_uid) / "goals.json"
            if not target_goals.exists():
                try:
                    _atomic_copy_file(legacy_goals, target_goals)
                    migrated_items.append("goals.json")
                except Exception as e:
                    logger.error("Failed to migrate goals: %s", e)

        # 8. Migrate Uploaded Files
        legacy_uploads = source / "uploads"
        if legacy_uploads.exists() and legacy_uploads.is_dir():
            target_uploads = self.get_user_uploads_dir(clean_uid)
            for f in legacy_uploads.iterdir():
                if f.is_file():
                    shutil.copy2(f, backup_dir / f"upload_{f.name}")
                    dest = target_uploads / f.name
                    if not dest.exists():
                        try:
                            _atomic_copy_file(f, dest)
                            migrated_items.append(f"upload:{f.name}")
                        except Exception as e:
                            logger.error("Failed to migrate upload %s: %s", f.name, e)

        manifest = {
            "status": "completed",
            "target_user_id": clean_uid,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "backup_path": str(backup_dir),
            "migrated_items_count": len(migrated_items),
            "migrated_items": migrated_items,
        }

        manifest_file = user_target_root / "migration_manifest.json"
        _atomic_write_bytes(manifest_file, json.dumps(manifest, indent=2).encode("utf-8"))
        logger.info(
            "Legacy migration finished for user '%s'. %d items migrated. Backup at %s",
            clean_uid,
            len(migrated_items),
            backup_dir,
        )
        return manifest


# Singleton instance
_tenant_storage_manager: TenantStorageManager | None = None
_tenant_storage_lock = threading.Lock()


def get_tenant_storage_manager() -> TenantStorageManager:
    global _tenant_storage_manager
    if _tenant_storage_manager is None:
        with _tenant_storage_lock:
            if _tenant_storage_manager is None:
                _tenant_storage_manager = TenantStorageManager()
    return _tenant_storage_manager
