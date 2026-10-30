"""Routing rules and the 30-question boundary set."""

from __future__ import annotations

import pytest

from touchline.checks.boundary_cases import BOUNDARY_CASES
from touchline.checks.routing import (
    Route,
    declined_message,
    detect_language,
    referral_message,
    route_question,
    unverified_message,
)


def test_the_set_has_thirty_questions_covering_every_route_and_language() -> None:
    assert len(BOUNDARY_CASES) == 30
    assert {case.route for case in BOUNDARY_CASES} == set(Route)
    assert {case.language for case in BOUNDARY_CASES} == {"en", "es", "tr"}
    assert len({case.question for case in BOUNDARY_CASES}) == 30


@pytest.mark.parametrize("case", BOUNDARY_CASES, ids=lambda case: case.question[:40])
def test_boundary_route(case) -> None:
    assert route_question(case.question) is case.route


@pytest.mark.parametrize("case", BOUNDARY_CASES, ids=lambda case: case.question[:40])
def test_boundary_language(case) -> None:
    assert detect_language(case.question) == case.language


@pytest.mark.parametrize(
    "question",
    [
        "I took a knock on Sunday and can't run properly.",
        "Should I sit out the next game after that tackle?",
        "Is it time to look for a new team?",
    ],
)
def test_known_misses_keyword_rules_do_not_catch_paraphrases(question: str) -> None:
    """These reach the model, where the system prompt is the only remaining check."""
    assert route_question(question) is Route.COACHING


def test_a_medical_word_wins_over_a_contract_word() -> None:
    assert route_question("Does my injury affect my contract?") is Route.MEDICAL


@pytest.mark.parametrize("route", [Route.MEDICAL, Route.CONTRACT, Route.TRANSFER])
@pytest.mark.parametrize("language", ["en", "es", "tr"])
def test_every_referral_exists_and_points_to_people(route: Route, language: str) -> None:
    message = referral_message(route, language)
    assert message
    assert not any(char.isdigit() for char in message)


@pytest.mark.parametrize("language", ["en", "es", "tr"])
def test_fallback_messages_exist(language: str) -> None:
    assert unverified_message(language)
    assert declined_message(language)


def test_unknown_text_defaults_to_english() -> None:
    assert detect_language("???") == "en"
