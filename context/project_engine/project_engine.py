import os
import json
import logging
import time
from pathlib import Path
from typing import Any

from foundation.event_bus.event_bus import EventBus, HelixEvent

logger = logging.getLogger("helix.project_engine")

# Marker files that indicate a project root
_ROOT_MARKERS = {
    ".git", ".svn", ".hg",
    "pyproject.toml", "setup.py", "setup.cfg",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml", "build.gradle",
    "CMakeLists.txt",
    ".sln",
    "composer.json",
    "Gemfile",
}


class ProjectEngine:
    def __init__(self, event_bus: EventBus):
        self._event_bus = event_bus
        from foundation.paths import get_runtime_dir
        self._storage_path = Path(
            os.getenv("HELIX_PROJECT_PATH", str(get_runtime_dir() / "projects"))
        )
        try:
            self._storage_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            fallback = Path.cwd() / "backend" / "data" / "projects"
            logger.warning(
                "Project path %s is unavailable (%s); using %s",
                self._storage_path,
                exc,
                fallback,
            )
            fallback.mkdir(parents=True, exist_ok=True)
            self._storage_path = fallback
        self._active_project: str | None = None
        self._projects: dict[str, dict[str, Any]] = {}
        self._subscriptions: list[str] = []
        self._event_handler_map: dict[str, Any] = {}

    async def start(self) -> None:
        self._load_projects()
        self._event_handler_map = {
            "project.create": self._handle_create,
            "project.open": self._handle_open,
            "project.close": self._handle_close,
            "project.list": self._handle_list,
            "project.detect": self._handle_detect,
            "project.update_session": self._handle_update_session,
        }
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.subscribe(event_type, handler)
        self._subscriptions = list(self._event_handler_map.keys())
        logger.info("Project Engine started")

    async def stop(self) -> None:
        # Save session timestamp for the active project before stopping
        if self._active_project and self._active_project in self._projects:
            self._projects[self._active_project]["last_closed"] = time.time()
        for event_type, handler in self._event_handler_map.items():
            self._event_bus.unsubscribe(event_type, handler)
        self._subscriptions.clear()
        self._save_projects()
        logger.info("Project Engine stopped")

    # ── Event Handlers ─────────────────────────────────────────

    async def _handle_create(self, event: HelixEvent) -> None:
        name = event.payload.get("name", "")
        path = event.payload.get("path", "")
        if not name:
            return
        now = time.time()
        self._projects[name] = {
            "name": name,
            "path": path,
            "created_at": now,
            "last_opened": now,
            "last_closed": None,
            "open_count": 0,
            "project_type": self._detect_project_type(path) if path else "unknown",
            "session": {
                "active_files": [],
                "last_conversation_id": None,
            },
        }
        self._save_projects()
        await self._event_bus.publish_event(
            source="project_engine",
            event_type="project.created",
            payload={"name": name, "path": path},
            correlation_id=event.correlation_id,
        )

    async def _handle_open(self, event: HelixEvent) -> None:
        name = event.payload.get("name", "")
        path = event.payload.get("path", "")

        # Auto-create if opening by path and project doesn't exist yet
        if not name and path:
            name = Path(path).name
        if name not in self._projects and path:
            await self._handle_create(event)

        if name not in self._projects:
            await self._event_bus.publish_event(
                source="project_engine",
                event_type="project.error",
                payload={"error": f"Project '{name}' not found"},
                correlation_id=event.correlation_id,
            )
            return

        # Detect project switch
        previous_project = self._active_project
        self._active_project = name
        now = time.time()

        proj = self._projects[name]
        proj["last_opened"] = now
        proj["open_count"] = proj.get("open_count", 0) + 1
        if path:
            proj["path"] = path
            proj["project_type"] = self._detect_project_type(path)
        self._save_projects()

        # Build rich payload with session history
        payload: dict[str, Any] = {
            "name": name,
            "path": proj.get("path", ""),
            "project_type": proj.get("project_type", "unknown"),
            "open_count": proj.get("open_count", 0),
            "session": proj.get("session", {}),
        }

        await self._event_bus.publish_event(
            source="project_engine",
            event_type="project.opened",
            payload=payload,
            correlation_id=event.correlation_id,
        )

        # Publish switch event if changing projects
        if previous_project and previous_project != name:
            # Save close timestamp for the old project
            if previous_project in self._projects:
                self._projects[previous_project]["last_closed"] = now
                self._save_projects()

            await self._event_bus.publish_event(
                source="project_engine",
                event_type="project.switched",
                payload={
                    "from": previous_project,
                    "to": name,
                },
                correlation_id=event.correlation_id,
            )

    async def _handle_close(self, event: HelixEvent) -> None:
        closed_name = self._active_project
        if closed_name and closed_name in self._projects:
            self._projects[closed_name]["last_closed"] = time.time()
            self._save_projects()
        self._active_project = None
        await self._event_bus.publish_event(
            source="project_engine",
            event_type="project.closed",
            payload={"name": closed_name or ""},
            correlation_id=event.correlation_id,
        )

    async def _handle_list(self, event: HelixEvent) -> None:
        # Return project summaries, not full data
        summaries = []
        for name, proj in self._projects.items():
            summaries.append({
                "name": name,
                "path": proj.get("path", ""),
                "project_type": proj.get("project_type", "unknown"),
                "last_opened": proj.get("last_opened"),
                "open_count": proj.get("open_count", 0),
            })
        await self._event_bus.publish_event(
            source="project_engine",
            event_type="project.listed",
            payload={
                "projects": summaries,
                "active": self._active_project,
                "count": len(summaries),
            },
            correlation_id=event.correlation_id,
        )

    async def _handle_detect(self, event: HelixEvent) -> None:
        """Auto-detect project root from a file path by searching upward."""
        file_path = event.payload.get("path", "")
        if not file_path:
            return
        try:
            root = self._find_project_root(file_path)
            project_type = self._detect_project_type(str(root)) if root else "unknown"
            await self._event_bus.publish_event(
                source="project_engine",
                event_type="project.detected",
                payload={
                    "path": file_path,
                    "project_root": str(root) if root else None,
                    "project_type": project_type,
                },
                correlation_id=event.correlation_id,
            )
        except Exception as e:
            logger.exception("Project detection failed for %s", file_path)
            await self._event_bus.publish_event(
                source="project_engine",
                event_type="project.error",
                payload={"error": str(e), "path": file_path},
                correlation_id=event.correlation_id,
            )

    async def _handle_update_session(self, event: HelixEvent) -> None:
        """Update session data (active files, conversation ID) for the active project."""
        if not self._active_project or self._active_project not in self._projects:
            return
        session = self._projects[self._active_project].setdefault("session", {})
        active_files = event.payload.get("active_files")
        conversation_id = event.payload.get("conversation_id")
        if active_files is not None:
            session["active_files"] = active_files
        if conversation_id is not None:
            session["last_conversation_id"] = conversation_id
        self._save_projects()

    # ── Core Logic ─────────────────────────────────────────────

    def _find_project_root(self, file_path: str) -> Path | None:
        """Walk upward from file_path to find the nearest project root marker."""
        current = Path(file_path).resolve()
        if current.is_file():
            current = current.parent

        # Walk up to 10 levels to avoid infinite loops on deep paths
        for _ in range(10):
            for marker in _ROOT_MARKERS:
                if (current / marker).exists():
                    return current
            parent = current.parent
            if parent == current:
                break  # reached filesystem root
            current = parent
        return None

    def _detect_project_type(self, path: str) -> str:
        """Detect the primary project type from marker files in the given directory."""
        root = Path(path)
        if not root.is_dir():
            root = root.parent

        # Ordered by priority — first match wins
        marker_type_map = [
            ("pyproject.toml", "python"),
            ("setup.py", "python"),
            ("requirements.txt", "python"),
            ("package.json", "node"),
            ("tsconfig.json", "typescript"),
            ("Cargo.toml", "rust"),
            ("go.mod", "go"),
            ("pom.xml", "java"),
            ("build.gradle", "java"),
            ("CMakeLists.txt", "cmake"),
            ("composer.json", "php"),
            ("Gemfile", "ruby"),
        ]
        for marker, ptype in marker_type_map:
            if (root / marker).exists():
                return ptype

        # Check for glob patterns
        if any(root.glob("*.sln")):
            return "dotnet"
        if any(root.glob("*.csproj")):
            return "dotnet"
        return "unknown"

    # ── Persistence ────────────────────────────────────────────

    def _load_projects(self) -> None:
        project_file = self._storage_path / "projects.json"
        if project_file.exists():
            try:
                with open(project_file, "r", encoding="utf-8") as f:
                    self._projects = json.load(f)
            except (json.JSONDecodeError, ValueError):
                self._projects = {}

    def _save_projects(self) -> None:
        self._storage_path.mkdir(parents=True, exist_ok=True)
        with open(self._storage_path / "projects.json", "w", encoding="utf-8") as f:
            json.dump(self._projects, f, indent=2)

    # ── Public API ─────────────────────────────────────────────

    def get_projects_list(self) -> list[dict[str, Any]]:
        """Return a list of all project summaries."""
        summaries = []
        for name, proj in self._projects.items():
            summaries.append({
                "name": name,
                "path": proj.get("path", ""),
                "project_type": proj.get("project_type", "unknown"),
                "last_opened": proj.get("last_opened"),
                "open_count": proj.get("open_count", 0),
            })
        return summaries

    def create_project_sync(self, name: str, path: str) -> dict[str, Any]:
        """Create a new project in-memory and persist it."""
        now = time.time()
        self._projects[name] = {
            "name": name,
            "path": path,
            "created_at": now,
            "last_opened": now,
            "last_closed": None,
            "open_count": 0,
            "project_type": self._detect_project_type(path) if path else "unknown",
            "session": {
                "active_files": [],
                "last_conversation_id": None,
            },
        }
        self._save_projects()
        return self._projects[name]

    async def create_project(self, name: str, path: str) -> dict[str, Any]:
        """Create a new project and publish the event."""
        proj = self.create_project_sync(name, path)
        await self._event_bus.publish_event(
            source="project_engine",
            event_type="project.created",
            payload={"name": name, "path": path},
        )
        return proj

    async def open_project(self, name: str, path: str = "") -> dict[str, Any]:
        """Open a project, update stats, publish events, and return details."""
        if not name and path:
            name = Path(path).name
        if name not in self._projects and path:
            self.create_project_sync(name, path)

        if name not in self._projects:
            raise ValueError(f"Project '{name}' not found")

        previous_project = self._active_project
        self._active_project = name
        now = time.time()

        proj = self._projects[name]
        proj["last_opened"] = now
        proj["open_count"] = proj.get("open_count", 0) + 1
        if path:
            proj["path"] = path
            proj["project_type"] = self._detect_project_type(path)
        self._save_projects()

        payload: dict[str, Any] = {
            "name": name,
            "path": proj.get("path", ""),
            "project_type": proj.get("project_type", "unknown"),
            "open_count": proj.get("open_count", 0),
            "session": proj.get("session", {}),
        }

        await self._event_bus.publish_event(
            source="project_engine",
            event_type="project.opened",
            payload=payload,
        )

        if previous_project and previous_project != name:
            if previous_project in self._projects:
                self._projects[previous_project]["last_closed"] = now
                self._save_projects()

            await self._event_bus.publish_event(
                source="project_engine",
                event_type="project.switched",
                payload={
                    "from": previous_project,
                    "to": name,
                },
            )
        return payload
