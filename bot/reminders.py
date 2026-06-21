from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass
class Reminder:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str = ""
    days_of_week: list[int] = field(default_factory=list)  # 0=Mon..6=Sun; empty = date-based or daily
    specific_dates: list[str] = field(default_factory=list)  # "YYYY-MM-DD"; empty = day-based
    notify_hour: int = 8
    notify_minute: int = 0
    repeat_count: int = 0   # 0 = unlimited
    fired_count: int = 0
    active: bool = True


class RemindersStore:
    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._reminders: list[Reminder] = self._load()

    def _load(self) -> list[Reminder]:
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return [Reminder(**item) for item in data]
        except Exception:
            return []

    def _save(self) -> None:
        self._path.write_text(
            json.dumps([asdict(r) for r in self._reminders], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def add(self, r: Reminder) -> str:
        if not r.id:
            r.id = str(uuid.uuid4())
        self._reminders.append(r)
        self._save()
        return r.id

    def all(self) -> list[Reminder]:
        return [r for r in self._reminders if r.active]

    def get(self, id: str) -> Reminder | None:
        return next((r for r in self._reminders if r.id == id), None)

    def delete(self, id: str) -> bool:
        before = len(self._reminders)
        self._reminders = [r for r in self._reminders if r.id != id]
        if len(self._reminders) < before:
            self._save()
            return True
        return False

    def mark_fired(self, id: str) -> None:
        r = self.get(id)
        if r is None:
            return
        r.fired_count += 1
        if r.repeat_count > 0 and r.fired_count >= r.repeat_count:
            r.active = False
        self._save()

    def due_now(self, now: datetime, tz: str) -> list[Reminder]:
        """Return reminders that should fire at `now` (matched to hour+minute in tz)."""
        local = now.astimezone(ZoneInfo(tz))
        today_str = local.date().isoformat()
        weekday = local.weekday()  # 0=Mon..6=Sun
        h, m = local.hour, local.minute
        due = []
        for r in self._reminders:
            if not r.active:
                continue
            if r.notify_hour != h or r.notify_minute != m:
                continue
            # Check schedule
            if r.specific_dates:
                if today_str in r.specific_dates:
                    due.append(r)
            elif r.days_of_week:
                if weekday in r.days_of_week:
                    due.append(r)
            else:
                # daily (no days_of_week, no specific_dates)
                due.append(r)
        return due
