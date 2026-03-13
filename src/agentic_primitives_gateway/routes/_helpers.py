"""Shared route helpers to reduce boilerplate."""

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from fastapi import HTTPException

from agentic_primitives_gateway.auth.models import AuthenticatedPrincipal
from agentic_primitives_gateway.context import get_authenticated_principal


def require_principal() -> AuthenticatedPrincipal:
    """Return the authenticated principal. Raises if not set."""
    principal = get_authenticated_principal()
    if principal is None:
        raise RuntimeError("No authenticated principal — auth middleware did not run")
    return principal


def handle_provider_errors(
    not_implemented: str = "Not supported by this provider",
    not_found: str | None = None,
) -> Callable[..., Any]:
    """Decorator that maps common provider exceptions to HTTP errors.

    - ``NotImplementedError`` → 501 with *not_implemented* message
    - ``KeyError`` → 404 with *not_found* message (only if *not_found* is set)

    Usage::

        @router.get("/something")
        @handle_provider_errors("Feature X not supported", not_found="Item not found")
        async def my_endpoint():
            return await registry.primitive.method()
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except NotImplementedError:
                raise HTTPException(status_code=501, detail=not_implemented) from None
            except KeyError:
                if not_found is not None:
                    raise HTTPException(status_code=404, detail=not_found) from None
                raise

        return wrapper

    return decorator
