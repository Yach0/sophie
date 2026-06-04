from aiogram.filters.callback_data import CallbackData


class UnlockAllCallback(CallbackData, prefix="unlock_all"):
    user_id: int
