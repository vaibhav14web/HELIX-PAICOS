"""
Persistent User Identity and Session Service for HELIX Personal AI Agent Platform.

Provides cryptographic password hashing (PBKDF2-HMAC-SHA256), multi-user account lifecycle,
persistent sessions, brute-force rate limiting, and role-based access control.
Implements atomic persistence, verified crash-recovery backup, thread synchronization,
and fail-closed integrity guarantees.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from foundation.auth.principal import AuthenticatedPrincipal, get_default_owner_principal
from foundation.storage_manager.crypto import encrypt_string, decrypt_string

logger = logging.getLogger("helix.identity")


class IdentityCorruptError(RuntimeError):
    """Raised when persistent identity or session data is corrupt, undecryptable, or unrecoverable."""
    pass


@dataclass
class UserRecord:
    user_id: str
    username: str
    password_hash: str
    salt_hex: str
    display_name: str
    roles: tuple[str, ...] = ("user",)
    is_active: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["roles"] = list(self.roles)
        d["is_owner"] = self.is_owner
        d["is_admin"] = self.is_admin
        return d

    @property
    def is_owner(self) -> bool:
        return "owner" in self.roles

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    def to_principal(self, token_hash: str | None = None, token: str | None = None) -> AuthenticatedPrincipal:
        return AuthenticatedPrincipal(
            user_id=self.user_id,
            username=self.username,
            display_name=self.display_name,
            roles=self.roles,
            is_authenticated=True,
            token_hash=token_hash,
            token=token,
            metadata=self.metadata,
        )


class CrossProcessFileLock:
    """
    Cross-process file lock supporting Windows (msvcrt) and POSIX (fcntl).
    Ensures safe serialization of owner bootstrap claims across concurrent processes.
    """
    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self._fd = None

    def __enter__(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self.lock_path, "a+b")
        import sys
        if sys.platform == "win32":
            import msvcrt
            self._fd.seek(0)
            msvcrt.locking(self._fd.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(self._fd.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._fd:
            try:
                import sys
                if sys.platform == "win32":
                    import msvcrt
                    self._fd.seek(0)
                    msvcrt.locking(self._fd.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            finally:
                try:
                    self._fd.close()
                except Exception:
                    pass
                self._fd = None


@dataclass
class SessionRecord:
    token: str
    token_hash: str
    user_id: str
    username: str
    roles: tuple[str, ...]
    created_at: float
    expires_at: float
    is_revoked: bool = False

    HARD_CAP_SECONDS: float = 7 * 86400.0  # 7 days absolute cap

    def is_expired(self) -> bool:
        now = time.time()
        return now > self.expires_at or self.is_revoked or (now - self.created_at > self.HARD_CAP_SECONDS)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["roles"] = list(self.roles)
        return d


class UserIdentityService:
    """
    Persistent identity directory and session manager for multi-user HELIX.
    Provides atomic persistence with temp files and fsync, crash-recovery backups,
    thread-safe concurrency, and fail-closed corrupt state handling.
    """

    PBKDF2_ITERATIONS = 200_000

    def __init__(self, security_dir: Path | str | None = None):
        if security_dir is not None:
            base_dir = Path(security_dir).resolve()
        else:
            from foundation.auth.auth import get_security_dir
            base_dir = get_security_dir().resolve()
        base_dir.mkdir(parents=True, exist_ok=True)
        self._security_dir = base_dir
        self._users_path = self._security_dir / "users.json"
        self._users_bak_path = self._security_dir / "users.json.bak"
        self._sessions_path = self._security_dir / "sessions.json"
        self._sessions_bak_path = self._security_dir / "sessions.json.bak"

        self._lock = threading.RLock()

        self._users: dict[str, UserRecord] = {}  # username -> UserRecord
        self._users_by_id: dict[str, UserRecord] = {}  # user_id -> UserRecord
        self._sessions: dict[str, SessionRecord] = {}  # token -> SessionRecord

        # Rate limiting: identifier -> [timestamps of failed attempts]
        self._failed_attempts: dict[str, list[float]] = {}
        self._max_failures = 5
        self._lockout_window = 60.0  # seconds

        with self._lock:
            self._load_users()
            self._load_sessions()
            self._bootstrap_default_owner_if_empty()

    def _hash_password(self, password: str, salt: bytes) -> str:
        key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            self.PBKDF2_ITERATIONS,
        )
        return key.hex()

    def _verify_password(self, password: str, salt_hex: str, expected_hash: str) -> bool:
        salt = bytes.fromhex(salt_hex)
        actual_hash = self._hash_password(password, salt)
        return secrets.compare_digest(actual_hash, expected_hash)

    # ── Parsing and Validation Helpers ─────────────────────────────────────────

    @classmethod
    def _parse_user_records(cls, raw_bytes: bytes) -> tuple[dict[str, UserRecord], dict[str, UserRecord]]:
        if not raw_bytes:
            raise ValueError("User payload is empty")
        content = decrypt_string(raw_bytes)
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError(f"User payload must be a JSON list, got {type(data).__name__}")

        users: dict[str, UserRecord] = {}
        users_by_id: dict[str, UserRecord] = {}
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"User record at index {idx} is not an object")
            required_keys = ("user_id", "username", "password_hash", "salt_hex")
            for k in required_keys:
                if k not in item or not isinstance(item[k], str) or not item[k].strip():
                    raise ValueError(f"User record at index {idx} missing or invalid required field '{k}'")

            roles = tuple(item.get("roles", ["user"]))
            rec = UserRecord(
                user_id=item["user_id"],
                username=item["username"].lower().strip(),
                password_hash=item["password_hash"],
                salt_hex=item["salt_hex"],
                display_name=item.get("display_name", item["username"].capitalize()),
                roles=roles,
                is_active=item.get("is_active", True),
                created_at=item.get("created_at", datetime.now(timezone.utc).isoformat()),
                metadata=item.get("metadata", {}),
            )
            users[rec.username] = rec
            users_by_id[rec.user_id] = rec
        return users, users_by_id

    @classmethod
    def _parse_session_records(cls, raw_bytes: bytes) -> dict[str, SessionRecord]:
        if not raw_bytes:
            raise ValueError("Session payload is empty")
        content = decrypt_string(raw_bytes)
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError(f"Session payload must be a JSON list, got {type(data).__name__}")

        sessions: dict[str, SessionRecord] = {}
        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(f"Session record at index {idx} is not an object")
            required_keys = ("token", "token_hash", "user_id", "username", "roles", "created_at", "expires_at")
            for k in required_keys:
                if k not in item:
                    raise ValueError(f"Session record at index {idx} missing required field '{k}'")

            rec = SessionRecord(
                token=str(item["token"]),
                token_hash=str(item["token_hash"]),
                user_id=str(item["user_id"]),
                username=str(item["username"]),
                roles=tuple(item.get("roles", ["user"])),
                created_at=float(item["created_at"]),
                expires_at=float(item["expires_at"]),
                is_revoked=bool(item.get("is_revoked", False)),
            )
            if not rec.is_expired():
                sessions[rec.token] = rec
        return sessions

    # ── Atomic Persistence Primitives ──────────────────────────────────────────

    def _atomic_write_file(self, dest_path: Path, raw_bytes: bytes) -> None:
        """
        Atomically writes raw_bytes to dest_path via a temporary file in the same directory,
        flushing buffers, fsyncing, setting restrictive permissions (0o600), and replacing.
        Cleans up temporary file upon success or failure.
        """
        dest_dir = dest_path.parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        tmp_name = f".{dest_path.name}.tmp.{os.getpid()}_{uuid.uuid4().hex[:8]}"
        tmp_path = dest_dir / tmp_name

        try:
            with open(tmp_path, "wb") as f:
                f.write(raw_bytes)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, AttributeError):
                    pass

            try:
                tmp_path.chmod(0o600)
            except Exception:
                pass

            # On Windows and POSIX, os.replace is atomic when tmp and dest are on the same volume.
            # Temporary file MUST be closed before os.replace on Windows to avoid sharing violations.
            os.replace(tmp_path, dest_path)
            try:
                dest_path.chmod(0o600)
            except Exception:
                pass
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    # ── State Loading and Saving ───────────────────────────────────────────────

    def _load_users(self) -> None:
        with self._lock:
            if not self._users_path.exists() and not self._users_bak_path.exists():
                return

            loaded = False
            primary_err: Exception | None = None

            if self._users_path.exists():
                try:
                    raw_bytes = self._users_path.read_bytes()
                    users_by_username, users_by_id = self._parse_user_records(raw_bytes)
                    self._users = users_by_username
                    self._users_by_id = users_by_id
                    loaded = True
                except Exception as e:
                    primary_err = e
                    logger.warning("Failed to load or decrypt primary users file %s: %s", self._users_path, e)

            if not loaded:
                if self._users_bak_path.exists():
                    try:
                        bak_bytes = self._users_bak_path.read_bytes()
                        users_by_username, users_by_id = self._parse_user_records(bak_bytes)
                        self._users = users_by_username
                        self._users_by_id = users_by_id
                        loaded = True
                        logger.warning("Recovered users state from verified backup %s", self._users_bak_path)
                        try:
                            self._atomic_write_file(self._users_path, bak_bytes)
                        except Exception as rest_err:
                            logger.warning("Failed to restore primary users file from backup: %s", rest_err)
                    except Exception as bak_err:
                        logger.error(
                            "Backup users file %s is also corrupt or undecryptable: %s",
                            self._users_bak_path,
                            bak_err,
                        )
                        raise IdentityCorruptError(
                            f"Primary users file is invalid ({primary_err}) and backup is also invalid ({bak_err}). Refusing to initialize empty state."
                        ) from bak_err
                else:
                    raise IdentityCorruptError(
                        f"Users file {self._users_path} exists but is corrupt or undecryptable ({primary_err}), and no backup was found. Refusing to initialize empty state."
                    ) from primary_err

    def _save_users(self) -> None:
        with self._lock:
            try:
                data = [u.to_dict() for u in self._users.values()]
                json_str = json.dumps(data, indent=2)
                encrypted = encrypt_string(json_str)
                # Verify candidate payload before touching destination
                self._parse_user_records(encrypted)
            except Exception as ser_err:
                logger.error("Serialization or encryption of users failed: %s", ser_err)
                raise

            # If existing users file is verified valid and no backup exists, create initial backup
            if self._users_path.exists() and not self._users_bak_path.exists():
                try:
                    curr_bytes = self._users_path.read_bytes()
                    self._parse_user_records(curr_bytes)
                    self._atomic_write_file(self._users_bak_path, curr_bytes)
                except Exception as bak_err:
                    logger.warning(
                        "Skipping initial backup of %s because existing file is unverified: %s",
                        self._users_path.name,
                        bak_err,
                    )

            # Atomically replace destination with verified payload
            self._atomic_write_file(self._users_path, encrypted)

            # Once primary write succeeds, sync verified payload to backup
            try:
                self._atomic_write_file(self._users_bak_path, encrypted)
            except Exception as bak_err:
                logger.warning(
                    "Failed to sync backup %s: %s",
                    self._users_bak_path.name,
                    bak_err,
                )

    def _load_sessions(self) -> None:
        with self._lock:
            if not self._sessions_path.exists() and not self._sessions_bak_path.exists():
                return

            loaded = False
            primary_err: Exception | None = None

            if self._sessions_path.exists():
                try:
                    raw_bytes = self._sessions_path.read_bytes()
                    self._sessions = self._parse_session_records(raw_bytes)
                    loaded = True
                except Exception as e:
                    primary_err = e
                    logger.warning("Failed to load or decrypt primary sessions file %s: %s", self._sessions_path, e)

            if not loaded:
                if self._sessions_bak_path.exists():
                    try:
                        bak_bytes = self._sessions_bak_path.read_bytes()
                        self._sessions = self._parse_session_records(bak_bytes)
                        loaded = True
                        logger.warning("Recovered sessions state from verified backup %s", self._sessions_bak_path)
                        try:
                            self._atomic_write_file(self._sessions_path, bak_bytes)
                        except Exception as rest_err:
                            logger.warning("Failed to restore primary sessions file from backup: %s", rest_err)
                    except Exception as bak_err:
                        logger.error(
                            "Backup sessions file %s is also corrupt or undecryptable: %s",
                            self._sessions_bak_path,
                            bak_err,
                        )
                        raise IdentityCorruptError(
                            f"Primary sessions file is invalid ({primary_err}) and backup is also invalid ({bak_err}). Refusing to initialize empty state."
                        ) from bak_err
                else:
                    raise IdentityCorruptError(
                        f"Sessions file {self._sessions_path} exists but is corrupt or undecryptable ({primary_err}), and no backup was found. Refusing to initialize empty state."
                    ) from primary_err

    def _save_sessions(self) -> None:
        with self._lock:
            try:
                # Prune expired in memory before serializing
                self._sessions = {t: s for t, s in self._sessions.items() if not s.is_expired()}
                valid = [s.to_dict() for s in self._sessions.values()]
                json_str = json.dumps(valid, indent=2)
                encrypted = encrypt_string(json_str)
                # Verify candidate payload
                self._parse_session_records(encrypted)
            except Exception as ser_err:
                logger.error("Serialization or encryption of sessions failed: %s", ser_err)
                raise

            if self._sessions_path.exists() and not self._sessions_bak_path.exists():
                try:
                    curr_bytes = self._sessions_path.read_bytes()
                    self._parse_session_records(curr_bytes)
                    self._atomic_write_file(self._sessions_bak_path, curr_bytes)
                except Exception as bak_err:
                    logger.warning(
                        "Skipping initial backup of %s because existing file is unverified: %s",
                        self._sessions_path.name,
                        bak_err,
                    )

            # Atomically replace destination with verified payload
            self._atomic_write_file(self._sessions_path, encrypted)

            # Once primary write succeeds, sync verified payload to backup
            try:
                self._atomic_write_file(self._sessions_bak_path, encrypted)
            except Exception as bak_err:
                logger.warning(
                    "Failed to sync backup %s: %s",
                    self._sessions_bak_path.name,
                    bak_err,
                )

    def _bootstrap_default_owner_if_empty(self) -> None:
        """Bootstraps a default owner account if the user directory is empty."""
        with self._lock:
            setup_file = self._security_dir / "setup_token.secret"
            if self._users:
                # Reload setup token from file if existing owner is still unclaimed
                if setup_file.exists():
                    try:
                        self._setup_token = setup_file.read_text(encoding="utf-8").strip()
                    except Exception:
                        pass
                return

            default_owner = get_default_owner_principal()
            username = default_owner.username.lower().strip()
            user_id = default_owner.user_id

            # Use HELIX_OWNER_PASSWORD if configured, or generate a secure default
            env_pw = os.getenv("HELIX_OWNER_PASSWORD")
            is_unclaimed = not bool(env_pw and env_pw.strip())
            password = env_pw.strip() if env_pw else secrets.token_urlsafe(32)

            salt = secrets.token_bytes(16)
            pw_hash = self._hash_password(password, salt)

            if is_unclaimed:
                if setup_file.exists():
                    try:
                        setup_token = setup_file.read_text(encoding="utf-8").strip()
                    except Exception:
                        setup_token = secrets.token_hex(32)
                        setup_file.write_text(setup_token, encoding="utf-8")
                        try:
                            setup_file.chmod(0o600)
                        except Exception:
                            pass
                else:
                    setup_token = secrets.token_hex(32)
                    setup_file.write_text(setup_token, encoding="utf-8")
                    try:
                        setup_file.chmod(0o600)
                    except Exception:
                        pass
                self._setup_token = setup_token
            else:
                self._setup_token = None

            rec = UserRecord(
                user_id=user_id,
                username=username,
                password_hash=pw_hash,
                salt_hex=salt.hex(),
                display_name=default_owner.display_name,
                roles=("user", "owner", "admin"),
                is_active=True,
                metadata={"unclaimed_bootstrap": is_unclaimed},
            )
            self._users[username] = rec
            self._users_by_id[user_id] = rec
            try:
                self._save_users()
            except Exception:
                self._users.pop(username, None)
                self._users_by_id.pop(user_id, None)
                raise

            logger.info(
                "Bootstrapped initial owner user account '%s' (user_id=%s, unclaimed=%s)",
                username,
                user_id,
                is_unclaimed,
            )

    def is_owner_unclaimed(self) -> bool:
        with self._lock:
            for user in self._users.values():
                if user.is_owner and user.metadata.get("unclaimed_bootstrap") is True:
                    return True
            return False

    def claim_owner_bootstrap(
        self,
        username: str,
        password: str,
        setup_token: str | None,
        display_name: str | None = None,
    ) -> UserRecord:
        lock_file = self._security_dir / ".owner_bootstrap.lock"
        with CrossProcessFileLock(lock_file):
            with self._lock:
                # Reload latest state from disk under lock to prevent stale cache race (AUTH-19)
                self._load_users()

                unclaimed_user = None
                for u in self._users.values():
                    if u.is_owner and u.metadata.get("unclaimed_bootstrap") is True:
                        unclaimed_user = u
                        break

                if not unclaimed_user:
                    raise PermissionError("Owner has already been claimed.")

                if not setup_token or not setup_token.strip():
                    raise PermissionError("Invalid or missing setup token. Unauthorized claim attempt.")

                expected_token = None
                setup_file = self._security_dir / "setup_token.secret"
                if setup_file.exists():
                    try:
                        expected_token = setup_file.read_text(encoding="utf-8").strip()
                    except Exception:
                        pass
                if not expected_token:
                    expected_token = getattr(self, "_setup_token", None)

                if not expected_token or not secrets.compare_digest(setup_token.strip(), expected_token):
                    raise PermissionError("Invalid or missing setup token. Unauthorized claim attempt.")

                u_clean = username.lower().strip()
                if not u_clean or len(u_clean) < 3:
                    raise ValueError("Username must be at least 3 characters long")
                if not re.match(r"^[a-zA-Z0-9_\-]+$", u_clean):
                    raise ValueError("Username must contain only alphanumeric characters, underscores, or dashes")
                if len(password) < 8:
                    raise ValueError("Password must be at least 8 characters long")

                if u_clean != unclaimed_user.username and u_clean in self._users:
                    raise ValueError(f"User '{u_clean}' already exists")

                old_username = unclaimed_user.username
                salt = secrets.token_bytes(16)
                pw_hash = self._hash_password(password, salt)
                disp = display_name.strip() if display_name else u_clean.capitalize()

                unclaimed_user.username = u_clean
                unclaimed_user.password_hash = pw_hash
                unclaimed_user.salt_hex = salt.hex()
                unclaimed_user.display_name = disp
                unclaimed_user.metadata["unclaimed_bootstrap"] = False

                if u_clean != old_username:
                    self._users.pop(old_username, None)
                    self._users[u_clean] = unclaimed_user

                try:
                    self._save_users()
                except Exception:
                    self._load_users()
                    raise

                self._setup_token = None
                try:
                    if setup_file.exists():
                        setup_file.unlink()
                except Exception as unlink_err:
                    logger.warning("Failed to delete setup_token.secret: %s", unlink_err)

                logger.info("Owner account claimed successfully by '%s'", u_clean)
                return unclaimed_user

    # ── Rate Limiting ──────────────────────────────────────────────────────────

    def check_rate_limit(self, identifier: str) -> tuple[bool, str]:
        with self._lock:
            now = time.time()
            attempts = self._failed_attempts.get(identifier, [])
            # Keep only attempts in recent window
            recent = [t for t in attempts if now - t < self._lockout_window]
            if recent:
                self._failed_attempts[identifier] = recent
            else:
                self._failed_attempts.pop(identifier, None)
            if len(recent) >= self._max_failures:
                retry_after = int(self._lockout_window - (now - recent[0]))
                return False, f"Too many failed login attempts. Account locked. Try again in {max(1, retry_after)}s."
            return True, ""

    def record_failed_attempt(self, identifier: str) -> None:
        with self._lock:
            now = time.time()
            if identifier not in self._failed_attempts:
                self._failed_attempts[identifier] = []
            self._failed_attempts[identifier].append(now)

    def clear_failed_attempts(self, identifier: str) -> None:
        with self._lock:
            self._failed_attempts.pop(identifier, None)

    # ── User Account Operations ────────────────────────────────────────────────

    def register_user(
        self,
        username: str,
        password: str,
        display_name: str | None = None,
        roles: tuple[str, ...] = ("user",),
        user_id: str | None = None,
    ) -> UserRecord:
        with self._lock:
            u_clean = username.lower().strip()
            if not u_clean or len(u_clean) < 3:
                raise ValueError("Username must be at least 3 characters long")
            if not re.match(r"^[a-zA-Z0-9_\-]+$", u_clean):
                raise ValueError("Username must contain only alphanumeric characters, underscores, or dashes")
            if len(password) < 8:
                raise ValueError("Password must be at least 8 characters long")
            if u_clean in self._users:
                raise ValueError(f"User '{u_clean}' already exists")

            uid = user_id or f"usr_{secrets.token_hex(8)}"
            salt = secrets.token_bytes(16)
            pw_hash = self._hash_password(password, salt)
            disp = display_name.strip() if display_name else u_clean.capitalize()

            rec = UserRecord(
                user_id=uid,
                username=u_clean,
                password_hash=pw_hash,
                salt_hex=salt.hex(),
                display_name=disp,
                roles=roles,
                is_active=True,
            )
            self._users[u_clean] = rec
            self._users_by_id[uid] = rec
            try:
                self._save_users()
            except Exception:
                self._users.pop(u_clean, None)
                self._users_by_id.pop(uid, None)
                raise

            logger.info("Created new user '%s' [id=%s, roles=%s]", u_clean, uid, roles)
            return rec

    def get_user_by_username(self, username: str) -> UserRecord | None:
        with self._lock:
            return self._users.get(username.lower().strip())

    def get_user_by_id(self, user_id: str) -> UserRecord | None:
        with self._lock:
            return self._users_by_id.get(user_id)

    def get_user(self, identifier: str) -> UserRecord | None:
        with self._lock:
            return self._users_by_id.get(identifier) or self._users.get(identifier.lower().strip())

    def list_users(self) -> list[UserRecord]:
        with self._lock:
            return list(self._users.values())

    def authenticate_user(
        self,
        username: str,
        password: str,
        client_identifier: str | None = None,
    ) -> tuple[UserRecord | None, str]:
        with self._lock:
            u_clean = username.lower().strip()
            rate_key = f"{client_identifier or 'global'}:{u_clean}"
            allowed, msg = self.check_rate_limit(rate_key)
            if not allowed:
                return None, msg

            user = self._users.get(u_clean)
            if not user or not user.is_active:
                self.record_failed_attempt(rate_key)
                return None, "Invalid username or password"

            if not self._verify_password(password, user.salt_hex, user.password_hash):
                self.record_failed_attempt(rate_key)
                return None, "Invalid username or password"

            self.clear_failed_attempts(rate_key)
            return user, "Authentication successful"

    # ── Session Lifecycle Operations ───────────────────────────────────────────

    def create_session(
        self,
        user: UserRecord,
        ttl_seconds: float = 86400.0,
    ) -> tuple[str, SessionRecord]:
        with self._lock:
            token = secrets.token_hex(32)
            token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
            now = time.time()
            rec = SessionRecord(
                token=token,
                token_hash=token_hash,
                user_id=user.user_id,
                username=user.username,
                roles=user.roles,
                created_at=now,
                expires_at=now + ttl_seconds,
                is_revoked=False,
            )
            self._sessions[token] = rec
            try:
                self._save_sessions()
            except Exception:
                self._sessions.pop(token, None)
                raise

            logger.info("Issued new session for user '%s' [expires_in=%ds]", user.username, int(ttl_seconds))
            return token, rec

    def validate_session(self, token: str) -> AuthenticatedPrincipal | None:
        if not token or not token.strip():
            return None
        with self._lock:
            t_clean = token.strip()
            rec = self._sessions.get(t_clean)
            if not rec or rec.is_expired():
                if rec and rec.is_expired():
                    self._sessions.pop(t_clean, None)
                return None

            user = self._users_by_id.get(rec.user_id)
            if not user or not user.is_active:
                return None

            return user.to_principal(token_hash=rec.token_hash, token=t_clean)

    def revoke_session(self, token: str) -> bool:
        if not token:
            return False
        with self._lock:
            t_clean = token.strip()
            rec = self._sessions.get(t_clean)
            if rec:
                rec.is_revoked = True
                self._sessions.pop(t_clean, None)
                try:
                    self._save_sessions()
                except Exception:
                    rec.is_revoked = False
                    self._sessions[t_clean] = rec
                    raise

                logger.info("Revoked active session token for user '%s'", rec.username)
                return True
            return False


# Singleton instance
_identity_service: UserIdentityService | None = None
_service_lock = threading.Lock()


def get_identity_service() -> UserIdentityService:
    global _identity_service
    if _identity_service is None:
        with _service_lock:
            if _identity_service is None:
                _identity_service = UserIdentityService()
    return _identity_service
