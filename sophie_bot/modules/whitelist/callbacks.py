from aiogram.filters.callback_data import CallbackData


class WhitelistPageCallback(CallbackData, prefix="whitelist_page"):
    page: int


class WhitelistRemoveCallback(CallbackData, prefix="whitelist_remove"):
    user_tid: int
    page: int
