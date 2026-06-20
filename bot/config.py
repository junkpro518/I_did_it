import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _int_list(name: str, default: list[int]) -> list[int]:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    values: list[int] = []
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        values.append(int(value))
    return values or default


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: int | None
    notion_token: str
    notion_database_id: str
    notion_log_database_id: str

    notion_title_property: str
    notion_date_property: str
    notion_status_property: str
    notion_type_property: str
    notion_days_property: str
    notion_value_property: str
    notion_note_property: str

    status_done: str
    status_missed: str
    status_postponed: str

    type_boolean: str
    type_number: str
    type_rating: str
    type_text: str

    timezone: str
    morning_hour: int
    morning_minute: int
    reminder_hours: list[int]
    reminder_minute: int


def load_config() -> Config:
    chat_id_raw = os.getenv("TELEGRAM_CHAT_ID")
    chat_id = int(chat_id_raw) if chat_id_raw else None

    return Config(
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=chat_id,
        notion_token=_required("NOTION_TOKEN"),
        notion_database_id=_required("NOTION_DATABASE_ID"),
        notion_log_database_id=_required("NOTION_LOG_DATABASE_ID"),

        notion_title_property=os.getenv("NOTION_TITLE_PROPERTY", "Name"),
        notion_date_property=os.getenv("NOTION_DATE_PROPERTY", "Date"),
        notion_status_property=os.getenv("NOTION_STATUS_PROPERTY", "Status"),
        notion_type_property=os.getenv("NOTION_TYPE_PROPERTY", "Type"),
        notion_days_property=os.getenv("NOTION_DAYS_PROPERTY", "Days"),
        notion_value_property=os.getenv("NOTION_VALUE_PROPERTY", "Value"),
        notion_note_property=os.getenv("NOTION_NOTE_PROPERTY", "Note"),

        status_done=os.getenv("NOTION_STATUS_DONE", "Done"),
        status_missed=os.getenv("NOTION_STATUS_MISSED", "Missed"),
        status_postponed=os.getenv("NOTION_STATUS_POSTPONED", "Postponed"),

        type_boolean=os.getenv("TYPE_BOOLEAN", "Boolean"),
        type_number=os.getenv("TYPE_NUMBER", "Number"),
        type_rating=os.getenv("TYPE_RATING", "Rating"),
        type_text=os.getenv("TYPE_TEXT", "Text"),

        timezone=os.getenv("TIMEZONE", "Asia/Riyadh"),
        morning_hour=_int("MORNING_HOUR", _int("DAILY_HOUR", 4)),
        morning_minute=_int("MORNING_MINUTE", _int("DAILY_MINUTE", 0)),
        reminder_hours=_int_list("REMINDER_HOURS", [16, 23]),
        reminder_minute=_int("REMINDER_MINUTE", 0),
    )
