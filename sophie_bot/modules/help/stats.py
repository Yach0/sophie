from stfu_tg import Code, KeyValue, Section, Template

from sophie_bot.services.application import ApplicationServices


async def module_stats(*, services: ApplicationServices) -> Section:
    modules = services.modules.help_modules.values()

    return Section(
        Template(
            "{modules} modules has {cmds} commands",
            modules=Code(len(modules)),
            cmds=Code(sum(len(module.handlers) for module in modules)),
        ),
        KeyValue(
            "With arguments definition",
            Code(sum(sum(1 for command in module.handlers if command.args) for module in modules)),
        ),
        title="Help",
    )
