from enum import StrEnum


class MemoryCommand(StrEnum):
    browse = "ai_memory"
    history = "ai_memory_history"
    sources = "ai_memory_sources"
    correct = "ai_memory_correct"
    forget = "ai_memory_forget"
    off = "ai_memory_off"
    on = "ai_memory_on"
    help = "ai_memory_help"
