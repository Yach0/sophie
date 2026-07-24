"""Every `Link` field must compile `.id` comparisons to the DBRef key MongoDB stores.

`Model.link.id == iid` only works when Beanie resolved the field's annotation to a real
`Link`; a `Link["ChatModel"]` forward reference that pydantic could not resolve at class
creation compiles to `{"link.id": ...}` instead of `{"link.$id": ...}`. Both are valid
queries, but the first matches nothing at all, in production and under mongomock alike — a
whole feature goes silently dead. This guards the module layout that keeps them resolvable.
"""

from __future__ import annotations

from typing import Any

from beanie import PydanticObjectId

from sophie_bot.db.models import models


async def test_link_comparisons_target_the_dbref_id(db_init: Any) -> None:
    some_iid = PydanticObjectId()

    unresolved: list[str] = []
    for model in models:
        for field_name in model.get_link_fields() or {}:
            query = model.find(getattr(model, field_name).id == some_iid).get_filter_query()
            if not any(key.endswith(".$id") for key in query):
                unresolved.append(f"{model.__name__}.{field_name} -> {list(query)}")

    assert not unresolved, f"Link annotations Beanie could not resolve: {unresolved}"
