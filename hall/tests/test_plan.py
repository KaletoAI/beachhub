from typing import Any

import pytest
from beachhub_hall import plan
from beachhub_shared.hallenplan import PlanFeld, pin_hash
from beachhub_shared.signatur import erzeuge_schluesselpaar
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.hilfen import F1, PIN_PARAMETER, baue_plan, buchung, signiertes_dokument, t

PRIV, PUB = erzeuge_schluesselpaar()


def _roh(version: int = 1, dokument: str = "hallenplan") -> dict[str, Any]:
    inhalt = baue_plan([buchung(F1, t(19), t(21), pin="482913", buchung_id="b-1")])
    return signiertes_dokument(inhalt, version, PRIV, dokument).model_dump(mode="json")


def test_gueltiger_plan_wird_gespeichert_und_geladen(sitzungen: sessionmaker[Session]) -> None:
    dok, inhalt = plan.pruefe(_roh(), PUB, 0)
    with sitzungen() as db:
        assert plan.version(db) == 0 and plan.lade(db) is None
        plan.speichere(db, dok, inhalt, t(17))
    with sitzungen() as db:
        geladen = plan.lade(db)
        assert geladen is not None
        assert geladen.version == 1 and geladen.inhalt == inhalt and geladen.empfangen_am == t(17)
        assert plan.version(db) == 1
        treffer = plan.buchungen_mit_pin(db, pin_hash("482913", PIN_PARAMETER))
        assert [b.buchung_id for b in treffer] == ["b-1"]
        assert treffer[0].beginn == t(19)
        assert plan.buchungen_mit_pin(db, pin_hash("000000", PIN_PARAMETER)) == []


def test_falscher_schluessel_und_manipulation() -> None:
    _, fremd_pub = erzeuge_schluesselpaar()
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe(_roh(), fremd_pub, 0)
    assert e.value.grund == "signatur"
    roh = _roh()
    roh["inhalt"]["buchungen"][0]["ende"] = t(23).isoformat()
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe(roh, PUB, 0)
    assert e.value.grund == "signatur"


def test_alte_oder_gleiche_version_wird_verworfen() -> None:
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe(_roh(version=3), PUB, 3)
    assert e.value.grund == "version_alt"
    with pytest.raises(plan.PlanFehler):
        plan.pruefe(_roh(version=2), PUB, 3)


def test_falscher_dokumentname_und_kaputtes_schema() -> None:
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe(_roh(dokument="belegung"), PUB, 0)
    assert e.value.grund == "schema"
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe({"dokument": "hallenplan"}, PUB, 0)
    assert e.value.grund == "schema"
    # korrekt signiert, aber der Inhalt ist kein Hallenplan
    from beachhub_shared.lesestand import Dokument
    from beachhub_shared.signatur import signiere

    entwurf = Dokument(
        dokument="hallenplan", version=1, erzeugt_am=t(0), inhalt={"x": 1}, signatur=""
    )
    sig = signiere(entwurf.model_dump(mode="json", exclude={"signatur"}), PRIV)
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe(entwurf.model_copy(update={"signatur": sig}).model_dump(mode="json"), PUB, 0)
    assert e.value.grund == "schema"


def test_naive_zeit_im_inhalt_wird_als_schema_verworfen() -> None:
    # Signatur und Dokumentname sind gültig, aber eine Zeit im Inhalt hat keine Zeitzone
    # (Ruling Task 3: HallenplanInhalt verlangt AwareDatetime). Das muss als "schema"
    # verworfen werden, nicht als ValidationError durchschlagen.
    from beachhub_shared.lesestand import Dokument
    from beachhub_shared.signatur import signiere

    inhalt = baue_plan([buchung(F1, t(19), t(21), pin="482913", buchung_id="b-1")]).model_dump(
        mode="json"
    )
    inhalt["gueltig_ab"] = "2027-12-01T00:00:00"  # ohne Zeitzone
    entwurf = Dokument(
        dokument="hallenplan", version=1, erzeugt_am=t(0), inhalt=inhalt, signatur=""
    )
    sig = signiere(entwurf.model_dump(mode="json", exclude={"signatur"}), PRIV)
    with pytest.raises(plan.PlanFehler) as e:
        plan.pruefe(entwurf.model_copy(update={"signatur": sig}).model_dump(mode="json"), PUB, 0)
    assert e.value.grund == "schema"


def test_ersetzen_ist_atomar(sitzungen: sessionmaker[Session]) -> None:
    dok, inhalt = plan.pruefe(_roh(version=1), PUB, 0)
    with sitzungen() as db:
        plan.speichere(db, dok, inhalt, t(17))
    kaputt = inhalt.model_copy(
        update={
            "felder": [PlanFeld(id=F1, name="A", aktiv=True), PlanFeld(id=F1, name="B", aktiv=True)]
        }
    )
    dok2 = signiertes_dokument(kaputt, 2, PRIV)
    with sitzungen() as db, pytest.raises(IntegrityError):
        plan.speichere(db, dok2, kaputt, t(18))
    with sitzungen() as db:
        geladen = plan.lade(db)
        assert geladen is not None and geladen.version == 1
        assert len(plan.buchungen_mit_pin(db, pin_hash("482913", PIN_PARAMETER))) == 1
