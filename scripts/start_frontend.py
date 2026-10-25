import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
UI_DIR = ROOT / "frontend"


def main():
    load_dotenv(ROOT / ".env")
    print("Starting HELIX frontend (Next.js)...")

    port = os.getenv("HELIX_UI_PORT", "3000")
    host = os.getenv("HELIX_UI_HOST", "localhost")

    result = subprocess.run(
        ["npx", "next", "dev", "-p", port, "-H", host],
        cwd=str(UI_DIR),
        shell=True,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
