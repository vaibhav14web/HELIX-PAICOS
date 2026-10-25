"""Streaming filter for removing <think>...</think> reasoning blocks from LLM responses."""

from typing import Final


class ThinkBlockFilter:
    """A streaming filter that strips <think>...</think> reasoning blocks.

    This filter operates across arbitrary token and chunk boundaries to ensure
    that internal reasoning blocks emitted by models (such as Qwen 3 or DeepSeek-R1)
    never reach user-facing response streams, event busses, or WebSocket clients.

    Behaviors and Guarantees:
    - Chunk boundary resilience: Handles <think> and </think> tags split across
      arbitrary chunk divisions (e.g. ['<th', 'ink>', 'reasoning', '</th', 'ink>']).
    - Complete suppression: Content between <think> and </think> is dropped entirely.
    - User text preservation: Text before, between, and after think blocks is preserved.
    - Multiple blocks: Correctly processes repeated or multiple reasoning blocks.
    - Unclosed block behavior: If a stream terminates while still inside an unclosed
      <think> block, all buffered reasoning content is suppressed and discarded at
      stream completion (flush() returns "").
    - Orphaned closing tag behavior: If an isolated </think> tag is encountered outside
      of a reasoning block, the tag itself is suppressed, preserving surrounding text.
    - End-of-stream flush: Incomplete tag prefixes (e.g. '<think', '<th', '</th') are
      discarded at stream completion so that partial or malformed tags are never emitted.
    - Privacy & isolation: Hidden reasoning is never logged or cached across instances.
      Each generation uses its own isolated filter instance.
    """

    OPEN_TAG: Final[str] = "<think>"
    CLOSE_TAG: Final[str] = "</think>"

    def __init__(self) -> None:
        self.in_think_block: bool = False
        self._buffer: str = ""

    def reset(self) -> None:
        """Reset internal state for a new generation."""
        self.in_think_block = False
        self._buffer = ""

    @classmethod
    def filter_text(cls, text: str) -> str:
        """Filter a complete (non-streaming) text string, removing all <think>...</think> blocks.

        Args:
            text: The full text string to filter.

        Returns:
            The filtered user-facing text with reasoning blocks removed.
        """
        if not text or not isinstance(text, str):
            return ""
        instance = cls()
        return instance.feed(text) + instance.flush()

    def feed(self, chunk: str) -> str:
        """Process an incoming stream chunk and return filtered user-facing text.

        Args:
            chunk: The new text chunk received from the LLM stream.

        Returns:
            Any user-facing text ready to be emitted immediately.
        """
        if not chunk or not isinstance(chunk, str):
            return ""

        self._buffer += chunk
        output: list[str] = []

        while self._buffer:
            if not self.in_think_block:
                idx = self._buffer.find("<")
                if idx == -1:
                    # No tag opener anywhere in buffer: all buffered text is user-facing
                    output.append(self._buffer)
                    self._buffer = ""
                    break

                if idx > 0:
                    # Emit ordinary text preceding the '<'
                    output.append(self._buffer[:idx])
                    self._buffer = self._buffer[idx:]

                # self._buffer now starts with '<'
                lower_buf = self._buffer.lower()

                # Check for opening tag
                if lower_buf.startswith(self.OPEN_TAG):
                    self.in_think_block = True
                    self._buffer = self._buffer[len(self.OPEN_TAG):]
                    continue

                # Check for orphaned closing tag
                if lower_buf.startswith(self.CLOSE_TAG):
                    self._buffer = self._buffer[len(self.CLOSE_TAG):]
                    continue

                # Check if buffer forms a partial prefix of OPEN_TAG or CLOSE_TAG
                is_open_prefix = (
                    len(self._buffer) < len(self.OPEN_TAG)
                    and self.OPEN_TAG.startswith(lower_buf)
                )
                is_close_prefix = (
                    len(self._buffer) < len(self.CLOSE_TAG)
                    and self.CLOSE_TAG.startswith(lower_buf)
                )

                if is_open_prefix or is_close_prefix:
                    # Tag may be split across chunks; withhold and wait for next chunk
                    break
                else:
                    # Starts with '<' but cannot form <think> or </think>.
                    # Emit '<' and continue evaluating remaining characters.
                    output.append(self._buffer[0])
                    self._buffer = self._buffer[1:]

            else:
                # Inside think block: reasoning content must be suppressed.
                idx = self._buffer.find("<")
                if idx == -1:
                    # No potential closing tag; discard reasoning content
                    self._buffer = ""
                    break

                if idx > 0:
                    # Discard reasoning content preceding '<'
                    self._buffer = self._buffer[idx:]

                # self._buffer now starts with '<'
                lower_buf = self._buffer.lower()

                # Check for closing tag
                if lower_buf.startswith(self.CLOSE_TAG):
                    self.in_think_block = False
                    self._buffer = self._buffer[len(self.CLOSE_TAG):]
                    continue

                # Check if buffer forms a partial prefix of CLOSE_TAG
                if (
                    len(self._buffer) < len(self.CLOSE_TAG)
                    and self.CLOSE_TAG.startswith(lower_buf)
                ):
                    # Potential closing tag split across chunks; withhold and wait
                    break
                else:
                    # Starts with '<' but cannot be </think>. Discard '<' as reasoning.
                    self._buffer = self._buffer[1:]

        return "".join(output)

    def flush(self) -> str:
        """Flush any pending ordinary text at end of stream.

        Suppresses reasoning content from unclosed think blocks and discards
        incomplete tag prefixes.

        Returns:
            Flushed user-facing text, or empty string if no valid text remains.
        """
        if self.in_think_block:
            # Generation ended inside think block: discard all unclosed reasoning
            self._buffer = ""
            return ""

        if not self._buffer:
            return ""

        lower_buf = self._buffer.lower()

        # If buffer holds a partial opening or closing tag, suppress it
        is_open_prefix = (
            len(self._buffer) < len(self.OPEN_TAG)
            and self.OPEN_TAG.startswith(lower_buf)
        )
        is_close_prefix = (
            len(self._buffer) < len(self.CLOSE_TAG)
            and self.CLOSE_TAG.startswith(lower_buf)
        )

        if is_open_prefix or is_close_prefix:
            self._buffer = ""
            return ""

        # Emit any remaining ordinary buffered text
        res = self._buffer
        self._buffer = ""
        return res
