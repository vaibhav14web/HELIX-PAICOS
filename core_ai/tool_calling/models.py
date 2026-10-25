from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class ToolParameter:
    """Descriptor for a single parameter in a tool definition."""
    name: str
    type: str  # "string", "integer", "number", "boolean", "array", "object"
    description: str
    required: bool = True
    enum: list[Any] | None = None
    default: Any | None = None
    items: dict[str, Any] | None = None  # for array items
    properties: dict[str, Any] | None = None  # for object properties

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "type": self.type,
            "description": self.description,
        }
        if self.enum is not None:
            schema["enum"] = self.enum
        if self.default is not None:
            schema["default"] = self.default
        if self.items is not None:
            schema["items"] = self.items
        if self.properties is not None:
            schema["properties"] = self.properties
        return schema


@dataclass
class ToolDefinition:
    """Full definition of a callable tool within the HELIX system."""
    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)
    risk_level: str = "low"  # "low", "medium", "high"
    requires_permission: bool = False
    category: str = "general"  # "system", "file", "web", "app", "note", "general"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json_schema(self) -> dict[str, Any]:
        """Convert parameter definitions to standard JSON schema object."""
        properties = {}
        required = []
        for param in self.parameters:
            properties[param.name] = param.to_schema()
            if param.required:
                required.append(param.name)

        return {
            "type": "object",
            "properties": properties,
            "required": required,
        }

    def to_openai_tool(self) -> dict[str, Any]:
        """Convert to OpenAI / Ollama standard function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.to_json_schema(),
            },
        }

    def to_ollama_tool(self) -> dict[str, Any]:
        """Convert to Ollama tool format (same as OpenAI function spec)."""
        return self.to_openai_tool()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": [asdict(p) for p in self.parameters],
            "risk_level": self.risk_level,
            "requires_permission": self.requires_permission,
            "category": self.category,
            "metadata": self.metadata,
        }


@dataclass
class ToolCall:
    """Represents a request from an LLM to execute a tool."""
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:8]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "arguments": self.arguments,
        }


@dataclass
class ToolResult:
    """Represents the output from executing a tool."""
    call_id: str
    name: str
    success: bool
    result: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    verified: bool = False
    lifecycle: str = "completed"
    verification_details: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "name": self.name,
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "verified": self.verified,
            "lifecycle": self.lifecycle,
            "verification_details": self.verification_details,
        }

    def to_tool_message(self) -> dict[str, Any]:
        """Convert to OpenAI/Ollama tool response message format."""
        content = json.dumps(self.result if self.success else {"error": self.error}, default=str)
        return {
            "role": "tool",
            "tool_call_id": self.call_id,
            "name": self.name,
            "content": content,
        }
