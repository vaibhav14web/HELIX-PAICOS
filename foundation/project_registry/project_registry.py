import os
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger("helix.project_registry")


@dataclass
class ProjectDescriptor:
    project_id: str
    name: str
    root_path: str
    is_git_repo: bool = False
    current_branch: str | None = None
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    last_modified: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProjectRegistry:
    def __init__(self, search_paths: list[str] | None = None):
        self._projects: dict[str, ProjectDescriptor] = {}
        from foundation.paths import get_user_home
        user_home = get_user_home()
        user_docs = user_home / "Documents"
        onedrive_docs = user_home / "OneDrive" / "Documents"
        default_search = [str(onedrive_docs), str(user_docs)]
        self._search_paths = search_paths or [p for p in default_search if os.path.exists(p)] or [str(user_docs)]
        self._scan_workspaces()

    def _scan_workspaces(self) -> None:
        # Pre-seed current HELIX workspace
        helix_path = str(Path(__file__).resolve().parents[2])
        if os.path.exists(helix_path):
            self._projects["helix-main"] = ProjectDescriptor(
                project_id="helix-main",
                name="HELIX-main",
                root_path=helix_path,
                is_git_repo=os.path.exists(os.path.join(helix_path, ".git")),
                current_branch="main",
                languages=["Python", "TypeScript"],
                frameworks=["FastAPI", "Next.js"],
            )

        ai_hms_env = os.getenv("HELIX_AI_HMS_PATH")
        from foundation.paths import get_user_home
        ai_hms_candidates = [
            Path(ai_hms_env) if ai_hms_env else None,
            get_user_home() / "AI_HMS",
            get_user_home() / "Documents" / "AI_HMS",
            Path(r"C:\AI_HMS") if os.name == "nt" else None,
        ]
        ai_hms_path = next((str(p) for p in ai_hms_candidates if p and p.exists()), None)
        if ai_hms_path and os.path.exists(ai_hms_path):
            self._projects["ai-hms"] = ProjectDescriptor(
                project_id="ai-hms",
                name="MEDISENSE-AI (AI-HMS)",
                root_path=ai_hms_path,
                is_git_repo=os.path.exists(os.path.join(ai_hms_path, ".git")),
                current_branch="main",
                languages=["Python", "JavaScript", "SQL"],
                frameworks=["FastAPI", "React", "PyTorch", "XGBoost", "PostgreSQL"],
            )

        for base_path in self._search_paths:
            if os.path.exists(base_path):
                self._scan_folder(Path(base_path))

    def _scan_folder(self, base_dir: Path) -> None:
        try:
            for item in base_dir.iterdir():
                if item.is_dir():
                    is_git = (item / ".git").exists()
                    has_py = any(item.glob("*.py")) or (item / "pyproject.toml").exists()
                    has_js = (item / "package.json").exists()
                    if is_git or has_py or has_js:
                        pid = item.name.lower()
                        langs = []
                        fworks = []
                        if has_py:
                            langs.append("Python")
                        if has_js:
                            langs.append("TypeScript/JavaScript")
                            fworks.append("Node.js")
                        proj = ProjectDescriptor(
                            project_id=pid,
                            name=item.name,
                            root_path=str(item.resolve()),
                            is_git_repo=is_git,
                            languages=langs,
                            frameworks=fworks,
                        )
                        self._projects[pid] = proj
        except Exception as e:
            logger.debug("Project scan folder skipped for %s: %s", base_dir, e)

    def resolve_project(self, query: str) -> ProjectDescriptor | None:
        clean = query.strip().lower()
        if not clean:
            return None
        if clean in self._projects:
            return self._projects[clean]

        # Check exact name match
        for pid, proj in self._projects.items():
            if clean == proj.name.lower():
                return proj

        # Check if query is contained within pid or project name
        for pid, proj in self._projects.items():
            if clean in pid or clean in proj.name.lower():
                return proj

        # Token-based word boundary match in query
        import re
        tokens = set(re.findall(r"[a-z0-9_-]+", clean))
        for pid, proj in self._projects.items():
            if pid in tokens or proj.name.lower() in tokens:
                return proj
            if re.search(r"\b" + re.escape(pid) + r"\b", clean) or re.search(r"\b" + re.escape(proj.name.lower()) + r"\b", clean):
                return proj
        return None

    def list_projects(self) -> list[dict[str, Any]]:
        return [proj.to_dict() for proj in self._projects.values()]
