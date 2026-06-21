from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram.ext import Application

from .config import Config
from .handlers import send_today_tasks
from .notion_client import NotionTasks
from .reminders import RemindersStore

log = logging.getLogger(__name__)


def setup_scheduler(app: Application, cfg: Config, notion: NotionTasks) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(cfg.timezone))

    async def send_job(heading: str) -> None:
        if cfg.telegram_chat_id is None:
            raise RuntimeError("TELEGRAM_CHAT_ID not configured")
        log.info("Running scheduled task job: %s", heading)
        try:
            count = await send_today_tasks(app, cfg.telegram_chat_id, cfg, notion, heading=heading)
            log.info("Sent %d tasks", count)
        except Exception:
            log.exception("Scheduled job failed")
            try:
                await app.bot.send_message(
                    chat_id=cfg.telegram_chat_id,
                    text="⚠️ فشل التذكير المجدول. تحقق من سجلات النظام.",
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
    async def reminder_check_job() -> None:
        store: RemindersStore | None = app.bot_data.get("reminders_store")
        if store is None or cfg.telegram_chat_id is None:
            return
        now = datetime.now(tz=ZoneInfo(cfg.timezone))
        for reminder in store.due_now(now, cfg.timezone):
            try:
                await app.bot.send_message(
                    chat_id=cfg.telegram_chat_id,
                    text=f"⏰ تذكير: {reminder.title}",
                )
                store.mark_fired(reminder.id)
            except Exception:
                log.exception("Failed to send reminder %s", reminder.id)

    reminder_check_job_obj = scheduler.add_job(
        reminder_check_job,
        trigger=CronTrigger(minute="*", timezone=ZoneInfo(cfg.timezone)),
        id="reminder_check",
        replace_existing=True,
    )
    jobs.append(reminder_check_job_obj)

    app.bot_data["scheduler_jobs"] = jobs
    return scheduler
