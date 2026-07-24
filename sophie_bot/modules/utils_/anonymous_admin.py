from __future__ import annotations

from beanie import PydanticObjectId

from sophie_bot.db.models.chat_admin import ChatAdminModel


def normalize_admin_title(title: str | None) -> str | None:
    """Collapse whitespace and case-fold an admin title for stable comparison.

    Telegram custom titles and author signatures can differ only in whitespace or
    case for what is conceptually the same title, so normalise both sides before
    matching.
    """
    if title is None:
        return None
    normalized_title = " ".join(title.split()).strip()
    return normalized_title.casefold() if normalized_title else None


async def resolve_anonymous_admin_candidates(chat_iid: PydanticObjectId, title: str) -> list[ChatAdminModel]:
    """Return the chat admins whose anonymous custom title matches ``title``.

    ``title`` must already be normalised (see ``normalize_admin_title``); each admin's
    stored ``custom_title`` is normalised here before comparison.
    """
    admins = await ChatAdminModel.find(ChatAdminModel.chat.id == chat_iid).to_list()
    matched_admins: list[ChatAdminModel] = []
    for admin in admins:
        member_is_anonymous = bool(getattr(admin.member, "is_anonymous", False))
        member_custom_title = normalize_admin_title(getattr(admin.member, "custom_title", None))
        if member_is_anonymous and member_custom_title == title:
            matched_admins.append(admin)
    return matched_admins
