from __future__ import annotations

import re
import json
import logging
from typing import Any

from core_ai.tool_calling.models import ToolCall

logger = logging.getLogger("helix.tool_parser")


class ToolParser:
    """Parses tool calls from varied LLM response formats (Ollama API, Qwen XML, Markdown JSON)."""

    @classmethod
    def parse_native_tool_calls(cls, tool_calls_data: list[dict[str, Any]] | None) -> list[ToolCall]:
        """Parse native OpenAI / Ollama tool_calls list from API response message."""
        if not tool_calls_data:
            return []

        calls = []
        for tc in tool_calls_data:
            function_data = tc.get("function", tc)
            name = function_data.get("name", "")
            raw_args = function_data.get("arguments", {})

            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except Exception:
                    args = {"raw_input": raw_args}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}

            call_id = tc.get("id") or function_data.get("id")
            tool_call = ToolCall(name=name, arguments=args)
            if call_id:
                tool_call.call_id = call_id
            calls.append(tool_call)

        return calls

    @classmethod
    def parse_text_response(cls, text: str) -> tuple[str, list[ToolCall]]:
        """
        Parse text containing potential tool call blocks.
        Returns:
            (cleaned_content_text, list_of_tool_calls)
        """
        if not text or not text.strip():
            return "", []

        tool_calls: list[ToolCall] = []

        # 1. Match <tool_call> ... </tool_call> tags (Qwen / Hermes / Llama style)
        tag_pattern = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
        matches = list(tag_pattern.finditer(text))

        if matches:
            cleaned_text = text
            for match in matches:
                block_content = match.group(1).strip()
                call = cls._parse_json_call(block_content)
                if call:
                    tool_calls.append(call)
            # Remove <tool_call> tags from natural text output
            cleaned_text = tag_pattern.sub("", text).strip()
            return cleaned_text, tool_calls

        # 2. Match ```tool_call ... ``` or ```json with tool schema
        code_block_pattern = re.compile(r"```(?:tool_call|json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)
        for match in code_block_pattern.finditer(text):
            block_content = match.group(1).strip()
            call = cls._parse_json_call(block_content)
            if call:
                tool_calls.append(call)

        if tool_calls:
            cleaned_text = code_block_pattern.sub("", text).strip()
            return cleaned_text, tool_calls

        # 3. If message contains or is a raw JSON string defining a tool call
        json_obj_pattern = re.compile(r"\{\s*\"name\"\s*:\s*\"[a-zA-Z0-9_\-]+\"[\s\S]*?\}")
        for match in json_obj_pattern.finditer(text):
            call = cls._parse_json_call(match.group(0).strip())
            if call:
                tool_calls.append(call)

        if tool_calls:
            cleaned_text = json_obj_pattern.sub("", text).replace("```json", "").replace("```", "").strip()
            return cleaned_text, tool_calls

        return text, []

    @classmethod
    def _parse_json_call(cls, content: str) -> ToolCall | None:
        """Helper to parse a JSON payload into a ToolCall."""
        try:
            data = json.loads(content)
            if not isinstance(data, dict):
                return None

            name = (
                data.get("name")
                or data.get("tool")
                or data.get("action")
                or data.get("function")
            )
            if not name or not isinstance(name, str):
                return None

            args = (
                data.get("arguments")
                or data.get("params")
                or data.get("parameters")
                or {}
            )

            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"input": args}
            elif not isinstance(args, dict):
                args = {"value": args}

            call_id = data.get("id") or data.get("call_id")
            tool_call = ToolCall(name=name, arguments=args)
            if call_id:
                tool_call.call_id = str(call_id)
            return tool_call
        except Exception as e:
            logger.debug("Failed to parse tool call JSON: %s", e)
            return None
