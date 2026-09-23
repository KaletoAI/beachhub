from datetime import date, time
from decimal import Decimal

from beachhub_portal.services import slots
from beachhub_portal.services import tarife as tarif_dienst
from beachhub_shared.lesestand import BelegungInhalt, TarifeInhalt
from beachhub_shared.slots import Slot
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from hilfen import FELD_ID, JETZT, KUNDE_ID, belegung, konto, speichere, tarif, tarife
from sqlalchemy.orm import Session

HEUTE = date(2027, 11, 25)  # JETZT = 10:00 Uhr Berlin


def _b(**abweichend) -> BelegungInhalt:
    return BelegungInhalt.model_validate(belegung(**abweichend))


def _slot(stunde: int, tag: date = HEUTE) -> Slot:
    return Slot(kombiniere(tag, time(stunde)), kombiniere(tag, time(stunde + 1)))


def _belegt(tag: date, von: int, bis: int) -> dict:
    return {
        "beginn": kombiniere(tag, time(von)).isoformat(),
        "ende": kombiniere(tag, time(bis)).isoformat(),
    }


def test_zustaende() -> None:
    b = _b(belegt={FELD_ID: [_belegt(HEUTE, 19, 21)]})
    [(feld, liste)] = slots.tagesansicht(b, None, None, HEUTE, JETZT)
    zustand = {s.beginn: s.zustand for s in liste}
    assert len(liste) == 14  # 9 bis 23 Uhr
    assert zustand[kombiniere(HEUTE, time(10))] == "vorbei"  # Mindestvorlauf 60 min
    assert zustand[kombiniere(HEUTE, time(11))] == "frei"
    assert zustand[kombiniere(HEUTE, time(19))] == "belegt"
    assert zustand[kombiniere(HEUTE, time(20))] == "belegt"
    assert all(s.preis is None for s in liste)


def test_tage_und_fenstergrenze() -> None:
    b = _b(fenster_tage=3)
    assert slots.tage(b, JETZT) == [date(2027, 11, d) for d in (25, 26, 27, 28)]
    letzter = date(2027, 11, 28)  # Fensterende: 28.11. 10:00 Uhr
    assert slots.zustand(b, FELD_ID, _slot(9, letzter), JETZT) == "frei"
    assert slots.zustand(b, FELD_ID, _slot(12, letzter), JETZT) == "vorbei"


def test_betriebszeit_gueltigkeit_und_ausnahmetag() -> None:
    bz = [
        {
            "wochentag": wt,
            "oeffnet": "09:00:00",
            "schliesst": "23:00:00",
            "gueltig_von": None,
            "gueltig_bis": "2027-11-25",
        }
        for wt in range(7)
    ]
    ausnahme = {
        "datum": "2027-11-25",
        "geschlossen": False,
        "oeffnet": "18:00:00",
        "schliesst": "20:00:00",
    }
    b = _b(betriebszeiten=bz, ausnahmetage=[ausnahme])
    f = slots.feld(b, FELD_ID)
    assert [s.beginn for s in slots.tages_slots(b, f, HEUTE)] == [
        kombiniere(HEUTE, time(18)),
        kombiniere(HEUTE, time(19)),
    ]
    assert slots.tages_slots(b, f, date(2027, 11, 26)) == []


def test_fensterraster() -> None:
    raster = [
        {
            "wochentag": None,
            "modus": "fenster",
            "slot_minuten": None,
            "fenster": [["19:00", "21:00"], ["21:00", "23:00"]],
        }
    ]
    b = _b(felder=[{"id": FELD_ID, "name": "Feld 1", "reihenfolge": 1, "raster": raster}])
    ergebnis = [(s.beginn, s.ende) for s in slots.tages_slots(b, slots.feld(b, FELD_ID), HEUTE)]
    assert ergebnis == [
        (kombiniere(HEUTE, time(19)), kombiniere(HEUTE, time(21))),
        (kombiniere(HEUTE, time(21)), kombiniere(HEUTE, time(23))),
    ]


def test_zeitumstellung_slots_bleiben_korrekt() -> None:
    """`kombiniere` lokalisiert jede Slot-Grenze einzeln; an den Tagen der Zeitumstellung
    (letzter Sonntag im Oktober/März – der Wechsel liegt nachts um 02/03 Uhr, vor der
    Öffnung 09-23 Uhr) müssen trotzdem genau 14 durchgehende Ein-Stunden-Slots entstehen,
    mit dem an diesem Datum jeweils gültigen UTC-Versatz (Winter-/Sommerzeit)."""
    b = _b()
    f = slots.feld(b, FELD_ID)
    herbst = date(2027, 10, 31)  # Ende der Sommerzeit (Wechsel auf MEZ/UTC+1)
    fruehling = date(2028, 3, 26)  # Beginn der Sommerzeit (Wechsel auf MESZ/UTC+2)
    for tag in (herbst, fruehling):
        tages = slots.tages_slots(b, f, tag)
        assert len(tages) == 14
        assert tages[0].beginn == kombiniere(tag, time(9))
        assert tages[-1].ende == kombiniere(tag, time(23))
        assert all(a.ende == b_.beginn for a, b_ in zip(tages, tages[1:], strict=False))
    vor_herbst_utc = kombiniere(date(2027, 10, 30), time(9)).time()  # noch MESZ
    nach_herbst_utc = kombiniere(herbst, time(9)).time()  # schon MEZ
    assert vor_herbst_utc != nach_herbst_utc  # UTC-Versatz hat sich um eine Stunde verschoben


def test_folge_bis_zur_naechsten_belegung() -> None:
    b = _b(belegt={FELD_ID: [_belegt(HEUTE, 19, 21)]})
    assert slots.folge(b, FELD_ID, kombiniere(HEUTE, time(17)), JETZT) == [_slot(17), _slot(18)]
    assert slots.folge(b, FELD_ID, kombiniere(HEUTE, time(19)), JETZT) == []
    assert slots.folge(b, FELD_ID, kombiniere(HEUTE, time(17, 30)), JETZT) == []
    assert slots.folge(b, "gibtsnicht", kombiniere(HEUTE, time(17)), JETZT) == []


def test_preis_spezifischste_regel_und_neuere_bei_gleichstand() -> None:
    t = TarifeInhalt.model_validate(
        tarife(
            tarif("Std", "30.00"),
            tarif("Abend", "40.00", uhrzeit_von="18:00:00", uhrzeit_bis="23:00:00"),
            tarif("Abend neu", "42.00", uhrzeit_von="18:00:00", uhrzeit_bis="23:00:00"),
            tarif("Mitglied", "20.00", kundengruppe="Mitglied"),
        )
    )
    assert tarif_dienst.preis(t, FELD_ID, [_slot(12)], "Privat") == Decimal("30.00")
    assert tarif_dienst.preis(t, FELD_ID, [_slot(19)], "Privat") == Decimal("42.00")
    assert tarif_dienst.preis(t, FELD_ID, [_slot(12)], "Mitglied") == Decimal("20.00")
    assert tarif_dienst.preis(t, FELD_ID, [_slot(18), _slot(19)], "Privat") == Decimal("84.00")
    assert tarif_dienst.preis(TarifeInhalt(regeln=[]), FELD_ID, [_slot(12)], "Privat") is None
    assert tarif_dienst.preis(t, FELD_ID, [], "Privat") is None


def test_ohne_lesestand_hinweis(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200 and "wird gerade geladen" in r.text


def test_anonym_ohne_preise(client: TestClient, db: Session, uhr_steht) -> None:
    speichere(db, "belegung", belegung())
    speichere(db, "tarife", tarife())
    seite = client.get("/").text
    assert "Feld 1" in seite and "/buchen?feld=" in seite
    assert "30,00" not in seite and "Melde dich an" in seite


def test_angemeldet_mit_preisen_und_tagwahl(angemeldet: TestClient, db: Session) -> None:
    speichere(db, "belegung", belegung())
    speichere(db, "tarife", tarife())
    speichere(db, f"konto:{KUNDE_ID}", konto())
    seite = angemeldet.get("/?tag=2027-11-26").text
    assert "30,00 €" in seite
    assert 'href="/?tag=2027-11-26" aria-current="page"' in seite
    assert angemeldet.get("/?tag=quatsch").status_code == 200
    assert angemeldet.get("/?tag=2030-01-01").status_code == 200
