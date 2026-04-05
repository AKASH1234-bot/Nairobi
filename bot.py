import os
import logging
import asyncio
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

from pyrogram import Client, __version__, enums
from pyrogram.raw.all import layer
from database.ia_filterdb import Media
from database.users_chats_db import db
from info import SESSION, API_ID, API_HASH, BOT_TOKEN, LOG_STR, LOG_CHANNEL
from utils import temp
from typing import Union, Optional, AsyncGenerator
from pyrogram import types
import traceback


async def health_check(request):
    return web.Response(text="OK")


async def self_ping(url: str):
    """Ping own health check URL every 5 minutes to prevent Render sleep."""
    import aiohttp
    await asyncio.sleep(60)
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    logging.info(f"Self-ping: {resp.status}")
        except Exception as e:
            logging.warning(f"Self-ping failed: {e}")
        await asyncio.sleep(300)


async def start_health_server():
    import os
    app_web = web.Application()
    app_web.router.add_get("/", health_check)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logging.info("Health check server started on port 8080")
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "")
    if render_url:
        asyncio.create_task(self_ping(render_url))
        logging.info(f"Self-ping started for {render_url}")


class Bot(Client):
    def __init__(self):
        session_string = os.environ.get("SESSION_STRING")
        super().__init__(
            name=SESSION or "bot",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string if session_string else None,
            bot_token=BOT_TOKEN,
            workers=50,
            plugins={"root": "plugins"},
            sleep_threshold=5,
        )

    async def start(self):
        try:
            b_users, b_chats = await db.get_banned()
            temp.BANNED_USERS = b_users
            temp.BANNED_CHATS = b_chats
            await super().start()

            # ensure indexes if method exists
            try:
                await Media.ensure_indexes()
            except Exception:
                pass

            me = await self.get_me()
            temp.ME = me.id
            temp.U_NAME = me.username
            temp.B_NAME = me.first_name
            self.username = '@' + me.username

            logging.info(
                f"{me.first_name} started | Pyrogram v{__version__} (Layer {layer}) | @{me.username}"
            )
            logging.info(LOG_STR)

            # ── Load persisted data from MongoDB ─────────────
            try:
                from plugins.pm_filter import _load_trending
                await _load_trending()
                logging.info("Trending data loaded from DB")
            except Exception as e:
                logging.warning(f"Could not load trending: {e}")

            # ── Resolve fsub channel peers so get_chat_member works immediately ──
            try:
                from plugins.pm_filter import _dynamic_channels
                from info import AUTH_CHANNEL_1, AUTH_CHANNEL_2
                channels_to_resolve = [
                    _dynamic_channels.get("ch1", AUTH_CHANNEL_1),
                    _dynamic_channels.get("ch2", AUTH_CHANNEL_2),
                ]
                for ch_id in channels_to_resolve:
                    if ch_id:
                        try:
                            await self.get_chat(ch_id)
                            logging.info(f"Resolved fsub channel peer: {ch_id}")
                        except Exception as e:
                            logging.warning(f"Could not resolve fsub channel {ch_id}: {e}")
            except Exception as e:
                logging.warning(f"Fsub peer resolution failed: {e}")

            # ── Resolve LOG_CHANNEL peer + send startup log ──
            if LOG_CHANNEL:
                try:
                    # Step 1: resolve the peer so Pyrogram caches it
                    # This fixes "Peer id invalid" after restarts
                    await self.get_chat(LOG_CHANNEL)
                    logging.info(f"LOG_CHANNEL {LOG_CHANNEL} peer resolved")
                except Exception as e:
                    logging.warning(f"Could not resolve LOG_CHANNEL peer: {e}")

                try:
                    await self.send_message(
                        LOG_CHANNEL,
                        f"<b>✅ Bot Started</b>\n\n"
                        f"🤖 <b>Name:</b> {me.first_name}\n"
                        f"👤 <b>Username:</b> @{me.username}\n"
                        f"🆔 <b>ID:</b> <code>{me.id}</code>\n"
                        f"📦 <b>Pyrogram:</b> v{__version__} (Layer {layer})\n\n"
                        f"{LOG_STR}",
                        parse_mode=enums.ParseMode.HTML
                    )
                    logging.info("Startup log sent successfully to LOG_CHANNEL")
                except Exception as e:
                    logging.error(f"Failed to send log to LOG_CHANNEL {LOG_CHANNEL}: {e}")
            else:
                logging.warning("LOG_CHANNEL is not set or is 0 — skipping startup log")

        except Exception as e:
            logging.error(f"START ERROR: {e}")
            traceback.print_exc()

    async def stop(self, *args):
        await super().stop()
        logging.info("Bot stopped.")

    async def iter_messages(
        self,
        chat_id: Union[int, str],
        limit: int,
        offset: int = 0,
    ) -> Optional[AsyncGenerator["types.Message", None]]:
        current = offset
        while True:
            new_diff = min(200, limit - current)
            if new_diff <= 0:
                return
            messages = await self.get_messages(
                chat_id,
                list(range(current, current + new_diff + 1))
            )
            for message in messages:
                yield message
                current += 1


bot = Bot()


async def main():
    await start_health_server()
    await bot.start()
    await asyncio.Event().wait()


try:
    bot.run(main())
except Exception as e:
    logging.error(f"CRASH: {e}")
    traceback.print_exc()
