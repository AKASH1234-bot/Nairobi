import os
import logging
import random
import asyncio
from Script import script
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.ia_filterdb import Media, get_file_details, unpack_new_file_id
from database.users_chats_db import db
from info import CHANNELS, ADMINS, AUTH_CHANNEL, LOG_CHANNEL, PICS, BATCH_FILE_CAPTION, CUSTOM_FILE_CAPTION, PROTECT_CONTENT
from utils import get_settings, get_size, is_subscribed, save_group_settings, temp
from database.connections_mdb import active_connection
import re
import json
import base64
from os import environ

logger = logging.getLogger(__name__)

# Buttons shown below every sent file
FILE_REPLY_MARKUP = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("🎬 Movie Search Group", url="https://t.me/+AngJ8lGmH4wwNWY1"),
        InlineKeyboardButton("📢 Movie Updates",      url="https://t.me/cinemaclubnew"),
    ],
    [
        InlineKeyboardButton("📰 Movie News", url="https://t.me/ccl_news"),
    ],
])

BATCH_FILES = {}

# ── Dual Force Subscribe channels (changeable via env vars) ──
AUTH_CHANNEL_1 = int(environ.get('AUTH_CHANNEL_1', -1003581625072))
AUTH_CHANNEL_2 = int(environ.get('AUTH_CHANNEL_2', -1003514982115))


async def check_dual_subscription(client, user_id):
    """Returns (joined_ch1, joined_ch2) — uses same channels as pm_filter._dynamic_channels"""
    from plugins.pm_filter import _dynamic_channels, _pending_requests, _load_pending_requests
    ch1 = _dynamic_channels.get("ch1", AUTH_CHANNEL_1)
    ch2 = _dynamic_channels.get("ch2", AUTH_CHANNEL_2)

    async def _check(ch_id):
        # 1. Check membership first — fastest path for existing members
        try:
            member = await client.get_chat_member(ch_id, user_id)
            if member.status in [
                enums.ChatMemberStatus.MEMBER,
                enums.ChatMemberStatus.ADMINISTRATOR,
                enums.ChatMemberStatus.OWNER,
                enums.ChatMemberStatus.RESTRICTED,
            ]:
                return True  # already a member ✅
            # Status is LEFT or BANNED — check pending request
        except UserNotParticipant:
            pass  # confirmed not a member — check pending request below
        except Exception as e:
            err = str(e).lower()
            # Bot not admin or channel unreachable — don't block user
            if any(x in err for x in ["peer_id_invalid", "chat_admin_required",
                                       "not enough rights", "channel_private",
                                       "chat_write_forbidden"]):
                return True  # can't verify, don't block
            # Any other error — don't block (avoids false rejections)
            return True

        # 2. Not a member — check in-memory pending requests
        pending = _pending_requests.get(user_id, set())
        if ch_id in pending:
            return True  # join request sent ✅

        # 3. Not in memory — check DB (handles bot restart case)
        db_pending = await _load_pending_requests(user_id)
        if db_pending:
            _pending_requests[user_id] = db_pending  # restore to memory
            if ch_id in db_pending:
                return True  # join request in DB ✅

        return False  # genuinely not joined

    import asyncio as _asyncio
    results = list(await _asyncio.gather(_check(ch1), _check(ch2)))
    return results


async def get_fsub_buttons(client, data=""):
    """Build join-request buttons for both channels — always shows both."""
    from plugins.pm_filter import get_invite_link, _dynamic_channels
    ch1 = _dynamic_channels.get("ch1", AUTH_CHANNEL_1)
    ch2 = _dynamic_channels.get("ch2", AUTH_CHANNEL_2)
    buttons = []
    for i, ch_id in enumerate([ch1, ch2], 1):
        link = await get_invite_link(client, ch_id)
        buttons.append([InlineKeyboardButton(f"📨 Join Channel {i}", url=link)])

    if data and data != "subscribe":
        try:
            pre, file_id = data.split("_", 1)
            buttons.append([InlineKeyboardButton("✅ I Joined — Send My File", callback_data=f"fsub_check#{pre}#{file_id}")])
        except (IndexError, ValueError):
            buttons.append([InlineKeyboardButton("✅ I Joined — Send My File", url=f"https://t.me/{temp.U_NAME}?start={data}")])

    return buttons


@Client.on_message(filters.command("start") & filters.incoming)
async def start(client, message):
    if message.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        buttons = [[
            InlineKeyboardButton('➕ Add Me As Admin 👉 Groups ➕', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
        ], [
            InlineKeyboardButton('Movie Search Group', url='https://t.me/+AngJ8lGmH4wwNWY1'),
            InlineKeyboardButton('Movie Updates', url='https://t.me/ccllinks')
        ], [
            InlineKeyboardButton('📖 How to Use', callback_data='how_to_use'),
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await message.reply(
            script.START_TXT.format(
                message.from_user.mention if message.from_user else message.chat.title,
                temp.U_NAME, temp.B_NAME
            ),
            reply_markup=reply_markup
        )
        await asyncio.sleep(2)
        if not await db.get_chat(message.chat.id):
            total = await client.get_chat_members_count(message.chat.id)
            try:
                await client.send_message(
                    LOG_CHANNEL,
                    script.LOG_TEXT_G.format(message.chat.title, message.chat.id, total, "Unknown"),
                    parse_mode=enums.ParseMode.HTML
                )
            except Exception as e:
                logger.error(f"Failed to send new group log: {e}")
            await db.add_chat(message.chat.id, message.chat.title)
        return

    if not await db.is_user_exist(message.from_user.id):
        await db.add_user(message.from_user.id, message.from_user.first_name)
        try:
            await client.send_message(
                LOG_CHANNEL,
                script.LOG_TEXT_P.format(message.from_user.id, message.from_user.mention),
                parse_mode=enums.ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Failed to send new user log: {e}")

    if len(message.command) != 2:
        buttons = [[
            InlineKeyboardButton('➕ Add Me As Admin 👉 Groups ➕', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
        ], [
            InlineKeyboardButton('Movie Search Group', url='https://t.me/+AngJ8lGmH4wwNWY1'),
            InlineKeyboardButton('Movie Updates', url='https://t.me/ccllinks')
        ], [
            InlineKeyboardButton('📖 How to Use', callback_data='how_to_use'),
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await message.reply_photo(
            photo=random.choice(PICS),
            caption=script.START_TXT.format(message.from_user.mention, temp.U_NAME, temp.B_NAME),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        return

    # ── Dual Force Subscribe check ────────────────────────
    joined = await check_dual_subscription(client, message.from_user.id)
    if not all(joined):
        data = message.command[1] if len(message.command) > 1 else "subscribe"
        # Store pending file so join_request_handler / fsub_check_cb can send it
        if data and data not in ("subscribe", "error", "okay", "help"):
            from plugins.pm_filter import _pending_files
            try:
                pre, file_id = data.split("_", 1)
                _pending_files[message.from_user.id] = (pre, file_id)
            except (ValueError, IndexError):
                pass
        buttons = await get_fsub_buttons(client, data)
        await client.send_message(
            chat_id=message.from_user.id,
            text=(
                "⚠️ <b>Join both channels to receive your file!</b>\n\n"
                "1️⃣ Click <b>Join Channel 1</b> → tap <b>Request to Join</b>\n"
                "2️⃣ Click <b>Join Channel 2</b> → tap <b>Request to Join</b>\n\n"
                "Then click <b>✅ I Joined — Send My File</b>"
            ),
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML
        )
        return

    if len(message.command) == 2 and message.command[1] in ["subscribe", "error", "okay", "help"]:
        buttons = [[
            InlineKeyboardButton('➕ Add Me As Admin 👉 Groups ➕', url=f'http://t.me/{temp.U_NAME}?startgroup=true')
        ], [
            InlineKeyboardButton('Movie Search Group', url='https://t.me/+AngJ8lGmH4wwNWY1'),
            InlineKeyboardButton('Movie Updates', url='https://t.me/ccllinks')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await message.reply_photo(
            photo=random.choice(PICS),
            caption=script.START_TXT.format(message.from_user.mention, temp.U_NAME, temp.B_NAME),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        return

    data = message.command[1]
    try:
        pre, file_id = data.split('_', 1)
    except:
        file_id = data
        pre = ""

    if data.split("-", 1)[0] == "BATCH":
        sts = await message.reply("Please wait")
        file_id = data.split("-", 1)[1]
        msgs = BATCH_FILES.get(file_id)
        if not msgs:
            file = await client.download_media(file_id)
            try:
                with open(file) as file_data:
                    msgs = json.loads(file_data.read())
            except:
                await sts.edit("FAILED")
                return await client.send_message(LOG_CHANNEL, "UNABLE TO OPEN FILE.")
            os.remove(file)
            BATCH_FILES[file_id] = msgs
        for msg in msgs:
            title = msg.get("title")
            size = get_size(int(msg.get("size", 0)))
            f_caption = msg.get("caption", "")
            if BATCH_FILE_CAPTION:
                try:
                    f_caption = BATCH_FILE_CAPTION.format(
                        file_name='' if title is None else title,
                        file_size='' if size is None else size,
                        file_caption='' if f_caption is None else f_caption
                    )
                except Exception as e:
                    logger.exception(e)
            if f_caption is None:
                f_caption = f"{title}"
            try:
                await client.send_cached_media(
                    chat_id=message.from_user.id,
                    file_id=msg.get("file_id"),
                    caption=f_caption,
                    protect_content=msg.get('protect', False),
                    reply_markup=FILE_REPLY_MARKUP
                )
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await client.send_cached_media(
                    chat_id=message.from_user.id,
                    file_id=msg.get("file_id"),
                    caption=f_caption,
                    protect_content=msg.get('protect', False),
                    reply_markup=FILE_REPLY_MARKUP
                )
            except Exception as e:
                logger.warning(e, exc_info=True)
                continue
            await asyncio.sleep(1)
        await sts.delete()
        return

    elif data.split("-", 1)[0] == "DSTORE":
        sts = await message.reply("Please wait")
        b_string = data.split("-", 1)[1]
        decoded = (base64.urlsafe_b64decode(b_string + "=" * (-len(b_string) % 4))).decode("ascii")
        try:
            f_msg_id, l_msg_id, f_chat_id, protect = decoded.split("_", 3)
        except:
            f_msg_id, l_msg_id, f_chat_id = decoded.split("_", 2)
            protect = "/pbatch" if PROTECT_CONTENT else "batch"
        async for msg in client.iter_messages(int(f_chat_id), int(l_msg_id), int(f_msg_id)):
            if msg.media:
                media = getattr(msg, msg.media.value)
                if BATCH_FILE_CAPTION:
                    try:
                        f_caption = BATCH_FILE_CAPTION.format(
                            file_name=getattr(media, 'file_name', ''),
                            file_size=getattr(media, 'file_size', ''),
                            file_caption=getattr(msg, 'caption', '')
                        )
                    except Exception as e:
                        logger.exception(e)
                        f_caption = getattr(msg, 'caption', '')
                else:
                    file_name = getattr(media, 'file_name', '')
                    f_caption = getattr(msg, 'caption', file_name)
                try:
                    await msg.copy(message.chat.id, caption=f_caption, protect_content=True if protect == "/pbatch" else False)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await msg.copy(message.chat.id, caption=f_caption, protect_content=True if protect == "/pbatch" else False)
                except Exception as e:
                    logger.exception(e)
                    continue
            elif msg.empty:
                continue
            else:
                try:
                    await msg.copy(message.chat.id, protect_content=True if protect == "/pbatch" else False)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await msg.copy(message.chat.id, protect_content=True if protect == "/pbatch" else False)
                except Exception as e:
                    logger.exception(e)
                    continue
            await asyncio.sleep(1)
        return await sts.delete()

    files_ = await get_file_details(file_id)
    if not files_:
        pre, file_id = ((base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))).decode("ascii")).split("_", 1)
        try:
            msg = await client.send_cached_media(
                chat_id=message.from_user.id,
                file_id=file_id,
                protect_content=True if pre == 'filep' else False,
            )
            filetype = msg.media
            file = getattr(msg, filetype.value)
            title = file.file_name
            size = get_size(file.file_size)
            f_caption = f"<code>{title}</code>"
            if CUSTOM_FILE_CAPTION:
                try:
                    f_caption = CUSTOM_FILE_CAPTION.format(
                        file_name='' if title is None else title,
                        file_size='' if size is None else size,
                        file_caption=''
                    )
                except:
                    return
            await msg.edit_caption(f_caption)
            return
        except:
            pass
        return await message.reply('No such file exist.')

    files = files_[0]
    title = files.file_name
    size = get_size(files.file_size)
    f_caption = files.caption
    if CUSTOM_FILE_CAPTION:
        try:
            f_caption = CUSTOM_FILE_CAPTION.format(
                file_name='' if title is None else title,
                file_size='' if size is None else size,
                file_caption='' if f_caption is None else f_caption
            )
        except Exception as e:
            logger.exception(e)
    if f_caption is None:
        f_caption = f"{files.file_name}"
    await client.send_cached_media(
        chat_id=message.from_user.id,
        file_id=file_id,
        caption=f_caption,
        protect_content=True if pre == 'filep' else False,
        reply_markup=FILE_REPLY_MARKUP
    )
    try:
        await client.send_message(
            LOG_CHANNEL,
            ("📥 <b>File Sent</b>\n"
             f"👤 User: {message.from_user.mention} (<code>{message.from_user.id}</code>)\n"
             f"🎬 File: <code>{title}</code>\n"
             f"📦 Size: {size}"),
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"Log channel error: {e}")


@Client.on_callback_query(filters.regex(r"^how_to_use$"))
async def how_to_use_cb(client, query):
    HOW_TO_USE_TEXT = (
        "🎬 <b>Cinema Club™ Bot — Features Guide</b>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 <b>SEARCH MOVIES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "• Type any movie name in the group\n"
        "• Bot shows all files instantly\n"
        "• Use <b>Language</b> buttons to filter\n"
        "• Use <b>Quality</b> buttons (480p/720p/1080p)\n"
        "• Click any file → sent to your PM\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📺 <b>SERIES SEARCH</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "• Type: <code>Loki S01E01</code>\n"
        "• Click <b>SERIES</b> tab to filter series only\n"
        "• Season buttons appear automatically\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🌐 <b>LANGUAGE FILTERS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Malayalam | Tamil | Hindi | English\n"
        "Telugu | Kannada | Multi Audio | Dual Audio\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚡ <b>SPECIAL FEATURES</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>Send All To PM</b> — get all results at once\n"
        "• <b>INFO tab</b> — movie info & details\n"
        "• <b>MOVIE tab</b> — movies only\n"
        "• <b>SERIES tab</b> — series only\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>YOUR STATS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "• /mystats — your search & download stats\n"
        "• /history — your last 10 searches\n"
        "• /trending — top searched movies\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <b>TIPS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "• Use short movie names for best results\n"
        "• Don't use: ':(|,./\n"
        "• Files auto-delete after 5 minutes in group\n"
        "• Files in group auto-delete after 5 minutes\n"
    )
    back_btn = InlineKeyboardMarkup([[
        InlineKeyboardButton('🏠 Back', callback_data='start')
    ]])
    try:
        await query.message.edit_text(
            HOW_TO_USE_TEXT,
            reply_markup=back_btn,
            parse_mode=enums.ParseMode.HTML
        )
    except Exception:
        await query.message.reply(
            HOW_TO_USE_TEXT,
            reply_markup=back_btn,
            parse_mode=enums.ParseMode.HTML
        )
    await query.answer()


@Client.on_message(filters.command('channel') & filters.user(ADMINS))
async def channel_info(bot, message):
    if isinstance(CHANNELS, (int, str)):
        channels = [CHANNELS]
    elif isinstance(CHANNELS, list):
        channels = CHANNELS
    else:
        raise ValueError("Unexpected type of CHANNELS")
    text = '📑 **Indexed channels/groups**\n'
    for channel in channels:
        chat = await bot.get_chat(channel)
        if chat.username:
            text += '\n@' + chat.username
        else:
            text += '\n' + chat.title or chat.first_name
    text += f'\n\n**Total:** {len(CHANNELS)}'
    if len(text) < 4096:
        await message.reply(text)
    else:
        file = 'Indexed channels.txt'
        with open(file, 'w') as f:
            f.write(text)
        await message.reply_document(file)
        os.remove(file)


@Client.on_message(filters.command('logs') & filters.user(ADMINS))
async def log_file(bot, message):
    try:
        await message.reply_document('TelegramBot.log')
    except Exception as e:
        await message.reply(str(e))


@Client.on_message(filters.command('delete') & filters.user(ADMINS))
async def delete(bot, message):
    reply = message.reply_to_message
    if reply and reply.media:
        msg = await message.reply("Processing...⏳", quote=True)
    else:
        await message.reply('Reply to file with /delete which you want to delete', quote=True)
        return
    for file_type in ("document", "video", "audio"):
        media = getattr(reply, file_type, None)
        if media is not None:
            break
    else:
        await msg.edit('This is not supported file format')
        return
    file_id, file_ref = unpack_new_file_id(media.file_id)
    result = await Media.collection.delete_one({'_id': file_id})
    if result.deleted_count:
        await msg.edit('File is successfully deleted from database')
    else:
        file_name = re.sub(r"(_|\-|\.|\+)", " ", str(media.file_name))
        result = await Media.collection.delete_many({
            'file_name': file_name,
            'file_size': media.file_size,
            'mime_type': media.mime_type
        })
        if result.deleted_count:
            await msg.edit('File is successfully deleted from database')
        else:
            result = await Media.collection.delete_many({
                'file_name': media.file_name,
                'file_size': media.file_size,
                'mime_type': media.mime_type
            })
            if result.deleted_count:
                await msg.edit('File is successfully deleted from database')
            else:
                await msg.edit('File not found in database')


@Client.on_message(filters.command('deleteall') & filters.user(ADMINS))
async def delete_all_index(bot, message):
    await message.reply_text(
        'This will delete all indexed files.\nDo you want to continue??',
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(text="YES", callback_data="autofilter_delete")],
            [InlineKeyboardButton(text="CANCEL", callback_data="close_data")],
        ]),
        quote=True,
    )


@Client.on_callback_query(filters.regex(r'^autofilter_delete'))
async def delete_all_index_confirm(bot, message):
    await Media.collection.drop()
    await message.answer('Piracy Is Crime')
    await message.message.edit('Succesfully Deleted All The Indexed Files.')


@Client.on_message(filters.command('settings'))
async def settings(client, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"You are anonymous admin. Use /connect {message.chat.id} in PM")
    chat_type = message.chat.type
    if chat_type == enums.ChatType.PRIVATE:
        grpid = await active_connection(str(userid))
        if grpid is not None:
            grp_id = grpid
            try:
                chat = await client.get_chat(grpid)
                title = chat.title
            except:
                await message.reply_text("Make sure I'm present in your group!!", quote=True)
                return
        else:
            await message.reply_text("I'm not connected to any groups!", quote=True)
            return
    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grp_id = message.chat.id
        title = message.chat.title
    else:
        return
    st = await client.get_chat_member(grp_id, userid)
    if (
        st.status != enums.ChatMemberStatus.ADMINISTRATOR
        and st.status != enums.ChatMemberStatus.OWNER
        and str(userid) not in ADMINS
    ):
        return
    settings = await get_settings(grp_id)
    if settings is not None:
        buttons = [
            [InlineKeyboardButton('Filter Button', callback_data=f'setgs#button#{settings["button"]}#{grp_id}'), InlineKeyboardButton('Single' if settings["button"] else 'Double', callback_data=f'setgs#button#{settings["button"]}#{grp_id}')],
            [InlineKeyboardButton('Bot PM', callback_data=f'setgs#botpm#{settings["botpm"]}#{grp_id}'), InlineKeyboardButton('✅ Yes' if settings["botpm"] else '❌ No', callback_data=f'setgs#botpm#{settings["botpm"]}#{grp_id}')],
            [InlineKeyboardButton('File Secure', callback_data=f'setgs#file_secure#{settings["file_secure"]}#{grp_id}'), InlineKeyboardButton('✅ Yes' if settings["file_secure"] else '❌ No', callback_data=f'setgs#file_secure#{settings["file_secure"]}#{grp_id}')],
            [InlineKeyboardButton('IMDB', callback_data=f'setgs#imdb#{settings["imdb"]}#{grp_id}'), InlineKeyboardButton('✅ Yes' if settings["imdb"] else '❌ No', callback_data=f'setgs#imdb#{settings["imdb"]}#{grp_id}')],
            [InlineKeyboardButton('Spell Check', callback_data=f'setgs#spell_check#{settings["spell_check"]}#{grp_id}'), InlineKeyboardButton('✅ Yes' if settings["spell_check"] else '❌ No', callback_data=f'setgs#spell_check#{settings["spell_check"]}#{grp_id}')],
            [InlineKeyboardButton('Welcome', callback_data=f'setgs#welcome#{settings["welcome"]}#{grp_id}'), InlineKeyboardButton('✅ Yes' if settings["welcome"] else '❌ No', callback_data=f'setgs#welcome#{settings["welcome"]}#{grp_id}')],
        ]
        await message.reply_text(
            text=f"<b>Change Your Settings for {title} As Your Wish ⚙</b>",
            reply_markup=InlineKeyboardMarkup(buttons),
            disable_web_page_preview=True,
            parse_mode=enums.ParseMode.HTML,
            reply_to_message_id=message.id
        )


@Client.on_message(filters.command('set_template'))
async def save_template(client, message):
    sts = await message.reply("Checking template")
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"You are anonymous admin. Use /connect {message.chat.id} in PM")
    chat_type = message.chat.type
    if chat_type == enums.ChatType.PRIVATE:
        grpid = await active_connection(str(userid))
        if grpid is not None:
            grp_id = grpid
            try:
                chat = await client.get_chat(grpid)
                title = chat.title
            except:
                await message.reply_text("Make sure I'm present in your group!!", quote=True)
                return
        else:
            await message.reply_text("I'm not connected to any groups!", quote=True)
            return
    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grp_id = message.chat.id
        title = message.chat.title
    else:
        return
    st = await client.get_chat_member(grp_id, userid)
    if (
        st.status != enums.ChatMemberStatus.ADMINISTRATOR
        and st.status != enums.ChatMemberStatus.OWNER
        and str(userid) not in ADMINS
    ):
        return
    if len(message.command) < 2:
        return await sts.edit("No Input!!")
    template = message.text.split(" ", 1)[1]
    await save_group_settings(grp_id, 'template', template)
    await sts.edit(f"Successfully changed template for {title} to\n\n{template}")
