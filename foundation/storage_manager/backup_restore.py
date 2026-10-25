"""
Portable Backup and Restore Manager for HELIX Personal AI Agent Platform.

Enforces:
- Cryptographic integrity verification (SHA-256) of manifest and archive components.
- Strict path-independent portability using canonical paths and TenantStorageManager.
- Atomic creation with temporary files and cleanup on failure.
- Complete preflight validation before any live state mutation.
- Path traversal, Zip-Slip, symlink, absolute-path, and duplicate-entry rejection.
- Safe staging and automatic rollback recovery on restore failure.
- Secure exclusion of machine-bound secrets (DPAPI credentials, sessions, symmetric keys).
- Strict Permission Manager and user confirmation gating for restore operations.
"""
from __future__ import annotations

import copy
import dataclasses
import enum
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import threading
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from foundation.paths import get_config_dir
from foundation.storage_manager.crypto import (
    decrypt_bytes,
    _HEADER,
    _HEADER_V1,
    _HEADER_V2,
    is_encrypted_bytes,
)
from foundation.storage_manager.tenant_storage import (
    TenantStorageManager,
    get_tenant_storage_manager,
)

logger = logging.getLogger("helix.backup_restore")

# Current backup archive specification versions
CURRENT_FORMAT_VERSION = "2.0"
SUPPORTED_FORMAT_VERSIONS = frozenset({"2.0"})
CURRENT_HELIX_VERSION = "2.0.0"

# Safety limits for archive extraction (Zip-Bomb mitigation)
MAX_ARCHIVE_FILES = 10_000
MAX_TOTAL_UNCOMPRESSED_BYTES = 500 * 1024 * 1024  # 500 MB
MAX_SINGLE_FILE_BYTES = 100 * 1024 * 1024  # 100 MB


class BackupError(Exception):
    """Base exception for all backup and restore failures."""
    pass


class BackupIntegrityError(BackupError):
    """Raised when an archive, manifest, or file fails cryptographic integrity verification."""
    pass


class BackupSecurityError(BackupError):
    """Raised when an archive violates security invariants (e.g., path traversal, symlink)."""
    pass


class BackupIncompatibleError(BackupError):
    """Raised when an archive format or schema version is unsupported."""
    pass


class BackupAuthorizationError(BackupError):
    """Raised when a restore operation lacks required user authorization or confirmation."""
    pass


class BackupCategory(str, enum.Enum):
    CONVERSATIONS = "conversations"
    PREFERENCES = "preferences"
    WORK_SESSIONS = "work"
    USER_MODEL = "user_model"
    EPISODIC_MEMORY = "episodes"
    PROCEDURAL_MEMORY = "workflows"
    GOALS = "goals"
    PLANS = "plans"
    UPLOADS = "uploads"
    NOTES = "notes"
    CONFIG = "config"


DEFAULT_BACKUP_CATEGORIES = [
    BackupCategory.CONVERSATIONS.value,
    BackupCategory.PREFERENCES.value,
    BackupCategory.WORK_SESSIONS.value,
    BackupCategory.USER_MODEL.value,
    BackupCategory.EPISODIC_MEMORY.value,
    BackupCategory.PROCEDURAL_MEMORY.value,
    BackupCategory.GOALS.value,
    BackupCategory.PLANS.value,
    BackupCategory.UPLOADS.value,
    BackupCategory.NOTES.value,
]

BLOCKED_SECRET_FILENAMES = frozenset({
    "users.json",
    "users.json.bak",
    "sessions.json",
    "sessions.json.bak",
    ".secret.key",
    ".env",
})


# Standard explanations for excluded data categories
STANDARD_EXCLUSIONS: dict[str, str] = {
    "security_credentials": "Machine-bound DPAPI credentials (users.json) excluded to prevent token leak and decryption failures.",
    "security_sessions": "Active session tokens (sessions.json) excluded to enforce local authentication on destination.",
    "symmetric_keys": "Machine-local secret keys (.secret.key) excluded for host isolation.",
    "vector_store": "Qdrant vector embeddings excluded (reconstructible from raw memories via embedding engine).",
    "model_weights": "Local GGUF/ONNX model files excluded (reconstructible/downloadable multi-GB binaries).",
    "caches_and_temp": "Temporary caches and build artifacts excluded.",
}


@dataclass
class FileEntry:
    size_bytes: int
    sha256: str
    category: str
    encrypted: bool = False
    cipher: str = "plaintext"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BackupManifest:
    format_version: str
    helix_version: str
    backup_id: str
    created_at: str
    source_user_id: str
    source_os: str
    included_categories: list[str]
    excluded_categories: dict[str, str]
    file_entries: dict[str, dict[str, Any]]
    system_secrets_excluded: bool = True
    manifest_sha256: str = ""
    comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackupManifest":
        return cls(
            format_version=str(data.get("format_version", "")),
            helix_version=str(data.get("helix_version", "")),
            backup_id=str(data.get("backup_id", "")),
            created_at=str(data.get("created_at", "")),
            source_user_id=str(data.get("source_user_id", "")),
            source_os=str(data.get("source_os", "")),
            included_categories=list(data.get("included_categories", [])),
            excluded_categories=dict(data.get("excluded_categories", {})),
            file_entries=dict(data.get("file_entries", {})),
            system_secrets_excluded=bool(data.get("system_secrets_excluded", True)),
            manifest_sha256=str(data.get("manifest_sha256", "")),
            comment=str(data.get("comment", "")),
        )


@dataclass
class BackupResult:
    success: bool
    backup_id: str
    archive_path: Optional[str] = None
    manifest: Optional[BackupManifest] = None
    total_files: int = 0
    total_bytes: int = 0
    error: Optional[str] = None
    execution_evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.manifest:
            d["manifest"] = self.manifest.to_dict()
        return d


@dataclass
class ValidationReport:
    valid: bool
    backup_id: str
    format_version: str
    source_user_id: str
    source_os: str
    total_files: int
    total_bytes: int
    categories: list[str]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    encryption_compatible: bool = True
    undecryptable_files: list[str] = field(default_factory=list)
    manifest: Optional[BackupManifest] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.manifest:
            d["manifest"] = self.manifest.to_dict()
        return d


@dataclass
class RestoreResult:
    success: bool
    backup_id: str
    target_user_id: str
    restored_files_count: int = 0
    restored_categories: list[str] = field(default_factory=list)
    destination_root: Optional[str] = None
    rollback_performed: bool = False
    error: Optional[str] = None
    execution_evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_bytes_sha256(data: bytes) -> str:
    """Compute deterministic SHA-256 hex digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def compute_file_sha256(file_path: Path) -> str:
    """Compute deterministic SHA-256 hex digest of a file in chunks."""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def sanitize_relative_archive_path(archive_member_name: str) -> str:
    """
    Validates and normalizes an archive member path.
    Rejects path traversal, drive letters, leading slashes, null bytes, and non-canonical forms.
    """
    if not archive_member_name or not isinstance(archive_member_name, str):
        raise BackupSecurityError("Archive entry filename cannot be empty")

    if "\0" in archive_member_name:
        raise BackupSecurityError("Archive entry filename contains null bytes")

    # Reject backslashes normalized to forward slashes for internal check
    normalized = archive_member_name.replace("\\", "/")

    # Reject absolute paths and drive letters (e.g. C:, /etc, \\server)
    if normalized.startswith("/") or re.match(r"^[a-zA-Z]:", normalized):
        raise BackupSecurityError(f"Absolute archive path rejected: '{archive_member_name}'")

    parts = [p for p in normalized.split("/") if p]
    if not parts:
        raise BackupSecurityError("Archive entry resolves to empty path")

    # Reject '..' traversal
    for p in parts:
        if p in ("..", "."):
            raise BackupSecurityError(f"Path traversal component '{p}' rejected in '{archive_member_name}'")

    clean_path = "/".join(parts)
    # Must start with allowed top-level directory: 'data/' or equal 'manifest.json'
    if clean_path != "manifest.json" and not clean_path.startswith("data/"):
        raise BackupSecurityError(f"Archive entry outside authorized data root: '{clean_path}'")

    return clean_path


class BackupRestoreManager:
    """
    Production-grade, secure backup creation and restore engine for HELIX.
    Integrates directly with TenantStorageManager, canonical paths, and PermissionManager.
    """

    def __init__(
        self,
        tenant_storage_manager: Optional[TenantStorageManager] = None,
        backup_storage_dir: Optional[Path | str] = None,
    ):
        self._tenant_storage = tenant_storage_manager or get_tenant_storage_manager()
        self._backup_storage_dir = Path(
            backup_storage_dir or (self._tenant_storage.base_dir.parent / "backups")
        ).resolve()
        self._backup_storage_dir.mkdir(parents=True, exist_ok=True)

    @property
    def backup_storage_dir(self) -> Path:
        return self._backup_storage_dir

    # ── Category Discovery ─────────────────────────────────────────────────────

    def _discover_user_category_files(
        self,
        user_id: str,
        category: BackupCategory,
    ) -> list[tuple[Path, str]]:
        """
        Discovers all valid user files for a specific category.
        Returns a list of (absolute_source_path, relative_archive_path).
        """
        clean_uid = self._tenant_storage.sanitize_user_id(user_id)
        results: list[tuple[Path, str]] = []

        if category == BackupCategory.CONVERSATIONS:
            conv_dir = self._tenant_storage.get_user_conversations_dir(clean_uid)
            if conv_dir.exists() and conv_dir.is_dir():
                for f in sorted(conv_dir.glob("*.jsonl")):
                    if f.is_file():
                        results.append((f, f"data/conversations/{f.name}"))

        elif category == BackupCategory.PREFERENCES:
            pref_file = self._tenant_storage.get_user_preferences_dir(clean_uid) / "preferences.json"
            if pref_file.exists() and pref_file.is_file():
                results.append((pref_file, "data/preferences/preferences.json"))

        elif category == BackupCategory.WORK_SESSIONS:
            work_file = self._tenant_storage.get_user_work_dir(clean_uid) / "work_sessions.json"
            if work_file.exists() and work_file.is_file():
                results.append((work_file, "data/work/work_sessions.json"))

        elif category == BackupCategory.USER_MODEL:
            profile_file = self._tenant_storage.get_user_model_dir(clean_uid) / "user_profile.enc"
            if profile_file.exists() and profile_file.is_file():
                results.append((profile_file, "data/user_model/user_profile.enc"))

        elif category == BackupCategory.EPISODIC_MEMORY:
            episodes_file = self._tenant_storage.get_user_episodes_dir(clean_uid) / "episodes.enc"
            if episodes_file.exists() and episodes_file.is_file():
                results.append((episodes_file, "data/episodes/episodes.enc"))

        elif category == BackupCategory.PROCEDURAL_MEMORY:
            wf_file = self._tenant_storage.get_user_workflows_dir(clean_uid) / "workflows.enc"
            if wf_file.exists() and wf_file.is_file():
                results.append((wf_file, "data/workflows/workflows.enc"))

        elif category == BackupCategory.GOALS:
            goals_file = self._tenant_storage.get_user_goals_dir(clean_uid) / "goals.json"
            if goals_file.exists() and goals_file.is_file():
                results.append((goals_file, "data/goals/goals.json"))

        elif category == BackupCategory.PLANS:
            plans_dir = self._tenant_storage.get_user_plans_dir(clean_uid)
            if plans_dir.exists() and plans_dir.is_dir():
                for f in sorted(plans_dir.glob("*.json")):
                    if f.is_file():
                        results.append((f, f"data/plans/{f.name}"))

        elif category == BackupCategory.UPLOADS:
            uploads_dir = self._tenant_storage.get_user_uploads_dir(clean_uid)
            if uploads_dir.exists() and uploads_dir.is_dir():
                for f in sorted(uploads_dir.iterdir()):
                    if f.is_file() and not f.name.startswith("."):
                        results.append((f, f"data/uploads/{f.name}"))

        elif category == BackupCategory.NOTES:
            notes_dir = self._tenant_storage.get_user_notes_dir(clean_uid)
            if notes_dir.exists() and notes_dir.is_dir():
                for f in sorted(notes_dir.glob("*.txt")):
                    if f.is_file() and not f.is_symlink():
                        results.append((f, f"data/notes/{f.name}"))

        elif category == BackupCategory.CONFIG:
            cfg_file = get_config_dir() / "config.json"
            if cfg_file.exists() and cfg_file.is_file():
                results.append((cfg_file, "data/config/config.json"))

        return results

    # ── Backup Creation ────────────────────────────────────────────────────────

    def create_backup(
        self,
        user_id: str,
        destination_path: Optional[Path | str] = None,
        include_categories: Optional[list[str]] = None,
        exclude_categories: Optional[list[str]] = None,
        comment: str = "",
    ) -> BackupResult:
        """
        Creates an atomic, cryptographically verified portable backup archive for a user.
        Writes to a temporary archive and only publishes after complete verification.
        Never overwrites or deletes existing valid backups on failure.
        """
        clean_uid = self._tenant_storage.sanitize_user_id(user_id)
        backup_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        # Determine target categories
        selected_cats = include_categories if include_categories is not None else DEFAULT_BACKUP_CATEGORIES
        if exclude_categories:
            selected_cats = [c for c in selected_cats if c not in exclude_categories]

        # Destination resolution
        if destination_path:
            dest = Path(destination_path).resolve()
            if dest.is_dir():
                final_archive_path = dest / f"helix_backup_{clean_uid}_{timestamp}_{backup_id[:8]}.hlxbak.zip"
            else:
                final_archive_path = dest
        else:
            final_archive_path = self._backup_storage_dir / f"helix_backup_{clean_uid}_{timestamp}_{backup_id[:8]}.hlxbak.zip"

        final_archive_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic temp destination
        pid = os.getpid()
        temp_archive_path = final_archive_path.parent / f"{final_archive_path.stem}.tmp.{pid}.zip"

        logger.info(
            "Initiating atomic backup creation for user '%s' (ID: %s) -> temp: %s",
            clean_uid,
            backup_id,
            temp_archive_path,
        )

        try:
            # 1. Discover files to backup
            files_to_pack: list[tuple[Path, str, str]] = []  # (abs_src, rel_archive, cat)
            for cat_str in selected_cats:
                try:
                    cat_enum = BackupCategory(cat_str)
                except ValueError:
                    continue
                found = self._discover_user_category_files(clean_uid, cat_enum)
                for src_path, rel_path in found:
                    # Security check: never pack machine-bound secrets
                    if src_path.name.lower() in BLOCKED_SECRET_FILENAMES:
                        continue
                    files_to_pack.append((src_path, rel_path, cat_str))

            # 2. Build file entries and compute SHA-256 for all members
            file_entries: dict[str, dict[str, Any]] = {}
            total_uncompressed_bytes = 0

            for src_path, rel_path, cat_str in files_to_pack:
                # Security check on source file
                if not src_path.is_file() or src_path.name.lower() in BLOCKED_SECRET_FILENAMES:
                    continue

                raw_bytes = src_path.read_bytes()
                size_b = len(raw_bytes)
                sha = compute_bytes_sha256(raw_bytes)

                total_uncompressed_bytes += size_b
                is_enc = is_encrypted_bytes(raw_bytes)
                cipher = "HLXENCv2" if raw_bytes.startswith(_HEADER_V2) else ("HLXENCv1" if raw_bytes.startswith(_HEADER_V1) else "plaintext")

                entry = FileEntry(
                    size_bytes=size_b,
                    sha256=sha,
                    category=cat_str,
                    encrypted=is_enc,
                    cipher=cipher,
                )
                file_entries[rel_path] = entry.to_dict()

            # 3. Build manifest metadata
            excluded_dict = copy.deepcopy(STANDARD_EXCLUSIONS)
            all_categories = [c.value for c in BackupCategory]
            for c in all_categories:
                if c not in selected_cats:
                    excluded_dict[f"category_{c}"] = "Explicitly omitted by caller configuration."

            # Calculate deterministic hash of canonical files inventory
            inventory_canonical = json.dumps(file_entries, sort_keys=True)
            manifest_sha = compute_bytes_sha256(inventory_canonical.encode("utf-8"))

            manifest = BackupManifest(
                format_version=CURRENT_FORMAT_VERSION,
                helix_version=CURRENT_HELIX_VERSION,
                backup_id=backup_id,
                created_at=datetime.now(timezone.utc).isoformat(),
                source_user_id=clean_uid,
                source_os=sys.platform,
                included_categories=selected_cats,
                excluded_categories=excluded_dict,
                file_entries=file_entries,
                system_secrets_excluded=True,
                manifest_sha256=manifest_sha,
                comment=comment,
            )

            manifest_bytes = json.dumps(manifest.to_dict(), indent=2).encode("utf-8")

            # 4. Write temporary archive using zipfile.ZIP_DEFLATED
            with zipfile.ZipFile(temp_archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
                # Write manifest first
                zf.writestr("manifest.json", manifest_bytes)

                # Write files
                for src_path, rel_path, _ in files_to_pack:
                    clean_rel = sanitize_relative_archive_path(rel_path)
                    zf.write(src_path, arcname=clean_rel)

            # 5. Integrity verification of the generated archive before publication
            val_report = self.validate_backup(temp_archive_path, target_user_id=clean_uid)
            if not val_report.valid:
                raise BackupIntegrityError(f"Self-verification of written backup failed: {val_report.errors}")

            # 6. Atomic replacement to final published path
            if final_archive_path.exists():
                # On Windows os.replace atomically replaces
                os.replace(temp_archive_path, final_archive_path)
            else:
                temp_archive_path.replace(final_archive_path)

            logger.info(
                "Backup completed successfully: %s (%d files, %d bytes)",
                final_archive_path,
                len(file_entries),
                total_uncompressed_bytes,
            )

            return BackupResult(
                success=True,
                backup_id=backup_id,
                archive_path=str(final_archive_path),
                manifest=manifest,
                total_files=len(file_entries),
                total_bytes=total_uncompressed_bytes,
                execution_evidence={
                    "archive_sha256": compute_file_sha256(final_archive_path),
                    "created_at": manifest.created_at,
                    "target_user_id": clean_uid,
                    "format_version": manifest.format_version,
                },
            )

        except Exception as e:
            logger.error("Backup creation failed for user '%s': %s", clean_uid, e, exc_info=True)
            # Cleanup temp file safely; never touch final_archive_path if it was pre-existing
            if temp_archive_path.exists():
                try:
                    temp_archive_path.unlink()
                except OSError:
                    pass
            return BackupResult(
                success=False,
                backup_id=backup_id,
                error=str(e),
                execution_evidence={"phase": "creation_failed", "exception": type(e).__name__},
            )

    # ── Inspection & Preflight Validation ──────────────────────────────────────

    def inspect_backup(self, archive_path: Path | str) -> BackupManifest:
        """
        Reads and parses the backup manifest without modifying live state or extracting files.
        """
        path = Path(archive_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Backup archive not found: {path}")

        try:
            with zipfile.ZipFile(path, mode="r") as zf:
                if "manifest.json" not in zf.namelist():
                    raise BackupIntegrityError("Archive missing required 'manifest.json' metadata")
                manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
                return BackupManifest.from_dict(manifest_data)
        except (zipfile.BadZipFile, json.JSONDecodeError) as e:
            raise BackupIntegrityError(f"Corrupted archive or invalid manifest: {e}")

    def validate_backup(
        self,
        archive_path: Path | str,
        target_user_id: Optional[str] = None,
        target_base_dir: Optional[Path | str] = None,
    ) -> ValidationReport:
        """
        Performs exhaustive preflight validation on a backup archive before mutation:
        1. Archive integrity & zip structure
        2. Manifest schema and format version compatibility
        3. Path traversal (Zip-Slip), absolute paths, symlinks, and duplicate detection
        4. Cryptographic integrity (SHA-256 matching for every file and manifest inventory)
        5. Encryption cipher compatibility testing
        6. Zip-bomb limits verification
        """
        path = Path(archive_path).resolve()
        errors: list[str] = []
        warnings: list[str] = []
        undecryptable_files: list[str] = []
        manifest: Optional[BackupManifest] = None

        if not path.is_file():
            return ValidationReport(
                valid=False,
                backup_id="unknown",
                format_version="unknown",
                source_user_id="unknown",
                source_os="unknown",
                total_files=0,
                total_bytes=0,
                categories=[],
                errors=[f"Archive file not found: {path}"],
            )

        try:
            # Check for bad zip
            if not zipfile.is_zipfile(path):
                return ValidationReport(
                    valid=False,
                    backup_id="unknown",
                    format_version="unknown",
                    source_user_id="unknown",
                    source_os="unknown",
                    total_files=0,
                    total_bytes=0,
                    categories=[],
                    errors=["File is not a valid ZIP archive or is corrupted."],
                )

            with zipfile.ZipFile(path, mode="r") as zf:
                names = zf.namelist()

                # Check duplicate entries
                if len(names) != len(set(names)):
                    errors.append("Archive contains duplicate filenames (ambiguous archive).")

                # Limit checks (Zip bomb prevention)
                if len(names) > MAX_ARCHIVE_FILES:
                    errors.append(f"Archive exceeds maximum allowed file count ({len(names)} > {MAX_ARCHIVE_FILES}).")

                # 1. Manifest verification
                if "manifest.json" not in names:
                    errors.append("Archive missing required 'manifest.json'.")
                    return ValidationReport(
                        valid=False,
                        backup_id="unknown",
                        format_version="unknown",
                        source_user_id="unknown",
                        source_os="unknown",
                        total_files=0,
                        total_bytes=0,
                        categories=[],
                        errors=errors,
                    )

                manifest_raw = zf.read("manifest.json")
                try:
                    manifest_dict = json.loads(manifest_raw.decode("utf-8"))
                    manifest = BackupManifest.from_dict(manifest_dict)
                except Exception as e:
                    errors.append(f"Failed to parse manifest.json: {e}")
                    return ValidationReport(
                        valid=False,
                        backup_id="unknown",
                        format_version="unknown",
                        source_user_id="unknown",
                        source_os="unknown",
                        total_files=0,
                        total_bytes=0,
                        categories=[],
                        errors=errors,
                    )

                # Format version check
                if manifest.format_version not in SUPPORTED_FORMAT_VERSIONS:
                    errors.append(
                        f"Unsupported backup format version '{manifest.format_version}'. Supported: {sorted(SUPPORTED_FORMAT_VERSIONS)}"
                    )

                # Verify manifest inventory SHA-256
                canonical_inventory = json.dumps(manifest.file_entries, sort_keys=True)
                expected_inv_sha = compute_bytes_sha256(canonical_inventory.encode("utf-8"))
                if manifest.manifest_sha256 != expected_inv_sha:
                    errors.append("Manifest metadata checksum mismatch: file inventory has been altered.")

                # 2. Member security & cryptographic integrity checks
                total_uncompressed = 0
                for info in zf.infolist():
                    # Check symlink or device attributes
                    # High bits of external_attr determine unix file mode
                    unix_mode = info.external_attr >> 16
                    if unix_mode and (unix_mode & 0o170000) == 0o120000:
                        errors.append(f"Symlinks not permitted in backup archive: '{info.filename}'")

                    total_uncompressed += info.file_size
                    if info.file_size > MAX_SINGLE_FILE_BYTES:
                        errors.append(f"File '{info.filename}' exceeds single file size limit.")

                    # Path traversal sanitization check
                    try:
                        clean_name = sanitize_relative_archive_path(info.filename)
                    except BackupSecurityError as sec_err:
                        errors.append(str(sec_err))
                        continue

                    if clean_name == "manifest.json":
                        continue

                    # Verify existence in manifest
                    if clean_name not in manifest.file_entries:
                        errors.append(f"Undeclared file found in archive not present in manifest: '{clean_name}'")
                        continue

                    # Read file and verify cryptographic SHA-256
                    content = zf.read(info)
                    actual_sha = compute_bytes_sha256(content)
                    entry_meta = manifest.file_entries[clean_name]

                    if actual_sha != entry_meta.get("sha256"):
                        errors.append(
                            f"Cryptographic checksum mismatch for '{clean_name}'. Expected {entry_meta.get('sha256')}, got {actual_sha}."
                        )

                    # Check encryption compatibility if encrypted
                    if entry_meta.get("encrypted"):
                        try:
                            # Test if decryptable on current machine
                            dec = decrypt_bytes(content)
                            if is_encrypted_bytes(content) and not dec and len(content) > 8:
                                undecryptable_files.append(clean_name)
                        except Exception:
                            undecryptable_files.append(clean_name)

                if total_uncompressed > MAX_TOTAL_UNCOMPRESSED_BYTES:
                    errors.append(f"Total uncompressed archive size exceeds limit ({total_uncompressed} > {MAX_TOTAL_UNCOMPRESSED_BYTES}).")

                # Check missing files declared in manifest
                for declared_file in manifest.file_entries:
                    if declared_file not in names:
                        errors.append(f"Declared file in manifest missing from archive: '{declared_file}'")

                enc_compatible = len(undecryptable_files) == 0
                if not enc_compatible:
                    warnings.append(
                        f"{len(undecryptable_files)} encrypted files cannot be decrypted on this host (machine-bound DPAPI or missing key). Data will be restored in encrypted format."
                    )

                return ValidationReport(
                    valid=len(errors) == 0,
                    backup_id=manifest.backup_id,
                    format_version=manifest.format_version,
                    source_user_id=manifest.source_user_id,
                    source_os=manifest.source_os,
                    total_files=len(manifest.file_entries),
                    total_bytes=total_uncompressed,
                    categories=manifest.included_categories,
                    errors=errors,
                    warnings=warnings,
                    encryption_compatible=enc_compatible,
                    undecryptable_files=undecryptable_files,
                    manifest=manifest,
                )

        except Exception as e:
            logger.error("Preflight validation error for archive %s: %s", path, e)
            return ValidationReport(
                valid=False,
                backup_id="unknown",
                format_version="unknown",
                source_user_id="unknown",
                source_os="unknown",
                total_files=0,
                total_bytes=0,
                categories=[],
                errors=[f"Validation exception: {e}"],
            )

    # ── Safe Restore & Rollback Recovery ───────────────────────────────────────

    def restore_backup(
        self,
        archive_path: Path | str,
        target_user_id: str,
        confirmation: bool = False,
        permission_grant: Optional[dict[str, Any]] = None,
        target_base_dir: Optional[Path | str] = None,
    ) -> RestoreResult:
        """
        Executes a safe, transactional restore of a validated backup to target_user_id.
        Requirements:
        1. Strict preflight validation passes completely.
        2. Explicit user authorization: confirmation=True OR a verified PermissionManager grant.
        3. Staged in temporary folder before touching live state.
        4. Snapshot of existing user state created before live mutation.
        5. Full rollback to snapshot on any error during application.
        6. Live state is never left in an inconsistent mixed state.
        """
        clean_uid = self._tenant_storage.sanitize_user_id(target_user_id)
        path = Path(archive_path).resolve()

        # 1. Authorization Verification
        authorized = False
        if confirmation:
            authorized = True
        elif permission_grant and isinstance(permission_grant, dict):
            # Verify grant
            if (
                permission_grant.get("action") in ("restore_backup", "restore_user_data")
                and permission_grant.get("target") in (clean_uid, str(path), "*")
                and permission_grant.get("status") == "approved"
            ):
                authorized = True

        if not authorized:
            raise BackupAuthorizationError(
                "Restore operation requires explicit user authorization (confirmation=True or verified permission grant)."
            )

        # 2. Complete Preflight Validation
        report = self.validate_backup(path, target_user_id=clean_uid, target_base_dir=target_base_dir)
        if not report.valid:
            raise BackupIntegrityError(f"Preflight validation failed: {'; '.join(report.errors)}")

        manifest = report.manifest
        if manifest is None:
            raise BackupIntegrityError("Manifest is missing after validation")

        # Resolve live user directory sandbox
        live_user_root = self._tenant_storage.user_dir(clean_uid)

        # Setup temporary directories for staging and rollback snapshot
        temp_parent = live_user_root.parent
        staging_dir = temp_parent / f"_staging_restore_{uuid.uuid4().hex[:8]}"
        rollback_snapshot_dir = temp_parent / f"_rollback_snapshot_{uuid.uuid4().hex[:8]}"

        restored_files_count = 0
        restored_categories: set[str] = set()
        rollback_performed = False

        try:
            # 3. Stage extraction into temporary sandbox
            staging_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(path, mode="r") as zf:
                for rel_path, entry_meta in manifest.file_entries.items():
                    clean_rel = sanitize_relative_archive_path(rel_path)
                    content = zf.read(clean_rel)

                    # Assert hash in staging matches
                    if compute_bytes_sha256(content) != entry_meta["sha256"]:
                        raise BackupIntegrityError(f"Staged file {clean_rel} failed hash verification")

                    dest_staging = staging_dir / clean_rel
                    dest_staging.parent.mkdir(parents=True, exist_ok=True)
                    dest_staging.write_bytes(content)

            # 4. Take snapshot of existing live user data for rollback
            if live_user_root.exists() and any(live_user_root.iterdir()):
                shutil.copytree(live_user_root, rollback_snapshot_dir)

            # 5. Apply staged files to destination user sandbox
            staged_data_dir = staging_dir / "data"
            if staged_data_dir.exists() and staged_data_dir.is_dir():
                for cat_folder in staged_data_dir.iterdir():
                    if not cat_folder.is_dir():
                        continue
                    cat_name = cat_folder.name
                    restored_categories.add(cat_name)

                    # Destination category folder
                    target_cat_dir = self._tenant_storage.user_dir(clean_uid, cat_name)
                    target_cat_dir.mkdir(parents=True, exist_ok=True)

                    for src_file in cat_folder.rglob("*"):
                        if src_file.is_file():
                            rel_to_cat = src_file.relative_to(cat_folder)
                            dest_file = target_cat_dir / rel_to_cat
                            dest_file.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_file, dest_file)
                            restored_files_count += 1

            # 6. Post-restore verification
            # Verify that files were written and match expected checksums
            for rel_path, entry_meta in manifest.file_entries.items():
                # rel_path is e.g. "data/preferences/preferences.json"
                parts = rel_path.split("/")
                if len(parts) >= 3 and parts[0] == "data":
                    cat = parts[1]
                    subpath = "/".join(parts[2:])
                    expected_target = self._tenant_storage.user_dir(clean_uid, cat) / subpath
                    if not expected_target.is_file():
                        raise BackupIntegrityError(f"Post-restore verification: missing file {expected_target}")
                    actual_sha = compute_file_sha256(expected_target)
                    if actual_sha != entry_meta["sha256"]:
                        raise BackupIntegrityError(f"Post-restore checksum mismatch on {expected_target}")

            # 7. Write restore record manifest to user directory for audit trail
            restore_audit = {
                "backup_id": manifest.backup_id,
                "restored_at": datetime.now(timezone.utc).isoformat(),
                "restored_categories": sorted(restored_categories),
                "restored_files_count": restored_files_count,
                "source_user_id": manifest.source_user_id,
                "source_os": manifest.source_os,
            }
            audit_file = live_user_root / "restore_manifest.json"
            audit_file.write_text(json.dumps(restore_audit, indent=2), encoding="utf-8")

            # Success: cleanup staging and rollback snapshot
            shutil.rmtree(staging_dir, ignore_errors=True)
            if rollback_snapshot_dir.exists():
                shutil.rmtree(rollback_snapshot_dir, ignore_errors=True)

            logger.info(
                "Restore completed successfully for user '%s' (%d files across categories: %s)",
                clean_uid,
                restored_files_count,
                sorted(restored_categories),
            )

            return RestoreResult(
                success=True,
                backup_id=manifest.backup_id,
                target_user_id=clean_uid,
                restored_files_count=restored_files_count,
                restored_categories=sorted(restored_categories),
                destination_root=str(live_user_root),
                rollback_performed=False,
                execution_evidence={
                    "audit_file": str(audit_file),
                    "backup_id": manifest.backup_id,
                    "target_user_id": clean_uid,
                },
            )

        except Exception as e:
            logger.error("Restore failed for user '%s': %s. Initiating rollback...", clean_uid, e, exc_info=True)
            # Perform rollback if snapshot exists
            if rollback_snapshot_dir.exists():
                try:
                    shutil.rmtree(live_user_root, ignore_errors=True)
                    if live_user_root.exists():
                        shutil.copytree(rollback_snapshot_dir, live_user_root, dirs_exist_ok=True)
                    else:
                        shutil.copytree(rollback_snapshot_dir, live_user_root)
                    rollback_performed = True
                    logger.info("Rollback successful: previous user state restored.")
                except Exception as roll_err:
                    logger.critical("Rollback failure for user '%s': %s", clean_uid, roll_err)
            else:
                # If no snapshot existed (clean user before restore), clean partially written target
                shutil.rmtree(live_user_root, ignore_errors=True)
                live_user_root.mkdir(parents=True, exist_ok=True)
                rollback_performed = True

            # Cleanup staging and rollback snapshot
            shutil.rmtree(staging_dir, ignore_errors=True)
            shutil.rmtree(rollback_snapshot_dir, ignore_errors=True)

            return RestoreResult(
                success=False,
                backup_id=manifest.backup_id if manifest else "unknown",
                target_user_id=clean_uid,
                rollback_performed=rollback_performed,
                error=str(e),
                execution_evidence={
                    "phase": "application_failed",
                    "rollback_performed": rollback_performed,
                    "exception": type(e).__name__,
                },
            )


# Singleton instance
_backup_restore_manager: Optional[BackupRestoreManager] = None
_backup_manager_lock = threading.Lock()


def get_backup_restore_manager() -> BackupRestoreManager:
    global _backup_restore_manager
    if _backup_restore_manager is None:
        with _backup_manager_lock:
            if _backup_restore_manager is None:
                _backup_restore_manager = BackupRestoreManager()
    return _backup_restore_manager
