"""Voice in (Groq Whisper) and voice out (Edge-TTS)."""
import asyncio
import os
import re
import uuid
from pathlib import Path

import httpx

import config

TMP = config.DATA / "tmp"
TMP.mkdir(parents=True, exist_ok=True)


async def transcribe(path: str) -> str:
    key = config.load()["keys"].get("groq")
    if not key:
        raise RuntimeError("Voice input needs a free Groq key. Run `dabe` -> [1] -> add Groq key.")
    with open(path, "rb") as f:
        raw = f.read()
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            data={"model": "whisper-large-v3-turbo"},
            files={"file": (os.path.basename(path), raw, "audio/ogg")},
        )
    r.raise_for_status()
    return r.json().get("text", "").strip()


async def synth(text: str, fmt: str = "ogg") -> Path:
    """Text -> speech file. ogg(opus) for Telegram voice notes, mp3 for browsers."""
    import edge_tts

    cfg = config.load()
    voice = cfg["voice_fa"] if re.search(r"[\u0600-\u06FF]", text) else cfg["voice_en"]
    base = TMP / uuid.uuid4().hex
    mp3 = base.with_suffix(".mp3")
    await edge_tts.Communicate(text[:3000], voice).save(str(mp3))
    if fmt == "mp3":
        return mp3
    ogg = base.with_suffix(".ogg")
    p = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-c:a", "libopus", "-b:a", "48k", str(ogg)
    )
    await p.wait()
    mp3.unlink(missing_ok=True)
    if p.returncode != 0 or not ogg.exists():
        raise RuntimeError("ffmpeg failed to convert audio")
    return ogg
