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

    async def send_job(heading: str) -> None:
        if cfg.telegram_chat_id is None:
            log.warning("Skipping scheduled job: TELEGRAM_CHAT_ID is not set")
            return
        log.info("Running scheduled task job: %s", heading)
        try:
            count = await send_today_tasks(app, cfg.telegram_chat_id, cfg, notion, heading=heading)
            log.info("Sent %d tasks", count)
        except Exception:
            log.exception("Scheduled job failed")
            try:
                await app.bot.send_message(
                    chat_id=cfg.telegram_chat_id,
                    text="⚠️ فشل التذكير المجدول. تحقق من logs.",
                )
            except Exception:
                log.exception("Could not even send error notification")

    morning_job = scheduler.add_job(
        send_job,
        trigger=CronTrigger(
            hour=cfg.morning_hour,
            minute=cfg.morning_minute,
            timezone=ZoneInfo(cfg.timezone),
        ),
        args=["🌅 صباح الخير. هذه قائمة مهامك لهذا اليوم:"],
        id="morning_task_list",
        replace_existing=True,
    )
    jobs = [morning_job]
    for hour in cfg.reminder_hours:
        jobs.append(
            scheduler.add_job(
                send_job,
                trigger=CronTrigger(
                    hour=hour,
                    minute=cfg.reminder_minute,
                    timezone=ZoneInfo(cfg.timezone),
                ),
                args=["⏰ تذكير: هذه المهام المفتوحة لليوم:"],
                id=f"task_reminder_{hour:02d}_{cfg.reminder_minute:02d}",
                replace_existing=True,
            )
        )
    app.bot_data["scheduler_jobs"] = jobs
    return scheduler
