import threading
import time as time_mod
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.database import SessionLocal
from beachhub_core.models import AppSetting, Ereignis, HalleDienst, HallenStatusZeile
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


def test_plan_nach_wiederherstellung_wird_neu_veroeffentlicht(
    client: TestClient, db: Session, welt
) -> None:
    """Hauptsystem aus einem Backup wiederhergestellt: Die Halle kennt schon eine höhere
    Planversion als die gespeicherte. Statt ihr die alte Version zu schicken (die sie als
    version_alt verwürfe, mit Alarm-Mail), veröffentlicht das Hauptsystem den Plan neu – die
    neue Version ist max(alt + 1, Unixzeit in ms) und damit größer als `ab`. Schon der Status
    mit der höheren Version meldet plan_neu, damit die Halle sofort abruft."""
    alt = client.get("/hall/plan?ab=0", headers=H).json()["version"]
    # Die Halle hat nach dem Stand des Backups noch eine Version bekommen (etwas später
    # veröffentlicht, also Unixzeit in ms knapp über `alt`), die das Hauptsystem nicht mehr kennt.
    halle_kennt = alt + 1
    time_mod.sleep(0.01)  # die Uhr (ms) ist seitdem sicher weitergelaufen
    r = client.post("/hall/status", headers=H, json=_status(halle_kennt).model_dump(mode="json"))
    assert r.json() == {"plan_neu": True}
    r = client.get(f"/hall/plan?ab={halle_kennt}", headers=H)
    assert r.status_code == 200
    dok = Dokument.model_validate(r.json())
    assert dok.version > halle_kennt and lesestand.pruefe(dok)
    assert client.get(f"/hall/plan?ab={dok.version}", headers=H).status_code == 304


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


def test_ereignis_created_at_nutzt_echte_uhr_trotz_datums_override(
    client: TestClient, db: Session
) -> None:
    """`Ereignis.created_at`/`updated_at` müssen mit der echten Uhr gesetzt werden, nicht mit
    `clock.now(db)` (vom Admin für Tests/Abnahme überschreibbar) – sonst verfälscht ein
    Datums-Override, wann ein Ereignis tatsächlich empfangen wurde (Ruling zu Task 14)."""
    clock.set_override(db, date(2030, 1, 1))
    db.commit()
    try:
        jetzt = clock.now(db)
        assert jetzt.year == 2030  # Override wirkt wie erwartet
        r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1))
        assert r.status_code == 200
        ereignis = db.query(Ereignis).filter(Ereignis.halle_seq == 1).one()
        db.refresh(ereignis)
        abstand = abs((datetime.now(UTC) - ereignis.created_at).total_seconds())
        assert abstand < 60
        assert ereignis.updated_at == ereignis.created_at
        assert ereignis.created_at.year != 2030
    finally:
        clock.set_override(db, None)
        db.commit()


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


def test_marke_hinter_lieferung_nach_backup_wird_nachgezogen(
    client: TestClient, db: Session, welt
) -> None:
    """Hauptsystem aus einem Backup wiederhergestellt: Die Marke in halle_dienst (2) ist älter
    als der Stand der Halle, die seq 3–5 schon als bestätigt kennt und ab seq 6 liefert. Die
    Halle liefert immer ab ihrer niedrigsten unbestätigten seq – alles davor hat das
    Hauptsystem also früher schon bestätigt. Ohne Nachziehen der Marke fände die Lückenprüfung
    seq 3 nie und bestätigte für immer nur bis 2."""
    jetzt = clock.now(db)
    client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1, 2))
    r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 6, 7))
    assert r.json()["bestaetigt_bis"] == 7
    assert db.query(Ereignis).filter(Ereignis.halle_seq.in_([6, 7])).count() == 2
    dienst = db.get(HalleDienst, DIENST)
    db.refresh(dienst)
    assert dienst.bestaetigt_bis == 7


def test_bestaetigt_bis_bleibt_nach_loeschen_alter_ereignisse(
    client: TestClient, db: Session, welt
) -> None:
    jetzt = clock.now(db)
    client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1))
    # Simuliert das 90-Tage-Aufräumen (Spec § 10): die Ereignis-Zeile zu seq 1 verschwindet, die
    # Marke in halle_dienst bleibt trotzdem erhalten. Ein Scan über die verbliebenen Ereignisse
    # ab seq 1 fände jetzt nichts mehr und bliebe bei 0 hängen (fixiertes Verhalten).
    db.query(Ereignis).filter(Ereignis.halle_seq == 1).delete()
    db.commit()
    leer = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt))
    assert leer.json()["bestaetigt_bis"] == 1
    weiter = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 2))
    assert weiter.json()["bestaetigt_bis"] == 2


def test_parallele_lieferungen_gleicher_dienst_id(
    client: TestClient, db: Session, welt, mail_ausgang: list
) -> None:
    jetzt = clock.now(db)
    db.add(AppSetting(key=halle.KONTAKT_MARKER, value=(jetzt - timedelta(hours=1)).isoformat()))
    db.commit()
    ergebnisse: list[tuple[int, dict] | None] = [None, None]

    def rufe(i: int) -> None:
        r = client.post(
            "/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1, 2, status=_status(0))
        )
        ergebnisse[i] = (r.status_code, r.json())

    t1 = threading.Thread(target=rufe, args=(0,))
    t2 = threading.Thread(target=rufe, args=(1,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert all(r is not None and r[0] == 200 for r in ergebnisse)
    assert {r[1]["bestaetigt_bis"] for r in ergebnisse if r} == {2}
    assert db.query(Ereignis).count() == 2
    entwarnungen = [m for m in mail_ausgang if m["betreff"] == "[Beachhub] Halle wieder verbunden"]
    assert len(entwarnungen) == 1


def test_sperre_blockiert_zweite_lieferung_bis_zum_commit(welt) -> None:
    """Direkter Servicetest mit zwei echten Sessions statt über HTTP: zeigt, dass die
    halle_dienst-Sperre eine zweite, gleichzeitige Lieferung derselben Dienst-ID wirklich
    blockiert, bis die erste committet (Fix-Runde 1, Punkt 2) – nicht nur, dass am Ende ein
    plausibles Ergebnis herauskommt."""
    jetzt = datetime(2027, 11, 25, 12, 0, tzinfo=UTC)
    lieferung1 = EreignisLieferung(
        dienst_id=DIENST,
        ereignisse=[HallenEreignis(seq=1, typ="licht_geschaltet", zeitpunkt=jetzt)],
    )
    lieferung2 = EreignisLieferung(
        dienst_id=DIENST,
        ereignisse=[HallenEreignis(seq=2, typ="licht_geschaltet", zeitpunkt=jetzt)],
    )
    # Die halle_dienst-Zeile muss schon committet existieren, bevor die beiden Sessions
    # anfangen: Sonst würde bereits der Upsert-Insert (ON CONFLICT gegen eine „in doubt“-Zeile
    # der jeweils anderen, noch nicht committeten Transaktion) blockieren, und der Test würde
    # nicht die hier zu prüfende SELECT-FOR-UPDATE-Sperre treffen, sondern nur diesen Nebeneffekt.
    vorbereitung = SessionLocal()
    vorbereitung.add(HalleDienst(dienst_id=DIENST, bestaetigt_bis=0))
    vorbereitung.commit()
    vorbereitung.close()

    db1, db2 = SessionLocal(), SessionLocal()
    try:
        bis1, _ = halle.speichere_ereignisse(db1, lieferung1)  # nicht committet: hält die Sperre
        assert bis1 == 1

        ergebnis: list[int] = []

        def rufe2() -> None:
            bis2, _ = halle.speichere_ereignisse(db2, lieferung2)
            ergebnis.append(bis2)

        t = threading.Thread(target=rufe2)
        t.start()
        t.join(timeout=0.5)
        assert not ergebnis and t.is_alive()  # db2 wartet auf die Sperre von db1
        db1.commit()
        t.join(timeout=5)
        assert ergebnis == [2]
    finally:
        db1.close()
        db2.close()


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


def test_alarm_mail_nur_erwartete_felder(
    client: TestClient, db: Session, welt, mail_ausgang: list
) -> None:
    jetzt = clock.now(db)
    lieferung = EreignisLieferung(
        dienst_id=DIENST,
        ereignisse=[
            HallenEreignis(
                seq=1,
                typ="aktor_fehler",
                zeitpunkt=jetzt,
                feld_id="kein-uuid",  # landet als daten.feld_id_unbekannt
                daten={"grund": "x" * 500, "unerwartet": "geheim"},
            )
        ],
    )
    client.post("/hall/ereignisse", headers=H, json=lieferung.model_dump(mode="json"))
    text = mail_ausgang[-1]["text"]
    assert "unerwartet" not in text and "geheim" not in text
    assert "feld_id_unbekannt" not in text
    assert ("grund: " + "x" * 200 + "…") in text
    assert "x" * 201 not in text


def test_lieferung_ohne_status_plan_neu_false(client: TestClient, db: Session, welt) -> None:
    jetzt = clock.now(db)
    r = client.post("/hall/ereignisse", headers=H, json=_lieferung(jetzt, 1))
    assert r.status_code == 200 and r.json()["plan_neu"] is False


def test_post_ereignisse_braucht_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    valide = _lieferung(datetime(2027, 1, 1, tzinfo=UTC))
    assert client.post("/hall/ereignisse", json=valide).status_code == 401
    assert (
        client.post(
            "/hall/ereignisse", headers={"Authorization": "Bearer falsch"}, json=valide
        ).status_code
        == 401
    )
    monkeypatch.setattr(settings, "hall_token", "")
    assert client.post("/hall/ereignisse", headers=H, json=valide).status_code == 404


def test_post_status_braucht_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    valide = _status(0).model_dump(mode="json")
    assert client.post("/hall/status", json=valide).status_code == 401
    assert (
        client.post(
            "/hall/status", headers={"Authorization": "Bearer falsch"}, json=valide
        ).status_code
        == 401
    )
    monkeypatch.setattr(settings, "hall_token", "")
    assert client.post("/hall/status", headers=H, json=valide).status_code == 404


def test_ereignisse_ungueltige_nutzlast_422(client: TestClient) -> None:
    assert (
        client.post(
            "/hall/ereignisse", headers=H, json={"dienst_id": "not-a-uuid", "ereignisse": []}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/hall/ereignisse",
            headers=H,
            json={"dienst_id": str(DIENST), "ereignisse": "not-a-list"},
        ).status_code
        == 422
    )
    assert client.post("/hall/ereignisse", headers=H, content=b"not json").status_code == 422


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
