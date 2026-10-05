import pytest

from sophie_bot.utils.command_names import normalize_command_name


@pytest.mark.parametrize("ignore_case", [True, False])
@pytest.mark.parametrize(
    ("name", "case_sensitive", "case_insensitive"),
    [
        ("ReSeT_-All__--Warns", "ReSeTAllWarns", "resetallwarns"),
        ("Stra_ße", "Straße", "strasse"),
        ("reset.all warns", "reset.all warns", "reset.all warns"),
        ("", "", ""),
    ],
)
def test_normalize_command_name(name: str, case_sensitive: str, case_insensitive: str, ignore_case: bool) -> None:
    assert normalize_command_name(name, ignore_case=ignore_case) == (
        case_insensitive if ignore_case else case_sensitive
    )
