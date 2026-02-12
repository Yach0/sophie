from beanie import PydanticObjectId

from sophie_bot.db.models import NoteModel


async def export(chat_iid: PydanticObjectId):
    data = []
    notes = await NoteModel.get_chat_notes(chat_iid)
    for note in notes:
        data.append(note.model_dump(mode="json"))

    return {"notes": data}
