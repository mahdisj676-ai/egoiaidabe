"""Optional: act through YOUR Telegram account (Telethon) to message other people."""
import asyncio
import getpass
import logging
from pathlib import Path

import config

log = logging.getLogger("dabe.tguser")
SESSION = str(config.DATA / "user")
_client = None


def session_exists() -> bool:
    return Path(SESSION + ".session").exists()


def available() -> bool:
    return _client is not None


async def start():
    global _client
    cfg = config.load()
    if not (cfg["api_id"] and cfg["api_hash"] and session_exists()):
        return None
    from telethon import TelegramClient

    c = TelegramClient(SESSION, int(cfg["api_id"]), cfg["api_hash"])
    await c.connect()
    if not await c.is_user_authorized():
        await c.disconnect()
        return None
    _client = c
    log.info("Telegram account client connected")
    return c


async def stop():
    if _client:
        await _client.disconnect()


async def _entity(target: str):
    if not _client:
        raise RuntimeError("Telegram account is not logged in. Run `dabe` -> [4].")
    t = str(target).strip()
    if t.lstrip("-").isdigit():
        t = int(t)
    try:
        return await _client.get_entity(t)
    except Exception:
        if isinstance(t, str):  # try matching a chat by its display name
            low = t.lower().lstrip("@")
            async for d in _client.iter_dialogs(limit=300):
                if low and low in (d.name or "").lower():
                    return d.entity
        raise RuntimeError(f"Could not find '{target}' in Telegram.")


async def send_text(target: str, text: str) -> str:
    ent = await _entity(target)
    await _client.send_message(ent, text)
    return f"sent to {target}"


async def send_voice(target: str, path: str) -> str:
    ent = await _entity(target)
    await _client.send_file(ent, path, voice_note=True)
    return f"voice sent to {target}"


async def send_file(target: str, path: str, caption: str = "") -> str:
    ent = await _entity(target)
    await _client.send_file(ent, path, caption=caption or None)
    return f"file sent to {target}"


async def list_chats(limit: int = 20) -> str:
    if not _client:
        raise RuntimeError("Telegram account is not logged in. Run `dabe` -> [4].")
    rows = []
    async for d in _client.iter_dialogs(limit=min(limit, 60)):
        uname = getattr(d.entity, "username", None)
        rows.append(f"{d.name} | id={d.id}" + (f" | @{uname}" if uname else "") + (f" | unread={d.unread_count}" if d.unread_count else ""))
    return "\n".join(rows) or "no chats"


async def read_chat(target: str, limit: int = 15) -> str:
    ent = await _entity(target)
    msgs = await _client.get_messages(ent, limit=min(limit, 40))
    out = []
    for m in reversed(msgs):
        who = "me" if m.out else (getattr(m.sender, "first_name", None) or str(m.sender_id))
        extra = f" [{type(m.media).__name__}]" if m.media else ""
        out.append(f"{m.date:%m-%d %H:%M} {who}: {(m.text or '')[:400]}{extra}")
    return "\n".join(out) or "no messages"


def login_cli(api_id: int, api_hash: str) -> None:
    """Interactive login (phone / code / 2FA) - called from the terminal menu."""
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError

    async def run():
        c = TelegramClient(SESSION, api_id, api_hash)
        await c.connect()
        if not await c.is_user_authorized():
            phone = input("Phone with country code (e.g. +98912xxxxxxx): ").strip()
            await c.send_code_request(phone)
            code = input("Code sent to your Telegram app: ").strip()
            try:
                await c.sign_in(phone, code)
            except SessionPasswordNeededError:
                await c.sign_in(password=getpass.getpass("2FA password (hidden): "))
        me = await c.get_me()
        print(f"Logged in as {me.first_name} (id {me.id})")
        await c.disconnect()

    asyncio.run(run())
