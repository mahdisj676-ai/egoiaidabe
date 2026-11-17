"""Long-term memory: facts the user told the agent to remember."""
import json
import uuid

import config

FILE = config.DATA / "memory.json"


def load() -> list:
    try:
        return json.loads(FILE.read_text())
    except Exception:
        return []


def _save(items: list) -> None:
    FILE.write_text(json.dumps(items, ensure_ascii=False, indent=1))


def add(text: str) -> str:
    items = load()
    mid = uuid.uuid4().hex[:5]
    items.append({"id": mid, "text": text.strip()[:500]})
    _save(items[-100:])
    return mid


def delete(mid: str) -> bool:
    items = load()
    new = [i for i in items if i["id"] != mid]
    _save(new)
    return len(new) != len(items)


def prompt_block() -> str:
    items = load()[-60:]
    if not items:
        return ""
    return "Things you remember about the user (long-term memory):\n" + "\n".join(f"- [{i['id']}] {i['text']}" for i in items)
