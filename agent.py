"""The brain: LLM + tools loop with human approval, vision, memory."""
import asyncio
import json
import logging
import platform
from datetime import datetime
from zoneinfo import ZoneInfo

import config
import llm
import memory
import tools

log = logging.getLogger("dabe.agent")
MAX_STEPS = 12


class Ctx:
    """Channel adapter (Telegram / Web). Subclasses implement these."""

    async def approve(self, name: str, args: dict) -> bool:
        raise NotImplementedError

    async def notify(self, text: str) -> None:
        raise NotImplementedError

    async def send_voice(self, text: str) -> None:
        raise NotImplementedError

    async def send_photo(self, path, caption: str = "") -> None:
        raise NotImplementedError

    async def send_video(self, path, caption: str = "") -> None:
        raise NotImplementedError

    async def send_audio(self, path, caption: str = "", title=None, performer=None) -> None:
        raise NotImplementedError

    async def send_document(self, path, caption: str = "") -> None:
        raise NotImplementedError


def _window(h: list, n: int = 40) -> list:
    w = h[-n:]
    while w and w[0].get("role") != "user":  # never start in the middle of a tool exchange
        w = w[1:]
    return w


def _strip_images(content):
    """Keep history small: replace base64 images with a short marker."""
    if isinstance(content, list):
        return [p if p.get("type") != "image_url" else {"type": "text", "text": "[image]"} for p in content]
    return content


class Agent:
    def __init__(self):
        self.hist: dict[str, list] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    # -- history ------------------------------------------------------------
    def _file(self, key):
        return config.DATA / f"history_{key}.json"

    def _get(self, key):
        if key not in self.hist:
            try:
                self.hist[key] = json.loads(self._file(key).read_text())
            except Exception:
                self.hist[key] = []
        return self.hist[key]

    def clear(self, key=None):
        for k in ([key] if key else list(set(list(self.hist) + ["tg", "web", "sched"]))):
            self.hist[k] = []
            self._file(k).unlink(missing_ok=True)

    def _persist(self, key):
        try:
            self._file(key).write_text(json.dumps(self.hist[key][-60:], ensure_ascii=False))
        except Exception:
            log.exception("history save failed")

    def note(self, key: str, user: str, assistant: str) -> None:
        """Add an exchange that happened outside the LLM loop (e.g. a video analysis) to the memory."""
        h = self._get(key)
        h.append({"role": "user", "content": user})
        h.append({"role": "assistant", "content": assistant})
        self._persist(key)

    # -- prompt -------------------------------------------------------------
    def system_prompt(self) -> str:
        cfg = config.load()
        now = datetime.now(ZoneInfo(cfg["timezone"])).strftime("%A %Y-%m-%d %H:%M %Z")
        base = (
            "You are Dabe, a loyal, extremely capable personal assistant (a smart human servant) living on the "
            f"user's Linux server ({platform.node()}). You can: run shell commands, manage files, search the web, "
            "SEE images and WATCH videos (analyze_image / analyze_video), DRAW images (generate_image), DOWNLOAD "
            "videos/reels/music from Instagram, YouTube, TikTok, Pinterest, X and more (download_media), recognise songs, "
            "schedule reminders and automatic messages, speak with voice messages, remember facts about the user, and (if the "
            "user logged in) read and send Telegram messages from the user's own account.\n"
            f"Current time: {now} (timezone {cfg['timezone']}).\n"
            "Rules:\n"
            "- Reply in the language the user writes (Persian or English). Be concise, warm and practical.\n"
            "- DO things with tools instead of explaining how. Chain several tool calls when needed. Never claim you cannot see "
            "images/videos or download links: use the tools.\n"
            "- If the user pastes a link to a video/reel, use download_media (what='both' sends video + mp3) unless they ask something else about it.\n"
            "- For generate_image always write the prompt in rich English, whatever language the user used.\n"
            "- Many tools need the user's approval; if a result says DENIED, do not retry; ask what to do.\n"
            "- When the user asks for a voice reply, call the speak tool.\n"
            "- When the user shares a lasting preference or personal fact, call remember.\n"
            "- Before destructive actions (deleting, overwriting, stopping services) say briefly what you will do.\n"
            "- Never reveal API keys or tokens from the config."
        )
        mem = memory.prompt_block()
        return base + ("\n\n" + mem if mem else "")

    # -- main loop ----------------------------------------------------------
    async def run(self, key: str, text: str, ctx: Ctx, images: list | None = None) -> str:
        lock = self.locks.setdefault(key, asyncio.Lock())
        async with lock:
            h = self._get(key)
            if images:
                content = [{"type": "text", "text": text or "Describe this image."}] + [llm.img_part(b) for b in images]
            else:
                content = text
            h.append({"role": "user", "content": content})
            user_idx = len(h) - 1
            try:
                for _ in range(MAX_STEPS):
                    msgs = [{"role": "system", "content": self.system_prompt()}] + _window(h)
                    msg, _provider = await llm.chat(msgs, tools.SPECS)
                    calls = msg.get("tool_calls") or []
                    entry = {"role": "assistant", "content": msg.get("content") or ""}
                    if calls:
                        entry["tool_calls"] = calls
                    h.append(entry)
                    if not calls:
                        return entry["content"] or "…"
                    results = await self._run_calls(calls, ctx)
                    for c, res in zip(calls, results):
                        h.append({"role": "tool", "tool_call_id": c.get("id", c["function"]["name"]),
                                  "name": c["function"]["name"], "content": str(res)[:6000]})
                return "I used too many steps on this. Tell me how to continue."
            finally:
                h[user_idx]["content"] = _strip_images(h[user_idx]["content"])
                self._persist(key)

    async def _run_calls(self, calls: list, ctx: Ctx) -> list:
        cfg = config.load()
        parsed = []
        for c in calls:
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            parsed.append((name, args))

        # 1) approvals, one at a time (so the user sees a clear sequence)
        denied = {}
        for i, (name, args) in enumerate(parsed):
            if name in tools.REG and tools.needs_approval(name, cfg["approval"]):
                if not await ctx.approve(name, args):
                    denied[i] = "DENIED by the user (or approval timed out)."

        async def go(i, name, args):
            if i in denied:
                return denied[i]
            return await self._exec(name, args, ctx)

        # 2) run: in parallel only when every call is read-only/harmless (faster), otherwise in order
        parallel = len(parsed) > 1 and all(tools.REG.get(n, (None, "danger"))[1] in ("free", "safe") for n, _ in parsed)
        if parallel:
            return list(await asyncio.gather(*[go(i, n, a) for i, (n, a) in enumerate(parsed)]))
        return [await go(i, n, a) for i, (n, a) in enumerate(parsed)]

    async def _exec(self, name: str, args: dict, ctx: Ctx) -> str:
        if name not in tools.REG:
            return f"ERROR: unknown tool {name}"
        fn, _ = tools.REG[name]
        try:
            log.info("tool %s %s", name, json.dumps(args, ensure_ascii=False)[:300])
            return await fn(ctx, **args)
        except TypeError as e:
            return f"ERROR: bad arguments for {name}: {e}"
        except Exception as e:
            log.exception("tool %s failed", name)
            return f"ERROR: {type(e).__name__}: {e}"
