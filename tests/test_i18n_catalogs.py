from __future__ import annotations

from pathlib import Path
from string import Formatter

from babel.messages.catalog import Catalog
from babel.messages.pofile import read_po

LOCALES_DIR = Path(__file__).parents[1] / "locales"


def _read_catalog(path: Path) -> Catalog:
    locale = path.parts[-3] if path.parent.name == "LC_MESSAGES" else None
    with path.open("rb") as catalog_file:
        return read_po(catalog_file, locale=locale)


def _format_fields(value: str) -> set[str]:
    return {field_name for _, field_name, _, _ in Formatter().parse(value) if field_name}


def test_locale_catalogs_match_the_source_catalog() -> None:
    source_catalog = _read_catalog(LOCALES_DIR / "sophie.pot")
    source_ids = {message.id for message in source_catalog if message.id}

    for path in sorted(LOCALES_DIR.glob("*/LC_MESSAGES/sophie.po")):
        catalog = _read_catalog(path)
        assert {message.id for message in catalog if message.id} == source_ids


def test_locale_catalogs_have_safe_translations() -> None:
    for path in sorted(LOCALES_DIR.glob("*/LC_MESSAGES/sophie.po")):
        catalog = _read_catalog(path)
        assert not catalog.fuzzy
        for message in catalog:
            if not message.id:
                continue
            assert not message.fuzzy
            source_id = message.id[0] if isinstance(message.id, tuple) else message.id
            translations = message.string if isinstance(message.string, tuple) else (message.string,)
            for translation in translations:
                if not translation:
                    continue
                assert "},{" not in translation
                assert _format_fields(source_id) == _format_fields(translation)

            if isinstance(message.string, tuple):
                assert len(message.string) == catalog.num_plurals


def test_locale_catalogs_use_relative_source_references() -> None:
    for path in sorted(LOCALES_DIR.glob("*/LC_MESSAGES/sophie.po")):
        assert "/home/" not in path.read_text()
