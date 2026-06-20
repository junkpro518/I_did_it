from __future__ import annotations

from datetime import date

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


def day_label(value: date) -> str:
    return DAY_LABELS[value.weekday()]


def format_days(days: list[str]) -> str:
    return "كل يوم" if not days else "، ".join(days)


def parse_days(text: str) -> list[str]:
    cleaned = text.strip()
    if cleaned in {"", "كل يوم", "كل", "يومي", "daily", "everyday", "all"}:
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
        key = raw.strip().lower()
        if not key:
            continue
        value = DAY_ALIASES.get(key)
        if value is None:
            raise ValueError(f"اليوم غير معروف: {raw.strip()}")
        if value not in days:
            days.append(value)
    return days

