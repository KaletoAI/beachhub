"""Die dauerhaft laufenden Aufgaben des Hallendienstes.

Jede Aufgabe hat eine Methode `einmal()` für genau einen Durchlauf; `takt` macht daraus die
Dauerschleife. Tests rufen `einmal()` direkt mit simulierter Uhr auf.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


async def takt(
    name: str,
    einmal: Callable[[], Awaitable[object]],
    intervall: float,
    wecker: asyncio.Event | None = None,
) -> None:
    while True:
        try:
            await einmal()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Aufgabe %s fehlgeschlagen", name)
        if wecker is None:
            await asyncio.sleep(intervall)
            continue
        try:
            await asyncio.wait_for(wecker.wait(), timeout=intervall)
        except TimeoutError:
            pass
        wecker.clear()
