# Kanged From @TroJanZheX
import asyncio
import re
import ast
import math
from difflib import get_close_matches
from os import environ as _env
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
logger.setLevel(logging.INFO)

BUTTONS = {}
SPELL_CHECK = {}

AUTO_DELETE_SECS = 300
filter_state  = {}
_search_cache   = {}
_fsub_cache   = {}   # {user_id: (timestamp, bool)} — cache fsub result 5 mins
_file_cache   = {}   # {file_id: file_obj} — cache file details to avoid repeated DB calls
_pending_files = {}  # {user_id: (ident, file_id)} — pending file for user after join approval
_pending_requests = {}  # {user_id: set(chat_ids)} — tracks channels user has requested to join
_settings_cache = {} # {chat_id: settings} — already in temp.SETTINGS but double-cache here

LANGUAGES = ["Malayalam", "Tamil", "Hindi", "English", "Telugu", "Kannada", "Multi Audio", "Dual Audio", "Korean", "Japanese", "Chinese", "Arabic", "French", "Spanish", "German"]
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
    "<i>Powered by Cinema Club™</i>"
)

QUALITY_PRIORITY = {"2160p": 5, "4k": 5, "1080p": 4, "720p": 3, "480p": 2, "360p": 1, "n/a": 0}
QUALITY_ORDER    = {"2160p": 0, "1080p": 1, "720p": 2, "480p": 3, "360p": 4}

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

def get_file_markup(file_id=None):
    """File buttons — reuse FILE_REPLY_MARKUP."""
    return FILE_REPLY_MARKUP

# ══════════════════════════════════════════════════════════
#  FEATURE HELPERS
# ══════════════════════════════════════════════════════════

import time as _time


# Movie Request Channel (changeable via env var)
REQUEST_CHANNEL_LINK = _env.get('REQUEST_CHANNEL_LINK', 'https://t.me/+vlrZcXNcmsA4Mjk1')

# Force subscribe channels — changeable by admin via /setchannel1 and /setchannel2
_CH1_DEFAULT = int(_env.get('AUTH_CHANNEL_1', -1003581625072))
_CH2_DEFAULT = int(_env.get('AUTH_CHANNEL_2', -1003514982115))

# Runtime storage — admin can change these without redeploying
_dynamic_channels = {
    "ch1": _CH1_DEFAULT,
    "ch2": _CH2_DEFAULT,
    "ch1_link": _env.get('CH1_LINK', 'https://t.me/+AngJ8lGmH4wwNWY1'),
    "ch2_link": _env.get('CH2_LINK', 'https://t.me/ccllinks'),
}

# Use properties via a simple accessor — always reads current value
def get_ch1(): return _dynamic_channels["ch1"]
def get_ch2(): return _dynamic_channels["ch2"]


# ══════════════════════════════════════════════════════════
#  AUTO DELETE
# ══════════════════════════════════════════════════════════

async def _delete_later(*msgs):
    await asyncio.sleep(AUTO_DELETE_SECS)
    for m in msgs:
        try:
            await m.delete()
        except Exception:
            pass

def auto_delete(*msgs):
    asyncio.create_task(_delete_later(*msgs))


# ══════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════

def detect_quality(fname):
    fl = fname.lower()
    for q in ["2160p", "4k", "1080p", "720p", "480p", "360p"]:
        if q in fl:
            return q
    return "n/a"

def detect_lang(fname):
    fl = fname.lower()
    # Multi audio check first
    if any(x in fl for x in ["multi", "multiaudio", "multi audio", "multi-audio"]):
        return "multi"
    if any(x in fl for x in ["dual", "dualaudio", "dual audio", "dual-audio"]):
        return "dual audio"
    # Full names
    LANG_MAP = {
        "malayalam": ["malayalam", "mallu", "mal"],
        "tamil":     ["tamil", "tam", "tam."],
        "hindi":     ["hindi", "hin"],
        "telugu":    ["telugu", "tel"],
        "kannada":   ["kannada", "kan"],
        "english":   ["english", "eng"],
        "korean":    ["korean", "kor"],
        "japanese":  ["japanese", "jap", "jpn"],
        "chinese":   ["chinese", "chi", "chn"],
        "arabic":    ["arabic", "ara"],
        "french":    ["french", "fre"],
        "spanish":   ["spanish", "spa"],
        "german":    ["german", "ger"],
    }
    for lang, keywords in LANG_MAP.items():
        for kw in keywords:
            # Match as word boundary to avoid false matches
            if re.search(r'(?<![a-z])' + re.escape(kw) + r'(?![a-z])', fl):
                return lang
    return "unknown"

def detect_season(fname):
    m = re.search(r'[Ss](\d{1,2})', fname)
    return f"S{int(m.group(1)):02d}" if m else ""

def is_series_file(fname):
    return bool(re.search(r'[Ss]\d{1,2}[Ee]\d{1,2}|[Ss]eason\s*\d|[Ee]pisode\s*\d|\bS\d{2}\b', fname or "", re.IGNORECASE))

def normalize_name(name):
    name = name.lower()
    name = re.sub(r'[\[\](){}@#$%^&*!.,;:\'"\\/-]', ' ', name)
    return re.sub(r'\s+', ' ', name).strip()

# Precompiled regex for deduplicate — compiled once at startup
_DEDUP_RE = re.compile(
    r'(19|20)\d{2}|2160p|1080p|720p|480p|360p|4k|hdrip|bluray|webrip|'
    r'hdtv|dvdrip|x264|x265|hevc|aac|hindi|tamil|malayalam|telugu|'
    r'kannada|english|multi|dubbed|web-dl|mkv|mp4|avi',
    re.IGNORECASE
)

def deduplicate(files):
    seen = {}
    for f in files:
        fname = f.file_name if hasattr(f, 'file_name') else ""
        if not fname:
            continue
        quality = detect_quality(fname)
        lang    = detect_lang(fname)
        clean   = normalize_name(_DEDUP_RE.sub(' ', fname.lower()))
        key     = (clean, lang, quality)
        prio    = QUALITY_PRIORITY.get(quality, 0)
        if key not in seen or prio > seen[key][1]:
            seen[key] = (f, prio)
    return [item[0] for item in seen.values()]

# Language display name → detect_lang return value mapping
LANG_FILTER_MAP = {
    "Malayalam":  "malayalam",
    "Tamil":      "tamil",
    "Hindi":      "hindi",
    "Telugu":     "telugu",
    "Kannada":    "kannada",
    "English":    "english",
    "Multi Audio": "multi",
    "Dual Audio": "dual audio",
    "Korean":     "korean",
    "Japanese":   "japanese",
    "Chinese":    "chinese",
    "Arabic":     "arabic",
    "French":     "french",
    "Spanish":    "spanish",
    "German":     "german",
}

def apply_filters(files, lang="All", qual="All", season="All", tab="All"):
    out = []
    # Get the internal lang key for matching
    lang_key = LANG_FILTER_MAP.get(lang) if lang != "All" else None
    for f in files:
        name = (f.file_name or "").lower()
        # Language filter — use detect_lang for accuracy
        if lang_key:
            detected = detect_lang(name)
            if detected != lang_key:
                continue
        if qual != "All" and qual.lower() not in name:
            continue
        if season != "All" and season.lower() not in name.replace(" ", ""):
            continue
        if tab == "MOVIE" and is_series_file(f.file_name or ""):
            continue
        if tab == "SERIES" and not is_series_file(f.file_name or ""):
            continue
        out.append(f)
    # Sort by file size ascending (smallest first)
    out.sort(key=lambda f: f.file_size if f.file_size else 0)
    return out

def get_seasons(files):
    return sorted({detect_season(f.file_name or "") for f in files if detect_season(f.file_name or "")})

def get_available_languages(files):
    found = set()
    for f in files:
        fname = (f.file_name or "").lower()
        lang = detect_lang(fname)
        if lang != "unknown":
            # Map detected lang to display name
            DISPLAY = {
                "malayalam":  "Malayalam",
                "tamil":      "Tamil",
                "hindi":      "Hindi",
                "telugu":     "Telugu",
                "kannada":    "Kannada",
                "english":    "English",
                "multi":      "Multi Audio",
                "dual audio": "Dual Audio",
                "korean":     "Korean",
                "japanese":   "Japanese",
                "chinese":    "Chinese",
                "arabic":     "Arabic",
                "french":     "French",
                "spanish":    "Spanish",
                "german":     "German",
            }
            display = DISPLAY.get(lang)
            if display:
                found.add(display)
    return sorted(found) if found else []

def get_available_qualities(files):
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


async def check_user_allowed(query, state_id):
    """Returns True if this user is allowed to use the filter buttons."""
    state = filter_state.get(state_id)
    if not state:
        await query.answer("Session expired. Search again.", show_alert=True)
        return False
    owner = state.get("user_id", 0)
    if owner and query.from_user.id != owner:
        await query.answer("These buttons are only for the person who searched.", show_alert=True)
        return False
    return True

async def fuzzy_search(query: str) -> str:
    """Try to find closest matching movie name from cache keys."""
    if not _search_cache:
        return ""
    q = query.lower().strip()
    # 55-60% accuracy threshold for spell check matching
    for cutoff in [0.6, 0.55]:
        matches = get_close_matches(q, _search_cache.keys(), n=1, cutoff=cutoff)
        if matches:
            return matches[0]
    return ""


async def db_fuzzy_search(query: str, client=None) -> list:
    """
    Search DB directly with partial/fuzzy query.
    Strips words one by one from end to find partial matches.
    E.g. 'Mnjumel Boys' → tries 'mnjumel boys', 'mnjumel', etc.
    """
    words = query.lower().strip().split()
    for i in range(len(words), 0, -1):
        partial = " ".join(words[:i])
        if len(partial) < 3:
            continue
        try:
            files, offset, total = await get_search_results(partial, offset=0, filter=True)
            if files:
                return files, partial
        except Exception:
            pass
    return [], query


# ══════════════════════════════════════════════════════════
#  FORCE SUBSCRIBE
# ══════════════════════════════════════════════════════════

async def get_file_details_cached(file_id):
    """Cached wrapper for get_file_details — avoids repeated MongoDB calls."""
    if file_id in _file_cache:
        return _file_cache[file_id]
    files_ = await get_file_details(file_id)
    if files_:
        # Cache max 500 entries
        if len(_file_cache) >= 500:
            # Remove oldest 100
            for k in list(_file_cache.keys())[:100]:
                del _file_cache[k]
        _file_cache[file_id] = files_
    return files_


async def check_fsub(client, user_id):
    """Check force subscribe with 5-min cache to avoid repeated API calls."""
    now = _time.time()
    cached = _fsub_cache.get(user_id)
    if cached and (now - cached[0]) < 300:  # 5 min cache
        return cached[1]
    not_joined = []
    ch1 = get_ch1()
    ch2 = get_ch2()
    channels = []
    if ch1:
        channels.append(ch1)
    if ch2 and ch2 != ch1:
        channels.append(ch2)
    results = await asyncio.gather(
        *[_check_single_channel(client, user_id, ch_id) for ch_id in channels],
        return_exceptions=True
    )
    for ch_id, result in zip(channels, results):
        if result is True:
            not_joined.append(ch_id)
    if len(_fsub_cache) > 1000:
        _fsub_cache.clear()
    _fsub_cache[user_id] = (now, not_joined)
    return not_joined

async def _check_single_channel(client, user_id, ch_id):
    """Returns True if user has NOT joined and has NOT sent a join request."""
    # Check actual membership first — existing members pass immediately
    try:
        member = await client.get_chat_member(ch_id, user_id)
        if member.status in [
            enums.ChatMemberStatus.MEMBER,
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER,
            enums.ChatMemberStatus.RESTRICTED,
        ]:
            return False  # already a member ✅
        # Not a member — check if they sent a join request
        requested = _pending_requests.get(user_id, set())
        if ch_id in requested:
            return False  # join request sent ✅
        return True  # not joined, no request
    except Exception as e:
        err = str(e).lower()
        if "user_not_participant" in err:
            # Confirmed not a member — check pending request
            requested = _pending_requests.get(user_id, set())
            if ch_id in requested:
                return False  # join request sent ✅
            return True  # not joined, no request
        if "peer_id_invalid" in err or "peer id invalid" in err:
            return False  # can't check, don't block
        if "chat_admin_required" in err or "not enough rights" in err:
            return False  # can't check, don't block
        return False

def invalidate_fsub_cache(user_id):
    """Clears fsub cache so next check is fresh."""
    _fsub_cache.pop(user_id, None)

def clear_pending_requests(user_id):
    """Remove pending requests after file is sent — prevents reuse."""
    _pending_requests.pop(user_id, None)
    _pending_files.pop(user_id, None)

def CH_LINKS():
    return {
        get_ch1(): _dynamic_channels["ch1_link"],
        get_ch2(): _dynamic_channels["ch2_link"],
    }

# Cache invite links to avoid regenerating every time
_invite_link_cache = {}  # {ch_id: link}

async def get_invite_link(client, ch_id):
    """Get join request invite link with cache."""
    # Return cached link if exists
    if ch_id in _invite_link_cache:
        return _invite_link_cache[ch_id]
    link = None
    try:
        invite = await client.create_chat_invite_link(
            ch_id,
            creates_join_request=True
        )
        link = invite.invite_link
        logger.info(f"Created join request link for {ch_id}: {link}")
        _invite_link_cache[ch_id] = link
    except Exception as e1:
        logger.warning(f"create_chat_invite_link (join_request) failed for {ch_id}: {e1}")
        try:
            invite = await client.create_chat_invite_link(ch_id)
            link = invite.invite_link
            _invite_link_cache[ch_id] = link
        except Exception as e2:
            logger.warning(f"create_chat_invite_link failed for {ch_id}: {e2}")
            try:
                chat = await client.get_chat(ch_id)
                if chat.username:
                    link = f"https://t.me/{chat.username}"
                    _invite_link_cache[ch_id] = link
            except Exception as e3:
                logger.warning(f"get_chat failed for {ch_id}: {e3}")
                link = CH_LINKS().get(ch_id)
    if not link:
        # Use per-channel configured link as final fallback
        ch_links = CH_LINKS()
        link = ch_links.get(ch_id)
        if not link:
            # Last resort: pick ch1 or ch2 link based on which channel this is
            if ch_id == _dynamic_channels.get("ch1"):
                link = _dynamic_channels.get("ch1_link", "https://t.me/+AngJ8lGmH4wwNWY1")
            else:
                link = _dynamic_channels.get("ch2_link", "https://t.me/ccllinks")
    return link


async def get_fsub_keyboard(client, not_joined, ident, file_id):
    btn = []
    # Always show BOTH channel buttons regardless of which ones are not joined
    ch1 = get_ch1()
    ch2 = get_ch2()
    ch1_link = await get_invite_link(client, ch1) if ch1 else _dynamic_channels.get("ch1_link", "https://t.me")
    ch2_link = await get_invite_link(client, ch2) if ch2 and ch2 != ch1 else _dynamic_channels.get("ch2_link", "https://t.me")
    btn.append([InlineKeyboardButton("📨 Join Channel 1", url=ch1_link)])
    btn.append([InlineKeyboardButton("📨 Join Channel 2", url=ch2_link)])
    btn.append([InlineKeyboardButton("✅ I Joined — Send My File", callback_data=f"fsub_check#{ident}#{file_id}")])
    return InlineKeyboardMarkup(btn)


# ══════════════════════════════════════════════════════════
#  KEYBOARD BUILDERS
# ══════════════════════════════════════════════════════════

def build_full_keyboard(state_id, filtered, settings, sel_lang="All", sel_qual="All", sel_season="All", all_files=None, sel_tab="All"):
    rows = []
    base_files = all_files or filtered
    pre = 'filep' if settings.get('file_secure') else 'file'

    # ── Row 1: ⚡ Check Bot PM ⚡ ─────────────────────────
    rows.append([
        InlineKeyboardButton("⚡ Check Bot PM ⚡", url=f"https://t.me/{temp.U_NAME}")
    ])

    # ── Row 2: Send All + Languages menu ─────────────────
    rows.append([
        InlineKeyboardButton("! Send All To PM !", callback_data=f"nf_sendall#{state_id}#{sel_lang}#{sel_qual}#{sel_season}#{sel_tab}"),
        InlineKeyboardButton("! Languages !",      callback_data=f"nf_langmenu#{state_id}#{sel_lang}#{sel_qual}#{sel_season}#{sel_tab}"),
    ])

    # ── Row 3: INFO | MOVIE | SERIES tabs ────────────────
    rows.append([
        InlineKeyboardButton(("✅ " if sel_tab == "INFO"   else "") + "INFO",   callback_data=f"nf_tab#{state_id}#{sel_lang}#{sel_qual}#{sel_season}#INFO"),
        InlineKeyboardButton(("✅ " if sel_tab == "MOVIE"  else "") + "MOVIE",  callback_data=f"nf_tab#{state_id}#{sel_lang}#{sel_qual}#{sel_season}#MOVIE"),
        InlineKeyboardButton(("✅ " if sel_tab == "SERIES" else "") + "SERIES", callback_data=f"nf_tab#{state_id}#{sel_lang}#{sel_qual}#{sel_season}#SERIES"),
    ])

    # ── Row 4: Seasons (only if series) ──────────────────
    seasons = get_seasons(base_files)
    if seasons:
        season_row = [
            InlineKeyboardButton(
                ("✅ " if s == sel_season else "") + s,
                callback_data=f"nf_season#{state_id}#{sel_lang}#{sel_qual}#{s}#{sel_tab}"
            ) for s in seasons[:4]
        ]
        season_row.append(InlineKeyboardButton(
            ("✅ " if sel_season == "All" else "") + "All",
            callback_data=f"nf_season#{state_id}#{sel_lang}#{sel_qual}#All#{sel_tab}"
        ))
        rows.append(season_row)

    # ── Row 5: How to Download + Close ───────────────────
    rows.append([
        InlineKeyboardButton("📥 How to Download", callback_data=f"nf_howdl#{state_id}"),
        InlineKeyboardButton("✖ Close",            callback_data="nf_close"),
    ])

    # ── File buttons with [size] prefix ──────────────────
    if not filtered:
        rows.append([InlineKeyboardButton("❌ No files found. Try another filter.", callback_data="nf_noop")])
    else:
        for f in filtered[:10]:
            fname = f.file_name or "Unknown"
            size  = get_size(f.file_size) if hasattr(f, 'file_size') and f.file_size else ""
            label = f"[{size}] {fname[:40]}" if size else fname[:48]
            rows.append([InlineKeyboardButton(label, url=f"https://t.me/{temp.U_NAME}?start={pre}_{f.file_id}")])

    return InlineKeyboardMarkup(rows)


def build_lang_keyboard(state_id, sel_lang, sel_qual, sel_season, sel_tab, base_files):
    """Language + Quality filter menu shown when ! Languages ! is tapped."""
    rows = []

    avail_langs = get_available_languages(base_files)
    if avail_langs:
        lang_row = [
            InlineKeyboardButton(
                ("✅ " if l == sel_lang else "") + l,
                callback_data=f"nf_lang#{state_id}#{l}#{sel_qual}#{sel_season}#{sel_tab}"
            ) for l in avail_langs
        ]
        lang_row.append(InlineKeyboardButton(
            ("✅ " if sel_lang == "All" else "") + "All",
            callback_data=f"nf_lang#{state_id}#All#{sel_qual}#{sel_season}#{sel_tab}"
        ))
        rows.append(lang_row)

    avail_quals = get_available_qualities(base_files)
    if avail_quals:
        qual_row = [
            InlineKeyboardButton(
                ("✅ " if q == sel_qual else "") + q,
                callback_data=f"nf_qual#{state_id}#{sel_lang}#{q}#{sel_season}#{sel_tab}"
            ) for q in avail_quals
        ]
        qual_row.append(InlineKeyboardButton(
            ("✅ " if sel_qual == "All" else "") + "All",
            callback_data=f"nf_qual#{state_id}#{sel_lang}#All#{sel_season}#{sel_tab}"
        ))
        rows.append(qual_row)

    rows.append([InlineKeyboardButton("◀️ Back", callback_data=f"nf_back#{state_id}#{sel_lang}#{sel_qual}#{sel_season}#{sel_tab}")])
    return InlineKeyboardMarkup(rows)


def build_header(query, filtered, sel_lang, sel_qual, sel_season, total, sel_tab="All"):
    active = " | ".join(x for x in [sel_lang, sel_qual, sel_season, sel_tab] if x != "All")
    text = (
        f"🔍 <b>Results for:</b> <i>{query}</i>\n"
        f"📦 <b>Found:</b> {total} file(s)"
    )
    if active:
        text += f"\n🎯 <b>Filter:</b> {active} → {len(filtered)} result(s)"
    return text


# ══════════════════════════════════════════════════════════
#  MAIN HANDLER
# ══════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════
#  USER COMMANDS
# ══════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════
#  ADMIN CHANNEL MANAGEMENT COMMANDS
# ══════════════════════════════════════════════════════════

@Client.on_message(filters.command("channels") & filters.private & filters.user(ADMINS))
async def show_channels(client, message):
    ch1 = _dynamic_channels["ch1"]
    ch2 = _dynamic_channels["ch2"]
    ch1_link = _dynamic_channels["ch1_link"]
    ch2_link = _dynamic_channels["ch2_link"]
    try:
        chat1 = await client.get_chat(ch1)
        name1 = chat1.title
    except Exception:
        name1 = "Unknown"
    try:
        chat2 = await client.get_chat(ch2)
        name2 = chat2.title
    except Exception:
        name2 = "Unknown"
    text = (
        f"📢 <b>Current Force Subscribe Channels</b>\n\n"
        f"<b>Channel 1:</b>\n"
        f"• Name: {name1}\n"
        f"• ID: <code>{ch1}</code>\n"
        f"• Link: {ch1_link}\n\n"
        f"<b>Channel 2:</b>\n"
        f"• Name: {name2}\n"
        f"• ID: <code>{ch2}</code>\n"
        f"• Link: {ch2_link}\n\n"
        f"<b>Commands to change:</b>\n"
        f"/setchannel1 &lt;channel_id&gt; &lt;join_link&gt;\n"
        f"/setchannel2 &lt;channel_id&gt; &lt;join_link&gt;\n\n"
        f"<b>Example:</b>\n"
        f"<code>/setchannel1 -1001234567890 https://t.me/+xxxxx</code>"
    )
    await message.reply(text, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("setchannel1") & filters.private & filters.user(ADMINS))
async def set_channel1(client, message):
    args = message.command[1:]
    if len(args) < 1:
        return await message.reply(
            "❌ Usage: <code>/setchannel1 -1001234567890 https://t.me/+xxxxx</code>\n\n"
            "Channel ID is required. Join link is optional.",
            parse_mode=enums.ParseMode.HTML
        )
    try:
        ch_id = int(args[0])
    except ValueError:
        return await message.reply("❌ Invalid channel ID. Must be a number like <code>-1001234567890</code>", parse_mode=enums.ParseMode.HTML)
    
    # Verify bot can access the channel
    try:
        chat = await client.get_chat(ch_id)
        name = chat.title
    except Exception as e:
        return await message.reply(f"❌ Cannot access channel: {e}\n\nMake sure bot is admin in that channel.", parse_mode=enums.ParseMode.HTML)
    
    _dynamic_channels["ch1"] = ch_id
    if len(args) >= 2:
        _dynamic_channels["ch1_link"] = args[1]
    # Invalidate all caches
    _fsub_cache.clear()
    _invite_link_cache.clear()
    await message.reply(
        f"✅ <b>Channel 1 updated!</b>\n\n"
        f"• Name: {name}\n"
        f"• ID: <code>{ch_id}</code>\n"
        f"• Link: {_dynamic_channels['ch1_link']}",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command("setchannel2") & filters.private & filters.user(ADMINS))
async def set_channel2(client, message):
    args = message.command[1:]
    if len(args) < 1:
        return await message.reply(
            "❌ Usage: <code>/setchannel2 -1001234567890 https://t.me/+xxxxx</code>\n\n"
            "Channel ID is required. Join link is optional.",
            parse_mode=enums.ParseMode.HTML
        )
    try:
        ch_id = int(args[0])
    except ValueError:
        return await message.reply("❌ Invalid channel ID. Must be a number like <code>-1001234567890</code>", parse_mode=enums.ParseMode.HTML)
    
    try:
        chat = await client.get_chat(ch_id)
        name = chat.title
    except Exception as e:
        return await message.reply(f"❌ Cannot access channel: {e}\n\nMake sure bot is admin in that channel.", parse_mode=enums.ParseMode.HTML)
    
    _dynamic_channels["ch2"] = ch_id
    if len(args) >= 2:
        _dynamic_channels["ch2_link"] = args[1]
    _fsub_cache.clear()
    _invite_link_cache.clear()
    await message.reply(
        f"✅ <b>Channel 2 updated!</b>\n\n"
        f"• Name: {name}\n"
        f"• ID: <code>{ch_id}</code>\n"
        f"• Link: {_dynamic_channels['ch2_link']}",
        parse_mode=enums.ParseMode.HTML
    )


@Client.on_message(filters.command("setrequestlink") & filters.private & filters.user(ADMINS))
async def set_request_link(client, message):
    global REQUEST_CHANNEL_LINK
    args = message.command[1:]
    if not args:
        return await message.reply(
            f"Current request link: {REQUEST_CHANNEL_LINK}\n\n"
            f"Usage: <code>/setrequestlink https://t.me/+xxxxx</code>",
            parse_mode=enums.ParseMode.HTML
        )
    REQUEST_CHANNEL_LINK = args[0]
    await message.reply(f"✅ Request channel link updated to:\n{REQUEST_CHANNEL_LINK}")


# ══════════════════════════════════════════════════════════
#  USER STAT COMMANDS
# ══════════════════════════════════════════════════════════

_user_stats = {}  # {user_id: {"searches": int, "downloads": int, "history": []}}
_trending   = {}  # {query: count}

def _track_search(user_id, query):
    if user_id not in _user_stats:
        _user_stats[user_id] = {"searches": 0, "downloads": 0, "history": []}
    _user_stats[user_id]["searches"] += 1
    history = _user_stats[user_id]["history"]
    if query not in history:
        history.insert(0, query)
    _user_stats[user_id]["history"] = history[:10]
    q = query.lower().strip()
    _trending[q] = _trending.get(q, 0) + 1

def _track_download(user_id):
    if user_id not in _user_stats:
        _user_stats[user_id] = {"searches": 0, "downloads": 0, "history": []}
    _user_stats[user_id]["downloads"] += 1

def get_trending(n=10):
    return sorted(_trending.items(), key=lambda x: x[1], reverse=True)[:n]


@Client.on_message(filters.command("mystats") & filters.incoming)
async def my_stats(client, message):
    uid = message.from_user.id
    stats = _user_stats.get(uid, {"searches": 0, "downloads": 0, "history": []})
    history_text = "\n".join(f"• {q.title()}" for q in stats["history"][:5]) if stats["history"] else "No searches yet."
    text = (
        f"📊 <b>Your Statistics</b>\n\n"
        f"🔍 <b>Total Searches:</b> {stats['searches']}\n"
        f"📥 <b>Total Downloads:</b> {stats['downloads']}\n\n"
        f"🕐 <b>Recent Searches:</b>\n{history_text}"
    )
    await message.reply(text, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("trending") & filters.incoming)
async def trending_movies(client, message):
    top = get_trending(10)
    if not top:
        return await message.reply("No trending searches yet.")
    text = "🔥 <b>Trending Searches</b>\n\n"
    for i, (query, count) in enumerate(top, 1):
        text += f"{i}. {query.title()} — <code>{count}</code> searches\n"
    await message.reply(text, parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("history") & filters.incoming)
async def search_history(client, message):
    uid = message.from_user.id
    stats = _user_stats.get(uid, {"history": []})
    if not stats["history"]:
        return await message.reply("You have no search history yet.")
    text = "🕐 <b>Your Search History</b>\n\n"
    for i, q in enumerate(stats["history"], 1):
        text += f"{i}. {q.title()}\n"
    await message.reply(text, parse_mode=enums.ParseMode.HTML)


# ══════════════════════════════════════════════════════════
#  INLINE SEARCH — @botname moviename
# ══════════════════════════════════════════════════════════

@Client.on_inline_query()
async def inline_search(client, inline_query):
    query = inline_query.query.strip()
    if not query or len(query) < 3:
        await inline_query.answer(
            results=[],
            cache_time=0,
            switch_pm_text="Type a movie name to search...",
            switch_pm_parameter="start"
        )
        return
    try:
        from pyrogram.types import InlineQueryResultArticle, InputTextMessageContent
        files, _, total = await get_search_results(query.lower(), offset=0, filter=True)
        if not files:
            await inline_query.answer(
                results=[],
                cache_time=0,
                switch_pm_text=f"No results for '{query}'",
                switch_pm_parameter="start"
            )
            return
        results = []
        for f in files[:10]:
            fname = f.file_name or "Unknown"
            size  = get_size(f.file_size) if f.file_size else "Unknown"
            q     = detect_quality(fname).upper()
            results.append(InlineQueryResultArticle(
                title=fname[:60],
                description=f"📦 {size} | 🎬 {q}",
                input_message_content=InputTextMessageContent(
                    f"🎬 <b>{fname}</b>\n📦 Size: {size}\n\n<i>Search on @{temp.U_NAME}</i>",
                    parse_mode=enums.ParseMode.HTML
                ),
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔍 Get File", url=f"https://t.me/{temp.U_NAME}?start=file_{f.file_id}")
                ]])
            ))
        await inline_query.answer(results=results, cache_time=10)
    except Exception as e:
        logger.exception(e)


@Client.on_chat_join_request()
async def join_request_handler(client, update):
    """
    Track join requests in _pending_requests.
    No auto-approval — user must click I Joined button.
    """
    try:
        user_id = update.from_user.id
        chat_id = update.chat.id
        if user_id not in _pending_requests:
            _pending_requests[user_id] = set()
        _pending_requests[user_id].add(chat_id)
        invalidate_fsub_cache(user_id)
        # Cap memory
        if len(_pending_requests) > 5000:
            for uid in list(_pending_requests.keys())[:1000]:
                del _pending_requests[uid]
    except Exception as e:
        logger.exception(e)


@Client.on_message((filters.group | filters.private) & filters.text & filters.incoming)
async def give_filter(client, message):
    k = await manual_filters(client, message)
    if k == False:
        await auto_filter(client, message)


# ══════════════════════════════════════════════════════════
#  FILTER CALLBACKS
# ══════════════════════════════════════════════════════════

@Client.on_callback_query(filters.regex(r"^nf_howdl#"))
async def nf_howdl_cb(client, query):
    _, state_id = query.data.split("#", 1)
    if not await check_user_allowed(query, state_id):
        return
    await query.answer()
    # Add a Back button to return to results
    back_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("◀️ Back to Results", callback_data=f"nf_back_howdl#{state_id}")
    ]])
    try:
        await query.message.edit_text(
            HOW_TO_DL_TEXT,
            reply_markup=back_kb,
            parse_mode=enums.ParseMode.HTML
        )
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^nf_back_howdl#"))
async def nf_back_howdl_cb(client, query):
    _, state_id = query.data.split("#", 1)
    if not await check_user_allowed(query, state_id):
        return
    state    = filter_state[state_id]
    settings = state.get("settings") or await get_settings(state["chat"])
    filtered = apply_filters(state["files"])
    kb = build_full_keyboard(state_id, filtered, settings, "All", "All", "All", state["files"], "All")
    try:
        await query.message.edit_text(
            build_header(state["query"], filtered, "All", "All", "All", state["total"], "All"),
            reply_markup=kb,
            parse_mode=enums.ParseMode.HTML
        )
    except Exception:
        pass
    await query.answer()


@Client.on_callback_query(filters.regex(r"^nf_langmenu#"))
async def nf_langmenu_cb(client, query):
    parts = query.data.split("#")
    state_id = parts[1]
    sel_lang = parts[2] if len(parts) > 2 else "All"
    sel_qual = parts[3] if len(parts) > 3 else "All"
    sel_season = parts[4] if len(parts) > 4 else "All"
    sel_tab = parts[5] if len(parts) > 5 else "All"
    if not await check_user_allowed(query, state_id):
        return
    state = filter_state[state_id]
    kb = build_lang_keyboard(state_id, sel_lang, sel_qual, sel_season, sel_tab, state["files"])
    try:
        await query.message.edit_reply_markup(kb)
    except Exception:
        pass
    await query.answer("Select language or quality")


@Client.on_callback_query(filters.regex(r"^nf_back#"))
async def nf_back_cb(client, query):
    parts = query.data.split("#")
    state_id = parts[1]
    sel_lang = parts[2] if len(parts) > 2 else "All"
    sel_qual = parts[3] if len(parts) > 3 else "All"
    sel_season = parts[4] if len(parts) > 4 else "All"
    sel_tab = parts[5] if len(parts) > 5 else "All"
    if not await check_user_allowed(query, state_id):
        return
    state    = filter_state[state_id]
    filtered = apply_filters(state["files"], lang=sel_lang, qual=sel_qual, season=sel_season, tab=sel_tab)
    settings = state.get("settings") or await get_settings(state["chat"])
    kb = build_full_keyboard(state_id, filtered, settings, sel_lang, sel_qual, sel_season, state["files"], sel_tab)
    try:
        await query.message.edit_reply_markup(kb)
    except Exception:
        pass
    await query.answer()


@Client.on_callback_query(filters.regex(r"^nf_lang#"))
async def nf_lang_cb(client, query):
    parts = query.data.split("#")
    state_id = parts[1]
    lang     = parts[2] if len(parts) > 2 else "All"
    qual     = parts[3] if len(parts) > 3 else "All"
    season   = parts[4] if len(parts) > 4 else "All"
    tab      = parts[5] if len(parts) > 5 else "All"
    if not await check_user_allowed(query, state_id):
        return
    state    = filter_state[state_id]
    filtered = apply_filters(state["files"], lang=lang, qual=qual, season=season, tab=tab)
    settings = state.get("settings") or await get_settings(state["chat"])
    new_text = build_header(state["query"], filtered, lang, qual, season, state["total"], tab)
    new_kb   = build_full_keyboard(state_id, filtered, settings, lang, qual, season, state["files"], tab)
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
    parts = query.data.split("#")
    state_id = parts[1]
    lang     = parts[2] if len(parts) > 2 else "All"
    qual     = parts[3] if len(parts) > 3 else "All"
    season   = parts[4] if len(parts) > 4 else "All"
    tab      = parts[5] if len(parts) > 5 else "All"
    if not await check_user_allowed(query, state_id):
        return
    state    = filter_state[state_id]
    filtered = apply_filters(state["files"], lang=lang, qual=qual, season=season, tab=tab)
    settings = state.get("settings") or await get_settings(state["chat"])
    new_text = build_header(state["query"], filtered, lang, qual, season, state["total"], tab)
    new_kb   = build_full_keyboard(state_id, filtered, settings, lang, qual, season, state["files"], tab)
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
    parts = query.data.split("#")
    state_id = parts[1]
    lang     = parts[2] if len(parts) > 2 else "All"
    qual     = parts[3] if len(parts) > 3 else "All"
    season   = parts[4] if len(parts) > 4 else "All"
    tab      = parts[5] if len(parts) > 5 else "All"
    if not await check_user_allowed(query, state_id):
        return
    state    = filter_state[state_id]
    filtered = apply_filters(state["files"], lang=lang, qual=qual, season=season, tab=tab)
    settings = state.get("settings") or await get_settings(state["chat"])
    new_text = build_header(state["query"], filtered, lang, qual, season, state["total"], tab)
    new_kb   = build_full_keyboard(state_id, filtered, settings, lang, qual, season, state["files"], tab)
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


@Client.on_callback_query(filters.regex(r"^nf_tab#"))
async def nf_tab_cb(client, query):
    parts = query.data.split("#")
    state_id = parts[1]
    lang     = parts[2] if len(parts) > 2 else "All"
    qual     = parts[3] if len(parts) > 3 else "All"
    season   = parts[4] if len(parts) > 4 else "All"
    tab      = parts[5] if len(parts) > 5 else "All"
    if not await check_user_allowed(query, state_id):
        return
    state    = filter_state[state_id]
    settings = state.get("settings") or await get_settings(state["chat"])

    if tab == "INFO":
        await query.answer(
            "⚠ INFORMATION ⚠\n\n"
            "AFTER 5 MINUTES THIS MESSAGE WILL BE AUTOMATICALLY DELETED\n\n"
            "IF YOU DO NOT SEE THE REQUESTED MOVIE / SERIES FILE, LOOK AT THE NEXT PAGE",
            show_alert=True
        )
        return

    if tab == "MOVIE":
        await query.answer(
            "✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦\n"
            "MOVIE REQUEST FORMAT\n"
            "✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦\n\n"
            "GO TO GOOGLE ➡ TYPE MOVIE NAME ➡ COPY CORRECT NAME ➡ PASTE THIS GROUP\n\n"
            "EXAMPLE : Uncharted\n\n"
            "🚫 DONT USE ➡ \':(|,./)",
            show_alert=True
        )
        # Still apply MOVIE filter to files
        filtered = apply_filters(state["files"], lang=lang, qual=qual, season=season, tab=tab)
        new_text = build_header(state["query"], filtered, lang, qual, season, state["total"], tab)
        new_kb   = build_full_keyboard(state_id, filtered, settings, lang, qual, season, state["files"], tab)
        try:
            await query.message.edit_text(new_text, reply_markup=new_kb, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            try:
                await query.message.edit_reply_markup(new_kb)
            except Exception:
                pass
        except Exception:
            pass
        return

    if tab == "SERIES":
        await query.answer(
            "✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦\n"
            "SERIES REQUEST FORMAT\n"
            "✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦✦\n\n"
            "GO TO GOOGLE ➡ TYPE SERIES NAME ➡ COPY CORRECT NAME ➡ PASTE THIS GROUP\n\n"
            "EXAMPLE : Loki S01E01\n\n"
            "🚫 DONT USE ➡ \':(|,./)",
            show_alert=True
        )
        # Still apply SERIES filter to files
        filtered = apply_filters(state["files"], lang=lang, qual=qual, season=season, tab=tab)
        new_text = build_header(state["query"], filtered, lang, qual, season, state["total"], tab)
        new_kb   = build_full_keyboard(state_id, filtered, settings, lang, qual, season, state["files"], tab)
        try:
            await query.message.edit_text(new_text, reply_markup=new_kb, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            try:
                await query.message.edit_reply_markup(new_kb)
            except Exception:
                pass
        except Exception:
            pass
        return

    filtered = apply_filters(state["files"], lang=lang, qual=qual, season=season, tab=tab)
    new_text = build_header(state["query"], filtered, lang, qual, season, state["total"], tab)
    new_kb   = build_full_keyboard(state_id, filtered, settings, lang, qual, season, state["files"], tab)
    try:
        await query.message.edit_text(new_text, reply_markup=new_kb, parse_mode=enums.ParseMode.HTML)
    except MessageNotModified:
        try:
            await query.message.edit_reply_markup(new_kb)
        except Exception:
            pass
    except Exception:
        pass
    await query.answer(f"Tab: {tab}")


@Client.on_callback_query(filters.regex(r"^nf_sendall#"))
async def nf_sendall_cb(client, query):
    parts    = query.data.split("#")
    state_id = parts[1]
    sel_lang = parts[2] if len(parts) > 2 else "All"
    sel_qual = parts[3] if len(parts) > 3 else "All"
    sel_season = parts[4] if len(parts) > 4 else "All"
    sel_tab  = parts[5] if len(parts) > 5 else "All"
    if not await check_user_allowed(query, state_id):
        return
    state    = filter_state[state_id]
    filtered = apply_filters(state["files"], lang=sel_lang, qual=sel_qual, season=sel_season, tab=sel_tab)
    if not filtered:
        return await query.answer("No files to send.", show_alert=True)

    # Force sub check before sending — always fresh
    invalidate_fsub_cache(query.from_user.id)
    not_joined = await check_fsub(client, query.from_user.id)
    if not_joined:
        kb = await get_fsub_keyboard(client, not_joined, "file", filtered[0].file_id)
        try:
            await client.send_message(
                chat_id=query.from_user.id,
                text="⚠️ <b>Request to join both channels first!</b>\nOnce approved, you can receive files.",
                reply_markup=kb,
                parse_mode=enums.ParseMode.HTML
            )
        except Exception:
            pass
        return await query.answer("Join channels first!", show_alert=True)

    await query.answer(f"Sending {min(len(filtered), 10)} files to your PM...", show_alert=True)
    settings = state.get("settings") or await get_settings(state["chat"])
    pre = 'filep' if settings.get('file_secure') else 'file'
    for f in filtered[:10]:
        try:
            f_caption = None
            if CUSTOM_FILE_CAPTION:
                try:
                    f_caption = CUSTOM_FILE_CAPTION.format(
                        file_name=f.file_name or '',
                        file_size=get_size(f.file_size) if f.file_size else '',
                        file_caption=''
                    )
                except Exception:
                    pass
            if not f_caption:
                f_caption = f.file_name or ""
            await client.send_cached_media(
                chat_id=query.from_user.id,
                file_id=f.file_id,
                caption=f_caption,
                protect_content=True if pre == 'filep' else False,
                reply_markup=FILE_REPLY_MARKUP
            )
            await asyncio.sleep(0.5)
        except UserIsBlocked:
            break
        except Exception as e:
            logger.exception(e)
            continue


@Client.on_callback_query(filters.regex(r"^nf_close$"))
async def nf_close_cb(client, query):
    # Extract state_id from the message to check ownership
    # Close button doesn't have state_id in callback_data so check via message
    # Find state owned by this message
    msg_id = str(query.message.reply_to_message.id) if query.message.reply_to_message else None
    if msg_id and msg_id in filter_state:
        owner = filter_state[msg_id].get("user_id", 0)
        if owner and query.from_user.id != owner:
            return await query.answer("This button is only for the person who searched.", show_alert=True)
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
    parts   = query.data.split("#", 3)
    ident   = parts[1]
    file_id = parts[2]
    invalidate_fsub_cache(query.from_user.id)  # always fresh check when user clicks button
    not_joined = await check_fsub(client, query.from_user.id)
    if not_joined:
        kb = await get_fsub_keyboard(client, not_joined, ident, file_id)
        try:
            await query.message.edit_reply_markup(kb)
        except Exception:
            pass
        return await query.answer("❌ You haven't joined yet! Please join first.", show_alert=True)
    files_ = await get_file_details_cached(file_id)
    if not files_:
        return await query.answer('No such file exist.', show_alert=True)
    files = files_[0]
    title = files.file_name
    size  = get_size(files.file_size)
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
            protect_content=True if ident == "filep" else False,
            reply_markup=get_file_markup(file_id)
        )
        clear_pending_requests(query.from_user.id)  # cleanup after file sent
        await query.answer('✅ File sent to your PM!', show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
        try:
            await client.send_message(
                LOG_CHANNEL,
                ("📥 <b>File Sent</b>\n"
                 f"👤 User: {query.from_user.mention} (<code>{query.from_user.id}</code>)\n"
                 f"🎬 File: <code>{title}</code>\n"
                 f"📦 Size: {size}"),
                parse_mode=enums.ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"Log channel error: {e}")
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
    settings_pre = 'filep' if settings.get('file_secure') else 'file'
    btn = [[InlineKeyboardButton(
        text=f"[{get_size(file.file_size)}] {file.file_name[:40]}",
        url=f"https://t.me/{temp.U_NAME}?start={settings_pre}_{file.file_id}"
    )] for file in files]
    if 0 < offset <= 10:
        off_set = 0
    elif offset == 0:
        off_set = None
    else:
        off_set = offset - 10
    page_num = math.ceil(int(offset)/10)+1
    total_pages = math.ceil(total/10)
    if n_offset == 0:
        btn.append([InlineKeyboardButton("⏪ BACK", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(f"PAGE {page_num}/{total_pages}", callback_data="pages")])
    elif off_set is None:
        btn.append([InlineKeyboardButton(f"PAGE {page_num}/{total_pages}", callback_data="pages"), InlineKeyboardButton("NEXT ⏩", callback_data=f"next_{req}_{key}_{n_offset}")])
    else:
        btn.append([InlineKeyboardButton("⏪ BACK", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(f"PAGE {page_num}/{total_pages}", callback_data="pages"), InlineKeyboardButton("NEXT ⏩", callback_data=f"next_{req}_{key}_{n_offset}")])
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
        # ── Force Subscribe check ─────────────────────────
        not_joined = await check_fsub(client, query.from_user.id)
        if not_joined:
            kb = await get_fsub_keyboard(client, not_joined, ident, file_id)
            try:
                await client.send_message(
                    chat_id=query.from_user.id,
                    text="⚠️ <b>You must join our channels to get files!</b>\n\n📨 Click the buttons below to <b>Request to Join</b>\nOnce approved, click ✅ Try Again",
                    reply_markup=kb,
                    parse_mode=enums.ParseMode.HTML
                )
            except Exception:
                pass
            await query.answer("Please join our channels first!", show_alert=True)
            return
        # ─────────────────────────────────────────────────
        files_ = await get_file_details_cached(file_id)
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
                _track_download(query.from_user.id)
                await query.answer(url=f"https://t.me/{temp.U_NAME}?start={ident}_{file_id}")
                return
            elif settings['botpm']:
                await query.answer(url=f"https://t.me/{temp.U_NAME}?start={ident}_{file_id}")
                return
            else:
                await query.answer(url=f"https://t.me/{temp.U_NAME}?start={ident}_{file_id}")
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
        files_ = await get_file_details_cached(file_id)
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
        await client.send_cached_media(chat_id=query.from_user.id, file_id=file_id, caption=f_caption, protect_content=True if ident == 'checksubp' else False, reply_markup=get_file_markup(file_id))
    elif query.data == "pages":
        await query.answer()
    elif query.data == "start":
        buttons = [[InlineKeyboardButton('➕ Add Me To Your Groups ➕', url=f'http://t.me/{temp.U_NAME}?startgroup=true')], [InlineKeyboardButton('Movie Search Group', url='https://t.me/+AngJ8lGmH4wwNWY1'), InlineKeyboardButton('Movie Updates', url='https://t.me/ccllinks')]]
        await query.message.edit_text(text=script.START_TXT.format(query.from_user.mention, temp.U_NAME, temp.B_NAME), reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
        await query.answer('Piracy Is Crime')
    elif query.data == "help":
        buttons = [[InlineKeyboardButton('Manual Filter', callback_data='manuelfilter'), InlineKeyboardButton('Auto Filter', callback_data='autofilter')], [InlineKeyboardButton('Connection', callback_data='coct'), InlineKeyboardButton('Extra Mods', callback_data='extra')], [InlineKeyboardButton('🏠 Home', callback_data='start'), InlineKeyboardButton('🔮 Status', callback_data='stats')]]
        await query.message.edit_text(text=script.HELP_TXT.format(query.from_user.mention), reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    elif query.data == "about":
        buttons = [[InlineKeyboardButton('🤖 Updates', url='https://t.me/cinemaclubnew'), InlineKeyboardButton('♥️ Source', callback_data='source')], [InlineKeyboardButton('🏠 Home', callback_data='start'), InlineKeyboardButton('🔐 Close', callback_data='close_data')]]
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


# ══════════════════════════════════════════════════════════
#  AUTO FILTER — FAST
# ══════════════════════════════════════════════════════════

async def auto_filter(client, msg, spoll=False):
    if not spoll:
        message = msg
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
                # Step 1: try cache fuzzy match
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
                else:
                    # Step 2: try DB partial/fuzzy search
                    db_files, matched_query = await db_fuzzy_search(search)
                    if db_files:
                        files = deduplicate(db_files)
                        _cache_set(matched_query, files)
                        sent = await message.reply(
                            f"🔍 No exact results for <b>{search}</b>\n✅ Showing results for: <b>{matched_query.title()}</b>",
                            quote=True, parse_mode=enums.ParseMode.HTML
                        )
                        auto_delete(message, sent)
                        search    = matched_query.title()
                        cache_key = matched_query
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
        uid = message.from_user.id if message.from_user else 0
        _track_search(uid, search)
        files.sort(key=lambda f: f.file_size if f.file_size else 0)
        # Cap filter_state to avoid memory leak on busy bots
        if len(filter_state) > 500:
            for old_key in list(filter_state.keys())[:100]:
                del filter_state[old_key]
        filter_state[state_id] = {
            "query":    search,
            "files":    files,
            "total":    len(files),
            "chat":     message.chat.id,
            "settings": settings,
            "user_id":  uid,
        }
        sent = await message.reply(
            build_header(search, files, "All", "All", "All", len(files), "All"),
            reply_markup=build_full_keyboard(state_id, files, settings, "All", "All", "All", files, "All"),
            quote=True,
            parse_mode=enums.ParseMode.HTML
        )
        if message.chat.type != enums.ChatType.PRIVATE:
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
                # Step 1: cache fuzzy
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
                else:
                    # Step 2: DB partial search
                    db_files, matched_query = await db_fuzzy_search(search)
                    if db_files:
                        files = deduplicate(db_files)
                        _cache_set(matched_query, files)
                        offset = ""
                        total_results = len(files)
                        await message.reply(
                            f"🔍 No exact results for <b>{search}</b>\n✅ Showing results for: <b>{matched_query.title()}</b>",
                            quote=True, parse_mode=enums.ParseMode.HTML
                        )
                        search = matched_query.title()
                    elif settings["spell_check"]:
                        spell_result = await advantage_spell_chok(msg)
                        if spell_result is False:
                            btn = InlineKeyboardMarkup([[
                                InlineKeyboardButton("📢 Request This Movie", url=REQUEST_CHANNEL_LINK)
                            ]])
                            await message.reply(
                                f"🔍 <b>Movie Not Found!</b>\n\n"
                                f"😔 <b>{search}</b> is not in our database yet.\n\n"
                                f"📢 <b>Request this movie</b> by joining our request channel.\n"
                                f"We will upload it soon — please wait! ⏳\n\n"
                                f"<i>Click below to request ⬇️</i>",
                                reply_markup=btn,
                                quote=True,
                                parse_mode=enums.ParseMode.HTML
                            )
                        return
                    else:
                        btn = InlineKeyboardMarkup([[
                            InlineKeyboardButton("📢 Request This Movie", url=REQUEST_CHANNEL_LINK)
                        ]])
                        await message.reply(
                            f"🔍 <b>Movie Not Found!</b>\n\n"
                            f"😔 <b>{search}</b> is not in our database yet.\n\n"
                            f"📢 <b>Request this movie</b> by joining our request channel.\n"
                            f"We will upload it soon — please wait! ⏳\n\n"
                            f"<i>Click below to request ⬇️</i>",
                            reply_markup=btn,
                            quote=True,
                            parse_mode=enums.ParseMode.HTML
                        )
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
    btn = [[InlineKeyboardButton(
        text=f"[{get_size(file.file_size)}] {file.file_name[:40]}",
        url=f"https://t.me/{temp.U_NAME}?start={pre}_{file.file_id}"
    )] for file in files]
    if offset != "":
        key = f"{message.chat.id}-{message.id}"
        if len(BUTTONS) > 200:
            for k in list(BUTTONS.keys())[:50]:
                del BUTTONS[k]
        BUTTONS[key] = search
        req = message.from_user.id if message.from_user else 0
        total_pages = math.ceil(int(total_results)/10)
        btn.append([
            InlineKeyboardButton(text=f"PAGE 1/{total_pages}", callback_data="pages"),
            InlineKeyboardButton(text="NEXT ⏩", callback_data=f"next_{req}_{key}_{offset}")
        ])
    else:
        btn.append([InlineKeyboardButton(text="PAGE 1/1", callback_data="pages")])
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
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Request This Movie", url=REQUEST_CHANNEL_LINK)]])
        k = await msg.reply(
            f"🔍 <b>Movie Not Found!</b>\n\n"
            f"😔 <b>{msg.text}</b> is not in our database yet.\n\n"
            f"📢 <b>Request this movie</b> by joining our request channel.\n"
            f"We will upload it soon — please wait! ⏳",
            reply_markup=btn,
            parse_mode=enums.ParseMode.HTML
        )
        await asyncio.sleep(30)
        await k.delete()
        try: await msg.delete()
        except: pass
        return False
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
        btn = InlineKeyboardMarkup([[InlineKeyboardButton("📢 Request This Movie", url=REQUEST_CHANNEL_LINK)]])
        k = await msg.reply(
            f"🔍 <b>Movie Not Found!</b>\n\n"
            f"😔 Not in our database yet.\n\n"
            f"📢 <b>Request this movie</b> by joining our request channel.\n"
            f"We will upload it soon — please wait! ⏳",
            reply_markup=btn,
            parse_mode=enums.ParseMode.HTML
        )
        await asyncio.sleep(30)
        await k.delete()
        try: await msg.delete()
        except: pass
        return False
    SPELL_CHECK[msg.id] = movielist
    btn = [[InlineKeyboardButton(text=movie.strip(), callback_data=f"spolling#{user}#{k}")] for k, movie in enumerate(movielist)]
    btn.append([InlineKeyboardButton(text="Close", callback_data=f'spolling#{user}#close_spellcheck')])
    dll = await msg.reply("I couldn't find anything related to that\nDid you mean any one of these?", reply_markup=InlineKeyboardMarkup(btn))
    await asyncio.sleep(10)
    await dll.delete()
    try: await msg.delete()
    except: pass


_keyword_cache = {}  # {group_id: sorted_keywords}

async def manual_filters(client, message, text=False):
    group_id = message.chat.id
    name = text or message.text
    reply_id = message.reply_to_message.id if message.reply_to_message else message.id
    # Cache sorted keywords per group — re-fetch every 60s
    cached_kw = _keyword_cache.get(group_id)
    if cached_kw and (_time.time() - cached_kw[0]) < 60:
        sorted_kw = cached_kw[1]
    else:
        keywords = await get_filters(group_id)
        sorted_kw = list(reversed(sorted(keywords, key=len)))
        _keyword_cache[group_id] = (_time.time(), sorted_kw)
    for keyword in sorted_kw:
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
