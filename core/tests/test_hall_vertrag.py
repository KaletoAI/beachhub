"""Ende-zu-Ende: der echte Hallendienst-Client gegen die echte Core-App, ohne Netz.

Prüft den ganzen Vertrag: Plan signiert abholen und prüfen, PIN mit den Plan-Parametern
finden, 304, Ereignis zurückliefern und im Hauptsystem wiederfinden.
"""

import asyncio
import uuid
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.main import app
from beachhub_core.models import Betriebszeit, Ereignis, Feld, FeldRaster, Kundengruppe, Tarif
from beachhub_core.services import buchungen, kunden, lesestand
from beachhub_shared.hallenplan import EreignisLieferung, pin_hash
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session

pytest.importorskip("beachhub_hall")

from beachhub_hall import plan as hall_plan  # noqa: E402
from beachhub_hall.clock import SimulierteUhr  # noqa: E402
from beachhub_hall.core import CoreClient  # noqa: E402
from beachhub_hall.db import oeffne  # noqa: E402
from beachhub_hall.ereignisse import Ereignisse  # noqa: E402

TOKEN = "hall-token-0123456789abcdef"


def test_halle_holt_plan_und_liefert_ereignisse(
    db: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "hall_token", TOKEN)
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    oeffentlich = lesestand.erzeuge_schluessel()
    f = Feld(name="Feld 1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    g = Kundengruppe(name="Privat")
    db.add_all([f, g, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=g.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        pin_klar="271828",
        beginn=kombiniere(date(2027, 11, 27), time(19)),
        ende=kombiniere(date(2027, 11, 27), time(20)),
    )
    db.commit()
    jetzt = clock.now(db)
    sitzungen = oeffne(tmp_path / "hall.sqlite")
    uhr = SimulierteUhr(jetzt)

    async def ablauf() -> int:
        core = CoreClient("http://core.test", TOKEN, transport=httpx.ASGITransport(app=app))
        try:
            roh = await core.hole_plan(0)
            assert roh is not None
            dok, inhalt = hall_plan.pruefe(roh, oeffentlich, 0)
            with sitzungen() as hdb:
                hall_plan.speichere(hdb, dok, inhalt, jetzt)
            with sitzungen() as hdb:
                treffer = hall_plan.buchungen_mit_pin(hdb, pin_hash("271828", inhalt.pin))
            assert [t.buchung_id for t in treffer] == [str(b.id)]
            assert await core.hole_plan(dok.version) is None
            ereignisse = Ereignisse(sitzungen, uhr)
            ereignisse.melde("pin_akzeptiert", feld_id=str(f.id), buchung_id=str(b.id))
            antwort = await core.sende_ereignisse(
                EreignisLieferung(dienst_id=uuid.uuid4(), ereignisse=ereignisse.unbestaetigt())
            )
            return antwort.bestaetigt_bis
        finally:
            await core.schliesse()

    assert asyncio.run(ablauf()) == 1
    e = db.query(Ereignis).one()
    assert e.typ == "pin_akzeptiert" and e.buchung_id == b.id and e.feld_id == f.id
