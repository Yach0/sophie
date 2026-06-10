"""REST API endpoints for resolving Telegram custom emojis and stickers.

These endpoints provide a unified interface for the frontend to resolve
Telegram media (custom emojis and stickers) without exposing the bot token.

Endpoint summary:
- POST /telegram/custom-emojis/resolve - Resolve custom emoji IDs
- POST /telegram/stickers/resolve - Resolve sticker file IDs
- POST /telegram/media/resolve - Unified endpoint for all media types
- GET /telegram/media/proxy/{file_id} - Proxy endpoint for media assets
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from sophie_bot.services.telegram_media import (
    ResolvedMedia,
    ResolveResult,
    TelegramMediaService,
)
from sophie_bot.utils.api.auth import get_current_user
from sophie_bot.utils.api.rate_limiter import rate_limit
from sophie_bot.utils.logger import log

router = APIRouter(prefix="/telegram", tags=["telegram-media"])
CurrentUser = Annotated[Any, Depends(get_current_user)]
_RATE_LIMIT_DEPENDENCIES = [Depends(rate_limit)]


class ResolveCustomEmojisRequest(BaseModel):
    ids: list[str] = Field(..., description="List of custom emoji IDs to resolve", max_length=500)


class ResolveStickersRequest(BaseModel):
    file_ids: list[str] = Field(..., description="List of sticker file IDs to resolve", max_length=100)


class ResolveMediaRequest(BaseModel):
    custom_emoji_ids: list[str] | None = Field(None, description="List of custom emoji IDs to resolve", max_length=500)
    sticker_file_ids: list[str] | None = Field(None, description="List of sticker file IDs to resolve", max_length=100)


class ResolveStickerSetRequest(BaseModel):
    name: str = Field(..., description="Name of the sticker set to resolve")


class StickerSetResponse(BaseModel):
    name: str = Field(..., description="Sticker set name")
    title: str = Field(..., description="Sticker set title")
    sticker_type: str = Field(..., description="Type of stickers in the set")
    is_animated: bool = Field(..., description="Whether the set contains animated stickers")
    is_video: bool = Field(..., description="Whether the set contains video stickers")
    stickers: list[ResolvedMediaResponse] = Field(..., description="List of stickers in the set")
    thumbnail_url: str | None = Field(None, description="URL to the sticker set thumbnail")


class ResolvedMediaResponse(BaseModel):
    kind: str = Field(..., description="Media kind: custom_emoji or sticker")
    telegram_type: str = Field(..., description="Telegram sticker type: regular, mask, or custom_emoji")
    custom_emoji_id: str | None = Field(None, description="Custom emoji ID if applicable")
    file_id: str = Field(..., description="Telegram file ID for the media")
    file_unique_id: str = Field(..., description="Unique file identifier (stable across bots)")
    emoji: str | None = Field(None, description="Associated emoji character")
    set_name: str | None = Field(None, description="Sticker set name")
    width: int = Field(..., description="Media width in pixels")
    height: int = Field(..., description="Media height in pixels")
    is_animated: bool = Field(..., description="Whether the media is animated (TGS format)")
    is_video: bool = Field(..., description="Whether the media is a video sticker (WebM format)")
    needs_repainting: bool = Field(..., description="Whether custom emoji needs repainting for dark mode")
    format: str = Field(..., description="Media format: webp, tgs, webm, or unknown")
    thumbnail_file_id: str | None = Field(None, description="Thumbnail file ID if available")
    asset_url: str | None = Field(None, description="URL to fetch the media asset through the proxy")
    thumbnail_url: str | None = Field(None, description="URL to fetch the thumbnail through the proxy")


class ResolveCustomEmojisResponse(BaseModel):
    resolved: dict[str, ResolvedMediaResponse] = Field(..., description="Map of emoji ID to resolved media")
    unresolved: list[str] = Field(..., description="List of emoji IDs that could not be resolved")


class ResolveStickersResponse(BaseModel):
    resolved: dict[str, ResolvedMediaResponse] = Field(..., description="Map of file ID to resolved media")
    unresolved: list[str] = Field(..., description="List of file IDs that could not be resolved")


class ResolveMediaResponseModel(BaseModel):
    resolved: dict[str, ResolvedMediaResponse] = Field(..., description="Map of identifier to resolved media")
    unresolved: list[str] = Field(..., description="List of identifiers that could not be resolved")


def _build_asset_url(request: Request, file_id: str) -> str:
    base_url = str(request.base_url).rstrip("/")
    return f"{base_url}/telegram/media/proxy/{file_id}"


def _resolved_media_to_response(media: ResolvedMedia, request: Request) -> ResolvedMediaResponse:
    asset_url = _build_asset_url(request, media.file_id)
    thumbnail_url = _build_asset_url(request, media.thumbnail_file_id) if media.thumbnail_file_id else None

    return ResolvedMediaResponse(
        kind=media.kind.value,
        telegram_type=media.telegram_type.value,
        custom_emoji_id=media.custom_emoji_id,
        file_id=media.file_id,
        file_unique_id=media.file_unique_id,
        emoji=media.emoji,
        set_name=media.set_name,
        width=media.width,
        height=media.height,
        is_animated=media.is_animated,
        is_video=media.is_video,
        needs_repainting=media.needs_repainting,
        format=media.format.value,
        thumbnail_file_id=media.thumbnail_file_id,
        asset_url=asset_url,
        thumbnail_url=thumbnail_url,
    )


def _resolve_result_to_response(result: ResolveResult, request: Request) -> dict[str, Any]:
    resolved = {
        identifier: _resolved_media_to_response(media, request) for identifier, media in result.resolved.items()
    }
    return {
        "resolved": resolved,
        "unresolved": result.unresolved,
    }


@router.post(
    "/custom-emojis/resolve",
    response_model=ResolveCustomEmojisResponse,
    dependencies=_RATE_LIMIT_DEPENDENCIES,
    summary="Resolve custom emoji IDs",
    description="Resolve a list of Telegram custom emoji IDs into renderable metadata.",
)
async def resolve_custom_emojis(
    request: Request,
    data: ResolveCustomEmojisRequest,
    user: CurrentUser,
) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(data.ids))

    log.debug(
        "Resolving custom emojis",
        user_tid=user.tid,
        count=len(unique_ids),
    )

    result = await TelegramMediaService.resolve_custom_emojis(unique_ids)

    log.debug(
        "Custom emoji resolution complete",
        user_tid=user.tid,
        resolved_count=len(result.resolved),
        unresolved_count=len(result.unresolved),
    )

    return _resolve_result_to_response(result, request)


@router.post(
    "/stickers/resolve",
    response_model=ResolveStickersResponse,
    dependencies=_RATE_LIMIT_DEPENDENCIES,
    summary="Resolve sticker file IDs",
    description="Resolve a list of Telegram sticker file IDs into renderable metadata.",
)
async def resolve_stickers(
    request: Request,
    data: ResolveStickersRequest,
    user: CurrentUser,
) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(data.file_ids))

    log.debug(
        "Resolving stickers",
        user_tid=user.tid,
        count=len(unique_ids),
    )

    result = await TelegramMediaService.resolve_media(sticker_file_ids=unique_ids)

    log.debug(
        "Sticker resolution complete",
        user_tid=user.tid,
        resolved_count=len(result.resolved),
        unresolved_count=len(result.unresolved),
    )

    return _resolve_result_to_response(result, request)


@router.post(
    "/media/resolve",
    response_model=ResolveMediaResponseModel,
    dependencies=_RATE_LIMIT_DEPENDENCIES,
    summary="Resolve all media types",
    description="Unified endpoint to resolve custom emojis, stickers, and other Telegram media.",
)
async def resolve_media(
    request: Request,
    data: ResolveMediaRequest,
    user: CurrentUser,
) -> dict[str, Any]:
    custom_emoji_ids = list(dict.fromkeys(data.custom_emoji_ids)) if data.custom_emoji_ids else None
    sticker_file_ids = list(dict.fromkeys(data.sticker_file_ids)) if data.sticker_file_ids else None

    log.debug(
        "Resolving media",
        user_tid=user.tid,
        custom_emoji_count=len(custom_emoji_ids) if custom_emoji_ids else 0,
        sticker_count=len(sticker_file_ids) if sticker_file_ids else 0,
    )

    result = await TelegramMediaService.resolve_media(
        custom_emoji_ids=custom_emoji_ids,
        sticker_file_ids=sticker_file_ids,
    )

    log.debug(
        "Media resolution complete",
        user_tid=user.tid,
        resolved_count=len(result.resolved),
        unresolved_count=len(result.unresolved),
    )

    return _resolve_result_to_response(result, request)


@router.get(
    "/media/proxy/{file_id}",
    summary="Proxy Telegram media asset",
    description="Fetch and proxy a Telegram media file. Returns the raw bytes with appropriate content type.",
    dependencies=_RATE_LIMIT_DEPENDENCIES,
)
async def proxy_media(
    file_id: str = Path(..., description="Telegram file ID to fetch"),
    user: CurrentUser = None,
) -> Response:
    _ = user
    file_path = await TelegramMediaService.get_file_path(file_id)
    if not file_path:
        raise HTTPException(status_code=404, detail="File not found")

    content = await TelegramMediaService.download_file(file_path)
    if content is None:
        raise HTTPException(status_code=500, detail="Failed to download file")

    content_type = "application/octet-stream"
    if file_path.endswith(".webp"):
        content_type = "image/webp"
    elif file_path.endswith(".tgs"):
        content_type = "application/x-tgsticker"
    elif file_path.endswith(".webm"):
        content_type = "video/webm"

    # Use only the known extension from the resolved file_path; never embed the
    # caller-supplied file_id directly in the header to prevent header injection.
    ext = file_path.rsplit(".", 1)[-1] if "." in file_path else "bin"
    safe_ext = ext if ext.isalnum() else "bin"

    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Cache-Control": "public, max-age=86400",
            "Content-Disposition": f'inline; filename="media.{safe_ext}"',
        },
    )


@router.get(
    "/sticker-set/{set_name}",
    response_model=StickerSetResponse,
    dependencies=_RATE_LIMIT_DEPENDENCIES,
    summary="Resolve sticker set",
    description="Fetch and resolve a Telegram sticker set by name. Returns all stickers with their metadata.",
)
async def resolve_sticker_set(
    request: Request,
    set_name: str = Path(..., description="Name of the sticker set to resolve"),
    user: CurrentUser = None,
) -> StickerSetResponse:
    _ = user
    result = await TelegramMediaService.resolve_sticker_set(set_name)
    if not result:
        raise HTTPException(status_code=404, detail="Sticker set not found")

    stickers = [_resolved_media_to_response(media, request) for media in result.get("stickers", [])]

    thumbnail_url = None
    if result.get("thumbnail_file_id"):
        thumbnail_url = _build_asset_url(request, result["thumbnail_file_id"])

    return StickerSetResponse(
        name=result["name"],
        title=result["title"],
        sticker_type=result["sticker_type"],
        is_animated=result["is_animated"],
        is_video=result["is_video"],
        stickers=stickers,
        thumbnail_url=thumbnail_url,
    )
