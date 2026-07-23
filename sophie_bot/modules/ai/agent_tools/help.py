from __future__ import annotations

from ass_tg.types.base_abc import ArgFabric
from pydantic_ai import RunContext, Tool
from stfu_tg import Doc, KeyValue, Section, VList
from stfu_tg.doc import Element

from sophie_bot.config import CONFIG
from sophie_bot.metrics import track_ai_tool
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContext
from sophie_bot.modules.help.utils.extract_info import HELP_MODULES
from sophie_bot.modules.help.utils.wiki_pages import get_wiki_pages, read_wiki_page
from sophie_bot.utils.i18n import gettext as _
from sophie_bot.utils.logger import log


def format_ass_arg_data(arg: ArgFabric) -> Section:
    return Section(_("Can be empty") if arg.can_be_empty else None, title=arg.description)


def _wiki_page_index() -> Element:
    pages = get_wiki_pages()
    if not pages:
        return Doc()
    return Section(
        _("Call this tool again with one of these slugs as `page` to read it in full:"),
        VList(*sorted(pages)),
        title=_("Wiki pages"),
    )


def _read_page(page: str) -> str:
    text = read_wiki_page(page)
    if text is None:
        available = ", ".join(sorted(get_wiki_pages())) or _("none")
        return _("No wiki page named {page}. Available pages: {available}").format(page=page, available=available)
    return text


async def sophie_help(ctx: RunContext[SophieAIToolContext], page: str | None = None) -> str:
    """Get Sophie's documentation: every module and command, or the full text of one wiki page.

    Args:
        page: Slug of a wiki page to read in full, from the list this tool returns without it.
              Leave empty to get the overview of all modules and commands first.
    """
    if page:
        return _read_page(page)

    async with track_ai_tool("help"):
        if not HELP_MODULES:
            return _("No modules found.")

        doc = Doc(
            _(
                "SOPHIE BOT HELP CONTEXT\n"
                "\n"
                "Commands are organized by module. Each module represents a feature area of the bot.\n"
                "\n"
                "Module fields:\n"
                "  - Name: the human-readable name of the module.\n"
                "  - Icon: emoji representing the module.\n"
                "  - Description: what the module does.\n"
                "  - Info: additional information about the module.\n"
                "  - Public: whether the module is shown in public listings.\n"
                "  - Wiki: whether the module has a wiki page.\n"
                "\n"
                "Command fields:\n"
                "  - Commands: slash commands that trigger the handler.\n"
                "  - Description: human-readable summary of what the handler does.\n"
                "  - Arguments: expected arguments (order matters).\n"
                "  - Context: where the command can be used (PM / groups / both).\n"
                "  - Permissions: whether admin / OP rights are required.\n"
                "  - Disableable: name of the feature flag used to disable this command in a chat, if any.\n"
                "\n"
            )
        )

        modules_sections: list[Element] = []
        for module_name, module_help in HELP_MODULES.items():
            module_info_parts = [
                KeyValue(_("Name"), str(module_help.name)),
                KeyValue(_("Icon"), module_help.icon),
            ]
            if module_help.description:
                module_info_parts.append(KeyValue(_("Description"), str(module_help.description)))
            if module_help.info:
                module_info_parts.append(KeyValue(_("Info"), str(module_help.info)))
            module_info_parts.append(KeyValue(_("Wiki page"), CONFIG.wiki_modules_link + module_name))

            commands_elements: list[Element] = []
            if module_help.handlers:
                for handler in module_help.handlers:
                    commands_elements.append(
                        Section(
                            KeyValue(_("Description"), handler.description) if handler.description else None,
                            Section(
                                VList(*(format_ass_arg_data(arg) for arg in handler.args.values())),
                                title=_("Arguments"),
                            )
                            if handler.args
                            else _("This command has no arguments."),
                            _("Can be used only in private chats (PM / DM)") if handler.only_pm else None,
                            _("Can be used only in groups / supergroups") if handler.only_chats else None,
                            _("Can be used in both private chats and groups")
                            if not handler.only_pm and not handler.only_chats
                            else None,
                            _("Can be used only by admins") if handler.only_admin else None,
                            _("Can be used only by OP") if handler.only_op else None,
                            _("No special permissions required")
                            if not handler.only_admin and not handler.only_op
                            else None,
                            KeyValue(_("Disableable"), handler.disableable) if handler.disableable else None,
                            title=" / ".join(f"/{command}" for command in handler.cmds),
                        )
                    )

            module_section_parts: list[Element | str] = [
                Section(*module_info_parts, title=_("Module Information")),
            ]
            if commands_elements:
                module_section_parts.append(Section(*commands_elements, title=_("Commands")))
            else:
                module_section_parts.append(_("No commands in this module."))

            modules_sections.append(Section(*module_section_parts, title=f"{module_help.icon} {module_name}"))

        md_text = VList(doc, *modules_sections, _wiki_page_index()).to_md()
        log.debug("help: generated help text", text_length=len(md_text), chat_id=ctx.deps.chat_tid)
        return md_text


help_tool = Tool(
    sophie_help,
    name="help",
    description=(
        "Get Sophie's documentation. Call it with no arguments for every module and command, then "
        "call it again with a page slug from the list it returns when a topic needs more detail. "
        "Run this before helping users with Sophie, and never invent commands or arguments."
    ),
    takes_ctx=True,
    docstring_format="google",
    require_parameter_descriptions=True,
)
