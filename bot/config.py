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


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    telegram_chat_id: int | None
    notion_token: str
    notion_database_id: str

    notion_title_property: str
    notion_date_property: str
    notion_status_property: str
    notion_type_property: str
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
    daily_hour: int
    daily_minute: int


def load_config() -> Config:
    chat_id_raw = os.getenv("TELEGRAM_CHAT_ID")
    chat_id = int(chat_id_raw) if chat_id_raw else None

    return Config(
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=chat_id,
        notion_token=_required("NOTION_TOKEN"),
        notion_database_id=_required("NOTION_DATABASE_ID"),

        notion_title_property=os.getenv("NOTION_TITLE_PROPERTY", "Name"),
        notion_date_property=os.getenv("NOTION_DATE_PROPERTY", "Date"),
        notion_status_property=os.getenv("NOTION_STATUS_PROPERTY", "Status"),
        notion_type_property=os.getenv("NOTION_TYPE_PROPERTY", "Type"),
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
        daily_hour=_int("DAILY_HOUR", 22),
        daily_minute=_int("DAILY_MINUTE", 0),
    )
