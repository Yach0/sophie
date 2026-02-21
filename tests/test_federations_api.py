from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from beanie import PydanticObjectId

from sophie_bot.db.models.federations import Federation
from sophie_bot.modules.federations.api.routers.manage import create_federation, list_federations
from sophie_bot.modules.federations.api.schemas import FederationCreate


@pytest.mark.asyncio
async def test_list_federations_includes_owner_and_admin():
    user = MagicMock()
    user.tid = 1001

    creator_model = MagicMock()
    creator_model.iid = PydanticObjectId("507f1f77bcf86cd799439021")

    owned_federation = MagicMock()
    owned_federation.fed_id = "fed-owned"
    owned_federation.fed_name = "Owned"
    owned_federation.creator.id = creator_model.iid
    owned_federation.log_chat = None

    admin_federation = MagicMock()
    admin_federation.fed_id = "fed-admin"
    admin_federation.fed_name = "Admin"
    admin_federation.creator.id = creator_model.iid
    admin_federation.log_chat = None

    owned_query = MagicMock()
    owned_query.to_list = AsyncMock(return_value=[owned_federation])
    admin_query = MagicMock()
    admin_query.to_list = AsyncMock(return_value=[admin_federation])

    with (
        patch.object(Federation, "creator", new=MagicMock(), create=True),
        patch.object(Federation, "admins", new=MagicMock(), create=True),
        patch(
            "sophie_bot.modules.federations.api.routers.manage.Federation.find",
            side_effect=[owned_query, admin_query],
        ),
        patch(
            "sophie_bot.modules.federations.api.routers.manage.get_current_user",
            new_callable=AsyncMock,
        ) as mock_batch_resolve,
    ):
        mock_batch_resolve.return_value = {
            "creator_map": {user.tid: creator_model.iid, 2002: creator_model.iid},
            "log_chat_map": {},
            "chat_map": {},
        }

        response = await list_federations(user)

    assert len(response) == 2
    response_ids = {item.fed_id for item in response}
    assert response_ids == {"fed-owned", "fed-admin"}


@pytest.mark.asyncio
async def test_create_federation_returns_summary():
    user = MagicMock()
    user.tid = 1001

    creator_model = MagicMock()
    creator_model.iid = PydanticObjectId("507f1f77bcf86cd799439031")

    federation = MagicMock()
    federation.fed_id = "fed-created"
    federation.fed_name = "Created"
    federation.creator.id = creator_model.iid
    federation.log_chat = None

    payload = FederationCreate(name="Created")

    with (
        patch(
            "sophie_bot.modules.federations.api.routers.manage.FederationManageService.create_federation",
            new_callable=AsyncMock,
        ) as mock_create_federation,
        patch(
            "sophie_bot.modules.federations.api.routers.manage.get_current_user",
            new_callable=AsyncMock,
        ) as mock_batch_resolve,
    ):
        mock_create_federation.return_value = federation
        mock_batch_resolve.return_value = {
            "creator_map": {user.tid: creator_model.iid},
            "log_chat_map": {},
            "chat_map": {},
        }

        response = await create_federation(payload, user)

    assert response.fed_id == "fed-created"
    assert response.creator_iid == creator_model.iid
