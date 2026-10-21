"""The final `Answer` schema and its validation in code (contracts/agent-answer.md).

Rules checked after the schema:
1. every `cite_id` was returned by a tool in this run;
2. every number in `summary`/`points` appears in a cited chunk text or fact value (TR
   `1.234,5` and EN `1,234.5` both normalised), or is such a number times 10^3, 10^6 or 10^9
   (tables state amounts in thousands or millions); a percentage may instead be derived from
   two cited facts or chunk numbers and must match within 0.1 pp;
3. the disclaimer is the fixed string (the server overwrites it);
4. `language` matches the detected question language.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from itertools import permutations
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tools.types import SeenItem

DISCLAIMER = "Yatırım tavsiyesi değildir / Not investment advice"
PERCENT_TOLERANCE = Decimal("0.1")
# "551928" in a million-TL table supports "551.928.000.000 TL".
UNIT_SCALES = (Decimal(10) ** 3, Decimal(10) ** 6, Decimal(10) ** 9)
# Bounds the pairwise search for derived percentages over chunk numbers.
MAX_DERIVATION_INPUTS = 120


Language = Literal["tr", "en"]
AnswerStatus = Literal["completed", "partial", "refused", "no_evidence"]

NO_VERIFIED_ANSWER: dict[Language, str] = {
    "tr": "Kaynaklarla doğrulanmış bir yanıt üretilemedi. Soruyu şirket ve dönemle "
    "daha açık sorarak tekrar deneyin.",
    "en": "No answer could be verified against the sources. Try again with the company "
    "and period stated more clearly.",
}


class CitationRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marker: int = Field(ge=1)
    cite_id: str


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    cite_ids: list[str] = Field(default_factory=list)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Language
    status: AnswerStatus
    summary: str
    points: list[Point] = Field(default_factory=list)
    citations: list[CitationRef] = Field(default_factory=list)
    disclaimer: str = DISCLAIMER


def answer_json_schema() -> dict[str, Any]:
    return Answer.model_json_schema()


# Labels that contain digits but are not figures: citation markers, dates, years, quarters.
_NOT_FIGURES = re.compile(
    r"\[\d+\]"
    r"|\b\d{1,2}[./]\d{1,2}[./](?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}-\d{2}-\d{2}\b"
    r"|\b(?:19|20)\d{2}(?:-(?:Q[1-4]|FY))?\b"
    r"|\bQ[1-4]\b"
    r"|\b[1-4]\.\s?çeyrek"
    r"|\b[1-4](?:st|nd|rd|th)\s+quarter"
    r"|\b(?:3|6|9|12)\s?(?:aylık|ay)\b"
    r"|\b(?:3|6|9|12)[\s-]months?\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)*")
_PERCENT_AFTER = re.compile(r"\s?%")
_PERCENT_BEFORE = re.compile(r"(?:%|yüzde)\s?$", re.IGNORECASE)


def parse_number(token: str, language: Language) -> Decimal | None:
    """`1.234,5` (tr) / `1,234.5` (en) to a Decimal; None if the token is malformed."""
    thousands, decimal = (".", ",") if language == "tr" else (",", ".")
    if token.count(decimal) > 1:
        return None
    whole, _, fraction = token.partition(decimal)
    groups = whole.split(thousands)
    if len(groups) > 1 and (not 1 <= len(groups[0]) <= 3 or any(len(g) != 3 for g in groups[1:])):
        return None
    try:
        return Decimal("".join(groups) + (f".{fraction}" if fraction else ""))
    except InvalidOperation:
        return None


@dataclass(frozen=True)
class Figure:
    value: Decimal
    percent: bool
    raw: str


def figures(text: str, language: Language) -> list[Figure]:
    cleaned = _NOT_FIGURES.sub(" ", text)
    found = []
    for match in _NUMBER.finditer(cleaned):
        value = parse_number(match.group(), language)
        if value is None:
            found.append(Figure(Decimal(-1), False, match.group()))  # malformed: never matches
            continue
        percent = bool(_PERCENT_AFTER.match(cleaned, match.end())) or bool(
            _PERCENT_BEFORE.search(cleaned[: match.start()])
        )
        found.append(Figure(value, percent, match.group()))
    return found


def _source_numbers(items: Iterable[SeenItem]) -> set[Decimal]:
    numbers: set[Decimal] = set()
    for item in items:
        if item.value is not None:
            numbers.add(item.value.normalize())
        for language in ("tr", "en"):
            numbers.update(
                f.value.normalize() for f in figures(item.text, language) if f.value >= 0
            )
    return numbers


def _supported(value: Decimal, numbers: set[Decimal]) -> bool:
    value = value.normalize()
    if value in numbers:
        return True
    return any((value / scale).normalize() in numbers for scale in UNIT_SCALES if value >= scale)


def _derived_percents(items: Iterable[SeenItem]) -> list[Decimal]:
    values = [i.value for i in items if i.value is not None and i.value != 0]
    seen = set(values)
    for item in items:
        for language in ("tr", "en"):
            for figure in figures(item.text, language):
                if figure.value > 0 and not figure.percent and figure.value not in seen:
                    seen.add(figure.value)
                    values.append(figure.value)
    values = values[:MAX_DERIVATION_INPUTS]
    derived = []
    for a, b in permutations(values, 2):
        derived.append(a / b * 100)
        derived.append((a - b) / abs(b) * 100)
    return derived


def unsupported_figures(text: str, language: Language, cited: list[SeenItem]) -> list[str]:
    numbers = _source_numbers(cited)
    derived: list[Decimal] | None = None
    missing = []
    for figure in figures(text, language):
        if figure.value >= 0 and _supported(figure.value, numbers):
            continue
        if figure.percent and figure.value >= 0:
            if derived is None:
                derived = _derived_percents(cited)
            if any(abs(abs(d) - figure.value) <= PERCENT_TOLERANCE for d in derived):
                continue
        missing.append(figure.raw)
    return missing


@dataclass
class Validation:
    errors: list[str] = field(default_factory=list)
    valid_points: list[Point] = field(default_factory=list)
    summary_ok: bool = True

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_answer(
    answer: Answer, seen_items: Mapping[str, SeenItem], language: Language
) -> Validation:
    result = Validation()
    if answer.language != language:
        result.errors.append(f"language must be {language!r}, got {answer.language!r}")

    unknown = sorted(
        ({c.cite_id for c in answer.citations} | {c for p in answer.points for c in p.cite_ids})
        - seen_items.keys()
    )
    if unknown:
        result.errors.append(f"unknown cite_id(s) not returned by any tool: {unknown}")

    markers = {c.marker: c.cite_id for c in answer.citations}
    used = {int(m) for m in re.findall(r"\[(\d+)\]", answer.summary)}
    if missing_markers := sorted(used - markers.keys()):
        result.errors.append(f"summary markers without a citation: {missing_markers}")
        result.summary_ok = False
    if any(markers[m] not in seen_items for m in used if m in markers):
        result.summary_ok = False

    summary_items = [seen_items[c] for c in markers.values() if c in seen_items]
    if bad := unsupported_figures(answer.summary, language, summary_items):
        result.errors.append(f"summary numbers not found in cited sources: {bad}")
        result.summary_ok = False

    for index, point in enumerate(answer.points):
        if not point.cite_ids:
            result.errors.append(f"point {index} has no cite_ids")
            continue
        if any(c not in seen_items for c in point.cite_ids):
            continue  # already reported as unknown
        cited = [seen_items[c] for c in point.cite_ids]
        if bad := unsupported_figures(point.text, language, cited):
            result.errors.append(f"point {index} numbers not found in cited sources: {bad}")
            continue
        result.valid_points.append(point)
    return result


def finalize(answer: Answer) -> Answer:
    """Server-side overwrite of the disclaimer."""
    return answer.model_copy(update={"disclaimer": DISCLAIMER})


def to_partial(answer: Answer, validation: Validation, language: Language) -> Answer:
    """Keep only validated content after a failed re-ask."""
    cited = {c for p in validation.valid_points for c in p.cite_ids}
    summary = answer.summary if validation.summary_ok and answer.language == language else ""
    keep_markers = {c.marker for c in answer.citations if c.cite_id in cited}
    if summary:
        keep_markers |= {
            c.marker
            for c in answer.citations
            if f"[{c.marker}]" in summary and c.marker not in keep_markers
        }
    if not summary and not validation.valid_points:
        summary = NO_VERIFIED_ANSWER[language]
    return Answer(
        language=language,
        status="partial",
        summary=summary,
        points=validation.valid_points,
        citations=[c for c in answer.citations if c.marker in keep_markers],
    )
