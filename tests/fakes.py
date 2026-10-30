"""A scripted stand-in for the Anthropic client, built from the SDK's own response types."""

from __future__ import annotations

import copy
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

from anthropic.types import Message, TextBlock, ThinkingBlock, ToolUseBlock, Usage


def text(value: str) -> TextBlock:
    return TextBlock(type="text", text=value, citations=None)


def tool_use(name: str, arguments: dict[str, Any], id_: str = "toolu_1") -> ToolUseBlock:
    return ToolUseBlock(type="tool_use", id=id_, name=name, input=arguments)


def thinking() -> ThinkingBlock:
    return ThinkingBlock(type="thinking", thinking="", signature="sig")


def message(
    content: Sequence[Any],
    stop_reason: str = "end_turn",
    stop_details: Any = None,
) -> Any:
    return Message.model_construct(
        id="msg_1",
        type="message",
        role="assistant",
        model="claude-opus-5-5",
        content=list(content),
        stop_reason=stop_reason,
        stop_sequence=None,
        stop_details=stop_details,
        usage=Usage.model_construct(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=3,
            cache_creation_input_tokens=2,
        ),
    )


def refusal(category: str = "cyber") -> Any:
    return message([], "refusal", SimpleNamespace(category=category, explanation="x"))


class ScriptedClient:
    """Returns queued responses in order and records every request."""

    def __init__(self, responses: Sequence[Any]) -> None:
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(copy.deepcopy(kwargs))
        if not self._responses:
            raise AssertionError("the agent made more API calls than the script expected")
        return self._responses.pop(0)

    @property
    def exhausted(self) -> bool:
        return not self._responses
