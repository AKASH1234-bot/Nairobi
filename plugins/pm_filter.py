# Kanged From @TroJanZheX
import asyncio
import re
import ast
import math
from difflib import get_close_matches
from pyrogram.errors.exceptions.bad_request_400 import MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty
from Script import script
import pyrogram
from database.connections_mdb import active_connection, all_connections, delete_connection, if_active, make_active, make_inactive
from info import ADMINS, AUTH_CHANNEL, AUTH_USERS, CUSTOM_FILE_CAPTION, AUTH_GROUPS, P_TTI_SHOW_OFF, IMDB, SINGLE_BUTTON, SPELL_CHECK_REPLY, IMDB_TEMPLATE, LOG_CHANNEL
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram import Client, filters, enums
from pyrogram.errors import FloodWait, UserIsBlocked, MessageNotModified, PeerIdInvalid
from utils import get_size, is_subscribed, get_poster, search_gagala, temp, get_settings, save_group_settings
from database.users_chats_db import db
from database.ia_filterdb import Media, get_file_details, get_search_results
from database.filters_mdb import del_all, find_filter, get_filters
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)

BUTTONS = {}
SPELL_CHECK = {}

AUTO_DELETE_SECS = 300
filter_state  = {}
_search_cache = {}

LANGUAGES = ["Malayalam", "Tamil", "Hindi", "English", "Telugu", "Kannada"]
QUALITIES  = ["2160p", "1080p", "720p", "480p", "360p"]

HOW_TO_DL_TEXT = (
    "📥 <b>How to Download</b>\n\n"
    "1️⃣ Type the movie name in the group.\n"
    "2️⃣ Bot shows all results instantly.\n"
    "3️⃣ Use Language / Quality buttons to filter.\n"
    "4️⃣ Click a file button — it sends to your PM.\n\n"
    "<b>Tips:</b>\n"
    "• Use short movie names\n"
    "• Try different spellings\n"
    "• Files auto-delete after 5 minutes ⏳\n\n"
    "<i>Powered by Eva Maria Bot</i>"
)

QUALITY_PRIORITY = {"2160p": 5, "4k": 5, "1080p": 4, "720p": 3, "480p": 2, "360p": 1, "n/a": 0}
QUALITY_ORDER    = {"2160p": 0, "1080p": 1, "720p": 2, "480p": 3, "360p": 4}


async def _delete_later(*msgs):
    await asyncio.sleep(AUTO_DELETE_SECS)
    for m in msgs:
        try:
            await m.delete()
        except Exception:
            pass


def auto_delete(*msgs):
    asyncio.create_task(_delete_later(*msgs))


def detect_quality(fname):
    fl = fname.lower()
    for q in ["2160p", "4k", "1080p", "720p", "480p", "360p"]:
        if q in fl:
            return q
    return "n/a"


def detect_lang(fname):
    fl = fname.lower()
    if "multi" in fl:
        return "multi"
    for l in ["malayalam", "tamil", "hindi", "telugu", "kannada", "english"]:
        if l in fl:
            return l
    return "unknown"


def detect_season(fname):
    m = re.search(r'[Ss](\d{1,2})', fname)
    return f"S{int(m.group(1)):02d}" if m else ""


def normalize_name(name):
    name = name.lower()
    name = re.sub(r'[\[\](){}@#$%^&*!.,;:\'"\\/-]', ' ', name)
    return re.sub(r'\s+', ' ', name).strip()


def deduplicate(files):
    seen = {}
    for f in files:
        fname = f.file_name if hasattr(f, 'file_name') else ""
        if not fname:
            continue
        quality = detect_quality(fname)
        lang    = detect_lang(fname)
        clean   = re.sub(
            r'(19|20)\d{2}|2160p|1080p|720p|480p|360p|4k|hdrip|bluray|webrip|'
            r'hdtv|dvdrip|x264|x265|hevc|aac|hindi|tamil|malayalam|telugu|'
            r'kannada|english|multi|dubbed|web-dl|mkv|mp4|avi',
            ' ', fname.lower(), flags=re.IGNORECASE
        )
        clean = normalize_name(clean)
        key   = (clean, lang, quality)
        prio  = QUALITY_PRIORITY.get(quality, 0)
        if key not in seen or prio > seen[key][1]:
            seen[key] = (f, prio)
    return [item[0] for item in seen.values()]


def apply_filters(files, lang="All", qual="All", season="All"):
    out = []
    for f in files:
        name = (f.file_name or "").lower()
        if lang != "All" and lang.lower() not in name:
            continue
        if qual != "All" and qual.lower() not in name:
            continue
        if season != "All" and season.lower() not in name.replace(" ", ""):
            continue
        out.append(f)
    return out


def get_seasons(files):
    return sorted({detect_season(f.file_name or "") for f in files if detect_season(f.file_name or "")})


def get_available_languages(files):
    """Return only languages that actually exist in the file list."""
    found = set()
    for f in files:
        fname = (f.file_name or "").lower()
        for lang in LANGUAGES:
            if lang.lower() in fname:
                found.add(lang)
    return sorted(found) if found else []


def get_available_qualities(files):
    """Return only qualities that actually exist in the file list, sorted best first."""
    found = set()
    for f in files:
        fname = (f.file_name or "").lower()
        for q in QUALITIES:
            if q.lower() in fname:
                found.add(q)
    return sorted(found, key=lambda x: QUALITY_ORDER.get(x, 99)) if found else []


def _cache_set(key, value):
    if len(_search_cache) >= 200:
        del _search_cache[next(iter(_search_cache))]
    _search_cache[key] = value


async def fuzzy_search(query: str) -> str:
    if not _search_cache:
        return ""
    matches = get_close_matches(query.lower(), _search_cache.keys(), n=1, cutoff=0.6)
    return matches[0] if matches else ""


def build_full_keyboard(state_id, filtered, settings, sel_lang="All", sel_qual="All", sel_season="All", all_files=None):
    rows = []
    base_files = all_files or filtered
    pre = 'filep' if settings.get('file_secure') else 'file'

    # ── Row 1: Seasons FIRST (only if series) ────────────
    seasons = get_seasons(base_files)
    if seasons:
        season_row = [
            InlineKeyboardButton(
                ("✅ " if s == sel_season else "") + s,
                callback_data=f"nf_season#{state_id}#{sel_lang}#{sel_qual}#{s}"
            ) for s in seasons[:5]
        ]
        season_row.append(InlineKeyboardButton(
            ("✅ " if sel_season == "All" else "") + "All",
            callback_data=f"nf_season#{state_id}#{sel_lang}#{sel_qual}#All"
        ))
        rows.append(season_row)

    # ── Row 2: Only available Languages + All ────────────
    avail_langs = get_available_languages(base_files)
    if avail_langs:
        lang_row = [
            InlineKeyboardButton(
                ("✅ " if l == sel_lang else "") + l,
                callback_data=f"nf_lang#{state_id}#{l}#{sel_qual}#{sel_season}"
            ) for l in avail_langs
        ]
        lang_row.append(InlineKeyboardButton(
            ("✅ " if sel_lang == "All" else "") + "All",
            callback_data=f"nf_lang#{state_id}#All#{sel_qual}#{sel_season}"
        ))
        rows.append(lang_row)

    # ── Row 3: Only available Qualities + All ────────────
    avail_quals = get_available_qualities(base_files)
    if avail_quals:
        qual_row = [
            InlineKeyboardButton(
                ("✅ " if q == sel_qual else "") + q,
                callback_data=f"nf_qual#{state_id}#{sel_lang}#{q}#{sel_season}"
            ) for q in avail_quals
        ]
        qual_row.append(InlineKeyboardButton(
            ("✅ " if sel_qual == "All" else "") + "All",
            callback_data=f"nf_qual#{state_id}#{sel_lang}#All#{sel_season}"
        ))
        rows.append(qual_row)

    # ── Row 4: How to Download + Close ───────────────────
    rows.append([
        InlineKeyboardButton("📥 How to Download", callback_data=f"nf_howdl#{state_id}"),
        InlineKeyboardButton("✖ Close",            callback_data="nf_close"),
    ])

    # ── File buttons ──────────────────────────────────────
    if not filtered:
        rows.append([InlineKeyboardButton(
            "❌ No files found for this filter. Try another.",
            callback_data="nf_noop"
        )])
    else:
        for f in filtered[:10]:
            fname = f.file_name or "Unknown"
            q     = detect_quality(fname).upper()
            label = f"📄 {fname[:35]} [{q}]"
            rows.append([InlineKeyboardButton(label, callback_data=f"{pre}#{f.file_id}")])

    return InlineKeyboardMarkup(rows)


def build_header(query, filtered, sel_lang, sel_qual, sel_season, total):
    active = " | ".join(x for x in [sel_lang, sel_qual, sel_season] if x != "All")
    text = (
        f"🔍 <b>Results for:</b> <i>{query}</i>\n"
        f"📦 <b>Found:</b> {total} file(s)"
    )
    if active:
        text += f"\n🎯 <b>Filter:</b> {active} → {len(filtered)} result(s)"
    return text


@Client.on_message((filters.group | filters.private) & filters.text & filters.incoming)
async def give_filter(client, message):
    k = await manual_filters(client, message)
    if k == False:
        await auto_filter(client, message)


@Client.on_callback_query(filters.regex(r"^nf_howdl#"))
async def nf_howdl_cb(client, query):
    await query.answer()
    sent = await query.message.reply(HOW_TO_DL_TEXT, quote=True, parse_mode=enums.ParseMode.HTML)
    auto_delete(sent)


@Client.on_callback_query(filters.regex(r"^nf_lang#"))
async def nf_lang_cb(client, query):
    _, state_id, lang, qual, season = query.data.split("#", 4)
    if state_id not in filter_state:
        return await query.answer("Session expired. Search again.", show_alert=True)
    state    = filter_state[state_id]
    filtered = apply_filters(state["files"], lang=lang, qual=qual, season=season)
    settings = state.get("settings") or await get_settings(state["chat"])
    new_text = build_header(state["query"], filtered, lang, qual, season, state["total"])
    new_kb   = build_full_keyboard(state_id, filtered, settings, lang, qual, season, state["files"])
    try:
        await query.message.edit_text(new_text, reply_markup=new_kb, parse_mode=enums.ParseMode.HTML)
    except MessageNotModified:
        try:
            await query.message.edit_reply_markup(new_kb)
        except Exception:
            pass
    except Exception:
        pass
    await query.answer(f"Language: {lang}")


@Client.on_callback_query(filters.regex(r"^nf_qual#"))
async def nf_qual_cb(client, query):
    _, state_id, lang, qual, season = query.data.split("#", 4)
    if state_id not in filter_state:
        return await query.answer("Session expired. Search again.", show_alert=True)
    state    = filter_state[state_id]
    filtered = apply_filters(state["files"], lang=lang, qual=qual, season=season)
    settings = state.get("settings") or await get_settings(state["chat"])
    new_text = build_header(state["query"], filtered, lang, qual, season, state["total"])
    new_kb   = build_full_keyboard(state_id, filtered, settings, lang, qual, season, state["files"])
    try:
        await query.message.edit_text(new_text, reply_markup=new_kb, parse_mode=enums.ParseMode.HTML)
    except MessageNotModified:
        try:
            await query.message.edit_reply_markup(new_kb)
        except Exception:
            pass
    except Exception:
        pass
    await query.answer(f"Quality: {qual}")


@Client.on_callback_query(filters.regex(r"^nf_season#"))
async def nf_season_cb(client, query):
    _, state_id, lang, qual, season = query.data.split("#", 4)
    if state_id not in filter_state:
        return await query.answer("Session expired. Search again.", show_alert=True)
    state    = filter_state[state_id]
    filtered = apply_filters(state["files"], lang=lang, qual=qual, season=season)
    settings = state.get("settings") or await get_settings(state["chat"])
    new_text = build_header(state["query"], filtered, lang, qual, season, state["total"])
    new_kb   = build_full_keyboard(state_id, filtered, settings, lang, qual, season, state["files"])
    try:
        await query.message.edit_text(new_text, reply_markup=new_kb, parse_mode=enums.ParseMode.HTML)
    except MessageNotModified:
        try:
            await query.message.edit_reply_markup(new_kb)
        except Exception:
            pass
    except Exception:
        pass
    await query.answer(f"Season: {season}")


@Client.on_callback_query(filters.regex(r"^nf_close$"))
async def nf_close_cb(client, query):
    try:
        await query.message.delete()
    except Exception:
        pass
    await query.answer("Closed")


@Client.on_callback_query(filters.regex(r"^nf_noop$"))
async def nf_noop_cb(client, query):
    await query.answer("❌ No files for this filter. Try another.", show_alert=True)


@Client.on_callback_query(filters.regex(r"^fsub_check#"))
async def fsub_check_cb(client, query):
    """Re-check subscription and send file if user has joined."""
    from os import environ as _env
    _, ident, file_id = query.data.split("#", 2)
    ch1 = int(_env.get('AUTH_CHANNEL_1', -1003581625072))
    ch2 = int(_env.get('AUTH_CHANNEL_2', -1003514982115))
    not_joined = []
    for ch_id in [ch1, ch2]:
        try:
            member = await client.get_chat_member(ch_id, query.from_user.id)
            if member.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]:
                not_joined.append(ch_id)
        except Exception:
            not_joined.append(ch_id)

    if not_joined:
        # Still not joined
        btn = []
        for i, ch_id in enumerate(not_joined, 1):
            try:
                invite = await client.create_chat_invite_link(ch_id)
                link = invite.invite_link
            except Exception:
                try:
                    chat = await client.get_chat(ch_id)
                    link = f"https://t.me/{chat.username}" if chat.username else "https://t.me"
                except Exception:
                    link = "https://t.me"
            btn.append([InlineKeyboardButton(f"📢 Join Channel {i}", url=link)])
        btn.append([InlineKeyboardButton("✅ Try Again", callback_data=f"fsub_check#{ident}#{file_id}")])
        try:
            await query.message.edit_reply_markup(InlineKeyboardMarkup(btn))
        except Exception:
            pass
        return await query.answer("❌ You haven't joined yet! Please join first.", show_alert=True)

    # User has joined — send the file
    files_ = await get_file_details(file_id)
    if not files_:
        return await query.answer('No such file exist.', show_alert=True)
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
    try:
        await client.send_cached_media(
            chat_id=query.from_user.id,
            file_id=file_id,
            caption=f_caption,
            protect_content=True if ident == "filep" else False
        )
        await query.answer('✅ File sent to your PM!', show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
    except UserIsBlocked:
        await query.answer('Unblock the bot first!', show_alert=True)
    except Exception as e:
        logger.exception(e)
        await query.answer('Error sending file. Try again.', show_alert=True)


@Client.on_callback_query(filters.regex(r"^next"))
async def next_page(bot, query):
    ident, req, key, offset = query.data.split("_")
    if int(req) not in [query.from_user.id, 0]:
        return await query.answer("oKda", show_alert=True)
    try:
        offset = int(offset)
    except:
        offset = 0
    search = BUTTONS.get(key)
    if not search:
        await query.answer("You are using one of my old messages, please send the request again.", show_alert=True)
        return
    files, n_offset, total = await get_search_results(search, offset=offset, filter=True)
    try:
        n_offset = int(n_offset)
    except:
        n_offset = 0
    if not files:
        return
    settings = await get_settings(query.message.chat.id)
    if settings['button']:
        btn = [[InlineKeyboardButton(text=f"📂 [{get_size(file.file_size)}] 👉 {file.file_name}", callback_data=f'files#{file.file_id}')] for file in files]
    else:
        btn = [[InlineKeyboardButton(text=f"{file.file_name}", callback_data=f'files#{file.file_id}'), InlineKeyboardButton(text=f"{get_size(file.file_size)}", callback_data=f'files_#{file.file_id}')] for file in files]
    if 0 < offset <= 10:
        off_set = 0
    elif offset == 0:
        off_set = None
    else:
        off_set = offset - 10
    if n_offset == 0:
        btn.append([InlineKeyboardButton("⏪ BACK", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(f"📃 Pages {math.ceil(int(offset)/10)+1}/{math.ceil(total/10)}", callback_data="pages")])
    elif off_set is None:
        btn.append([InlineKeyboardButton(f"🗓 {math.ceil(int(offset)/10)+1}/{math.ceil(total/10)}", callback_data="pages"), InlineKeyboardButton("NEXT ⏩", callback_data=f"next_{req}_{key}_{n_offset}")])
    else:
        btn.append([InlineKeyboardButton("⏪ BACK", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(f"🗓 {math.ceil(int(offset)/10)+1}/{math.ceil(total/10)}", callback_data="pages"), InlineKeyboardButton("NEXT ⏩", callback_data=f"next_{req}_{key}_{n_offset}")])
    try:
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
    except MessageNotModified:
        pass
    await query.answer()


@Client.on_callback_query(filters.regex(r"^spolling"))
async def advantage_spoll_choker(bot, query):
    _, user, movie_ = query.data.split('#')
    if int(user) != 0 and query.from_user.id != int(user):
        return await query.answer("okDa", show_alert=True)
    if movie_ == "close_spellcheck":
        return await query.message.delete()
    movies = SPELL_CHECK.get(query.message.reply_to_message.id)
    if not movies:
        return await query.answer("You are clicking on an old button which is expired.", show_alert=True)
    movie = movies[(int(movie_))]
    await query.answer('Checking for Movie in database...')
    k = await manual_filters(bot, query.message, text=movie)
    if k == False:
        files, offset, total_results = await get_search_results(movie, offset=0, filter=True)
        if files:
            k = (movie, files, offset, total_results)
            await auto_filter(bot, query, k)
        else:
            k = await query.message.edit('⚠️Wait For OTT Release.')
            await asyncio.sleep(10)
            await k.delete()


@Client.on_callback_query()
async def cb_handler(client: Client, query: CallbackQuery):
    if query.data == "close_data":
        await query.message.delete()
    elif query.data == "delallconfirm":
        userid = query.from_user.id
        chat_type = query.message.chat.type
        if chat_type == enums.ChatType.PRIVATE:
            grpid = await active_connection(str(userid))
            if grpid is not None:
                grp_id = grpid
                try:
                    chat = await client.get_chat(grpid)
                    title = chat.title
                except:
                    await query.message.edit_text("Make sure I'm present in your group!!")
                    return await query.answer('Piracy Is Crime')
            else:
                await query.message.edit_text("I'm not connected to any groups!")
                return await query.answer('Piracy Is Crime')
        elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            grp_id = query.message.chat.id
            title = query.message.chat.title
        else:
            return await query.answer('Piracy Is Crime')
        st = await client.get_chat_member(grp_id, userid)
        if (st.status == enums.ChatMemberStatus.OWNER) or (str(userid) in ADMINS):
            await del_all(query.message, grp_id, title)
        else:
            await query.answer("You need to be Group Owner or an Auth User to do that!", show_alert=True)
    elif query.data == "delallcancel":
        userid = query.from_user.id
        chat_type = query.message.chat.type
        if chat_type == enums.ChatType.PRIVATE:
            await query.message.reply_to_message.delete()
            await query.message.delete()
        elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            grp_id = query.message.chat.id
            st = await client.get_chat_member(grp_id, userid)
            if (st.status == enums.ChatMemberStatus.OWNER) or (str(userid) in ADMINS):
                await query.message.delete()
                try:
                    await query.message.reply_to_message.delete()
                except:
                    pass
            else:
                await query.answer("That's not for you!!", show_alert=True)
    elif "groupcb" in query.data:
        await query.answer()
        group_id = query.data.split(":")[1]
        act = query.data.split(":")[2]
        hr = await client.get_chat(int(group_id))
        title = hr.title
        stat, cb = ("CONNECT", "connectcb") if act == "" else ("DISCONNECT", "disconnect")
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(f"{stat}", callback_data=f"{cb}:{group_id}"), InlineKeyboardButton("DELETE", callback_data=f"deletecb:{group_id}")], [InlineKeyboardButton("BACK", callback_data="backcb")]])
        await query.message.edit_text(f"Group Name : **{title}**\nGroup ID : `{group_id}`", reply_markup=keyboard, parse_mode=enums.ParseMode.MARKDOWN)
        return await query.answer('Piracy Is Crime')
    elif "connectcb" in query.data:
        await query.answer()
        group_id = query.data.split(":")[1]
        hr = await client.get_chat(int(group_id))
        title = hr.title
        mkact = await make_active(str(query.from_user.id), str(group_id))
        await query.message.edit_text(f"Connected to **{title}**" if mkact else 'Some error occurred!!', parse_mode=enums.ParseMode.MARKDOWN)
        return await query.answer('Piracy Is Crime')
    elif "disconnect" in query.data:
        await query.answer()
        group_id = query.data.split(":")[1]
        hr = await client.get_chat(int(group_id))
        title = hr.title
        mkinact = await make_inactive(str(query.from_user.id))
        await query.message.edit_text(f"Disconnected from **{title}**" if mkinact else "Some error occurred!!", parse_mode=enums.ParseMode.MARKDOWN)
        return await query.answer('Piracy Is Crime')
    elif "deletecb" in query.data:
        await query.answer()
        group_id = query.data.split(":")[1]
        delcon = await delete_connection(str(query.from_user.id), str(group_id))
        await query.message.edit_text("Successfully deleted connection" if delcon else "Some error occurred!!", parse_mode=enums.ParseMode.MARKDOWN)
        return await query.answer('Piracy Is Crime')
    elif query.data == "backcb":
        await query.answer()
        userid = query.from_user.id
        groupids = await all_connections(str(userid))
        if groupids is None:
            await query.message.edit_text("There are no active connections!!")
            return await query.answer('Piracy Is Crime')
        buttons = []
        for groupid in groupids:
            try:
                ttl = await client.get_chat(int(groupid))
                title = ttl.title
                active = await if_active(str(userid), str(groupid))
                act = " - ACTIVE" if active else ""
                buttons.append([InlineKeyboardButton(text=f"{title}{act}", callback_data=f"groupcb:{groupid}:{act}")])
            except:
                pass
        if buttons:
            await query.message.edit_text("Your connected group details ;\n\n", reply_markup=InlineKeyboardMarkup(buttons))
    elif "alertmessage" in query.data:
        grp_id = query.message.chat.id
        i = query.data.split(":")[1]
        keyword = query.data.split(":")[2]
        reply_text, btn, alerts, fileid = await find_filter(grp_id, keyword)
        if alerts is not None:
            alerts = ast.literal_eval(alerts)
            alert = alerts[int(i)].replace("\\n", "\n").replace("\\t", "\t")
            await query.answer(alert, show_alert=True)
    if query.data.startswith("file"):
        ident, file_id = query.data.split("#")
        # ── Dual Force Subscribe check ────────────────────
        from os import environ as _env
        ch1 = int(_env.get('AUTH_CHANNEL_1', -1003581625072))
        ch2 = int(_env.get('AUTH_CHANNEL_2', -1003514982115))
        not_joined = []
        for ch_id in [ch1, ch2]:
            try:
                member = await client.get_chat_member(ch_id, query.from_user.id)
                if member.status in [enums.ChatMemberStatus.LEFT, enums.ChatMemberStatus.BANNED]:
                    not_joined.append(ch_id)
            except Exception:
                not_joined.append(ch_id)
        if not_joined:
            btn = []
            for i, ch_id in enumerate(not_joined, 1):
                try:
                    invite = await client.create_chat_invite_link(ch_id)
                    link = invite.invite_link
                except Exception:
                    try:
                        chat = await client.get_chat(ch_id)
                        link = f"https://t.me/{chat.username}" if chat.username else "https://t.me"
                    except Exception:
                        link = "https://t.me"
                btn.append([InlineKeyboardButton(f"📢 Join Channel {i}", url=link)])
            btn.append([InlineKeyboardButton("✅ Try Again", callback_data=f"fsub_check#{ident}#{file_id}")])
            try:
                await client.send_message(
                    chat_id=query.from_user.id,
                    text=(
                        "⚠️ <b>You must join our channels to get files!</b>\n\n"
                        "Join the channels below, then click ✅ Try Again"
                    ),
                    reply_markup=InlineKeyboardMarkup(btn),
                    parse_mode=enums.ParseMode.HTML
                )
            except Exception:
                pass
            await query.answer("Please join our channels first!", show_alert=True)
            return
        # ─────────────────────────────────────────────────
        files_ = await get_file_details(file_id)
        if not files_:
            return await query.answer('No such file exist.')
        files = files_[0]
        title = files.file_name
        size = get_size(files.file_size)
        f_caption = files.caption
        settings = await get_settings(query.message.chat.id)
        if CUSTOM_FILE_CAPTION:
            try:
                f_caption = CUSTOM_FILE_CAPTION.format(file_name='' if title is None else title, file_size='' if size is None else size, file_caption='' if f_caption is None else f_caption)
            except Exception as e:
                logger.exception(e)
        if f_caption is None:
            f_caption = f"{files.file_name}"
        try:
            if AUTH_CHANNEL and not await is_subscribed(client, query):
                await query.answer(url=f"https://t.me/{temp.U_NAME}?start={ident}_{file_id}")
                return
            elif settings['botpm']:
                await query.answer(url=f"https://t.me/{temp.U_NAME}?start={ident}_{file_id}")
                return
            else:
                await client.send_cached_media(chat_id=query.from_user.id, file_id=file_id, caption=f_caption, protect_content=True if ident == "filep" else False)
                await query.answer('Check PM, I have sent files in pm', show_alert=True)
        except UserIsBlocked:
            await query.answer('Unblock the bot mahn !', show_alert=True)
        except PeerIdInvalid:
            await query.answer(url=f"https://t.me/{temp.U_NAME}?start={ident}_{file_id}")
        except Exception as e:
            await query.answer(url=f"https://t.me/{temp.U_NAME}?start={ident}_{file_id}")
    elif query.data.startswith("checksub"):
        if AUTH_CHANNEL and not await is_subscribed(client, query):
            await query.answer("I Like Your Smartness, But Don't Be Oversmart 😒", show_alert=True)
            return
        ident, file_id = query.data.split("#")
        files_ = await get_file_details(file_id)
        if not files_:
            return await query.answer('No such file exist.')
        files = files_[0]
        title = files.file_name
        size = get_size(files.file_size)
        f_caption = files.caption
        if CUSTOM_FILE_CAPTION:
            try:
                f_caption = CUSTOM_FILE_CAPTION.format(file_name='' if title is None else title, file_size='' if size is None else size, file_caption='' if f_caption is None else f_caption)
            except Exception as e:
                logger.exception(e)
                f_caption = f_caption
        if f_caption is None:
            f_caption = f"{title}"
        await query.answer()
        await client.send_cached_media(chat_id=query.from_user.id, file_id=file_id, caption=f_caption, protect_content=True if ident == 'checksubp' else False)
    elif query.data == "pages":
        await query.answer()
    elif query.data == "start":
        buttons = [[InlineKeyboardButton('➕ Add Me To Your Groups ➕', url=f'http://t.me/{temp.U_NAME}?startgroup=true')], [InlineKeyboardButton('Movie Search Group', url='https://t.me/+40mgi-EjhQdmNTk1'), InlineKeyboardButton('Movie Updates', url='https://t.me/+zW-bcv3QtgZjZTE9')]]
        await query.message.edit_text(text=script.START_TXT.format(query.from_user.mention, temp.U_NAME, temp.B_NAME), reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
        await query.answer('Piracy Is Crime')
    elif query.data == "help":
        buttons = [[InlineKeyboardButton('Manual Filter', callback_data='manuelfilter'), InlineKeyboardButton('Auto Filter', callback_data='autofilter')], [InlineKeyboardButton('Connection', callback_data='coct'), InlineKeyboardButton('Extra Mods', callback_data='extra')], [InlineKeyboardButton('🏠 Home', callback_data='start'), InlineKeyboardButton('🔮 Status', callback_data='stats')]]
        await query.message.edit_text(text=script.HELP_TXT.format(query.from_user.mention), reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    elif query.data == "about":
        buttons = [[InlineKeyboardButton('🤖 Updates', url='https://t.me/TeamEvamaria'), InlineKeyboardButton('♥️ Source', callback_data='source')], [InlineKeyboardButton('🏠 Home', callback_data='start'), InlineKeyboardButton('🔐 Close', callback_data='close_data')]]
        await query.message.edit_text(text=script.ABOUT_TXT.format(temp.B_NAME), reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    elif query.data == "source":
        await query.message.edit_text(text=script.SOURCE_TXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('👩‍🦯 Back', callback_data='about')]]), parse_mode=enums.ParseMode.HTML)
    elif query.data == "manuelfilter":
        await query.message.edit_text(text=script.MANUELFILTER_TXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('👩‍🦯 Back', callback_data='help'), InlineKeyboardButton('⏹️ Buttons', callback_data='button')]]), parse_mode=enums.ParseMode.HTML)
    elif query.data == "button":
        await query.message.edit_text(text=script.BUTTON_TXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('👩‍🦯 Back', callback_data='manuelfilter')]]), parse_mode=enums.ParseMode.HTML)
    elif query.data == "autofilter":
        await query.message.edit_text(text=script.AUTOFILTER_TXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('👩‍🦯 Back', callback_data='help')]]), parse_mode=enums.ParseMode.HTML)
    elif query.data == "coct":
        await query.message.edit_text(text=script.CONNECTION_TXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('👩‍🦯 Back', callback_data='help')]]), parse_mode=enums.ParseMode.HTML)
    elif query.data == "extra":
        await query.message.edit_text(text=script.EXTRAMOD_TXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('👩‍🦯 Back', callback_data='help'), InlineKeyboardButton('👮‍♂️ Admin', callback_data='admin')]]), parse_mode=enums.ParseMode.HTML)
    elif query.data == "admin":
        await query.message.edit_text(text=script.ADMIN_TXT, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('👩‍🦯 Back', callback_data='extra')]]), parse_mode=enums.ParseMode.HTML)
    elif query.data in ("stats", "rfrsh"):
        if query.data == "rfrsh":
            await query.answer("Fetching MongoDb DataBase")
        buttons = [[InlineKeyboardButton('👩‍🦯 Back', callback_data='help'), InlineKeyboardButton('♻️', callback_data='rfrsh')]]
        total = await Media.count_documents()
        users = await db.total_users_count()
        chats = await db.total_chat_count()
        monsize = await db.get_db_size()
        free = 536870912 - monsize
        await query.message.edit_text(text=script.STATUS_TXT.format(total, users, chats, get_size(monsize), get_size(free)), reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    elif query.data.startswith("setgs"):
        ident, set_type, status, grp_id = query.data.split("#")
        grpid = await active_connection(str(query.from_user.id))
        if str(grp_id) != str(grpid):
            await query.message.edit("Your Active Connection Has Been Changed. Go To /settings.")
            return await query.answer('Piracy Is Crime')
        await save_group_settings(grpid, set_type, False if status == "True" else True)
        settings = await get_settings(grpid)
        if settings is not None:
            buttons = [
                [InlineKeyboardButton('Filter Button', callback_data=f'setgs#button#{settings["button"]}#{grp_id}'), InlineKeyboardButton('Single' if settings["button"] else 'Double', callback_data=f'setgs#button#{settings["button"]}#{grp_id}')],
                [InlineKeyboardButton('Bot PM', callback_data=f'setgs#botpm#{settings["botpm"]}#{grp_id}'), InlineKeyboardButton('✅ Yes' if settings["botpm"] else '❌ No', callback_data=f'setgs#botpm#{settings["botpm"]}#{grp_id}')],
                [InlineKeyboardButton('File Secure', callback_data=f'setgs#file_secure#{settings["file_secure"]}#{grp_id}'), InlineKeyboardButton('✅ Yes' if settings["file_secure"] else '❌ No', callback_data=f'setgs#file_secure#{settings["file_secure"]}#{grp_id}')],
                [InlineKeyboardButton('IMDB', callback_data=f'setgs#imdb#{settings["imdb"]}#{grp_id}'), InlineKeyboardButton('✅ Yes' if settings["imdb"] else '❌ No', callback_data=f'setgs#imdb#{settings["imdb"]}#{grp_id}')],
                [InlineKeyboardButton('Spell Check', callback_data=f'setgs#spell_check#{settings["spell_check"]}#{grp_id}'), InlineKeyboardButton('✅ Yes' if settings["spell_check"] else '❌ No', callback_data=f'setgs#spell_check#{settings["spell_check"]}#{grp_id}')],
                [InlineKeyboardButton('Welcome', callback_data=f'setgs#welcome#{settings["welcome"]}#{grp_id}'), InlineKeyboardButton('✅ Yes' if settings["welcome"] else '❌ No', callback_data=f'setgs#welcome#{settings["welcome"]}#{grp_id}')]
            ]
            await query.message.edit_reply_markup(InlineKeyboardMarkup(buttons))
    await query.answer('Piracy Is Crime')


async def auto_filter(client, msg, spoll=False):
    if not spoll:
        message = msg
        if message.chat.type == enums.ChatType.PRIVATE:
            return await _auto_filter_direct(client, msg, spoll)
        settings = await get_settings(message.chat.id)
        if message.text.startswith("/"): return
        if re.findall(r"((^\/|^,|^!|^\.|^[\U0001F600-\U000E007F]).*)", message.text): return
        if not (2 < len(message.text) < 100): return
        search    = message.text
        cache_key = search.lower()
        if cache_key in _search_cache:
            files = _search_cache[cache_key]
        else:
            files, offset, total_results = await get_search_results(search.lower(), offset=0, filter=True)
            if not files:
                fuzzy = await fuzzy_search(search)
                if fuzzy and fuzzy != cache_key:
                    files = _search_cache[fuzzy]
                    sent = await message.reply(
                        f"🔍 No exact results for <b>{search}</b>\n✅ Showing results for: <b>{fuzzy.title()}</b>",
                        quote=True, parse_mode=enums.ParseMode.HTML
                    )
                    auto_delete(message, sent)
                    search    = fuzzy.title()
                    cache_key = fuzzy
                elif settings["spell_check"]:
                    return await advantage_spell_chok(msg)
                else:
                    return
            if not files:
                return
            files = deduplicate(files)
            _cache_set(cache_key, files)
        if not files:
            return
        state_id = str(message.id)
        filter_state[state_id] = {
            "query":    search,
            "files":    files,
            "total":    len(files),
            "chat":     message.chat.id,
            "settings": settings,
        }
        sent = await message.reply(
            build_header(search, files, "All", "All", "All", len(files)),
            reply_markup=build_full_keyboard(state_id, files, settings, "All", "All", "All", files),
            quote=True,
            parse_mode=enums.ParseMode.HTML
        )
        auto_delete(message, sent)
    else:
        await _auto_filter_direct(client, msg, spoll)


async def _auto_filter_direct(client, msg, spoll=False):
    if not spoll:
        message = msg
        settings = await get_settings(message.chat.id)
        if message.text.startswith("/"): return
        if re.findall(r"((^\/|^,|^!|^\.|^[\U0001F600-\U000E007F]).*)", message.text): return
        if 2 < len(message.text) < 100:
            search = message.text
            cache_key = search.lower()
            files, offset, total_results = await get_search_results(search.lower(), offset=0, filter=True)
            if not files:
                # ── Fuzzy spell check for PM too ──────────────
                fuzzy = await fuzzy_search(search)
                if fuzzy and fuzzy != cache_key and fuzzy in _search_cache:
                    files = _search_cache[fuzzy]
                    offset = ""
                    total_results = len(files)
                    await message.reply(
                        f"🔍 No exact results for <b>{search}</b>\n✅ Showing results for: <b>{fuzzy.title()}</b>",
                        quote=True, parse_mode=enums.ParseMode.HTML
                    )
                    search = fuzzy.title()
                elif settings["spell_check"]:
                    return await advantage_spell_chok(msg)
                else:
                    return
            if not files:
                return
        else:
            return
    else:
        settings = await get_settings(msg.message.chat.id)
        message = msg.message.reply_to_message
        search, files, offset, total_results = spoll
    pre = 'filep' if settings['file_secure'] else 'file'
    if settings["button"]:
        btn = [[InlineKeyboardButton(text=f"📂[{get_size(file.file_size)}]👉{file.file_name}", callback_data=f'{pre}#{file.file_id}')] for file in files]
    else:
        btn = [[InlineKeyboardButton(text=f"{file.file_name}", callback_data=f'{pre}#{file.file_id}'), InlineKeyboardButton(text=f"{get_size(file.file_size)}", callback_data=f'{pre}#{file.file_id}')] for file in files]
    if offset != "":
        key = f"{message.chat.id}-{message.id}"
        BUTTONS[key] = search
        req = message.from_user.id if message.from_user else 0
        btn.append([InlineKeyboardButton(text=f"🗓 1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="NEXT ⏩", callback_data=f"next_{req}_{key}_{offset}")])
    else:
        btn.append([InlineKeyboardButton(text="🗓 1/1", callback_data="pages")])
    imdb = await get_poster(search, file=(files[0]).file_name) if settings["imdb"] else None
    TEMPLATE = settings['template']
    if imdb:
        cap = TEMPLATE.format(query=search, title=imdb['title'], votes=imdb['votes'], aka=imdb["aka"], seasons=imdb["seasons"], box_office=imdb['box_office'], localized_title=imdb['localized_title'], kind=imdb['kind'], imdb_id=imdb["imdb_id"], cast=imdb["cast"], runtime=imdb["runtime"], countries=imdb["countries"], certificates=imdb["certificates"], languages=imdb["languages"], director=imdb["director"], writer=imdb["writer"], producer=imdb["producer"], composer=imdb["composer"], cinematographer=imdb["cinematographer"], music_team=imdb["music_team"], distributors=imdb["distributors"], release_date=imdb['release_date'], year=imdb['year'], genres=imdb['genres'], poster=imdb['poster'], plot=imdb['plot'], rating=imdb['rating'], url=imdb['url'], **locals())
    else:
        cap = f"Hey 👋 Buddy 😎 \n \nHere Is The Results For #{search}"
    if imdb and imdb.get('poster'):
        try:
            await message.reply_photo(photo=imdb.get('poster'), caption=cap[:1024], reply_markup=InlineKeyboardMarkup(btn))
        except (MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty):
            await message.reply_photo(photo=imdb.get('poster').replace('.jpg', "._V1_UX360.jpg"), caption=cap[:1024], reply_markup=InlineKeyboardMarkup(btn))
        except Exception as e:
            logger.exception(e)
            await message.reply_text(cap, reply_markup=InlineKeyboardMarkup(btn))
    else:
        dll = await message.reply_text(cap, reply_markup=InlineKeyboardMarkup(btn))
        await asyncio.sleep(60)
        try:
            fll = await dll.edit_text("<b>🗑️ Filter Deleted After 1 Min ‼️ \n 🔍Search Again !!</b>", parse_mode=enums.ParseMode.HTML)
            await asyncio.sleep(60)
            await fll.delete()
        except Exception:
            try:
                await dll.delete()
            except Exception:
                pass
        try:
            await message.delete()
        except Exception:
            pass
    if spoll:
        await msg.message.delete()


async def advantage_spell_chok(msg):
    query = re.sub(r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|br((o|u)h?)*|^h(e|a)?(l)*(o)*|mal(ayalam)?|t(h)?amil|file|that|find|und(o)*|kit(t(i|y)?)?o(w)?|thar(u)?(o)*w?|kittum(o)*|aya(k)*(um(o)*)?|full\smovie|any(one)|with\ssubtitle(s)?)", "", msg.text, flags=re.IGNORECASE)
    query = query.strip() + " movie"
    g_s = await search_gagala(query)
    g_s += await search_gagala(msg.text)
    if not g_s:
        k = await msg.reply("I couldn't find any movie in that name.")
        await asyncio.sleep(8)
        await k.delete()
        try: await msg.delete()
        except: pass
        return
    regex = re.compile(r".*(imdb|wikipedia).*", re.IGNORECASE)
    gs = list(filter(regex.match, g_s))
    gs_parsed = [re.sub(r'\b(\-([a-zA-Z-\s])\-\simdb|(\-\s)?imdb|(\-\s)?wikipedia|\(|\)|\-|reviews|full|all|episode(s)?|film|movie|series)', '', i, flags=re.IGNORECASE) for i in gs]
    if not gs_parsed:
        reg = re.compile(r"watch(\s[a-zA-Z0-9_\s\-\(\)]*)*\|.*", re.IGNORECASE)
        for mv in g_s:
            match = reg.match(mv)
            if match:
                gs_parsed.append(match.group(1))
    user = msg.from_user.id if msg.from_user else 0
    movielist = []
    gs_parsed = list(dict.fromkeys(gs_parsed))
    if len(gs_parsed) > 3: gs_parsed = gs_parsed[:3]
    if gs_parsed:
        for mov in gs_parsed:
            imdb_s = await get_poster(mov.strip(), bulk=True)
            if imdb_s:
                movielist += [movie.get('title') for movie in imdb_s]
    movielist += [(re.sub(r'(\-|\(|\)|_)', '', i, flags=re.IGNORECASE)).strip() for i in gs_parsed]
    movielist = list(dict.fromkeys(movielist))
    if not movielist:
        k = await msg.reply("I couldn't find anything related to that. Check your spelling")
        await asyncio.sleep(10)
        await k.delete()
        try: await msg.delete()
        except: pass
        return
    SPELL_CHECK[msg.id] = movielist
    btn = [[InlineKeyboardButton(text=movie.strip(), callback_data=f"spolling#{user}#{k}")] for k, movie in enumerate(movielist)]
    btn.append([InlineKeyboardButton(text="Close", callback_data=f'spolling#{user}#close_spellcheck')])
    dll = await msg.reply("I couldn't find anything related to that\nDid you mean any one of these?", reply_markup=InlineKeyboardMarkup(btn))
    await asyncio.sleep(10)
    await dll.delete()
    try: await msg.delete()
    except: pass


async def manual_filters(client, message, text=False):
    group_id = message.chat.id
    name = text or message.text
    reply_id = message.reply_to_message.id if message.reply_to_message else message.id
    keywords = await get_filters(group_id)
    for keyword in reversed(sorted(keywords, key=len)):
        pattern = r"( |^|[^\w])" + re.escape(keyword) + r"( |$|[^\w])"
        if re.search(pattern, name, flags=re.IGNORECASE):
            reply_text, btn, alert, fileid = await find_filter(group_id, keyword)
            if reply_text:
                reply_text = reply_text.replace("\\n", "\n").replace("\\t", "\t")
            if btn is not None:
                try:
                    if fileid == "None":
                        if btn == "[]":
                            dm = await client.send_message(group_id, reply_text, disable_web_page_preview=True, reply_to_message_id=reply_id)
                        else:
                            dm = await client.send_message(group_id, reply_text, disable_web_page_preview=True, reply_markup=InlineKeyboardMarkup(eval(btn)), reply_to_message_id=reply_id)
                    elif btn == "[]":
                        dm = await client.send_cached_media(group_id, fileid, caption=reply_text or "", reply_to_message_id=reply_id)
                    else:
                        dm = await message.reply_cached_media(fileid, caption=reply_text or "", reply_markup=InlineKeyboardMarkup(eval(btn)), reply_to_message_id=reply_id)
                    await asyncio.sleep(30)
                    await dm.delete()
                    try: await message.delete()
                    except: pass
                except Exception as e:
                    logger.exception(e)
                break
    else:
        return False
