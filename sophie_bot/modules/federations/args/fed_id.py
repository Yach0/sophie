from typing import Any, Optional

from ass_tg.entities import ArgEntities
from ass_tg.exceptions import ArgStrictError, ArgSimpleTypeError
from ass_tg.types import TextArg
from stfu_tg import Code, Template

from sophie_bot.constants import FEDERATION_ID_HYPHEN_COUNT, FEDERATION_ID_PART_LENGTH
from sophie_bot.db.models.federations import Federation
from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.i18n import lazy_gettext as l_


class FedIdArg(TextArg):
    """Argument type for federation IDs with validation and lookup."""

    def __init__(self, description: Optional[LazyProxy] = None):
        super().__init__(description or l_("Federation ID"))

    def check(self, text: str, entities: ArgEntities) -> bool:
        """Check if text has valid federation ID format and no overlapping entities."""
        fed_id, *_rest = text.split(maxsplit=1) or (text,)

        if fed_id.count("-") != FEDERATION_ID_HYPHEN_COUNT:
            raise ArgSimpleTypeError(
                Template(
                    _("Invalid federation ID format. Federation IDs must contain exactly {count} hyphens."),
                    count=FEDERATION_ID_HYPHEN_COUNT,
                ).to_html()
            )

        if entities.get_overlapping(0, len(fed_id)):
            raise ArgSimpleTypeError(_("Federation ID cannot contain formatting or mentions."))

        return True

    async def parse(self, text: str, offset: int, entities: ArgEntities) -> tuple[int, Federation]:
        """Parse and validate federation ID, return Federation model."""
        fed_id, *_rest = text.split(maxsplit=1) or (text,)

        # Lookup federation
        federation = await Federation.find_one(Federation.fed_id == fed_id)
        if not federation:
            raise ArgStrictError(Template(_("Federation with ID {fed_id} not found."), fed_id=Code(fed_id)).to_html())

        return len(fed_id), federation

    def needed_type(self) -> tuple[LazyProxy, LazyProxy]:
        return l_("Federation ID (format: xxxx-xxxx-xxxx-xxxx)"), l_("Federation IDs")

    def unparse(self, data: Any, **kwargs) -> str:
        return data.fed_id

    @property
    def examples(self) -> Optional[dict[str, Optional[LazyProxy]]]:
        example_id = "-".join(["x" * FEDERATION_ID_PART_LENGTH] * FEDERATION_ID_HYPHEN_COUNT)
        return {example_id: l_("Federation ID example")}
