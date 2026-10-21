"""Investment-advice detection and question language detection (TR + EN).

The detector is deliberately keyword based: an advice question gets a `refused` answer with
information only, so a false positive costs little and a false negative is caught again by the
system prompt.
"""

import re

from agent.answer import Language

_ADVICE_PATTERNS = [
    # Turkish
    r"\b(al|sat|tut)(malı|mali|meli)\s*(mı|mi|mıyım|miyim|yım|yim)?\b",
    r"\b(alayım|alayim|satayım|satayim|tutayım|tutayim)\s*(mı|mi)\b",
    r"\b(alınır|alinir|satılır|satilir)\s*(mı|mi)\b",
    r"\bhedef\s*fiyat",
    r"\bfiyat\s*hedef",
    r"\b(al|sat|tut)\s+(tavsiye|öneri|önerisi|tavsiyesi)",
    r"\byatırım\s+(tavsiye|öneri)",
    r"\bportf(ö|o)y(ü|u)m",
    r"\bportf(ö|o)y(e|üme|ume)\s+(ekle|kat)",
    r"\b(yükselir|yukselir|düşer|duser)\s*(mi|mı)\b",
    r"\bkaç\s+(lira|tl)\s+olur",
    r"\bşimdi\s+(almak|satmak)",
    r"\b(almak|satmak)\s+(mantıklı|mantikli|doğru|dogru)",
    # English
    r"\bshould\s+i\s+(buy|sell|hold|invest|short)\b",
    r"\b(is|are)\s+\S+(\s+\S+)?\s+a\s+(good\s+)?(buy|sell)\b",
    r"\bprice\s+target",
    r"\btarget\s+price",
    r"\b(buy|sell|hold)\s+(rating|recommendation|signal)",
    r"\bworth\s+(buying|investing)",
    r"\bmy\s+portfolio\b",
    r"\b(add|put)\s+\S+\s+(to|in)\s+my\s+portfolio",
    r"\bwill\s+\S+\s+(stock|shares?)\s+(go\s+up|rise|fall|drop)",
    r"\bgood\s+(investment|time\s+to\s+(buy|sell))",
    r"\bwhat\s+(should|would)\s+you\s+(buy|invest)",
]
_ADVICE = re.compile("|".join(f"(?:{p})" for p in _ADVICE_PATTERNS), re.IGNORECASE)

_TR_LETTERS = re.compile(r"[çğıöşüÇĞİÖŞÜ]")
_TR_STOPWORDS = frozenset(
    [
        "ve",
        "ile",
        "bir",
        "bu",
        "şu",
        "ne",
        "nedir",
        "neden",
        "nasıl",
        "kaç",
        "mı",
        "mi",
        "mu",
        "mü",
        "için",
        "gibi",
        "olan",
        "olarak",
        "daha",
        "çok",
        "yıl",
        "yılı",
        "yılında",
        "çeyrek",
        "çeyreğinde",
        "kâr",
        "kar",
        "borç",
        "borcu",
        "şirket",
        "şirketin",
        "hangi",
        "ise",
        "oldu",
        "olmuş",
        "arttı",
        "azaldı",
    ]
)


def is_advice_request(question: str) -> bool:
    return bool(_ADVICE.search(question))


def detect_language(question: str) -> Language:
    """`tr` if the question has Turkish-specific letters or stopwords, else `en`."""
    if _TR_LETTERS.search(question):
        return "tr"
    words = re.findall(r"[a-zçğıöşü]+", question.lower())
    return "tr" if any(w in _TR_STOPWORDS for w in words) else "en"
