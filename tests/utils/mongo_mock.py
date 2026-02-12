"""Async MongoDB mock wrapper for mongomock to work with Beanie 2.0.

This module provides an async wrapper around mongomock that emulates
the AsyncMongoClient interface required by Beanie 2.0 and PyMongo 4.x.

Based on the workaround from https://github.com/mongomock/mongomock/issues/916
"""

from __future__ import annotations

import asyncio
from functools import partial, wraps
from typing import TYPE_CHECKING, Any

from mongomock import MongoClient as SyncMongoClient

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class AsyncMongoMockClient:
    """Mock AsyncMongoClient that emulates PyMongo's async interface.

    This is designed to work with Beanie 2.0's expectations.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize with a sync mongomock client."""
        clean_kwargs = {
            key: value for key, value in kwargs.items() if key not in ("io_loop", "maxPoolSize", "minPoolSize")
        }
        self._sync_client = SyncMongoClient(*args, **clean_kwargs)

    async def aconnect(self) -> None:
        """PyMongo AsyncMongoClient connection method."""
        pass

    async def aclose(self) -> None:
        """PyMongo AsyncMongoClient close method."""
        if hasattr(self._sync_client, "close"):
            self._sync_client.close()

    def __getitem__(self, name: str) -> AsyncDatabaseMock:
        """Get database by name - returns AsyncDatabase mock."""
        sync_db = self._sync_client[name]
        return AsyncDatabaseMock(sync_db)

    def get_database(self, name: str, **kwargs: Any) -> AsyncDatabaseMock:
        """Get database by name with options."""
        sync_db = self._sync_client.get_database(name, **kwargs)
        return AsyncDatabaseMock(sync_db)

    @property
    def admin(self) -> AsyncDatabaseMock:
        """Get admin database."""
        return AsyncDatabaseMock(self._sync_client.admin)

    def __getattr__(self, name: str) -> Any:
        """Forward unknown attributes to sync client, making them async if callable."""
        attr = getattr(self._sync_client, name)
        if callable(attr):

            @wraps(attr)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, partial(attr, *args, **kwargs))

            return async_wrapper
        return attr


class AsyncDatabaseMock:
    """Mock async database that emulates PyMongo's async database interface."""

    def __init__(self, sync_db: Any) -> None:
        self._sync_db = sync_db

    def __getitem__(self, name: str) -> AsyncCollectionMock:
        """Get collection by name."""
        sync_collection = self._sync_db[name]
        return AsyncCollectionMock(sync_collection)

    def get_collection(self, name: str, **kwargs: Any) -> AsyncCollectionMock:
        """Get collection by name with options."""
        sync_collection = self._sync_db.get_collection(name, **kwargs)
        return AsyncCollectionMock(sync_collection)

    async def command(self, command: Any, **kwargs: Any) -> dict[str, Any]:
        """Execute database command."""
        if isinstance(command, dict):
            if "buildInfo" in command:
                return {
                    "ok": 1.0,
                    "version": "4.4.0",
                    "gitVersion": "mock",
                    "modules": [],
                    "allocator": "tcmalloc",
                    "storageEngines": ["wiredTiger"],
                }
            if "ping" in command:
                return {"ok": 1.0}

        func = partial(self._sync_db.command, command, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    def __getattr__(self, name: str) -> Any:
        """Forward other database methods, making them async."""
        attr = getattr(self._sync_db, name)
        if callable(attr):

            @wraps(attr)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                func = partial(attr, *args, **kwargs)
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, func)
                if hasattr(result, "__class__") and "mongomock" in str(result.__class__):
                    if "Collection" in str(result.__class__):
                        return AsyncCollectionMock(result)
                return result

            return async_wrapper
        return attr


class AsyncCollectionMock:
    """Mock async collection that emulates PyMongo's async collection interface."""

    def __init__(self, sync_collection: Any) -> None:
        self._sync_collection = sync_collection

    def find(self, *args: Any, **kwargs: Any) -> AsyncCursorMock:
        """Return an async cursor mock."""
        sync_cursor = self._sync_collection.find(*args, **kwargs)
        return AsyncCursorMock(sync_cursor)

    async def find_one(self, *args: Any, **kwargs: Any) -> Any:
        """Find one document."""
        func = partial(self._sync_collection.find_one, *args, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    async def insert_one(self, document: Any, **kwargs: Any) -> Any:
        """Insert one document."""
        func = partial(self._sync_collection.insert_one, document, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    async def insert_many(self, documents: Any, **kwargs: Any) -> Any:
        """Insert many documents."""
        func = partial(self._sync_collection.insert_many, documents, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    async def update_one(self, filter: Any, update: Any, **kwargs: Any) -> Any:  # noqa: A002
        """Update one document."""
        func = partial(self._sync_collection.update_one, filter, update, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    async def update_many(self, filter: Any, update: Any, **kwargs: Any) -> Any:  # noqa: A002
        """Update many documents."""
        func = partial(self._sync_collection.update_many, filter, update, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    async def delete_one(self, filter: Any, **kwargs: Any) -> Any:  # noqa: A002
        """Delete one document."""
        func = partial(self._sync_collection.delete_one, filter, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    async def delete_many(self, filter: Any, **kwargs: Any) -> Any:  # noqa: A002
        """Delete many documents."""
        func = partial(self._sync_collection.delete_many, filter, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    async def count_documents(self, filter: Any, **kwargs: Any) -> int:  # noqa: A002
        """Count documents."""
        func = partial(self._sync_collection.count_documents, filter, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    async def create_index(self, keys: Any, **kwargs: Any) -> str:
        """Create index."""
        func = partial(self._sync_collection.create_index, keys, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    async def drop_index(self, index_or_name: Any, **kwargs: Any) -> None:
        """Drop index."""
        func = partial(self._sync_collection.drop_index, index_or_name, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    async def list_indexes(self, **kwargs: Any) -> Any:
        """List indexes."""
        func = partial(self._sync_collection.list_indexes, **kwargs)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func)

    def __getattr__(self, name: str) -> Any:
        """Forward other collection methods, making them async."""
        attr = getattr(self._sync_collection, name)
        if callable(attr):

            @wraps(attr)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                func = partial(attr, *args, **kwargs)
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, func)
                if hasattr(result, "__class__") and "mongomock" in str(result.__class__):
                    if "Cursor" in str(result.__class__):
                        return AsyncCursorMock(result)
                return result

            return async_wrapper
        return attr


class AsyncCursorMock:
    """Mock async cursor that emulates PyMongo's async cursor interface."""

    def __init__(self, sync_cursor: Any) -> None:
        self._sync_cursor = sync_cursor
        self._data_cache: list[Any] | None = None

    async def to_list(self, length: int | None = None) -> list[Any]:
        """Convert cursor to list - this is what Beanie calls."""
        if self._data_cache is None:
            func = partial(list, self._sync_cursor)
            loop = asyncio.get_event_loop()
            self._data_cache = await loop.run_in_executor(None, func)

        if length is not None:
            return self._data_cache[:length]
        return self._data_cache.copy()

    def __aiter__(self) -> AsyncIterator[Any]:
        return self

    async def __anext__(self) -> Any:
        def get_next():
            try:
                return next(self._sync_cursor)
            except StopIteration:
                return None
            except Exception as e:
                return e

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, get_next)

        if result is None:
            raise StopAsyncIteration
        if isinstance(result, Exception):
            raise result
        return result

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._sync_cursor, name)
        if callable(attr):

            @wraps(attr)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                func = partial(attr, *args, **kwargs)
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, func)
                if hasattr(result, "__class__") and "Cursor" in str(result.__class__):
                    return AsyncCursorMock(result)
                return result

            return async_wrapper
        return attr
