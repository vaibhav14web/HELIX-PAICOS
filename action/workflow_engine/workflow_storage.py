"""
Encrypted Compound Snapshot Storage and Atomic CAS Engine for HELIX P0.2 Workflow Engine.
Implements binary authenticated snapshot framing, strict parse bounds,
HMAC verification before parsing, directory-level coordinator locking, and post-replacement settlement.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from action.workflow_engine.workflow_models import (
    Workflow,
    WorkflowStep,
    OutboxEvent,
    WorkflowException,
)
from foundation.storage_manager.crypto import (
    _get_or_create_key,
    encrypt_string,
    decrypt_string,
)
from foundation.storage_manager.tenant_storage import (
    TenantStorageManager,
    get_tenant_storage_manager,
)

logger = logging.getLogger("helix.workflow_storage")

MAGIC_BYTES = b"HLXWF01\n"
MAGIC_LEN = 8
META_LEN_FIELD = 4
MIN_HEADER_JSON = 2
HMAC_LEN = 32
MIN_CIPHERTEXT_LEN = 8  # crypto.py outputs at least b"HLXENCv1"

MIN_ENVELOPE_SIZE = MAGIC_LEN + META_LEN_FIELD + MIN_HEADER_JSON + HMAC_LEN + MIN_CIPHERTEXT_LEN  # 54 Bytes
MAX_METADATA_HEADER_SIZE = 64 * 1024       # 64 KB
MAX_CIPHERTEXT_SIZE = 50 * 1024 * 1024     # 50 MB
MAX_TOTAL_FILE_SIZE = 51 * 1024 * 1024     # 51 MB

WORKFLOW_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


class WorkflowStorageException(WorkflowException):
    """Base exception for workflow storage and persistence operations."""
    pass


class StaleStorageRevisionException(WorkflowStorageException):
    """Raised when a CAS operation fails due to revision mismatch against disk."""
    pass


class WorkflowAlreadyExistsException(WorkflowStorageException):
    """Raised on initial save when the target workflow file already exists on disk."""
    pass


class SecurityIntegrityException(WorkflowStorageException):
    """Raised when HMAC authentication fails, payload is tampered, or limits are violated."""
    pass


class KeyUnavailableException(WorkflowStorageException):
    """Raised when the root secret key cannot be obtained to derive tenant keys."""
    pass


class StorageCommitFailureException(WorkflowStorageException):
    """Raised when a commit fails before or during atomic replacement."""
    pass


class CommitOutcomeUncertainException(WorkflowStorageException):
    """Raised when atomic replacement occurred on disk but post-replacement verification failed."""
    pass


class TenantAccessViolationException(WorkflowStorageException):
    """Raised when tenant ownership checks fail on file access or outbox events."""
    pass


class CorruptSnapshotException(WorkflowStorageException):
    """Raised when snapshot framing, magic, or sizing is structurally invalid."""
    pass


class WorkflowNotFoundException(WorkflowStorageException):
    """Raised when the requested workflow snapshot file does not exist on disk."""
    pass


class ConcurrentEngineOwnershipException(WorkflowStorageException):
    """Raised when another OS process holds the exclusive coordinator lock."""
    pass


def validate_workflow_id(workflow_id: str) -> str:
    """Validate workflow ID format and prevent path traversal."""
    if not isinstance(workflow_id, str) or not WORKFLOW_ID_PATTERN.match(workflow_id.strip()):
        raise ValueError(
            f"Invalid workflow_id '{workflow_id}'. Must be 1-64 alphanumeric, underscore, or hyphen characters."
        )
    clean = workflow_id.strip()
    if clean in (".", ".."):
        raise ValueError(f"Unsafe workflow_id: '{workflow_id}'")
    return clean


def derive_tenant_hmac_key(tenant_id: str) -> bytes:
    """
    Derive a deterministic, tenant-isolated HMAC key from the root secret key.
    Fails closed if the root secret key is unavailable.
    """
    if not tenant_id or not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant_id must be a non-empty string")
    base_key = _get_or_create_key()
    if not base_key:
        raise KeyUnavailableException("System root secret key is unavailable.")
    info = b"HELIX_TENANT_HMAC_v1:" + tenant_id.strip().encode("utf-8")
    return hmac.new(base_key, info, hashlib.sha256).digest()


class WorkflowStorageManager:
    """
    Encrypted compound snapshot storage manager for HELIX P0.2 workflows.
    Enforces atomic CAS commits, binary authenticated framing, strict parsing bounds,
    HMAC authentication prior to header parsing, and single-coordinator process locking.
    """

    def __init__(self, tenant_storage_manager: Optional[TenantStorageManager] = None):
        self._tenant_storage = tenant_storage_manager or get_tenant_storage_manager()
        self._save_lock = asyncio.Lock()
        self._lock_file_handles: dict[str, Any] = {}

    def get_workflows_dir(self, tenant_id: str) -> Path:
        """Resolve and sandbox the tenant's workflows directory."""
        return self._tenant_storage.get_user_workflows_dir(tenant_id)

    def workflow_file_path(self, tenant_id: str, workflow_id: str) -> Path:
        """Resolve sandboxed path for a workflow's state snapshot file."""
        clean_wid = validate_workflow_id(workflow_id)
        wf_dir = self.get_workflows_dir(tenant_id)
        target = (wf_dir / f"{clean_wid}.state.enc").resolve()
        try:
            target.relative_to(wf_dir.resolve())
        except ValueError:
            raise TenantAccessViolationException(f"Path traversal detected: {target} outside {wf_dir}")
        return target

    def acquire_coordinator_lock(self, tenant_id: str) -> None:
        """
        Acquire exclusive OS-level file lock on .workflow_engine.lock in tenant workflow directory.
        Fails closed with ConcurrentEngineOwnershipException if another process holds the lock.
        """
        wf_dir = self.get_workflows_dir(tenant_id)
        wf_dir.mkdir(parents=True, exist_ok=True)
        lock_path = wf_dir / ".workflow_engine.lock"

        if tenant_id in self._lock_file_handles:
            return  # Already acquired by this instance

        try:
            f = open(lock_path, "a+b")
            if sys.platform == "win32":
                import msvcrt
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                except (OSError, IOError) as lock_err:
                    f.close()
                    raise ConcurrentEngineOwnershipException(
                        f"Another OS process holds coordinator lock on {lock_path}"
                    ) from lock_err
            else:
                import fcntl
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, IOError) as lock_err:
                    f.close()
                    raise ConcurrentEngineOwnershipException(
                        f"Another OS process holds coordinator lock on {lock_path}"
                    ) from lock_err

            self._lock_file_handles[tenant_id] = f
        except ConcurrentEngineOwnershipException:
            raise
        except Exception as e:
            raise WorkflowStorageException(f"Failed to acquire coordinator lock for tenant {tenant_id}: {e}") from e

    def release_coordinator_lock(self, tenant_id: str) -> None:
        """Release OS-level file lock on .workflow_engine.lock."""
        f = self._lock_file_handles.pop(tenant_id, None)
        if f:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    try:
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    except Exception:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except Exception:
                        pass
                f.close()
            except Exception as e:
                logger.warning("Error releasing coordinator lock for tenant %s: %s", tenant_id, e)

    def close(self) -> None:
        """Release all held coordinator locks on shutdown."""
        active_tenants = list(self._lock_file_handles.keys())
        for tid in active_tenants:
            self.release_coordinator_lock(tid)

    # ── Binary Framing Helpers ────────────────────────────────────────────────

    def _build_envelope(
        self,
        tenant_id: str,
        workflow_id: str,
        expected_previous_revision: int,
        committed_revision: int,
        ciphertext_bytes: bytes,
    ) -> bytes:
        """Construct authenticated binary snapshot envelope."""
        tenant_hmac_key = derive_tenant_hmac_key(tenant_id)
        payload_sha256 = hashlib.sha256(ciphertext_bytes).hexdigest()

        header_dict = {
            "magic": "HLXWF01",
            "schema_version": "3.2.0",
            "tenant_id": tenant_id,
            "workflow_id": workflow_id,
            "expected_previous_revision": expected_previous_revision,
            "committed_revision": committed_revision,
            "committed_at": time.time(),
            "payload_sha256": payload_sha256,
        }
        header_json_bytes = json.dumps(header_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")
        meta_len = len(header_json_bytes)
        if meta_len > MAX_METADATA_HEADER_SIZE:
            raise SecurityIntegrityException(f"Metadata header size {meta_len} exceeds maximum {MAX_METADATA_HEADER_SIZE}")

        meta_len_bytes = meta_len.to_bytes(4, byteorder="big")
        authenticated_data = MAGIC_BYTES + meta_len_bytes + header_json_bytes + ciphertext_bytes
        computed_hmac = hmac.new(tenant_hmac_key, authenticated_data, hashlib.sha256).digest()

        return MAGIC_BYTES + meta_len_bytes + header_json_bytes + computed_hmac + ciphertext_bytes

    def _parse_and_authenticate_file(
        self,
        file_path: Path,
        expected_tenant_id: str,
    ) -> tuple[dict[str, Any], bytes]:
        """
        Parse and authenticate snapshot file with bounded reads.
        Verifies physical size, framing, exact bounds, and HMAC BEFORE any JSON parsing.
        """
        if not file_path.exists():
            raise WorkflowNotFoundException(f"Snapshot file not found: {file_path}")

        file_size = file_path.stat().st_size
        if file_size < MIN_ENVELOPE_SIZE:
            raise CorruptSnapshotException(f"File size {file_size} is less than minimum envelope size {MIN_ENVELOPE_SIZE}")
        if file_size > MAX_TOTAL_FILE_SIZE:
            raise SecurityIntegrityException(f"File size {file_size} exceeds maximum {MAX_TOTAL_FILE_SIZE}")

        with open(file_path, "rb") as f:
            magic = f.read(MAGIC_LEN)
            if len(magic) != MAGIC_LEN or magic != MAGIC_BYTES:
                raise CorruptSnapshotException("Invalid snapshot magic header.")

            meta_len_bytes = f.read(META_LEN_FIELD)
            if len(meta_len_bytes) != META_LEN_FIELD:
                raise CorruptSnapshotException("Truncated header length field.")
            meta_len = int.from_bytes(meta_len_bytes, byteorder="big")

            if meta_len < MIN_HEADER_JSON or meta_len > MAX_METADATA_HEADER_SIZE:
                raise SecurityIntegrityException(f"Metadata length {meta_len} out of safe bounds.")
            if meta_len + MAGIC_LEN + META_LEN_FIELD + HMAC_LEN + MIN_CIPHERTEXT_LEN > file_size:
                raise CorruptSnapshotException("Header length exceeds envelope file bounds.")

            raw_header_bytes = f.read(meta_len)
            if len(raw_header_bytes) != meta_len:
                raise CorruptSnapshotException("Short read on metadata header.")

            stored_hmac = f.read(HMAC_LEN)
            if len(stored_hmac) != HMAC_LEN:
                raise CorruptSnapshotException("Short read on HMAC authentication tag.")

            expected_ciphertext_len = file_size - (MAGIC_LEN + META_LEN_FIELD + meta_len + HMAC_LEN)
            if expected_ciphertext_len < MIN_CIPHERTEXT_LEN:
                raise CorruptSnapshotException("Ciphertext payload too small.")
            if expected_ciphertext_len > MAX_CIPHERTEXT_SIZE:
                raise SecurityIntegrityException("Ciphertext payload exceeds maximum safe size.")

            ciphertext_bytes = f.read(expected_ciphertext_len)
            if len(ciphertext_bytes) != expected_ciphertext_len:
                raise CorruptSnapshotException("Short read on ciphertext payload.")

            extra_byte = f.read(1)
            if extra_byte != b"":
                raise CorruptSnapshotException("Unexpected trailing unauthenticated bytes detected at EOF.")

        # --- HMAC AUTHENTICATION BEFORE PARSING ANY JSON ---
        tenant_hmac_key = derive_tenant_hmac_key(expected_tenant_id)
        authenticated_data = MAGIC_BYTES + meta_len_bytes + raw_header_bytes + ciphertext_bytes
        expected_hmac = hmac.new(tenant_hmac_key, authenticated_data, hashlib.sha256).digest()

        if not hmac.compare_digest(stored_hmac, expected_hmac):
            raise SecurityIntegrityException("HMAC authentication failed: Snapshot file has been tampered with or corrupted.")

        # --- AUTHENTICATED JSON PARSING (Safe after HMAC check) ---
        try:
            header_json = raw_header_bytes.decode("utf-8")
            header = json.loads(header_json)
        except Exception as e:
            raise CorruptSnapshotException(f"Authenticated metadata header contains malformed JSON: {e}") from e

        if not isinstance(header, dict) or header.get("magic") != "HLXWF01":
            raise CorruptSnapshotException("Snapshot metadata header has invalid structure.")

        if header.get("tenant_id") != expected_tenant_id:
            raise TenantAccessViolationException(
                f"Tenant isolation mismatch: header has '{header.get('tenant_id')}', expected '{expected_tenant_id}'."
            )

        # Verify payload checksum recorded in header
        computed_payload_sha256 = hashlib.sha256(ciphertext_bytes).hexdigest()
        if header.get("payload_sha256") != computed_payload_sha256:
            raise SecurityIntegrityException("Ciphertext SHA-256 does not match authenticated header checksum.")

        return header, ciphertext_bytes

    def _read_and_validate_full_snapshot(
        self,
        file_path: Path,
        expected_tenant_id: str,
    ) -> tuple[Workflow, list[OutboxEvent]]:
        """Fully parse, authenticate, decrypt, and validate a snapshot file."""
        header, ciphertext_bytes = self._parse_and_authenticate_file(file_path, expected_tenant_id)

        try:
            decrypted_str = decrypt_string(ciphertext_bytes)
            compound_dict = json.loads(decrypted_str)
        except Exception as e:
            raise SecurityIntegrityException(f"Failed to decrypt or parse compound snapshot payload: {e}") from e

        if not isinstance(compound_dict, dict) or "workflow_state" not in compound_dict:
            raise CorruptSnapshotException("Decrypted payload missing 'workflow_state'.")

        wf_dict = compound_dict["workflow_state"]
        raw_events = compound_dict.get("outbox_events", [])

        if wf_dict.get("state_revision") != header.get("committed_revision"):
            raise SecurityIntegrityException("Revision accounting mismatch between header and workflow_state.")

        if wf_dict.get("tenant_id") != expected_tenant_id or wf_dict.get("workflow_id") != header.get("workflow_id"):
            raise SecurityIntegrityException("Identity mismatch between header and workflow_state.")

        workflow = Workflow.from_dict(wf_dict)
        outbox_events = [OutboxEvent.from_dict(e) for e in raw_events]

        for e in outbox_events:
            if e.tenant_id != expected_tenant_id or e.workflow_id != workflow.workflow_id:
                raise SecurityIntegrityException(f"Outbox event '{e.event_id}' has mismatched tenant/workflow ownership.")

        return workflow, outbox_events

    def _reconcile_live_workflow(self, live: Workflow, committed: Workflow) -> None:
        """Reconcile all mutated fields from validated committed state into live workflow."""
        live.state_revision = committed.state_revision
        live.status = committed.status
        live.updated_at = committed.updated_at
        live.steps = {sid: step.clone() for sid, step in committed.steps.items()}

    def _unlink_temp_safe(self, temp_path: Path) -> None:
        """Safely unlink temporary file without masking primary exceptions."""
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError as e:
                logger.warning("Failed to unlink temporary snapshot file %s: %s", temp_path, e)

    def _get_prior_outbox_events(self, dest_path: Path, tenant_id: str) -> list[OutboxEvent]:
        """Load prior outbox events from active disk file to preserve pending events."""
        if not dest_path.exists():
            return []
        try:
            _, prior_outbox = self._read_and_validate_full_snapshot(dest_path, tenant_id)
            return prior_outbox
        except Exception as e:
            logger.warning("Could not load prior outbox events from %s: %s", dest_path, e)
            return []

    # ── Public API ────────────────────────────────────────────────────────────

    async def save_snapshot(
        self,
        workflow: Workflow,
        expected_previous_revision: int,
        outbox_events: Optional[list[OutboxEvent]] = None,
    ) -> int:
        """
        Atomically save a compound snapshot containing workflow_state and outbox_events.
        Enforces CAS revision verification, fail-closed HMAC framing, and post-replacement validation.
        Returns the committed revision integer.
        """
        async with self._save_lock:
            tenant_id = workflow.tenant_id
            workflow_id = validate_workflow_id(workflow.workflow_id)
            dest_path = self.workflow_file_path(tenant_id, workflow_id)
            dest_dir = dest_path.parent
            dest_dir.mkdir(parents=True, exist_ok=True)

            # 1. CAS Revision Verification against active disk state
            if expected_previous_revision == 0:
                if dest_path.exists():
                    raise WorkflowAlreadyExistsException(f"Workflow '{workflow_id}' already exists at {dest_path}.")
            else:
                if not dest_path.exists():
                    raise StaleStorageRevisionException(f"Target snapshot does not exist for revision {expected_previous_revision}.")
                header, _ = self._parse_and_authenticate_file(dest_path, tenant_id)
                if header["committed_revision"] != expected_previous_revision:
                    raise StaleStorageRevisionException(
                        f"CAS Mismatch: Disk is at revision {header['committed_revision']}, expected {expected_previous_revision}."
                    )

            # 2. Stage candidate clone (DO NOT mutate live workflow yet)
            candidate: Workflow = workflow.clone()
            target_revision = expected_previous_revision + 1
            candidate.state_revision = target_revision
            candidate.updated_at = time.time()

            # 3. Resolve Outbox Events (Preserve if None)
            effective_outbox: list[OutboxEvent] = (
                self._get_prior_outbox_events(dest_path, tenant_id)
                if outbox_events is None and expected_previous_revision > 0
                else (list(outbox_events) if outbox_events is not None else [])
            )

            for e in effective_outbox:
                if e.tenant_id != tenant_id or e.workflow_id != workflow_id:
                    raise TenantAccessViolationException(
                        f"Outbox event '{e.event_id}' has mismatched ownership: ({e.tenant_id}, {e.workflow_id}) != ({tenant_id}, {workflow_id})"
                    )
                if e.sequence_num <= 0:
                    raise ValueError(f"Invalid sequence_num {e.sequence_num} on outbox event '{e.event_id}'.")

            compound_dict = {
                "workflow_state": candidate.to_dict(),
                "outbox_events": [e.to_dict() for e in effective_outbox],
            }
            compound_json = json.dumps(compound_dict, separators=(",", ":"), sort_keys=True)
            ciphertext_bytes = encrypt_string(compound_json)

            envelope_bytes = self._build_envelope(
                tenant_id, workflow_id, expected_previous_revision, target_revision, ciphertext_bytes
            )

            # 4. Write temporary file and fsync
            temp_path = dest_path.with_name(f"{dest_path.name}.tmp.{uuid.uuid4().hex}")
            try:
                with open(temp_path, "wb") as f:
                    f.write(envelope_bytes)
                    f.flush()
                    os.fsync(f.fileno())

                # Readback verification of temporary file prior to replacement
                self._read_and_validate_full_snapshot(temp_path, tenant_id)
            except Exception as pre_replace_err:
                self._unlink_temp_safe(temp_path)
                raise StorageCommitFailureException(f"Pre-replacement commit failure: {pre_replace_err}") from pre_replace_err

            # 5. Atomic Replacement (THE LINEARIZATION POINT)
            try:
                os.replace(temp_path, dest_path)
            except Exception as replace_err:
                self._unlink_temp_safe(temp_path)
                raise StorageCommitFailureException(f"Replacement failed during commit: {replace_err}") from replace_err

            # 6. Post-Replacement Full Validation & Reconciliation
            try:
                committed_wf, _ = self._read_and_validate_full_snapshot(dest_path, tenant_id)
                if committed_wf.state_revision != target_revision or committed_wf.workflow_id != workflow_id:
                    raise CorruptSnapshotException("Committed file verification mismatch after replacement.")

                # Success: reconcile live workflow instance with committed state
                self._reconcile_live_workflow(workflow, committed_wf)
                return target_revision

            except Exception as post_replace_err:
                logger.critical(
                    "Workflow '%s' replaced on disk at revision %d, but post-replacement verification failed: %s",
                    workflow_id, target_revision, post_replace_err
                )
                raise CommitOutcomeUncertainException(
                    f"Replacement succeeded on disk, but post-replacement verification failed: {post_replace_err}. "
                    f"Commit outcome is UNCERTAIN. Downstream action dispatch is prohibited."
                ) from post_replace_err

    async def load_snapshot(self, workflow_id: str, tenant_id: str) -> tuple[Workflow, list[OutboxEvent]]:
        """
        Load, authenticate, and validate an encrypted compound workflow snapshot from disk.
        Returns the restored Workflow instance and list of OutboxEvents.
        """
        clean_wid = validate_workflow_id(workflow_id)
        target_path = self.workflow_file_path(tenant_id, clean_wid)
        return self._read_and_validate_full_snapshot(target_path, tenant_id)
