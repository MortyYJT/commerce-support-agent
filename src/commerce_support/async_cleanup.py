"""Bounded cleanup helpers for tasks cancelled by client disconnects."""

import asyncio
from collections.abc import Awaitable

import anyio

_CLEANUP_TIMEOUT_SECONDS = 10


async def run_shielded_cleanup(operation: Awaitable[object]) -> None:
    """Let a cleanup operation finish despite cancellation, with a fixed bound."""
    with anyio.move_on_after(_CLEANUP_TIMEOUT_SECONDS, shield=True):
        await operation


async def close_async_iterator(iterator: object) -> None:
    close = getattr(iterator, "aclose", None)
    if close is None:
        return
    try:
        await run_shielded_cleanup(close())
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - do not leak iterator-close failures.
        return
