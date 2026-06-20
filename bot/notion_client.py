from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from notion_client import AsyncClient

from .config import Config
from .days import day_label

log = logging.getLogger(__name__)

StatusKind = Literal["select", "status"]


@dataclass
class Task:
    """A permanent task from the master Tasks database."""
    page_id: str        # Tasks Master page id
    title: str
    type_value: str     # raw value of Notion `Type`; "" if not set
    days: list[str]     # empty means every day


@dataclass
class LogEntry:
    """A daily log row (one row per task per day)."""
    page_id: str        # Daily Log page id (used for status/value/note updates)
    title: str
    type_value: str
    task_id: str        # Tasks Master page id (origin)
    status: str = ""


@dataclass
class ReportStats:
    total: int
    done: int
    missed: int
    postponed: int
    pending: int
    start: date
    end: date


class NotionTasks:
    def __init__(self, config: Config):
        self.cfg = config
        self.client = AsyncClient(auth=config.notion_token)
        self._status_kind: StatusKind | None = None
        self._db_props: dict | None = None
        self._log_db_props: dict | None = None

    # ───────── schema introspection (Tasks Master) ─────────

    async def _load_db(self) -> dict:
        if self._db_props is None:
            db = await self.client.databases.retrieve(database_id=self.cfg.notion_database_id)
            self._db_props = db["properties"]
        return self._db_props

    async def _load_log_db(self) -> dict:
        if self._log_db_props is None:
            db = await self.client.databases.retrieve(database_id=self.cfg.notion_log_database_id)
            self._log_db_props = db["properties"]
        return self._log_db_props

    async def _detect_status_kind(self) -> StatusKind:
        if self._status_kind is not None:
            return self._status_kind
        props = await self._load_log_db()
        prop = props.get(self.cfg.notion_status_property)
        if prop is None:
            raise RuntimeError(
                f"Property {self.cfg.notion_status_property!r} not found in Daily Log database"
            )
        kind = prop["type"]
        if kind not in ("select", "status"):
            raise RuntimeError(
                f"Status property must be 'select' or 'status', got {kind!r}"
            )
        self._status_kind = kind
        return kind

    async def has_log_property(self, name: str) -> bool:
        props = await self._load_log_db()
        return name in props

    async def health_check(self) -> str:
        kind = await self._detect_status_kind()
        master_props = await self._load_db()
        log_props = await self._load_log_db()
        m = []
        for label, name in [
            ("Name", self.cfg.notion_title_property),
            ("Type", self.cfg.notion_type_property),
            ("Days", self.cfg.notion_days_property),
        ]:
            m.append(f"{label}={'✓' if name in master_props else '✗'}")
        l = []
        for label, name in [
            ("Date", self.cfg.notion_date_property),
            ("Status", self.cfg.notion_status_property),
            ("Value", self.cfg.notion_value_property),
            ("Note", self.cfg.notion_note_property),
        ]:
            l.append(f"{label}={'✓' if name in log_props else '✗'}")
        return (
            f"Notion OK (status='{kind}')\n"
            f"  Tasks: {', '.join(m)}\n"
            f"  Log:   {', '.join(l)}"
        )

    # ───────── Tasks Master CRUD ─────────

    async def list_tasks(self) -> list[Task]:
        cfg = self.cfg
        results: list[Task] = []
        cursor = None
        while True:
            kwargs: dict = {"database_id": cfg.notion_database_id, "page_size": 100}
            if cfg.notion_database_id == cfg.notion_log_database_id:
                kwargs["filter"] = {
                    "property": cfg.notion_date_property,
                    "date": {"is_empty": True},
                }
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = await self.client.databases.query(**kwargs)
            for page in resp["results"]:
                if page.get("archived"):
                    continue
                title = self._extract_title(page, cfg.notion_title_property)
                type_value = self._extract_select(page, cfg.notion_type_property)
                days = self._extract_multi_select(page, cfg.notion_days_property)
                results.append(
                    Task(
                        page_id=page["id"],
                        title=title,
                        type_value=type_value,
                        days=days,
                    )
                )
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return results

    async def list_tasks_for_date(self, target_date: date) -> list[Task]:
        current_day = day_label(target_date)
        tasks = await self.list_tasks()
        return [task for task in tasks if not task.days or current_day in task.days]

    async def create_task(self, name: str, type_value: str, days: list[str] | None = None) -> Task:
        cfg = self.cfg
        props: dict = {
            cfg.notion_title_property: {
                "title": [{"type": "text", "text": {"content": name}}]
            },
        }
        if type_value:
            props[cfg.notion_type_property] = {"select": {"name": type_value}}
        if days:
            props[cfg.notion_days_property] = {
                "multi_select": [{"name": day} for day in days]
            }
        resp = await self.client.pages.create(
            parent={"database_id": cfg.notion_database_id},
            properties=props,
        )
        return Task(page_id=resp["id"], title=name, type_value=type_value, days=days or [])

    async def update_task(
        self,
        page_id: str,
        name: str | None = None,
        type_value: str | None = None,
        days: list[str] | None = None,
    ) -> None:
        cfg = self.cfg
        props: dict = {}
        if name is not None:
            props[cfg.notion_title_property] = {
                "title": [{"type": "text", "text": {"content": name}}]
            }
        if type_value is not None:
            props[cfg.notion_type_property] = (
                {"select": {"name": type_value}} if type_value else {"select": None}
            )
        if days is not None:
            props[cfg.notion_days_property] = {
                "multi_select": [{"name": day} for day in days]
            }
        if not props:
            return
        await self.client.pages.update(page_id=page_id, properties=props)

    async def delete_task(self, page_id: str) -> None:
        await self.client.pages.update(page_id=page_id, archived=True)

    # ───────── Daily Log entries ─────────

    async def create_log_entry(self, task: Task, today: date) -> LogEntry:
        cfg = self.cfg
        props: dict = {
            cfg.notion_title_property: {
                "title": [{"type": "text", "text": {"content": task.title}}]
            },
            cfg.notion_date_property: {"date": {"start": today.isoformat()}},
        }
        resp = await self.client.pages.create(
            parent={"database_id": cfg.notion_log_database_id},
            properties=props,
        )
        return LogEntry(
            page_id=resp["id"],
            title=task.title,
            type_value=task.type_value,
            task_id=task.page_id,
        )

    async def list_log_entries_for_date(self, target_date: date) -> list[LogEntry]:
        cfg = self.cfg
        results: list[LogEntry] = []
        cursor = None
        while True:
            kwargs: dict = {
                "database_id": cfg.notion_log_database_id,
                "page_size": 100,
                "filter": {
                    "property": cfg.notion_date_property,
                    "date": {"equals": target_date.isoformat()},
                },
            }
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = await self.client.databases.query(**kwargs)
            for page in resp["results"]:
                if page.get("archived"):
                    continue
                results.append(
                    LogEntry(
                        page_id=page["id"],
                        title=self._extract_title(page, cfg.notion_title_property),
                        type_value="",
                        task_id="",
                        status=self._extract_select(page, cfg.notion_status_property),
                    )
                )
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return results

    async def ensure_log_entries_for_date(self, target_date: date) -> list[LogEntry]:
        tasks = await self.list_tasks_for_date(target_date)
        existing = await self.list_log_entries_for_date(target_date)
        by_title = {entry.title: entry for entry in existing}
        entries: list[LogEntry] = []
        for task in tasks:
            current = by_title.get(task.title)
            if current is None:
                current = await self.create_log_entry(task, target_date)
            current.type_value = task.type_value
            current.task_id = task.page_id
            entries.append(current)
        return entries

    async def delete_all_log_entries(self) -> int:
        count = 0
        cursor = None
        while True:
            kwargs: dict = {"database_id": self.cfg.notion_log_database_id, "page_size": 100}
            if self.cfg.notion_database_id == self.cfg.notion_log_database_id:
                kwargs["filter"] = {
                    "property": self.cfg.notion_date_property,
                    "date": {"is_not_empty": True},
                }
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = await self.client.databases.query(**kwargs)
            for page in resp["results"]:
                if page.get("archived"):
                    continue
                await self.client.pages.update(page_id=page["id"], archived=True)
                count += 1
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return count

    async def report_stats(self, days: int = 30, today: date | None = None) -> ReportStats:
        cfg = self.cfg
        end = today or date.today()
        start = end - timedelta(days=days - 1)
        stats = ReportStats(total=0, done=0, missed=0, postponed=0, pending=0, start=start, end=end)
        cursor = None
        while True:
            kwargs: dict = {
                "database_id": cfg.notion_log_database_id,
                "page_size": 100,
                "filter": {
                    "property": cfg.notion_date_property,
                    "date": {"on_or_after": start.isoformat()},
                },
            }
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = await self.client.databases.query(**kwargs)
            for page in resp["results"]:
                if page.get("archived"):
                    continue
                row_date = self._extract_date(page, cfg.notion_date_property)
                if row_date is None or row_date > end:
                    continue
                status = self._extract_select(page, cfg.notion_status_property)
                stats.total += 1
                if status == cfg.status_done:
                    stats.done += 1
                elif status == cfg.status_missed:
                    stats.missed += 1
                elif status == cfg.status_postponed:
                    stats.postponed += 1
                else:
                    stats.pending += 1
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return stats

    # ───────── log row updates ─────────

    async def update_status(self, page_id: str, status: str) -> None:
        kind = await self._detect_status_kind()
        await self.client.pages.update(
            page_id=page_id,
            properties={
                self.cfg.notion_status_property: {kind: {"name": status}},
            },
        )

    async def update_value(self, page_id: str, value: float | int) -> None:
        if not await self.has_log_property(self.cfg.notion_value_property):
            raise RuntimeError(
                f"Daily Log property {self.cfg.notion_value_property!r} (Number) is missing"
            )
        await self.client.pages.update(
            page_id=page_id,
            properties={
                self.cfg.notion_value_property: {"number": value},
            },
        )

    async def update_note(self, page_id: str, text: str) -> None:
        if not await self.has_log_property(self.cfg.notion_note_property):
            raise RuntimeError(
                f"Daily Log property {self.cfg.notion_note_property!r} (Rich Text) is missing"
            )
        await self.client.pages.update(
            page_id=page_id,
            properties={
                self.cfg.notion_note_property: {
                    "rich_text": [{"type": "text", "text": {"content": text}}],
                },
            },
        )

    # ───────── helpers ─────────

    def _extract_title(self, page: dict, prop_name: str) -> str:
        prop = page["properties"].get(prop_name)
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

    def _extract_multi_select(self, page: dict, prop_name: str) -> list[str]:
        prop = page["properties"].get(prop_name)
        if not prop or prop.get("type") != "multi_select":
            return []
        return [item.get("name", "") for item in prop.get("multi_select", []) if item.get("name")]

    def _extract_date(self, page: dict, prop_name: str) -> date | None:
        prop = page["properties"].get(prop_name)
        if not prop or prop.get("type") != "date":
            return None
        value = prop.get("date")
        if not value or not value.get("start"):
            return None
        return date.fromisoformat(value["start"][:10])

    async def aclose(self) -> None:
        await self.client.aclose()
