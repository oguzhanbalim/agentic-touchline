"""A small agentic loop over the Messages API.

A manual loop rather than the SDK's beta tool runner because this project needs three things
at the seam: a ledger of every tool result for the faithfulness check, explicit handling of
every stop reason, and a client that tests can replace with a scripted fake.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from touchline.tools import ToolBox, tool_definitions

DEFAULT_MODEL = "claude-opus-5-5"

RunStatus = Literal["completed", "refused", "incomplete"]

_MAX_ITERATIONS = 12


class ClaudeClient(Protocol):
    """The slice of `anthropic.Anthropic` this project uses, so tests can inject a fake.

    Only `messages.create(**kwargs)` is called. The SDK's overloads do not fit a narrower
    Protocol, so the attribute stays `Any`; tests pin the request shape instead.
    """

    @property
    def messages(self) -> Any: ...


@dataclass(frozen=True)
class AgentConfig:
    system: str
    effort: str
    max_tokens: int
    model: str = DEFAULT_MODEL
    output_schema: dict[str, Any] | None = None
    max_iterations: int = _MAX_ITERATIONS


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def add(self, usage: Any) -> None:
        """Add an API usage object, or another Usage. Fields the API omits count as zero."""
        for name in vars(self):
            setattr(self, name, getattr(self, name) + (getattr(usage, name, 0) or 0))


@dataclass
class AgentRun:
    status: RunStatus
    stop_reason: str | None
    text: str = ""
    refusal_category: str | None = None
    tool_calls_made: int = 0
    iterations: int = 0
    usage: Usage = field(default_factory=Usage)


def build_request(config: AgentConfig, messages: Sequence[Any]) -> dict[str, Any]:
    """Assemble one request.

    Adaptive thinking and an explicit effort; no thinking budget, sampling parameters or
    prefill, which claude-opus-5-5 rejects. Tool choice stays on auto because it also
    rejects forced tool use, so the strict schemas carry the guarantee on arguments.

    Render order is tools, system, messages. The tools and system are byte-stable, so the
    explicit breakpoint caches that whole prefix, and the top-level marker caches the
    growing conversation.
    """
    output_config: dict[str, Any] = {"effort": config.effort}
    if config.output_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": config.output_schema}
    return {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "thinking": {"type": "adaptive"},
        "output_config": output_config,
        "tools": tool_definitions(),
        "tool_choice": {"type": "auto"},
        "system": [{"type": "text", "text": config.system, "cache_control": {"type": "ephemeral"}}],
        "cache_control": {"type": "ephemeral"},
        "messages": list(messages),
    }


def _text_of(content: Sequence[Any]) -> str:
    return "\n".join(block.text for block in content if block.type == "text").strip()


def run_agent(
    client: ClaudeClient,
    config: AgentConfig,
    toolbox: ToolBox,
    messages: list[Any],
) -> AgentRun:
    """Run until the model finishes, stops early or hits the iteration cap.

    `messages` is extended in place with the model's turns and the tool results, so a caller
    can continue the conversation. Assistant content is appended unmodified: thinking blocks
    must go back to the API exactly as they came.
    """
    run = AgentRun(status="incomplete", stop_reason=None)
    for _ in range(config.max_iterations):
        response = client.messages.create(**build_request(config, messages))
        run.iterations += 1
        run.usage.add(response.usage)
        run.stop_reason = response.stop_reason

        if response.stop_reason in ("end_turn", "stop_sequence"):
            messages.append({"role": "assistant", "content": response.content})
            run.status = "completed"
            run.text = _text_of(response.content)
            return run

        if response.stop_reason == "refusal":
            # Not appended: a refused turn must not be replayed in later requests.
            details = getattr(response, "stop_details", None)
            run.status = "refused"
            run.refusal_category = getattr(details, "category", None)
            return run

        if response.stop_reason == "pause_turn":
            # The API resumes from the trailing assistant turn; no extra user message.
            messages.append({"role": "assistant", "content": response.content})
            continue

        if response.stop_reason == "tool_use":
            tool_blocks = [block for block in response.content if block.type == "tool_use"]
            if tool_blocks:
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": _run_tools(tool_blocks, toolbox)})
                run.tool_calls_made += len(tool_blocks)
                continue

        # max_tokens, model_context_window_exceeded, or anything new: stop, do not guess.
        run.text = _text_of(response.content)
        return run
    return run


def _run_tools(blocks: Sequence[Any], toolbox: ToolBox) -> list[dict[str, Any]]:
    results = []
    for block in blocks:
        call = toolbox.run(block.name, dict(block.input))
        result: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": call.content,
        }
        if call.is_error:
            result["is_error"] = True
        results.append(result)
    return results
