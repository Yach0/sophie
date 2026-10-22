"""Isolated reviewer smoke-test fixture; must not be imported into production."""


def average(values: list[float]) -> float:
    total = sum(values)
    return total / len(values)
