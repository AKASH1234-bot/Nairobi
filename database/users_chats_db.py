# https://github.com/odysseusmax/animated-lamp/blob/master/bot/database/database.py
import motor.motor_asyncio
from info import DATABASE_NAME, DATABASE_URI, IMDB, IMDB_TEMPLATE, MELCOW_NEW_USERS, P_TTI_SHOW_OFF, SINGLE_BUTTON, SPELL_CHECK_REPLY, PROTECT_CONTENT


class Database:

    def __init__(self, uri, database_name):
        self._client = motor.motor_asyncio.AsyncIOMotorClient(
            uri,
            # Connection pool for faster queries
            maxPoolSize=10,
            minPoolSize=2,
            connectTimeoutMS=5000,
            serverSelectionTimeoutMS=5000,
        )
        self.db = self._client[database_name]
        self.col = self.db.users
        self.grp = self.db.groups
        # Ensure indexes are created on startup
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self._ensure_indexes())
        except Exception:
            pass

    async def _ensure_indexes(self):
        """Create indexes for faster queries — runs once on startup."""
        try:
            # Index on user id for fast lookups
            await self.col.create_index('id', unique=True, background=True)
            # Index on ban status for fast banned user queries
            await self.col.create_index('ban_status.is_banned', background=True)
            # Index on group id for fast lookups
            await self.grp.create_index('id', unique=True, background=True)
            # Index on disabled status
            await self.grp.create_index('chat_status.is_disabled', background=True)
        except Exception as e:
            pass  # Indexes may already exist

    def new_user(self, id, name):
        return dict(
            id=id,
            name=name,
            ban_status=dict(
                is_banned=False,
                ban_reason="",
            ),
        )

    def new_group(self, id, title):
        return dict(
            id=id,
            title=title,
            chat_status=dict(
                is_disabled=False,
                reason="",
            ),
        )

    async def add_user(self, id, name):
        user = self.new_user(id, name)
        await self.col.insert_one(user)

    async def is_user_exist(self, id):
        # Uses index on 'id' — fast O(log n) lookup
        user = await self.col.find_one({'id': int(id)}, {'_id': 1})
        return bool(user)

    async def total_users_count(self):
        count = await self.col.count_documents({})
        return count

    async def remove_ban(self, id):
        ban_status = dict(is_banned=False, ban_reason='')
        await self.col.update_one({'id': id}, {'$set': {'ban_status': ban_status}})

    async def ban_user(self, user_id, ban_reason="No Reason"):
        ban_status = dict(is_banned=True, ban_reason=ban_reason)
        await self.col.update_one({'id': user_id}, {'$set': {'ban_status': ban_status}})

    async def get_ban_status(self, id):
        default = dict(is_banned=False, ban_reason='')
        user = await self.col.find_one({'id': int(id)}, {'ban_status': 1})
        if not user:
            return default
        return user.get('ban_status', default)

    async def get_all_users(self):
        return self.col.find({})

    async def delete_user(self, user_id):
        await self.col.delete_many({'id': int(user_id)})

    async def get_banned(self):
        # Only fetch id field — faster than fetching full documents
        users = self.col.find({'ban_status.is_banned': True}, {'id': 1})
        chats = self.grp.find({'chat_status.is_disabled': True}, {'id': 1})
        b_users = [user['id'] async for user in users]
        b_chats = [chat['id'] async for chat in chats]
        return b_users, b_chats

    async def add_chat(self, chat, title):
        chat = self.new_group(chat, title)
        await self.grp.insert_one(chat)

    async def get_chat(self, chat):
        # Only fetch chat_status field — faster
        chat = await self.grp.find_one({'id': int(chat)}, {'chat_status': 1})
        return False if not chat else chat.get('chat_status')

    async def re_enable_chat(self, id):
        chat_status = dict(is_disabled=False, reason="")
        await self.grp.update_one({'id': int(id)}, {'$set': {'chat_status': chat_status}})

    async def update_settings(self, id, settings):
        await self.grp.update_one({'id': int(id)}, {'$set': {'settings': settings}})

    async def get_settings(self, id):
        default = {
            'button': SINGLE_BUTTON,
            'botpm': P_TTI_SHOW_OFF,
            'file_secure': PROTECT_CONTENT,
            'imdb': IMDB,
            'spell_check': SPELL_CHECK_REPLY,
            'welcome': MELCOW_NEW_USERS,
            'template': IMDB_TEMPLATE
        }
        # Only fetch settings field — faster
        chat = await self.grp.find_one({'id': int(id)}, {'settings': 1})
        if chat:
            return chat.get('settings', default)
        return default

    async def disable_chat(self, chat, reason="No Reason"):
        chat_status = dict(is_disabled=True, reason=reason)
        await self.grp.update_one({'id': int(chat)}, {'$set': {'chat_status': chat_status}})

    async def total_chat_count(self):
        count = await self.grp.count_documents({})
        return count

    async def get_all_chats(self):
        return self.grp.find({})

    async def get_db_size(self):
        return (await self.db.command("dbstats"))['dataSize']


db = Database(DATABASE_URI, DATABASE_NAME)
