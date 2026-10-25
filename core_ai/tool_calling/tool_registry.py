from __future__ import annotations

import json
import logging
from typing import Any

from core_ai.tool_calling.models import ToolDefinition
from core_ai.tool_calling.definitions import get_default_tool_definitions

logger = logging.getLogger("helix.tool_registry")


class ToolRegistry:
    """Central registry of executable tools within HELIX PAIOS."""

    def __init__(self, load_defaults: bool = True):
        self._tools: dict[str, ToolDefinition] = {}
        if load_defaults:
            self.load_defaults()

    def load_defaults(self) -> None:
        """Register all default native tools."""
        for tool in get_default_tool_definitions():
            self.register(tool)

    def register(self, tool: ToolDefinition) -> None:
        """Register a new tool definition."""
        self._tools[tool.name] = tool
        logger.debug("Registered tool: %s (%s)", tool.name, tool.risk_level)

    def unregister(self, tool_name: str) -> bool:
        """Remove a tool from the registry."""
        if tool_name in self._tools:
            del self._tools[tool_name]
            return True
        return False

    def get(self, name: str) -> ToolDefinition | None:
        """Retrieve a tool definition by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check whether a tool exists."""
        return name in self._tools

    def list_all(self, category: str | None = None, max_risk_level: str | None = None) -> list[ToolDefinition]:
        """List registered tools, optionally filtered by category or risk level."""
        risk_rank = {"low": 1, "medium": 2, "high": 3}
        max_rank = risk_rank.get(max_risk_level, 99) if max_risk_level else 99

        tools = []
        for tool in self._tools.values():
            if category and tool.category != category:
                continue
            if risk_rank.get(tool.risk_level, 2) > max_rank:
                continue
            tools.append(tool)
        return tools

    def to_openai_tools(self, category: str | None = None, max_risk_level: str | None = None) -> list[dict[str, Any]]:
        """Export tools formatted according to the OpenAI / Ollama standard tool specification."""
        tools = self.list_all(category=category, max_risk_level=max_risk_level)
        return [tool.to_openai_tool() for tool in tools]

    def to_system_prompt_instruction(self, category: str | None = None) -> str:
        """Generate tool calling instructions formatted for Qwen/Hermes/local LLM system prompts."""
        tools = self.list_all(category=category)
        if not tools:
            return ""

        lines = [
            "## Tool Calling Guidelines & Native Device Tools",
            "You are running inside HELIX with native tool-calling capabilities on this laptop.",
            "When the user asks you to search for apps, scan files, read documents, edit docx, generate pdf, or launch software, YOU MUST CALL THE CORRESPONDING TOOL.",
            "NEVER apologize or say you cannot execute code or access files. You do not run code manually; your backend executes the tool for you.",
            "",
            "To call a tool, respond with an XML tool call:",
            "<tool_call>",
            '{"name": "tool_name", "arguments": {"param": "value"}}',
            "</tool_call>",
            "",
            "Available tools:",
        ]

        for tool in tools:
            param_parts = []
            for p in tool.parameters:
                p_desc = f"{p.name}: {p.type}"
                if not p.required:
                    p_desc += f" (optional, default={p.default})" if p.default is not None else " (optional)"
                param_parts.append(p_desc)
            params_signature = ", ".join(param_parts)
            lines.append(f"- `{tool.name}({params_signature})`: {tool.description}")

        return "\n".join(lines)
