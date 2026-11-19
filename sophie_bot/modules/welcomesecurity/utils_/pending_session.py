from sophie_bot.db.models.chat import UserInGroupModel
from sophie_bot.db.models.ws_user import WSUserModel


async def is_current_session(ws_user: WSUserModel, *, allow_departed: bool = False) -> bool:
    membership = await UserInGroupModel.get_user_in_group(ws_user.user.ref.id, ws_user.group.ref.id)
    # A successful kick's leave event can remove membership before the API reply.
    # A present, different membership boundary must still invalidate cleanup.
    if membership is None and allow_departed:
        return True
    if ws_user.membership_id is None and ws_user.membership_join_message_id is None:
        return membership is None or membership.joined_message_id is None
    return (
        membership is not None
        and membership.id == ws_user.membership_id
        and membership.joined_message_id == ws_user.membership_join_message_id
    )
