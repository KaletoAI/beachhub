"""Ende-zu-Ende: der echte Hallendienst-Client gegen die echte Core-App, ohne Netz.

Prüft den ganzen Vertrag: Plan signiert abholen und prüfen, PIN mit den Plan-Parametern
finden, 304, Ereignis zurückliefern und im Hauptsystem wiederfinden, sowie plan_neu über den
Status-Teil der Lieferung (unverändert → False, nach einer neuen Buchung → True).
"""

import asyncio
import uuid
from datetime import date, time
from pathlib import Path

import httpx
import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.main import app
from beachhub_core.models import Ereignis
from beachhub_core.services import buchungen, lesestand
from beachhub_shared.hallenplan import EreignisLieferung, HallenStatus, pin_hash
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
    db: Session, welt, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "hall_token", TOKEN)
    f, k = welt
    oeffentlich = lesestand.oeffentlicher_schluessel()
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
    dienst_id = uuid.uuid4()

    async def ablauf() -> tuple[int, bool, bool]:
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
            status = HallenStatus(
                planversion=dok.version,
                letzter_abruf=None,
                ha_erreichbar=True,
                handbetrieb=False,
                version_dienst="test",
            )
            erste = await core.sende_ereignisse(
                EreignisLieferung(
                    dienst_id=dienst_id, ereignisse=ereignisse.unbestaetigt(), status=status
                )
            )

            # Eine neue Buchung markiert den Hallenplan als geändert (lesestand.markiere_geaendert
            # hängt hallenplan.DOKUMENT automatisch an "belegung" an) – derselbe planversion-Wert
            # muss jetzt plan_neu=True auslösen, auch ohne neuen Planabruf der Halle.
            buchungen.lege_an(
                db,
                feld_id=f.id,
                kunde_id=k.id,
                pin_klar="481516",
                beginn=kombiniere(date(2027, 11, 28), time(19)),
                ende=kombiniere(date(2027, 11, 28), time(20)),
            )
            db.commit()
            zweite = await core.sende_ereignisse(
                EreignisLieferung(dienst_id=dienst_id, ereignisse=[], status=status)
            )
            return erste.bestaetigt_bis, erste.plan_neu, zweite.plan_neu
        finally:
            await core.schliesse()

    bestaetigt_bis, plan_neu_unveraendert, plan_neu_nach_neuer_buchung = asyncio.run(ablauf())
    assert bestaetigt_bis == 1
    assert plan_neu_unveraendert is False
    assert plan_neu_nach_neuer_buchung is True

    e = db.query(Ereignis).one()
    assert e.typ == "pin_akzeptiert" and e.buchung_id == b.id and e.feld_id == f.id
