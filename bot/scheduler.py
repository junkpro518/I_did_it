from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from .config import Config
from .handlers import send_today_tasks
from .notion_client import NotionTasks

log = logging.getLogger(__name__)


def setup_scheduler(app: Application, cfg: Config, notion: NotionTasks) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(cfg.timezone))

    async def daily_job() -> None:
        if cfg.telegram_chat_id is None:
            log.warning("Skipping daily job: TELEGRAM_CHAT_ID is not set")
            return
        log.info("Running daily task review job")
        try:
            count = await send_today_tasks(app, cfg.telegram_chat_id, cfg, notion)
            log.info("Sent %d tasks", count)
        except Exception:
            log.exception("Daily job failed")
            try:
                await app.bot.send_message(
                    chat_id=cfg.telegram_chat_id,
                    text="⚠️ فشل التذكير اليومي. تحقق من logs.",
                )
            except Exception:
                log.exception("Could not even send error notification")

    job = scheduler.add_job(
        daily_job,
        trigger=CronTrigger(
            hour=cfg.daily_hour,
            minute=cfg.daily_minute,
            timezone=ZoneInfo(cfg.timezone),
        ),
        id="daily_task_review",
        replace_existing=True,
    )
    app.bot_data["scheduler_jobs"] = [job]
    return scheduler
