import base64
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import pytest
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.database import SessionLocal
from beachhub_core.models import (
    AnfrageVerarbeitet,
    Audit,
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    Kunde,
    Kundengruppe,
    LesestandVersion,
    Tarif,
    utcnow,
)
from beachhub_core.services import (
    anfragen,
    buchungen,
    konfiguration,
    kunden,
    lesestand,
    rechnung_pdf,
    rechnungen,
)
from beachhub_shared import kanal
from beachhub_shared.zeit import kombiniere
from sqlalchemy import select
from sqlalchemy.orm import Session

D = date(2027, 12, 1)


def anfrage(typ: str, konto_id: uuid.UUID | None = None, **nutzlast) -> kanal.Anfrage:
    return kanal.Anfrage(
        anfrage_id=uuid.uuid4(),
        typ=typ,
        konto_id=konto_id,
        nutzlast=nutzlast,
        erstellt_am=datetime.now(UTC),
    )


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p, v = Kundengruppe(name="Privat"), Kundengruppe(name="Verein")
    db.add_all([f, p, v, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, p, v


def _angelegt(db: Session, konto_id: uuid.UUID, email: str = "anna@x.de") -> Kunde:
    antwort, _ = anfragen.bearbeite(
        db, anfrage("konto_angelegt", konto_id, email=email, anzeigename="Anna")
    )
    assert antwort.status == "ok"
    return db.get(Kunde, antwort.kunde_id)


def test_konto_angelegt_legt_kunden_an(db: Session, welt) -> None:
    _, p, _ = welt
    konto = uuid.uuid4()
    k = _angelegt(db, konto, email="  Anna@X.de ")
    assert k.email == "anna@x.de" and k.name == "Anna"
    assert k.portal_konto_id == konto and k.zahlungsart == "online"
    assert k.kundengruppe_id == p.id  # erste Gruppe nach Name: "Privat" < "Verein"
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert


def test_konto_angelegt_nutzt_konfigurierte_gruppe(db: Session, welt) -> None:
    _, _, v = welt
    konfiguration.setze(db, "portal_kundengruppe", "Verein")
    db.commit()
    assert _angelegt(db, uuid.uuid4()).kundengruppe_id == v.id
    konfiguration.setze(db, "portal_kundengruppe", "Gibtsnicht")
    db.commit()
    assert _angelegt(db, uuid.uuid4(), "b@x.de").kundengruppe.name == "Privat"


def test_konto_angelegt_verknuepft_bestehenden_kunden(db: Session, welt) -> None:
    _, _, v = welt
    alt = kunden.lege_an(db, name="Anna Abo", email="anna@x.de", kundengruppe_id=v.id)
    db.commit()
    k = _angelegt(db, uuid.uuid4())
    assert k.id == alt.id and k.name == "Anna Abo"
    neu = uuid.uuid4()
    assert _angelegt(db, neu).id == alt.id  # Portal wiederhergestellt: neue Konto-ID
    assert db.get(Kunde, alt.id).portal_konto_id == neu


def test_konto_angelegt_ignoriert_anonymisierten_kunden_mit_gleicher_email(
    db: Session, welt
) -> None:
    # kunden.anonymisiere überschreibt die E-Mail zwar immer mit einer Ersatzadresse – der
    # Ausschluss in anfragen._konto_angelegt bleibt trotzdem bestehen, falls sich das je ändert:
    # Ein gelöschtes Konto darf nie über eine (zufällig) passende E-Mail mit einem neuen
    # Portal-Konto verknüpft werden. Ohne den Ausschluss würde die folgende Anfrage
    # `alt.portal_konto_id` setzen und einen Audit-Eintrag mit quelle="portal" auf den bereits
    # gelöschten Kunden schreiben.
    _, p, _ = welt
    alt = kunden.lege_an(db, name="Anna Alt", email="anna@x.de", kundengruppe_id=p.id)
    alt.anonymisiert_am = utcnow()
    db.commit()
    antwort, _ = anfragen.bearbeite(
        db, anfrage("konto_angelegt", uuid.uuid4(), email="anna@x.de", anzeigename="Anna")
    )
    # Die E-Mail bleibt in diesem (künstlich herbeigeführten) Zustand auf `alt` belegt, daher
    # scheitert die Neuanlage über kunden.lege_an – entscheidend ist, dass `alt` dabei
    # unangetastet bleibt, statt reaktiviert zu werden.
    assert antwort.status == "fehler"
    db.expire_all()
    alt_danach = db.get(Kunde, alt.id)
    assert alt_danach.portal_konto_id is None
    assert alt_danach.anonymisiert_am is not None
    assert not db.scalars(
        select(Audit).where(Audit.objekt_id == alt.id, Audit.quelle == "portal")
    ).all()


def test_konto_angelegt_zweimal_gleicher_kunde(db: Session, welt) -> None:
    konto = uuid.uuid4()
    assert _angelegt(db, konto).id == _angelegt(db, konto).id
    assert len(db.scalars(select(Kunde)).all()) == 1


def test_ohne_kundengruppe(db: Session) -> None:
    antwort, _ = anfragen.bearbeite(
        db, anfrage("konto_angelegt", uuid.uuid4(), email="a@x.de", anzeigename="A")
    )
    assert antwort.status == "abgelehnt" and antwort.grund == "keine_kundengruppe"


def test_unbekannt_ungueltig_und_konto_unbekannt(db: Session, welt) -> None:
    assert anfragen.verarbeite(db, anfrage("gibtsnicht")).antwort.grund == "unbekannter_typ"
    kaputt = anfrage("buchung_anfragen", uuid.uuid4(), feld_id="x")
    assert anfragen.verarbeite(db, kaputt).antwort.grund == "ungueltig"
    fremd = anfrage("buchung_stornieren", uuid.uuid4(), buchung_id=str(uuid.uuid4()))
    assert anfragen.verarbeite(db, fremd).antwort.grund == "konto_unbekannt"


def test_typ_ueberlang_ohne_dataerror(db: Session, welt) -> None:
    # anfrage_verarbeitet.typ ist String(40); ein überlanger unbekannter Typ darf nicht zu
    # einem DataError beim Speichern führen.
    lang = "x" * 60
    a = anfrage(lang, uuid.uuid4())
    antwort, nachlauf = anfragen.bearbeite(db, a)
    assert antwort.status == "abgelehnt" and antwort.grund == "unbekannter_typ"
    assert nachlauf == []
    gespeichert = db.get(AnfrageVerarbeitet, a.anfrage_id)
    assert gespeichert is not None
    assert len(gespeichert.typ) <= 40


def test_zahlung_eingegangen_braucht_kein_konto(db: Session, welt) -> None:
    a = anfrage("zahlung_eingegangen", None, provider="fake", rohdaten="{}")
    assert anfragen.verarbeite(db, a).antwort.grund == "nicht_verifiziert"


def test_konto_geaendert_nur_wenn_name_unveraendert(db: Session, welt) -> None:
    konto = uuid.uuid4()
    k = _angelegt(db, konto)
    anfragen.bearbeite(db, anfrage("konto_geaendert", konto, anzeigename="Anni", bisher="Anna"))
    assert db.get(Kunde, k.id).name == "Anni"
    k.name = "Anna Beispiel"  # vom Betreiber gepflegt
    db.commit()
    anfragen.bearbeite(db, anfrage("konto_geaendert", konto, anzeigename="A.", bisher="Anni"))
    assert db.get(Kunde, k.id).name == "Anna Beispiel"


def test_konto_loeschen_anonymisiert(db: Session, welt) -> None:
    konto = uuid.uuid4()
    k = _angelegt(db, konto)
    antwort, _ = anfragen.bearbeite(db, anfrage("konto_loeschen", konto))
    assert antwort.status == "ok"
    k = db.get(Kunde, k.id)
    assert k.anonymisiert_am is not None and k.portal_konto_id is None
    assert k.name == "Gelöschter Kunde"
    audits = db.scalars(select(Audit).where(Audit.objekt_id == k.id)).all()
    loesch_audit = next(
        a for a in audits if a.nachher_json and a.nachher_json.get("name") == k.name
    )
    assert loesch_audit.quelle == "portal"


def test_konto_zweimal_anlegen_und_loeschen_mit_gleicher_email(db: Session, welt) -> None:
    # Die Ersatz-E-Mail hing an der alten Adresse: Das zweite Löschen derselben Adresse
    # kollidierte am Unique-Constraint, der Kunde blieb mit Daten und Verknüpfung erhalten.
    for _ in range(2):
        konto = uuid.uuid4()
        k = _angelegt(db, konto)
        antwort, _ = anfragen.bearbeite(db, anfrage("konto_loeschen", konto))
        assert antwort.status == "ok"
        db.expire_all()
        k = db.get(Kunde, k.id)
        assert k.anonymisiert_am is not None and k.portal_konto_id is None
    alle = db.scalars(select(Kunde)).all()
    assert len(alle) == 2
    assert all(k.email == f"geloescht-{k.id.hex}" for k in alle)


def test_konto_loeschen_markiert_lesestand_nicht_neu(db: Session, welt) -> None:
    # Das Portal hat das Konto-Dokument bereits gelöscht; es darf nicht wieder erscheinen.
    konto = uuid.uuid4()
    k = _angelegt(db, konto)
    zeile = db.get(LesestandVersion, f"konto:{k.id}")
    zeile.geaendert = False  # Stand wurde bereits ans Portal geschickt
    db.commit()
    anfragen.bearbeite(db, anfrage("konto_loeschen", konto))
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert is False


def test_buchung_anfragen_mit_rueckkehradresse(
    db: Session, welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    f, _, _ = welt
    konto = uuid.uuid4()
    _angelegt(db, konto)
    # portal_url ist nur die mTLS-Kanaladresse (:8443); die Rückkehradresse für den
    # Kunden-Browser stammt aus portal_oeffentliche_url.
    monkeypatch.setattr(settings, "portal_url", "https://portal-kanal.example:8443")
    monkeypatch.setattr(settings, "portal_oeffentliche_url", "https://portal.example")
    a = anfrage(
        "buchung_anfragen",
        konto,
        feld_id=str(f.id),
        beginn=kombiniere(D, time(19)).isoformat(),
        ende=kombiniere(D, time(20)).isoformat(),
    )
    antwort, _ = anfragen.bearbeite(db, a)
    assert antwort.status == "reserviert"
    assert (
        f"zurueck=https%3A%2F%2Fportal.example%2Fzahlung%2Fzurueck%3Fanfrage%3D{a.anfrage_id}"
        in antwort.checkout_url
    )
    assert db.get(Buchung, antwort.buchung_id).anfrage_id == a.anfrage_id


def test_rechnung_nur_eigene(db: Session, welt) -> None:
    f, p, _ = welt
    konto = uuid.uuid4()
    k = _angelegt(db, konto)
    fremd = kunden.lege_an(db, name="B", email="b@x.de", kundengruppe_id=p.id)
    eigene = []
    for kunde, stunde in ((k, 19), (fremd, 20)):
        b = buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=kunde.id,
            beginn=kombiniere(D, time(stunde)),
            ende=kombiniere(D, time(stunde + 1)),
            zahlungsart="online",
        )
        eigene.append(rechnungen.erzeuge_einzelrechnung(db, b))
    db.commit()
    for r in eigene:
        rechnung_pdf.erzeuge(db, r)
    db.commit()
    mein, sein = eigene
    antwort, _ = anfragen.bearbeite(
        db, anfrage("rechnung_anfordern", konto, rechnung_nr=mein.nummer)
    )
    assert antwort.status == "ok" and antwort.dateiname == f"Rechnung-{mein.nummer}.pdf"
    assert base64.b64decode(antwort.pdf_base64) == Path(mein.pdf_pfad).read_bytes()
    antwort, _ = anfragen.bearbeite(
        db, anfrage("rechnung_anfordern", konto, rechnung_nr=sein.nummer)
    )
    assert antwort.grund == "nicht_gefunden"


def test_rechnung_ohne_intaktes_pdf(db: Session, welt, mail_ausgang) -> None:
    f, _, _ = welt
    konto = uuid.uuid4()
    k = _angelegt(db, konto)
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    antwort, nachlauf = anfragen.bearbeite(
        db, anfrage("rechnung_anfordern", konto, rechnung_nr=r.nummer)
    )
    assert antwort.grund == "nicht_gefunden" and len(nachlauf) == 1  # noch kein PDF
    nachlauf[0](db)
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] Rechnungs-PDF fehlt"
    rechnung_pdf.erzeuge(db, r)
    db.commit()
    Path(r.pdf_pfad).write_bytes(b"manipuliert")
    antwort, nachlauf = anfragen.bearbeite(
        db, anfrage("rechnung_anfordern", konto, rechnung_nr=r.nummer)
    )
    assert antwort.grund == "nicht_gefunden" and len(nachlauf) == 1
    nachlauf[0](db)
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] Rechnungs-PDF beschädigt"


def test_bearbeite_ist_idempotent(db: Session, welt) -> None:
    a = anfrage("konto_angelegt", uuid.uuid4(), email="a@x.de", anzeigename="A")
    erste, _ = anfragen.bearbeite(db, a)
    zweite, nachlauf = anfragen.bearbeite(db, a)
    assert erste == zweite and nachlauf == []
    assert db.get(AnfrageVerarbeitet, a.anfrage_id).typ == "konto_angelegt"
    assert len(db.scalars(select(Kunde)).all()) == 1


def test_ausnahme_wird_fehler_mit_alarm(
    db: Session, welt, monkeypatch: pytest.MonkeyPatch, mail_ausgang
) -> None:
    def kaputt(*args, **kwargs):
        raise RuntimeError("kaputt")

    # Wirft erst NACHDEM kunden.lege_an den neuen Kunden bereits geflusht hat (letzter Schritt
    # von _konto_angelegt) – der Rollback muss diesen Teil-Schreibvorgang zurücknehmen, nicht
    # nur bestätigen, dass "nichts geschah".
    monkeypatch.setattr(lesestand, "markiere_geaendert", kaputt)
    a = anfrage("konto_angelegt", uuid.uuid4(), email="a@x.de", anzeigename="A")
    antwort, nachlauf = anfragen.bearbeite(db, a)
    assert antwort.status == "fehler"
    assert db.get(AnfrageVerarbeitet, a.anfrage_id).antwort_json == {"status": "fehler"}
    for schritt in nachlauf:
        schritt(db)
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] Portal-Anfrage fehlgeschlagen"
    assert db.scalars(select(Kunde)).all() == []


def test_gleichzeitige_zustellung_liefert_die_andere_antwort(
    db: Session, welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Zwei Zustellungen derselben Anfrage laufen in unabhängigen Sessions gleichzeitig ab. Die
    # andere gewinnt den Wettlauf um den Commit von AnfrageVerarbeitet; unser eigener Commit
    # kollidiert danach am Primärschlüssel (anfrage_id) und muss die schon gespeicherte Antwort
    # der anderen Session übernehmen, statt selbst noch einmal etwas anzulegen.
    a = anfrage("konto_angelegt", uuid.uuid4(), email="a@x.de", anzeigename="A")
    andere_antwort = {"status": "ok", "kunde_id": str(uuid.uuid4())}
    original = anfragen.verarbeite

    def wettlauf(db_: Session, anfrage_: kanal.Anfrage):
        erg = original(db_, anfrage_)
        with SessionLocal() as andere:
            andere.add(
                AnfrageVerarbeitet(
                    anfrage_id=anfrage_.anfrage_id,
                    typ=anfrage_.typ,
                    antwort_json=andere_antwort,
                )
            )
            andere.commit()
        return erg

    monkeypatch.setattr(anfragen, "verarbeite", wettlauf)
    antwort, nachlauf = anfragen.bearbeite(db, a)
    assert antwort.model_dump(mode="json", exclude_none=True) == andere_antwort
    assert nachlauf == []
    # Unser eigener Versuch (kunden.lege_an im Original-verarbeite) wurde durch den Rollback
    # nach dem kollidierenden Commit zurückgenommen – kein zusätzlicher Kunde.
    assert db.scalars(select(Kunde)).all() == []


def test_gleichzeitige_zustellung_im_fehlerpfad_liefert_die_andere_antwort(
    db: Session, welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Diese Session scheitert (Ausnahme); während sie noch dabei ist, hat eine andere Session die
    # Anfrage bereits erfolgreich verarbeitet und committet. Der Commit der lokalen "fehler"-
    # Antwort muss ebenfalls gegen diesen Wettlauf abgesichert sein und darf die echte Antwort
    # nicht überschreiben.
    a = anfrage("konto_angelegt", uuid.uuid4(), email="a@x.de", anzeigename="A")
    andere_antwort = {"status": "ok", "kunde_id": str(uuid.uuid4())}

    def kaputt(*args, **kwargs):
        with SessionLocal() as andere:
            andere.add(
                AnfrageVerarbeitet(anfrage_id=a.anfrage_id, typ=a.typ, antwort_json=andere_antwort)
            )
            andere.commit()
        raise RuntimeError("kaputt")

    monkeypatch.setattr(anfragen, "_konto_angelegt", kaputt)
    antwort, nachlauf = anfragen.bearbeite(db, a)
    assert antwort.model_dump(mode="json", exclude_none=True) == andere_antwort
    assert nachlauf == []
    assert db.scalars(select(Kunde)).all() == []
