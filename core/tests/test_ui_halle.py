import uuid
from datetime import timedelta

from beachhub_core import clock
from beachhub_core.models import Ereignis, Feld, HallenStatusZeile, LesestandVersion
from beachhub_shared.hallenplan import FeldStatus, HallenStatus, HeizungStatus
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def test_halle_ohne_meldung(eingeloggt: TestClient) -> None:
    r = eingeloggt.get("/admin/halle")
    assert r.status_code == 200
    assert "Die Halle hat sich noch nie gemeldet" in r.text


def test_halle_mit_status_und_ereignissen(eingeloggt: TestClient, db: Session) -> None:
    jetzt = clock.now(db)
    f = Feld(name="Feld 1", reihenfolge=1)
    db.add(f)
    db.flush()
    status = HallenStatus(
        planversion=3,
        letzter_abruf=jetzt,
        ha_erreichbar=True,
        handbetrieb=True,
        felder=[FeldStatus(feld_id=str(f.id), licht_ist=True, praesenz=False)],
        heizung=HeizungStatus(soll="18.0", ist="12.5"),
        warteschlange=4,
        version_dienst="0.1.0",
    )
    db.add(
        HallenStatusZeile(
            id=1,
            daten_json=status.model_dump(mode="json"),
            empfangen_am=jetzt - timedelta(minutes=90),
        )
    )
    db.add(LesestandVersion(dokument="hallenplan", version=5, geaendert=False))
    dienst = uuid.uuid4()
    db.add(
        Ereignis(
            quelle="halle",
            typ="pin_akzeptiert",
            zeitpunkt=jetzt,
            feld_id=f.id,
            daten_json={},
            halle_dienst_id=dienst,
            halle_seq=1,
        )
    )
    db.add(
        Ereignis(
            quelle="halle",
            typ="aktor_fehler",
            zeitpunkt=jetzt,
            daten_json={"entity": "light.feld_1"},
            halle_dienst_id=dienst,
            halle_seq=2,
        )
    )
    db.commit()
    text = eingeloggt.get("/admin/halle").text
    assert "Die Halle hat noch nicht den aktuellen Plan" in text and "Version 3" in text
    assert "seit über 60 Minuten" in text
    assert "Handbetrieb" in text and "Feld 1" in text and "12,5" in text
    assert "pin_akzeptiert" in text and "light.feld_1" in text
    gefiltert = eingeloggt.get("/admin/halle?typ=aktor_fehler").text
    assert "light.feld_1" in gefiltert and "pin_akzeptiert</td>" not in gefiltert
