"""Check that every number in a text appears in the tool results of its conversation.

The check proves provenance, not meaning. It catches invented numbers and arithmetic the
model did on its own, in the formats a tri-language answer uses ("1,5", "%12", "12 %",
"3-1", "yedi"). It cannot tell whether a true number is attached to the right claim: "12
shots" passes if a 12 appears anywhere in the evidence. It fails closed, so an unmatched
number blocks the text even if the number is harmless.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

_FLOAT_TOLERANCE = 1e-9
# A rounded integer in prose must not claim more than rounding allows: "1" for 0.6 xG, or "0"
# for 0.3 xG, is misleading. Up to this magnitude, integers must match exactly.
_MIN_MAGNITUDE_FOR_ROUNDED_INTEGER = 1.5

# Phrases whose digits are names rather than measurements.
_EXEMPT_PHRASES = re.compile(
    r"\b(?:per|por|cada)\s+90\b|\b90\s+(?:minutes?|minutos?|dakika\w*)\b|/\s?90\b"
    r"|\b360\b|\b1st half\b|\b2nd half\b",
    re.IGNORECASE,
)
_LIST_MARKER = re.compile(r"^\s*\d+[.)]\s+", re.MULTILINE)
_COMPOUND = re.compile(r"(?<![\w.,])\d+(?:\s?[-–]\s?\d+)+(?![\w]|[.,]\d)")
_NUMBER = re.compile(
    r"(?<![\w.,])"
    r"(?P<pre>%\s?)?"
    r"(?P<body>\d+(?:[.,]\d+)*)"
    r"(?P<post>\s?(?:%|percent\b|por ciento\b))?"
)
_NUMBER_WORDS: dict[str, dict[str, float]] = {
    # "one", "uno/una" and "bir" are left out: they mostly mean "a" or "some".
    "en": {
        "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "twenty": 20,
    },
    "es": {
        "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
        "nueve": 9, "diez": 10, "once": 11, "doce": 12, "veinte": 20,
    },
    "tr": {
        "iki": 2, "üç": 3, "dört": 4, "beş": 5, "altı": 6, "yedi": 7, "sekiz": 8,
        "dokuz": 9, "on": 10, "yirmi": 20,
    },
}  # fmt: skip


@dataclass(frozen=True)
class NumberToken:
    """A number found in text, with every value its spelling could stand for."""

    text: str
    candidates: tuple[tuple[float, int], ...]  # (value, decimal places)
    is_percent: bool = False


@dataclass(frozen=True)
class FaithfulnessResult:
    checked: tuple[str, ...]
    unsupported: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.unsupported


def _decimals(fraction: str) -> int:
    return len(fraction)


def parse_candidates(body: str) -> tuple[tuple[float, int], ...]:
    """Every value a numeric spelling can mean. "1,234" is 1.234 or 1234; "1,5" is only 1.5."""
    separators = [char for char in body if char in ".,"]
    if not separators:
        return ((float(body), 0),)
    if len(set(separators)) == 2:
        # Both separators: the last one is the decimal point, as in 1.234,5 or 1,234.5.
        last = max(body.rfind("."), body.rfind(","))
        whole, fraction = body[:last], body[last + 1 :]
        whole = whole.replace(".", "").replace(",", "")
        return ((float(f"{whole}.{fraction}"), _decimals(fraction)),)
    groups = re.split(r"[.,]", body)
    if len(groups) > 2:
        # Repeated separator can only be grouping: 1.234.567.
        return ((float("".join(groups)), 0),)
    whole, fraction = groups
    values = [(float(f"{whole}.{fraction}"), _decimals(fraction))]
    if len(fraction) == 3:
        values.append((float(whole + fraction), 0))
    return tuple(values)


def extract_numbers(text: str, language: str | None = None) -> list[NumberToken]:
    """Find numbers in digits, and spelled-out numbers in the given or every language."""
    cleaned = _EXEMPT_PHRASES.sub(" ", _LIST_MARKER.sub(" ", text))
    tokens: list[NumberToken] = []

    def remove_compound(match: re.Match[str]) -> str:
        parts = re.split(r"\s?[-–]\s?", match.group())
        joined = "-".join(parts)
        tokens.append(NumberToken(joined, tuple((float(part), 0) for part in parts)))
        return " "

    cleaned = _COMPOUND.sub(remove_compound, cleaned)
    for match in _NUMBER.finditer(cleaned):
        is_percent = bool(match.group("pre") or match.group("post"))
        tokens.append(
            NumberToken(match.group().strip(), parse_candidates(match.group("body")), is_percent)
        )

    languages = [language] if language in _NUMBER_WORDS else list(_NUMBER_WORDS)
    for code in languages:
        for word, value in _NUMBER_WORDS[str(code)].items():
            for _ in re.finditer(rf"(?<!\w){word}(?!\w)", cleaned, re.IGNORECASE):
                tokens.append(NumberToken(word, ((value, -1),)))
    return tokens


def _walk(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _walk(item)
    else:
        yield value


@dataclass(frozen=True)
class _Evidence:
    numbers: frozenset[float]
    literals: frozenset[str]


def _collect_evidence(outputs: Iterable[Any]) -> _Evidence:
    """Numeric leaves, plus numeric or score-like strings. Dates and ids in text do not count."""
    numbers: set[float] = set()
    literals: set[str] = set()
    for output in outputs:
        for leaf in _walk(output):
            if isinstance(leaf, bool) or leaf is None:
                continue
            if isinstance(leaf, int | float):
                numbers.add(float(leaf))
            elif isinstance(leaf, str):
                stripped = leaf.strip()
                if re.fullmatch(r"\d+(?:\.\d+)?", stripped):
                    numbers.add(float(stripped))
                elif re.fullmatch(r"\d+(?:-\d+)+", stripped):
                    literals.add(stripped)
    return _Evidence(frozenset(numbers), frozenset(literals))


def _value_matches(value: float, decimals: int, evidence: float) -> bool:
    if decimals == -1:  # A spelled-out number must be exact.
        return abs(evidence - value) <= _FLOAT_TOLERANCE
    is_rounded_integer = decimals == 0 and evidence != int(evidence)
    if is_rounded_integer and abs(evidence) <= _MIN_MAGNITUDE_FOR_ROUNDED_INTEGER:
        return False
    return abs(evidence - value) <= 0.5 * 10.0**-decimals + _FLOAT_TOLERANCE


def _token_supported(token: NumberToken, evidence: _Evidence) -> bool:
    if "-" in token.text and any(char.isdigit() for char in token.text):
        if token.text in evidence.literals:
            return True
        # Not a known score or formation: each part must stand on its own.
        return all(
            any(_value_matches(value, 0, item) for item in evidence.numbers)
            for value, _ in token.candidates
        )
    for value, decimals in token.candidates:
        for item in evidence.numbers:
            if _value_matches(value, decimals, item):
                return True
            # 12% may come from a stored ratio of 0.12.
            if token.is_percent and _value_matches(value, decimals, item * 100):
                return True
    return False


def check_faithfulness(
    text: str, tool_outputs: Iterable[Any], language: str | None = None
) -> FaithfulnessResult:
    """Check every number in text against the outputs of the conversation's tool calls."""
    evidence = _collect_evidence(tool_outputs)
    tokens = extract_numbers(text, language)
    unsupported = [token.text for token in tokens if not _token_supported(token, evidence)]
    return FaithfulnessResult(
        checked=tuple(token.text for token in tokens),
        unsupported=tuple(dict.fromkeys(unsupported)),
    )
