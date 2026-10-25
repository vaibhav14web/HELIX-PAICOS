"""
HELIX Directory Inspector Engine.

Analyzes any requested directory or codebase on the user's laptop,
extracts folder trees, detects technology stacks and manifests, reads documentation,
registers the directory in ProjectRegistry, and indexes its knowledge into HELIX Warm Memory (Qdrant).
"""
import os
import sys
import time
from pathlib import Path
from typing import Any
import logging

logger = logging.getLogger("helix.directory_inspector")

_SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", "venv", ".venv",
    ".pytest_cache", ".mypy_cache", ".next", ".nuxt", "dist", "build",
    "$recycle.bin", "system volume information", "appdata"
}

_BLOCKED_SYSTEM_PATHS = {
    "c:\\windows", "c:\\program files", "c:\\program files (x86)",
    "c:\\programdata\\microsoft", "system32"
}

_MANIFEST_FRAMEWORKS = {
    "package.json": ("JavaScript/TypeScript", ["Node.js"]),
    "requirements.txt": ("Python", ["Python Packages"]),
    "pyproject.toml": ("Python", ["Poetry/Packaging"]),
    "setup.py": ("Python", ["Setuptools"]),
    "docker-compose.yml": ("DevOps", ["Docker Compose"]),
    "Dockerfile": ("DevOps", ["Docker"]),
    "Cargo.toml": ("Rust", ["Cargo"]),
    "go.mod": ("Go", ["Go Modules"]),
    "pom.xml": ("Java", ["Maven"]),
    "build.gradle": ("Java/Kotlin", ["Gradle"]),
    "vite.config.js": ("Frontend", ["Vite"]),
    "vite.config.ts": ("Frontend", ["Vite"]),
    "next.config.js": ("Frontend", ["Next.js"]),
    "next.config.mjs": ("Frontend", ["Next.js"]),
    "tailwind.config.js": ("CSS", ["Tailwind CSS"]),
}


class DirectoryInspector:
    def __init__(self, event_bus: Any | None = None, api_base_url: str = "http://127.0.0.1:8000"):
        self._event_bus = event_bus
        self._api_base_url = api_base_url

    def _resolve_user_folder(self, user_profile: Path, winreg_name: str, subpath_name: str) -> Path:
        """Resolves standard or redirected user folder (Documents, Desktop, Downloads)."""
        if sys.platform == "win32":
            try:
                import winreg
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                ) as key:
                    val, _ = winreg.QueryValueEx(key, winreg_name)
                    expanded = Path(os.path.expandvars(val))
                    if expanded.exists():
                        return expanded
            except Exception:
                pass

        onedrive_cand = user_profile / "OneDrive" / subpath_name
        if onedrive_cand.exists():
            return onedrive_cand

        standard_cand = user_profile / subpath_name
        if standard_cand.exists():
            return standard_cand

        return standard_cand

    def resolve_path(self, path_input: str) -> Path | None:
        """Resolve a user-provided string, alias, or project name to an existing Path."""
        clean = path_input.strip().strip("'\"").strip()
        if not clean:
            return None

        from foundation.paths import get_user_home
        user_profile = get_user_home()
        repo_root = Path(__file__).resolve().parents[2]

        ai_hms_env = os.getenv("HELIX_AI_HMS_PATH")
        ai_hms_candidates = [
            Path(ai_hms_env) if ai_hms_env else None,
            user_profile / "AI_HMS",
            user_profile / "Documents" / "AI_HMS",
            Path(r"C:\AI_HMS") if os.name == "nt" else None,
        ]
        ai_hms_dir = next((p for p in ai_hms_candidates if p and p.exists()), Path(r"C:\AI_HMS") if os.name == "nt" else repo_root)

        docs_dir = self._resolve_user_folder(user_profile, "Personal", "Documents")
        desktop_dir = self._resolve_user_folder(user_profile, "Desktop", "Desktop")
        downloads_dir = self._resolve_user_folder(user_profile, "{374DE290-123F-4565-9164-39C4925E467B}", "Downloads")

        aliases = {
            "documents": docs_dir,
            "docs": docs_dir,
            "desktop": desktop_dir,
            "downloads": downloads_dir,
            "ai-hms": ai_hms_dir,
            "ai_hms": ai_hms_dir,
            "hms": ai_hms_dir,
            "medisense": ai_hms_dir,
            "helix": repo_root,
            "helix-main": repo_root,
        }

        lower_key = clean.lower().replace("/", "\\")
        if lower_key in aliases:
            cand = aliases[lower_key]
            if cand.exists():
                return cand.resolve()

        p = Path(clean).resolve()
        if p.exists() and p.is_dir():
            return p

        # Check in discovered document parents
        parent_candidates = [docs_dir, user_profile / "OneDrive" / "Documents", user_profile / "Documents"]
        if os.name == "nt":
            parent_candidates.append(Path(r"C:\\"))
        for parent in parent_candidates:
            try:
                if parent.exists():
                    cand = (parent / clean).resolve()
                    if cand.exists() and cand.is_dir():
                        return cand
            except (OSError, ValueError):
                pass

        return None

    def inspect(self, target_input: str, max_depth: int = 3, index_to_memory: bool = True) -> dict[str, Any]:
        """Perform a full structural and semantic inspection of the directory."""
        resolved = self.resolve_path(target_input)
        if not resolved:
            return {
                "success": False,
                "error": f"Directory '{target_input}' could not be found on your laptop.",
                "path": target_input,
            }

        resolved_str = str(resolved).lower()
        if any(b in resolved_str for b in _BLOCKED_SYSTEM_PATHS):
            return {
                "success": False,
                "error": f"Security restriction: Access to system directory '{resolved}' is blocked.",
                "path": str(resolved),
            }

        tree_lines = []
        files_by_ext: dict[str, int] = {}
        detected_manifests = []
        detected_frameworks = set()
        detected_languages = set()
        readme_content = ""
        key_entrypoints = []

        # Build clean directory tree
        def _scan(curr: Path, depth: int, prefix: str = ""):
            if depth > max_depth:
                return
            try:
                items = sorted(curr.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            except (PermissionError, OSError):
                return

            subdirs = [i for i in items if i.is_dir() and i.name.lower() not in _SKIP_DIRS and not i.name.startswith(".")]
            subfiles = [i for i in items if i.is_file() and not i.name.startswith((".", "~$"))]

            for d in subdirs:
                tree_lines.append(f"{prefix}📁 {d.name}/")
                _scan(d, depth + 1, prefix + "  ")

            for f in subfiles:
                ext = f.suffix.lower()
                files_by_ext[ext] = files_by_ext.get(ext, 0) + 1

                if f.name in _MANIFEST_FRAMEWORKS:
                    detected_manifests.append(f.name)
                    lang, fws = _MANIFEST_FRAMEWORKS[f.name]
                    detected_languages.add(lang)
                    detected_frameworks.update(fws)

                if f.name.lower() in ("main.py", "app.py", "index.ts", "index.js", "app.jsx", "main.jsx", "server.py", "api.py"):
                    key_entrypoints.append(str(f.relative_to(resolved)))

                if depth <= 2:
                    tree_lines.append(f"{prefix}📄 {f.name}")

        _scan(resolved, 1)

        # Detect languages from extensions
        ext_to_lang = {
            ".py": "Python", ".ts": "TypeScript", ".tsx": "React / TypeScript",
            ".js": "JavaScript", ".jsx": "React / JavaScript", ".sql": "SQL",
            ".html": "HTML", ".css": "CSS", ".rs": "Rust", ".go": "Go",
            ".java": "Java", ".cpp": "C++", ".c": "C", ".json": "JSON", ".md": "Markdown"
        }
        for ext, count in files_by_ext.items():
            if ext in ext_to_lang and count > 0:
                detected_languages.add(ext_to_lang[ext])

        # Read README if present
        for readme_name in ["README.md", "readme.md", "README", "architecture.md", "docs/architecture-and-decisions.md"]:
            cand = resolved / readme_name
            if cand.exists() and cand.is_file():
                try:
                    readme_content = cand.read_text(encoding="utf-8", errors="ignore")[:3000]
                    break
                except Exception:
                    pass

        # Format markdown summary
        folder_name = resolved.name
        langs_str = ", ".join(sorted(detected_languages)) or "General"
        fws_str = ", ".join(sorted(detected_frameworks)) or "Standard"
        tree_preview = "\n".join(tree_lines[:40])
        if len(tree_lines) > 40:
            tree_preview += f"\n... and {len(tree_lines) - 40} more items."

        summary_markdown = (
            f"### Directory Analysis: `{folder_name}`\n\n"
            f"- **Path:** `{resolved}`\n"
            f"- **Detected Languages:** {langs_str}\n"
            f"- **Frameworks / Manifests:** {fws_str} ({', '.join(detected_manifests) or 'none'})\n"
            f"- **Key Entrypoints:** {', '.join(f'`{ep}`' for ep in key_entrypoints) or 'None identified'}\n\n"
            f"#### Folder Structure:\n```text\n{tree_preview}\n```\n"
        )
        if readme_content:
            first_para = readme_content.split("\n\n")[0].strip()
            if len(first_para) > 500:
                first_para = first_para[:500] + "..."
            summary_markdown += f"\n#### Project Description:\n{first_para}\n"

        # Index into Warm Memory if requested
        indexed_count = 0
        if index_to_memory:
            indexed_count = self._index_summary_to_memory(resolved, folder_name, summary_markdown, readme_content)

        return {
            "success": True,
            "path": str(resolved),
            "folder_name": folder_name,
            "languages": list(detected_languages),
            "frameworks": list(detected_frameworks),
            "manifests": detected_manifests,
            "entrypoints": key_entrypoints,
            "summary_markdown": summary_markdown,
            "indexed_entries": indexed_count,
        }

    def _index_summary_to_memory(self, path: Path, folder_name: str, summary: str, readme: str) -> int:
        """Embeds summary and README into HELIX's Warm Memory vector store."""
        import requests
        index_url = f"{self._api_base_url}/memory/warm/index"
        indexed = 0

        # Index the structured overview
        try:
            res = requests.post(
                index_url,
                json={
                    "text": f"Directory Context for {folder_name} at {path}:\n{summary}",
                    "category": "project_knowledge",
                    "metadata": {"directory": str(path), "folder": folder_name, "type": "directory_structure"},
                },
                timeout=5,
            )
            if res.status_code == 200:
                indexed += 1
        except Exception as e:
            logger.debug("Failed to index directory overview to warm memory: %s", e)

        # Index README if present
        if readme:
            try:
                res = requests.post(
                    index_url,
                    json={
                        "text": f"Project README for {folder_name} at {path}:\n{readme[:2000]}",
                        "category": "project_knowledge",
                        "metadata": {"directory": str(path), "folder": folder_name, "type": "readme"},
                    },
                    timeout=5,
                )
                if res.status_code == 200:
                    indexed += 1
            except Exception as e:
                logger.debug("Failed to index README to warm memory: %s", e)

        return indexed
