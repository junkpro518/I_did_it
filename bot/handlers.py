from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .answer_types import REGISTRY, AnswerContext, AnswerType
from .config import Config
from .notion_client import NotionTasks, Task

log = logging.getLogger(__name__)


def _today(cfg: Config):
    return datetime.now(ZoneInfo(cfg.timezone)).date()


def _authorized(update: Update, cfg: Config) -> bool:
    if cfg.telegram_chat_id is None:
        return True
    chat = update.effective_chat
    return chat is not None and chat.id == cfg.telegram_chat_id


def _pending(app: Application) -> dict:
    """Pending text-reply lookup: { (chat_id, message_id) -> entry dict }."""
    return app.bot_data.setdefault("pending_text", {})


def _ctx(app: Application, chat_id: int) -> AnswerContext:
    cfg: Config = app.bot_data["cfg"]
    notion: NotionTasks = app.bot_data["notion"]
    return AnswerContext(
        notion=notion,
        cfg=cfg,
        today=_today(cfg),
        bot=app.bot,
        chat_id=chat_id,
        pending=_pending(app),
    )


async def _send_question(app: Application, chat_id: int, task: Task) -> None:
    answer_type = REGISTRY.for_task(task)
    await app.bot.send_message(
        chat_id=chat_id,
        text=answer_type.prompt(task),
        reply_markup=answer_type.keyboard(task.page_id),
    )


async def send_today_tasks(
    app: Application, chat_id: int, cfg: Config, notion: NotionTasks
) -> int:
    today = _today(cfg)
    tasks = await notion.query_today_tasks(today)
    if not tasks:
        await app.bot.send_message(chat_id=chat_id, text="✨ ما عندك مهام معلّقة اليوم. مساء الخير!")
        return 0

    await app.bot.send_message(
        chat_id=chat_id,
        text=f"\U0001f319 مساء الخير! عندك {len(tasks)} مهمة لليوم. خل نراجعها:",
    )
    for task in tasks:
        await _send_question(app, chat_id, task)
    return len(tasks)


# ──────────────────────────── commands ────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    chat = update.effective_chat
    if chat is None or update.message is None:
        return
    if cfg.telegram_chat_id is None:
        await update.message.reply_text(
            f"\U0001f44b أهلاً!\n\nChat ID: `{chat.id}`\n\n"
            f"حطّه في `.env` كـ `TELEGRAM_CHAT_ID={chat.id}` ثم أعد تشغيل البوت.",
            parse_mode="Markdown",
        )
        return
    if not _authorized(update, cfg):
        await update.message.reply_text("هذا البوت خاص.")
        return
    await update.message.reply_text(
        "✅ البوت شغّال. استخدم /tasks لمراجعة مهام اليوم الآن، أو انتظر التذكير اليومي."
    )


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    notion: NotionTasks = context.application.bot_data["notion"]
    if not _authorized(update, cfg):
        return
    chat = update.effective_chat
    assert chat is not None
    await send_today_tasks(context.application, chat.id, cfg, notion)


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    notion: NotionTasks = context.application.bot_data["notion"]
    if not _authorized(update, cfg) or update.message is None:
        return

    try:
        notion_status = await notion.health_check()
    except Exception as exc:
        notion_status = f"Notion FAIL: {exc}"

    next_run = "—"
    jobs = context.application.bot_data.get("scheduler_jobs") or []
    if jobs and jobs[0].next_run_time:
        next_run = jobs[0].next_run_time.astimezone(ZoneInfo(cfg.timezone)).strftime(
            "%Y-%m-%d %H:%M %Z"
        )

    await update.message.reply_text(
        f"\U0001f7e2 Bot: OK\n{notion_status}\nNext run: {next_run}"
    )


# ──────────────────────────── callbacks ────────────────────────────


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    query = update.callback_query
    if query is None or query.data is None:
        return

    chat = update.effective_chat
    if cfg.telegram_chat_id is not None:
        if chat is None or chat.id != cfg.telegram_chat_id:
            await query.answer()
            return
    if chat is None:
        return

    parts = query.data.split(":", 2)
    if len(parts) != 3:
        await query.answer("صيغة غير معروفة")
        return
    type_code, action, page_id = parts

    answer_type: AnswerType | None = REGISTRY.by_code(type_code)
    if answer_type is None:
        await query.answer("نوع غير معروف")
        return

    original = query.message.text if query.message else ""

    try:
        new_text = await answer_type.on_button(
            action, page_id, original, _ctx(context.application, chat.id)
        )
    except Exception as exc:
        log.exception("Callback failed")
        await query.answer(f"فشل: {exc}", show_alert=True)
        return

    await query.answer("تم ✅" if new_text else "")
    if new_text is not None:
        try:
            await query.edit_message_text(text=new_text, reply_markup=None)
        except Exception:
            log.warning("Could not edit message", exc_info=True)


# ──────────────────────────── text replies ────────────────────────────


async def on_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    if not _authorized(update, cfg):
        return
    msg = update.message
    if msg is None or msg.text is None or msg.reply_to_message is None:
        return
    chat = update.effective_chat
    if chat is None:
        return

    pending = _pending(context.application)
    key = (chat.id, msg.reply_to_message.message_id)
    entry = pending.get(key)
    if entry is None:
        return  # not a reply to one of our questions; ignore

    answer_type = REGISTRY.by_code(entry["code"])
    if answer_type is None:
        return

    try:
        new_text = await answer_type.on_text(
            msg.text, entry["page_id"], entry["original"], _ctx(context.application, chat.id)
        )
    except ValueError as exc:
        await msg.reply_text(str(exc))
        return
    except Exception as exc:
        log.exception("Text reply failed")
        await msg.reply_text(f"فشل التحديث: {exc}")
        return

    pending.pop(key, None)
    await msg.reply_text(new_text)


# ──────────────────────────── registration ────────────────────────────


def register(app: Application) -> None:
    cfg: Config = app.bot_data["cfg"]
    REGISTRY.configure_names(cfg)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(
        MessageHandler(filters.TEXT & filters.REPLY & ~filters.COMMAND, on_text_reply)
    )
