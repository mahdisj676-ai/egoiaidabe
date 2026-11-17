"""`dabe` -> [D] Doctor: checks everything, shows exactly what is wrong and offers to fix it."""
import importlib
import shutil
import socket
import subprocess
import sys

import httpx
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

import config

GOLD, GOLD2, OK, BAD, WARN = "#d4af37", "#f5d776", "#5fd38d", "#ff6b6b", "#ffb84d"
REQUIRED = {
    "telegram": "python-telegram-bot", "telethon": "telethon", "httpx": "httpx", "fastapi": "fastapi",
    "uvicorn": "uvicorn", "edge_tts": "edge-tts", "ddgs": "ddgs", "bs4": "beautifulsoup4", "rich": "rich",
    "arabic_reshaper": "arabic-reshaper", "bidi": "python-bidi", "psutil": "psutil", "yt_dlp": "yt-dlp[default]",
}


def install_shazam(console: Console):
    """Optional song recognition. Rolled back automatically if it breaks anything else."""
    console.print("Installing shazamio (may take a minute)…")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "shazamio"])
    core = "import fastapi, uvicorn, telegram, telethon, yt_dlp, httpx, pydantic"
    ok = subprocess.run([sys.executable, "-c", core], capture_output=True).returncode == 0
    if ok:
        console.print(f"[{OK}]Song recognition installed ✔  (restart the service: menu [7])[/]")
    else:
        console.print(f"[{WARN}]It conflicts with other packages - rolling back…[/]")
        subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "shazamio"], capture_output=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "-r", str(config.BASE / "requirements.txt")])
        console.print("Rolled back. Everything else is intact.")


def run(console: Console):
    rows = []
    fixes = []

    def add(name, ok, detail="", level="bad"):
        rows.append((name, ok, detail, level))

    # python packages
    for mod, pkg in REQUIRED.items():
        try:
            importlib.import_module(mod)
            add(f"python: {pkg}", True)
        except Exception as e:
            add(f"python: {pkg}", False, f"{type(e).__name__}: {str(e)[:80]}")
            fixes.append(pkg)
    try:
        importlib.import_module("shazamio")
        add("python: shazamio (song recognition)", True)
    except Exception:
        add("python: shazamio (song recognition)", False, "optional - song names won't be recognised", "warn")

    # programs
    for exe in ("ffmpeg", "ffprobe"):
        add(f"program: {exe}", bool(shutil.which(exe)), "run: apt-get install -y ffmpeg")
    add("program: deno (YouTube helper)", bool(shutil.which("deno")), "optional - some YouTube links may fail", "warn")

    # config
    cfg = config.load()
    p = cfg["provider"]
    add("AI key set", bool(cfg["keys"].get(p)), f"menu [1] or [S] (provider: {p})")
    add("Telegram bot token set", bool(cfg["bot_token"]), "menu [2] or [S]")
    add("Owner linked", bool(cfg["owner_id"]), "menu [3]", "warn")
    add("Voice-input key (Groq)", bool(cfg["keys"].get("groq")), "optional - needed to understand voice messages", "warn")
    add("Instagram/YouTube cookies", config.COOKIES.exists(), "optional - send cookies.txt to the bot if Instagram links fail", "warn")

    # live tests
    if cfg["bot_token"]:
        try:
            r = httpx.get(f"https://api.telegram.org/bot{cfg['bot_token']}/getMe", timeout=8).json()
            add("Telegram bot token valid", bool(r.get("ok")), "token rejected: " + str(r.get("description", ""))[:60])
        except Exception as e:
            add("Telegram reachable", False, f"{type(e).__name__} (server cannot reach telegram?)")
    if cfg["keys"].get(p):
        try:
            r = httpx.post(config.PROVIDERS[p]["base"] + "/chat/completions", timeout=25,
                           headers={"Authorization": f"Bearer {cfg['keys'][p]}"},
                           json={"model": cfg["models"].get(p) or config.PROVIDERS[p]["model"],
                                 "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 8})
            add(f"{p} key works", r.status_code == 200, f"HTTP {r.status_code}: {r.text[:90]}")
        except Exception as e:
            add(f"{p} reachable", False, f"{type(e).__name__}: {str(e)[:70]}")

    # service
    active = subprocess.run(["systemctl", "is-active", "dabe"], capture_output=True, text=True).stdout.strip() == "active"
    add("Service running", active, "menu [7] to start")
    try:
        s = socket.create_connection(("127.0.0.1", int(cfg["web_port"])), timeout=2)
        s.close()
        add("Web panel port open", True)
    except Exception:
        add("Web panel port open", False, "service not running yet?", "warn")

    # show
    tbl = Table(box=box.SIMPLE_HEAVY, border_style=GOLD, show_header=False, padding=(0, 1))
    tbl.add_column(width=2)
    tbl.add_column()
    tbl.add_column(style="dim")
    bad_count = 0
    for name, ok, detail, level in rows:
        if ok:
            tbl.add_row(f"[{OK}]✔[/]", name, "")
        else:
            bad_count += level == "bad"
            col = BAD if level == "bad" else WARN
            tbl.add_row(f"[{col}]{'✘' if level == 'bad' else '!'}[/]", name, detail)
    console.print(Panel(tbl, title=f"[bold {GOLD2}]◆ Doctor ◆[/]", border_style=GOLD, box=box.DOUBLE))

    # recent errors from the log
    log = config.DATA / "dabe.log"
    if log.exists():
        lines = [l for l in log.read_text(errors="replace").splitlines() if "ERROR" in l or "Traceback" in l or "Error" in l][-6:]
        if lines:
            console.print(f"[{WARN}]Recent errors in the log:[/]")
            for l in lines:
                console.print(f"  [dim]{l[:160]}[/]")

    if fixes:
        console.print(f"\n[{BAD}]Missing Python packages:[/] {', '.join(fixes)}")
        if Confirm.ask("Install them now?", default=True):
            subprocess.run([sys.executable, "-m", "pip", "install", "-q", *fixes])
            console.print("Done. Restart the service from the menu [7].")
    elif bad_count == 0:
        console.print(f"\n[{OK}]Everything important looks good ✔[/]")
    if any(r[0].startswith("python: shazamio") and not r[1] for r in rows):
        if Confirm.ask("\nInstall the OPTIONAL song recognition (tells you the song name of a reel)?", default=False):
            install_shazam(console)
