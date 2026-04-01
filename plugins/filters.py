import io
from pyrogram import filters, Client, enums
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from database.filters_mdb import (
    add_filter,
    get_filters,
    delete_filter,
    count_filters
)

from database.connections_mdb import active_connection
from utils import get_file_id, parser, split_quotes
from info import ADMINS


@Client.on_message(filters.command(['filter', 'add']) & filters.incoming)
async def addfilter(client, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"You are anonymous admin. Use /connect {message.chat.id} in PM")

    chat_type = message.chat.type

    # ✅ SAFE SPLIT
    args = message.text.split(None, 1) if message.text else []

    if chat_type == enums.ChatType.PRIVATE:
        grpid = await active_connection(str(userid))
        if not grpid:
            return await message.reply_text("I'm not connected to any groups!", quote=True)

        grp_id = grpid
        try:
            chat = await client.get_chat(grpid)
            title = chat.title
        except:
            return await message.reply_text("Make sure I'm present in your group!!", quote=True)

    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grp_id = message.chat.id
        title = message.chat.title
    else:
        return

    st = await client.get_chat_member(grp_id, userid)
    if (
        st.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
        and str(userid) not in ADMINS
    ):
        return

    if len(args) < 2:
        return await message.reply_text("Command Incomplete :(", quote=True)

    extracted = split_quotes(args[1])
    text = extracted[0].lower()

    if not message.reply_to_message and len(extracted) < 2:
        return await message.reply_text("Add some content to save your filter!", quote=True)

    # ✅ SAFE PARSER HANDLING
    try:
        if (len(extracted) >= 2) and not message.reply_to_message:
            reply_text, btn, alert = parser(extracted[1], text)
            fileid = None

            if not reply_text:
                return await message.reply_text(
                    "You cannot have buttons alone, give some text!",
                    quote=True
                )

        elif message.reply_to_message:
            msg = message.reply_to_message

            if msg.reply_markup:
                btn = msg.reply_markup.inline_keyboard
                file = get_file_id(msg)
                fileid = file.file_id if file else None
                reply_text = (msg.caption or msg.text or "")
                alert = None

            elif msg.media:
                file = get_file_id(msg)
                fileid = file.file_id if file else None

                content = msg.caption or ""
                reply_text, btn, alert = parser(content, text)

            elif msg.text:
                fileid = None
                reply_text, btn, alert = parser(msg.text, text)

            else:
                return

    except Exception as e:
        return await message.reply_text(f"Error: {e}", quote=True)

    await add_filter(grp_id, text, reply_text, btn, fileid, alert)

    await message.reply_text(
        f"Filter for `{text}` added in **{title}**",
        quote=True,
        parse_mode=enums.ParseMode.MARKDOWN
    )


@Client.on_message(filters.command(['viewfilters', 'filters']) & filters.incoming)
async def get_all(client, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"You are anonymous admin. Use /connect {message.chat.id} in PM")

    chat_type = message.chat.type

    if chat_type == enums.ChatType.PRIVATE:
        grpid = await active_connection(str(userid))
        if not grpid:
            return await message.reply_text("I'm not connected to any groups!", quote=True)

        grp_id = grpid
        try:
            chat = await client.get_chat(grpid)
            title = chat.title
        except:
            return await message.reply_text("Make sure I'm present in your group!!", quote=True)

    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grp_id = message.chat.id
        title = message.chat.title
    else:
        return

    st = await client.get_chat_member(grp_id, userid)
    if (
        st.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
        and str(userid) not in ADMINS
    ):
        return

    texts = await get_filters(grp_id)
    count = await count_filters(grp_id)

    if count:
        filterlist = f"Total filters in **{title}** : {count}\n\n"

        for text in texts:
            filterlist += f" × `{text}`\n"

        if len(filterlist) > 4096:
            with io.BytesIO(filterlist.encode()) as f:
                f.name = "filters.txt"
                return await message.reply_document(f, quote=True)
    else:
        filterlist = f"There are no active filters in **{title}**"

    await message.reply_text(filterlist, quote=True, parse_mode=enums.ParseMode.MARKDOWN)


@Client.on_message(filters.command('del') & filters.incoming)
async def deletefilter(client, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return await message.reply(f"You are anonymous admin. Use /connect {message.chat.id} in PM")

    chat_type = message.chat.type

    if chat_type == enums.ChatType.PRIVATE:
        grpid = await active_connection(str(userid))
        if not grpid:
            return await message.reply_text("I'm not connected!", quote=True)

        grp_id = grpid
    elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
        grp_id = message.chat.id
    else:
        return

    st = await client.get_chat_member(grp_id, userid)
    if (
        st.status not in [enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
        and str(userid) not in ADMINS
    ):
        return

    # ✅ FULL SAFE SPLIT
    if not message.text:
        return

    parts = message.text.split(" ", 1)

    if len(parts) < 2:
        return await message.reply_text(
            "<i>Mention the filter name!</i>\n\n<code>/del name</code>",
            quote=True
        )

    text = parts[1].strip().lower()

    await delete_filter(message, text, grp_id)


@Client.on_message(filters.command('delall') & filters.incoming)
async def delallconfirm(client, message):
    userid = message.from_user.id if message.from_user else None
    if not userid:
        return

    chat_type = message.chat.type

    if chat_type == enums.ChatType.PRIVATE:
        grpid = await active_connection(str(userid))
        if not grpid:
            return
        grp_id = grpid
        title = "your group"

    else:
        grp_id = message.chat.id
        title = message.chat.title

    st = await client.get_chat_member(grp_id, userid)

    if st.status == enums.ChatMemberStatus.OWNER or str(userid) in ADMINS:
        await message.reply_text(
            f"This will delete all filters from '{title}'. Continue?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("YES", callback_data="delallconfirm")],
                [InlineKeyboardButton("CANCEL", callback_data="delallcancel")]
            ]),
            quote=True
        )
