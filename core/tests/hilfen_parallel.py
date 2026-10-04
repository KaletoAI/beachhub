"""Hilfen für echte, über beobachtete PostgreSQL-Sperren synchronisierte Tests."""

from time import monotonic, sleep

from beachhub_core.database import engine
from sqlalchemy import text


def warte_auf_sperre(pid: int) -> None:
    deadline = monotonic() + 5
    with engine.connect() as conn:
        while not conn.scalar(
            text(
                "select exists(select 1 from pg_stat_activity "
                "where pid=:pid and wait_event_type='Lock')"
            ),
            {"pid": pid},
        ):
            assert monotonic() < deadline, "Backend wartet nicht auf die erwartete Sperre"
            sleep(0.01)
