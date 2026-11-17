"""Media toolbox: yt-dlp downloads, ffmpeg helpers, video understanding, image generation, song id."""
import asyncio
import html as htmllib
import logging
import re
import shutil
import time
import urllib.parse
import uuid
from pathlib import Path

import httpx

import config
import llm
import voice

log = logging.getLogger("dabe.media")
MEDIA = config.DATA / "media"
MEDIA.mkdir(parents=True, exist_ok=True)

UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v"}
AUDIO_EXT = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aac", ".flac"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
SITES = (
    "instagram.com", "instagr.am", "youtube.com", "youtu.be", "tiktok.com", "pinterest.", "pin.it",
    "twitter.com", "x.com", "facebook.com", "fb.watch", "vimeo.com", "soundcloud.com", "reddit.com",
    "twitch.tv", "snapchat.com", "threads.net", "dailymotion.com", "bilibili.com", "likee.video",
)
URL_RE = re.compile(r"https?://[^\s<>\"']+")


class MediaError(Exception):
    """Error with a message that is safe/helpful to show to the user."""


def first_url(text: str):
    m = URL_RE.search(text or "")
    return m.group(0).rstrip(".,)!؟،") if m else None


def is_media_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(s in host for s in SITES)


def kind_of(path) -> str:
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in IMAGE_EXT:
        return "image"
    return "file"


async def sh(*cmd, timeout: int = 300):
    p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return 124, "timeout"
    return p.returncode, out.decode(errors="replace")


def workdir() -> Path:
    # delete old work folders (older than 6h) so the disk never fills up
    now = time.time()
    for d in MEDIA.iterdir():
        try:
            if now - d.stat().st_mtime > 6 * 3600:
                shutil.rmtree(d, ignore_errors=True) if d.is_dir() else d.unlink()
        except Exception:
            pass
    d = MEDIA / uuid.uuid4().hex[:10]
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------- ffmpeg helpers
async def duration(path) -> float:
    rc, out = await sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path), timeout=60)
    try:
        return float(out.strip().splitlines()[0])
    except Exception:
        return 0.0


async def extract_audio(video, quality: int = 2) -> Path:
    out = Path(video).with_suffix(".mp3")
    if out.exists():
        out = out.with_name(out.stem + "_a.mp3")
    rc, log_ = await sh("ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vn", "-c:a", "libmp3lame", "-q:a", str(quality), str(out))
    if rc != 0 or not out.exists():
        raise MediaError("This video has no audio track (or ffmpeg failed).")
    return out


async def extract_frames(video, n: int = 8) -> list[bytes]:
    d = Path(video).parent / ("frames_" + uuid.uuid4().hex[:6])
    d.mkdir(exist_ok=True)
    dur = await duration(video)
    fps = n / max(dur, 1.0)
    await sh("ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", f"fps={fps:.5f},scale=768:-2",
             "-frames:v", str(n), "-q:v", "4", str(d / "f_%02d.jpg"), timeout=180)
    frames = [p.read_bytes() for p in sorted(d.glob("f_*.jpg"))]
    if not frames:  # very short clip: grab the first frame
        await sh("ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-frames:v", "1", "-vf", "scale=768:-2", str(d / "f_01.jpg"), timeout=60)
        frames = [p.read_bytes() for p in sorted(d.glob("f_*.jpg"))]
    return frames


async def fit_for_telegram(path, kind: str | None = None) -> Path:
    """Telegram bots can upload max ~50MB. Compress videos that are bigger."""
    path = Path(path)
    kind = kind or kind_of(path)
    limit = int(config.load().get("max_upload_mb", 48)) * 1024 * 1024
    if path.stat().st_size <= limit:
        return path
    if kind != "video":
        raise MediaError("File is too big for Telegram (max ~50MB).")
    dur = max(await duration(path), 1.0)
    bitrate = max(int(limit * 8 * 0.90 / dur) - 96_000, 120_000)
    out = path.with_name(path.stem + "_small.mp4")
    rc, log_ = await sh(
        "ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-c:v", "libx264", "-preset", "veryfast",
        "-b:v", str(bitrate), "-maxrate", str(bitrate), "-bufsize", str(bitrate * 2),
        "-vf", "scale=-2:'min(720,ih)'", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(out), timeout=900)
    if rc != 0 or not out.exists() or out.stat().st_size > limit * 1.05:
        raise MediaError("The video is too long/big to send on Telegram (even compressed).")
    return out


# ---------------------------------------------------------------- downloading
def _newest(outdir: Path):
    files = [p for p in outdir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXT | AUDIO_EXT | IMAGE_EXT]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _ydl_sync(url: str, outdir: Path, audio_only: bool) -> dict:
    import yt_dlp

    opts = {
        "outtmpl": str(outdir / "%(id)s.%(ext)s"),
        "noplaylist": True, "quiet": True, "no_warnings": True, "retries": 3,
        "socket_timeout": 30, "merge_output_format": "mp4",
        "format": "bv*[ext=mp4][height<=720]+ba[ext=m4a]/b[ext=mp4][height<=720]/bv*[height<=720]+ba/b[height<=720]/b",
    }
    if config.COOKIES.exists():
        opts["cookiefile"] = str(config.COOKIES)
    if audio_only:
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}]
    with yt_dlp.YoutubeDL(opts) as y:
        info = y.extract_info(url, download=False)
        if info.get("entries"):
            info = list(info["entries"])[0]
        if (info.get("duration") or 0) > 3600 or info.get("is_live"):
            raise MediaError("This video is live or longer than 1 hour. I only download up to 1 hour.")
        res = y.process_ie_result(info, download=True)
    rd = res.get("requested_downloads") or []
    path = (rd[0].get("filepath") if rd else None) or res.get("filepath")
    if not path or not Path(path).exists():
        path = _newest(outdir)
    if not path:
        raise MediaError("Download finished but no file was found.")
    artist = res.get("artist") or (", ".join(res["artists"]) if res.get("artists") else None)
    return {
        "path": Path(path), "kind": kind_of(path), "title": res.get("title") or "", "uploader": res.get("uploader") or res.get("channel") or "",
        "duration": res.get("duration"), "track": res.get("track"), "artist": artist, "url": url,
    }


def _meta(page: str, prop: str):
    for pat in (rf'<meta[^>]+(?:property|name)=["\']{prop}["\'][^>]+content=["\']([^"\']+)',
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\']{prop}["\']'):
        m = re.search(pat, page, re.I)
        if m:
            return htmllib.unescape(m.group(1))
    return None


async def _og_fallback(url: str, outdir: Path) -> dict:
    """For Pinterest/other pages yt-dlp can't handle: read the page's own og:video / og:image."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": UA}) as c:
        page = (await c.get(url)).text
        src = _meta(page, "og:video:url") or _meta(page, "og:video") or _meta(page, "og:image")
        if not src:
            raise MediaError("Could not find any video/image on that page.")
        r = await c.get(src)
        r.raise_for_status()
    ext = Path(urllib.parse.urlparse(src).path).suffix.lower() or ".jpg"
    if ext not in VIDEO_EXT | IMAGE_EXT:
        ext = ".mp4" if "video" in r.headers.get("content-type", "") else ".jpg"
    path = outdir / f"page{ext}"
    path.write_bytes(r.content)
    return {"path": path, "kind": kind_of(path), "title": _meta(page, "og:title") or "",
            "uploader": "", "duration": None, "track": None, "artist": None, "url": url}


async def download(url: str, audio_only: bool = False) -> dict:
    outdir = workdir()
    try:
        return await asyncio.to_thread(_ydl_sync, url, outdir, audio_only)
    except MediaError:
        raise
    except Exception as e:
        msg = str(e)
        log.warning("yt-dlp failed for %s: %s", url, msg[:300])
        if not audio_only:
            try:
                return await _og_fallback(url, outdir)
            except Exception as e2:
                log.warning("og fallback failed: %s", e2)
        low = msg.lower()
        if any(k in low for k in ("login", "cookie", "private", "rate-limit", "rate limit", "sign in", "age")):
            raise MediaError("This site wants a login (common with Instagram/YouTube). Send me a cookies.txt file from your browser "
                             "(see /cookies) and try again.")
        if "unsupported url" in low:
            raise MediaError("I can't download from that link.")
        raise MediaError("Download failed: " + msg.replace("ERROR: ", "")[:200])


# ---------------------------------------------------------------- understanding
async def analyze_video(path, question: str = "") -> str:
    frames = await extract_frames(path, 8)
    if not frames:
        raise MediaError("Could not read frames from this video.")
    transcript = ""
    try:
        a = Path(path).with_name("stt_" + uuid.uuid4().hex[:5] + ".mp3")
        rc, _ = await sh("ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", str(a), timeout=180)
        if rc == 0 and a.exists() and a.stat().st_size > 2000:
            transcript = await voice.transcribe(str(a))
    except Exception as e:
        log.info("no transcript: %s", e)
    q = question.strip() or "Describe this video: what happens, who/what is shown, any text on screen, and the overall mood."
    prompt = (f"These {len(frames)} images are evenly spaced frames of ONE video, in order.\n"
              + (f"Speech transcript of its audio: {transcript}\n" if transcript else "The audio has no detectable speech.\n")
              + f"Task: {q}\nAnswer in the user's language.")
    return await llm.describe(frames, prompt)


async def identify_song(path) -> str | None:
    """Shazam-style recognition (optional package `shazamio`). Returns 'Title — Artist' or None."""
    try:
        from shazamio import Shazam
    except ImportError:
        return None
    try:
        sample = Path(path).with_name("sample_" + uuid.uuid4().hex[:5] + ".mp3")
        rc, _ = await sh("ffmpeg", "-y", "-loglevel", "error", "-i", str(path), "-vn", "-t", "25", "-ac", "1", "-ar", "44100", str(sample), timeout=60)
        if rc != 0:
            return None
        sz = Shazam()
        fn = getattr(sz, "recognize", None) or getattr(sz, "recognize_song")
        out = await asyncio.wait_for(fn(str(sample)), 45)
        tr = (out or {}).get("track")
        return f"{tr['title']} — {tr['subtitle']}" if tr else None
    except Exception as e:
        log.info("song id failed: %s", e)
        return None


# ---------------------------------------------------------------- image generation
async def generate_image(prompt: str, width: int = 1024, height: int = 1024) -> Path:
    cfg = config.load()
    errors = []
    out_base = MEDIA / ("img_" + uuid.uuid4().hex[:8])
    width, height = max(256, min(int(width), 1536)), max(256, min(int(height), 1536))

    # 1) Pollinations: free, no key
    try:
        q = urllib.parse.quote(prompt[:900])
        seed = uuid.uuid4().int % 100000
        url = f"https://image.pollinations.ai/prompt/{q}?width={width}&height={height}&nologo=true&model=flux&seed={seed}"
        async with httpx.AsyncClient(timeout=120, follow_redirects=True, headers={"User-Agent": UA}) as c:
            r = await c.get(url)
        if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
            p = out_base.with_suffix(".png" if "png" in r.headers["content-type"] else ".jpg")
            p.write_bytes(r.content)
            return p
        errors.append(f"pollinations HTTP {r.status_code}")
    except Exception as e:
        errors.append(f"pollinations {type(e).__name__}")

    # 2) Gemini image model (uses your Gemini key; free quota may be 0 in some accounts)
    key = cfg["keys"].get("gemini")
    if key:
        try:
            model = cfg.get("gemini_image_model", "gemini-2.5-flash-image")
            r = await llm.client().post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key},
                json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["IMAGE"]}})
            if r.status_code == 200:
                import base64
                for part in r.json()["candidates"][0]["content"]["parts"]:
                    inline = part.get("inlineData") or part.get("inline_data")
                    if inline:
                        p = out_base.with_suffix(".png")
                        p.write_bytes(base64.b64decode(inline["data"]))
                        return p
            errors.append(f"gemini HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"gemini {type(e).__name__}")
    raise MediaError("Image generation failed (" + "; ".join(errors) + "). Try again in a minute.")
