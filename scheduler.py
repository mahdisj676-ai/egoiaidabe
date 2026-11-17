"""Tiny persistent scheduler for reminders and automatic messages."""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config

log = logging.getLogger("dabe.scheduler")
FILE = config.DATA / "tasks.json"


def _load() -> list:
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return []


def _save(tasks: list) -> None:
    FILE.write_text(json.dumps(tasks, indent=1, ensure_ascii=False))


def add(text: str, when_iso: str, repeat_minutes: int = 0, mode: str = "message", target: str = "owner") -> dict:
    tz = ZoneInfo(config.load()["timezone"])
    dt = datetime.fromisoformat(when_iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    task = {
        "id": uuid.uuid4().hex[:6],
        "text": text,
        "due": dt.astimezone(timezone.utc).isoformat(),
        "repeat": int(repeat_minutes or 0),
        "mode": mode,  # message | agent
        "target": target,  # owner | @username | phone | id
    }
    tasks = _load()
    tasks.append(task)
    _save(tasks)
    return task


def list_tasks() -> list:
    tz = ZoneInfo(config.load()["timezone"])
    out = []
    for t in _load():
        d = dict(t)
        d["due_local"] = datetime.fromisoformat(t["due"]).astimezone(tz).strftime("%Y-%m-%d %H:%M")
        out.append(d)
    return out


def remove(task_id: str) -> bool:
    tasks = _load()
    new = [t for t in tasks if t["id"] != task_id]
    _save(new)
    return len(new) != len(tasks)


async def loop(fire) -> None:
    async def _run(t):
        try:
            await fire(t)
        except Exception:
            log.exception("task %s failed", t.get("id"))

    while True:
        try:
            now = datetime.now(timezone.utc)
            tasks = _load()
            keep = []
            changed = False
            for t in tasks:
                due = datetime.fromisoformat(t["due"])
                if due <= now:
                    asyncio.create_task(_run(t))
                    if t["repeat"] > 0:
                        step = timedelta(minutes=t["repeat"])
                        while due <= now:
                            due += step
                        t["due"] = due.isoformat()
                        keep.append(t)
                    changed = True
                else:
                    keep.append(t)
            if changed:
                _save(keep)
        except Exception:
            log.exception("scheduler loop error")
        await asyncio.sleep(15)
