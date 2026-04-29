from sophie_bot.utils.i18n import LazyProxy


def test_lazy_proxy_does_not_cache_values_between_contexts() -> None:
    selected_language = {"code": "en_US"}
    proxy = LazyProxy(lambda: f"translated:{selected_language['code']}")

    assert str(proxy) == "translated:en_US"

    selected_language["code"] = "uk_UA"

    assert str(proxy) == "translated:uk_UA"
