from beanie import PydanticObjectId

from sophie_bot.db.models import DisablingModel
from sophie_bot.services.application import ApplicationServices


async def export_disabled(
    chat_iid: PydanticObjectId,
    *,
    services: ApplicationServices,
) -> dict[str, object]:
    return {"disabled": await DisablingModel.get_disabled(chat_iid)}
