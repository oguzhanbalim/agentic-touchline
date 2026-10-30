"""Number extraction and matching across the formats the three languages use."""

from __future__ import annotations

import pytest

from touchline.checks.faithfulness import check_faithfulness, extract_numbers, parse_candidates

EVIDENCE = [
    {
        "xg": 1.5,
        "shots": 12,
        "ratio": 0.12,
        "median_space_yards": 5.1,
        "between_lines_pct": 23.3,
        "low_xg": 0.62,
        "tiny_xg": 0.3,
        "zero": 0,
        "passes": 1234,
        "season": "2024",
        "date": "2024-06-18",
        "formation": "4-2-3-1",
        "player": "Player 99",
        "nested": [{"minute": 64}],
        "flag": True,
    }
]


@pytest.mark.parametrize(
    "text",
    [
        "He had 12 shots.",
        "xG of 1.5",
        "xG de 1,5",
        "1,5 xG",
        "%12 of receptions",  # Turkish percent, from 0.12 or 12.
        "12% of receptions",
        "12 % of receptions",
        "%12'si ceza sahasında",  # Turkish suffix after an apostrophe.
        "23,3 por ciento",
        "yüzde 23,3",
        "1,234 passes",  # English thousands.
        "1.234 pases",  # Spanish and Turkish thousands.
        "1234 passes",
        "0.6 xG",  # Rounded from 0.62.
        "0,62 xG",
        "5 yards",  # Rounded from 5.1.
        "5.1 yards",
        "in minute 64",
        "64'",
        "64th minute",
        "Euro 2024",  # A numeric string in the evidence.
        "a 4-2-3-1 shape",  # A literal string in the evidence.
        "per 90 minutes",
        "por 90",
        "90 dakikada",
        "the 360 frames",
        "in the 2nd half",
        "1. First item\n2. Second item",  # List markers are not claims.
        "twelve shots",
        "doce tiros",
        "no numbers at all",
    ],
)
def test_supported(text: str) -> None:
    result = check_faithfulness(text, EVIDENCE)
    assert result.passed, result.unsupported


@pytest.mark.parametrize(
    ("text", "unsupported"),
    [
        ("He had 13 shots.", "13"),
        ("xG de 1,6", "1,6"),
        ("%13 of receptions", "%13"),
        ("0.7 xG", "0.7"),  # 0.62 rounds to 0.6, not 0.7.
        ("1 xG", "1"),  # 0.62 must not become a whole goal's worth.
        ("0 xG", None),  # Exact zero is in the evidence.
        ("about 0.31 xG", "0.31"),  # Half of 0.62, a derived number.
        ("beat them 3-1", "3-1"),
        ("seven shots", "seven"),
        ("yedi şut", "yedi"),
        ("siete tiros", "siete"),
        ("the 18th", "18"),  # Date parts are not evidence.
        ("06", "06"),
        ("Player 99", "99"),  # Numbers inside evidence strings do not count.
    ],
)
def test_unsupported(text: str, unsupported: str | None) -> None:
    result = check_faithfulness(text, EVIDENCE)
    if unsupported is None:
        assert result.passed
    else:
        assert unsupported in result.unsupported, result


def test_booleans_are_not_numbers() -> None:
    assert not check_faithfulness("1 goal", [{"flag": True}]).passed


def test_empty_evidence_fails_any_number() -> None:
    assert not check_faithfulness("3 shots", []).passed


def test_language_limits_number_words() -> None:
    # "on" is ten in Turkish and only a preposition in English.
    assert check_faithfulness("on the left", [{}], "en").passed
    assert not check_faithfulness("on şut", [{}], "tr").passed


@pytest.mark.parametrize(
    ("body", "values"),
    [
        ("12", [12.0]),
        ("1,5", [1.5]),
        ("1.5", [1.5]),
        ("1,234", [1.234, 1234.0]),
        ("1.234", [1.234, 1234.0]),
        ("1.234,5", [1234.5]),
        ("1,234.5", [1234.5]),
        ("1.234.567", [1234567.0]),
    ],
)
def test_parse_candidates(body: str, values: list[float]) -> None:
    assert [value for value, _ in parse_candidates(body)] == values


def test_a_decimal_comma_list_is_not_merged() -> None:
    assert [token.text for token in extract_numbers("3, 4 and 5")] == ["3", "4", "5"]
