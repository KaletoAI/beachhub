import json
import uuid
from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock, jobs
from beachhub_core.config import settings
from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    GuthabenBuchung,
    Kundengruppe,
    Rechnung,
    Tarif,
    Zahlung,
)
from beachhub_core.services import (
    buchungen,
    dauerbuchungen,
    guthaben,
    kunden,
    lesestand,
    online_buchung,
)
from beachhub_core.services.rechnungen import RechnungsFehler
from beachhub_shared import kanal
from beachhub_shared.zeit import kombiniere
from sqlalchemy import select
from sqlalchemy.orm import Session

D = date(2027, 12, 1)
RUECK = "/zahlung/zurueck?anfrage=x"


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    p = Kundengruppe(name="Privat")
    db.add_all([f, p, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="A", email="a@x.de", kundengruppe_id=p.id)
    andere = kunden.lege_an(db, name="B", email="b@x.de", kundengruppe_id=p.id)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k, andere


def _anfragen(db: Session, f: Feld, k, stunde: int = 19, tag: date = D) -> online_buchung.Ergebnis:
    return online_buchung.anfragen(
        db,
        kunde=k,
        anfrage_id=uuid.uuid4(),
        feld_id=f.id,
        beginn=kombiniere(tag, time(stunde)),
        ende=kombiniere(tag, time(stunde + 1)),
        rueckkehr_url=RUECK,
    )


def _rueckmeldung(ref: str, ergebnis: str = "bezahlt", betrag: str = "30.00"):
    return kanal.ZahlungEingegangen(
        provider="fake",
        rohdaten=json.dumps({"ref": ref, "ergebnis": ergebnis, "betrag": betrag}),
    )


def _nachlauf(db: Session, erg: online_buchung.Ergebnis) -> None:
    db.commit()
    for schritt in erg.nach_commit:
        schritt(db)


def test_reservierung_mit_bezahlsitzung(db: Session, welt) -> None:
    f, k, _ = welt
    erg = _anfragen(db, f, k)
    db.commit()
    a = erg.antwort
    assert a.status == "reserviert"
    assert a.zu_zahlen == Decimal("30.00") and a.guthaben_verrechnet == Decimal("0.00")
    assert a.checkout_url is not None and a.checkout_url.startswith("/test-zahlung/fake_")
    b = db.get(Buchung, a.buchung_id)
    assert b.status == "reserviert" and b.zahlungsart == "online" and b.quelle == "portal"
    assert b.reserviert_bis == a.reserviert_bis
    z = db.scalar(select(Zahlung).where(Zahlung.buchung_id == b.id))
    assert z.status == "offen" and z.betrag == Decimal("30.00") and z.provider == "fake"
    assert z.checkout_url == a.checkout_url
    assert erg.nach_commit == []


def test_guthaben_deckt_alles(db: Session, welt, mail_ausgang) -> None:
    f, k, _ = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("40.00"), art="manuell")
    erg = _anfragen(db, f, k)
    _nachlauf(db, erg)
    assert erg.antwort.status == "bestaetigt"
    assert erg.antwort.guthaben_verrechnet == Decimal("30.00")
    assert k.guthaben == Decimal("10.00")
    r = db.scalar(select(Rechnung))
    assert r.status == "bezahlt" and r.pdf_pfad
    betreffe = [m["betreff"] for m in mail_ausgang]
    assert any(b.startswith("Buchung bestätigt") for b in betreffe)
    assert any(b.startswith("Rechnung ") for b in betreffe)


def test_pdf_fehler_im_nachlauf_alarmiert_betreiber(
    db: Session, welt, mail_ausgang, monkeypatch: pytest.MonkeyPatch
) -> None:
    f, k, _ = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("30.00"), art="manuell")

    def kaputt(*_a, **_k):
        raise RechnungsFehler("weasyprint")

    monkeypatch.setattr(online_buchung.rechnung_pdf, "erzeuge", kaputt)
    erg = _anfragen(db, f, k)
    _nachlauf(db, erg)
    assert erg.antwort.status == "bestaetigt"
    alarme = [m for m in mail_ausgang if m["an"] == settings.email_from]
    assert [m["betreff"] for m in alarme] == ["[Beachhub] Rechnungs-PDF nicht erzeugt"]
    assert db.scalar(select(Rechnung)).nummer in alarme[0]["text"]
    assert not any(m["betreff"].startswith("Rechnung ") for m in mail_ausgang)


def test_guthaben_teilweise(db: Session, welt) -> None:
    f, k, _ = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("10.00"), art="manuell")
    erg = _anfragen(db, f, k)
    assert erg.antwort.status == "reserviert"
    assert erg.antwort.guthaben_verrechnet == Decimal("10.00")
    assert erg.antwort.zu_zahlen == Decimal("20.00")
    assert k.guthaben == Decimal("0.00")
    z = db.scalar(select(Zahlung))
    assert z.betrag == Decimal("20.00")


def test_abgelehnt_mit_gruenden(db: Session, welt) -> None:
    f, k, andere = welt
    _anfragen(db, f, andere)
    db.commit()
    assert _anfragen(db, f, k).antwort.grund == "belegt"
    assert _anfragen(db, f, k, tag=date(2028, 1, 20)).antwort.grund == "ausserhalb_fenster"
    assert _anfragen(db, f, k, stunde=8).antwort.grund == "ausserhalb_betriebszeit"
    kunden.anonymisiere(db, k)
    db.commit()
    assert _anfragen(db, f, k, stunde=12).antwort.grund == "konto_gesperrt"


def test_kein_tarif(db: Session, welt) -> None:
    f, k, _ = welt
    for t in db.scalars(select(Tarif)):
        t.aktiv = False
    db.commit()
    assert _anfragen(db, f, k).antwort.grund == "kein_tarif"


def test_zahlung_bestaetigt_buchung(db: Session, welt, mail_ausgang) -> None:
    f, k, _ = welt
    a = _anfragen(db, f, k).antwort
    db.commit()
    z = db.scalar(select(Zahlung))
    erg = online_buchung.zahlung_eingegangen(db, _rueckmeldung(z.provider_ref))
    _nachlauf(db, erg)
    assert erg.antwort.status == "ok"
    b = db.get(Buchung, a.buchung_id)
    assert b.status == "bestaetigt" and b.reserviert_bis is None
    assert z.status == "bezahlt" and z.empfangen_am is not None
    assert db.scalar(select(Rechnung)).status == "bezahlt"
    assert any(m["betreff"].startswith("Buchung bestätigt") for m in mail_ausgang)


def test_doppelte_rueckmeldung_ist_folgenlos(db: Session, welt) -> None:
    f, k, _ = welt
    _anfragen(db, f, k)
    db.commit()
    ref = db.scalar(select(Zahlung)).provider_ref
    _nachlauf(db, online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref)))
    zweite = online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref))
    db.commit()
    assert zweite.antwort.status == "ok" and zweite.nach_commit == []
    assert len(db.scalars(select(Rechnung)).all()) == 1
    assert k.guthaben == Decimal("0.00")


def test_zahlung_nach_storno_wird_guthaben(db: Session, welt, mail_ausgang) -> None:
    f, k, _ = welt
    a = _anfragen(db, f, k).antwort
    db.commit()
    online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id)
    db.commit()
    ref = db.scalar(select(Zahlung)).provider_ref
    erg = online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref))
    _nachlauf(db, erg)
    assert db.get(Buchung, a.buchung_id).status == "storniert"
    assert k.guthaben == Decimal("30.00")
    assert db.scalar(select(Rechnung)) is None
    assert any(m["an"] == settings.email_from for m in mail_ausgang)


def test_zahlung_nach_verfall_wird_guthaben(db: Session, welt) -> None:
    f, k, _ = welt
    a = _anfragen(db, f, k).antwort
    db.commit()
    clock.set_override(db, date(2027, 11, 26))
    assert [b.id for b in online_buchung.verfalle_abgelaufene(db)] == [a.buchung_id]
    db.commit()
    ref = db.scalar(select(Zahlung)).provider_ref
    _nachlauf(db, online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref)))
    assert db.get(Buchung, a.buchung_id).status == "verfallen"
    assert k.guthaben == Decimal("30.00")


def test_zu_geringer_betrag_bestaetigt_nicht(db: Session, welt) -> None:
    f, k, _ = welt
    a = _anfragen(db, f, k).antwort
    db.commit()
    ref = db.scalar(select(Zahlung)).provider_ref
    erg = online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref, betrag="10.00"))
    _nachlauf(db, erg)
    assert db.get(Buchung, a.buchung_id).status == "reserviert"
    assert k.guthaben == Decimal("10.00")
    # Keine offene Zahlung mehr: Das Konto-Dokument führt keinen Zahlungslink (das Portal zeigt
    # daraufhin „Zahlung unvollständig“ statt eines Links).
    kb = next(b for b in lesestand.baue_konto(db, k).buchungen if b.id == str(a.buchung_id))
    assert kb.status == "reserviert" and kb.checkout_url is None and kb.reserviert_bis is None


def test_negativer_betrag_bestaetigt_nicht_und_bucht_kein_guthaben(db: Session, welt) -> None:
    """FakeProvider lässt negative Beträge durch (Task 3, R-Minor). Ein Zahlungseingang mit
    Betrag <= 0 darf weder bestätigen noch Guthaben erzeugen noch die Zahlung als bezahlt
    markieren – sonst würde eine später eingehende echte Zahlung folgenlos verpuffen
    (`z.status == BEZAHLT` löst den frühen `return ok()` aus, siehe Fix-Runde 1)."""
    f, k, _ = welt
    a = _anfragen(db, f, k).antwort
    db.commit()
    z = db.scalar(select(Zahlung))
    ref = z.provider_ref
    erg = online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref, betrag="-5.00"))
    _nachlauf(db, erg)
    assert erg.antwort.status == "ignoriert" and erg.antwort.grund == "betrag_ungueltig"
    assert erg.nach_commit == []
    assert z.status == "offen"
    assert db.get(Buchung, a.buchung_id).status == "reserviert"
    assert k.guthaben == Decimal("0.00")

    # Eine später eingehende gültige Rückmeldung zur selben Referenz bestätigt weiterhin.
    zweite = online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref))
    _nachlauf(db, zweite)
    assert db.get(Buchung, a.buchung_id).status == "bestaetigt"
    assert z.status == "bezahlt"


def test_abgebrochene_zahlung_laesst_reservierung_stehen(db: Session, welt) -> None:
    f, k, _ = welt
    a = _anfragen(db, f, k).antwort
    db.commit()
    z = db.scalar(select(Zahlung))
    erg = online_buchung.zahlung_eingegangen(db, _rueckmeldung(z.provider_ref, "abgebrochen"))
    db.commit()
    assert erg.antwort.status == "ok"
    assert z.status == "abgebrochen"
    assert db.get(Buchung, a.buchung_id).status == "reserviert"


def test_unbrauchbare_rueckmeldungen_werden_ignoriert(db: Session, welt) -> None:
    fremd = kanal.ZahlungEingegangen(provider="stripe", rohdaten="{}")
    assert online_buchung.zahlung_eingegangen(db, fremd).antwort.grund == "anbieter_unbekannt"
    kaputt = kanal.ZahlungEingegangen(provider="fake", rohdaten="kein json")
    assert online_buchung.zahlung_eingegangen(db, kaputt).antwort.grund == "nicht_verifiziert"
    unbekannt = _rueckmeldung("fake_gibtsnicht")
    assert online_buchung.zahlung_eingegangen(db, unbekannt).antwort.grund == "zahlung_unbekannt"


def test_nicht_verifizierbare_rueckmeldung_loggt_keine_rohdaten(
    db: Session, welt, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # alembic.fileConfig (test_migrationen) schaltet bestehende Logger ab; hier wieder an.
    monkeypatch.setattr(online_buchung.logger, "disabled", False)
    roh = "kein json, aber vielleicht Zahlungsdaten: IBAN DE02120300000000202051"
    with caplog.at_level("WARNING", logger=online_buchung.logger.name):
        erg = online_buchung.zahlung_eingegangen(
            db, kanal.ZahlungEingegangen(provider="fake", rohdaten=roh)
        )
    assert erg.antwort.grund == "nicht_verifiziert"
    assert "fake" in caplog.text and f"{len(roh)} Zeichen" in caplog.text
    assert "IBAN" not in caplog.text and "kein json" not in caplog.text


def test_unbekannte_referenz_alarmiert_betreiber(db: Session, welt, mail_ausgang) -> None:
    unbekannt = _rueckmeldung("fake_gibtsnicht")
    erg = online_buchung.zahlung_eingegangen(db, unbekannt)
    assert erg.antwort.status == "ignoriert" and erg.antwort.grund == "zahlung_unbekannt"
    assert len(erg.nach_commit) == 1
    _nachlauf(db, erg)
    assert any(m["an"] == settings.email_from for m in mail_ausgang)


def test_ueberzahlung_bestaetigt_und_bucht_ueberschuss_als_guthaben(
    db: Session, welt, mail_ausgang
) -> None:
    f, k, _ = welt
    a = _anfragen(db, f, k).antwort
    db.commit()
    ref = db.scalar(select(Zahlung)).provider_ref
    erg = online_buchung.zahlung_eingegangen(db, _rueckmeldung(ref, betrag="35.00"))
    _nachlauf(db, erg)
    assert erg.antwort.status == "ok"
    assert db.get(Buchung, a.buchung_id).status == "bestaetigt"
    assert k.guthaben == Decimal("5.00")
    arten = [
        g.art for g in db.scalars(select(GuthabenBuchung).order_by(GuthabenBuchung.created_at))
    ]
    assert arten == ["ueberzahlung"]
    assert any(m["an"] == settings.email_from for m in mail_ausgang)


def test_storno_reservierung_kostenfrei_mit_rueckbuchung(db: Session, welt) -> None:
    f, k, _ = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("10.00"), art="manuell")
    a = _anfragen(db, f, k).antwort
    db.commit()
    assert k.guthaben == Decimal("0.00")
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id)
    db.commit()
    assert erg.antwort.status == "ok" and erg.antwort.kostenfrei is True
    assert k.guthaben == Decimal("10.00")
    assert db.get(Buchung, a.buchung_id).status == "storniert"
    arten = [
        g.art for g in db.scalars(select(GuthabenBuchung).order_by(GuthabenBuchung.created_at))
    ]
    assert arten == ["manuell", "verrechnung", "rueckbuchung"]


def test_storno_bestaetigt_vor_frist_mit_gutschrift(db: Session, welt, mail_ausgang) -> None:
    f, k, _ = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("30.00"), art="manuell")
    a = _anfragen(db, f, k).antwort
    db.commit()
    assert a.status == "bestaetigt"
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id)
    _nachlauf(db, erg)
    assert erg.antwort.kostenfrei is True
    assert k.guthaben == Decimal("30.00")
    assert any(m["betreff"] == "Stornierung Ihrer Buchung" for m in mail_ausgang)


def test_storno_dauerbuchungstermin_nicht_stornierbar(db: Session, welt) -> None:
    # Ein Dauerbuchungstermin ist nie online bezahlt worden: kein Storno im Portal, kein Guthaben.
    f, k, _ = welt
    dauer = dauerbuchungen.lege_an(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=1,
        start=time(19),
        ende=time(20),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 10),
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
    )
    db.commit()
    termin = dauer.buchungen[0]
    assert termin.zahlungsart == "online"
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=termin.id)
    db.commit()
    assert erg.antwort.status == "abgelehnt" and erg.antwort.grund == "nicht_stornierbar"
    assert erg.nach_commit == []
    assert db.get(Buchung, termin.id).status == "bestaetigt"
    assert k.guthaben == Decimal("0.00")
    assert db.scalars(select(GuthabenBuchung)).all() == []


def test_storno_betreiberbuchung_nicht_stornierbar(db: Session, welt) -> None:
    # Eine vom Betreiber angelegte Buchung wird außerhalb des Systems bezahlt; ob und wie viel
    # Geld zurückgeht, entscheidet der Betreiber – das Portal storniert nur Portal-Buchungen.
    f, k, _ = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(20)),
    )
    db.commit()
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=b.id)
    db.commit()
    assert erg.antwort.status == "abgelehnt" and erg.antwort.grund == "nicht_stornierbar"
    assert db.get(Buchung, b.id).status == "bestaetigt"
    assert k.guthaben == Decimal("0.00")


def test_storno_fremder_buchung(db: Session, welt) -> None:
    f, k, andere = welt
    a = _anfragen(db, f, andere).antwort
    db.commit()
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id)
    assert erg.antwort.status == "abgelehnt" and erg.antwort.grund == "nicht_gefunden"
    assert db.get(Buchung, a.buchung_id).status == "reserviert"
    unbekannt = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=uuid.uuid4())
    assert unbekannt.antwort.grund == "nicht_gefunden"


def test_storno_nach_beginn_zu_spaet(db: Session, welt) -> None:
    f, k, _ = welt
    b = buchungen.lege_an(
        db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, time(9)), ende=kombiniere(D, time(10))
    )
    db.commit()
    clock.set_override(db, date(2027, 12, 2))
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=b.id)
    assert erg.antwort.grund == "zu_spaet"


def test_storno_waehrend_der_buchung_zu_spaet(db: Session, welt, monkeypatch) -> None:
    f, k, _ = welt
    b = buchungen.lege_an(
        db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, time(9)), ende=kombiniere(D, time(10))
    )
    db.commit()
    monkeypatch.setattr(online_buchung.clock, "now", lambda db: kombiniere(D, time(9, 30)))
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=b.id)
    assert erg.antwort.grund == "zu_spaet"
    assert db.get(Buchung, b.id).status == "bestaetigt"


def test_storno_bestaetigt_nach_frist_kostenpflichtig_ohne_gutschrift(
    db: Session, welt, monkeypatch
) -> None:
    f, k, _ = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("30.00"), art="manuell")
    a = _anfragen(db, f, k).antwort
    db.commit()
    assert a.status == "bestaetigt"
    # 23 h vor Buchungsbeginn (Do 19:00): innerhalb der Default-Stornofrist von 24 h.
    monkeypatch.setattr(
        online_buchung.clock, "now", lambda db: kombiniere(date(2027, 11, 30), time(20))
    )
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id)
    assert erg.antwort.status == "ok" and erg.antwort.kostenfrei is False
    assert db.get(Buchung, a.buchung_id).status == "storniert"
    assert k.guthaben == Decimal("0.00")


def test_verfall_job_bucht_zurueck_und_mailt(db: Session, welt, mail_ausgang) -> None:
    f, k, _ = welt
    guthaben.buche(db, kunde=k, betrag=Decimal("10.00"), art="manuell")
    a = _anfragen(db, f, k).antwort
    db.commit()
    assert jobs.verfall_ausfuehren(db) == 0
    clock.set_override(db, date(2027, 11, 26))
    assert jobs.verfall_ausfuehren(db) == 1
    assert db.get(Buchung, a.buchung_id).status == "verfallen"
    assert k.guthaben == Decimal("10.00")
    assert any(m["betreff"] == "Reservierung verfallen" for m in mail_ausgang)
    assert jobs.verfall_ausfuehren(db) == 0
