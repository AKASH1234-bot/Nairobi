import logging
import asyncio
from aiohttp import web

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

from pyrogram import Client, __version__
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


async def start_health_server():
    app_web = web.Application()
    app_web.router.add_get("/", health_check)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()
    logging.info("Health check server started on port 8080")


class Bot(Client):
    def __init__(self):
        super().__init__(
            name=SESSION or "bot",
            api_id=API_ID,
            api_hash=API_HASH,
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

            # ── Send startup log to LOG_CHANNEL ──────────────
            if LOG_CHANNEL:
                try:
                    logging.info(f"Sending startup log to LOG_CHANNEL: {LOG_CHANNEL}")
                    await self.send_message(
                        LOG_CHANNEL,
                        f"<b>✅ Bot Started</b>\n\n"
                        f"🤖 <b>Name:</b> {me.first_name}\n"
                        f"👤 <b>Username:</b> @{me.username}\n"
                        f"🆔 <b>ID:</b> <code>{me.id}</code>\n"
                        f"📦 <b>Pyrogram:</b> v{__version__} (Layer {layer})\n\n"
                        f"{LOG_STR}",
                        parse_mode="html"
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
