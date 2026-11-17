"""Service entrypoint: Telegram bot + web panel + scheduler in one process."""
import asyncio
import contextlib
import logging
import signal
import sys
import time
from logging.handlers import RotatingFileHandler

import uvicorn

import config
import scheduler
import tgbot
import tguser
import web
from agent import Agent

log = logging.getLogger("dabe")


class Server(uvicorn.Server):
    # we handle SIGTERM ourselves
    def install_signal_handlers(self):
        pass

    @contextlib.contextmanager
    def capture_signals(self):
        yield


def setup_logging():
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    fh = RotatingFileHandler(config.DATA / "dabe.log", maxBytes=1_000_000, backupCount=2)
    sh = logging.StreamHandler()
    for h in (fh, sh):
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[fh, sh])
    for noisy in ("httpx", "httpcore", "telethon", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def keep_ytdlp_fresh():
    """Instagram/YouTube/TikTok change often: upgrade yt-dlp at most once a day, in the background."""
    stamp = config.DATA / ".ytdlp_updated"
    try:
        if stamp.exists() and time.time() - stamp.stat().st_mtime < 86400:
            return
        p = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "-q", "-U", "yt-dlp[default]",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(p.wait(), 300)
        stamp.touch()
        log.info("yt-dlp checked for updates")
    except Exception as e:
        log.info("yt-dlp update skipped: %s", e)


async def main():
    setup_logging()
    cfg = config.load()
    agent = Agent()
    web.AGENT = agent
    await tguser.start()

    bot_app = None

    async def fire(task):
        owner = config.load()["owner_id"]
        if task["mode"] == "message":
            if task["target"] == "owner":
                if bot_app and owner:
                    await bot_app.bot.send_message(owner, f"⏰ {task['text']}")
            else:
                await tguser.send_text(task["target"], task["text"])
        elif bot_app and owner:
            ctx = tgbot.TgCtx(bot_app.bot, owner)
            ans = await agent.run("sched", task["text"], ctx)
            await tgbot.send_long(bot_app.bot, owner, f"⏰ {ans}")

    if cfg["bot_token"]:
        try:
            bot_app = tgbot.build(agent)
            await bot_app.initialize()
            await bot_app.start()
            await bot_app.updater.start_polling(drop_pending_updates=True)
            web.STATE["bot_running"] = True
            log.info("Telegram bot started")
        except Exception:
            log.exception("Telegram bot failed to start (check the token)")
            bot_app = None
    else:
        log.warning("No Telegram bot token set. Run `dabe` and choose [2].")

    server = Server(uvicorn.Config(web.app, host=cfg["web_host"], port=int(cfg["web_port"]), log_level="warning"))
    tasks = [asyncio.create_task(server.serve()), asyncio.create_task(scheduler.loop(fire)), asyncio.create_task(keep_ytdlp_fresh())]
    log.info("Web panel on http://%s:%s", cfg["web_host"], cfg["web_port"])

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(s, stop.set)
    await stop.wait()

    log.info("Shutting down")
    server.should_exit = True
    for t in tasks[1:]:
        t.cancel()
    if bot_app:
        with contextlib.suppress(Exception):
            await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()
    await tguser.stop()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
