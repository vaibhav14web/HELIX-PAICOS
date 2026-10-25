import os
import uuid
import json
import shutil
import hashlib
import logging
from pathlib import Path
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from foundation.storage_manager.crypto import (
    _HEADER_V1,
    _HEADER_V2,
    encrypt_bytes,
    decrypt_bytes,
    get_cipher_version,
    is_encrypted_bytes,
    CryptographicError,
    CryptographicIntegrityError,
    UnsupportedCipherVersionError,
)

logger = logging.getLogger("helix.crypto.migration")


class MigrationStatus(str, Enum):
    MIGRATED = "migrated"
    ALREADY_V2 = "already_v2"
    SKIPPED_PLAINTEXT = "skipped_plaintext"
    SKIPPED_UNRECOGNIZED = "skipped_unrecognized"
    FAILED_DECRYPTION = "failed_decryption"
    FAILED_VERIFICATION = "failed_verification"
    FAILED_IO = "failed_io"


class RecordMigrationResult:
    def __init__(
        self,
        status: MigrationStatus,
        original_version: Optional[int],
        target_version: int,
        original_sha256: str,
        migrated_sha256: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        self.status = status
        self.original_version = original_version
        self.target_version = target_version
        self.original_sha256 = original_sha256
        self.migrated_sha256 = migrated_sha256
        self.error_message = error_message

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "original_version": self.original_version,
            "target_version": self.target_version,
            "original_sha256": self.original_sha256,
            "migrated_sha256": self.migrated_sha256,
            "error_message": self.error_message,
        }


class FileMigrationResult:
    def __init__(
        self,
        file_path: str,
        status: MigrationStatus,
        original_version: Optional[int],
        target_version: int,
        original_sha256: str,
        migrated_sha256: Optional[str] = None,
        backup_path: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        self.file_path = file_path
        self.status = status
        self.original_version = original_version
        self.target_version = target_version
        self.original_sha256 = original_sha256
        self.migrated_sha256 = migrated_sha256
        self.backup_path = backup_path
        self.error_message = error_message

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "status": self.status.value,
            "original_version": self.original_version,
            "target_version": self.target_version,
            "original_sha256": self.original_sha256,
            "migrated_sha256": self.migrated_sha256,
            "backup_path": self.backup_path,
            "error_message": self.error_message,
        }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CryptoMigrationEngine:
    """
    Non-destructive, idempotent migration engine transitioning stored
    HLXENCv1 records to standard AEAD (AES-256-GCM, HLXENCv2) with HKDF key separation.
    """

    @staticmethod
    def migrate_record_bytes(
        data: bytes,
        aad: Optional[bytes] = None,
        context: Optional[str] = None,
    ) -> Tuple[bytes, RecordMigrationResult]:
        """
        Migrate in-memory encrypted bytes from HLXENCv1 to HLXENCv2.
        - Idempotent: already HLXENCv2 payloads are verified and preserved.
        - Fail-closed: corrupted or invalid records raise or return failed status without altering data.
        """
        if not data:
            return data, RecordMigrationResult(
                status=MigrationStatus.SKIPPED_PLAINTEXT,
                original_version=None,
                target_version=2,
                original_sha256=_sha256(b""),
            )

        orig_sha = _sha256(data)
        version = get_cipher_version(data)

        if version == 2:
            # Already V2: verify round-trip integrity
            try:
                decrypt_bytes(data, aad=aad, context=context)
                return data, RecordMigrationResult(
                    status=MigrationStatus.ALREADY_V2,
                    original_version=2,
                    target_version=2,
                    original_sha256=orig_sha,
                    migrated_sha256=orig_sha,
                )
            except Exception as e:
                logger.error("Existing V2 record failed integrity verification: %s", e)
                return data, RecordMigrationResult(
                    status=MigrationStatus.FAILED_VERIFICATION,
                    original_version=2,
                    target_version=2,
                    original_sha256=orig_sha,
                    error_message=f"Corrupted V2 record: {e}",
                )

        if version == 1:
            try:
                plaintext = decrypt_bytes(data)
            except Exception as e:
                logger.error("Failed to decrypt legacy HLXENCv1 record: %s", e)
                return data, RecordMigrationResult(
                    status=MigrationStatus.FAILED_DECRYPTION,
                    original_version=1,
                    target_version=2,
                    original_sha256=orig_sha,
                    error_message=f"Legacy decryption failed: {e}",
                )

            # Re-encrypt to V2
            new_ciphertext = encrypt_bytes(plaintext, aad=aad, context=context, version=2)

            # Immediate verification
            try:
                verified_plaintext = decrypt_bytes(new_ciphertext, aad=aad, context=context)
                if verified_plaintext != plaintext:
                    raise CryptographicIntegrityError("Migrated plaintext mismatch during post-migration verification")
            except Exception as e:
                logger.error("Post-migration verification failed: %s", e)
                return data, RecordMigrationResult(
                    status=MigrationStatus.FAILED_VERIFICATION,
                    original_version=1,
                    target_version=2,
                    original_sha256=orig_sha,
                    error_message=f"Verification failure: {e}",
                )

            return new_ciphertext, RecordMigrationResult(
                status=MigrationStatus.MIGRATED,
                original_version=1,
                target_version=2,
                original_sha256=orig_sha,
                migrated_sha256=_sha256(new_ciphertext),
            )

        # Check for unknown HLXENC header
        if data.startswith(b"HLXENC"):
            return data, RecordMigrationResult(
                status=MigrationStatus.SKIPPED_UNRECOGNIZED,
                original_version=None,
                target_version=2,
                original_sha256=orig_sha,
                error_message=f"Unrecognized header: {data[:8]!r}",
            )

        # Plaintext
        return data, RecordMigrationResult(
            status=MigrationStatus.SKIPPED_PLAINTEXT,
            original_version=None,
            target_version=2,
            original_sha256=orig_sha,
        )

    @classmethod
    def migrate_file(
        cls,
        file_path: Path,
        backup: bool = True,
        aad: Optional[bytes] = None,
        context: Optional[str] = None,
    ) -> FileMigrationResult:
        """
        Durably and atomically migrate a single file from HLXENCv1 to HLXENCv2.
        - Preserves original backup at <file_path>.bak.hlxencv1.
        - Writes to temporary file and atomically replaces on disk.
        """
        p = Path(file_path).resolve()
        if not p.is_file():
            return FileMigrationResult(
                file_path=str(p),
                status=MigrationStatus.FAILED_IO,
                original_version=None,
                target_version=2,
                original_sha256="",
                error_message=f"File not found: {p}",
            )

        try:
            raw_bytes = p.read_bytes()
        except Exception as e:
            return FileMigrationResult(
                file_path=str(p),
                status=MigrationStatus.FAILED_IO,
                original_version=None,
                target_version=2,
                original_sha256="",
                error_message=f"Read error: {e}",
            )

        orig_sha = _sha256(raw_bytes)
        version = get_cipher_version(raw_bytes)

        if version == 2:
            # Already V2: verify decryptability
            try:
                decrypt_bytes(raw_bytes, aad=aad, context=context)
                return FileMigrationResult(
                    file_path=str(p),
                    status=MigrationStatus.ALREADY_V2,
                    original_version=2,
                    target_version=2,
                    original_sha256=orig_sha,
                    migrated_sha256=orig_sha,
                )
            except Exception as e:
                return FileMigrationResult(
                    file_path=str(p),
                    status=MigrationStatus.FAILED_VERIFICATION,
                    original_version=2,
                    target_version=2,
                    original_sha256=orig_sha,
                    error_message=f"Corrupted V2 file: {e}",
                )

        if version != 1:
            if raw_bytes.startswith(b"HLXENC"):
                return FileMigrationResult(
                    file_path=str(p),
                    status=MigrationStatus.SKIPPED_UNRECOGNIZED,
                    original_version=None,
                    target_version=2,
                    original_sha256=orig_sha,
                    error_message=f"Unsupported header: {raw_bytes[:8]!r}",
                )
            return FileMigrationResult(
                file_path=str(p),
                status=MigrationStatus.SKIPPED_PLAINTEXT,
                original_version=None,
                target_version=2,
                original_sha256=orig_sha,
            )

        # 1. Create backup if requested
        backup_path: Optional[Path] = None
        if backup:
            backup_path = p.with_name(f"{p.name}.bak.hlxencv1")
            try:
                shutil.copy2(p, backup_path)
            except Exception as e:
                return FileMigrationResult(
                    file_path=str(p),
                    status=MigrationStatus.FAILED_IO,
                    original_version=1,
                    target_version=2,
                    original_sha256=orig_sha,
                    error_message=f"Failed to create backup: {e}",
                )

        # 2. Decrypt legacy content
        try:
            plaintext = decrypt_bytes(raw_bytes)
        except Exception as e:
            return FileMigrationResult(
                file_path=str(p),
                status=MigrationStatus.FAILED_DECRYPTION,
                original_version=1,
                target_version=2,
                original_sha256=orig_sha,
                backup_path=str(backup_path) if backup_path else None,
                error_message=f"Decryption failed: {e}",
            )

        # 3. Re-encrypt with AEAD V2
        try:
            new_ciphertext = encrypt_bytes(plaintext, aad=aad, context=context, version=2)
            verified_plain = decrypt_bytes(new_ciphertext, aad=aad, context=context)
            if verified_plain != plaintext:
                raise CryptographicIntegrityError("Round-trip validation mismatch")
        except Exception as e:
            return FileMigrationResult(
                file_path=str(p),
                status=MigrationStatus.FAILED_VERIFICATION,
                original_version=1,
                target_version=2,
                original_sha256=orig_sha,
                backup_path=str(backup_path) if backup_path else None,
                error_message=f"Post-migration verification failed: {e}",
            )

        # 4. Atomic durable write
        tmp_path = p.with_name(f"{p.name}.tmp.{uuid.uuid4().hex}")
        try:
            with open(tmp_path, "wb") as f:
                f.write(new_ciphertext)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, p)
        except Exception as e:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return FileMigrationResult(
                file_path=str(p),
                status=MigrationStatus.FAILED_IO,
                original_version=1,
                target_version=2,
                original_sha256=orig_sha,
                backup_path=str(backup_path) if backup_path else None,
                error_message=f"Atomic write failed: {e}",
            )

        migrated_sha = _sha256(new_ciphertext)
        return FileMigrationResult(
            file_path=str(p),
            status=MigrationStatus.MIGRATED,
            original_version=1,
            target_version=2,
            original_sha256=orig_sha,
            migrated_sha256=migrated_sha,
            backup_path=str(backup_path) if backup_path else None,
        )

    @classmethod
    def rollback_file(cls, file_path: Path) -> bool:
        """
        Restore a file from its <file_path>.bak.hlxencv1 backup.
        """
        p = Path(file_path).resolve()
        backup_path = p.with_name(f"{p.name}.bak.hlxencv1")
        if not backup_path.is_file():
            logger.warning("Rollback backup not found for %s", p)
            return False

        tmp_path = p.with_name(f"{p.name}.rollback.tmp.{uuid.uuid4().hex}")
        try:
            shutil.copy2(backup_path, tmp_path)
            os.replace(tmp_path, p)
            return True
        except Exception as e:
            logger.error("Failed to rollback %s from %s: %s", p, backup_path, e)
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            return False

    @classmethod
    def migrate_directory(
        cls,
        dir_path: Path,
        patterns: Optional[List[str]] = None,
        recursive: bool = True,
        backup: bool = True,
    ) -> Dict[str, Any]:
        """
        Scan and migrate all encrypted files in a directory.
        Records summary journal without logging secrets or plaintext.
        """
        root = Path(dir_path).resolve()
        if not root.is_dir():
            return {
                "success": False,
                "error": f"Directory not found: {root}",
                "results": [],
            }

        search_patterns = patterns or ["*.json", "*.jsonl", "*.bin"]
        candidate_files: List[Path] = []

        for pat in search_patterns:
            if recursive:
                candidate_files.extend(root.rglob(pat))
            else:
                candidate_files.extend(root.glob(pat))

        # Filter out temporary, backup, and lock files
        files_to_process = [
            f for f in candidate_files
            if not f.name.endswith(".bak.hlxencv1")
            and not f.name.endswith(".tmp")
            and ".tmp." not in f.name
            and not f.name.endswith(".lock")
        ]

        results: List[Dict[str, Any]] = []
        counts = {
            "total_files": len(files_to_process),
            "migrated": 0,
            "already_v2": 0,
            "skipped_plaintext": 0,
            "skipped_unrecognized": 0,
            "failed": 0,
        }

        for f in sorted(files_to_process):
            res = cls.migrate_file(f, backup=backup)
            results.append(res.to_dict())
            if res.status == MigrationStatus.MIGRATED:
                counts["migrated"] += 1
            elif res.status == MigrationStatus.ALREADY_V2:
                counts["already_v2"] += 1
            elif res.status == MigrationStatus.SKIPPED_PLAINTEXT:
                counts["skipped_plaintext"] += 1
            elif res.status == MigrationStatus.SKIPPED_UNRECOGNIZED:
                counts["skipped_unrecognized"] += 1
            else:
                counts["failed"] += 1

        journal_file = root / ".migration_journal_v2.json"
        summary = {
            "root_directory": str(root),
            "counts": counts,
            "results": results,
            "success": counts["failed"] == 0,
        }

        try:
            journal_file.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("Could not write migration journal to %s: %s", journal_file, e)

        return summary
