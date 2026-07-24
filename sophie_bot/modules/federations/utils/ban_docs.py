from __future__ import annotations

from stfu_tg import Code, Doc, KeyValue, Section, Template, Title, UserLink
from stfu_tg.formatting import Spoiler

from sophie_bot.db.models import ChatModel, Federation
from sophie_bot.utils.i18n import gettext as _


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
    banned_by = _("Anonymous admin") if banner_anonymous else UserLink(banner_tid, banner_name)
    doc = Doc(
        Title(_("🏛 User Banned from Federation")),
        KeyValue(_("Federation"), federation.fed_name),
        KeyValue(_("User"), UserLink(user.tid, user.first_name_or_title or _("Unknown"))),
        KeyValue(_("Banned by"), banned_by),
    )
    if reason:
        doc += KeyValue(_("Reason"), reason)

    if propagating:
        if immediate_chat_banned:
            doc += _("✅ Banned in this chat. Propagating across the federation…")
        else:
            doc += _("⏳ Ban recorded. Propagating across the federation…")
    else:
        doc += KeyValue(_("Result"), Template(_("Banned in {count} chats"), count=Code(banned_count or 0)))
        if lazy_ban_count > 0:
            doc += KeyValue(
                _("Also banned in"), Template(_("{count} subscribed federations"), count=Code(lazy_ban_count))
            )

    if silent:
        doc += _("🤫 The action is silent, all related messages would be deleted shortly")

    return doc


def build_ban_log_doc(
    federation: Federation,
    user: ChatModel,
    banner_name: str,
    banned_count: int,
    total_chats: int,
    reason: str | None,
    original_message_text: str | None,
) -> Doc:
    """Format the federation log entry document for a ban."""
    log_doc = Doc(
        Title(_("Ban user in the fed #FedBan")),
        KeyValue(_("Fed"), Template("{fed_name} ({fed_id})", fed_name=federation.fed_name, fed_id=federation.fed_id)),
        KeyValue(
            _("User"),
            Template(
                "{user_name} ({user_id})",
                user_name=user.first_name_or_title or _("Unknown"),
                user_id=Code(user.tid),
            ),
        ),
        KeyValue(_("By"), banner_name),
        Template(
            "User banned in {banned_count} out of {total_chats} chats in the federation",
            banned_count=banned_count,
            total_chats=total_chats,
        ),
    )
    if reason:
        log_doc += KeyValue(_("Reason"), reason)
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
        doc += KeyValue(_("Result"), Template(_("Unbanned in {count} chats"), count=str(unbanned_count or 0)))

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
