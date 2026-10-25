"""
Start HELIX (backend + frontend) in parallel.
Press Ctrl+C to stop both.
"""
import os
import subprocess
import sys
import signal
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")


def main():
    backend_port = os.getenv("HELIX_API_PORT", "8000")
    frontend_port = os.getenv("HELIX_UI_PORT", "3000")

    print("=" * 50)
    print("  HELIX — Starting Backend + Frontend")
    print(f"  Backend:  http://localhost:{backend_port}")
    print(f"  Frontend: http://localhost:{frontend_port}")
    print("=" * 50)

    backend = subprocess.Popen(
        [sys.executable, "-m", "scripts.start_backend"],
        cwd=str(ROOT),
    )

    frontend = subprocess.Popen(
        ["npx", "next", "dev", "-p", frontend_port],
        cwd=str(ROOT / "frontend"),
        shell=True,
    )

    def cleanup(sig, frame):
        print("\nShutting down HELIX...")
        backend.terminate()
        frontend.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    try:
        backend.wait()
        frontend.wait()
    except KeyboardInterrupt:
        cleanup(None, None)


if __name__ == "__main__":
    main()
