"""Kommandozeile: `beachhub-hall start` (Vorgabe) und `beachhub-hall master-pin`."""

import argparse
import asyncio
import logging
import re
from getpass import getpass

from argon2 import PasswordHasher

from beachhub_hall.config import Umgebung
from beachhub_hall.dienst import Dienst
from beachhub_hall.health import starte_health


def _master_pin() -> None:
    erste = getpass("Master-PIN (4–12 Ziffern): ")
    zweite = getpass("Master-PIN wiederholen: ")
    if erste != zweite or not re.fullmatch(r"\d{4,12}", erste):
        raise SystemExit("Die PINs stimmen nicht überein oder sind nicht 4 bis 12 Ziffern lang.")
    print(PasswordHasher().hash(erste))  # noqa: T201 – Ausgabe für hall.toml


async def _start(umgebung: Umgebung) -> None:
    dienst = Dienst.aus_umgebung(umgebung)
    runner = await starte_health(dienst, umgebung.health_port)
    try:
        await dienst.laufen()
    finally:
        await runner.cleanup()
        await dienst.schliesse()


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
    asyncio.run(_start(umgebung))


if __name__ == "__main__":
    main()
