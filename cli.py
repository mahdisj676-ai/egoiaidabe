"""Ego/Dabe terminal control panel (gold & black)."""
import getpass
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

import config
import doctor
import tguser
from i18n import shape, t

console = Console()
GOLD, GOLD2, DIM, OK, BAD, WARN = "#d4af37", "#f5d776", "#8a6d1d", "#5fd38d", "#ff6b6b", "#ffb84d"
SERVICE = "dabe"

ART_EGO = [
    " ███████╗ ██████╗  ██████╗ ",
    " ██╔════╝██╔════╝ ██╔═══██╗",
    " █████╗  ██║  ███╗██║   ██║",
    " ██╔══╝  ██║   ██║██║   ██║",
    " ███████╗╚██████╔╝╚██████╔╝",
    " ╚══════╝ ╚═════╝  ╚═════╝ ",
]
ART_DABE = [
    " ██████╗  █████╗ ██████╗ ███████╗",
    " ██╔══██╗██╔══██╗██╔══██╗██╔════╝",
    " ██║  ██║███████║██████╔╝█████╗  ",
    " ██║  ██║██╔══██║██╔══██╗██╔══╝  ",
    " ██████╔╝██║  ██║██████╔╝███████╗",
    " ╚═════╝ ╚═╝  ╚═╝╚═════╝ ╚══════╝",
]
SHADES = ["#fff1a8", "#f5d776", "#e8c04a", "#d4af37", "#b8932b", "#8a6d1d"]


def banner():
    lines = []
    for art in (ART_EGO, ART_DABE):
        for i, row in enumerate(art):
            lines.append(Align.center(Text(row, style=f"bold {SHADES[i]}")))
    lines.append(Align.center(Text(f"✦  {shape(t('subtitle'))}  ✦", style=f"{GOLD}")))
    lines.append(Align.center(Text(f"v{config.VERSION}", style=DIM)))
    return Group(*lines)


# ---- helpers ------------------------------------------------------------------
def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def service_active() -> bool:
    return sh(["systemctl", "is-active", SERVICE]).stdout.strip() == "active"


def svc(action: str):
    return subprocess.run(["systemctl", action, SERVICE], capture_output=True, text=True)


def pause():
    console.print()
    Prompt.ask(f"[{DIM}]{t('enter')}[/]", default="", show_default=False)


def ok(msg):
    console.print(f"[{OK}]✔[/] {shape(msg)}")


def bad(msg):
    console.print(f"[{BAD}]✘[/] {shape(msg)}")


def info(msg):
    console.print(f"[{GOLD2}]{shape(msg)}[/]")


_botname = {}


def bot_username(token: str):
    if not token:
        return None
    if token in _botname:
        return _botname[token]
    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=4)
        name = "@" + r.json()["result"]["username"] if r.json().get("ok") else "?"
    except Exception:
        name = "?"
    _botname[token] = name
    return name


def dot(color):
    return f"[{color}]●[/]"


def status_panel():
    cfg = config.load()
    tbl = Table.grid(padding=(0, 2))
    tbl.add_column(style=DIM)
    tbl.add_column()
    p = cfg["provider"]
    has_key = bool(cfg["keys"].get(p))
    model = cfg["models"].get(p) or config.PROVIDERS[p]["model"]
    tbl.add_row(t("ai"), f"{dot(OK if has_key else BAD)} {p} / {model}" if has_key else f"{dot(BAD)} {t('unset')}")
    if cfg["bot_token"]:
        tbl.add_row(t("bot"), f"{dot(OK)} {bot_username(cfg['bot_token'])}")
    else:
        tbl.add_row(t("bot"), f"{dot(BAD)} {t('unset')}")
    tbl.add_row(t("owner"), f"{dot(OK)} {cfg['owner_id']}" if cfg["owner_id"] else f"{dot(WARN)} {t('unlinked')}")
    tbl.add_row(t("account"), f"{dot(OK)} {t('loggedin')}" if tguser.session_exists() else f"{dot(DIM)} {t('notin')}")
    tbl.add_row(t("tz"), f"[{GOLD2}]{cfg['timezone']}[/]")
    tbl.add_row(t("approval"), f"[{GOLD2}]{cfg['approval']}[/]")
    if cfg.get("domain"):
        web_val = f"{dot(OK)} https://{cfg['domain']}"
    elif cfg["web_host"] == "0.0.0.0":
        web_val = f"{dot(WARN)} http://IP:{cfg['web_port']}"
    elif cfg["web_password_hash"]:
        web_val = f"{dot(OK)} {t('set')} (private)"
    else:
        web_val = f"{dot(BAD)} {t('unset')}"
    tbl.add_row(t("web"), web_val)
    tbl.add_row(t("service"), f"{dot(OK)} {t('running')}" if service_active() else f"{dot(BAD)} {t('stopped')}")
    return Panel(tbl, title=f"[bold {GOLD2}]◆ {t('status')} ◆[/]", border_style=GOLD, box=box.DOUBLE, padding=(1, 2))


MENU = [
    ("S", "m_quick"), ("1", "m_ai"), ("2", "m_bot"), ("3", "m_pair"), ("4", "m_login"),
    ("5", "m_web"), ("6", "m_settings"), ("7", "m_start"), ("8", "m_stop"), ("9", "m_logs"),
    ("D", "m_doctor"), ("U", "m_update"), ("X", "m_uninstall"), ("0", "m_exit"),
]


def menu_panel():
    tbl = Table.grid(padding=(0, 1))
    tbl.add_column(justify="center")
    tbl.add_column()
    for key, label in MENU:
        color = BAD if key in ("X", "8") else GOLD
        tbl.add_row(f"[bold {color}][ {key} ][/]", f"[{GOLD2}]{t(label)}[/]")
    return Panel(tbl, title=f"[bold {GOLD2}]◆ {t('menu')} ◆[/]", border_style=GOLD, box=box.ROUNDED, padding=(1, 2))


def screen():
    console.clear()
    console.print(banner())
    console.print()
    console.print(status_panel())
    console.print(menu_panel())


# ---- actions ------------------------------------------------------------------
def test_key(provider: str, key: str) -> bool:
    info("Testing key…")
    try:
        cfg = config.load()
        r = httpx.post(
            config.PROVIDERS[provider]["base"] + "/chat/completions",
            headers={"Authorization": f"Bearer {key}"}, timeout=25,
            json={"model": cfg["models"].get(provider) or config.PROVIDERS[provider]["model"],
                  "messages": [{"role": "user", "content": "Say OK"}], "max_tokens": 8})
        if r.status_code == 200:
            ok("Key works!")
            return True
        bad(f"HTTP {r.status_code}: {r.text[:160]}")
    except Exception as e:
        bad(f"Could not reach the provider: {e}")
    return False


def act_ai():
    console.print()
    keys = list(config.PROVIDERS)
    for i, k in enumerate(keys, 1):
        has = "✔" if config.load()["keys"].get(k) else " "
        console.print(f"[{GOLD}][{i}][/] {config.PROVIDERS[k]['label']} [{OK}]{has}[/]  [{DIM}]{config.PROVIDERS[k]['url']}[/]")
    console.print()
    info("Recommended free: Gemini (brain) + Groq (voice input).")
    c = Prompt.ask("Provider number to set (Enter = cancel)", default="")
    if not c.isdigit() or not 1 <= int(c) <= len(keys):
        return
    p = keys[int(c) - 1]
    info(f"Get your key at: {config.PROVIDERS[p]['url']}")
    key = getpass.getpass("Paste API key (hidden, then Enter): ").strip()
    if not key:
        return
    config.set_key(p, key)
    test_key(p, key)
    if Confirm.ask(f"Use {p} as the main brain?", default=True):
        config.update(provider=p)
        ok(f"Main provider: {p}")
    if p != "groq" and not config.load()["keys"].get("groq"):
        info("Tip: add a free Groq key too, to let the bot understand your voice messages.")


def act_ai_quick():
    """Quick-setup version: asks directly for the Gemini key (no provider list)."""
    console.print()
    info("Get a FREE Gemini key: open aistudio.google.com/apikey -> Create API key -> copy it.")
    key = getpass.getpass("Paste Gemini API key (hidden, then Enter): ").strip()
    if key:
        config.set_key("gemini", key)
        config.update(provider="gemini")
        test_key("gemini", key)
    console.print()
    info("Optional: free Groq key so the bot understands your VOICE messages (console.groq.com/keys).")
    g = getpass.getpass("Paste Groq key, or just press Enter to skip: ").strip()
    if g:
        config.set_key("groq", g)
        ok("Groq key saved")


def act_bot():
    console.print()
    info("Create a bot with @BotFather in Telegram and copy its token.")
    tok = getpass.getpass("Bot token (hidden): ").strip()
    if not tok:
        return
    name = bot_username(tok)
    if name == "?":
        bad("Telegram rejected this token (or no internet). Saved anyway; double-check it.")
    else:
        ok(f"Bot found: {name}")
    config.update(bot_token=tok)
    if service_active():
        svc("restart")
        ok("Service restarted")


def act_pair():
    cfg = config.load()
    if not cfg["bot_token"]:
        bad("Set the bot token first ([2]).")
        return
    console.print()
    c = Prompt.ask("[1] Pair with a code (easy)   [2] Type my Telegram numeric ID", choices=["1", "2"], default="1")
    if c == "2":
        v = Prompt.ask("Your numeric Telegram ID")
        if v.strip().isdigit():
            config.update(owner_id=int(v.strip()), pair_code="")
            ok("Owner saved")
        return
    code = secrets.token_hex(3)
    config.update(owner_id=0, pair_code=code)
    svc("restart")
    time.sleep(3)
    uname = bot_username(cfg["bot_token"])
    console.print(Panel(
        f"1) Open Telegram and go to [bold]{uname}[/]\n2) Send exactly:\n\n   [bold {GOLD2}]/start {code}[/]\n\nWaiting up to 3 minutes… (Ctrl+C to cancel)",
        border_style=GOLD, title="Pair"))
    try:
        for _ in range(90):
            time.sleep(2)
            if config.load()["owner_id"]:
                ok(f"Linked! Owner ID = {config.load()['owner_id']}")
                return
    except KeyboardInterrupt:
        pass
    bad("Timed out. Try again (the service must be running and the token valid).")


def act_login():
    console.print()
    info("Optional: lets the agent message OTHER people from your own Telegram account.")
    info("You need API ID + API hash from https://my.telegram.org -> API development tools.")
    cfg = config.load()
    if cfg["api_id"] and cfg["api_hash"]:
        if not Confirm.ask("API ID/hash already saved. Replace?", default=False):
            api_id, api_hash = int(cfg["api_id"]), cfg["api_hash"]
        else:
            api_id = int(Prompt.ask("API ID"))
            api_hash = getpass.getpass("API hash (hidden): ").strip()
    else:
        api_id = int(Prompt.ask("API ID"))
        api_hash = getpass.getpass("API hash (hidden): ").strip()
    config.update(api_id=api_id, api_hash=api_hash)
    was = service_active()
    if was:
        svc("stop")
    try:
        tguser.login_cli(api_id, api_hash)
        ok("Account logged in")
    except Exception as e:
        bad(f"Login failed: {e}")
    if was:
        svc("start")


def server_ip() -> str:
    try:
        return httpx.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception:
        return "YOUR_SERVER_IP"


def install_caddy() -> bool:
    if shutil.which("caddy"):
        return True
    info("Installing Caddy (it gives your domain automatic HTTPS). Please wait…")
    cmd = (
        "set -e; apt-get update -qq; "
        "apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gpg; "
        "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg; "
        "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' -o /etc/apt/sources.list.d/caddy-stable.list; "
        "chmod o+r /usr/share/keyrings/caddy-stable-archive-keyring.gpg /etc/apt/sources.list.d/caddy-stable.list; "
        "apt-get update -qq; apt-get install -y -qq caddy"
    )
    subprocess.run(["bash", "-c", cmd])
    return shutil.which("caddy") is not None


def open_ports(*ports):
    if shutil.which("ufw") and "active" in sh(["ufw", "status"]).stdout.split("\n")[0].lower():
        for p in ports:
            sh(["ufw", "allow", f"{p}/tcp"])


def set_domain():
    cfg = config.load()
    if not cfg["web_password_hash"]:
        bad("Set a panel password first (option 1).")
        return
    ip = server_ip()
    console.print(Panel(
        f"1) Own a domain (e.g. panel.example.com)\n"
        f"2) In the domain's DNS settings add an [bold]A record[/] pointing to this server:  [bold {GOLD2}]{ip}[/]\n"
        f"3) If you use Cloudflare: set the cloud to [bold]grey (DNS only)[/]\n"
        f"4) Wait 1-5 minutes, then enter the domain below",
        border_style=GOLD, title="Domain setup"))
    dom = Prompt.ask("Your domain (Enter = cancel)", default="", show_default=False).strip().lower()
    dom = dom.replace("https://", "").replace("http://", "").strip("/")
    if not dom:
        return
    try:
        resolved = socket.gethostbyname(dom)
    except Exception:
        resolved = None
    if resolved != ip:
        bad(f"The domain points to {resolved or 'nothing'}, but this server is {ip}.")
        info("HTTPS will fail until the A record is correct.")
        if not Confirm.ask("Continue anyway?", default=False):
            return
    if not install_caddy():
        bad("Could not install Caddy. Check the server internet and try again.")
        return
    cf = Path("/etc/caddy/Caddyfile")
    bak = Path("/etc/caddy/Caddyfile.dabe-backup")
    if cf.exists() and not bak.exists():
        shutil.copy(cf, bak)
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(f"{dom} {{\n    encode gzip\n    reverse_proxy 127.0.0.1:{cfg['web_port']}\n}}\n")
    open_ports(80, 443)
    config.update(domain=dom, web_host="127.0.0.1")
    sh(["systemctl", "enable", "--now", "caddy"])
    sh(["systemctl", "restart", "caddy"])
    if service_active():
        svc("restart")
    console.print(Panel(f"[bold {GOLD2}]https://{dom}[/]\n\nOpen it in your browser (the HTTPS certificate can take ~1 minute the first time).\nLog in with your panel password.",
                        border_style=GOLD, title="Your panel"))


def set_direct():
    cfg = config.load()
    if not cfg["web_password_hash"]:
        bad("Set a panel password first (option 1).")
        return
    bad("Warning: plain HTTP is NOT encrypted. Your password can be sniffed. Use a domain if you can.")
    if not Confirm.ask("Open the panel on the server IP anyway?", default=False):
        return
    config.update(web_host="0.0.0.0", domain="")
    open_ports(cfg["web_port"])
    svc("restart")
    console.print(Panel(f"[bold {GOLD2}]http://{server_ip()}:{cfg['web_port']}[/]", border_style=GOLD, title="Your panel"))


def set_private():
    cfg = config.load()
    config.update(web_host="127.0.0.1", domain="")
    if shutil.which("caddy"):
        sh(["systemctl", "disable", "--now", "caddy"])
    svc("restart")
    ok("Panel is private again (only reachable from the server itself).")
    console.print(f"Use an SSH tunnel:  [bold {GOLD2}]ssh -L {cfg['web_port']}:127.0.0.1:{cfg['web_port']} root@{server_ip()}[/]  then open http://localhost:{cfg['web_port']}")


def act_web():
    cfg = config.load()
    console.print()
    console.print(f"[{GOLD}][1][/] Set / change panel password")
    console.print(f"[{GOLD}][2][/] Connect my DOMAIN with automatic HTTPS  [{OK}](recommended)[/]")
    console.print(f"[{GOLD}][3][/] Open on server IP:port (HTTP, not encrypted)")
    console.print(f"[{GOLD}][4][/] Make panel private again (localhost only)")
    console.print(f"[{GOLD}][5][/] Show my panel address")
    c = Prompt.ask("Choose", choices=["1", "2", "3", "4", "5", "0"], default="0")
    if c == "1":
        act_web_pw()
    elif c == "2":
        set_domain()
    elif c == "3":
        set_direct()
    elif c == "4":
        set_private()
    elif c == "5":
        if cfg.get("domain"):
            console.print(f"[bold {GOLD2}]https://{cfg['domain']}[/]")
        elif cfg["web_host"] == "0.0.0.0":
            console.print(f"[bold {GOLD2}]http://{server_ip()}:{cfg['web_port']}[/]")
        else:
            console.print("Private (localhost only). Choose option 2 to connect a domain.")


def act_settings():
    cfg = config.load()
    console.print()
    console.print(f"[{GOLD}][1][/] Language (fa/en)   [{GOLD}][2][/] Timezone   [{GOLD}][3][/] Approval mode   [{GOLD}][4][/] Model name")
    c = Prompt.ask("Choose", choices=["1", "2", "3", "4", "0"], default="0")
    if c == "1":
        config.update(lang=Prompt.ask("Language", choices=["fa", "en"], default=cfg["lang"]))
    elif c == "2":
        v = Prompt.ask("Timezone (e.g. Asia/Tehran, Europe/London)", default=cfg["timezone"])
        try:
            ZoneInfo(v)
            config.update(timezone=v)
            ok("Saved")
        except Exception:
            bad("Unknown timezone")
    elif c == "3":
        console.print("all = ask for EVERYTHING (safest) | dangerous = only shell/files/send | none = never ask")
        config.update(approval=Prompt.ask("Mode", choices=["all", "dangerous", "none"], default=cfg["approval"]))
    elif c == "4":
        p = cfg["provider"]
        v = Prompt.ask(f"Model for {p}", default=cfg["models"].get(p) or config.PROVIDERS[p]["model"])
        cfg["models"][p] = v
        config.save(cfg)
        ok("Saved")


def act_start():
    cfg = config.load()
    if not cfg["keys"].get(cfg["provider"]):
        bad("Set an AI API key first ([1]).")
        return
    r = svc("restart")
    time.sleep(3)
    ok("Service running") if service_active() else bad("Service failed. See logs ([9]). " + r.stderr[:200])


def act_stop():
    svc("stop")
    ok("Stopped")


def act_logs():
    info("Ctrl+C to go back")
    try:
        subprocess.run(["journalctl", "-u", SERVICE, "-f", "-n", "40", "--output", "cat"])
    except KeyboardInterrupt:
        pass


def act_update():
    base = config.BASE
    info("Pulling latest version…")
    r = subprocess.run(["git", "-C", str(base), "pull", "--ff-only"])
    if r.returncode != 0:
        bad("git pull failed")
        return
    subprocess.run([str(base / "venv/bin/pip"), "install", "-q", "-U", "-r", str(base / "requirements.txt")])
    svc("restart")
    ok("Updated and restarted")


def act_uninstall():
    if Prompt.ask("Type DELETE to remove Ego/Dabe from this server") != "DELETE":
        return
    keep = Confirm.ask("Keep your data (keys, settings)?", default=False)
    sh(["systemctl", "disable", "--now", SERVICE])
    sh(["systemctl", "stop", "dabe-tunnel"])
    for f in ("/etc/systemd/system/dabe.service", "/usr/local/bin/dabe"):
        Path(f).unlink(missing_ok=True)
    sh(["systemctl", "daemon-reload"])
    if keep:
        shutil.move(str(config.DATA), "/root/dabe-data-backup")
        ok("Data moved to /root/dabe-data-backup")
    shutil.rmtree(config.BASE, ignore_errors=True)
    ok("Uninstalled. Goodbye!")
    sys.exit(0)


def step(n, total, title, hint=""):
    console.print()
    console.rule(f"[bold {GOLD2}]✦ Step {n}/{total}: {title} ✦[/]", style=DIM)
    if hint:
        info(hint)


def secret(prompt):
    console.print(f"[{DIM}](what you type/paste is hidden - that is normal. Paste, then press Enter)[/]")
    return getpass.getpass(prompt).strip()


def do_login(api_id, api_hash):
    was = service_active()
    if was:
        svc("stop")
    try:
        tguser.login_cli(int(api_id), api_hash)
        ok("Telegram account logged in")
    except Exception as e:
        bad(f"Login failed: {e}")
    if was:
        svc("start")


def act_quick():
    total = 10
    info("Quick Setup. Paste the value for each step and press Enter.")
    info("Optional steps: just press Enter to skip them.")

    step(1, total, "Gemini API key  (the brain - FREE)",
         "Open  aistudio.google.com/apikey  -> Create API key -> copy it")
    k = secret("Gemini key: ")
    if k:
        config.set_key("gemini", k)
        config.update(provider="gemini")
        test_key("gemini", k)

    step(2, total, "Groq API key  (understands your VOICE - FREE, optional)",
         "Open  console.groq.com/keys  -> Create API Key -> copy it")
    k = secret("Groq key (Enter = skip): ")
    if k:
        config.set_key("groq", k)
        ok("Groq key saved")

    step(3, total, "OpenRouter API key  (backup brain - optional)",
         "Open  openrouter.ai/keys  -> Create key. Used automatically if Gemini hits its free limit.")
    k = secret("OpenRouter key (Enter = skip): ")
    if k:
        config.set_key("openrouter", k)
        ok("OpenRouter key saved")

    step(4, total, "Telegram bot token  (required)",
         "In Telegram open @BotFather -> /newbot -> copy the token it gives you")
    tok = secret("Bot token: ")
    if tok:
        config.update(bot_token=tok)
        name = bot_username(tok)
        ok(f"Bot found: {name}") if name != "?" else bad("Telegram did not accept this token. Check it later with menu [2].")

    step(5, total, "Telegram API ID + hash  (optional)",
         "Only needed if you want the agent to message OTHER people from your own account.\n"
         "Open  my.telegram.org -> API development tools -> create app -> copy api_id and api_hash")
    api_id = Prompt.ask("API ID (Enter = skip)", default="", show_default=False).strip()
    if api_id.isdigit():
        h = secret("API hash: ")
        if h:
            config.update(api_id=int(api_id), api_hash=h)
            if Confirm.ask("Log in to your Telegram account now (phone + code)?", default=True):
                do_login(api_id, h)

    step(6, total, "Web panel password", "Used to log in to the web panel. At least 8 characters.")
    act_web_pw()

    step(7, total, "Your domain for the web panel  (optional)",
         "Press Enter to skip. You can do it later from menu [5].")
    if Confirm.ask("Connect a domain now?", default=False):
        set_domain()

    step(8, total, "Timezone", "Press Enter to keep the default.")
    act_tz()

    step(9, total, "Start the agent")
    act_start()

    step(10, total, "Link YOUR Telegram account as the owner",
         "Only the owner can give orders to the bot.")
    act_pair()

    console.print()
    ok("All done! Open your bot in Telegram and say hi.")


def act_web_pw():
    pw = getpass.getpass("Web panel password (min 8 chars, hidden): ")
    if len(pw) >= 8 and pw == getpass.getpass("Repeat: "):
        config.update(web_password_hash=config.hash_password(pw))
        ok("Saved. Use menu [5] later to open the panel.")
    else:
        bad("Skipped (too short / mismatch). You can do it later from [5].")


def act_tz():
    v = Prompt.ask("Timezone", default=config.load()["timezone"])
    try:
        ZoneInfo(v)
        config.update(timezone=v)
    except Exception:
        bad("Unknown timezone, kept the old one.")


ACTIONS = {"S": act_quick, "1": act_ai, "2": act_bot, "3": act_pair, "4": act_login, "5": act_web,
           "6": act_settings, "7": act_start, "8": act_stop, "9": act_logs, "D": lambda: doctor.run(console), "U": act_update, "X": act_uninstall}


def main():
    if os.geteuid() != 0:
        console.print("Please run as root:  sudo dabe")
        sys.exit(1)
    while True:
        screen()
        console.print()
        try:
            c = Prompt.ask(f"[bold {GOLD2}]{t('choose')}[/]").strip().upper()
        except (KeyboardInterrupt, EOFError):
            break
        if c in ("0", "Q", "EXIT"):
            break
        fn = ACTIONS.get(c)
        if not fn:
            continue
        try:
            fn()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            bad(f"{type(e).__name__}: {e}")
        if c not in ("9",):
            pause()
    console.print(f"[{GOLD}]✦ Bye ✦[/]")


if __name__ == "__main__":
    main()
