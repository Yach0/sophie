from __future__ import annotations

from beanie import Document

from sophie_bot.db.models.ai import (
    AIAutotranslateModel,
    AIChatSummaryLine as AIChatSummaryLine,
    AIChatSummaryModel,
    AIEnabledModel,
    AIMemoryModel,
    AIModeratorModel,
    AIProviderModel,
    AIQuotaModel,
    AIUsageModel,
)
from sophie_bot.db.models.antiflood import AntifloodModel
from sophie_bot.db.models.api_token import ApiTokenModel
from sophie_bot.db.models.beta import BetaModeModel
from sophie_bot.db.models.chat import ChatModel, ChatTopicModel, UserInGroupModel
from sophie_bot.db.models.chat_leave_log import ChatLeaveLogModel
from sophie_bot.db.models.chat_admin import ChatAdminModel
from sophie_bot.db.models.chat_connection_settings import ChatConnectionSettingsModel
from sophie_bot.db.models.chat_connections import ChatConnectionModel
from sophie_bot.db.models.chat_photo import ChatPhotoModel
from sophie_bot.db.models.disabling import DisablingModel
from sophie_bot.db.models.federations import Federation, FederationBan, FederationImportTask, FederationExportTask
from sophie_bot.db.models.feature_flag import FeatureFlagOverride
from sophie_bot.db.models.filters import FiltersModel
from sophie_bot.db.models.greetings import GreetingsModel
from sophie_bot.db.models.locks import LocksModel
from sophie_bot.db.models.language import LanguageModel
from sophie_bot.db.models.log import LogModel
from sophie_bot.db.models.migrations import MigrationState
from sophie_bot.db.models.notes import NoteModel
from sophie_bot.db.models.privatenotes import PrivateNotesModel
from sophie_bot.db.models.refresh_token import RefreshTokenModel
from sophie_bot.db.models.rules import RulesModel
from sophie_bot.db.models.settings_keyvalue import GlobalSettings
from sophie_bot.db.models.op_debug_feature_request import OpDebugFeatureRequestModel
from sophie_bot.db.models.op_debug_snapshot import OpDebugSnapshotModel
from sophie_bot.db.models.warns import WarnModel, WarnSettingsModel
from sophie_bot.db.models.ws_user import WSUserModel

models: list[type[Document]] = [
    ChatModel,
    ChatPhotoModel,
    UserInGroupModel,
    ChatTopicModel,
    ChatLeaveLogModel,
    ChatAdminModel,
    LanguageModel,
    LogModel,
    MigrationState,
    ChatConnectionModel,
    ChatConnectionSettingsModel,
    NoteModel,
    BetaModeModel,
    GlobalSettings,
    AIEnabledModel,
    AIUsageModel,
    AIAutotranslateModel,
    AIModeratorModel,
    AIMemoryModel,
    AIChatSummaryModel,
    DisablingModel,
    PrivateNotesModel,
    RulesModel,
    GreetingsModel,
    WSUserModel,
    WarnSettingsModel,
    WarnModel,
    FiltersModel,
    LocksModel,
    AIProviderModel,
    AIQuotaModel,
    AntifloodModel,
    ApiTokenModel,
    Federation,
    FederationBan,
    FederationImportTask,
    FederationExportTask,
    RefreshTokenModel,
    OpDebugFeatureRequestModel,
    OpDebugSnapshotModel,
    FeatureFlagOverride,
]

__all__ = [
    "AIAutotranslateModel",
    "AIChatSummaryLine",
    "AIChatSummaryModel",
    "AIEnabledModel",
    "AIMemoryModel",
    "AIModeratorModel",
    "AIProviderModel",
    "AIQuotaModel",
    "AIUsageModel",
    "AntifloodModel",
    "ApiTokenModel",
    "BetaModeModel",
    "ChatAdminModel",
    "ChatConnectionModel",
    "ChatConnectionSettingsModel",
    "ChatLeaveLogModel",
    "ChatModel",
    "ChatPhotoModel",
    "ChatTopicModel",
    "DisablingModel",
    "FeatureFlagOverride",
    "Federation",
    "FederationBan",
    "FederationExportTask",
    "FederationImportTask",
    "FiltersModel",
    "GlobalSettings",
    "GreetingsModel",
    "LanguageModel",
    "LocksModel",
    "LogModel",
    "MigrationState",
    "NoteModel",
    "OpDebugFeatureRequestModel",
    "OpDebugSnapshotModel",
    "PrivateNotesModel",
    "RefreshTokenModel",
    "RulesModel",
    "UserInGroupModel",
    "WSUserModel",
    "WarnModel",
    "WarnSettingsModel",
    "models",
]
