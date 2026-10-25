import os
import sys
import ctypes
import hashlib
import hmac
import secrets
import logging
import threading
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidTag

logger = logging.getLogger("helix.crypto")

_HEADER_V1 = b"HLXENCv1"
_HEADER_V2 = b"HLXENCv2"
_HEADER = _HEADER_V2  # Active default format header

SALT_LEN_V2 = 16
NONCE_LEN_V2 = 12  # 96-bit standard nonce for AES-GCM
TAG_LEN_V2 = 16    # 128-bit authentication tag
MIN_ENVELOPE_LEN_V2 = len(_HEADER_V2) + SALT_LEN_V2 + NONCE_LEN_V2 + TAG_LEN_V2  # 52 bytes

_MASTER_KEY_CACHE: bytes | None = None
_MASTER_KEY_LOCK = threading.Lock()


def clear_master_key_cache() -> None:
    """Clear in-memory master key cache (primarily for tests)."""
    global _MASTER_KEY_CACHE
    with _MASTER_KEY_LOCK:
        _MASTER_KEY_CACHE = None


class CryptographicError(RuntimeError):
    """Base exception for cryptographic operations and failures."""
    pass


class CryptographicIntegrityError(CryptographicError, InvalidTag):
    """Raised when authentication tag verification fails, payload is tampered, or envelope is corrupted."""
    pass


class UnsupportedCipherVersionError(CryptographicError):
    """Raised when ciphertext specifies an unsupported or unknown format version."""
    pass


class TruncatedCiphertextError(CryptographicIntegrityError):
    """Raised when ciphertext payload is shorter than the minimum envelope size."""
    pass


def _get_or_create_key() -> bytes:
    """
    Retrieve or create the master storage key.
    Checks HELIX_SECRET_KEY env var (hashed with SHA-256),
    then checks get_helix_home() / ".secret.key", or creates a 32-byte CSPRNG key file.
    """
    global _MASTER_KEY_CACHE
    if _MASTER_KEY_CACHE is not None:
        return _MASTER_KEY_CACHE

    with _MASTER_KEY_LOCK:
        if _MASTER_KEY_CACHE is not None:
            return _MASTER_KEY_CACHE

        env_key = os.getenv("HELIX_SECRET_KEY")
        if env_key:
            _MASTER_KEY_CACHE = hashlib.sha256(env_key.encode("utf-8")).digest()
            return _MASTER_KEY_CACHE

        try:
            from foundation.paths import get_helix_home
            key_file = get_helix_home() / ".secret.key"
        except Exception:
            key_file = Path.home() / ".helix" / ".secret.key"

        if key_file.exists():
            try:
                _MASTER_KEY_CACHE = key_file.read_bytes()
                return _MASTER_KEY_CACHE
            except Exception:
                pass

        fallback_file = Path.home() / ".helix" / ".secret.key"
        if fallback_file != key_file and fallback_file.exists():
            try:
                _MASTER_KEY_CACHE = fallback_file.read_bytes()
                return _MASTER_KEY_CACHE
            except Exception:
                pass

        key = secrets.token_bytes(32)
        try:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_bytes(key)
            try:
                key_file.chmod(0o600)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to persist master key to %s: %s", key_file, e)

        _MASTER_KEY_CACHE = key
        return _MASTER_KEY_CACHE


def derive_record_key(master_key: bytes, salt: bytes, context: str | None = None) -> bytes:
    """
    Derive a 256-bit symmetric encryption key using HKDF-SHA256 with explicit domain separation.
    """
    info = (
        f"HELIX-AEAD-AES256GCM-v2::{context}".encode("utf-8")
        if context
        else b"HELIX-AEAD-AES256GCM-v2"
    )
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=info,
    )
    return hkdf.derive(master_key)


# Legacy POSIX keystream implementation
def _xor_keystream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray(len(data))
    block_idx = 0
    offset = 0
    while offset < len(data):
        block_key = hmac.new(key, nonce + block_idx.to_bytes(8, "big"), hashlib.sha256).digest()
        chunk_len = min(len(block_key), len(data) - offset)
        for i in range(chunk_len):
            out[offset + i] = data[offset + i] ^ block_key[i]
        offset += chunk_len
        block_idx += 1
    return bytes(out)


def _stream_protect(data: bytes) -> bytes:
    if not data:
        return b""
    key = _get_or_create_key()
    nonce = secrets.token_bytes(16)
    ciphertext = _xor_keystream(data, key, nonce)
    tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    return nonce + tag + ciphertext


def _stream_unprotect(data: bytes) -> bytes:
    if not data:
        return b""
    if len(data) < 48:
        return data
    nonce = data[:16]
    tag = data[16:48]
    ciphertext = data[48:]
    key = _get_or_create_key()
    expected_tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        return data
    return _xor_keystream(ciphertext, key, nonce)


if sys.platform == "win32":
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ('cbData', wintypes.DWORD),
            ('pbData', ctypes.POINTER(ctypes.c_byte))
        ]

    def _win32_protect(data: bytes) -> bytes:
        if not data:
            return b""
        buffer = ctypes.create_string_buffer(data)
        data_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        data_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(data_in), "HELIX_DPAPI", None, None, None, 0, ctypes.byref(data_out)
        ):
            encrypted = ctypes.string_at(data_out.pbData, data_out.cbData)
            ctypes.windll.kernel32.LocalFree(data_out.pbData)
            return encrypted
        raise RuntimeError("CryptProtectData failed")

    def _win32_unprotect(data: bytes) -> bytes:
        if not data:
            return b""
        buffer = ctypes.create_string_buffer(data)
        data_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        data_out = DATA_BLOB()
        if ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(data_in), None, None, None, None, 0, ctypes.byref(data_out)
        ):
            decrypted = ctypes.string_at(data_out.pbData, data_out.cbData)
            ctypes.windll.kernel32.LocalFree(data_out.pbData)
            return decrypted
        raise RuntimeError("CryptUnprotectData failed")

    _protect_impl = _win32_protect
    _unprotect_impl = _win32_unprotect
else:
    _protect_impl = _stream_protect
    _unprotect_impl = _stream_unprotect


def _aead_protect(data: bytes, aad: bytes | None = None, context: str | None = None) -> bytes:
    master_key = _get_or_create_key()
    salt = secrets.token_bytes(SALT_LEN_V2)
    nonce = secrets.token_bytes(NONCE_LEN_V2)
    record_key = derive_record_key(master_key, salt, context=context)

    # AAD binds format header, salt, and optional caller metadata
    effective_aad = _HEADER_V2 + salt + (aad if aad else b"")
    aesgcm = AESGCM(record_key)
    ciphertext_and_tag = aesgcm.encrypt(nonce, data, effective_aad)
    return _HEADER_V2 + salt + nonce + ciphertext_and_tag


def _aead_unprotect(data: bytes, aad: bytes | None = None, context: str | None = None) -> bytes:
    if not data.startswith(_HEADER_V2):
        raise UnsupportedCipherVersionError(f"Data does not start with {_HEADER_V2!r}")

    if len(data) < MIN_ENVELOPE_LEN_V2:
        raise TruncatedCiphertextError(
            f"Truncated HLXENCv2 payload: length {len(data)} is less than minimum envelope {MIN_ENVELOPE_LEN_V2}"
        )

    header_len = len(_HEADER_V2)
    salt = data[header_len : header_len + SALT_LEN_V2]
    nonce_offset = header_len + SALT_LEN_V2
    nonce = data[nonce_offset : nonce_offset + NONCE_LEN_V2]
    ciphertext_and_tag = data[nonce_offset + NONCE_LEN_V2 :]

    master_key = _get_or_create_key()
    record_key = derive_record_key(master_key, salt, context=context)
    effective_aad = _HEADER_V2 + salt + (aad if aad else b"")

    try:
        aesgcm = AESGCM(record_key)
        return aesgcm.decrypt(nonce, ciphertext_and_tag, effective_aad)
    except InvalidTag as e:
        raise CryptographicIntegrityError(
            "Cryptographic authentication failure: AES-256-GCM authentication tag verification failed"
        ) from e


def encrypt_bytes(
    data: bytes,
    aad: bytes | None = None,
    context: str | None = None,
    version: int | None = None,
) -> bytes:
    """
    Encrypt raw bytes.
    Default version: 2 (Standard AEAD AES-256-GCM with HKDF key separation and AAD).
    Legacy version 1 (HLXENCv1) supported for backward-compatibility or explicit request.
    """
    if version is None:
        env_ver = os.getenv("HELIX_CRYPTO_VERSION", "2").strip().lower()
        version = 1 if env_ver in ("1", "v1") else 2

    if not data:
        return _HEADER_V2 if version == 2 else _HEADER_V1

    if version == 2:
        return _aead_protect(data, aad=aad, context=context)
    elif version == 1:
        encrypted = _protect_impl(data)
        return _HEADER_V1 + encrypted
    else:
        raise UnsupportedCipherVersionError(f"Unsupported encryption version requested: {version}")


def decrypt_bytes(
    data: bytes,
    aad: bytes | None = None,
    context: str | None = None,
) -> bytes:
    """
    Decrypt binary data.
    - If HLXENCv2 header is present, validates AAD, tag, and decrypts via AES-256-GCM.
    - If HLXENCv1 header is present, decrypts via legacy platform cipher.
    - If unknown HLXENC header is present, raises UnsupportedCipherVersionError.
    - Otherwise returns raw data (legacy unencrypted plaintext compatibility).
    """
    if not data:
        return b""

    # Empty payload sentinels
    if data == _HEADER_V2 or data == _HEADER_V1:
        return b""

    if data.startswith(_HEADER_V2):
        return _aead_unprotect(data, aad=aad, context=context)
    elif data.startswith(_HEADER_V1):
        payload = data[len(_HEADER_V1) :]
        if not payload:
            return b""
        return _unprotect_impl(payload)
    elif data.startswith(b"HLXENC"):
        raise UnsupportedCipherVersionError(f"Unknown or unsupported ciphertext header: {data[:8]!r}")

    # Legacy unencrypted plaintext fallback
    return data


def encrypt_string(
    text: str,
    aad: bytes | None = None,
    context: str | None = None,
    version: int | None = None,
) -> bytes:
    return encrypt_bytes(text.encode("utf-8"), aad=aad, context=context, version=version)


def decrypt_string(
    data: bytes,
    aad: bytes | None = None,
    context: str | None = None,
) -> str:
    raw = decrypt_bytes(data, aad=aad, context=context)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise CryptographicIntegrityError(f"Decrypted payload is not valid UTF-8: {e}") from e


def get_cipher_version(data: bytes) -> int | None:
    """Inspect binary data and return detected format version (1, 2, or None)."""
    if not data:
        return None
    if data.startswith(_HEADER_V2):
        return 2
    if data.startswith(_HEADER_V1):
        return 1
    return None


def is_encrypted_bytes(data: bytes) -> bool:
    """Check if binary data begins with any recognized HELIX encryption header."""
    if not data:
        return False
    return data.startswith(_HEADER_V2) or data.startswith(_HEADER_V1)
