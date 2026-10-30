"""The tactical coach: a tri-language chat that answers only from tool results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from touchline.agents.loop import DEFAULT_MODEL, AgentConfig, ClaudeClient, Usage, run_agent
from touchline.agents.prompts import COACH_SYSTEM
from touchline.checks.faithfulness import FaithfulnessResult, check_faithfulness
from touchline.checks.routing import (
    Route,
    declined_message,
    detect_language,
    referral_message,
    route_question,
    unverified_message,
)
from touchline.tools import ToolBox

_EFFORT = "medium"
_MAX_TOKENS = 8000

Outcome = Literal["answered", "referred", "declined", "unverified", "incomplete"]


@dataclass
class CoachReply:
    text: str
    outcome: Outcome
    route: Route
    language: str
    faithfulness: FaithfulnessResult | None = None
    tool_calls_made: int = 0


class CoachSession:
    """One player's conversation. The evidence for numbers accumulates across its turns."""

    def __init__(
        self,
        client: ClaudeClient,
        toolbox: ToolBox,
        player_id: int,
        player: str,
        match_id: int | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._client = client
        self._toolbox = toolbox
        self._config = AgentConfig(
            system=COACH_SYSTEM, effort=_EFFORT, max_tokens=_MAX_TOKENS, model=model
        )
        scope = f"match_id {match_id}" if match_id is not None else "all ingested matches"
        # Per-session facts go in the first user turn so the cached system prefix stays shared.
        self._context = f"[Player: {player}, player_id {player_id}. Scope: {scope}.]\n\n"
        self._messages: list[Any] = []
        self.usage = Usage()

    def ask(self, question: str) -> CoachReply:
        language = detect_language(question)
        route = route_question(question)
        if route is not Route.COACHING:
            # No API call and nothing added to the conversation.
            return CoachReply(referral_message(route, language), "referred", route, language)

        history_mark = len(self._messages)
        ledger_mark = len(self._toolbox.ledger)
        content = (self._context if not self._messages else "") + question
        self._messages.append({"role": "user", "content": content})
        reply = self._answer(language)
        if reply.outcome != "answered":
            # A turn the player never saw must not shape later answers or count as evidence.
            del self._messages[history_mark:]
            del self._toolbox.ledger[ledger_mark:]
        return reply

    def _answer(self, language: str) -> CoachReply:
        tool_calls = 0
        verdict: FaithfulnessResult | None = None
        for attempt in range(2):
            run = run_agent(self._client, self._config, self._toolbox, self._messages)
            self.usage.add(run.usage)
            tool_calls += run.tool_calls_made
            if run.status == "refused":
                return CoachReply(
                    declined_message(language),
                    "declined",
                    Route.COACHING,
                    language,
                    tool_calls_made=tool_calls,
                )
            if run.status == "incomplete":
                return CoachReply(
                    unverified_message(language),
                    "incomplete",
                    Route.COACHING,
                    language,
                    tool_calls_made=tool_calls,
                )
            if self._toolbox.evidence:
                verdict = check_faithfulness(run.text, self._toolbox.evidence, language)
                if verdict.passed:
                    return CoachReply(
                        run.text, "answered", Route.COACHING, language, verdict, tool_calls
                    )
                problem = (
                    "These numbers do not appear in your tool results: "
                    f"{', '.join(verdict.unsupported)}. Query the data to back them, or answer "
                    "without them. Do not derive numbers yourself."
                )
            else:
                # Auto tool choice does not guarantee a call, so check that one happened.
                problem = "You did not call any tools. Query the data before you answer."
            if attempt == 0:
                self._messages.append({"role": "user", "content": f"[Automated check] {problem}"})
        return CoachReply(
            unverified_message(language),
            "unverified",
            Route.COACHING,
            language,
            verdict,
            tool_calls,
        )
