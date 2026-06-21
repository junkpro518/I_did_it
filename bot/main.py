from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import Application

from .config import load_config
from .handlers import register
from .notion_client import NotionTasks
from .reminders import RemindersStore
from .scheduler import setup_scheduler

BOT_COMMANDS = [
    BotCommand("tasks", "مراجعة مهام اليوم الآن"),
    BotCommand("progress", "تقدم مهام اليوم"),
    BotCommand("report", "تقرير التقدم والإحصائيات"),
    BotCommand("add", "إضافة مهمة جديدة"),
    BotCommand("list", "عرض كل المهام الدائمة"),
    BotCommand("edit", "تعديل مهمة"),
    BotCommand("delete", "حذف مهمة"),
    BotCommand("reminder", "إضافة تذكير"),
    BotCommand("reminders", "قائمة التذكيرات"),
    BotCommand("delreminder", "حذف تذكير"),
    BotCommand("health", "حالة البوت و Notion"),
    BotCommand("help", "عرض قائمة الأوامر"),
    BotCommand("start", "ترحيب وعرض Chat ID"),
]

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("bot")


async def _post_init(app: Application) -> None:
    cfg = app.bot_data["cfg"]
    notion: NotionTasks = app.bot_data["notion"]
    scheduler = setup_scheduler(app, cfg, notion)
    scheduler.start()
    app.bot_data["scheduler"] = scheduler

    await app.bot.set_my_commands(BOT_COMMANDS)
    log.info("Registered %d commands in Telegram menu", len(BOT_COMMANDS))

    log.info(
        "Scheduler started (morning at %02d:%02d, reminders=%s:%02d %s, chat_id=%s)",
        cfg.morning_hour,
        cfg.morning_minute,
        ",".join(f"{hour:02d}" for hour in cfg.reminder_hours),
        cfg.reminder_minute,
        cfg.timezone,
        cfg.telegram_chat_id,
    )


async def _post_shutdown(app: Application) -> None:
    scheduler = app.bot_data.get("scheduler")
    if scheduler is not None:
        scheduler.shutdown(wait=False)
    notion: NotionTasks | None = app.bot_data.get("notion")
    if notion is not None:
        await notion.aclose()


def main() -> None:
    cfg = load_config()
    notion = NotionTasks(cfg)

    app = (
        Application.builder()
        .token(cfg.telegram_bot_token)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["cfg"] = cfg
    app.bot_data["notion"] = notion
    app.bot_data["reminders_store"] = RemindersStore(cfg.reminders_file)
    register(app)

    log.info("Starting Telegram bot (long polling)…")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
