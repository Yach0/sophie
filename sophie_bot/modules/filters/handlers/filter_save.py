from typing import Any

from aiogram.dispatcher.event.handler import CallbackType
from aiogram.fsm.context import FSMContext
from bson import ObjectId
from stfu_tg import Code, Template

from sophie_bot.db.models import FiltersModel
from sophie_bot.db.models.filters import FilterInSetupType
from sophie_bot.filters.admin_rights import UserRestricting
from sophie_bot.filters.is_connected import GroupOrConnectedFilter
from sophie_bot.modules.filters.callbacks import SaveFilterCallback
from sophie_bot.modules.filters.filter_wizard import FilterWizardContext
from sophie_bot.modules.filters.utils_.filter_handler_rules import validate_filter_handler
from sophie_bot.modules.logging.events import LogEvent
from sophie_bot.modules.logging.utils import log_event
from sophie_bot.modules.utils_.action_config_wizard.config import ActionWizardDraft
from sophie_bot.modules.utils_.action_config_wizard.state import WizardState
from sophie_bot.utils.handlers import SophieCallbackQueryHandler
from sophie_bot.utils.i18n import gettext as _


class FilterSaveHandler(SophieCallbackQueryHandler):
    @staticmethod
    def filters() -> tuple[CallbackType, ...]:
        return (SaveFilterCallback.filter(), UserRestricting(admin=True), GroupOrConnectedFilter())

    async def save_filter(self, filter_setup: FilterInSetupType) -> None:
        if filter_setup.oid:
            filter_model = await FiltersModel.get_by_id(ObjectId(filter_setup.oid))
            await filter_model.update_fields(filter_setup)
            await filter_model.save()
        else:
            filter_model = filter_setup.to_model(self.connection.db_model.iid)
            await filter_model.save()
        await log_event(
            self.connection.tid,
            self.event.from_user.id,
            LogEvent.FILTER_SAVED,
            {"keyword": filter_setup.handler.keyword},
        )

    async def handle(self) -> Any:
        wizard_state = WizardState(self.state) if isinstance(self.state, FSMContext) else None
        draft_data = await wizard_state.get_draft() if wizard_state is not None else None
        if draft_data is not None:
            draft = ActionWizardDraft.from_data(draft_data)
            context = FilterWizardContext()
            try:
                await context.validate(self.connection.db_model.iid, draft, self.event, self.connection)
                await context.commit(self.connection.db_model.iid, draft, self.event, self.connection)
            except (TypeError, ValueError) as error:
                return await self.event.answer(str(error))
            if wizard_state is not None:
                await wizard_state.clear()
                await wizard_state.clear_fsm()
            return await self.edit_text(
                Template(_("Filter on {keyword} was saved."), keyword=Code(draft.metadata["handler"])).to_html()
            )

        try:
            filter_item: FilterInSetupType = await FilterInSetupType.get_filter(self.state)
        except ValueError:
            return await self.event.answer(_("Continuing setup is only possible by the same user who started it."))
        if not filter_item.actions:
            return await self.event.answer(_("No actions configured"))
        if not await validate_filter_handler(self.event, filter_item.handler.keyword, self.connection, filter_item.oid):
            return
        await self.save_filter(filter_item)
        return await self.edit_text(
            Template(_("Filter on {keyword} was saved."), keyword=Code(filter_item.handler.keyword)).to_html()
        )
