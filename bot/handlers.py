from __future__ import annotations

import logging
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from .answer_types import REGISTRY, AnswerContext, AnswerType
from .config import Config
from .days import DAY_LABELS, day_label, format_days, parse_days
from .notion_client import LogEntry, NotionTasks
from .reminders import Reminder, RemindersStore

log = logging.getLogger(__name__)


# ──────────────────────────── helpers ────────────────────────────


def _today(cfg: Config):
    return datetime.now(ZoneInfo(cfg.timezone)).date()


def _authorized(update: Update, cfg: Config) -> bool:
    if cfg.telegram_chat_id is None:
        return True
    chat = update.effective_chat
    return chat is not None and chat.id == cfg.telegram_chat_id


class _PendingDict(dict):
    """dict subclass that auto-stamps entries with a creation timestamp."""

    def __setitem__(self, key, value):
        if isinstance(value, dict) and "ts" not in value:
            value = dict(value, ts=time.time())
        super().__setitem__(key, value)


def _pending(app: Application) -> _PendingDict:
    p = app.bot_data.get("pending_text")
    if not isinstance(p, _PendingDict):
        p = _PendingDict(p or {})
        app.bot_data["pending_text"] = p
    return p


def _clean_pending(pending: _PendingDict) -> None:
    """Remove entries older than 24 hours to prevent memory leaks."""
    cutoff = time.time() - 86400
    stale = [k for k, v in pending.items() if isinstance(v, dict) and v.get("ts", 0) < cutoff]
    for k in stale:
        del pending[k]


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


# ──────────────────────────── daily review ────────────────────────────


async def _send_question(app: Application, chat_id: int, entry: LogEntry) -> None:
    answer_type = REGISTRY.for_task(entry)
    await app.bot.send_message(
        chat_id=chat_id,
        text=answer_type.prompt(entry),
        reply_markup=answer_type.keyboard(entry.page_id),
    )


def _is_final_status(status: str, cfg: Config) -> bool:
    return status in {cfg.status_done, cfg.status_missed, cfg.status_postponed}


async def send_today_tasks(
    app: Application,
    chat_id: int,
    cfg: Config,
    notion: NotionTasks,
    heading: str = "هذه قائمة مهامك لهذا اليوم:",
) -> int:
    today = _today(cfg)
    entries = await notion.ensure_log_entries_for_date(today)
    open_entries = [entry for entry in entries if not _is_final_status(entry.status, cfg)]
    if not entries:
        await app.bot.send_message(
            chat_id=chat_id,
            text="ما عندك مهام مجدولة لهذا اليوم. استخدم /add لإضافة مهمة.",
        )
        return 0

    if not open_entries:
        await app.bot.send_message(chat_id=chat_id, text="✅ خلصت كل مهام اليوم.")
        return 0

    await app.bot.send_message(
        chat_id=chat_id,
        text=f"{heading}\nعندك {len(open_entries)} مهمة مفتوحة من أصل {len(entries)}.",
    )
    for entry in open_entries:
        await _send_question(app, chat_id, entry)
    return len(open_entries)


# ──────────────────────────── basic commands ────────────────────────────


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
        "✅ البوت شغّال.\n\n"
        "/tasks — مراجعة مهام اليوم\n"
        "/progress — تقدم مهام اليوم\n"
        "/report — تقرير إحصائي\n"
        "/reminder — إضافة تذكير\n"
        "/reminders — قائمة التذكيرات\n"
        "/delreminder — حذف تذكير\n"
        "/add — إضافة مهمة جديدة\n"
        "/list — عرض كل المهام الدائمة\n"
        "/edit — تعديل مهمة\n"
        "/delete — حذف مهمة\n"
        "/health — فحص الاتصال"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    if not _authorized(update, cfg) or update.message is None:
        return
    await update.message.reply_text(
        "✅ البوت شغّال.\n\n"
        "/tasks — مراجعة مهام اليوم\n"
        "/progress — تقدم مهام اليوم\n"
        "/report — تقرير إحصائي\n"
        "/reminder — إضافة تذكير\n"
        "/reminders — قائمة التذكيرات\n"
        "/delreminder — حذف تذكير\n"
        "/add — إضافة مهمة جديدة\n"
        "/list — عرض كل المهام الدائمة\n"
        "/edit — تعديل مهمة\n"
        "/delete — حذف مهمة\n"
        "/health — فحص الاتصال"
    )


async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    notion: NotionTasks = context.application.bot_data["notion"]
    if not _authorized(update, cfg):
        return
    chat = update.effective_chat
    if chat is None:
        return
    await send_today_tasks(context.application, chat.id, cfg, notion)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    notion: NotionTasks = context.application.bot_data["notion"]
    if not _authorized(update, cfg) or update.message is None:
        return

    days = 30
    clamped_note = ""
    if context.args:
        try:
            raw_days = int(context.args[0])
            days = max(1, min(365, raw_days))
            if days != raw_days:
                clamped_note = f"\n(تم تقليص العدد إلى {days} يوماً)"
        except ValueError:
            await update.message.reply_text("استخدم رقم أيام صحيح، مثال: /report 7")
            return

    stats = await notion.report_stats(days=days, today=_today(cfg))
    answered = stats.done + stats.missed + stats.postponed
    completion = round((stats.done / answered) * 100) if answered else 0
    total_completion = round((stats.done / stats.total) * 100) if stats.total else 0

    await update.message.reply_text(
        "📊 تقرير التقدم\n\n"
        f"الفترة: {stats.start.isoformat()} → {stats.end.isoformat()}\n"
        f"إجمالي السجلات: {stats.total}\n"
        f"✅ تم: {stats.done}\n"
        f"❌ لم يتم: {stats.missed}\n"
        f"⏭️ مؤجل: {stats.postponed}\n"
        f"⏳ بدون إجابة: {stats.pending}\n\n"
        f"نسبة الإنجاز من المهام المُجاب عليها: {completion}%\n"
        f"نسبة الإنجاز من كل السجلات: {total_completion}%"
        f"{clamped_note}"
    )


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
    if jobs:
        next_runs = []
        for job in jobs:
            if job.next_run_time:
                when = job.next_run_time.astimezone(ZoneInfo(cfg.timezone)).strftime(
                    "%Y-%m-%d %H:%M %Z"
                )
                next_runs.append(f"- {job.id}: {when}")
        next_run = "\n".join(next_runs) if next_runs else "—"

    await update.message.reply_text(
        f"\U0001f7e2 Bot: OK\n{notion_status}\nNext runs:\n{next_run}"
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    notion: NotionTasks = context.application.bot_data["notion"]
    if not _authorized(update, cfg) or update.message is None:
        return

    tasks = await notion.list_tasks()
    if not tasks:
        await update.message.reply_text("ما عندك مهام مسجّلة. استخدم /add لإضافة واحدة.")
        return

    lines = [f"\U0001f4cb عندك {len(tasks)} مهمة دائمة:\n"]
    for i, t in enumerate(tasks, 1):
        type_label = t.type_value or "Boolean"
        lines.append(f"{i}. {t.title}  —  <i>{type_label}</i>  —  {format_days(t.days)}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    notion: NotionTasks = context.application.bot_data["notion"]
    if not _authorized(update, cfg) or update.message is None:
        return

    today = _today(cfg)
    entries = await notion.list_log_entries_for_date(today)

    if not entries:
        await update.message.reply_text("لا توجد مهام لليوم.")
        return

    done_entries = [e for e in entries if e.status == cfg.status_done]
    missed_entries = [e for e in entries if e.status == cfg.status_missed]
    pending_entries = [e for e in entries if e.status not in {cfg.status_done, cfg.status_missed}]

    total = len(entries)
    done_count = len(done_entries)
    percent = (done_count / total * 100) if total else 0.0

    day_name = day_label(today)
    date_str = today.isoformat()

    lines: list[str] = [f"📊 تقدم اليوم — {day_name} {date_str}\n"]

    if done_entries:
        lines.append(f"✅ تم ({len(done_entries)}):")
        for e in done_entries:
            lines.append(f"  • {e.title}")
        lines.append("")

    if missed_entries:
        lines.append(f"❌ لم يتم ({len(missed_entries)}):")
        for e in missed_entries:
            lines.append(f"  • {e.title}")
        lines.append("")

    if pending_entries:
        lines.append(f"⏳ لم يُجب عليه ({len(pending_entries)}):")
        for e in pending_entries:
            lines.append(f"  • {e.title}")
        lines.append("")

    lines.append(f"📈 الإنجاز: {done_count}/{total} ({percent:.0f}%)")

    await update.message.reply_text("\n".join(lines))


# ──────────────────────────── /add ────────────────────────────

ADD_NAME, ADD_TYPE, ADD_DAYS = range(3)
TYPE_OPTIONS = ["Boolean", "Number", "Rating", "Text"]

# ──────────────────────────── /reminder conversation states ────────────────────────────

REM_TITLE = 30
REM_TYPE = 31
REM_DAYS = 32
REM_DATE = 33
REM_TIME = 34
REM_REPEAT = 35


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg: Config = context.application.bot_data["cfg"]
    if not _authorized(update, cfg) or update.message is None:
        return ConversationHandler.END
    await update.message.reply_text(
        "➕ إضافة مهمة جديدة.\n\nاكتب اسم المهمة:\n(اكتب /cancel للإلغاء)",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ADD_NAME


async def add_receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return ADD_NAME
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("الاسم فارغ. اكتب اسم المهمة:")
        return ADD_NAME
    context.user_data["new_task_name"] = name
    keyboard = ReplyKeyboardMarkup(
        [TYPE_OPTIONS[:2], TYPE_OPTIONS[2:]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        f"اختر نوع السؤال لـ «{name}»:",
        reply_markup=keyboard,
    )
    return ADD_TYPE


async def add_receive_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return ADD_TYPE
    chosen = update.message.text.strip()
    if chosen not in TYPE_OPTIONS:
        await update.message.reply_text(
            f"اختر من القائمة فقط: {', '.join(TYPE_OPTIONS)}"
        )
        return ADD_TYPE

    context.user_data["new_task_type"] = chosen
    await update.message.reply_text(
        "حدد أيام المهمة.\n\n"
        "اكتب: كل يوم\n"
        "أو اكتب أيام مفصولة بفواصل، مثال:\n"
        "السبت، الأحد، الاثنين",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ADD_DAYS


async def add_receive_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return ADD_DAYS
    try:
        days = parse_days(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(f"{exc}\nجرّب: كل يوم أو السبت، الأحد")
        return ADD_DAYS

    notion: NotionTasks = context.application.bot_data["notion"]
    name = context.user_data.get("new_task_name", "")
    chosen = context.user_data.get("new_task_type", "Boolean")

    try:
        await notion.create_task(name=name, type_value=chosen, days=days)
    except Exception as exc:
        log.exception("create_task failed")
        await update.message.reply_text(
            f"⚠️ فشل: {exc}", reply_markup=ReplyKeyboardRemove()
        )
        return ADD_DAYS

    context.user_data.pop("new_task_name", None)
    context.user_data.pop("new_task_type", None)
    await update.message.reply_text(
        f"✅ أُضيفت: «{name}» ({chosen})\nالأيام: {format_days(days)}",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_task_name", None)
    context.user_data.pop("new_task_type", None)
    if update.message:
        await update.message.reply_text("أُلغيت إضافة المهمة.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ──────────────────────────── /delete ────────────────────────────

CB_DELETE = "del"


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    notion: NotionTasks = context.application.bot_data["notion"]
    if not _authorized(update, cfg) or update.message is None:
        return
    tasks = await notion.list_tasks()
    if not tasks:
        await update.message.reply_text("ما عندك مهام للحذف.")
        return
    rows = [
        [InlineKeyboardButton(f"🗑 {t.title}", callback_data=f"{CB_DELETE}:{t.page_id}")]
        for t in tasks
    ]
    await update.message.reply_text(
        "اختر المهمة للحذف:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def on_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    notion: NotionTasks = context.application.bot_data["notion"]
    query = update.callback_query
    if query is None or query.data is None or not query.data.startswith(f"{CB_DELETE}:"):
        return
    if not _authorized(update, cfg):
        await query.answer()
        return
    page_id = query.data.split(":", 1)[1]
    try:
        await notion.delete_task(page_id)
    except Exception as exc:
        log.exception("delete_task failed")
        await query.answer(f"فشل: {exc}", show_alert=True)
        return
    await query.answer("حُذفت ✅")
    if query.message:
        try:
            await query.edit_message_text("🗑 تم الحذف.")
        except Exception:
            pass


# ──────────────────────────── /edit ────────────────────────────

EDIT_PICK_FIELD, EDIT_NEW_NAME, EDIT_NEW_TYPE, EDIT_NEW_DAYS = range(3, 7)
CB_EDIT = "edit"


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    notion: NotionTasks = context.application.bot_data["notion"]
    if not _authorized(update, cfg) or update.message is None:
        return
    tasks = await notion.list_tasks()
    if not tasks:
        await update.message.reply_text("ما عندك مهام للتعديل.")
        return
    rows = [
        [InlineKeyboardButton(f"✏️ {t.title}", callback_data=f"{CB_EDIT}:{t.page_id}")]
        for t in tasks
    ]
    await update.message.reply_text(
        "اختر المهمة للتعديل:",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def on_edit_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg: Config = context.application.bot_data["cfg"]
    query = update.callback_query
    if query is None or query.data is None or not query.data.startswith(f"{CB_EDIT}:"):
        return ConversationHandler.END
    if not _authorized(update, cfg):
        await query.answer()
        return ConversationHandler.END
    page_id = query.data.split(":", 1)[1]
    context.user_data["edit_page_id"] = page_id
    await query.answer()
    keyboard = ReplyKeyboardMarkup(
        [["الاسم", "النوع"], ["الأيام", "إلغاء"]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    if query.message:
        await query.message.reply_text(
            "ويش تبغى تعدّل؟",
            reply_markup=keyboard,
        )
    return EDIT_PICK_FIELD


async def edit_field_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return EDIT_PICK_FIELD
    choice = update.message.text.strip()
    if choice == "إلغاء":
        return await edit_cancel(update, context)
    if choice == "الاسم":
        await update.message.reply_text(
            "اكتب الاسم الجديد:", reply_markup=ReplyKeyboardRemove()
        )
        return EDIT_NEW_NAME
    if choice == "النوع":
        keyboard = ReplyKeyboardMarkup(
            [TYPE_OPTIONS[:2], TYPE_OPTIONS[2:]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await update.message.reply_text("اختر النوع الجديد:", reply_markup=keyboard)
        return EDIT_NEW_TYPE
    if choice == "الأيام":
        await update.message.reply_text(
            "اكتب الأيام الجديدة:\n"
            "كل يوم\n"
            "أو مثال: السبت، الأحد، الاثنين",
            reply_markup=ReplyKeyboardRemove(),
        )
        return EDIT_NEW_DAYS
    await update.message.reply_text("اختر من القائمة: الاسم / النوع / الأيام / إلغاء")
    return EDIT_PICK_FIELD


async def edit_apply_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return EDIT_NEW_NAME
    new_name = update.message.text.strip()
    if not new_name:
        await update.message.reply_text("الاسم فارغ.")
        return EDIT_NEW_NAME
    notion: NotionTasks = context.application.bot_data["notion"]
    page_id = context.user_data.get("edit_page_id")
    if not page_id:
        await update.message.reply_text("⚠️ خطأ داخلي. ابدأ من جديد بـ /edit.")
        return ConversationHandler.END
    try:
        await notion.update_task(page_id, name=new_name)
    except Exception as exc:
        log.exception("update_task name failed")
        await update.message.reply_text(f"⚠️ فشل: {exc}")
        return EDIT_NEW_NAME
    context.user_data.pop("edit_page_id", None)
    await update.message.reply_text(f"✅ الاسم محدّث: «{new_name}»")
    return ConversationHandler.END


async def edit_apply_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return EDIT_NEW_TYPE
    new_type = update.message.text.strip()
    if new_type not in TYPE_OPTIONS:
        await update.message.reply_text(
            f"اختر من: {', '.join(TYPE_OPTIONS)}"
        )
        return EDIT_NEW_TYPE
    notion: NotionTasks = context.application.bot_data["notion"]
    page_id = context.user_data.get("edit_page_id")
    if not page_id:
        await update.message.reply_text("⚠️ خطأ داخلي. ابدأ من جديد بـ /edit.")
        return ConversationHandler.END
    try:
        await notion.update_task(page_id, type_value=new_type)
    except Exception as exc:
        log.exception("update_task type failed")
        await update.message.reply_text(f"⚠️ فشل: {exc}")
        return EDIT_NEW_TYPE
    context.user_data.pop("edit_page_id", None)
    await update.message.reply_text(
        f"✅ النوع محدّث: {new_type}", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


async def edit_apply_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return EDIT_NEW_DAYS
    try:
        days = parse_days(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(f"{exc}\nجرّب: كل يوم أو السبت، الأحد")
        return EDIT_NEW_DAYS

    notion: NotionTasks = context.application.bot_data["notion"]
    page_id = context.user_data.get("edit_page_id")
    if not page_id:
        await update.message.reply_text("⚠️ خطأ داخلي. ابدأ من جديد بـ /edit.")
        return ConversationHandler.END
    try:
        await notion.update_task(page_id, days=days)
    except Exception as exc:
        log.exception("update_task days failed")
        await update.message.reply_text(f"⚠️ فشل: {exc}")
        return EDIT_NEW_DAYS
    context.user_data.pop("edit_page_id", None)
    await update.message.reply_text(
        f"✅ الأيام محدّثة: {format_days(days)}",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("edit_page_id", None)
    if update.message:
        await update.message.reply_text("أُلغي تعديل المهمة.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ──────────────────────────── /reminder ConversationHandler ────────────────────────────


async def cmd_reminder_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cfg: Config = context.application.bot_data["cfg"]
    if not _authorized(update, cfg) or update.message is None:
        return ConversationHandler.END
    await update.message.reply_text(
        "📌 أدخل نص التذكير:\n(اكتب /cancel للإلغاء)",
        reply_markup=ReplyKeyboardRemove(),
    )
    return REM_TITLE


async def rem_receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return REM_TITLE
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text("النص فارغ. أدخل نص التذكير:")
        return REM_TITLE
    context.user_data["rem_title"] = title
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("كل يوم", callback_data="rem_type:daily"),
            InlineKeyboardButton("أيام محددة", callback_data="rem_type:weekly"),
            InlineKeyboardButton("تاريخ محدد", callback_data="rem_type:once"),
        ]
    ])
    await update.message.reply_text("🗓️ متى تريد التذكير؟", reply_markup=keyboard)
    return REM_TYPE


async def rem_receive_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query is None or query.data is None:
        return REM_TYPE
    await query.answer()
    choice = query.data.split(":", 1)[1]
    if choice == "daily":
        context.user_data["rem_days"] = []
        context.user_data["rem_dates"] = []
        if query.message:
            await query.message.reply_text("🕐 أدخل وقت التذكير (مثال: 09:00):")
        return REM_TIME
    elif choice == "weekly":
        if query.message:
            await query.message.reply_text(
                "أدخل الأيام (مثال: الاثنين، الأربعاء، الجمعة):"
            )
        return REM_DAYS
    else:  # once
        if query.message:
            await query.message.reply_text("أدخل التاريخ (مثال: 2026-07-15):")
        return REM_DATE


async def rem_receive_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return REM_DAYS
    try:
        day_names = parse_days(update.message.text)
    except ValueError as exc:
        await update.message.reply_text(f"{exc}\nجرّب: الاثنين، الأربعاء، الجمعة")
        return REM_DAYS
    # Convert Arabic day names to weekday numbers using DAY_LABELS index
    day_nums = [DAY_LABELS.index(name) for name in day_names if name in DAY_LABELS]
    context.user_data["rem_days"] = day_nums
    context.user_data["rem_dates"] = []
    await update.message.reply_text("🕐 أدخل وقت التذكير (مثال: 09:00):")
    return REM_TIME


async def rem_receive_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return REM_DATE
    text = update.message.text.strip()
    try:
        date.fromisoformat(text)
    except ValueError:
        await update.message.reply_text("تنسيق التاريخ غير صحيح. مثال: 2026-07-15")
        return REM_DATE
    context.user_data["rem_dates"] = [text]
    context.user_data["rem_days"] = []
    await update.message.reply_text("🕐 أدخل وقت التذكير (مثال: 09:00):")
    return REM_TIME


async def rem_receive_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return REM_TIME
    text = update.message.text.strip()
    try:
        hour_str, minute_str = text.split(":")
        hour = int(hour_str)
        minute = int(minute_str)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except ValueError:
        await update.message.reply_text("تنسيق الوقت غير صحيح. مثال: 09:00")
        return REM_TIME
    context.user_data["rem_hour"] = hour
    context.user_data["rem_minute"] = minute
    await update.message.reply_text("🔁 كم مرة تكرار؟ (أدخل رقم، أو 0 لغير محدد):")
    return REM_REPEAT


async def rem_receive_repeat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return REM_REPEAT
    text = update.message.text.strip()
    try:
        repeat_count = int(text)
        if repeat_count < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("أدخل رقماً صحيحاً (0 = غير محدد):")
        return REM_REPEAT

    store: RemindersStore = context.application.bot_data["reminders_store"]
    r = Reminder(
        title=context.user_data.get("rem_title", ""),
        days_of_week=context.user_data.get("rem_days", []),
        specific_dates=context.user_data.get("rem_dates", []),
        notify_hour=context.user_data.get("rem_hour", 9),
        notify_minute=context.user_data.get("rem_minute", 0),
        repeat_count=repeat_count,
    )
    store.add(r)

    # Build summary
    hour = r.notify_hour
    minute = r.notify_minute
    if r.specific_dates:
        schedule = f"في {', '.join(r.specific_dates)}"
    elif r.days_of_week:
        day_names = "، ".join(DAY_LABELS[d] for d in r.days_of_week)
        schedule = day_names
    else:
        schedule = "كل يوم"
    repeat_label = "غير محدد" if repeat_count == 0 else f"{repeat_count} مرة"

    await update.message.reply_text(
        f"✅ تم إضافة التذكير:\n\n"
        f"📌 {r.title}\n"
        f"🕐 {hour:02d}:{minute:02d} | {schedule}\n"
        f"🔁 {repeat_label}"
    )

    for key in ("rem_title", "rem_days", "rem_dates", "rem_hour", "rem_minute"):
        context.user_data.pop(key, None)

    return ConversationHandler.END


async def rem_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    for key in ("rem_title", "rem_days", "rem_dates", "rem_hour", "rem_minute"):
        context.user_data.pop(key, None)
    if update.message:
        await update.message.reply_text("أُلغيت إضافة التذكير.")
    return ConversationHandler.END


# ──────────────────────────── /reminders & /delreminder ────────────────────────────


def _format_reminder_list(reminders: list[Reminder]) -> str:
    if not reminders:
        return "لا توجد تذكيرات."
    lines = ["📋 تذكيراتك:"]
    for i, r in enumerate(reminders, 1):
        if r.specific_dates:
            schedule = "، ".join(r.specific_dates)
        elif r.days_of_week:
            schedule = "، ".join(DAY_LABELS[d] for d in r.days_of_week)
        else:
            schedule = "كل يوم"
        repeat_label = "غير محدد" if r.repeat_count == 0 else f"{r.repeat_count} مرة"
        lines.append(
            f"{i}. {r.title}\n"
            f"   🕐 {r.notify_hour:02d}:{r.notify_minute:02d} | {schedule}\n"
            f"   🔁 {repeat_label} | تم الإرسال {r.fired_count} مرة"
        )
    return "\n".join(lines)


async def cmd_list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    if not _authorized(update, cfg) or update.message is None:
        return
    store: RemindersStore = context.application.bot_data["reminders_store"]
    await update.message.reply_text(_format_reminder_list(store.all()))


async def cmd_del_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    if not _authorized(update, cfg) or update.message is None:
        return
    store: RemindersStore = context.application.bot_data["reminders_store"]

    if not context.args:
        text = _format_reminder_list(store.all())
        if store.all():
            text += "\n\nاستخدم /delreminder <رقم>"
        await update.message.reply_text(text)
        return

    try:
        index = int(context.args[0])
    except ValueError:
        await update.message.reply_text("أدخل رقم التذكير. مثال: /delreminder 1")
        return

    reminders = store.all()
    if index < 1 or index > len(reminders):
        await update.message.reply_text("لا يوجد تذكير بهذا الرقم.")
        return

    target = reminders[index - 1]
    store.delete(target.id)
    await update.message.reply_text(f"🗑 تم حذف التذكير: {target.title}")


# ──────────────────────────── daily question callbacks ────────────────────────────


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    query = update.callback_query
    if query is None or query.data is None:
        return
    if query.data.startswith((f"{CB_DELETE}:", f"{CB_EDIT}:", "rem_type:")):
        return  # routed elsewhere

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

    await query.answer("جارٍ المعالجة…")

    ctx = _ctx(context.application, chat.id)
    if query.message:
        ctx.original_message_id = query.message.message_id
    try:
        new_text = await answer_type.on_button(
            action, page_id, original, ctx
        )
    except Exception as exc:
        log.exception("Callback failed")
        try:
            await query.message.reply_text(f"⚠️ فشل: {exc}") if query.message else None
        except Exception:
            pass
        return

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
    _clean_pending(pending)
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

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_name)],
            ADD_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_type)],
            ADD_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_days)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_edit_pick, pattern=f"^{CB_EDIT}:")],
        states={
            EDIT_PICK_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field_chosen)],
            EDIT_NEW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_apply_name)],
            EDIT_NEW_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_apply_type)],
            EDIT_NEW_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_apply_days)],
        },
        fallbacks=[CommandHandler("cancel", edit_cancel)],
        per_message=False,
    )

    reminder_conv = ConversationHandler(
        entry_points=[CommandHandler("reminder", cmd_reminder_start)],
        states={
            REM_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, rem_receive_title)],
            REM_TYPE: [CallbackQueryHandler(rem_receive_type, pattern=r"^rem_type:")],
            REM_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND, rem_receive_days)],
            REM_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, rem_receive_date)],
            REM_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, rem_receive_time)],
            REM_REPEAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, rem_receive_repeat)],
        },
        fallbacks=[CommandHandler("cancel", rem_cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("progress", cmd_progress))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("edit", cmd_edit))
    app.add_handler(CommandHandler("reminders", cmd_list_reminders))
    app.add_handler(CommandHandler("delreminder", cmd_del_reminder))
    app.add_handler(add_conv)
    app.add_handler(edit_conv)
    app.add_handler(reminder_conv)
    app.add_handler(CallbackQueryHandler(on_delete_callback, pattern=f"^{CB_DELETE}:"))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(
        MessageHandler(filters.TEXT & filters.REPLY & ~filters.COMMAND, on_text_reply)
    )
