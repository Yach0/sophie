from __future__ import annotations

from dataclasses import dataclass, replace

from sophie_bot.utils.i18n import LazyProxy
from sophie_bot.utils.i18n import lazy_gettext as l_


@dataclass(frozen=True, slots=True)
class AITool:
    name: str
    label: LazyProxy
    emoji: str
    custom_emoji_id: str | None = None
    activity_texts: tuple[LazyProxy, ...] = ()
    display_in_ai_header: bool = True

    def display_label(self) -> str:
        return str(self.label)


_WEB_SEARCH = AITool(
    "web_search",
    l_("Search"),
    "🔍",
    "5535248817659576336",
    (
        l_("Searching the web..."),
        l_("Looking it up online..."),
        l_("Browsing the internet..."),
        l_("Checking sources online..."),
    ),
)
AI_TOOLS: tuple[AITool, ...] = (
    _WEB_SEARCH,
    replace(_WEB_SEARCH, name="kagi_search"),
    replace(_WEB_SEARCH, name="tinyfish_search"),
    replace(_WEB_SEARCH, name="tavily_search"),
    AITool(
        "get_notes",
        l_("Notes"),
        "📝",
        "5537203062138994712",
        (
            l_("Scanning notes..."),
            l_("Looking through notes..."),
            l_("Checking saved notes..."),
            l_("Gathering chat notes..."),
        ),
    ),
    AITool(
        "get_note_content",
        l_("Notes"),
        "📝",
        "5537203062138994712",
        (
            l_("Reading note..."),
            l_("Fetching note content..."),
            l_("Opening the note..."),
            l_("Checking the note contents..."),
        ),
    ),
    AITool(
        "save_note",
        l_("Save notes"),
        "📝",
        "5537203062138994712",
        (
            l_("Saving note..."),
            l_("Writing to notes..."),
            l_("Storing the note..."),
            l_("Updating saved notes..."),
        ),
    ),
    AITool(
        "delete_note",
        l_("Delete notes"),
        "📝",
        "5537203062138994712",
        (
            l_("Deleting note..."),
            l_("Removing note..."),
            l_("Removing the saved note..."),
            l_("Clearing the note..."),
        ),
    ),
    AITool(
        "write_memory",
        l_("Memory"),
        "🧠",
        "5535287489545109527",
        (
            l_("Updating memory..."),
            l_("Saving to memory..."),
            l_("Recording that for later..."),
            l_("Refreshing memory..."),
        ),
    ),
    AITool(
        "forget_memory",
        l_("Memory"),
        "🧠",
        "5535287489545109527",
        (
            l_("Removing from memory..."),
            l_("Forgetting..."),
            l_("Clearing that memory..."),
            l_("Updating what I remember..."),
        ),
    ),
    AITool(
        "research_topic",
        l_("Research"),
        "🔬",
        "5535365052359507996",
        (
            l_("Researching the topic..."),
            l_("Comparing sources..."),
            l_("Digging into the details..."),
            l_("Following leads..."),
        ),
    ),
    AITool(
        "sophie_help",
        l_("Help"),
        "📖",
        "5535039193190760468",
        (
            l_("Checking the documentation..."),
            l_("Looking up how Sophie works..."),
            l_("Reading Sophie help..."),
            l_("Finding the right guide..."),
        ),
    ),
    AITool(
        "sophie_inspect",
        l_("Source Inspection"),
        "🔧",
        activity_texts=(
            l_("Digging through my own sources..."),
            l_("Reading my own code..."),
            l_("Inspecting my source code..."),
            l_("Tracing how Sophie works..."),
        ),
    ),
)
AI_TOOLS_BY_NAME: dict[str, AITool] = {tool.name: tool for tool in AI_TOOLS}
