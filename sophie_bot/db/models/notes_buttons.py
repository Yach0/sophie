from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from .button_action import ButtonAction


type ButtonStyle = Literal["primary", "danger", "success"]


class Button(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    text: str
    action: ButtonAction
    data: Any | None = None
    style: ButtonStyle | None = None
