from __future__ import annotations

from beanie import Document

from sophie_bot.db.models.ai import (
    AIAutotranslateModel,
    AIChatSummaryLine as AIChatSummaryLine,
    AIChatSummaryModel,
    AIMemoryModel,
    AIMode as AIMode,
    AIModeModel,
    AIModeratorModel,
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
from sophie_bot.db.models.communities import (
    CommunityBanModel,
    CommunityModel,
    CommunityTask,
)
from sophie_bot.db.models.disabling import DisablingModel
from sophie_bot.db.models.federations import (
    Federation,
    FederationBan,
    FederationTask,
)
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
from sophie_bot.db.models.op_debug_snapshot import OpDebugSnapshotModel
from sophie_bot.db.models.warns import WarnModel, WarnSettingsModel
from sophie_bot.db.models.ws_user import WSUserModel
from sophie_bot.db.models.spam_match import SpamMatchModel

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
    AIModeModel,
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
    AIQuotaModel,
    AntifloodModel,
    ApiTokenModel,
    Federation,
    FederationBan,
    FederationTask,
    CommunityModel,
    CommunityBanModel,
    CommunityTask,
    RefreshTokenModel,
    OpDebugSnapshotModel,
    FeatureFlagOverride,
    SpamMatchModel,
]

__all__ = [
    "AIAutotranslateModel",
    "AIChatSummaryLine",
    "AIChatSummaryModel",
    "AIMemoryModel",
    "AIMode",
    "AIModeModel",
    "AIModeratorModel",
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
    "CommunityBanModel",
    "CommunityModel",
    "CommunityTask",
    "DisablingModel",
    "FeatureFlagOverride",
    "Federation",
    "FederationBan",
    "FederationTask",
    "FiltersModel",
    "GlobalSettings",
    "GreetingsModel",
    "LanguageModel",
    "LocksModel",
    "LogModel",
    "MigrationState",
    "NoteModel",
    "OpDebugSnapshotModel",
    "PrivateNotesModel",
    "RefreshTokenModel",
    "RulesModel",
    "UserInGroupModel",
    "WSUserModel",
    "WarnModel",
    "WarnSettingsModel",
    "models",
    "SpamMatchModel",
]
