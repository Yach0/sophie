from __future__ import annotations

from stfu_tg import Code, Doc, KeyValue, Template, Title, UserLink

from sophie_bot.db.models import ChatModel, CommunityModel
from sophie_bot.utils.i18n import gettext as _


def _community_name(community: CommunityModel) -> str:
    return community.name or _("Community")


def build_ban_reply_doc(
    community: CommunityModel,
    user: ChatModel,
    banner_tid: int,
    banner_name: str,
    reason: str | None,
    silent: bool,
    *,
    banned_count: int | None = None,
    propagating: bool = False,
    immediate_chat_banned: bool = False,
) -> Doc:
    """Format the user-facing community ban response.

    When ``propagating`` is True the document reflects the in-progress state shown right
    after the command; otherwise it shows the final per-chat count.
    """
    doc = Doc(
        Title(_("🌐 User Banned from Community")),
        KeyValue(_("Community"), _community_name(community)),
        KeyValue(_("User"), UserLink(user.tid, user.first_name_or_title or _("Unknown"))),
        KeyValue(_("Banned by"), UserLink(banner_tid, banner_name)),
    )
    if reason:
        doc += KeyValue(_("Reason"), reason)

    if propagating:
        if immediate_chat_banned:
            doc += _("✅ Banned in this chat. Propagating across the community…")
        else:
            doc += _("⏳ Ban recorded. Propagating across the community…")
    else:
        doc += KeyValue(_("Result"), Template(_("Banned in {count} chats"), count=Code(banned_count or 0)))

    if silent:
        doc += _("🤫 The action is silent, all related messages would be deleted shortly")

    return doc


def build_unban_reply_doc(
    community: CommunityModel,
    user: ChatModel,
    unbanner_tid: int,
    unbanner_name: str,
    *,
    unbanned_count: int | None = None,
    propagating: bool = False,
    immediate_chat_unbanned: bool = False,
) -> Doc:
    """Format the user-facing community unban response."""
    doc = Doc(
        Title(_("🌐 User Unbanned from Community")),
        KeyValue(_("Community"), _community_name(community)),
        KeyValue(_("User"), UserLink(user.tid, user.first_name_or_title or _("Unknown"))),
        KeyValue(_("Unbanned by"), UserLink(unbanner_tid, unbanner_name)),
    )

    if propagating:
        if immediate_chat_unbanned:
            doc += _("✅ Unbanned in this chat. Propagating across the community…")
        else:
            doc += _("⏳ Unban recorded. Propagating across the community…")
    else:
        doc += KeyValue(_("Result"), Template(_("Unbanned in {count} chats"), count=str(unbanned_count or 0)))

    return doc


def build_task_failed_doc(error_message: str | None) -> Doc:
    """Reply shown when propagating a community (un)ban across chats fails.

    The action was already applied to the DB record and the current chat, so this reports a
    partial failure of the community-wide propagation rather than a total failure.
    """
    doc = Doc(
        Title(_("⚠️ Community Propagation Failed")),
        _("The action was applied in this chat, but propagating it across the community didn't complete."),
    )
    if error_message:
        doc += KeyValue(_("Error"), error_message)
    doc += _("Please run the command again to retry.")
    return doc


def build_ban_superseded_doc() -> Doc:
    """Reply shown when the ban record is gone before propagation ran."""
    return Doc(
        Title(_("ℹ️ Ban Superseded")),
        _("This user was unbanned before the ban finished propagating, so there was nothing left to do."),
    )
