import urllib.request
import os
from pathlib import Path
import time

url = "https://developer.download.nvidia.com/compute/cuda/12.1.0/local_installers/cuda_12.1.0_531.14_windows.exe"
project_root = Path(__file__).resolve().parents[1]
dest = Path(os.getenv("HELIX_CUDA_INSTALLER_PATH", str(project_root / "cuda_12.1_installer.exe")))

print(f"Resumable download using urllib from {url}")
print(f"Saving to {dest}")

downloaded = 0
if dest.exists():
    downloaded = dest.stat().st_size
    print(f"Found existing partial file of size: {downloaded / (1024*1024):.2f} MB")

max_retries = 15
retry_delay = 5

for attempt in range(max_retries):
    try:
        req = urllib.request.Request(url)
        if downloaded > 0:
            req.add_header("Range", f"bytes={downloaded}-")
            mode = "ab"
        else:
            mode = "wb"
            
        print(f"Connecting (attempt {attempt+1}/{max_retries})...")
        
        with urllib.request.urlopen(req, timeout=30) as response:
            status_code = response.getcode()
            
            if status_code == 200 and downloaded > 0:
                print("Server does not support range requests. Restarting from scratch...")
                downloaded = 0
                mode = "wb"
                
            content_length = response.headers.get("Content-Length")
            total_bytes = int(content_length) + downloaded if content_length else downloaded
            
            print(f"Total target size: {total_bytes / (1024*1024):.2f} MB")
            
            with open(dest, mode) as f:
                block_size = 1024 * 1024
                while True:
                    buffer = response.read(block_size)
                    if not buffer:
                        break
                    f.write(buffer)
                    f.flush()
                    downloaded += len(buffer)
                    percent = (downloaded / total_bytes) * 100 if total_bytes > 0 else 0
                    print(f"Downloaded: {downloaded / (1024*1024):.2f} MB ({percent:.1f}%)", flush=True)
                    
            print("Download completed successfully!")
            break
            
    except Exception as e:
        print(f"Connection issue encountered: {e}. Retrying in {retry_delay}s...", flush=True)
        time.sleep(retry_delay)
        if dest.exists():
            downloaded = dest.stat().st_size
