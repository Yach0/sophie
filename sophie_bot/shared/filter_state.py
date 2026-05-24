_restrictive_filter_triggered: dict[int, bool] = {}


def set_restrictive_triggered(message_id: int, triggered: bool) -> None:
    _restrictive_filter_triggered[message_id] = triggered


def is_restrictive_triggered(message_id: int) -> bool:
    return _restrictive_filter_triggered.get(message_id, False)


def clear_restrictive_state(message_id: int) -> None:
    _restrictive_filter_triggered.pop(message_id, None)
