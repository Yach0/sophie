from aiogram.filters.callback_data import CallbackData


class FilterManagementCallback(CallbackData, prefix="filter_manage"):
    operation: str
    oid: str


class FilterDeleteConfirmCallback(CallbackData, prefix="filter_delete_confirm"):
    oid: str


class FiltersPageCallback(CallbackData, prefix="filters_page"):
    page: int
