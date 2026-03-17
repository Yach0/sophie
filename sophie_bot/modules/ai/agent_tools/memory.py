from pydantic_ai import RunContext, Tool

from sophie_bot.db.models.ai.ai_memory import AIMemoryModel
from sophie_bot.modules.ai.utils.ai_tool_context import SophieAIToolContenxt
from sophie_bot.metrics import track_ai_tool


class MemoryAgentTool:
    @staticmethod
    async def tool_call(ctx: RunContext[SophieAIToolContenxt], information_to_save: str):
        async with track_ai_tool("write_memory"):
            await AIMemoryModel.append_line(ctx.deps.connection.db_model, information_to_save)

    def __new__(cls):
        return Tool(
            cls.tool_call, name="write_memory", description="Save information to the long term memory", takes_ctx=True
        )


class ForgetMemoryAgentTool:
    @staticmethod
    async def tool_call(ctx: RunContext[SophieAIToolContenxt], index: int):
        async with track_ai_tool("forget_memory"):
            # index is 1-based from the system prompt
            await AIMemoryModel.remove_line_by_index(ctx.deps.connection.db_model.iid, index - 1)

    def __new__(cls):
        return Tool(
            cls.tool_call,
            name="forget_memory",
            description="Forget information from the long term memory by its index",
            takes_ctx=True,
        )
