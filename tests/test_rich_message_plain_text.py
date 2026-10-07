from aiogram.types import (
    Message,
    RichBlockBlockQuotation,
    RichBlockCaption,
    RichBlockCollage,
    RichBlockDetails,
    RichBlockList,
    RichBlockListItem,
    RichBlockMathematicalExpression,
    RichBlockParagraph,
    RichBlockTable,
    RichBlockTableCell,
    RichMessage,
    RichTextBold,
    RichTextCustomEmoji,
)

from sophie_bot.modules.ai.utils.ai_header import AI_BATTERY_CUSTOM_EMOJI_IDS, AI_CUSTOM_EMOJI_ID
from sophie_bot.modules.ai.utils.self_reply import cut_titlebar, message_text
from sophie_bot.utils.rich_message import rich_message_to_plain_text


def test_list_labels_and_nested_block_boundaries_are_preserved() -> None:
    message = RichMessage(
        blocks=[
            RichBlockParagraph(text="Before"),
            RichBlockList(
                items=[
                    RichBlockListItem(
                        label="1.",
                        blocks=[RichBlockParagraph(text="First"), RichBlockParagraph(text="Continuation")],
                    ),
                    RichBlockListItem(
                        label="2.",
                        blocks=[
                            RichBlockParagraph(text="Second"),
                            RichBlockList(
                                items=[RichBlockListItem(label="•", blocks=[RichBlockParagraph(text="Nested")])]
                            ),
                        ],
                    ),
                ]
            ),
            RichBlockParagraph(text="After"),
        ]
    )

    assert rich_message_to_plain_text(message) == "Before\n1. First\nContinuation\n2. Second\n• Nested\nAfter"


def test_details_keep_summaries_nested_contents_and_credits() -> None:
    message = RichMessage(
        blocks=[
            RichBlockDetails(
                summary=[RichTextBold(text="Summary"), " remains visible"],
                blocks=[
                    RichBlockParagraph(text="Introduction"),
                    RichBlockDetails(
                        summary="Nested summary",
                        blocks=[
                            RichBlockBlockQuotation(blocks=[RichBlockParagraph(text="Quoted text")], credit="Author")
                        ],
                    ),
                    RichBlockCollage(
                        blocks=[RichBlockMathematicalExpression(expression="x + y")],
                        caption=RichBlockCaption(text="Gallery", credit="Photographer"),
                    ),
                ],
            )
        ]
    )

    assert rich_message_to_plain_text(message) == (
        "Summary remains visible\nIntroduction\nNested summary\nQuoted text (Author)\nx + y\nGallery (Photographer)"
    )


def test_table_keeps_row_cell_boundaries_and_caption() -> None:
    table = RichBlockTable(
        cells=[
            [
                RichBlockTableCell(text="Name", align="left", valign="top"),
                RichBlockTableCell(text="Value", align="left", valign="top"),
            ],
            [
                RichBlockTableCell(text=[RichTextBold(text="Alice"), " Smith"], align="left", valign="top"),
                RichBlockTableCell(
                    text=RichTextCustomEmoji(custom_emoji_id="123", alternative_text="✨"),
                    align="left",
                    valign="top",
                ),
            ],
        ],
        caption="Table caption",
    )
    rich = RichMessage(blocks=[table, RichBlockParagraph(text="After table")])

    assert rich_message_to_plain_text(rich) == "Name | Value\nAlice Smith | ✨\nTable caption\nAfter table"
    message = Message.model_validate(
        {
            "message_id": 1,
            "date": 1790115467,
            "chat": {"id": 483808054, "type": "private"},
            "rich_message": rich,
        }
    )
    assert message_text(message) == "Name | Value\nAlice Smith | ✨\nTable caption\nAfter table"


def test_self_reply_keeps_nested_content_before_battery_footer() -> None:
    rich = RichMessage(
        blocks=[
            RichBlockParagraph(
                text=[RichTextCustomEmoji(custom_emoji_id=AI_CUSTOM_EMOJI_ID, alternative_text="✨"), " Answer"]
            ),
            RichBlockDetails(
                summary="Details",
                blocks=[
                    RichBlockList(
                        items=[RichBlockListItem(label="1.", blocks=[RichBlockParagraph(text="Nested answer")])]
                    ),
                    RichBlockMathematicalExpression(expression="x + y"),
                ],
            ),
            RichBlockParagraph(
                text=[
                    RichTextCustomEmoji(
                        custom_emoji_id=next(iter(AI_BATTERY_CUSTOM_EMOJI_IDS)), alternative_text="🔋"
                    ),
                    " 95%",
                ]
            ),
        ]
    )
    message = Message.model_validate(
        {
            "message_id": 1,
            "date": 1790115467,
            "chat": {"id": 483808054, "type": "private"},
            "text": "Plain fallback",
            "rich_message": rich,
        }
    )

    assert message_text(message) == "✨ Answer\nDetails\n1. Nested answer\nx + y\n🔋 95%"
    assert cut_titlebar(message) == "Answer\nDetails\n1. Nested answer\nx + y"
