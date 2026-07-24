"""Telegram media resolution service for custom emojis and stickers.

This service provides a unified interface for resolving Telegram custom emoji
and sticker metadata, with caching to minimize API calls.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from sophie_bot.services.bot import bot
from sophie_bot.services.redis import aredis
from sophie_bot.utils.logger import log

if TYPE_CHECKING:
    from aiogram.types import Sticker


TG_EMOJI_PATTERN = re.compile(r'<tg-emoji\s+emoji-id="(\d+)"', re.IGNORECASE)

BATCH_SIZE = 200
CACHE_PREFIX = "tg_media:"
METADATA_CACHE_TTL = 86400
FILE_PATH_CACHE_TTL = 3600


class TelegramMediaType(str, Enum):
    REGULAR = "regular"
    MASK = "mask"
    CUSTOM_EMOJI = "custom_emoji"


class MediaKind(str, Enum):
    CUSTOM_EMOJI = "custom_emoji"
    STICKER = "sticker"


class MediaFormat(str, Enum):
    WEBP = "webp"
    TGS = "tgs"
    WEBM = "webm"
    UNKNOWN = "unknown"


class ResolvedMedia(BaseModel):
    kind: MediaKind
    telegram_type: TelegramMediaType
    custom_emoji_id: str | None = None
    file_id: str
    file_unique_id: str
    emoji: str | None = None
    set_name: str | None = None
    width: int
    height: int
    is_animated: bool
    is_video: bool
    needs_repainting: bool = False
    format: MediaFormat
    thumbnail_file_id: str | None = None


class ResolveResult(BaseModel):
    resolved: dict[str, ResolvedMedia]
    unresolved: list[str]


def _determine_format(sticker: Sticker) -> MediaFormat:
    if sticker.is_animated:
        return MediaFormat.TGS
    if sticker.is_video:
        return MediaFormat.WEBM
    mime_type = getattr(sticker, "mime_type", None) or ""
    if "webm" in mime_type:
        return MediaFormat.WEBM
    if "tgs" in mime_type or "json" in mime_type:
        return MediaFormat.TGS
    return MediaFormat.WEBP


def _sticker_to_resolved_media(sticker: Sticker) -> ResolvedMedia:
    telegram_type = TelegramMediaType(sticker.type) if sticker.type else TelegramMediaType.REGULAR

    kind = MediaKind.CUSTOM_EMOJI if telegram_type == TelegramMediaType.CUSTOM_EMOJI else MediaKind.STICKER

    thumbnail_file_id: str | None = None
    if sticker.thumbnail:
        thumbnail_file_id = sticker.thumbnail.file_id

    return ResolvedMedia(
        kind=kind,
        telegram_type=telegram_type,
        custom_emoji_id=sticker.custom_emoji_id,
        file_id=sticker.file_id,
        file_unique_id=sticker.file_unique_id,
        emoji=sticker.emoji,
        set_name=sticker.set_name,
        width=sticker.width,
        height=sticker.height,
        is_animated=sticker.is_animated,
        is_video=sticker.is_video,
        needs_repainting=sticker.needs_repainting or False,
        format=_determine_format(sticker),
        thumbnail_file_id=thumbnail_file_id,
    )


class TelegramMediaService:
    @staticmethod
    def _metadata_cache_key(identifier: str) -> str:
        return f"{CACHE_PREFIX}meta:{identifier}"

    @staticmethod
    def _file_path_cache_key(file_id: str) -> str:
        return f"{CACHE_PREFIX}path:{file_id}"

    @staticmethod
    async def _get_cached_metadata(identifier: str) -> ResolvedMedia | None:
        key = TelegramMediaService._metadata_cache_key(identifier)
        data = await aredis.get(key)
        if data:
            import ujson

            try:
                return ResolvedMedia.model_validate(ujson.loads(data))
            except Exception:  # noqa: BLE001  # boundary: corrupt cache entry is non-fatal, treat as miss
                log.warning("Failed to deserialize cached media metadata", identifier=identifier)
        return None

    @staticmethod
    async def _cache_metadata(identifier: str, media: ResolvedMedia) -> None:
        key = TelegramMediaService._metadata_cache_key(identifier)
        await aredis.set(key, media.model_dump_json(), ex=METADATA_CACHE_TTL)

    @staticmethod
    async def _get_cached_file_path(file_id: str) -> str | None:
        key = TelegramMediaService._file_path_cache_key(file_id)
        data = await aredis.get(key)
        if data:
            return data.decode() if isinstance(data, bytes) else data
        return None

    @staticmethod
    async def _cache_file_path(file_id: str, file_path: str) -> None:
        key = TelegramMediaService._file_path_cache_key(file_id)
        await aredis.set(key, file_path, ex=FILE_PATH_CACHE_TTL)

    @staticmethod
    async def resolve_custom_emojis(custom_emoji_ids: list[str]) -> ResolveResult:
        if not custom_emoji_ids:
            return ResolveResult(resolved={}, unresolved=[])

        unique_ids = list(dict.fromkeys(custom_emoji_ids))

        resolved: dict[str, ResolvedMedia] = {}
        ids_to_fetch: list[str] = []

        for emoji_id in unique_ids:
            cached = await TelegramMediaService._get_cached_metadata(emoji_id)
            if cached:
                resolved[emoji_id] = cached
            else:
                ids_to_fetch.append(emoji_id)

        if ids_to_fetch:
            batches = [ids_to_fetch[i : i + BATCH_SIZE] for i in range(0, len(ids_to_fetch), BATCH_SIZE)]

            for batch in batches:
                try:
                    stickers = await bot.get_custom_emoji_stickers(custom_emoji_ids=batch)

                    fetched_ids = set()
                    for sticker in stickers:
                        if sticker.custom_emoji_id:
                            media = _sticker_to_resolved_media(sticker)
                            resolved[sticker.custom_emoji_id] = media
                            await TelegramMediaService._cache_metadata(sticker.custom_emoji_id, media)
                            fetched_ids.add(sticker.custom_emoji_id)

                    for emoji_id in batch:
                        if emoji_id not in fetched_ids:
                            log.warning("Custom emoji not found in Telegram response", emoji_id=emoji_id)

                except Exception as e:  # noqa: BLE001  # boundary: Telegram API failure, skip this batch
                    log.error("Failed to fetch custom emoji batch", batch_size=len(batch), error=str(e))

        unresolved = [eid for eid in unique_ids if eid not in resolved]

        return ResolveResult(resolved=resolved, unresolved=unresolved)

    @staticmethod
    async def resolve_sticker_from_file_id(file_id: str) -> ResolvedMedia | None:
        cached = await TelegramMediaService._get_cached_metadata(file_id)
        if cached:
            return cached

        try:
            file_info = await bot.get_file(file_id)
            if not file_info.file_path:
                log.warning("File path not found for sticker", file_id=file_id)
                return None

            await TelegramMediaService._cache_file_path(file_id, file_info.file_path)

            return ResolvedMedia(
                kind=MediaKind.STICKER,
                telegram_type=TelegramMediaType.REGULAR,
                file_id=file_id,
                file_unique_id=file_info.file_unique_id,
                width=512,
                height=512,
                is_animated=file_info.file_path.endswith(".tgs"),
                is_video=file_info.file_path.endswith(".webm"),
                format=MediaFormat.TGS
                if file_info.file_path.endswith(".tgs")
                else MediaFormat.WEBM
                if file_info.file_path.endswith(".webm")
                else MediaFormat.WEBP,
            )

        except Exception as e:  # noqa: BLE001  # boundary: Telegram API failure, resolution unavailable
            log.error("Failed to resolve sticker file", file_id=file_id, error=str(e))
            return None

    @staticmethod
    async def resolve_stickers_from_objects(stickers: list[Sticker]) -> dict[str, ResolvedMedia]:
        resolved: dict[str, ResolvedMedia] = {}

        for sticker in stickers:
            media = _sticker_to_resolved_media(sticker)
            identifier = sticker.custom_emoji_id or sticker.file_id
            if identifier:
                resolved[identifier] = media
                await TelegramMediaService._cache_metadata(identifier, media)

        return resolved

    @staticmethod
    async def get_file_path(file_id: str) -> str | None:
        cached_path = await TelegramMediaService._get_cached_file_path(file_id)
        if cached_path:
            return cached_path

        try:
            file_info = await bot.get_file(file_id)
            if file_info.file_path:
                await TelegramMediaService._cache_file_path(file_id, file_info.file_path)
                return file_info.file_path
        except Exception as e:  # noqa: BLE001  # boundary: Telegram API failure, resolution unavailable
            log.error("Failed to get file path", file_id=file_id, error=str(e))

        return None

    @staticmethod
    async def download_file(file_path: str) -> bytes | None:
        try:
            content = await bot.download_file(file_path)
            if content:
                return content.read()
        except Exception as e:  # noqa: BLE001  # boundary: Telegram API failure, download unavailable
            log.error("Failed to download file", file_path=file_path, error=str(e))

        return None

    @staticmethod
    async def resolve_media(
        custom_emoji_ids: list[str] | None = None,
        sticker_file_ids: list[str] | None = None,
    ) -> ResolveResult:
        resolved: dict[str, ResolvedMedia] = {}
        unresolved: list[str] = []

        if custom_emoji_ids:
            emoji_result = await TelegramMediaService.resolve_custom_emojis(custom_emoji_ids)
            resolved.update(emoji_result.resolved)
            unresolved.extend(emoji_result.unresolved)

        if sticker_file_ids:
            unique_sticker_ids = list(dict.fromkeys(sticker_file_ids))
            for file_id in unique_sticker_ids:
                cached = await TelegramMediaService._get_cached_metadata(file_id)
                if cached:
                    resolved[file_id] = cached
                    continue

                sticker_media = await TelegramMediaService.resolve_sticker_from_file_id(file_id)
                if sticker_media:
                    resolved[file_id] = sticker_media
                else:
                    unresolved.append(file_id)

        return ResolveResult(resolved=resolved, unresolved=unresolved)

    @staticmethod
    def extract_custom_emoji_ids(text: str | None) -> list[str]:
        if not text:
            return []
        return list(dict.fromkeys(TG_EMOJI_PATTERN.findall(text)))

    @staticmethod
    async def resolve_media_from_texts(texts: list[str | None]) -> ResolveResult:
        all_emoji_ids: list[str] = []
        for text in texts:
            all_emoji_ids.extend(TelegramMediaService.extract_custom_emoji_ids(text))

        if not all_emoji_ids:
            return ResolveResult(resolved={}, unresolved=[])

        unique_ids = list(dict.fromkeys(all_emoji_ids))
        return await TelegramMediaService.resolve_custom_emojis(unique_ids)

    @staticmethod
    async def resolve_sticker_set(set_name: str) -> dict[str, Any]:
        try:
            sticker_set = await bot.get_sticker_set(set_name)

            stickers_data: list[ResolvedMedia] = []
            for sticker in sticker_set.stickers:
                media = _sticker_to_resolved_media(sticker)
                stickers_data.append(media)

            thumbnail_file_id: str | None = None
            if sticker_set.thumbnail:
                thumbnail_file_id = sticker_set.thumbnail.file_id

            return {
                "name": sticker_set.name,
                "title": sticker_set.title,
                "sticker_type": sticker_set.sticker_type,
                "is_animated": sticker_set.is_animated or False,
                "is_video": sticker_set.is_video or False,
                "stickers": stickers_data,
                "thumbnail_file_id": thumbnail_file_id,
            }

        except Exception as e:  # noqa: BLE001  # boundary: Telegram API failure, resolution unavailable
            log.error("Failed to resolve sticker set", set_name=set_name, error=str(e))
            return {}
