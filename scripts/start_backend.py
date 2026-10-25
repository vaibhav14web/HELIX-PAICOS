import os
import sys
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent


def main():
    load_dotenv(ROOT / ".env")
    sys.path.insert(0, str(ROOT))

    from foundation.auth import validate_complete_startup_policy

    host = os.getenv("HELIX_API_HOST", "127.0.0.1").strip()
    allow_remote = os.getenv("HELIX_ALLOW_REMOTE_ACCESS", "").lower() in ("true", "1", "yes")
    token = os.getenv("HELIX_AUTH_TOKEN", "").strip()
    allowed_hosts = os.getenv("HELIX_ALLOWED_HOSTS", "").strip()
    trusted_proxies = os.getenv("HELIX_TRUSTED_PROXIES", "").strip()

    valid, reason = validate_complete_startup_policy(
        host=host,
        allow_remote=allow_remote,
        token=token,
        allowed_hosts_raw=allowed_hosts,
        trusted_proxies_raw=trusted_proxies,
    )
    if not valid:
        print(f"[SECURITY FATAL] Interface binding rejected: {reason}", file=sys.stderr)
        sys.exit(1)

    port = int(os.getenv("HELIX_API_PORT", "8000"))
    reload_enabled = os.getenv("HELIX_API_RELOAD", "true").lower() in ("true", "1", "yes")

    print(f"Starting HELIX API server on {host}:{port} ({reason}) ...")
    uvicorn.run(
        "api.main:app",
        host=host,
        port=port,
        reload=reload_enabled,
    )


if __name__ == "__main__":
    main()
