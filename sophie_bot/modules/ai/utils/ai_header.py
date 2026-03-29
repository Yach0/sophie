from pydantic_ai.models import Model
from stfu_tg import HList, Title
from stfu_tg.doc import Element

from sophie_bot.constants import AI_CREDIT_EMOJI
from sophie_bot.modules.ai.fsm.pm import AI_GENERATED_TEXT
from sophie_bot.modules.ai.utils.ai_models import AI_MODEL_TO_SHORT_NAME
from sophie_bot.utils.i18n import gettext as _


def ai_get_model_text(provider: Model) -> str | Element:
    return AI_MODEL_TO_SHORT_NAME[provider.model_name]


def ai_header(model: Model, *additional_elements: Element) -> Element:
    return HList(Title(AI_GENERATED_TEXT), Title(ai_get_model_text(model), bold=False), *additional_elements)


def ai_credit_header(percentage: int) -> Element:
    return Title(
        _("{credit_emoji} Quota {percentage}%").format(credit_emoji=AI_CREDIT_EMOJI, percentage=percentage), bold=False
    )
