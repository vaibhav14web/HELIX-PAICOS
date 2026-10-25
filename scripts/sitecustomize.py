import sys
import os
from pathlib import Path


def _ensure_repo_root_on_path() -> None:
    current_dir = Path(__file__).resolve().parent
    repo_root = current_dir.parent
    repo_root_str = str(repo_root)
    current_dir_str = str(current_dir)

    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)

    if current_dir_str in sys.path and current_dir_str != repo_root_str:
        sys.path.remove(current_dir_str)
        sys.path.insert(1, current_dir_str)


def _ensure_cuda_dlls_on_path() -> None:
    # 1. Check CUDA_PATH env var
    cuda_path_env = os.getenv("CUDA_PATH")
    if cuda_path_env:
        cuda_bin = Path(cuda_path_env) / "bin"
        if cuda_bin.exists():
            try:
                os.add_dll_directory(str(cuda_bin))
                print(f"[sitecustomize] Added CUDA dll directory from CUDA_PATH: {cuda_bin}")
            except Exception:
                pass

    # 2. Check standard installation directories for CUDA 12.x fallback
    base_dir = Path(r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA")
    if base_dir.exists():
        for sub in base_dir.iterdir():
            cuda_bin = sub / "bin"
            if cuda_bin.exists():
                try:
                    os.add_dll_directory(str(cuda_bin))
                    print(f"[sitecustomize] Added CUDA dll directory: {cuda_bin}")
                except Exception:
                    pass


_ensure_repo_root_on_path()
if sys.platform == "win32":
    _ensure_cuda_dlls_on_path()
