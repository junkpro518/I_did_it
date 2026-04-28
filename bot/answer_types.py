"""Registry of answer types.

Each task in Notion has a `Type` Select property whose value selects an
AnswerType from the registry below. To add a new type:

  1. Subclass `AnswerType`.
  2. Register it via `REGISTRY.register(MyType())`.
  3. Add the new value to your Notion `Type` Select options.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date

from telegram import Bot, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup

from .config import Config
from .notion_client import NotionTasks, Task

log = logging.getLogger(__name__)

# Common actions every type supports
ACTION_MISSED = "m"
ACTION_POSTPONE = "p"


@dataclass
class AnswerContext:
    """Services passed to AnswerType handlers."""

    notion: NotionTasks
    cfg: Config
    today: date
    bot: Bot
    chat_id: int
    pending: dict  # mutated when a type asks for a follow-up text reply


class AnswerType(ABC):
    """Base class for an answer type."""

    code: str  # 1-2 char identifier embedded in callback_data
    name: str  # canonical name (matches a Notion `Type` Select option)

    @abstractmethod
    def prompt(self, task: Task) -> str:
        """Question text shown above the keyboard."""

    @abstractmethod
    def keyboard(self, page_id: str) -> InlineKeyboardMarkup:
        """Inline buttons attached to the question."""

    def _common_row(self, page_id: str) -> list[InlineKeyboardButton]:
        return [
            InlineKeyboardButton(
                "❌ لم يتم", callback_data=f"{self.code}:{ACTION_MISSED}:{page_id}"
            ),
            InlineKeyboardButton(
                "⏭️ بكرة", callback_data=f"{self.code}:{ACTION_POSTPONE}:{page_id}"
            ),
        ]

    async def on_button(
        self, action: str, page_id: str, original: str, ctx: AnswerContext
    ) -> str | None:
        """Default: handle MISSED/POSTPONE. Subclasses override and call super().

        Return value:
          - str → bot edits the original message to this text.
          - None → bot does not edit (the type handled UI itself, e.g. by sending a follow-up).
        """
        if action == ACTION_MISSED:
            await ctx.notion.update_status(page_id, ctx.cfg.status_missed)
            return f"❌ {original}"
        if action == ACTION_POSTPONE:
            await ctx.notion.postpone_to_tomorrow(page_id, ctx.today)
            return f"⏭️ {original} (أُجِّلت لبكرة)"
        raise ValueError(f"Unknown action {action!r} for type {self.name!r}")

    async def on_text(self, text: str, page_id: str, original: str, ctx: AnswerContext) -> str:
        """Handle a text reply registered earlier via on_button."""
        raise NotImplementedError


# ────────────────────────── built-in types ──────────────────────────


class BooleanAnswer(AnswerType):
    code = "b"
    name = "Boolean"
    ACTION_DONE = "d"

    def prompt(self, task: Task) -> str:
        return f"• {task.title}"

    def keyboard(self, page_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ تم", callback_data=f"{self.code}:{self.ACTION_DONE}:{page_id}"
                    ),
                    InlineKeyboardButton(
                        "❌ لم يتم", callback_data=f"{self.code}:{ACTION_MISSED}:{page_id}"
                    ),
                    InlineKeyboardButton(
                        "⏭️ بكرة", callback_data=f"{self.code}:{ACTION_POSTPONE}:{page_id}"
                    ),
                ]
            ]
        )

    async def on_button(
        self, action: str, page_id: str, original: str, ctx: AnswerContext
    ) -> str | None:
        if action == self.ACTION_DONE:
            await ctx.notion.update_status(page_id, ctx.cfg.status_done)
            return f"✅ {original}"
        return await super().on_button(action, page_id, original, ctx)


class RatingAnswer(AnswerType):
    code = "r"
    name = "Rating"

    def prompt(self, task: Task) -> str:
        return f"⭐ {task.title}\nقيّم من 1 إلى 5:"

    def keyboard(self, page_id: str) -> InlineKeyboardMarkup:
        rating_row = [
            InlineKeyboardButton(str(n), callback_data=f"{self.code}:{n}:{page_id}")
            for n in range(1, 6)
        ]
        return InlineKeyboardMarkup([rating_row, self._common_row(page_id)])

    async def on_button(
        self, action: str, page_id: str, original: str, ctx: AnswerContext
    ) -> str | None:
        if action.isdigit():
            value = int(action)
            if 1 <= value <= 5:
                await ctx.notion.update_value(page_id, value)
                await ctx.notion.update_status(page_id, ctx.cfg.status_done)
                return f"⭐ {original}\nالتقييم: {value}/5"
        return await super().on_button(action, page_id, original, ctx)


class _AskTextMixin:
    """Shared logic for types that prompt a follow-up text reply."""

    code: str
    ACTION_ASK = "a"

    async def _ask(
        self,
        prompt_text: str,
        placeholder: str,
        page_id: str,
        original: str,
        ctx: AnswerContext,
    ) -> None:
        sent = await ctx.bot.send_message(
            chat_id=ctx.chat_id,
            text=prompt_text,
            reply_markup=ForceReply(input_field_placeholder=placeholder),
        )
        ctx.pending[(ctx.chat_id, sent.message_id)] = {
            "code": self.code,
            "page_id": page_id,
            "original": original,
        }


class NumberAnswer(_AskTextMixin, AnswerType):
    code = "n"
    name = "Number"

    def prompt(self, task: Task) -> str:
        return f"🔢 {task.title}"

    def keyboard(self, page_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "🔢 أدخل الرقم",
                        callback_data=f"{self.code}:{self.ACTION_ASK}:{page_id}",
                    ),
                ],
                self._common_row(page_id),
            ]
        )

    async def on_button(
        self, action: str, page_id: str, original: str, ctx: AnswerContext
    ) -> str | None:
        if action == self.ACTION_ASK:
            await self._ask(
                prompt_text=f"🔢 اكتب الرقم لـ:\n{original}",
                placeholder="مثل: 12 أو 5.5",
                page_id=page_id,
                original=original,
                ctx=ctx,
            )
            return None  # original message stays; reply triggers edit
        return await super().on_button(action, page_id, original, ctx)

    async def on_text(self, text: str, page_id: str, original: str, ctx: AnswerContext) -> str:
        try:
            number = float(text.strip().replace(",", "."))
        except ValueError:
            raise ValueError("الرد ليس رقماً صحيحاً.")
        value: float | int = int(number) if number.is_integer() else number
        await ctx.notion.update_value(page_id, value)
        await ctx.notion.update_status(page_id, ctx.cfg.status_done)
        return f"🔢 {original}\nالقيمة: {value}"


class TextAnswer(_AskTextMixin, AnswerType):
    code = "t"
    name = "Text"

    def prompt(self, task: Task) -> str:
        return f"📝 {task.title}"

    def keyboard(self, page_id: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "📝 أدخل الملاحظة",
                        callback_data=f"{self.code}:{self.ACTION_ASK}:{page_id}",
                    ),
                ],
                self._common_row(page_id),
            ]
        )

    async def on_button(
        self, action: str, page_id: str, original: str, ctx: AnswerContext
    ) -> str | None:
        if action == self.ACTION_ASK:
            await self._ask(
                prompt_text=f"📝 اكتب ملاحظتك لـ:\n{original}",
                placeholder="ملاحظة...",
                page_id=page_id,
                original=original,
                ctx=ctx,
            )
            return None
        return await super().on_button(action, page_id, original, ctx)

    async def on_text(self, text: str, page_id: str, original: str, ctx: AnswerContext) -> str:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("النص فارغ.")
        await ctx.notion.update_note(page_id, cleaned)
        await ctx.notion.update_status(page_id, ctx.cfg.status_done)
        preview = cleaned if len(cleaned) <= 80 else cleaned[:77] + "…"
        return f"📝 {original}\nالملاحظة: {preview}"


# ────────────────────────── registry ──────────────────────────


class _Registry:
    def __init__(self) -> None:
        self._by_code: dict[str, AnswerType] = {}
        self._by_name: dict[str, AnswerType] = {}
        self._default: AnswerType | None = None

    def register(self, answer_type: AnswerType, *, default: bool = False) -> None:
        if answer_type.code in self._by_code:
            raise ValueError(f"Duplicate answer-type code: {answer_type.code}")
        self._by_code[answer_type.code] = answer_type
        self._by_name[answer_type.name.lower()] = answer_type
        if default:
            self._default = answer_type

    def configure_names(self, cfg: Config) -> None:
        """Re-key by user-facing names from config so Notion lookups work."""
        renames = {
            "Boolean": cfg.type_boolean,
            "Number": cfg.type_number,
            "Rating": cfg.type_rating,
            "Text": cfg.type_text,
        }
        new_by_name: dict[str, AnswerType] = {}
        for at in self._by_code.values():
            target = renames.get(at.name, at.name)
            at.name = target
            new_by_name[target.lower()] = at
        self._by_name = new_by_name

    def for_task(self, task: Task) -> AnswerType:
        if task.type_value:
            at = self._by_name.get(task.type_value.lower())
            if at is not None:
                return at
            log.warning(
                "Unknown answer type %r for page %s; falling back to default",
                task.type_value,
                task.page_id,
            )
        if self._default is None:
            raise RuntimeError("No default answer type registered")
        return self._default

    def by_code(self, code: str) -> AnswerType | None:
        return self._by_code.get(code)


REGISTRY = _Registry()
REGISTRY.register(BooleanAnswer(), default=True)
REGISTRY.register(NumberAnswer())
REGISTRY.register(RatingAnswer())
REGISTRY.register(TextAnswer())
