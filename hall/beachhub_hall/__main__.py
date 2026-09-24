"""Kommandozeile: `beachhub-hall start` (Vorgabe) und `beachhub-hall master-pin`."""

import argparse
import asyncio
import logging
import re
import signal
from getpass import getpass

from argon2 import PasswordHasher

from beachhub_hall.config import KonfigFehler, Umgebung
from beachhub_hall.dienst import Dienst
from beachhub_hall.health import starte_health

# Nur ASCII-Ziffern wie am Tastenfeld (pin.ZIFFERN), aber mindestens 8 statt 4 Stellen: Der
# Master-PIN öffnet immer, auch ohne Buchung und ohne Plan – er muss deutlich schwerer zu erraten
# sein als eine Buchungs-PIN. Höchstens 12 Stellen, mehr nimmt das Tastenfeld nicht an.
MASTER_ZIFFERN = re.compile(r"[0-9]{8,12}")


def _master_pin() -> None:
    erste = getpass("Master-PIN (8–12 Ziffern): ")
    zweite = getpass("Master-PIN wiederholen: ")
    if erste != zweite or not MASTER_ZIFFERN.fullmatch(erste):
        raise SystemExit("Die PINs stimmen nicht überein oder sind nicht 8 bis 12 Ziffern lang.")
    print(PasswordHasher().hash(erste))  # noqa: T201 – Ausgabe für hall.toml


async def _laufen_bis_signal(dienst: Dienst, host: str, port: int) -> None:
    """Läuft, bis SIGTERM/SIGINT kommt oder `laufen()` selbst endet, und fährt danach immer
    geordnet herunter. Als PID 1 im Container ignoriert Python SIGTERM ohne eigenen Handler –
    `docker stop` würde dann nach der Kulanzfrist hart per SIGKILL beenden, ohne dass
    `Dienst.schliesse()` (u. a. `tuer.schliesse()`) noch laufen könnte; ein eingeschalteter
    switch.*-Türöffner bliebe dann im Zweifel offen."""
    runner = await starte_health(dienst, host, port)
    lauf = asyncio.create_task(dienst.laufen())
    schleife = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        schleife.add_signal_handler(sig, lauf.cancel)
    try:
        await lauf
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()
        await dienst.schliesse()


async def _start(umgebung: Umgebung) -> None:
    dienst = Dienst.aus_umgebung(umgebung)
    await _laufen_bis_signal(dienst, umgebung.health_host, umgebung.health_port)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="beachhub-hall")
    unter = parser.add_subparsers(dest="befehl")
    unter.add_parser("start", help="Hallendienst starten (Vorgabe)")
    unter.add_parser("master-pin", help="Hash für master_pin_hash in hall.toml erzeugen")
    args = parser.parse_args(argv)
    if args.befehl == "master-pin":
        _master_pin()
        return
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    umgebung = Umgebung()
    if not umgebung.core_public_key:
        raise SystemExit(
            "CORE_PUBLIC_KEY fehlt (öffentlicher Schlüssel von der System-Seite des Hauptsystems)."
        )
    try:
        asyncio.run(_start(umgebung))
    except KonfigFehler as e:
        raise SystemExit(str(e)) from e


if __name__ == "__main__":
    main()
