from __future__ import annotations

from stfu_tg import Code, Doc, KeyValue, Section, Template, Title, UserLink
from stfu_tg.formatting import Spoiler

from sophie_bot.db.models import ChatModel, Federation
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import ngettext as pl_


def _build_ban_doc(
    federation: Federation,
    user: ChatModel,
    banner_tid: int,
    banner_name: str,
    reason: str | None,
    *,
    banned_count: int | None,
    subscribed_federation_count: int,
    subscribed_banned_count: int,
    total_chats: int | None = None,
    banner_anonymous: bool = False,
) -> Doc:
    """Share the ban presentation; a log denominator enables audit identifiers."""
    federation_value = Template("{name}", name=federation.fed_name)
    user_link = UserLink(user.tid, user.first_name_or_title or _("Unknown"))
    user_value = Template("{user}", user=user_link)
    if total_chats is not None:
        federation_value = Template("{name} ({id})", name=federation.fed_name, id=federation.fed_id)
        user_value = Template("{user} ({id})", user=user_link, id=Code(user.tid))
    banned_by = _("Anonymous admin") if banner_anonymous else UserLink(banner_tid, banner_name)
    doc = Doc(
        Title(_("🏛 User Banned from Federation")),
        KeyValue(_("Federation"), federation_value),
        KeyValue(_("User"), user_value),
        KeyValue(_("Banned by"), banned_by),
    )
    if reason:
        doc += KeyValue(_("Reason"), Code(reason))
    if banned_count is not None:
        direct_count = Template("{count}", count=Code(banned_count))
        if total_chats is not None:
            direct_count = Template(_("{count} out of {total}"), count=Code(banned_count), total=Code(total_chats))
        doc += KeyValue(_("Federation chats"), direct_count)
        if subscribed_federation_count > 0:
            doc += KeyValue(_("Subscribed federations"), Code(subscribed_federation_count))
            doc += KeyValue(_("Subscribed-federation chats"), Code(subscribed_banned_count))
    return doc


def build_ban_reply_doc(
    federation: Federation,
    user: ChatModel,
    banner_tid: int,
    banner_name: str,
    reason: str | None,
    silent: bool,
    *,
    banned_count: int | None = None,
    lazy_ban_count: int = 0,
    subscribed_banned_count: int = 0,
    propagating: bool = False,
    immediate_chat_banned: bool = False,
    banner_anonymous: bool = False,
) -> Doc:
    """Format the user-facing ban response document.

    When ``propagating`` is True the document reflects the in-progress state shown
    right after the command (before the scheduler finishes); otherwise it shows the
    final per-chat counts.

    When ``banner_anonymous`` is True the banner is shown as plain "Anonymous admin"
    with no user link, so an anonymous admin's identity stays hidden in the public
    reply (the fed-channel log keeps the real identity for accountability).
    """
    doc = _build_ban_doc(
        federation,
        user,
        banner_tid,
        banner_name,
        reason,
        banned_count=None if propagating else (banned_count or 0),
        subscribed_federation_count=lazy_ban_count,
        subscribed_banned_count=subscribed_banned_count,
        banner_anonymous=banner_anonymous,
    )

    if propagating:
        if immediate_chat_banned:
            doc += _("✅ Banned in this chat. Propagating across the federation…")
        else:
            doc += _("⏳ Ban recorded. Propagating across the federation…")

    if silent:
        doc += _("🤫 The action is silent, all related messages would be deleted shortly")

    return doc


def build_ban_log_doc(
    federation: Federation,
    user: ChatModel,
    banner_tid: int,
    banner_name: str,
    banned_count: int,
    total_chats: int,
    reason: str | None,
    original_message_text: str | None,
    subscribed_banned_count: int = 0,
    subscribed_federation_count: int = 0,
) -> Doc:
    """Format the federation log entry document for a ban."""
    log_doc = _build_ban_doc(
        federation,
        user,
        banner_tid,
        banner_name,
        reason,
        banned_count=banned_count,
        subscribed_federation_count=subscribed_federation_count,
        subscribed_banned_count=subscribed_banned_count,
        total_chats=total_chats,
    )
    log_doc += _("#FedBan")
    if original_message_text:
        log_doc += Section(
            Spoiler(original_message_text),
            title=_("Original message"),
        )
    return log_doc


def build_unban_reply_doc(
    federation: Federation,
    user: ChatModel,
    unbanner_tid: int,
    unbanner_name: str,
    *,
    unbanned_count: int | None = None,
    propagating: bool = False,
    immediate_chat_unbanned: bool = False,
) -> Doc:
    """Format the user-facing unban response document."""
    doc = Doc(
        Title(_("🏛 User Unbanned from Federation")),
        KeyValue(_("Federation"), federation.fed_name),
        KeyValue(_("User"), UserLink(user.tid, user.first_name_or_title or _("Unknown"))),
        KeyValue(_("Unbanned by"), UserLink(unbanner_tid, unbanner_name)),
    )

    if propagating:
        if immediate_chat_unbanned:
            doc += _("✅ Unbanned in this chat. Propagating across the federation…")
        else:
            doc += _("⏳ Unban recorded. Propagating across the federation…")
    else:
        unbanned_count = unbanned_count or 0
        doc += KeyValue(
            _("Result"),
            Template(
                pl_("Unbanned in {count} chat", "Unbanned in {count} chats", unbanned_count),
                count=Code(unbanned_count),
            ),
        )

    return doc


def build_ban_superseded_doc() -> Doc:
    """Format the reply shown when the ban record is gone before propagation ran.

    The user was unbanned between recording the ban and this job running, so there is
    nothing left to propagate. Edit the queued reply to a terminal state instead of
    leaving it stuck on "Propagating across the federation…".
    """
    return Doc(
        Title(_("ℹ️ Ban Superseded")),
        _("This user was unbanned before the ban finished propagating, so there was nothing left to do."),
    )


def build_unban_log_text(user: ChatModel, unbanner_tid: int, unbanner_name: str) -> str:
    """Format the federation log entry for an unban."""
    return Template(
        _("🏛 User {unbanned_user} has been unbanned from federation by {unbanner}."),
        unbanned_user=UserLink(user.tid, user.first_name_or_title or _("Unknown")),
        unbanner=UserLink(unbanner_tid, unbanner_name),
    ).to_html()
