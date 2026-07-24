from datetime import timedelta
from typing import Annotated

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, BeforeValidator

from sophie_bot.db.models._link_type import Link
from sophie_bot.db.models.chat import ChatModel
from sophie_bot.db.models.notes import Saveable


# TODO: Migrate properly
def _coerce_timedelta(value: object) -> object:
    """Coerce legacy numeric values to timedelta for MongoDB migration.

    - ``int`` values are milliseconds (produced by the greeting-durations migration).
    - ``float`` values are seconds (produced by old code that stored
      ``timedelta.total_seconds()``; the migration missed them because it
      cast floats to ``int`` without running on every document).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return timedelta(milliseconds=value)
    if isinstance(value, float):
        return timedelta(seconds=value)
    return value


class CleanWelcome(BaseModel):
    enabled: bool = False
    last_msg: int | None = None


class CleanService(BaseModel):
    enabled: bool = False


WELCOMEMUTE_DEFAULT_TIME = timedelta(hours=48)
WELCOMESECURITY_EXPIRE_DEFAULT_TIME = timedelta(hours=48)


class WelcomeMute(BaseModel):
    enabled: bool = False
    time: Annotated[timedelta | None, BeforeValidator(_coerce_timedelta)] = WELCOMEMUTE_DEFAULT_TIME


class WelcomeSecurity(BaseModel):
    enabled: bool = False
    expire: Annotated[timedelta | None, BeforeValidator(_coerce_timedelta)] = WELCOMESECURITY_EXPIRE_DEFAULT_TIME


class GreetingsModel(Document):
    chat: Link[ChatModel]

    welcome_disabled: bool | None = False

    note: Saveable | None = None
    security_note: Saveable | None = None
    join_request_message: Saveable | None = None

    clean_welcome: CleanWelcome | None = CleanWelcome()
    clean_service: CleanService | None = CleanService()

    welcome_mute: WelcomeMute | None = WelcomeMute()
    welcome_security: WelcomeSecurity | None = WelcomeSecurity()

    class Settings:
        name = "greetings"

    @staticmethod
    async def get_by_chat_iid(chat_iid: PydanticObjectId) -> "GreetingsModel":
        return await GreetingsModel.find_one(GreetingsModel.chat.id == chat_iid) or GreetingsModel(chat=chat_iid)

    @staticmethod
    async def change_state_welcome(chat_iid: PydanticObjectId, new_state: bool) -> "GreetingsModel":
        model = await GreetingsModel.get_by_chat_iid(chat_iid)
        model.welcome_disabled = not new_state
        return await model.save()

    @staticmethod
    async def change_welcome_message(chat_iid: PydanticObjectId, saveable: Saveable) -> "GreetingsModel":
        model = await GreetingsModel.get_by_chat_iid(chat_iid)
        model.note = saveable
        return await model.save()

    @staticmethod
    async def change_join_request_message(chat_iid: PydanticObjectId, saveable: Saveable) -> "GreetingsModel":
        model = await GreetingsModel.get_by_chat_iid(chat_iid)
        model.join_request_message = saveable
        return await model.save()

    async def set_clean_welcome_status(self, new_state: bool) -> "GreetingsModel":
        if not self.clean_welcome:
            self.clean_welcome = CleanWelcome(enabled=new_state)
        else:
            self.clean_welcome.enabled = new_state
        return await self.save()

    async def set_service_clean_status(self, new_state: bool) -> "GreetingsModel":
        if not self.clean_service:
            self.clean_service = CleanService(enabled=new_state)
        else:
            self.clean_service.enabled = new_state
        return await self.save()

    async def clean_welcome_new_message(self, msg_id: int) -> "GreetingsModel":
        if not self.clean_welcome:
            self.clean_welcome = CleanWelcome(last_msg=msg_id)
        else:
            self.clean_welcome.last_msg = msg_id
        return await self.save()

    async def set_status_welcomesecurity(self, new_state: bool) -> "GreetingsModel":
        if not self.welcome_security:
            self.welcome_security = WelcomeSecurity(enabled=new_state)
        else:
            self.welcome_security.enabled = new_state
        return await self.save()

    async def set_status_welcomemute(self, new_state: bool, time: timedelta | None) -> "GreetingsModel":
        if not self.welcome_mute:
            self.welcome_mute = WelcomeMute(enabled=new_state, time=time)
        else:
            self.welcome_mute.enabled = new_state

            if time:
                self.welcome_mute.time = time
        return await self.save()
