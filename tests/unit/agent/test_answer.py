"""Tests for agent.answer (T014)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from agent.answer import (
    DISCLAIMER,
    NO_VERIFIED_ANSWER,
    Answer,
    CitationRef,
    Point,
    answer_json_schema,
    figures,
    finalize,
    parse_number,
    to_partial,
    validate_answer,
)
from tools.types import SeenItem

SEEN = {
    "f:1": SeenItem(cite_id="f:1", text="net_income 2025-Q3: 1234.5 million TRY",
                    value=Decimal("1234.5"), source={}),
    "f:2": SeenItem(cite_id="f:2", text="net_income 2024-Q3: 1000 million TRY",
                    value=Decimal("1000"), source={}),
    "c:a": SeenItem(cite_id="c:a", text="FAVÖK marjı yüzde 25 seviyesinde, gelir 9.876,5 mn TL.",
                    source={}),
    "c:b": SeenItem(cite_id="c:b", text="Revenue was $391,035 million in fiscal 2024.",
                    source={}),
}  # fmt: skip


def _answer(
    summary: str,
    points: list[tuple[str, list[str]]] = (),  # type: ignore[assignment]
    citations: list[tuple[int, str]] = (),  # type: ignore[assignment]
    language: str = "tr",
) -> Answer:
    return Answer.model_validate(
        {
            "language": language,
            "status": "completed",
            "summary": summary,
            "points": [{"text": t, "cite_ids": c} for t, c in points],
            "citations": [{"marker": m, "cite_id": c} for m, c in citations],
        }
    )


@pytest.mark.parametrize(
    ("token", "language", "expected"),
    [
        ("1.234,5", "tr", Decimal("1234.5")),
        ("1,234.5", "en", Decimal("1234.5")),
        ("1234,5", "tr", Decimal("1234.5")),
        ("1234.5", "en", Decimal("1234.5")),
        ("391,035", "en", Decimal("391035")),
        ("1.000.000", "tr", Decimal("1000000")),
        ("25", "tr", Decimal("25")),
        ("1.23.4", "tr", None),
        ("12,34,567", "en", None),
        ("1,2,3", "tr", None),
    ],
)
def test_parse_number(token: str, language: str, expected: Decimal | None) -> None:
    assert parse_number(token, language) == expected  # type: ignore[arg-type]


def test_figures_skip_markers_years_and_quarters() -> None:
    found = figures("2025 3. çeyrek ve Q3 2024-FY sonucu [1] 1.234,5 TL, %12,5 arttı", "tr")
    assert [(f.value, f.percent) for f in found] == [
        (Decimal("1234.5"), False),
        (Decimal("12.5"), True),
    ]
    assert figures("3rd quarter of 2025 revenue grew 23.45%", "en")[0].percent


@pytest.mark.parametrize(
    ("summary", "points", "citations", "language", "errors"),
    [
        # TR formats match fact value and chunk text
        ("Net kâr 1.234,5 milyon TL [1].", [], [(1, "f:1")], "tr", 0),
        ("Gelir 9.876,5 mn TL [1].", [], [(1, "c:a")], "tr", 0),
        ("FAVÖK marjı %25 [1].", [], [(1, "c:a")], "tr", 0),
        # EN formats
        ("Net income was 1,234.5 million TRY [1].", [], [(1, "f:1")], "en", 0),
        ("Revenue was $391,035 million [1].", [], [(1, "c:b")], "en", 0),
        # derived percentage within 0.1 pp: (1234.5 - 1000) / 1000 = 23.45%
        ("Net kâr %23,5 arttı [1][2].", [], [(1, "f:1"), (2, "f:2")], "tr", 0),
        ("Net income rose 23.4% [1][2].", [], [(1, "f:1"), (2, "f:2")], "en", 0),
        ("Net kâr %25,0 arttı [1][2].", [], [(1, "f:1"), (2, "f:2")], "tr", 1),
        # dates are not figures
        ("İhale 30.10.2025 tarihinde yapıldı [1].", [], [(1, "c:a")], "tr", 0),
        ("Filed on 10/30/2025 and 2025-10-30 [1].", [], [(1, "c:b")], "en", 0),
        # number not in any cited item
        ("Net kâr 1.500 milyon TL [1].", [], [(1, "f:1")], "tr", 1),
        # number exists but in an item that was not cited
        ("Gelir 9.876,5 mn TL [1].", [], [(1, "f:1")], "tr", 1),
        # unknown cite_id
        ("Net kâr arttı [1].", [], [(1, "f:999")], "tr", 1),
        # marker without citation
        ("Net kâr arttı [2].", [], [(1, "f:1")], "tr", 1),
        # language mismatch
        ("Net kâr arttı [1].", [], [(1, "f:1")], "en", 1),
        # point checks
        ("Özet.", [("Net kâr 1.234,5 milyon TL", ["f:1"])], [], "tr", 0),
        ("Özet.", [("Net kâr 1.234,5 milyon TL", ["f:2"])], [], "tr", 1),
        ("Özet.", [("Net kâr arttı", [])], [], "tr", 1),
        ("Özet.", [("Net kâr arttı", ["c:zzz"])], [], "tr", 1),
    ],
)
def test_validate_answer_rules(
    summary: str,
    points: list[tuple[str, list[str]]],
    citations: list[tuple[int, str]],
    language: str,
    errors: int,
) -> None:
    # `language` is the detected question language; the answer is in it unless the summary
    # is the mismatch case (a Turkish answer to an English question).
    answer_language = "tr" if summary == "Net kâr arttı [1]." else language
    answer = _answer(summary, points, citations, language=answer_language)
    validation = validate_answer(answer, SEEN, language)  # type: ignore[arg-type]
    assert len(validation.errors) == errors, validation.errors


def test_partial_keeps_only_validated_points() -> None:
    answer = _answer(
        "Net kâr 9.999 milyon TL [1].",
        points=[("Net kâr 1.234,5 milyon TL", ["f:1"]), ("Gelir 5 mn TL", ["f:2"])],
        citations=[(1, "f:1")],
    )
    validation = validate_answer(answer, SEEN, "tr")
    assert not validation.ok
    partial = to_partial(answer, validation, "tr")
    assert partial.status == "partial"
    assert partial.summary == ""
    assert [p.text for p in partial.points] == ["Net kâr 1.234,5 milyon TL"]
    assert partial.disclaimer == DISCLAIMER


def test_finalize_overwrites_disclaimer() -> None:
    answer = _answer("Özet.").model_copy(update={"disclaimer": "buy now"})
    assert finalize(answer).disclaimer == DISCLAIMER


def test_schema_rejects_extra_fields_and_bad_status() -> None:
    with pytest.raises(ValidationError):
        Answer.model_validate(
            {"language": "tr", "status": "done", "summary": "x", "points": [], "citations": []}
        )
    with pytest.raises(ValidationError):
        Point.model_validate({"text": "x", "cite_ids": [], "extra": 1})
    with pytest.raises(ValidationError):
        CitationRef(marker=0, cite_id="f:1")
    assert "properties" in answer_json_schema()


# The production case (2026-10-21): a KAP table in million TL, with figures restated in TL
# and a margin computed from two table cells.
TABLE = {
    "c:t": SeenItem(
        cite_id="c:t",
        text="Kalem | Cari Dönem 01.01.2024 - 30.09.2024\n"
        "Hasılat | ifrs-full_Revenue | 19 | 551928 | 358781\n"
        "Net Dönem Karı | ifrs-full_ProfitLoss | | 88831 | 70212",
        source={},
        scale=Decimal(10) ** 6,
        unit_note="1.000.000 TL",
    )
}
# The same table when the report's presentation unit is not known.
UNSCALED = {"c:t": TABLE["c:t"].model_copy(update={"scale": None, "unit_note": None})}


def test_unit_scaled_numbers_and_chunk_derived_percent_are_supported() -> None:
    answer = _answer(
        "THYAO'nun 9 aylık net kâr marjı yaklaşık %16,09'dur [1].",
        points=[("Hasılat 551.928.000.000 TL, net kâr 88.831 milyon TL.", ["c:t"])],
        citations=[(1, "c:t")],
    )
    assert validate_answer(answer, TABLE, "tr").ok


@pytest.mark.parametrize(
    "text",
    [
        "Hasılat 551.928.000.001 TL.",  # not an exact scale of a source number
        "Hasılat 5.519.280 TL.",  # x10, not a unit scale
        "Net kâr marjı %17,5.",  # no pair of table numbers gives it
    ],
)
def test_scaling_does_not_admit_other_numbers(text: str) -> None:
    answer = _answer("Özet.", points=[(text, ["c:t"])])
    assert not validate_answer(answer, TABLE, "tr").ok


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Net kâr 88831 milyon TL.", True),
        ("Net kâr 88,8 milyar TL.", True),  # rounded to the stated precision
        ("Net kâr 88.831.000.000 TL.", True),
        ("Net kâr 88831.", True),  # the bare table number
        ("Net kâr 88831 bin TL.", False),  # production case: wrong unit
        ("Net kâr 88831 TL.", False),  # a currency without the table's scale
        ("Net kâr 88,9 milyar TL.", False),
    ],
)
def test_stated_units_must_match_the_report_unit(text: str, ok: bool) -> None:
    answer = _answer("Özet.", points=[(text, ["c:t"])])
    assert validate_answer(answer, TABLE, "tr").ok is ok


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("Net kâr 88831.", True),
        ("Net kâr 88831 bin TL.", False),
        ("Net kâr 88.831.000.000 TL.", False),  # the scale cannot be known
    ],
)
def test_unknown_report_unit_admits_only_the_bare_number(text: str, ok: bool) -> None:
    answer = _answer("Özet.", points=[(text, ["c:t"])])
    assert validate_answer(answer, UNSCALED, "tr").ok is ok


def test_table_header_declares_the_scale() -> None:
    seen = {
        "c:m": SeenItem(
            cite_id="c:m",
            text="(In millions) | Three Months Ended March 31, | 2026 | 2025\n"
            "Net income | 31,778 | 25,824",
            source={},
        )
    }
    for text, ok in [
        ("Net income was $31,778 million.", True),
        ("Net income was $31.8 billion.", True),
        ("Net income was $31,778,000,000.", True),
        ("Net income was $31,778.", False),
        ("Quarter ended March 31, 2026: 31,778.", True),  # the date's day is not a figure
    ]:
        answer = _answer("Summary.", points=[(text, ["c:m"])], language="en")
        assert validate_answer(answer, seen, "en").ok is ok, text


def test_empty_partial_gets_the_no_verified_answer_notice() -> None:
    answer = _answer("Net kâr 9.999 milyon TL [1].", citations=[(1, "f:1")])
    partial = to_partial(answer, validate_answer(answer, SEEN, "tr"), "tr")
    assert partial.summary == NO_VERIFIED_ANSWER["tr"]
    assert partial.points == []
    assert partial.citations == []


@pytest.mark.parametrize("text", ["ilk 9 aylık dönem", "6 ay", "the 9-month period", "12 months"])
def test_period_lengths_are_not_figures(text: str) -> None:
    assert figures(text, "tr") == [] and figures(text, "en") == []


def test_inline_cite_ids_become_markers() -> None:
    from agent.answer import Answer, CitationRef, normalize_markers

    answer = Answer(
        language="tr",
        status="completed",
        summary="Marj %43,5 [c:abc]. FAVÖK 23,1 milyar TL [c:abc]; gelir [f:9] [1].",
        citations=[CitationRef(marker=1, cite_id="f:7")],
    )
    fixed = normalize_markers(answer)
    assert fixed.summary == "Marj %43,5 [2]. FAVÖK 23,1 milyar TL [2]; gelir [3] [1]."
    assert [(c.marker, c.cite_id) for c in fixed.citations] == [
        (1, "f:7"),
        (2, "c:abc"),
        (3, "f:9"),
    ]
    assert normalize_markers(fixed) == fixed


def test_bare_chunk_ids_become_markers() -> None:
    from agent.answer import normalize_markers

    chunk = "9c" * 32
    answer = Answer(
        language="en", status="completed", summary=f"Sales were $416,161 million [{chunk}]."
    )
    fixed = normalize_markers(answer)
    assert fixed.summary == "Sales were $416,161 million [1]."
    assert [(c.marker, c.cite_id) for c in fixed.citations] == [(1, f"c:{chunk}")]


def test_bare_chunk_ids_get_their_prefix_everywhere() -> None:
    from agent.answer import normalize_markers

    chunk = "04" * 32
    answer = Answer.model_validate(
        {
            "language": "tr",
            "status": "completed",
            "summary": f"Payın %48,88'i satışa çıkarıldı [{chunk}].",
            "points": [{"text": "Pay %48,88.", "cite_ids": [chunk]}],
            "citations": [{"marker": 1, "cite_id": chunk}],
        }
    )
    fixed = normalize_markers(answer)
    assert fixed.summary == "Payın %48,88'i satışa çıkarıldı [1]."
    assert [(c.marker, c.cite_id) for c in fixed.citations] == [(1, f"c:{chunk}")]
    assert fixed.points[0].cite_ids == [f"c:{chunk}"]
