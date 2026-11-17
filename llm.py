"""One OpenAI-compatible client for every provider: fallback, speed (shared connection) and vision."""
import asyncio
import base64
import logging

import httpx

import config

log = logging.getLogger("dabe.llm")
ORDER = ["gemini", "groq", "openrouter", "openai", "anthropic"]
VISION_OK = ("gemini", "openai", "anthropic")
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15))
    return _client


def model_for(cfg: dict, provider: str) -> str:
    return cfg["models"].get(provider) or config.PROVIDERS[provider]["model"]


def sniff_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:3] == b"GIF":
        return "image/gif"
    return "image/jpeg"


def img_part(data: bytes, mime: str | None = None) -> dict:
    mime = mime or sniff_mime(data)
    b64 = base64.b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def has_images(messages: list) -> bool:
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
            return True
    return False


async def chat(messages: list, tools: list | None = None):
    cfg = config.load()
    first = cfg["provider"]
    order = [first] + [p for p in ORDER if p != first]
    cands = [p for p in order if cfg["keys"].get(p)]
    if not cands:
        raise RuntimeError("No AI API key is set. Run `dabe` in the server terminal and choose [S] or [1].")
    if has_images(messages):
        cands = [p for p in cands if p in VISION_OK]
        if not cands:
            raise RuntimeError("Seeing images/videos needs a Gemini key (free). Run `dabe` -> [1].")
    last = ""
    for p in cands:
        info = config.PROVIDERS[p]
        body = {"model": model_for(cfg, p), "messages": messages}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {cfg['keys'][p]}"}
        for attempt in range(2):
            try:
                r = await client().post(info["base"] + "/chat/completions", json=body, headers=headers)
                if r.status_code == 429 or r.status_code >= 500:
                    last = f"{p}: HTTP {r.status_code}"
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                if r.status_code >= 400:
                    last = f"{p}: HTTP {r.status_code} {r.text[:300]}"
                    log.warning("LLM error %s", last)
                    break
                return r.json()["choices"][0]["message"], p
            except Exception as e:
                last = f"{p}: {type(e).__name__} {str(e)[:200]}"
                log.warning("LLM error %s", last)
                break
    raise RuntimeError(f"All AI providers failed ({last})")


async def describe(images: list[bytes], question: str) -> str:
    """Ask a vision model about one or more JPEG images and get plain text back."""
    content = [{"type": "text", "text": question}] + [img_part(b) for b in images]
    msg, _ = await chat([{"role": "user", "content": content}])
    return (msg.get("content") or "").strip()
