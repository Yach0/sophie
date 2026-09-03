from typing import Any

from aiogram.dispatcher.event.handler import CallbackType

from sophie_bot.db.models.filters import FilterInSetupType
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.feature_flag import FeatureFlagFilter
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.modules.filters.callbacks import ToggleFilterSilentCallback
from sophie_bot.modules.filters.handlers.filter_confirm import FilterConfirmHandler
from sophie_bot.modules.utils_.action_config_wizard.config import ActionWizardDraft
from sophie_bot.modules.utils_.action_config_wizard.state import WizardState
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


class FilterToggleSilentHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (
            ToggleFilterSilentCallback.filter(),
            FeatureFlagFilter("filters_silent_mode"),
            UserRestricting(admin=True),
            GroupOrConnectedFilter(),
        )

    async def handle(self) -> Any:
        wizard_state = WizardState(self.state)
        draft_data = await wizard_state.get_draft()
        if draft_data is not None:
            draft = ActionWizardDraft.from_data(draft_data)
            draft.metadata["silent"] = not bool(draft.metadata.get("silent", False))
            await wizard_state.set_draft(draft.to_data())
            return await self.event.answer()
        try:
            filter_item = await FilterInSetupType.get_filter(self.state, data=self.data)
        except ValueError:
            return await self.event.answer(_("Continuing setup is only possible by the same user who started it."))
        filter_item.silent = not filter_item.silent
        await filter_item.set_filter_state(self.state)
        return await FilterConfirmHandler(self.event, **self.data)
