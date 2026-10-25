import os
import httpx
from pathlib import Path
import time

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from foundation.paths import get_models_dir

base_url = "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/"
files = ["en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx.json"]
dest_dir = get_models_dir() / "PIPER"
print(f"Target directory resolved to: {dest_dir}")

dest_dir.mkdir(parents=True, exist_ok=True)
max_retries = 10
retry_delay = 5

for file in files:
    url = base_url + file
    dest_path = dest_dir / file
    print(f"Resumable download for {file} from {url}")
    
    downloaded = 0
    if dest_path.exists():
        downloaded = dest_path.stat().st_size
        print(f"Found existing size: {downloaded / (1024*1024):.2f} MB")
        
    for attempt in range(max_retries):
        try:
            headers = {}
            if downloaded > 0:
                headers["Range"] = f"bytes={downloaded}-"
                mode = "ab"
            else:
                mode = "wb"
                
            with httpx.Client(timeout=25.0) as client:
                with client.stream("GET", url, headers=headers, follow_redirects=True) as r:
                    if r.status_code == 200 and downloaded > 0:
                        print("Server ignored range requests. Restarting from scratch...")
                        downloaded = 0
                        mode = "wb"
                    elif r.status_code not in (200, 206):
                        r.raise_for_status()
                    
                    content_range = r.headers.get("content-range")
                    total_bytes = 0
                    if content_range:
                        total_bytes = int(content_range.split("/")[-1])
                    else:
                        total_bytes = int(r.headers.get("content-length", 0)) + downloaded
                        
                    print(f"Total target size: {total_bytes / (1024*1024):.2f} MB")
                    
                    if downloaded >= total_bytes and total_bytes > 0:
                        print(f"{file} is already fully downloaded.")
                        break
                        
                    with open(dest_path, mode) as f:
                        for chunk in r.iter_bytes(chunk_size=262144):
                            f.write(chunk)
                            f.flush()
                            downloaded += len(chunk)
                            percent = (downloaded / total_bytes) * 100 if total_bytes > 0 else 0
                            print(f"Progress ({file}): {downloaded/(1024*1024):.2f} / {total_bytes/(1024*1024):.2f} MB ({percent:.1f}%)", end="\r", flush=True)
                    print(f"\nSuccessfully downloaded {file}")
                    break
        except (httpx.HTTPError, OSError) as e:
            print(f"\nConnection issue ({file}): {e}. Retrying in {retry_delay}s...", flush=True)
            time.sleep(retry_delay)
            if dest_path.exists():
                downloaded = dest_path.stat().st_size
