import os
import asyncio
import logging
import time
import tempfile
from pathlib import Path
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.file_intelligence_engine")

# Directories to always skip during scans
_DEFAULT_IGNORE = {
    ".git", ".svn", ".hg",
    "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".venv", "venv", ".env",
    ".next", ".nuxt", "dist", "build",
    ".tox", ".eggs", "*.egg-info",
}

# Map marker files to project types
_PROJECT_MARKERS: dict[str, str] = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "requirements.txt": "python",
    "Pipfile": "python",
    "package.json": "node",
    "tsconfig.json": "typescript",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "pom.xml": "java-maven",
    "build.gradle": "java-gradle",
    "CMakeLists.txt": "cmake",
    "Makefile": "make",
    ".sln": "dotnet",
    ".csproj": "dotnet",
    "composer.json": "php",
    "Gemfile": "ruby",
}

# Map file extensions to language names
_EXTENSION_LANGUAGES: dict[str, str] = {
    ".py": "Python", ".pyw": "Python",
    ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".jsx": "React",
    ".java": "Java",
    ".cs": "C#",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++",
    ".c": "C", ".h": "C/C++",
    ".rs": "Rust",
    ".go": "Go",
    ".rb": "Ruby",
    ".php": "PHP",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".r": "R", ".R": "R",
    ".m": "MATLAB/Objective-C",
    ".sql": "SQL",
    ".html": "HTML", ".htm": "HTML",
    ".css": "CSS", ".scss": "SCSS", ".sass": "Sass", ".less": "Less",
    ".json": "JSON",
    ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
    ".md": "Markdown", ".rst": "reStructuredText",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".ps1": "PowerShell",
    ".bat": "Batch", ".cmd": "Batch",
    ".dockerfile": "Docker",
}


class FileIntelligenceEngine:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        self._watch_paths = [
            Path(p) for p in os.getenv("HELIX_FILE_WATCH_PATHS", ".").split(";") if p
        ]
        self._ignore_dirs = set(
            os.getenv("HELIX_FILE_IGNORE_DIRS", "").split(";")
        ) | _DEFAULT_IGNORE
        self._ignore_dirs.discard("")
        self._recent_minutes = int(os.getenv("HELIX_FILE_RECENT_MINUTES", "30"))
        self._max_depth = int(os.getenv("HELIX_FILE_MAX_DEPTH", "4"))
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}

        allowed_env = os.getenv("HELIX_ALLOWED_READ_PATHS", "")
        self._allowed_paths: list[Path] = [
            Path.cwd().resolve(),
            (Path.cwd() / "data").resolve(),
            Path(tempfile.gettempdir()).resolve(),
        ]
        for p in self._watch_paths:
            try:
                self._allowed_paths.append(p.resolve())
            except Exception:
                pass
        if allowed_env:
            from foundation.paths import expand_path
            for p in allowed_env.split(";"):
                if p.strip():
                    try:
                        exp = expand_path(p.strip())
                        self._allowed_paths.append((exp or Path(p.strip())).resolve())
                    except Exception:
                        pass

    def _is_path_allowed(self, path: str | Path) -> bool:
        """Check if a target path is located within an allowlisted folder."""
        try:
            target = Path(path).resolve()
            for allowed in self._allowed_paths:
                if target == allowed or allowed in target.parents:
                    return True
        except Exception:
            return False
        return False

    async def start(self) -> None:
        self._event_handler_map = {
            "context.file.request": self._handle_request,
            "context.file.list": self._handle_list,
            "context.file.recent": self._handle_recent,
            "context.file.detect_project": self._handle_detect_project,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())
        logger.info("File Intelligence Engine started")

    async def stop(self) -> None:
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        logger.info("File Intelligence Engine stopped")

    # ── Event Handlers ─────────────────────────────────────────

    async def _handle_request(self, event: HelixEvent) -> None:
        """Return info about a single file or directory."""
        path = event.payload.get("path", ".")
        try:
            loop = asyncio.get_event_loop()
            info = await loop.run_in_executor(None, self._get_file_info, path)
            await self._event_bus.publish_event(
                source="file_intelligence_engine",
                event_type="context.file.provided",
                payload={"path": path, "info": info},
                correlation_id=event.correlation_id,
            )
        except Exception as e:
            logger.exception("File context fetch failed")
            await self._event_bus.publish_event(
                source="file_intelligence_engine",
                event_type="context.file.error",
                payload={"path": path, "error": str(e)},
                correlation_id=event.correlation_id,
            )

    async def _handle_list(self, event: HelixEvent) -> None:
        """List contents of a directory with depth control."""
        path = event.payload.get("path", ".")
        max_depth = event.payload.get("max_depth", self._max_depth)
        try:
            loop = asyncio.get_event_loop()
            listing = await loop.run_in_executor(
                None, self._list_directory, path, max_depth
            )
            await self._event_bus.publish_event(
                source="file_intelligence_engine",
                event_type="context.file.listed",
                payload={"path": path, "listing": listing},
                correlation_id=event.correlation_id,
            )
        except Exception as e:
            logger.exception("Directory listing failed")
            await self._event_bus.publish_event(
                source="file_intelligence_engine",
                event_type="context.file.error",
                payload={"path": path, "error": str(e)},
                correlation_id=event.correlation_id,
            )

    async def _handle_recent(self, event: HelixEvent) -> None:
        """Return recently modified files in a workspace."""
        path = event.payload.get("path", ".")
        minutes = event.payload.get("minutes", self._recent_minutes)
        try:
            loop = asyncio.get_event_loop()
            recent = await loop.run_in_executor(
                None, self._get_recent_files, path, minutes
            )
            await self._event_bus.publish_event(
                source="file_intelligence_engine",
                event_type="context.file.recent.provided",
                payload={
                    "path": path,
                    "minutes": minutes,
                    "files": recent,
                    "count": len(recent),
                },
                correlation_id=event.correlation_id,
            )
        except Exception as e:
            logger.exception("Recent files scan failed")
            await self._event_bus.publish_event(
                source="file_intelligence_engine",
                event_type="context.file.error",
                payload={"path": path, "error": str(e)},
                correlation_id=event.correlation_id,
            )

    async def _handle_detect_project(self, event: HelixEvent) -> None:
        """Detect project type from marker files in a directory."""
        path = event.payload.get("path", ".")
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, self._detect_project_type, path)
            await self._event_bus.publish_event(
                source="file_intelligence_engine",
                event_type="context.file.project_detected",
                payload={"path": path, **result},
                correlation_id=event.correlation_id,
            )
        except Exception as e:
            logger.exception("Project detection failed")
            await self._event_bus.publish_event(
                source="file_intelligence_engine",
                event_type="context.file.error",
                payload={"path": path, "error": str(e)},
                correlation_id=event.correlation_id,
            )

    # ── Core Logic ─────────────────────────────────────────────

    def _get_file_info(self, path: str) -> dict[str, Any]:
        """Return metadata for a single file or directory."""
        if not self._is_path_allowed(path):
            return {"exists": False, "denied": True, "error": "Access denied: Path outside allowlist"}
        p = Path(path)
        if not p.exists():
            return {"exists": False}
        stat = p.stat()
        info: dict[str, Any] = {
            "exists": True,
            "is_file": p.is_file(),
            "is_dir": p.is_dir(),
            "size_bytes": stat.st_size,
            "modified": stat.st_mtime,
            "name": p.name,
        }
        if p.is_file():
            info["extension"] = p.suffix.lower()
            info["language"] = _EXTENSION_LANGUAGES.get(p.suffix.lower(), "Unknown")
        if p.is_dir():
            info["summary"] = self._get_directory_summary(p)
        return info

    def _list_directory(self, path: str, max_depth: int) -> list[dict[str, Any]]:
        """List directory contents recursively up to max_depth."""
        if not self._is_path_allowed(path):
            return []
        root = Path(path)
        if not root.is_dir():
            return []
        entries: list[dict[str, Any]] = []
        self._walk(root, root, 0, max_depth, entries)
        return entries

    def _walk(
        self,
        base: Path,
        current: Path,
        depth: int,
        max_depth: int,
        entries: list[dict[str, Any]],
    ) -> None:
        if depth > max_depth:
            return
        try:
            children = sorted(current.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
        except PermissionError:
            return

        for child in children:
            if child.name in self._ignore_dirs:
                continue
            rel = str(child.relative_to(base))
            if child.is_dir():
                entries.append({
                    "path": rel,
                    "is_dir": True,
                    "depth": depth,
                })
                self._walk(base, child, depth + 1, max_depth, entries)
            elif child.is_file():
                try:
                    stat = child.stat()
                    entries.append({
                        "path": rel,
                        "is_dir": False,
                        "depth": depth,
                        "size_bytes": stat.st_size,
                        "modified": stat.st_mtime,
                        "extension": child.suffix.lower(),
                    })
                except (PermissionError, OSError):
                    continue

    def _get_recent_files(self, path: str, minutes: int) -> list[dict[str, Any]]:
        """Find files modified in the last N minutes."""
        if not self._is_path_allowed(path):
            return []
        root = Path(path)
        if not root.is_dir():
            return []

        cutoff = time.time() - (minutes * 60)
        recent: list[dict[str, Any]] = []

        for child in self._iter_files(root, max_depth=self._max_depth):
            try:
                mtime = child.stat().st_mtime
                if mtime >= cutoff:
                    recent.append({
                        "path": str(child.relative_to(root)),
                        "modified": mtime,
                        "size_bytes": child.stat().st_size,
                        "extension": child.suffix.lower(),
                        "language": _EXTENSION_LANGUAGES.get(child.suffix.lower(), "Unknown"),
                    })
            except (PermissionError, OSError):
                continue

        recent.sort(key=lambda f: f["modified"], reverse=True)
        return recent[:100]  # cap to avoid excessive payloads

    def _detect_project_type(self, path: str) -> dict[str, Any]:
        """Detect project type(s) from marker files."""
        if not self._is_path_allowed(path):
            return {"project_types": [], "markers_found": [], "primary_type": "unknown", "denied": True}
        root = Path(path)
        if not root.is_dir():
            return {"project_types": [], "markers_found": []}

        types: list[str] = []
        markers: list[str] = []

        for marker, project_type in _PROJECT_MARKERS.items():
            if (root / marker).exists():
                markers.append(marker)
                if project_type not in types:
                    types.append(project_type)

        # Also check for glob patterns (e.g. .sln, .csproj)
        for ext in [".sln", ".csproj"]:
            if any(root.glob(f"*{ext}")):
                marker_name = f"*{ext}"
                project_type = _PROJECT_MARKERS.get(ext, "unknown")
                if marker_name not in markers:
                    markers.append(marker_name)
                if project_type not in types:
                    types.append(project_type)

        return {
            "project_types": types,
            "markers_found": markers,
            "primary_type": types[0] if types else "unknown",
        }

    def _get_directory_summary(self, root: Path) -> dict[str, Any]:
        """Return a summary of a directory: file count, language breakdown, total size."""
        file_count = 0
        total_size = 0
        language_counts: dict[str, int] = {}

        for f in self._iter_files(root, max_depth=self._max_depth):
            try:
                stat = f.stat()
                file_count += 1
                total_size += stat.st_size
                lang = _EXTENSION_LANGUAGES.get(f.suffix.lower())
                if lang:
                    language_counts[lang] = language_counts.get(lang, 0) + 1
            except (PermissionError, OSError):
                continue

        return {
            "file_count": file_count,
            "total_size_bytes": total_size,
            "languages": language_counts,
        }

    def _iter_files(self, root: Path, max_depth: int, _depth: int = 0):
        """Yield files recursively, respecting ignore patterns and depth."""
        if _depth > max_depth:
            return
        try:
            for child in root.iterdir():
                if child.name in self._ignore_dirs:
                    continue
                if child.is_file():
                    yield child
                elif child.is_dir():
                    yield from self._iter_files(child, max_depth, _depth + 1)
        except PermissionError:
            return
