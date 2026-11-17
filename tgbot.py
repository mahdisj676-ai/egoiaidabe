"""Telegram bot channel: text, photos, videos, voice, files, links, approval buttons."""
import asyncio
import json
import logging
import os
import uuid
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

import config
import flow
import media
import voice
from agent import Agent, Ctx

log = logging.getLogger("dabe.tg")
PENDING: dict[str, asyncio.Future] = {}
UP = dict(read_timeout=300, write_timeout=300, connect_timeout=30, pool_timeout=30)
MB = 1024 * 1024

HELP = (
    "🤵 من Dabe هستم؛ هر کاری بخوای بگو (متن یا ویس):\n\n"
    "🔗 لینک ریلز/یوتیوب/تیک‌تاک/پینترست بده → ویدیو + آهنگش (mp3)\n"
    "🖼 عکس بفرست → می‌بینم و توضیح می‌دم\n"
    "🎬 ویدیو بفرست → تماشا می‌کنم و می‌گم چیه\n"
    "🎨 «یه عکس از … بکش» → می‌سازم\n"
    "💻 دستور سرور، فایل، جستجوی اینترنت، یادآوری، ویس…\n\n"
    "/clear پاک کردن حافظه گفتگو · /status وضعیت · /cookies راهنمای اینستاگرام"
)
COOKIES_HELP = (
    "🍪 بعضی لینک‌ها (اینستاگرام، گاهی یوتیوب) بدون لاگین دانلود نمی‌شن.\n"
    "1) با مرورگر کامپیوتر (یا Kiwi/Firefox موبایل) وارد اکانت بشید\n"
    "2) افزونه «Get cookies.txt LOCALLY» رو نصب و فایل cookies.txt رو بگیرید\n"
    "3) همون فایل رو همین‌جا برای من بفرستید.\n\n"
    "⚠️ بهتره از یه اکانت فرعی استفاده کنید، نه اکانت اصلی."
)


def pretty(args: dict) -> str:
    try:
        return "\n".join(f"• {k}: {v}" for k, v in args.items())[:3000]
    except Exception:
        return json.dumps(args)[:3000]


class Busy:
    """Keeps the 'typing…' / 'uploading…' indicator alive while we work."""

    def __init__(self, bot, chat_id, action="typing"):
        self.bot, self.chat_id, self.action, self.task = bot, chat_id, action, None

    async def _loop(self):
        while True:
            try:
                await self.bot.send_chat_action(self.chat_id, self.action)
            except Exception:
                pass
            await asyncio.sleep(4)

    async def __aenter__(self):
        self.task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *a):
        self.task.cancel()


class TgCtx(Ctx):
    def __init__(self, bot, chat_id):
        self.bot, self.chat_id = bot, chat_id

    async def approve(self, name, args):
        aid = uuid.uuid4().hex[:8]
        fut = asyncio.get_running_loop().create_future()
        PENDING[aid] = fut
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ تایید / Approve", callback_data=f"ok:{aid}"),
            InlineKeyboardButton("❌ رد / Deny", callback_data=f"no:{aid}"),
        ]])
        await self.bot.send_message(self.chat_id, f"🔐 درخواست تایید / Approval\n\n🛠 {name}\n{pretty(args)}", reply_markup=kb)
        try:
            return await asyncio.wait_for(fut, 300)
        except asyncio.TimeoutError:
            return False
        finally:
            PENDING.pop(aid, None)

    async def notify(self, text):
        await self.bot.send_message(self.chat_id, text[:3900])

    async def send_voice(self, text):
        path = await voice.synth(text, "ogg")
        try:
            with open(path, "rb") as f:
                await self.bot.send_voice(self.chat_id, f, **UP)
        finally:
            Path(path).unlink(missing_ok=True)

    async def send_photo(self, path, caption=""):
        await self.bot.send_chat_action(self.chat_id, "upload_photo")
        if os.path.getsize(path) > 9 * MB:
            return await self.send_document(path, caption)
        with open(path, "rb") as f:
            await self.bot.send_photo(self.chat_id, f, caption=caption[:1000] or None, **UP)

    async def send_video(self, path, caption=""):
        await self.bot.send_chat_action(self.chat_id, "upload_video")
        with open(path, "rb") as f:
            await self.bot.send_video(self.chat_id, f, caption=caption[:1000] or None, supports_streaming=True, **UP)

    async def send_audio(self, path, caption="", title=None, performer=None):
        await self.bot.send_chat_action(self.chat_id, "upload_voice")
        with open(path, "rb") as f:
            await self.bot.send_audio(self.chat_id, f, caption=caption[:1000] or None,
                                      title=(title or None) and str(title)[:64], performer=(performer or None) and str(performer)[:64], **UP)

    async def send_document(self, path, caption=""):
        await self.bot.send_chat_action(self.chat_id, "upload_document")
        with open(path, "rb") as f:
            await self.bot.send_document(self.chat_id, f, caption=caption[:1000] or None, filename=Path(path).name, **UP)


def is_owner(update: Update) -> bool:
    owner = config.load()["owner_id"]
    return bool(owner) and update.effective_user is not None and update.effective_user.id == owner


async def send_long(bot, chat_id, text):
    text = text or "…"
    for i in range(0, len(text), 3900):
        await bot.send_message(chat_id, text[i:i + 3900])


def build(agent: Agent) -> Application:
    token = config.load()["bot_token"]
    app = Application.builder().token(token).concurrent_updates(True).build()

    # ---------------------------------------------------------------- commands
    async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        cfg = config.load()
        uid = update.effective_user.id
        if not cfg["owner_id"] and cfg["pair_code"] and ctx.args and ctx.args[0] == cfg["pair_code"]:
            config.update(owner_id=uid, pair_code="")
            await update.message.reply_text("✅ اتصال انجام شد! شما مالک این ایجنت هستید.\nYou are now the owner.\n\n" + HELP)
        elif cfg["owner_id"] == uid:
            await update.message.reply_text("سلام! 👋\n\n" + HELP)
        else:
            await update.message.reply_text(f"⛔ Private bot.\nYour Telegram ID: {uid}")

    async def help_cmd(update: Update, ctx):
        if is_owner(update):
            await update.message.reply_text(HELP)

    async def cookies_cmd(update: Update, ctx):
        if is_owner(update):
            have = "✅ cookies.txt ذخیره شده" if config.COOKIES.exists() else "❌ هنوز cookies.txt ندارم"
            await update.message.reply_text(have + "\n\n" + COOKIES_HELP)

    async def status(update: Update, ctx):
        if not is_owner(update):
            return
        cfg = config.load()
        await update.message.reply_text(
            f"✅ Online · v{config.VERSION}\nProvider: {cfg['provider']}\nApproval: {cfg['approval']}\n"
            f"Timezone: {cfg['timezone']}\nCookies: {'yes' if config.COOKIES.exists() else 'no'}")

    async def clear(update: Update, ctx):
        if is_owner(update):
            agent.clear("tg")
            await update.message.reply_text("🧹 حافظه‌ی گفتگو پاک شد / Memory cleared")

    # ---------------------------------------------------------------- helpers
    async def reply_with_agent(update: Update, text: str, images=None):
        chat_id = update.effective_chat.id
        bot = update.get_bot()
        try:
            async with Busy(bot, chat_id):
                answer = await agent.run("tg", text, TgCtx(bot, chat_id), images)
        except Exception as e:
            log.exception("agent error")
            answer = f"⚠️ {e}"
        await send_long(bot, chat_id, answer)

    async def handle_link(update: Update, url: str, what: str):
        chat_id = update.effective_chat.id
        bot = update.get_bot()
        status_msg = await bot.send_message(chat_id, "⏬ دارم دانلود می‌کنم… / Downloading…")
        try:
            async with Busy(bot, chat_id, "upload_video"):
                await flow.deliver_link(TgCtx(bot, chat_id), url, what)
        except media.MediaError as e:
            await bot.send_message(chat_id, f"⚠️ {e}")
        except Exception as e:
            log.exception("link flow failed")
            await bot.send_message(chat_id, f"⚠️ {type(e).__name__}: {str(e)[:300]}")
        finally:
            try:
                await status_msg.delete()
            except Exception:
                pass

    async def watch_video(update: Update, tg_file_obj, caption: str):
        m = update.message
        if tg_file_obj.file_size and tg_file_obj.file_size > 20 * MB:
            await m.reply_text("⚠️ ویدیو بزرگ‌تر از ۲۰ مگابایته و ربات تلگرام اجازه‌ی دریافتش رو نداره. یه ویدیوی کوچیک‌تر یا لینکش رو بفرست.")
            return
        bot = update.get_bot()
        try:
            async with Busy(bot, m.chat_id):
                f = await tg_file_obj.get_file()
                path = media.workdir() / "tg_video.mp4"
                await f.download_to_drive(str(path))
                answer = await media.analyze_video(path, caption)
            agent.note("tg", f"[sent a video] {caption}", answer)
        except Exception as e:
            log.exception("video analysis failed")
            answer = f"⚠️ {e}"
        await send_long(bot, m.chat_id, answer)

    # ---------------------------------------------------------------- handlers
    async def on_text(update: Update, ctx):
        if not is_owner(update):
            return
        text = update.message.text
        req = flow.parse_link_request(text)
        if req:
            await handle_link(update, *req)
        else:
            await reply_with_agent(update, text)

    async def on_voice(update: Update, ctx):
        if not is_owner(update):
            return
        m = update.message
        path = media.workdir() / "voice.ogg"
        f = await (m.voice or m.audio).get_file()
        await f.download_to_drive(str(path))
        try:
            text = await voice.transcribe(str(path))
        except Exception as e:
            await m.reply_text(f"⚠️ {e}")
            return
        await m.reply_text(f"🎤 {text}")
        req = flow.parse_link_request(text)
        if req:
            await handle_link(update, *req)
        else:
            await reply_with_agent(update, text)

    async def on_photo(update: Update, ctx):
        if not is_owner(update):
            return
        m = update.message
        f = await m.photo[-1].get_file()
        data = bytes(await f.download_as_bytearray())
        await reply_with_agent(update, m.caption or "این عکس رو توضیح بده. / Describe this image.", [data])

    async def on_video(update: Update, ctx):
        if not is_owner(update):
            return
        m = update.message
        await watch_video(update, m.video or m.video_note or m.animation, m.caption or "")

    async def on_document(update: Update, ctx):
        if not is_owner(update):
            return
        m = update.message
        d = m.document
        name = (d.file_name or "file").replace("/", "_")
        if name.lower().endswith(".txt") and "cookie" in name.lower():
            f = await d.get_file()
            await f.download_to_drive(str(config.COOKIES))
            os.chmod(config.COOKIES, 0o600)
            await m.reply_text("✅ cookies.txt ذخیره شد. حالا لینک اینستاگرام/یوتیوب رو دوباره بفرست.")
            return
        mime = d.mime_type or ""
        if mime.startswith("video/"):
            await watch_video(update, d, m.caption or "")
            return
        if d.file_size and d.file_size > 20 * MB:
            await m.reply_text("⚠️ فایل بزرگ‌تر از ۲۰ مگابایته و ربات نمی‌تونه دریافتش کنه.")
            return
        f = await d.get_file()
        if mime.startswith("image/"):
            data = bytes(await f.download_as_bytearray())
            await reply_with_agent(update, m.caption or "این عکس رو توضیح بده. / Describe this image.", [data])
            return
        dest = config.INBOX / f"{uuid.uuid4().hex[:6]}_{name}"
        await f.download_to_drive(str(dest))
        await reply_with_agent(update, f"[I received a file and saved it at {dest}] {m.caption or 'What is this file?'}")

    async def on_button(update: Update, ctx):
        q = update.callback_query
        if not is_owner(update):
            await q.answer("⛔", show_alert=True)
            return
        verdict, _, aid = q.data.partition(":")
        fut = PENDING.get(aid)
        if fut and not fut.done():
            fut.set_result(verdict == "ok")
            await q.answer("OK")
            await q.edit_message_text(q.message.text + ("\n\n✅ تایید شد" if verdict == "ok" else "\n\n❌ رد شد"))
        else:
            await q.answer("منقضی شده / expired")

    async def on_error(update, ctx):
        log.error("telegram error: %s", ctx.error, exc_info=ctx.error)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("cookies", cookies_cmd))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE | filters.ANIMATION, on_video))
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    return app
