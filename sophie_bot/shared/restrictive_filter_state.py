_restrictive_triggered: dict[int, bool] = {}


def mark_restrictive_triggered(message_id: int) -> None:
    _restrictive_triggered[message_id] = True


def is_restrictive_triggered(message_id: int) -> bool:
    return _restrictive_triggered.pop(message_id, False)
