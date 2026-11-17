"""Link flow: user sends a reel/video link -> bot returns the video AND its music (mp3)."""
import asyncio
import logging
import re

import media

log = logging.getLogger("dabe.flow")
AUDIO_WORDS = re.compile(r"(mp3|audio|music|song|sound|آهنگ|اهنگ|موزیک|صدا)", re.I)
VIDEO_WORDS = re.compile(r"(video|ویدیو|ویدئو|فیلم|clip|کلیپ)", re.I)
ONLY_WORDS = re.compile(r"(فقط|only|just)", re.I)


def parse_link_request(text: str):
    """Return (url, what) if the message is basically just a media link, else None (let the AI decide)."""
    url = media.first_url(text)
    if not url or not media.is_media_url(url):
        return None
    rest = text.replace(url, "").strip()
    if len(rest) > 30 or "?" in rest or "؟" in rest:
        return None
    a, v = bool(AUDIO_WORDS.search(rest)), bool(VIDEO_WORDS.search(rest))
    what = "both"
    if a and not v:
        what = "audio"
    elif v and not a and ONLY_WORDS.search(rest):
        what = "video"
    return url, what


def _caption(res: dict) -> str:
    parts = []
    if res.get("title"):
        parts.append("🎬 " + res["title"])
    if res.get("uploader"):
        parts.append("👤 " + res["uploader"])
    return "\n".join(parts)[:1000]


async def deliver_link(ctx, url: str, what: str = "both") -> str:
    """Download and send. what = video | audio | both. Returns a short summary for the AI."""
    sent = []
    if what == "audio":
        a = await media.download(url, audio_only=True)
        path = await media.fit_for_telegram(a["path"], "audio")
        await ctx.send_audio(path, caption="", title=a.get("track") or a.get("title"), performer=a.get("artist") or a.get("uploader"))
        return f"sent audio: {a.get('title')}"

    v = await media.download(url)
    if v["kind"] == "image":
        await ctx.send_photo(v["path"], _caption(v))
        return "sent image"
    if v["kind"] == "audio":
        await ctx.send_audio(v["path"], caption="", title=v.get("title"), performer=v.get("uploader"))
        return "sent audio"

    mp3 = None
    song_task = None
    if what == "both":
        try:
            mp3 = await media.extract_audio(v["path"])
            if not v.get("track"):
                song_task = asyncio.create_task(media.identify_song(mp3))
        except media.MediaError as e:
            log.info("no audio: %s", e)

    small = await media.fit_for_telegram(v["path"], "video")
    await ctx.send_video(small, _caption(v))
    sent.append("video")

    if mp3:
        title = v.get("track") or v.get("title")
        performer = v.get("artist") or v.get("uploader")
        await ctx.send_audio(await media.fit_for_telegram(mp3, "audio"), caption="", title=title, performer=performer)
        sent.append("audio mp3")
    if song_task:
        try:
            song = await asyncio.wait_for(song_task, 50)
            if song:
                await ctx.notify(f"🎵 {song}")
                sent.append(f"song: {song}")
        except Exception:
            pass
    elif v.get("track") and what == "both":
        await ctx.notify("🎵 " + v["track"] + (f" — {v['artist']}" if v.get("artist") else ""))
    return "sent " + " + ".join(sent) + f" ({v.get('title') or url})"
