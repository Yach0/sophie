from aiogram.filters.callback_data import CallbackData


class FilterActionCallback(CallbackData, prefix="filter_action_modern"):
    # Action name
    name: str


class FilterConfirm(CallbackData, prefix="filter_confirm"):
    pass


class FilterSettingCallback(CallbackData, prefix="filter_setting"):
    name: str  # action ID
    setting_name: str  # ID of the setting


class NewFilterActionCallback(CallbackData, prefix="new_filter_action"):
    back_to_confirm: bool = False


class ListActionsToRemoveCallback(CallbackData, prefix="list_actions_to_remove"):
    pass


class RemoveFilterActionCallback(CallbackData, prefix="remove_filter_action"):
    name: str


class ToggleFilterSilentCallback(CallbackData, prefix="filter_toggle_silent"):
    pass


class SaveFilterCallback(CallbackData, prefix="save_filter"):
    pass


class FilterManagementCallback(CallbackData, prefix="filter_manage"):
    operation: str
    oid: str


class FilterDeleteConfirmCallback(CallbackData, prefix="filter_delete_confirm"):
    oid: str


class FiltersPageCallback(CallbackData, prefix="filters_page"):
    page: int
