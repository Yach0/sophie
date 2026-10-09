def normalize_command_name(command_name: str, *, ignore_case: bool) -> str:
    """Remove literal command separators and optionally fold case for matching."""
    normalized_name = command_name.replace("_", "").replace("-", "")
    return normalized_name.casefold() if ignore_case else normalized_name
