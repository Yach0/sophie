from beanie import PydanticObjectId

from sophie_bot.db.models import DisablingModel


async def export_disabled(chat_iid: PydanticObjectId):
    return {"disabled": await DisablingModel.get_disabled(chat_iid)}
