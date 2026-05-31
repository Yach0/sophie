from typing import Final

from pydantic_ai.models import Model
from stfu_tg import HList, PreformattedHTML, Template, Title
from stfu_tg.doc import Element

from sophie_bot.constants import AI_EMOJI
from sophie_bot.modules.ai.utils.ai_models import AI_MODEL_TO_SHORT_NAME
from sophie_bot.utils.i18n import gettext as _

_LOW_BATTERY_CUSTOM_EMOJI_ID: Final[str] = "5819177212833697095"
_MIDDLE_BATTERY_CUSTOM_EMOJI_ID: Final[str] = "5818860416045945285"
_HIGH_BATTERY_CUSTOM_EMOJI_ID: Final[str] = "5816915599019741395"


def _get_short_model_text(provider: Model) -> str:
    return AI_MODEL_TO_SHORT_NAME[provider.model_name].replace(" Preview", "")


def _get_battery_custom_emoji_id(percentage: int) -> str:
    if percentage >= 66:
        return _HIGH_BATTERY_CUSTOM_EMOJI_ID
    if percentage >= 33:
        return _MIDDLE_BATTERY_CUSTOM_EMOJI_ID
    return _LOW_BATTERY_CUSTOM_EMOJI_ID


def _battery_custom_emoji(percentage: int) -> Element:
    emoji_id = _get_battery_custom_emoji_id(percentage)
    return PreformattedHTML(f'<tg-emoji emoji-id="{emoji_id}">🔋</tg-emoji>')


def ai_short_title_header(model: Model, *additional_elements: Element) -> Element:
    return HList(
        Title(Template(_("{ai_emoji} AI"), ai_emoji=AI_EMOJI), bold=False),
        _get_short_model_text(model),
        *additional_elements,
        divider=" | ",
    )


def ai_chatbot_header(model: Model, *additional_elements: Element) -> Element:
    return ai_short_title_header(model, *additional_elements)


def ai_credit_header(percentage: int) -> Element:
    return HList(_battery_custom_emoji(percentage), str(percentage) + "%", divider=" ")
