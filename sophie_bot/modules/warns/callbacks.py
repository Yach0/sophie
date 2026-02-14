from aiogram.filters.callback_data import CallbackData


class DeleteWarnCallback(CallbackData, prefix="del_warn"):
    warn_iid: str


class ResetWarnsCallback(CallbackData, prefix="reset_warns"):
    user_tid: int


class ResetAllWarnsCallback(CallbackData, prefix="reset_all_warns"):
    pass
