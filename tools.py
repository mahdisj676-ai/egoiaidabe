"""Everything the agent can DO. Each tool is async and receives a Ctx (the chat it is working for)."""
import asyncio
import mimetypes
import shutil
import urllib.parse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

import config
import flow
import llm
import media
import memory
import scheduler
import tguser
import voice

SPECS: list = []
REG: dict = {}  # name -> (fn, kind)   kind: free | safe | danger
MAX_OUT = 6000
S = {"type": "string"}
I = {"type": "integer"}


def tool(name, desc, props=None, required=(), kind="safe"):
    def deco(fn):
        params = None
        if props:
            params = {"type": "object", "properties": props}
            if required:
                params["required"] = list(required)
        spec = {"name": name, "description": desc}
        if params:
            spec["parameters"] = params  # tools without arguments must have NO parameters block (Gemini rejects empty ones)
        SPECS.append({"type": "function", "function": spec})
        REG[name] = (fn, kind)
        return fn
    return deco


async def _local(source: str) -> Path:
    """URL or path -> local file."""
    source = source.strip()
    if source.startswith(("http://", "https://")):
        if media.is_media_url(source):
            return (await media.download(source))["path"]
        d = media.workdir()
        async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": media.UA}) as c:
            r = await c.get(source)
            r.raise_for_status()
        ext = Path(urllib.parse.urlparse(source).path).suffix.lower()
        if not ext:
            ext = mimetypes.guess_extension(r.headers.get("content-type", "").split(";")[0]) or ".bin"
        p = d / ("file" + ext)
        p.write_bytes(r.content)
        return p
    p = Path(source).expanduser()
    if not p.exists():
        raise FileNotFoundError(source)
    return p


async def _send_by_kind(ctx, path: Path, caption: str = ""):
    k = media.kind_of(path)
    if k == "image":
        await ctx.send_photo(path, caption)
    elif k == "video":
        await ctx.send_video(await media.fit_for_telegram(path, "video"), caption)
    elif k == "audio":
        await ctx.send_audio(path, caption=caption)
    else:
        await ctx.send_document(path, caption)


# ------------------------------------------------------------------ basics
@tool("get_time", "Get the current date and time in the user's timezone.", kind="free")
async def get_time(ctx):
    return datetime.now(ZoneInfo(config.load()["timezone"])).strftime("%A %Y-%m-%d %H:%M:%S %Z")


@tool("run_shell", "Run a bash command on the Linux server and return its output.",
      {"command": S, "timeout": {"type": "integer", "description": "seconds, default 60, max 300"}}, ["command"], kind="danger")
async def run_shell(ctx, command, timeout=60):
    p = await asyncio.create_subprocess_shell(
        command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, executable="/bin/bash")
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout=min(int(timeout), 300))
    except asyncio.TimeoutError:
        p.kill()
        return "ERROR: command timed out"
    return f"exit={p.returncode}\n" + out.decode(errors="replace")[-MAX_OUT:]


@tool("read_file", "Read a text file from the server.", {"path": S}, ["path"])
async def read_file(ctx, path):
    return Path(path).expanduser().read_text(errors="replace")[:MAX_OUT]


@tool("write_file", "Create or overwrite a text file on the server.", {"path": S, "content": S}, ["path", "content"], kind="danger")
async def write_file(ctx, path, content):
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"wrote {len(content)} chars to {p}"


@tool("list_dir", "List files in a server folder.", {"path": S}, ["path"])
async def list_dir(ctx, path="."):
    items = sorted(Path(path).expanduser().iterdir())
    return "\n".join(("[dir] " if i.is_dir() else "      ") + i.name for i in items[:200]) or "(empty)"


@tool("delete_path", "Delete a file or folder on the server.", {"path": S}, ["path"], kind="danger")
async def delete_path(ctx, path):
    p = Path(path).expanduser()
    if str(p.resolve()) in ("/", "/root", "/home", "/etc", "/usr", "/var", "/boot", "/bin", "/lib", "/opt", "/opt/dabe"):
        return "REFUSED: protected path"
    shutil.rmtree(p) if p.is_dir() else p.unlink()
    return f"deleted {p}"


@tool("send_file", "Send a file from the server to the user in the chat (photo/video/audio/document, auto-detected).",
      {"path": S, "caption": S}, ["path"])
async def send_file(ctx, path, caption=""):
    p = Path(path).expanduser()
    if not p.exists():
        return "ERROR: file not found"
    await _send_by_kind(ctx, p, caption)
    return "file sent"


@tool("web_search", "Search the internet (DuckDuckGo). Returns titles, links and snippets.", {"query": S}, ["query"])
async def web_search(ctx, query):
    def _s():
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        return list(DDGS().text(query, max_results=6))
    res = await asyncio.to_thread(_s)
    return "\n\n".join(f"{r.get('title')}\n{r.get('href')}\n{r.get('body')}" for r in res) or "no results"


@tool("web_fetch", "Download a web page and return its readable text.", {"url": S}, ["url"])
async def web_fetch(ctx, url):
    from bs4 import BeautifulSoup
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": media.UA}) as c:
        r = await c.get(url)
    soup = BeautifulSoup(r.text, "html.parser")
    for t in soup(["script", "style", "nav", "footer"]):
        t.decompose()
    return " ".join(soup.get_text(" ").split())[:MAX_OUT]


# ------------------------------------------------------------------ media
@tool("download_media",
      "Download a video/reel/short/song/image from Instagram, YouTube, TikTok, Pinterest, X/Twitter, Facebook, SoundCloud, "
      "Reddit etc. and SEND it to the user in the chat. what = 'video', 'audio' (mp3 music) or 'both'.",
      {"url": S, "what": {"type": "string", "enum": ["video", "audio", "both"]}}, ["url"])
async def download_media(ctx, url, what="both"):
    return await flow.deliver_link(ctx, url, what if what in ("video", "audio", "both") else "both")


@tool("analyze_image", "LOOK at an image (URL or server path) and answer a question about it / describe it.",
      {"source": S, "question": S}, ["source"])
async def analyze_image(ctx, source, question=""):
    p = await _local(source)
    if media.kind_of(p) == "video":
        return await media.analyze_video(p, question)
    return await llm.describe([p.read_bytes()], question or "Describe this image in detail. Read any text in it.")


@tool("analyze_video",
      "WATCH a video (link from Instagram/YouTube/TikTok/etc. or server path): looks at frames + listens to speech, then answers a question about it.",
      {"source": S, "question": S}, ["source"])
async def analyze_video(ctx, source, question=""):
    return await media.analyze_video(await _local(source), question)


@tool("generate_image",
      "Create (draw) an image from a text description and send it to the user. Write the prompt in ENGLISH, rich and detailed.",
      {"prompt": S, "width": I, "height": I}, ["prompt"])
async def generate_image(ctx, prompt, width=1024, height=1024):
    p = await media.generate_image(prompt, width, height)
    await ctx.send_photo(p, "")
    return "image generated and sent to the user"


@tool("identify_song", "Recognise which song is playing in an audio/video file or link (like Shazam).", {"source": S}, ["source"])
async def identify_song(ctx, source):
    p = await _local(source)
    r = await media.identify_song(p)
    return r or "could not recognise the song"


# ------------------------------------------------------------------ scheduling, voice, memory
@tool("schedule_task",
      "Schedule a reminder or automatic message. 'when' is local ISO time like 2026-11-18T08:30:00. "
      "mode 'message' just sends the text; mode 'agent' makes the AI run the text as an instruction at that time. "
      "target is 'owner' (the user) or a Telegram @username/phone/id (needs Telegram account login). "
      "repeat_minutes>0 makes it repeat (1440 = daily).",
      {"text": S, "when": S, "repeat_minutes": I, "mode": {"type": "string", "enum": ["message", "agent"]}, "target": S},
      ["text", "when"], kind="danger")
async def schedule_task(ctx, text, when, repeat_minutes=0, mode="message", target="owner"):
    t = scheduler.add(text, when, repeat_minutes, mode, target)
    return f"scheduled #{t['id']} at {when} repeat={repeat_minutes}min mode={mode} target={target}"


@tool("list_tasks", "List scheduled reminders/tasks.")
async def list_tasks(ctx):
    ts = scheduler.list_tasks()
    return "\n".join(f"#{t['id']} {t['due_local']} every {t['repeat']}min [{t['mode']}->{t['target']}] {t['text']}" for t in ts) or "no tasks"


@tool("cancel_task", "Cancel a scheduled task by id.", {"id": S}, ["id"], kind="danger")
async def cancel_task(ctx, id):
    return "cancelled" if scheduler.remove(id) else "not found"


@tool("speak", "Reply to the user with a VOICE message (text-to-speech, Persian or English). Use when the user asks for a voice reply.",
      {"text": S}, ["text"], kind="free")
async def speak(ctx, text):
    await ctx.send_voice(text)
    return "voice message sent to the user"


@tool("remember", "Save a fact about the user to long-term memory (preferences, names, important info).", {"fact": S}, ["fact"], kind="free")
async def remember(ctx, fact):
    return "remembered #" + memory.add(fact)


@tool("forget", "Delete a long-term memory by its id.", {"id": S}, ["id"], kind="free")
async def forget(ctx, id):
    return "forgotten" if memory.delete(id) else "not found"


# ------------------------------------------------------------------ the user's own Telegram account
@tool("tg_list_chats", "List the user's recent Telegram chats (needs Telegram account login).", {"limit": I})
async def tg_list_chats(ctx, limit=20):
    return await tguser.list_chats(int(limit))


@tool("tg_read_chat", "Read the latest messages of a Telegram chat/person (needs account login).", {"target": S, "limit": I}, ["target"])
async def tg_read_chat(ctx, target, limit=15):
    return await tguser.read_chat(target, int(limit))


@tool("tg_send_text", "Send a Telegram text message to a person/group using the user's own account.",
      {"target": S, "text": S}, ["target", "text"], kind="danger")
async def tg_send_text(ctx, target, text):
    return await tguser.send_text(target, text)


@tool("tg_send_voice", "Send a Telegram VOICE message (text-to-speech) to a person using the user's own account.",
      {"target": S, "text": S}, ["target", "text"], kind="danger")
async def tg_send_voice(ctx, target, text):
    path = await voice.synth(text, "ogg")
    try:
        return await tguser.send_voice(target, str(path))
    finally:
        Path(path).unlink(missing_ok=True)


@tool("tg_send_file", "Send a file/photo/video from the server to a person using the user's own Telegram account.",
      {"target": S, "path": S, "caption": S}, ["target", "path"], kind="danger")
async def tg_send_file(ctx, target, path, caption=""):
    return await tguser.send_file(target, str(Path(path).expanduser()), caption)


def needs_approval(name: str, mode: str) -> bool:
    kind = REG[name][1]
    if kind == "free" or mode == "none":
        return False
    if mode == "dangerous":
        return kind == "danger"
    return True  # "all"
