"""Admin web panel (FastAPI). Listens on 127.0.0.1 only; reach it via SSH tunnel or Cloudflare tunnel."""
import asyncio
import logging
import mimetypes
import shutil
import time
import uuid
from pathlib import Path
from zoneinfo import ZoneInfo

import psutil
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

import config
import flow
import llm
import media
import scheduler
import tguser
import voice
from agent import Agent, Ctx

log = logging.getLogger("dabe.web")
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
STATIC = Path(__file__).resolve().parent
LOG_FILE = config.DATA / "dabe.log"
STARTED = time.time()

AGENT: Agent | None = None
STATE = {"bot_running": False}
FAIL = {"n": 0, "until": 0.0}

EVENTS: list = []
BASE = {"n": 0}
APPROVALS: dict[str, asyncio.Future] = {}
WEB_MEDIA = config.DATA / "web_media"
WEB_MEDIA.mkdir(parents=True, exist_ok=True)


def push(ev: dict):
    EVENTS.append(ev)
    if len(EVENTS) > 800:
        del EVENTS[:400]
        BASE["n"] += 400


async def guard(request: Request):
    if not config.verify_token(request.cookies.get("dabe_session", "")):
        raise HTTPException(401, "login required")


class WebCtx(Ctx):
    async def approve(self, name, args):
        aid = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        APPROVALS[aid] = fut
        push({"type": "approval", "id": aid, "tool": name, "args": args})
        try:
            return await asyncio.wait_for(fut, 300)
        except asyncio.TimeoutError:
            return False
        finally:
            APPROVALS.pop(aid, None)

    def _publish(self, path, kind, caption=""):
        now = time.time()
        for old in WEB_MEDIA.iterdir():  # keep the folder small
            try:
                if now - old.stat().st_mtime > 12 * 3600:
                    old.unlink()
            except Exception:
                pass
        name = uuid.uuid4().hex[:8] + Path(path).suffix
        shutil.copy(path, WEB_MEDIA / name)
        push({"type": "media", "kind": kind, "url": f"/api/media/{name}", "caption": caption, "name": Path(path).name})

    async def notify(self, text):
        push({"type": "assistant", "text": text})

    async def send_voice(self, text):
        self._publish(await voice.synth(text, "mp3"), "audio")

    async def send_photo(self, path, caption=""):
        self._publish(path, "image", caption)

    async def send_video(self, path, caption=""):
        self._publish(path, "video", caption)

    async def send_audio(self, path, caption="", title=None, performer=None):
        self._publish(path, "audio", " - ".join(x for x in (performer, title) if x) or caption)

    async def send_document(self, path, caption=""):
        self._publish(path, "file", caption)


@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC / "index.html").read_text()


@app.post("/api/login")
async def login(request: Request):
    cfg = config.load()
    if not cfg["web_password_hash"]:
        return JSONResponse({"error": "No panel password set. Run `dabe` in the server terminal -> Web panel."}, 403)
    if time.time() < FAIL["until"]:
        return JSONResponse({"error": "Too many attempts. Wait a few minutes."}, 429)
    body = await request.json()
    if not config.check_password(str(body.get("password", "")), cfg["web_password_hash"]):
        FAIL["n"] += 1
        if FAIL["n"] >= 8:
            FAIL.update(n=0, until=time.time() + 300)
        await asyncio.sleep(1)
        return JSONResponse({"error": "Wrong password"}, 401)
    FAIL["n"] = 0
    r = JSONResponse({"ok": True})
    r.set_cookie("dabe_session", config.make_token(), httponly=True, samesite="strict",
                 secure=request.headers.get("x-forwarded-proto") == "https", max_age=7 * 86400)
    return r


@app.post("/api/logout")
async def logout():
    r = JSONResponse({"ok": True})
    r.delete_cookie("dabe_session")
    return r


@app.get("/api/me", dependencies=[Depends(guard)])
async def me():
    return {"ok": True}


@app.get("/api/status", dependencies=[Depends(guard)])
async def status():
    cfg = config.load()
    vm = psutil.virtual_memory()
    return {
        "version": config.VERSION,
        "uptime_min": int((time.time() - STARTED) / 60),
        "cpu": psutil.cpu_percent(interval=None),
        "ram": vm.percent,
        "disk": psutil.disk_usage("/").percent,
        "provider": cfg["provider"],
        "model": llm.model_for(cfg, cfg["provider"]),
        "bot": STATE["bot_running"],
        "owner": bool(cfg["owner_id"]),
        "account": tguser.available(),
        "tasks": len(scheduler.list_tasks()),
        "timezone": cfg["timezone"],
        "approval": cfg["approval"],
    }


@app.get("/api/logs", dependencies=[Depends(guard)])
async def logs(n: int = 150):
    try:
        lines = LOG_FILE.read_text(errors="replace").splitlines()[-min(n, 500):]
    except Exception:
        lines = []
    return {"lines": lines}


@app.get("/api/tasks", dependencies=[Depends(guard)])
async def tasks():
    return scheduler.list_tasks()


@app.delete("/api/tasks/{tid}", dependencies=[Depends(guard)])
async def del_task(tid: str):
    return {"ok": scheduler.remove(tid)}


@app.get("/api/settings", dependencies=[Depends(guard)])
async def get_settings():
    cfg = config.load()
    return {
        "provider": cfg["provider"],
        "providers": {k: {"label": v["label"], "model": llm.model_for(cfg, k), "key_set": bool(cfg["keys"].get(k))}
                      for k, v in config.PROVIDERS.items()},
        "timezone": cfg["timezone"],
        "approval": cfg["approval"],
        "lang": cfg["lang"],
    }


@app.post("/api/settings", dependencies=[Depends(guard)])
async def set_settings(request: Request):
    b = await request.json()
    cfg = config.load()
    if "provider" in b:
        if b["provider"] not in config.PROVIDERS:
            raise HTTPException(400, "bad provider")
        cfg["provider"] = b["provider"]
    if b.get("model") and b.get("model_for") in config.PROVIDERS:
        cfg["models"][b["model_for"]] = str(b["model"]).strip()
    if b.get("key_for") in config.PROVIDERS and b.get("key"):
        cfg["keys"][b["key_for"]] = str(b["key"]).strip()
    if b.get("approval") in ("all", "dangerous", "none"):
        cfg["approval"] = b["approval"]
    if b.get("lang") in ("fa", "en"):
        cfg["lang"] = b["lang"]
    if b.get("timezone"):
        try:
            ZoneInfo(b["timezone"])
        except Exception:
            raise HTTPException(400, "bad timezone")
        cfg["timezone"] = b["timezone"]
    config.save(cfg)
    return {"ok": True}


@app.post("/api/clear", dependencies=[Depends(guard)])
async def clear():
    AGENT.clear("web")
    EVENTS.clear()
    return {"ok": True}


@app.post("/api/chat", dependencies=[Depends(guard)])
async def chat(request: Request):
    text = str((await request.json()).get("message", "")).strip()
    if not text:
        raise HTTPException(400, "empty")
    push({"type": "user", "text": text})

    async def work():
        ctx = WebCtx()
        try:
            req = flow.parse_link_request(text)
            if req:
                push({"type": "assistant", "text": "⏬ Downloading…"})
                await flow.deliver_link(ctx, *req)
            else:
                push({"type": "assistant", "text": await AGENT.run("web", text, ctx)})
        except media.MediaError as e:
            push({"type": "assistant", "text": f"⚠️ {e}"})
        except Exception as e:
            log.exception("web chat error")
            push({"type": "assistant", "text": f"⚠️ {e}"})

    asyncio.create_task(work())
    return {"ok": True}


@app.get("/api/events", dependencies=[Depends(guard)])
async def events(since: int = 0):
    start = max(since - BASE["n"], 0)
    return {"events": EVENTS[start:], "next": BASE["n"] + len(EVENTS)}


@app.post("/api/approve", dependencies=[Depends(guard)])
async def approve(request: Request):
    b = await request.json()
    fut = APPROVALS.get(b.get("id"))
    if fut and not fut.done():
        fut.set_result(bool(b.get("ok")))
    return {"ok": True}


@app.get("/api/media/{name}", dependencies=[Depends(guard)])
async def media_file(name: str):
    p = WEB_MEDIA / Path(name).name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type=mimetypes.guess_type(p.name)[0] or "application/octet-stream")
