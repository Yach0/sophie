from sophie_bot.db.models.beta import PreferredMode
from sophie_bot.modules.troubleshooters.handlers.beta_state import preferred_mode_by_user_mode


def test_user_modes_map_to_existing_database_modes() -> None:
    assert preferred_mode_by_user_mode == {
        "auto": PreferredMode.auto,
        "latest": PreferredMode.beta,
        "old": PreferredMode.stable,
        "beta": PreferredMode.beta,
        "stable": PreferredMode.stable,
    }
