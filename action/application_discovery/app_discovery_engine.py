import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger("helix.app_discovery")


@dataclass
class DiscoveredApp:
    name: str
    executable_name: str
    executable_path: str
    source: str = "start_menu"  # start_menu | registry | program_files | path | win32
    aliases: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ApplicationDiscoveryEngine:
    def __init__(self):
        self._indexed_apps: dict[str, DiscoveredApp] = {}
        self._scan_and_index()

    def _scan_and_index(self) -> None:
        from foundation.paths import get_discovered_app_roots
        roots = get_discovered_app_roots()
        local_programs = roots["local_programs"]
        windows_apps = roots["windows_apps"]
        program_files = roots["program_files"]
        program_files_x86 = roots["program_files_x86"]
        system_root = roots["system_root"]
        system32 = roots["system32"]

        # Dynamically candidate paths for common apps
        chrome_path = program_files / "Google" / "Chrome" / "Application" / "chrome.exe"
        if not chrome_path.is_file():
            alt_chrome = program_files_x86 / "Google" / "Chrome" / "Application" / "chrome.exe"
            if alt_chrome.is_file():
                chrome_path = alt_chrome

        edge_path = program_files_x86 / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if not edge_path.is_file():
            alt_edge = program_files / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if alt_edge.is_file():
                edge_path = alt_edge

        vscode_path = local_programs / "Microsoft VS Code" / "Code.exe"
        if not vscode_path.is_file():
            alt_vscode = program_files / "Microsoft VS Code" / "Code.exe"
            if alt_vscode.is_file():
                vscode_path = alt_vscode

        wt_path = windows_apps / "wt.exe"
        if not wt_path.is_file():
            alt_wt = system32 / "wt.exe"
            if alt_wt.is_file():
                wt_path = alt_wt

        notepad_path = system32 / "notepad.exe"
        calc_path = system32 / "calc.exe"
        explorer_path = system_root / "explorer.exe"
        cmd_path = system32 / "cmd.exe"

        seed_apps = [
            DiscoveredApp("Google Chrome", "chrome.exe", str(chrome_path), "program_files", ["chrome", "google chrome", "browser"]),
            DiscoveredApp("Microsoft Edge", "msedge.exe", str(edge_path), "program_files", ["edge", "microsoft edge"]),
            DiscoveredApp("Visual Studio Code", "code.exe", str(vscode_path), "program_files", ["vscode", "vs code", "code"]),
            DiscoveredApp("Notepad", "notepad.exe", str(notepad_path), "win32", ["notepad", "editor", "text editor"]),
            DiscoveredApp("Calculator", "calc.exe", str(calc_path), "win32", ["calc", "calculator"]),
            DiscoveredApp("File Explorer", "explorer.exe", str(explorer_path), "win32", ["explorer", "file explorer", "my computer"]),
            DiscoveredApp("Command Prompt", "cmd.exe", str(cmd_path), "win32", ["cmd", "command prompt", "terminal"]),
            DiscoveredApp("Windows Terminal", "wt.exe", str(wt_path), "win32", ["wt", "windows terminal", "terminal"]),
        ]
        for app in seed_apps:
            self._add_to_index(app)

        # Dynamic scan of standard program directories
        for scan_dir in [program_files, program_files_x86, local_programs]:
            try:
                if scan_dir.exists():
                    self._scan_directory(scan_dir, depth=2)
            except Exception as e:
                logger.debug("Failed scanning %s: %s", scan_dir, e)

    def _scan_directory(self, base_dir: Path, depth: int) -> None:
        if not base_dir.exists():
            return
        try:
            for item in base_dir.iterdir():
                if item.is_dir() and depth > 0:
                    try:
                        for subitem in item.iterdir():
                            if subitem.is_file() and subitem.suffix.lower() == ".exe":
                                app_name = item.name
                                exe_name = subitem.name.lower()
                                app = DiscoveredApp(
                                    name=app_name,
                                    executable_name=exe_name,
                                    executable_path=str(subitem),
                                    source="program_files",
                                    aliases=[app_name.lower(), exe_name.replace(".exe", "")],
                                )
                                self._add_to_index(app)
                    except Exception:
                        continue
        except Exception as e:
            logger.debug("Scan directory failed for %s: %s", base_dir, e)

    def _add_to_index(self, app: DiscoveredApp) -> None:
        self._indexed_apps[app.name.lower()] = app
        for alias in app.aliases:
            self._indexed_apps[alias.lower()] = app

    def resolve_app(self, query: str) -> DiscoveredApp | None:
        clean = query.strip().lower()
        if clean in self._indexed_apps:
            return self._indexed_apps[clean]

        # Fuzzy match
        for key, app in self._indexed_apps.items():
            if clean in key or key in clean:
                return app
        return None

    def list_apps(self) -> list[dict[str, Any]]:
        unique = {app.executable_path: app for app in self._indexed_apps.values()}
        return [app.to_dict() for app in unique.values()]
