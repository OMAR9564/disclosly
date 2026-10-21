"""Tests for agent.advice (T015)."""

import pytest

from agent.advice import detect_language, is_advice_request

ADVICE = [
    "THYAO hissesi almalı mıyım?",
    "ASELS satmalı mıyım şimdi?",
    "BIMAS tutmalı mı?",
    "THYAO için hedef fiyat nedir?",
    "Bu hisse için fiyat hedefi var mı?",
    "Şimdi almak mantıklı mı?",
    "Portföyüme ne eklemeliyim?",
    "THYAO alınır mı?",
    "Hisse yükselir mi yarın?",
    "Yatırım tavsiyesi verir misin?",
    "KCHOL alayım mı?",
    "Satmak doğru mu?",
    "Bu hisse yıl sonunda kaç lira olur?",
    "Should I buy AAPL?",
    "should i sell my Tesla shares",
    "Is AAPL a good buy right now?",
    "What is the price target for MSFT?",
    "Give me a target price for NVDA",
    "Is Apple stock worth buying?",
    "What should I add to my portfolio?",
    "Is this a good time to buy?",
    "Will Apple stock go up next week?",
    "Buy rating or hold rating for AMZN?",
]
NOT_ADVICE = [
    "THYAO 2025 üçüncü çeyrek net kârı ne kadar?",
    "ASELS'in net borcu nasıl değişti?",
    "BIMAS 2024 yıllık raporunda risk faktörleri neler?",
    "THYAO yeni uçak siparişi verdi mi?",
    "Şirketin FAVÖK marjı kaç?",
    "KAP'ta son özel durum açıklaması nedir?",
    "Gelir yıllık bazda ne kadar arttı?",
    "Hedeflenen kapasite artışı nedir?",
    "Satış gelirleri hangi segmentten geliyor?",
    "Almanya pazarındaki büyüme nasıl?",
    "What was Apple's revenue in fiscal 2024?",
    "How did AAPL net debt change?",
    "List Microsoft's risk factors from the latest 10-K.",
    "What is the gross margin trend for NVDA?",
    "Did Tesla buy back shares in 2024?",
    "Summarize the buyback program in the 10-Q.",
    "Which segment holds the most revenue?",
    "What does the company say about portfolio companies?",
    "How many employees does Amazon report?",
    "What is the target capital structure stated in the report?",
    "Explain the sell-side commentary mentioned in the filing.",
]


@pytest.mark.parametrize("question", ADVICE)
def test_detects_advice(question: str) -> None:
    assert is_advice_request(question)


@pytest.mark.parametrize("question", NOT_ADVICE)
def test_does_not_flag_information_questions(question: str) -> None:
    assert not is_advice_request(question)


def test_case_counts() -> None:
    assert len(ADVICE) >= 20
    assert len(NOT_ADVICE) >= 20


@pytest.mark.parametrize(
    ("question", "language"),
    [
        ("THYAO 2025 net kârı ne kadar?", "tr"),
        ("ASELS net borc ne oldu", "tr"),
        ("Sirketin geliri nedir", "tr"),
        ("What was Apple's revenue?", "en"),
        ("AAPL 2024 revenue", "en"),
        ("İş Bankası", "tr"),
    ],
)
def test_detect_language(question: str, language: str) -> None:
    assert detect_language(question) == language
