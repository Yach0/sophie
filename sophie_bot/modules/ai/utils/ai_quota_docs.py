from __future__ import annotations

from datetime import date

from stfu_tg import Code, Doc, Italic, Template, Title

from sophie_bot.constants import AI_EMOJI
from sophie_bot.modules.ai.utils.ai_credit_text import format_credit_amount
from sophie_bot.utils.i18n import gettext as _


def build_chatbot_quota_exhausted_doc(total_credits: int | str, period_end: date) -> Doc:
    formatted_total = total_credits if isinstance(total_credits, str) else format_credit_amount(total_credits)
    return Doc(
        Title(f"{AI_EMOJI} {_('AI Quota Exhausted')}"),
        Template(
            _("This chat has used all {total} for this month."),
            total=Code(formatted_total),
        ),
        Template(_("Quota resets on {date}."), date=Code(period_end.strftime("%B %d, %Y"))),
        Template(_("Run {cmd} to check usage details."), cmd=Italic("/aiusage")),
    )


def build_feature_quota_exhausted_doc(period_end: date) -> Doc:
    return Doc(
        f"{AI_EMOJI} {_('AI Quota Exhausted')}",
        Template(_("AI features are disabled till {date}."), date=Code(period_end.strftime("%B %d, %Y"))),
        Template(_("Run {cmd} to check usage details."), cmd=Code("/aiusage")),
    )
