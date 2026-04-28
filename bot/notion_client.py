from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from notion_client import AsyncClient

from .config import Config

log = logging.getLogger(__name__)

StatusKind = Literal["select", "status"]


@dataclass
class Task:
    page_id: str
    title: str
    type_value: str  # raw value from Notion Type property; "" if not set


class NotionTasks:
    def __init__(self, config: Config):
        self.cfg = config
        self.client = AsyncClient(auth=config.notion_token)
        self._status_kind: StatusKind | None = None
        self._db_props: dict | None = None

    async def _load_db(self) -> dict:
        if self._db_props is None:
            db = await self.client.databases.retrieve(database_id=self.cfg.notion_database_id)
            self._db_props = db["properties"]
        return self._db_props

    async def _detect_status_kind(self) -> StatusKind:
        if self._status_kind is not None:
            return self._status_kind
        props = await self._load_db()
        prop = props.get(self.cfg.notion_status_property)
        if prop is None:
            raise RuntimeError(
                f"Property {self.cfg.notion_status_property!r} not found in Notion database"
            )
        kind = prop["type"]
        if kind not in ("select", "status"):
            raise RuntimeError(
                f"Property {self.cfg.notion_status_property!r} must be 'select' or 'status', got {kind!r}"
            )
        self._status_kind = kind
        return kind

    async def has_property(self, name: str) -> bool:
        props = await self._load_db()
        return name in props

    async def health_check(self) -> str:
        kind = await self._detect_status_kind()
        props = await self._load_db()
        present = []
        for label, name in [
            ("Type", self.cfg.notion_type_property),
            ("Value", self.cfg.notion_value_property),
            ("Note", self.cfg.notion_note_property),
        ]:
            present.append(f"{label}={'✓' if name in props else '✗'}")
        return f"Notion OK (status='{kind}', {', '.join(present)})"

    async def query_today_tasks(self, today: date) -> list[Task]:
        cfg = self.cfg
        kind = await self._detect_status_kind()

        filt = {
            "and": [
                {
                    "property": cfg.notion_date_property,
                    "date": {"equals": today.isoformat()},
                },
                {
                    "property": cfg.notion_status_property,
                    kind: {"does_not_equal": cfg.status_done},
                },
            ]
        }

        results: list[Task] = []
        cursor = None
        while True:
            kwargs = {"database_id": cfg.notion_database_id, "filter": filt, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = await self.client.databases.query(**kwargs)
            for page in resp["results"]:
                title = self._extract_title(page)
                type_value = self._extract_select(page, cfg.notion_type_property)
                results.append(Task(page_id=page["id"], title=title, type_value=type_value))
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return results

    def _extract_title(self, page: dict) -> str:
        prop = page["properties"].get(self.cfg.notion_title_property)
        if not prop or prop.get("type") != "title":
            return "(بدون عنوان)"
        rich = prop.get("title") or []
        return "".join(part.get("plain_text", "") for part in rich) or "(بدون عنوان)"

    def _extract_select(self, page: dict, prop_name: str) -> str:
        prop = page["properties"].get(prop_name)
        if not prop:
            return ""
        kind = prop.get("type")
        if kind == "select":
            sel = prop.get("select")
            return sel.get("name", "") if sel else ""
        if kind == "status":
            sel = prop.get("status")
            return sel.get("name", "") if sel else ""
        return ""

    async def update_status(self, page_id: str, status: str) -> None:
        kind = await self._detect_status_kind()
        await self.client.pages.update(
            page_id=page_id,
            properties={
                self.cfg.notion_status_property: {kind: {"name": status}},
            },
        )

    async def update_value(self, page_id: str, value: float | int) -> None:
        if not await self.has_property(self.cfg.notion_value_property):
            raise RuntimeError(
                f"Notion property {self.cfg.notion_value_property!r} (Number) is missing"
            )
        await self.client.pages.update(
            page_id=page_id,
            properties={
                self.cfg.notion_value_property: {"number": value},
            },
        )

    async def update_note(self, page_id: str, text: str) -> None:
        if not await self.has_property(self.cfg.notion_note_property):
            raise RuntimeError(
                f"Notion property {self.cfg.notion_note_property!r} (Rich Text) is missing"
            )
        await self.client.pages.update(
            page_id=page_id,
            properties={
                self.cfg.notion_note_property: {
                    "rich_text": [{"type": "text", "text": {"content": text}}],
                },
            },
        )

    async def postpone_to_tomorrow(self, page_id: str, today: date) -> None:
        kind = await self._detect_status_kind()
        tomorrow = (today + timedelta(days=1)).isoformat()
        await self.client.pages.update(
            page_id=page_id,
            properties={
                self.cfg.notion_date_property: {"date": {"start": tomorrow}},
                self.cfg.notion_status_property: {kind: {"name": self.cfg.status_postponed}},
            },
        )

    async def aclose(self) -> None:
        await self.client.aclose()
