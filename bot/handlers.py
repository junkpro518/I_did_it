from __future__ import annotations

import logging
from datetime import datetime
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
from .notion_client import LogEntry, NotionTasks

log = logging.getLogger(__name__)


# ──────────────────────────── helpers ────────────────────────────


def _today(cfg: Config):
    return datetime.now(ZoneInfo(cfg.timezone)).date()


def _authorized(update: Update, cfg: Config) -> bool:
    if cfg.telegram_chat_id is None:
        return True
    chat = update.effective_chat
    return chat is not None and chat.id == cfg.telegram_chat_id


def _pending(app: Application) -> dict:
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


# ──────────────────────────── daily review ────────────────────────────


async def _send_question(app: Application, chat_id: int, entry: LogEntry) -> None:
    answer_type = REGISTRY.for_task(entry)
    await app.bot.send_message(
        chat_id=chat_id,
        text=answer_type.prompt(entry),
        reply_markup=answer_type.keyboard(entry.page_id),
    )


async def send_today_tasks(
    app: Application, chat_id: int, cfg: Config, notion: NotionTasks
) -> int:
    today = _today(cfg)
    tasks = await notion.list_tasks()
    if not tasks:
        await app.bot.send_message(
            chat_id=chat_id,
            text="ما عندك مهام مسجّلة. استخدم /add لإضافة مهمة.",
        )
        return 0

    await app.bot.send_message(
        chat_id=chat_id,
        text=f"\U0001f319 مساء الخير! عندك {len(tasks)} مهمة لليوم. خل نراجعها:",
    )
    for task in tasks:
        try:
            entry = await notion.create_log_entry(task, today)
        except Exception:
            log.exception("Failed to create log entry for %s", task.title)
            await app.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ تعذّر إنشاء سجل لـ: {task.title}",
            )
            continue
        await _send_question(app, chat_id, entry)
    return len(tasks)


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
        lines.append(f"{i}. {t.title}  —  <i>{type_label}</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


# ──────────────────────────── /add ────────────────────────────

ADD_NAME, ADD_TYPE = range(2)
TYPE_OPTIONS = ["Boolean", "Number", "Rating", "Text"]


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

    notion: NotionTasks = context.application.bot_data["notion"]
    name = context.user_data.pop("new_task_name", "")

    try:
        await notion.create_task(name=name, type_value=chosen)
    except Exception as exc:
        log.exception("create_task failed")
        await update.message.reply_text(
            f"⚠️ فشل: {exc}", reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"✅ أُضيفت: «{name}» ({chosen})",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_task_name", None)
    if update.message:
        await update.message.reply_text("أُلغي.", reply_markup=ReplyKeyboardRemove())
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

EDIT_PICK_FIELD, EDIT_NEW_NAME, EDIT_NEW_TYPE = range(2, 5)
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
        [["الاسم", "النوع"], ["إلغاء"]],
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
    await update.message.reply_text("اختر من القائمة: الاسم / النوع / إلغاء")
    return EDIT_PICK_FIELD


async def edit_apply_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message is None or update.message.text is None:
        return EDIT_NEW_NAME
    new_name = update.message.text.strip()
    if not new_name:
        await update.message.reply_text("الاسم فارغ.")
        return EDIT_NEW_NAME
    notion: NotionTasks = context.application.bot_data["notion"]
    page_id = context.user_data.pop("edit_page_id", None)
    if not page_id:
        await update.message.reply_text("⚠️ خطأ داخلي. ابدأ من جديد بـ /edit.")
        return ConversationHandler.END
    try:
        await notion.update_task(page_id, name=new_name)
    except Exception as exc:
        log.exception("update_task name failed")
        await update.message.reply_text(f"⚠️ فشل: {exc}")
        return ConversationHandler.END
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
    page_id = context.user_data.pop("edit_page_id", None)
    if not page_id:
        await update.message.reply_text("⚠️ خطأ داخلي. ابدأ من جديد بـ /edit.")
        return ConversationHandler.END
    try:
        await notion.update_task(page_id, type_value=new_type)
    except Exception as exc:
        log.exception("update_task type failed")
        await update.message.reply_text(f"⚠️ فشل: {exc}")
        return ConversationHandler.END
    await update.message.reply_text(
        f"✅ النوع محدّث: {new_type}", reply_markup=ReplyKeyboardRemove()
    )
    return ConversationHandler.END


async def edit_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("edit_page_id", None)
    if update.message:
        await update.message.reply_text("أُلغي.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ──────────────────────────── daily question callbacks ────────────────────────────


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg: Config = context.application.bot_data["cfg"]
    query = update.callback_query
    if query is None or query.data is None:
        return
    if query.data.startswith((f"{CB_DELETE}:", f"{CB_EDIT}:")):
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

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            ADD_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_name)],
            ADD_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_receive_type)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(on_edit_pick, pattern=f"^{CB_EDIT}:")],
        states={
            EDIT_PICK_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field_chosen)],
            EDIT_NEW_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_apply_name)],
            EDIT_NEW_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_apply_type)],
        },
        fallbacks=[CommandHandler("cancel", edit_cancel)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("tasks", cmd_tasks))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CommandHandler("edit", cmd_edit))
    app.add_handler(add_conv)
    app.add_handler(edit_conv)
    app.add_handler(CallbackQueryHandler(on_delete_callback, pattern=f"^{CB_DELETE}:"))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(
        MessageHandler(filters.TEXT & filters.REPLY & ~filters.COMMAND, on_text_reply)
    )
