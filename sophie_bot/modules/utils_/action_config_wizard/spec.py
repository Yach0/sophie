from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from aiogram.types import CallbackQuery, Message
from pydantic import BaseModel
from stfu_tg.doc import Element

from sophie_bot.utils.i18n import LazyProxy


class ActionSetupTryAgainException(Exception):
    """Keep interactive action setup active after a user-correctable error."""


@dataclass(frozen=True, slots=True)
class ActionWizardSetting:
    title: LazyProxy
    setup_confirm: (
        Callable[
            [Message | CallbackQuery, dict[str, Any]],
            Awaitable[BaseModel | None],
        ]
        | None
    )
    setup_message: (
        Callable[
            [Message | CallbackQuery, dict[str, Any]],
            Awaitable[Element],
        ]
        | None
    ) = None
    name_id: str = "setup"
    icon: str = ""


@dataclass(frozen=True, slots=True)
class ActionWizardSpec:
    interactive_setup: ActionWizardSetting | None
    settings: Callable[
        [BaseModel | None],
        Mapping[str, ActionWizardSetting],
    ]
