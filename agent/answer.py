"""The final `Answer` schema and its validation in code (contracts/agent-answer.md).

Rules checked after the schema:
1. every `cite_id` was returned by a tool in this run;
2. every number in `summary`/`points` appears in a cited chunk text or fact value (TR
   `1.234,5` and EN `1,234.5` both normalised), or is such a number times 10^3, 10^6 or 10^9
   (tables state amounts in thousands or millions); a percentage may instead be derived from
   two cited facts or chunk numbers and must match within 0.1 pp; a number citing a verified
   calculation (`k:` item, Phase 4) must equal its output at the precision shown;
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

from tools.documents import table_header_unit
from tools.types import SeenItem

DISCLAIMER = "Yatırım tavsiyesi değildir / Not investment advice"
PERCENT_TOLERANCE = Decimal("0.1")
# Scale words after a number: "23,1 milyar TL", "$31,778 million".
SCALE_WORDS: dict[str, Decimal] = {
    "bin": Decimal(10) ** 3,
    "thousand": Decimal(10) ** 3,
    "milyon": Decimal(10) ** 6,
    "million": Decimal(10) ** 6,
    "mn": Decimal(10) ** 6,
    "milyar": Decimal(10) ** 9,
    "billion": Decimal(10) ** 9,
    "bn": Decimal(10) ** 9,
    "trilyon": Decimal(10) ** 12,
    "trillion": Decimal(10) ** 12,
}
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


# "[c:…]", "[f:12]", or a chunk id without its "c:" prefix (seen in production).
_INLINE_CITE = re.compile(r"\[([cf]:[^\]\s]+|[0-9a-f]{64})\]")


_BARE_CHUNK_ID = re.compile(r"[0-9a-f]{64}")


def _prefixed(cite_id: str) -> str:
    """A chunk id written without its "c:" prefix gets it back."""
    return f"c:{cite_id}" if _BARE_CHUNK_ID.fullmatch(cite_id) else cite_id


def normalize_markers(answer: Answer) -> Answer:
    """Repair cite ids the model wrote in the wrong form before they are validated.

    A bare chunk id gets its "c:" prefix back (in `citations` and `points`), and a cite id
    written inline in the summary becomes its `[n]` marker.
    """
    citations = [c.model_copy(update={"cite_id": _prefixed(c.cite_id)}) for c in answer.citations]
    points = [
        p.model_copy(update={"cite_ids": [_prefixed(c) for c in p.cite_ids]}) for p in answer.points
    ]
    by_id = {c.cite_id: c.marker for c in citations}

    def marker(match: re.Match[str]) -> str:
        cite_id = _prefixed(match.group(1))
        if cite_id not in by_id:
            by_id[cite_id] = max((c.marker for c in citations), default=0) + 1
            citations.append(CitationRef(marker=by_id[cite_id], cite_id=cite_id))
        return f"[{by_id[cite_id]}]"

    summary = _INLINE_CITE.sub(marker, answer.summary)
    fixed = answer.model_copy(update={"summary": summary, "citations": citations, "points": points})
    return answer if fixed == answer else fixed


def answer_json_schema() -> dict[str, Any]:
    return Answer.model_json_schema()


_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|ocak|şubat|mart|nisan|mayıs|haziran|temmuz|ağustos|eylül|ekim|kasım|aralık"
)
# Labels that contain digits but are not figures: citation markers, dates, years, quarters.
_NOT_FIGURES = re.compile(
    r"\[\d+\]"
    r"|\b\d{1,2}[./]\d{1,2}[./](?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}-\d{2}-\d{2}\b"
    rf"|\b(?:{_MONTHS})\s+\d{{1,2}}\b"
    rf"|\b\d{{1,2}}\s+(?:{_MONTHS})"
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
_SCALE_AFTER = re.compile(rf"\s?({'|'.join(SCALE_WORDS)})(?![\wçğıöşü])", re.IGNORECASE)
_CURRENCY_AFTER = re.compile(
    r"\s?(?:TL|TRY|USD|EUR|dolar|dollars?|lira|₺)(?![\wçğıöşü])", re.IGNORECASE
)
_CURRENCY_BEFORE = re.compile(r"(?:\$|€|₺|USD|TRY|TL|EUR)\s?$")


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
    # "23,1 milyar" has multiplier 10^9; `unit` is true when a scale word or currency is attached.
    multiplier: Decimal = Decimal(1)
    unit: bool = False
    places: int = 0


def figures(text: str, language: Language) -> list[Figure]:
    cleaned = _NOT_FIGURES.sub(" ", text)
    decimal = "," if language == "tr" else "."
    found = []
    for match in _NUMBER.finditer(cleaned):
        value = parse_number(match.group(), language)
        if value is None:
            found.append(Figure(Decimal(-1), False, match.group()))  # malformed: never matches
            continue
        percent = bool(_PERCENT_AFTER.match(cleaned, match.end())) or bool(
            _PERCENT_BEFORE.search(cleaned[: match.start()])
        )
        scale = _SCALE_AFTER.match(cleaned, match.end())
        multiplier = SCALE_WORDS[scale.group(1).lower()] if scale else Decimal(1)
        after = scale.end() if scale else match.end()
        currency = bool(_CURRENCY_AFTER.match(cleaned, after)) or bool(
            _CURRENCY_BEFORE.search(cleaned[: match.start()])
        )
        _, _, fraction = match.group().partition(decimal)
        found.append(
            Figure(
                value,
                percent,
                match.group(),
                multiplier=multiplier,
                unit=bool(scale) or currency,
                places=len(fraction),
            )
        )
    return found


CALCULATION_PREFIX = "k:"


@dataclass
class _Sources:
    raw: set[Decimal] = field(default_factory=set)  # every number as written
    amounts: set[Decimal] = field(default_factory=set)  # with its scale applied
    # Verified calculation outputs (`k:` items, Phase 4): matched at the precision shown.
    calculated: set[Decimal] = field(default_factory=set)


def _sources(items: Iterable[SeenItem]) -> _Sources:
    found = _Sources()
    for item in items:
        if item.cite_id.startswith(CALCULATION_PREFIX):
            if item.value is not None:
                found.calculated.add(item.value)
            continue
        declared: set[Decimal] = set()
        if item.value is not None:
            # A fact's text renders its unit ("1.234,5 milyon TRY"), so it is parsed below too.
            found.raw.add(item.value.normalize())
            found.amounts.add((item.value * (item.scale or 1)).normalize())
        elif item.scale is not None:
            declared.add(item.scale)
        elif (header := table_header_unit(item.text)) is not None:
            declared.add(header[0])
        for language in ("tr", "en"):
            for f in figures(item.text, language):
                if f.value < 0:
                    continue
                found.raw.add(f.value.normalize())
                if f.multiplier != 1 or f.percent:
                    found.amounts.add((f.value * f.multiplier).normalize())
                elif declared:
                    found.amounts.update((f.value * s).normalize() for s in declared)
                else:
                    found.amounts.add(f.value.normalize())
    return found


def _rounds_to(amount: Decimal, figure: Figure) -> bool:
    """`amount` written in the figure's scale and precision: 31,778 million is "31.8 billion"."""
    scaled = amount / figure.multiplier
    try:
        return scaled.quantize(Decimal(1).scaleb(-figure.places)) == figure.value
    except InvalidOperation:
        return False


def _calculated(figure: Figure, outputs: set[Decimal]) -> bool:
    """A calculation output written at the figure's precision; a ratio may be shown as a %."""
    candidates = [*outputs, *(o * 100 for o in outputs)] if figure.percent else outputs
    return any(_rounds_to(output, figure) for output in candidates)


def _supported(figure: Figure, sources: _Sources) -> bool:
    if sources.calculated and _calculated(figure, sources.calculated):
        return True
    if figure.unit:
        # A stated unit must match the source amount, so "56016 bin TL" fails against a
        # million-TL table and against a table whose unit is unknown.
        return any(_rounds_to(amount, figure) for amount in sources.amounts)
    value = figure.value.normalize()
    return value in sources.raw or value in sources.amounts


def _derived_percents(items: Iterable[SeenItem]) -> list[Decimal]:
    items = [i for i in items if not i.cite_id.startswith(CALCULATION_PREFIX)]
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
    sources = _sources(cited)
    derived: list[Decimal] | None = None
    missing = []
    for figure in figures(text, language):
        if figure.value >= 0 and _supported(figure, sources):
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
