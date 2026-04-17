import logging
from struct import pack
import re
import base64
from pyrogram.file_id import FileId
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow.exceptions import ValidationError
from info import FILES_DATABASE, DATABASE_NAME, COLLECTION_NAME, MAX_BTN

logger = logging.getLogger(__name__)

client = AsyncIOMotorClient(
    FILES_DATABASE,
    maxPoolSize=10,
    minPoolSize=2,
    connectTimeoutMS=5000,
    serverSelectionTimeoutMS=5000,
)
mydb = client[DATABASE_NAME]

instance = Instance.from_db(mydb)


@instance.register
class Media(Document):
    file_id = fields.StrField(attribute='_id')
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)
    file_type = fields.StrField(allow_none=True)

    class Meta:
        indexes = ('$file_name', )
        collection_name = COLLECTION_NAME


async def get_files_db_size():
    return (await mydb.command("dbstats"))['dataSize']


async def save_file(media):
    """Save file in database"""
    file_id, file_ref = unpack_new_file_id(media.file_id)
    file_name = re.sub(r"(_|\-|\.|\+)", " ", str(media.file_name))

    # Pre-check: if file_id already exists, skip immediately
    # This protects ALL existing data from being touched or overwritten
    existing = await Media.collection.find_one({'_id': file_id})
    if existing:
        logger.warning(
            f'Duplicate skipped: {getattr(media, "file_name", "NO_FILE")} '
            f'already exists in DB — no data was changed.'
        )
        return 'dup'

    try:
        file = Media(
            file_id=file_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            mime_type=media.mime_type,
            caption=media.caption.html if media.caption else None,
            file_type=media.mime_type.split('/')[0] if media.mime_type else None
        )
    except ValidationError as e:
        logger.warning(f'Validation error saving file {getattr(media, "file_name", "NO_FILE")}: {e}')
        return 'err'
    else:
        try:
            await file.commit()
        except DuplicateKeyError:
            # Safety net in case pre-check above is bypassed under race condition
            logger.warning(
                f'Duplicate skipped: {getattr(media, "file_name", "NO_FILE")} '
                f'(file_id: {file_id}) — no data was changed.'
            )
            return 'dup'
        else:
            logger.info(f'{getattr(media, "file_name", "NO_FILE")} saved to database')
            return 'suc'


async def get_search_results(query, max_results=MAX_BTN, offset=0, lang=None, filter=False):
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.\+\-\_])' + query + r'(\b|[\.\+\-\_])'
    else:
        raw_pattern = query.replace(' ', r'.*[\s\.\+\-\_]')

    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except Exception:
        regex = query

    filter_q = {'file_name': regex}

    if lang:
        lang_pattern = re.compile(lang, re.IGNORECASE)
        filter_q = {
            '$and': [
                {'file_name': regex},
                {'file_name': lang_pattern}
            ]
        }

    import asyncio
    cursor = Media.find(filter_q)
    cursor.sort('$natural', -1)
    cursor.skip(offset).limit(max_results)

    files_task = cursor.to_list(length=max_results)
    count_task = Media.count_documents(filter_q)

    files, total_results = await asyncio.gather(files_task, count_task)

    next_offset = offset + max_results
    if next_offset >= total_results:
        next_offset = ''

    return files, next_offset, total_results


async def get_bad_files(query, file_type=None, offset=0, filter=False):
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r'(\b|[\.\+\-\_])' + query + r'(\b|[\.\+\-\_])'
    else:
        raw_pattern = query.replace(' ', r'.*[\s\.\+\-\_]')

    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except Exception:
        return []

    filter_q = {'file_name': regex}
    if file_type:
        filter_q['file_type'] = file_type

    total_results = await Media.count_documents(filter_q)
    cursor = Media.find(filter_q)
    cursor.sort('$natural', -1)
    files = await cursor.to_list(length=total_results)
    return files, total_results


async def get_file_details(query):
    filter_q = {'file_id': query}
    cursor = Media.find(filter_q)
    filedetails = await cursor.to_list(length=1)
    return filedetails


def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0
            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")


def encode_file_ref(file_ref: bytes) -> str:
    return base64.urlsafe_b64encode(file_ref).decode().rstrip("=")


def unpack_new_file_id(new_file_id):
    """Return file_id, file_ref"""
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash
        )
    )
    file_ref = encode_file_ref(decoded.file_reference)
    return file_id, file_ref
