from typing import Any

from sophie_bot.services.i18n import i18n as production_i18n
from sophie_bot.utils.i18n import LazyProxy


def test_test_i18n_matches_production_and_actually_loads_catalogs(i18n_context: Any) -> None:
    """Guard the session i18n fixture against silently finding no catalogs.

    It is built separately from the production instance, so it can drift from it. When it
    does, it degrades quietly: the wrong domain simply matches no .mo files, leaving
    available_locales empty, and any code under test that validates against it rejects
    every locale while still looking like it works.
    """
    assert i18n_context.available_locales, "test i18n found no catalogs -- check domain/path"
    assert i18n_context.available_locales == production_i18n.available_locales
    assert i18n_context.default_locale == production_i18n.default_locale


def test_lazy_proxy_does_not_cache_values_between_contexts() -> None:
    selected_language = {"code": "en_US"}
    proxy = LazyProxy(lambda: f"translated:{selected_language['code']}")

    assert str(proxy) == "translated:en_US"

    selected_language["code"] = "uk_UA"

    assert str(proxy) == "translated:uk_UA"
