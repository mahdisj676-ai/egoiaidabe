"""Config storage for Ego/Dabe (single JSON file, chmod 600)."""
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

VERSION = "2.0.0"
BASE = Path(os.environ.get("DABE_HOME", Path(__file__).resolve().parent))
DATA = BASE / "data"
DATA.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA / "config.json"
COOKIES = DATA / "cookies.txt"   # optional: Instagram/YouTube login cookies (send cookies.txt to the bot)
INBOX = DATA / "inbox"
INBOX.mkdir(parents=True, exist_ok=True)

# All providers speak the OpenAI-compatible "chat/completions" protocol.
PROVIDERS = {
    "gemini": {
        "label": "Google Gemini (free tier)",
        "base": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-flash-latest",
        "url": "aistudio.google.com/apikey",
    },
    "groq": {
        "label": "Groq (free tier, fast + voice input)",
        "base": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "url": "console.groq.com/keys",
    },
    "openrouter": {
        "label": "OpenRouter (has free models)",
        "base": "https://openrouter.ai/api/v1",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "url": "openrouter.ai/keys",
    },
    "openai": {
        "label": "OpenAI (paid)",
        "base": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "url": "platform.openai.com/api-keys",
    },
    "anthropic": {
        "label": "Claude / Anthropic (paid)",
        "base": "https://api.anthropic.com/v1",
        "model": "claude-sonnet-5-5",
        "url": "console.anthropic.com",
    },
}

DEFAULTS = {
    "provider": "gemini",
    "models": {},
    "keys": {},
    "bot_token": "",
    "owner_id": 0,
    "pair_code": "",
    "api_id": 0,
    "api_hash": "",
    "timezone": "Asia/Tehran",
    "lang": "fa",
    "approval": "all",  # all | dangerous | none
    "domain": "",
    "web_host": "127.0.0.1",
    "web_port": 8787,
    "web_password_hash": "",
    "web_secret": "",
    "voice_fa": "fa-IR-DilaraNeural",
    "voice_en": "en-US-AriaNeural",
    "gemini_image_model": "gemini-2.5-flash-image",
    "max_upload_mb": 48,
}


def load() -> dict:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text()))
        except Exception:
            pass
    return cfg


def save(cfg: dict) -> None:
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG_FILE)


def update(**kw) -> dict:
    cfg = load()
    cfg.update(kw)
    save(cfg)
    return cfg


def set_key(provider: str, value: str) -> None:
    cfg = load()
    cfg["keys"][provider] = value.strip()
    save(cfg)


# ---- passwords & session tokens -------------------------------------------
def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.scrypt(pw.encode(), salt=salt.encode(), n=2**14, r=8, p=1).hex()
    return f"{salt}${h}"


def check_password(pw: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$")
        c = hashlib.scrypt(pw.encode(), salt=salt.encode(), n=2**14, r=8, p=1).hex()
        return hmac.compare_digest(c, h)
    except Exception:
        return False


def web_secret() -> str:
    cfg = load()
    if not cfg["web_secret"]:
        cfg["web_secret"] = secrets.token_hex(32)
        save(cfg)
    return cfg["web_secret"]


def make_token() -> str:
    ts = str(int(time.time()))
    sig = hmac.new(web_secret().encode(), ts.encode(), "sha256").hexdigest()
    return f"{ts}.{sig}"


def verify_token(tok: str, max_age: int = 7 * 86400) -> bool:
    try:
        ts, sig = tok.split(".")
        good = hmac.new(web_secret().encode(), ts.encode(), "sha256").hexdigest()
        return hmac.compare_digest(sig, good) and time.time() - int(ts) < max_age
    except Exception:
        return False
