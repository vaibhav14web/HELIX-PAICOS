"""
Authentication and Token Management for HELIX Personal AI Agent Platform.

Enforces least-privilege token authentication, token revocation, origin validation,
and safe interface binding. Decoupled from static global tokens to establish
the foundation for multi-user identity progression.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from pathlib import Path

from foundation.auth.principal import AuthenticatedPrincipal, get_default_owner_principal

logger = logging.getLogger("helix.auth")

_AUTH_TOKEN: str | None = None
_SCOPED_TOKENS: dict[str, tuple[AuthenticatedPrincipal, float | None]] = {}


def hash_token(token: str) -> str:
    """Computes a deterministic SHA-256 hash of a token for indexing/revocation."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_security_dir() -> Path:
    """
    Returns the filesystem path to the persistent security directory.
    Precedence:
    1. HELIX_SECURITY_PATH environment variable
    2. HELIX_MEMORY_PATH parent directory / 'security' (if explicitly configured)
    3. Canonical per-user directory: get_runtime_dir() / 'security'
    """
    env_sec = os.getenv("HELIX_SECURITY_PATH")
    if env_sec and env_sec.strip():
        from foundation.paths import expand_path
        expanded = expand_path(env_sec.strip())
        if expanded:
            expanded.mkdir(parents=True, exist_ok=True)
            return expanded

    env_mem = os.getenv("HELIX_MEMORY_PATH")
    if env_mem and env_mem.strip():
        from foundation.paths import expand_path
        expanded = expand_path(env_mem.strip())
        if expanded:
            sec_dir = expanded.parent / "security"
            sec_dir.mkdir(parents=True, exist_ok=True)
            return sec_dir

    from foundation.paths import get_runtime_dir
    security_dir = get_runtime_dir() / "security"
    security_dir.mkdir(parents=True, exist_ok=True)
    return security_dir


def get_token_file_path() -> Path:
    """Returns the filesystem path to the local secret token file."""
    return get_security_dir() / "auth_token.secret"


def get_revoked_tokens_file_path() -> Path:
    """Returns the filesystem path to the persistent revoked token hashes file."""
    return get_security_dir() / "revoked_tokens.json"


def _load_revoked_hashes() -> set[str]:
    rev_path = get_revoked_tokens_file_path()
    if rev_path.exists():
        try:
            data = json.loads(rev_path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(data)
        except Exception as e:
            logger.warning(f"Failed to load revoked tokens file: {e}")
    return set()


def _save_revoked_hashes(hashes: set[str]) -> None:
    rev_path = get_revoked_tokens_file_path()
    try:
        rev_path.write_text(json.dumps(sorted(list(hashes))), encoding="utf-8")
        try:
            rev_path.chmod(0o600)
        except Exception:
            pass
    except Exception as e:
        logger.warning(f"Failed to save revoked tokens file: {e}")


_REVOKED_TOKEN_HASHES: set[str] = _load_revoked_hashes()


def get_or_create_auth_token() -> str:
    """
    Retrieves the persistent local secret auth token or generates a new 32-byte token.
    Enforces secure storage at rest.
    """
    global _AUTH_TOKEN
    if _AUTH_TOKEN:
        return _AUTH_TOKEN

    env_token = os.getenv("HELIX_AUTH_TOKEN")
    if env_token and env_token.strip():
        _AUTH_TOKEN = env_token.strip()
        return _AUTH_TOKEN

    token_path = get_token_file_path()
    if token_path.exists():
        try:
            token = token_path.read_text(encoding="utf-8").strip()
            if token:
                _AUTH_TOKEN = token
                return _AUTH_TOKEN
        except Exception as e:
            logger.warning(f"Failed to read auth token file: {e}")

    new_token = secrets.token_hex(32)
    try:
        token_path.write_text(new_token, encoding="utf-8")
        try:
            token_path.chmod(0o600)
        except Exception:
            pass
        logger.info(f"Generated new local auth token at {token_path}")
    except Exception as e:
        logger.warning(f"Failed to write auth token file: {e}")

    _AUTH_TOKEN = new_token
    return _AUTH_TOKEN


def register_scoped_token(
    token: str,
    principal: AuthenticatedPrincipal,
    ttl_seconds: float | None = None,
) -> None:
    """Registers a scoped token for an explicit principal with optional TTL."""
    expires_at = time.time() + ttl_seconds if ttl_seconds is not None else None
    _SCOPED_TOKENS[token] = (principal, expires_at)


def revoke_token(token: str) -> bool:
    """
    Explicitly revokes an authentication token.
    Subsequent validation attempts will fail and revocation persists across restarts.
    """
    if not token or not token.strip():
        return False
    t_clean = token.strip()
    h = hash_token(t_clean)
    _REVOKED_TOKEN_HASHES.add(h)
    _save_revoked_hashes(_REVOKED_TOKEN_HASHES)
    _SCOPED_TOKENS.pop(t_clean, None)
    try:
        from foundation.auth.identity_service import get_identity_service
        get_identity_service().revoke_session(t_clean)
    except Exception:
        pass
    logger.info("Revoked authentication token [hash=%s]", h[:12])
    return True


def is_token_revoked(token: str) -> bool:
    """Checks whether a token has been explicitly revoked."""
    if not token:
        return True
    return hash_token(token.strip()) in _REVOKED_TOKEN_HASHES


def authenticate_token(token: str | None) -> AuthenticatedPrincipal | None:
    """
    Validates a bearer token and resolves the corresponding AuthenticatedPrincipal.
    Returns None if the token is missing, malformed, revoked, or expired.
    """
    if not token or not token.strip():
        return None

    cleaned = token.strip()
    if is_token_revoked(cleaned):
        return None

    # Check scoped tokens first
    if cleaned in _SCOPED_TOKENS:
        principal, expires_at = _SCOPED_TOKENS[cleaned]
        if expires_at is not None and time.time() > expires_at:
            # Token expired
            _SCOPED_TOKENS.pop(cleaned, None)
            logger.warning("Rejected expired scoped token for user %s", principal.user_id)
            return None
        return principal

    # Check persistent user sessions
    try:
        from foundation.auth.identity_service import get_identity_service
        id_svc = get_identity_service()
        session_principal = id_svc.validate_session(cleaned)
        if session_principal:
            return session_principal
    except Exception as e:
        logger.debug("Identity service session check note: %s", e)

    # Check primary system token
    expected = get_or_create_auth_token()
    if secrets.compare_digest(cleaned, expected.strip()):
        return get_default_owner_principal(token_hash=hash_token(cleaned))

    return None


def verify_token(token: str | None) -> bool:
    """
    Verifies that a token is valid, unrevoked, and unexpired.
    """
    return authenticate_token(token) is not None


# ── Network Interface & Origin Security ──────────────────────────────────────


def validate_complete_startup_policy(
    host: str | None,
    allow_remote: bool = False,
    token: str | None = None,
    allowed_hosts_raw: str | None = None,
    trusted_proxies_raw: str | None = None,
) -> tuple[bool, str]:
    """
    Definitive fail-closed startup validation for HELIX interface binding and host allowlists.
    Enforced during both CLI startup (scripts/start_backend.py) and ASGI lifespan startup (api/main.py).
    """
    raw_h = (host or "").strip()
    h_clean = raw_h.lower() if raw_h else "127.0.0.1"
    is_loopback = h_clean in ("127.0.0.1", "localhost", "::1")

    if is_loopback:
        return True, f"Safe loopback binding on {h_clean}"

    if not allow_remote:
        return False, (
            f"Binding to non-loopback host '{raw_h}' is prohibited without HELIX_ALLOW_REMOTE_ACCESS=true."
        )

    if not token or not token.strip():
        return False, "Remote access requires non-empty HELIX_AUTH_TOKEN."

    t_clean = token.strip()
    if len(t_clean) < 16:
        return False, f"HELIX_AUTH_TOKEN must be at least 16 characters long (got {len(t_clean)})."

    hosts = [h.strip() for h in (allowed_hosts_raw or "").split(",") if h.strip()]
    if not hosts:
        return False, "Remote access requires non-empty HELIX_ALLOWED_HOSTS."

    if any("*" in h for h in hosts):
        return False, "Wildcard '*' prohibited in HELIX_ALLOWED_HOSTS."

    for h in hosts:
        valid_h, h_reason = validate_single_host_authority(h)
        if not valid_h:
            return False, f"Invalid entry in HELIX_ALLOWED_HOSTS ('{h}'): {h_reason}"

    if trusted_proxies_raw and trusted_proxies_raw.strip():
        import ipaddress
        for entry in trusted_proxies_raw.split(","):
            e = entry.strip()
            if not e:
                continue
            try:
                if "/" in e:
                    ipaddress.ip_network(e, strict=False)
                else:
                    ipaddress.ip_address(e)
            except ValueError:
                return False, f"Malformed entry in HELIX_TRUSTED_PROXIES: '{e}'"

    return True, f"Authorized remote binding on {raw_h}"


def validate_interface_binding(
    host: str,
    allow_remote: bool = False,
    token: str | None = None,
) -> tuple[bool, str]:
    """
    Backwards compatibility alias for interface binding validation.
    """
    if not host or not host.strip():
        host = "127.0.0.1"
    raw_h = host.strip().lower()
    if raw_h.startswith("[") and raw_h.endswith("]"):
        raw_h = raw_h[1:-1]

    is_loopback = raw_h in ("127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1")
    if is_loopback:
        return True, f"Safe loopback binding on {raw_h}"

    if not allow_remote:
        return False, (
            f"Binding to non-loopback host '{raw_h}' is prohibited by default without HELIX_ALLOW_REMOTE_ACCESS=true."
        )

    if not token or not token.strip():
        return False, "Remote access requires a pre-configured, non-empty secret token (HELIX_AUTH_TOKEN)."

    if len(token.strip()) < 16:
        return False, f"HELIX_AUTH_TOKEN must be at least 16 characters long (got {len(token.strip())})."

    allowed_hosts = os.getenv("HELIX_ALLOWED_HOSTS", "")
    if allowed_hosts:
        return validate_complete_startup_policy(
            host=host,
            allow_remote=allow_remote,
            token=token,
            allowed_hosts_raw=allowed_hosts,
            trusted_proxies_raw=os.getenv("HELIX_TRUSTED_PROXIES", ""),
        )
    return True, f"Interface binding allowed on {raw_h}"


def validate_single_host_authority(authority: str) -> tuple[bool, str]:
    """
    Validates a single host authority string (host or host:port) per RFC 3986 / RFC 7230.
    """
    auth = authority.strip()
    if not auth:
        return False, "Empty authority"
    if "@" in auth:
        return False, "Userinfo ('@') prohibited in Host authority"
    if "/" in auth or "?" in auth or "#" in auth:
        return False, "Path, query, or fragment prohibited in Host authority"

    # Bracketed IPv6: [::1] or [::1]:8000
    if auth.startswith("["):
        closing = auth.find("]")
        if closing == -1:
            return False, "Unclosed IPv6 bracket in Host authority"
        ip_str = auth[1:closing]
        import ipaddress
        try:
            ipaddress.IPv6Address(ip_str)
        except ValueError:
            return False, f"Invalid IPv6 literal '{ip_str}'"
        port_part = auth[closing + 1:]
        if port_part:
            if not port_part.startswith(":"):
                return False, f"Invalid character sequence after IPv6 bracket: '{port_part}'"
            port_str = port_part[1:]
            if not port_str.isdigit():
                return False, f"Non-numeric port '{port_str}'"
            p = int(port_str)
            if p < 1 or p > 65535:
                return False, f"Port {p} out of valid range (1-65535)"
        return True, "Valid IPv6 authority"

    # Multi-colon unbracketed IPv6 rejected
    if auth.count(":") > 1:
        return False, "Unbracketed IPv6 authority prohibited (use '[...]:port')"

    # Hostname or IPv4 with optional port
    parts = auth.split(":")
    host_part = parts[0]
    if not host_part:
        return False, "Empty host component"

    import re
    if not re.match(r"^[a-zA-Z0-9.\-]+$", host_part):
        return False, f"Invalid characters in host '{host_part}'"
    if host_part.startswith(".") or host_part.endswith(".") or ".." in host_part:
        return False, f"Malformed dot syntax in host '{host_part}'"

    if len(parts) == 2:
        port_str = parts[1]
        if not port_str.isdigit():
            return False, f"Non-numeric port '{port_str}'"
        p = int(port_str)
        if p < 1 or p > 65535:
            return False, f"Port {p} out of valid range (1-65535)"

    return True, "Valid host authority"


def parse_allowed_hosts(allowed_hosts_raw: str | None = None) -> set[str]:
    """
    Parses and returns normalized allowed host authorities.
    Includes default loopback hosts unless explicitly configured otherwise.
    """
    raw = allowed_hosts_raw if allowed_hosts_raw is not None else os.getenv("HELIX_ALLOWED_HOSTS", "")
    hosts = set()
    default_loopback = {
        "localhost:8000",
        "localhost",
        "127.0.0.1:8000",
        "127.0.0.1",
        "[::1]:8000",
        "[::1]",
        "testserver",  # Starlette TestClient default
    }
    hosts.update(default_loopback)
    for entry in raw.split(","):
        e = entry.strip().lower()
        if e:
            hosts.add(e)
            if ":" not in e and not e.startswith("["):
                hosts.add(f"{e}:8000")
    return hosts


def evaluate_host_authority(
    headers: Any,
    client_ip: str | None = None,
    allowed_hosts: set[str] | None = None,
    trusted_proxies_raw: str | None = None,
) -> tuple[bool, str, str]:
    """
    Evaluates host authority from headers taking into account trusted proxies.
    Returns (is_valid, reason, effective_host).
    """
    hosts_set = allowed_hosts if allowed_hosts is not None else parse_allowed_hosts()
    trusted_raw = trusted_proxies_raw if trusted_proxies_raw is not None else os.getenv("HELIX_TRUSTED_PROXIES", "")

    # Parse trusted proxies
    trusted_ips: set[str] = set()
    trusted_nets = []
    if trusted_raw.strip():
        import ipaddress
        for p in trusted_raw.split(","):
            p_clean = p.strip()
            if not p_clean:
                continue
            try:
                if "/" in p_clean:
                    trusted_nets.append(ipaddress.ip_network(p_clean, strict=False))
                else:
                    trusted_ips.add(str(ipaddress.ip_address(p_clean)))
            except ValueError:
                pass

    peer_trusted = False
    if client_ip:
        import ipaddress
        try:
            c_addr = ipaddress.ip_address(client_ip)
            if str(c_addr) in trusted_ips:
                peer_trusted = True
            else:
                for net in trusted_nets:
                    if c_addr in net:
                        peer_trusted = True
                        break
        except ValueError:
            pass

    # Extract headers
    hdr_dict = {}
    if hasattr(headers, "get"):
        host_hdr = headers.get("host") or ""
        xfh_hdr = headers.get("x-forwarded-host")
        fwd_hdr = headers.get("forwarded")
    else:
        # Sequence of tuples
        for k, v in headers:
            k_str = k.decode("latin1").lower() if isinstance(k, bytes) else str(k).lower()
            v_str = v.decode("latin1") if isinstance(v, bytes) else str(v)
            hdr_dict[k_str] = v_str
        host_hdr = hdr_dict.get("host", "")
        xfh_hdr = hdr_dict.get("x-forwarded-host")
        fwd_hdr = hdr_dict.get("forwarded")

    effective_host = host_hdr.strip()

    if peer_trusted:
        # Check conflicting proxy headers
        forwarded_val = None
        if fwd_hdr:
            for part in fwd_hdr.split(";"):
                part = part.strip()
                if part.lower().startswith("host="):
                    forwarded_val = part[5:].strip().strip('"')
                    break

        if xfh_hdr and forwarded_val:
            xfh_first = xfh_hdr.split(",")[0].strip().lower()
            if xfh_first != forwarded_val.lower():
                return False, "Conflicting forwarding headers (X-Forwarded-Host vs Forwarded)", effective_host

        if xfh_hdr:
            effective_host = xfh_hdr.split(",")[0].strip()
        elif forwarded_val:
            effective_host = forwarded_val

    if not effective_host:
        return False, "Missing Host header", ""

    # Validate syntax
    valid_syntax, syntax_reason = validate_single_host_authority(effective_host)
    if not valid_syntax:
        return False, syntax_reason, effective_host

    eff_clean = effective_host.lower()
    if eff_clean in hosts_set:
        return True, "Host authority authorized", eff_clean

    # If port is standard HTTP/HTTPS (80 or 443), check host without port
    if ":" in eff_clean and not eff_clean.startswith("["):
        parts = eff_clean.split(":", 1)
        if parts[1] in ("80", "443") and parts[0] in hosts_set:
            return True, "Host authorized on standard port", eff_clean

    return False, f"Host '{effective_host}' not in allowed hosts", effective_host


def get_csp_header(env_mode: str | None = None) -> str:
    """
    Returns strict, perimeter-hardened Content-Security-Policy header.
    Wildcard 'ws:' and 'wss:' are completely removed.
    """
    mode = env_mode or os.getenv("HELIX_ENV", "production").lower()
    is_dev = mode in ("development", "dev")

    if is_dev:
        return (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-eval'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "connect-src 'self' ws://localhost:8000 ws://127.0.0.1:8000 ws://localhost:3000 ws://127.0.0.1:3000 http://localhost:8000 http://127.0.0.1:8000; "
            "frame-ancestors 'none'; "
            "object-src 'none'; "
            "base-uri 'self';"
        )

    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self' ws://localhost:8000 ws://127.0.0.1:8000 http://localhost:8000 http://127.0.0.1:8000; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "base-uri 'self';"
    )


def get_allowed_origins() -> list[str]:
    """Retrieves normalized allowed CORS / WebSocket origins."""
    env_origins = os.getenv("HELIX_CORS_ORIGINS", "")
    origins = [o.strip() for o in env_origins.split(",") if o.strip()]
    default_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    for d in default_origins:
        if d not in origins:
            origins.append(d)
    return origins


def is_allowed_origin(origin: str | None, allowed_origins: list[str] | None = None) -> bool:
    """
    Validates the Origin header against the whitelist to prevent cross-origin abuse.
    If no origin is passed (e.g. non-browser direct API tool), returns True.
    If origin is passed, it MUST match an entry in allowed_origins.
    """
    if not origin or not origin.strip():
        return True

    allowed = allowed_origins if allowed_origins is not None else get_allowed_origins()
    normalized_allowed = {a.strip().rstrip("/").lower() for a in allowed}

    norm_origin = origin.strip().rstrip("/").lower()
    return norm_origin in normalized_allowed
