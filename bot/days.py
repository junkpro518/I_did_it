from __future__ import annotations

import unicodedata
from datetime import date


def _normalize(s: str) -> str:
    """Normalize Unicode and strip whitespace for consistent comparisons."""
    return unicodedata.normalize("NFKC", s.strip())


DAY_LABELS = [
    "الاثنين",
    "الثلاثاء",
    "الأربعاء",
    "الخميس",
    "الجمعة",
    "السبت",
    "الأحد",
]

DAY_ALIASES = {
    "mon": "الاثنين",
    "monday": "الاثنين",
    "الاثنين": "الاثنين",
    "الإثنين": "الاثنين",
    "اثنين": "الاثنين",
    "tue": "الثلاثاء",
    "tuesday": "الثلاثاء",
    "الثلاثاء": "الثلاثاء",
    "ثلاثاء": "الثلاثاء",
    "wed": "الأربعاء",
    "wednesday": "الأربعاء",
    "الأربعاء": "الأربعاء",
    "اربعاء": "الأربعاء",
    "أربعاء": "الأربعاء",
    "thu": "الخميس",
    "thursday": "الخميس",
    "الخميس": "الخميس",
    "خميس": "الخميس",
    "fri": "الجمعة",
    "friday": "الجمعة",
    "الجمعة": "الجمعة",
    "جمعة": "الجمعة",
    "sat": "السبت",
    "saturday": "السبت",
    "السبت": "السبت",
    "سبت": "السبت",
    "sun": "الأحد",
    "sunday": "الأحد",
    "الأحد": "الأحد",
    "الاحد": "الأحد",
    "أحد": "الأحد",
    "احد": "الأحد",
}

# Pre-normalized alias map: both keys and values are NFKC-normalized so that
# Arabic Unicode variants (alef with/without hamza, ta marbuta, etc.) match.
_DAY_ALIASES_NORMALIZED: dict[str, str] = {
    _normalize(k): _normalize(v) for k, v in DAY_ALIASES.items()
}


def day_label(value: date) -> str:
    return DAY_LABELS[value.weekday()]


def format_days(days: list[str]) -> str:
    return "كل يوم" if not days else "، ".join(days)


def parse_days(text: str) -> list[str]:
    cleaned = _normalize(text)
    # Normalize the "all days" sentinel values too
    _all_sentinels = {_normalize(s) for s in {"", "كل يوم", "كل", "يومي", "daily", "everyday", "all"}}
    if cleaned in _all_sentinels:
        return []

    parts = (
        cleaned.replace("،", ",")
        .replace("/", ",")
        .replace("|", ",")
        .replace(" و ", ",")
        .split(",")
    )
    days: list[str] = []
    for raw in parts:
        key = _normalize(raw).lower()
        if not key:
            continue
        value = _DAY_ALIASES_NORMALIZED.get(key)
        if value is None:
            raise ValueError(f"اليوم غير معروف: {raw.strip()}")
        if value not in days:
            days.append(value)
    return days

