from __future__ import annotations

from core_ai.tool_calling.models import ToolDefinition, ToolParameter


def get_tool_definition(name: str) -> ToolDefinition | None:
    """Returns a specific tool definition by name, or None if not found."""
    for tool in get_default_tool_definitions():
        if tool.name == name:
            return tool
    return None


def get_default_tool_definitions() -> list[ToolDefinition]:
    """Returns the list of core native tool definitions for HELIX PAIOS."""
    return [
        ToolDefinition(
            name="launch_application",
            description="Launch a desktop application or registered software alias on the user's Windows system.",
            parameters=[
                ToolParameter(
                    name="application",
                    type="string",
                    description="Name or alias of the application to launch (e.g. 'chrome', 'notepad', 'code', 'calc', 'paint', 'terminal', 'explorer').",
                    required=True,
                ),
                ToolParameter(
                    name="url",
                    type="string",
                    description="Optional web URL or command parameter to pass to the launched application.",
                    required=False,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="app",
        ),
        ToolDefinition(
            name="browser_search",
            description="Perform a web search using the user's default browser and return or open search results.",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Search query terms to search on the web.",
                    required=True,
                ),
            ],
            risk_level="low",
            requires_permission=False,
            category="web",
        ),
        ToolDefinition(
            name="read_file",
            description="Read the text content of a local file. System directories and executable files are strictly blocked.",
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description="Path of the file to read (must be within user documents, desktop, or project workspaces).",
                    required=True,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="file",
        ),
        ToolDefinition(
            name="send_notification",
            description="Display a native Windows desktop notification alert to the user.",
            parameters=[
                ToolParameter(
                    name="message",
                    type="string",
                    description="The notification message text to display.",
                    required=True,
                ),
                ToolParameter(
                    name="title",
                    type="string",
                    description="Optional title for the notification window.",
                    required=False,
                    default="HELIX",
                ),
            ],
            risk_level="low",
            requires_permission=False,
            category="system",
        ),
        ToolDefinition(
            name="compose_email",
            description="Draft or compose an email using the system email client.",
            parameters=[
                ToolParameter(
                    name="subject",
                    type="string",
                    description="Subject line for the email.",
                    required=True,
                ),
                ToolParameter(
                    name="recipient",
                    type="string",
                    description="Optional recipient email address (e.g. user@example.com).",
                    required=False,
                ),
                ToolParameter(
                    name="body",
                    type="string",
                    description="Optional text content for the email body.",
                    required=False,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="app",
        ),
        ToolDefinition(
            name="open_url",
            description="Open a target URL in the default web browser. Only http, https, and mailto schemes are allowed.",
            parameters=[
                ToolParameter(
                    name="url",
                    type="string",
                    description="The web address URL to navigate to.",
                    required=True,
                ),
            ],
            risk_level="low",
            requires_permission=False,
            category="web",
        ),
        ToolDefinition(
            name="read_browser_page",
            description="Extract readable text content from an open browser tab or a specified URL.",
            parameters=[
                ToolParameter(
                    name="url",
                    type="string",
                    description="Optional URL of the web page to read.",
                    required=False,
                ),
                ToolParameter(
                    name="title",
                    type="string",
                    description="Optional window title of the target browser tab.",
                    required=False,
                ),
            ],
            risk_level="low",
            requires_permission=False,
            category="web",
        ),
        ToolDefinition(
            name="in_app_task",
            description="Perform a specific workflow task inside a recognized application.",
            parameters=[
                ToolParameter(
                    name="app",
                    type="string",
                    description="The application identifier (e.g. 'vscode', 'notepad').",
                    required=True,
                ),
                ToolParameter(
                    name="task",
                    type="string",
                    description="Description of the task to perform.",
                    required=True,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="app",
        ),
        ToolDefinition(
            name="vscode_open",
            description="Open a workspace folder or file directly in Visual Studio Code.",
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description="Absolute or relative path to file or workspace folder.",
                    required=True,
                ),
                ToolParameter(
                    name="line",
                    type="integer",
                    description="Optional line number to place cursor on.",
                    required=False,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="app",
        ),
        ToolDefinition(
            name="create_note",
            description="Save a text or markdown note into the user's HELIX notes collection.",
            parameters=[
                ToolParameter(
                    name="title",
                    type="string",
                    description="Title of the note.",
                    required=True,
                ),
                ToolParameter(
                    name="content",
                    type="string",
                    description="Text or markdown content of the note.",
                    required=True,
                ),
                ToolParameter(
                    name="category",
                    type="string",
                    description="Optional tag or category for the note (e.g. 'work', 'ideas').",
                    required=False,
                    default="general",
                ),
            ],
            risk_level="low",
            requires_permission=False,
            category="note",
        ),
        ToolDefinition(
            name="list_open_applications",
            description="Inspect live visible desktop application windows and window titles. Reports which observed window is focused; it does not enumerate all processes.",
            parameters=[],
            risk_level="low",
            requires_permission=False,
            category="system",
        ),
        ToolDefinition(
            name="get_system_state",
            description="Get the current focused window, active application, and system environment state.",
            parameters=[],
            risk_level="low",
            requires_permission=False,
            category="system",
        ),
        ToolDefinition(
            name="search_installed_apps",
            description="Search the indexed Windows applications, Start Menu programs, and aliases to find executable paths.",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Search term (e.g. 'Blender', 'Photoshop', 'Steam', 'Docker').",
                    required=True,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="app",
        ),
        ToolDefinition(
            name="search_projects",
            description="Search local indexed development repositories, Git branches, and codebases.",
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Project name or keyword (e.g. 'HELIX', 'frontend', 'python').",
                    required=True,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="file",
        ),
        ToolDefinition(
            name="scan_laptop_files",
            description="Scan and locate files on the user's laptop across Documents, Desktop, Downloads, or custom directories.",
            parameters=[
                ToolParameter(
                    name="folder",
                    type="string",
                    description="Folder to scan: 'documents', 'desktop', 'downloads', 'home', or an explicit folder path.",
                    required=False,
                    default="documents",
                ),
                ToolParameter(
                    name="query",
                    type="string",
                    description="Optional filename pattern or keyword to search for (e.g. 'budget', '*.pdf', 'notes').",
                    required=False,
                ),
                ToolParameter(
                    name="max_results",
                    type="integer",
                    description="Maximum number of files to return (default 20).",
                    required=False,
                    default=20,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="file",
        ),
        ToolDefinition(
            name="read_document",
            description="Read and extract clean text, headings, and tables from Microsoft Word (.docx) and Adobe PDF (.pdf) documents.",
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description="Path to the .docx or .pdf file on the user's laptop.",
                    required=True,
                ),
                ToolParameter(
                    name="max_pages",
                    type="integer",
                    description="Maximum number of pages to read for PDF documents (default 20).",
                    required=False,
                    default=20,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="file",
        ),
        ToolDefinition(
            name="edit_docx",
            description="Create a new Microsoft Word (.docx) document, append sections, or find and replace text while preserving document structure.",
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description="Target path for the .docx file.",
                    required=True,
                ),
                ToolParameter(
                    name="action",
                    type="string",
                    description="Action to perform: 'create', 'append', or 'replace_text'.",
                    required=True,
                ),
                ToolParameter(
                    name="content",
                    type="string",
                    description="Text or paragraphs to write into the document.",
                    required=False,
                ),
                ToolParameter(
                    name="title",
                    type="string",
                    description="Optional document heading or section title.",
                    required=False,
                ),
                ToolParameter(
                    name="search_text",
                    type="string",
                    description="Text to search for when action is 'replace_text'.",
                    required=False,
                ),
                ToolParameter(
                    name="replace_text",
                    type="string",
                    description="Replacement text when action is 'replace_text'.",
                    required=False,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="file",
        ),
        ToolDefinition(
            name="generate_pdf",
            description="Generate a clean, professionally formatted PDF document from title and content text on the user's laptop.",
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description="Destination path for the generated .pdf file.",
                    required=True,
                ),
                ToolParameter(
                    name="title",
                    type="string",
                    description="Title of the PDF document.",
                    required=True,
                ),
                ToolParameter(
                    name="content",
                    type="string",
                    description="Body text content or paragraphs to format into the PDF.",
                    required=True,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="file",
        ),
        ToolDefinition(
            name="inspect_directory",
            description="Inspect any folder or codebase directory on the user's laptop. Recursively maps folder structure, detects languages and frameworks, reads documentation, and indexes it into HELIX cognitive memory.",
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description="Path to the directory on the laptop (e.g. 'C:\\AI_HMS', 'Documents\\project', 'downloads').",
                    required=True,
                ),
                ToolParameter(
                    name="max_depth",
                    type="integer",
                    description="Maximum directory depth to explore (default 3).",
                    required=False,
                    default=3,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="file",
        ),
        ToolDefinition(
            name="create_backup",
            description="Create an atomic, cryptographically verified portable backup archive of user data and cognitive state.",
            parameters=[
                ToolParameter(
                    name="user_id",
                    type="string",
                    description="User ID whose data is to be backed up (default: current user).",
                    required=False,
                    default="default_owner",
                ),
                ToolParameter(
                    name="comment",
                    type="string",
                    description="Optional note or comment describing the backup.",
                    required=False,
                ),
            ],
            risk_level="medium",
            requires_permission=True,
            category="system",
        ),
        ToolDefinition(
            name="restore_backup",
            description="Safely restore user data and state from a verified backup archive with preflight validation and rollback protection.",
            parameters=[
                ToolParameter(
                    name="archive_path",
                    type="string",
                    description="Path to the validated .hlxbak.zip backup archive.",
                    required=True,
                ),
                ToolParameter(
                    name="target_user_id",
                    type="string",
                    description="Destination user ID to receive the restored state.",
                    required=True,
                ),
            ],
            risk_level="high",
            requires_permission=True,
            category="system",
        ),
        ToolDefinition(
            name="create_goal",
            description="Create, track, and persist a long-running goal in HELIX with title, description, priority, and optional subgoals.",
            parameters=[
                ToolParameter(
                    name="title",
                    type="string",
                    description="Title or summary name of the goal.",
                    required=True,
                ),
                ToolParameter(
                    name="description",
                    type="string",
                    description="Detailed description or context of the goal.",
                    required=False,
                ),
                ToolParameter(
                    name="priority",
                    type="string",
                    description="Priority level of the goal: 'low', 'medium', 'high', or 'critical' (default 'medium').",
                    required=False,
                    default="medium",
                ),
                ToolParameter(
                    name="subgoals",
                    type="array",
                    description="Optional list of subgoal titles or milestone tasks under this goal.",
                    required=False,
                ),
                ToolParameter(
                    name="tasks",
                    type="array",
                    description="Optional list of task objects with title, description, etc.",
                    required=False,
                ),
            ],
            risk_level="low",
            requires_permission=False,
            category="goals",
        ),
        ToolDefinition(
            name="add_subgoal",
            description="Add a subgoal or subtask milestone to an existing long-running goal.",
            parameters=[
                ToolParameter(
                    name="goal_title",
                    type="string",
                    description="Title or ID of the existing goal.",
                    required=True,
                ),
                ToolParameter(
                    name="subgoal_title",
                    type="string",
                    description="Title or summary of the new subgoal / subtask.",
                    required=True,
                ),
                ToolParameter(
                    name="description",
                    type="string",
                    description="Optional description or details of the subgoal.",
                    required=False,
                ),
            ],
            risk_level="low",
            requires_permission=False,
            category="goals",
        ),
        ToolDefinition(
            name="list_goals",
            description="List and inspect long-running goals belonging to the authenticated user.",
            parameters=[
                ToolParameter(
                    name="include_archived",
                    type="boolean",
                    description="Whether to include archived goals in the list.",
                    required=False,
                    default=False,
                ),
                ToolParameter(
                    name="archived_only",
                    type="boolean",
                    description="Whether to list only archived goals.",
                    required=False,
                    default=False,
                ),
            ],
            risk_level="low",
            requires_permission=False,
            category="goals",
        ),
        ToolDefinition(
            name="archive_goal",
            description="Archive a long-running goal, removing it from active lists while preserving its history and subtasks.",
            parameters=[
                ToolParameter(
                    name="goal_id",
                    type="string",
                    description="Unique identifier of the goal to archive.",
                    required=True,
                ),
            ],
            risk_level="low",
            requires_permission=False,
            category="goals",
        ),
        ToolDefinition(
            name="restore_goal",
            description="Restore a previously archived goal back to active state.",
            parameters=[
                ToolParameter(
                    name="goal_id",
                    type="string",
                    description="Unique identifier of the archived goal to restore.",
                    required=True,
                ),
            ],
            risk_level="low",
            requires_permission=False,
            category="goals",
        ),
        ToolDefinition(
            name="delete_goal",
            description="Permanently delete a long-running goal and its state from persistent storage. Requires explicit user confirmation.",
            parameters=[
                ToolParameter(
                    name="goal_id",
                    type="string",
                    description="Unique identifier of the goal to delete permanently.",
                    required=True,
                ),
                ToolParameter(
                    name="confirmed",
                    type="boolean",
                    description="Explicit confirmation flag indicating user has confirmed permanent deletion.",
                    required=True,
                    default=False,
                ),
            ],
            risk_level="high",
            requires_permission=True,
            category="goals",
        ),
    ]
