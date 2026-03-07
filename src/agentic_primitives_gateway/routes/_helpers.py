"""Shared route helpers to reduce boilerplate."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from fastapi import HTTPException


def handle_not_implemented(detail: str = "Not supported by this provider") -> Callable[..., Any]:
    """Decorator that converts NotImplementedError into HTTP 501.

    Usage::

        @router.get("/something")
        @handle_not_implemented("Feature X not supported by this provider")
        async def my_endpoint():
            return await registry.primitive.method()
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except NotImplementedError:
                raise HTTPException(status_code=501, detail=detail) from None

        return wrapper

    return decorator
