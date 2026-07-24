from __future__ import annotations


class _FakeNote:
    last_get_by_notenames: tuple[object, tuple[str, ...]] | None = None

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.names = kwargs["names"]

    @staticmethod
    async def get_by_notenames(chat_iid: object, notenames: tuple[str, ...]) -> None:
        _FakeNote.last_get_by_notenames = (chat_iid, notenames)
        return None

    async def insert(self) -> None:
        return None
