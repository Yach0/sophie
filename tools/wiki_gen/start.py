import asyncio

from sophie_bot.config import CONFIG
from sophie_bot.modules import discover_modules
from sophie_bot.modules.help.utils.extract_info import build_help_catalog
from sophie_bot.utils.feature_flags import FEATURE_FLAGS, get_default_value
from sophie_bot.utils.logger import log
from tools.wiki_gen.generate_pages import generate_wiki_pages


async def _generate_wiki() -> None:
    registry = discover_modules(["*"], CONFIG.modules_not_load)
    feature_defaults = {
        feature: get_default_value(feature) for feature in FEATURE_FLAGS
    }
    await build_help_catalog(registry, feature_defaults)
    await generate_wiki_pages(registry.help_modules)


def generate_wiki() -> None:
    """Generate help pages from import-only module metadata."""
    log.info("Starting wiki generation task...")
    asyncio.run(_generate_wiki())


if __name__ == "__main__":
    generate_wiki()
