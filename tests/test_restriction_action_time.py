from datetime import timedelta

import pytest
from ass_tg.entities import ArgEntities
from ass_tg.exceptions import ArgCustomError

from sophie_bot.modules.restrictions.handlers.base import RestrictionActionTimeArg


@pytest.mark.asyncio
async def test_action_time_accepts_seconds_when_duration_is_large_enough() -> None:
    parsed_length, duration = await RestrictionActionTimeArg().parse("1m10s", 0, ArgEntities([]))

    assert parsed_length == 5
    assert duration == timedelta(minutes=1, seconds=10)


@pytest.mark.asyncio
async def test_action_time_rejects_seconds_only_as_too_small() -> None:
    with pytest.raises(ArgCustomError) as error:
        await RestrictionActionTimeArg().parse("10s", 0, ArgEntities([]))

    assert str(error.value.doc[0]) == "The duration is too small. It must be at least 1 minute."
