import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import AppSetting, Ereignis, HallenStatusZeile
from beachhub_core.services import buchungen, halle, lesestand
from beachhub_shared.hallenplan import EreignisLieferung, HallenEreignis, HallenStatus
from beachhub_shared.lesestand import Dokument
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

TOKEN = "hall-token-0123456789abcdef"
H = {"Authorization": f"Bearer {TOKEN}"}
DIENST = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


@pytest.fixture(autouse=True)
def token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "hall_token", TOKEN)


def _status(version: int) -> HallenStatus:
    return HallenStatus(
        planversion=version,
        letzter_abruf=None,
        ha_erreichbar=True,
        handbetrieb=False,
        version_dienst="0.1.0",
    )


def _lieferung(
    jetzt: datetime,
    *seqs: int,
    typ: str = "licht_geschaltet",
    status: HallenStatus | None = None,
    dienst: uuid.UUID = DIENST,
) -> dict:
    return EreignisLieferung(
        dienst_id=dienst,
        ereignisse=[HallenEreignis(seq=s, typ=typ, zeitpunkt=jetzt) for s in seqs],
        status=status,
    ).model_dump(mode="json")


def test_token_pflicht(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.get("/hall/plan").status_code == 401
    assert client.get("/hall/plan", headers={"Authorization": "Bearer falsch"}).status_code == 401
    monkeypatch.setattr(settings, "hall_token", "")
    assert client.get("/hall/plan", headers=H).status_code == 404


def test_plan_ist_signiert_und_liefert_304(client: TestClient, db: Session, welt) -> None:
    f, k = welt
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(date(2027, 11, 27), time(19)),
        ende=kombiniere(date(2027, 11, 27), time(20)),
    )
    db.commit()
    r = client.get("/hall/plan?ab=0", headers=H)
    assert r.status_code == 200
    dok = Dokument.model_validate(r.json())
    assert dok.dokument == "hallenplan" and lesestand.pruefe(dok)
    assert len(dok.inhalt["buchungen"]) == 1
    assert client.get(f"/hall/plan?ab={dok.version}", headers=H).status_code == 304
    buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(date(2027, 11, 28), time(19)),
        ende=kombiniere(date(2027, 11, 28), time(20)),
    )
    db.commit()
    r2 = client.get(f"/hall/plan?ab={dok.version}", headers=H)
    # Version ist max(bisherige+1, Unixzeit in ms) (Ruling Lesestand-Versionen), also nicht
    # zwingend genau +1 – wie test_lesestand.py::test_publiziere_zweimal_erhoeht_version.
    assert r2.status_code == 200 and r2.json()["version"] > dok.version


def test_plan_ohne_schluessel_503(client: TestClient, welt) -> None:
    Path(settings.signatur_privatschluessel_pfad).unlink()
    assert client.get("/hall/plan", headers=H).status_code == 503


def test_ereignisse_idempotent_je_dienst(client: TestClient, db: Session, welt) -> None:
    jetzt = clock.now(db)
    r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1, 2))
    assert r.status_code == 200 and r.json()["bestaetigt_bis"] == 2
    r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1, 2, 3))
    assert r.json()["bestaetigt_bis"] == 3
    assert db.query(Ereignis).count() == 3
    anderer = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1, dienst=anderer))
    assert r.json()["bestaetigt_bis"] == 1
    assert db.query(Ereignis).count() == 4
    leer = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt))
    assert leer.json()["bestaetigt_bis"] == 3


def test_ereignisse_luecke_haelt_bestaetigt_bis_zurueck(
    client: TestClient, db: Session, welt
) -> None:
    jetzt = clock.now(db)
    # seq 3 fehlt (z. B. eine Lieferung, die das Hauptsystem nie erreicht hat): bestaetigt_bis
    # darf nicht über die Lücke hinaus bestätigen, sonst holt die Halle seq 3 nie nach.
    r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1, 2, 4))
    assert r.json()["bestaetigt_bis"] == 2
    assert db.query(Ereignis).count() == 3
    r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 3))
    assert r.json()["bestaetigt_bis"] == 4
    assert db.query(Ereignis).count() == 4


def test_ereignis_mit_unbekannter_feld_id_wird_trotzdem_gespeichert(
    client: TestClient, db: Session, welt
) -> None:
    lieferung = EreignisLieferung(
        dienst_id=DIENST,
        ereignisse=[
            HallenEreignis(
                seq=1,
                typ="aktor_fehler",
                zeitpunkt=clock.now(db),
                feld_id="kein-uuid",
                daten={"grund": "x"},
            )
        ],
    )
    assert (
        client.post(
            "/hall/ereignisse", headers=H, json=lieferung.model_dump(mode="json")
        ).status_code
        == 200
    )
    e = db.query(Ereignis).one()
    assert e.feld_id is None and e.daten_json == {"grund": "x", "feld_id_unbekannt": "kein-uuid"}


def test_alarm_mails_frisch_einzeln_nachgeliefert_gesammelt(
    client: TestClient, db: Session, welt, mail_ausgang: list
) -> None:
    jetzt = clock.now(db)
    client.post(
        "/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1, typ="tastenfeld_fehlversuche")
    )
    assert [m["betreff"] for m in mail_ausgang] == ["[Beachhub] Tastenfeld: mehrere falsche Codes"]
    alt = jetzt - timedelta(hours=7)
    client.post("/hall/ereignisse", headers=H, json=_lieferung(alt, 2, 3, typ="aktor_fehler"))
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] 2 nachgelieferte Meldungen der Halle"
    client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 4, typ="licht_geschaltet"))
    assert len(mail_ausgang) == 2


def test_plan_neu_und_status(client: TestClient, db: Session, welt) -> None:
    jetzt = clock.now(db)
    r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, status=_status(0)))
    assert r.json()["plan_neu"] is True
    version = client.get("/hall/plan", headers=H).json()["version"]
    r = client.post("/hall/status", headers=H, json=_status(version).model_dump(mode="json"))
    assert r.status_code == 200 and r.json() == {"plan_neu": False}
    zeile = db.get(HallenStatusZeile, 1)
    db.refresh(zeile)
    assert zeile.daten_json["planversion"] == version


def test_entwarnung_nach_kontakt_alarm(
    client: TestClient, db: Session, welt, mail_ausgang: list
) -> None:
    jetzt = clock.now(db)
    db.add(AppSetting(key=halle.KONTAKT_MARKER, value=(jetzt - timedelta(hours=3)).isoformat()))
    db.commit()
    client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, status=_status(0)))
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] Halle wieder verbunden"
    assert "3 h 0 min" in mail_ausgang[-1]["text"]
    db.expire_all()
    assert db.get(AppSetting, halle.KONTAKT_MARKER) is None


def test_hallenplan_geht_nicht_ans_portal() -> None:
    from beachhub_shared.kanal import fuer_portal

    assert fuer_portal("hallenplan") is False
    assert fuer_portal("belegung") is True
