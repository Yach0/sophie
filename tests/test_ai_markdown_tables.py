from stfu_tg.ai_md import ai_markdown_to_doc


def test_markdown_table_uses_compact_rich_style() -> None:
    doc = ai_markdown_to_doc("| Name | Value |\n| --- | ---: |\n| foo | bar |")

    assert doc.to_rich() == (
        '<table bordered striped compact><tr><th>Name</th><th align="right">Value</th></tr>'
        '<tr><td>foo</td><td align="right">bar</td></tr></table>'
    )
