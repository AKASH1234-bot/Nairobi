import logging

# ✅ SAFE LOGGING (NO CRASH)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

from pyrogram import Client, __version__
from pyrogram.raw.all import layer
from database.ia_filterdb import Media
from database.users_chats_db import db
from info import SESSION, API_ID, API_HASH, BOT_TOKEN, LOG_STR
from utils import temp
from typing import Union, Optional, AsyncGenerator
from pyrogram import types
import traceback


class Bot(Client):

    def __init__(self):
        super().__init__(
            name=SESSION or "bot",  # ✅ SAFE DEFAULT
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
            await Media.ensure_indexes()

            me = await self.get_me()

            temp.ME = me.id
            temp.U_NAME = me.username
            temp.B_NAME = me.first_name
            self.username = '@' + me.username

            logging.info(
                f"{me.first_name} started | Pyrogram v{__version__} (Layer {layer}) | @{me.username}"
            )
            logging.info(LOG_STR)

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


# ✅ SAFE RUN (NO SILENT CRASH)
app = Bot()

try:
    app.run()
except Exception as e:
    logging.error(f"CRASH: {e}")
    traceback.print_exc()
