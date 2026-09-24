import base64
import hashlib
from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from argon2.low_level import Type, hash_secret_raw
from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import Buchung, LesestandVersion, Sperre
from beachhub_core.services import buchungen, konfiguration, lesestand, pin, storno
from beachhub_shared.hallenplan import HallenplanInhalt, pin_hash
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session

# Fixture `welt`: aus core/tests/hilfen_halle.py, projektweit über conftest.py (pytest_plugins)
# eingebunden.


def _buchung(db: Session, f, k, tag: date, von: int, bis: int, **kw) -> Buchung:
    return buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(tag, time(von)),
        ende=kombiniere(tag, time(bis)),
        **kw,
    )


def test_neue_vorgaben_des_betreibers(db: Session) -> None:
    assert konfiguration.hole(db, "heiz_vorlauf_minuten") == 30
    assert konfiguration.hole(db, "spiel_temperatur") == Decimal("18.0")
    assert konfiguration.hole(db, "grund_temperatur") == Decimal("0.0")
    assert konfiguration.hole(db, "praesenz_alarm_minuten") == 10
    assert set(konfiguration.HALLEN_KONFIG) <= set(konfiguration.BESCHREIBUNGEN)


def test_pin_hash_ist_unveraendert() -> None:
    """Bestehende Buchungen tragen Hashes aus Stufe 1 – der Umbau auf shared darf keinen
    einzigen davon ungültig machen. Die Goldwerte sind mit dem alten Algorithmus (vor der
    Umstellung auf shared) und dem PIN_SCHLUESSEL aus conftest.py berechnet und fest
    hinterlegt, damit ein zukünftiger Bug in der Nachrechnung selbst diesen Test nicht mehr
    grün aussehen lässt."""
    assert pin.hash("482913") == "argon2id$T0ZWylobq8ZodOzcaVFAac73RaKoG/u4gJROclD42IE="
    assert pin.parameter().salt_b64 == "DzmZWTngLr0Yo5w45jX/7g=="

    # Nachrechnung mit dem alten, direkt hier nachgebauten Algorithmus – bleibt zusätzlich zu
    # den Goldwerten bestehen, damit auch eine unbeabsichtigte Änderung von PIN_SCHLUESSEL in
    # conftest.py auffiele (die Goldwerte allein würden dann nur beide falsch, aber gleich sein).
    schluessel = hashlib.sha256(settings.pin_schluessel.encode()).digest()
    salt = hashlib.sha256(b"beachhub-pin-salt" + schluessel).digest()[:16]
    raw = hash_secret_raw(
        b"482913", salt, time_cost=2, memory_cost=65536, parallelism=1, hash_len=32, type=Type.ID
    )
    assert pin.hash("482913") == "argon2id$" + base64.b64encode(raw).decode()
    assert base64.b64decode(pin.parameter().salt_b64) == pin.salt() == salt


def test_plan_enthaelt_nur_bestaetigte_buchungen_der_naechsten_7_tage(db: Session, welt) -> None:
    f, k = welt
    rein = _buchung(db, f, k, date(2027, 11, 27), 19, 20, pin_klar="482913")
    _buchung(db, f, k, date(2027, 11, 27), 20, 21, status=Buchung.RESERVIERT)
    _buchung(db, f, k, date(2027, 12, 5), 19, 20)  # 10 Tage entfernt
    weg = _buchung(db, f, k, date(2027, 11, 28), 19, 20)
    storno.storniere(db, weg, durch="betreiber", kostenfrei=True)
    db.add(
        Sperre(
            feld_id=None,
            beginn=kombiniere(date(2027, 11, 29), time(9)),
            ende=kombiniere(date(2027, 11, 29), time(12)),
            grund="Turnier",
        )
    )
    db.commit()
    inhalt = lesestand.baue_hallenplan(db)
    assert [b.buchung_id for b in inhalt.buchungen] == [str(rein.id)]
    assert inhalt.buchungen[0].pin_hash == pin.hash("482913") == rein.pin_hash
    assert len(inhalt.sperren) == 1 and inhalt.sperren[0].feld_id is None
    assert [x.id for x in inhalt.felder] == [str(f.id)]
    assert inhalt.konfig.heiz_vorlauf_minuten == 30 and inhalt.konfig.spiel_temperatur == Decimal(
        "18.0"
    )
    assert (inhalt.gueltig_bis - inhalt.gueltig_ab).days == 7
    assert "a@x.de" not in inhalt.model_dump_json() and '"A"' not in inhalt.model_dump_json()


def test_hallenplan_grenzfaelle_des_fensters(
    db: Session, welt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Laufende Buchung (beginn < jetzt < ende) ist enthalten; eine Buchung, die erst mit
    gueltig_bis beginnt, und eine Sperre, die schon vor jetzt endet, dagegen nicht – mit fest
    gesetzter Uhr, damit die Grenzen exakt getroffen werden."""
    f, k = welt
    tag = date(2027, 11, 27)
    # Zur Anlage eine frühere Uhr als zum Planbau: sonst würde buchungen.lege_an eine Buchung
    # mit beginn in der Vergangenheit ablehnen ("vergangenheit").
    monkeypatch.setattr(clock, "now", lambda db: kombiniere(tag, time(8)))
    laufend = _buchung(db, f, k, tag, 11, 13)
    genau_am_rand = _buchung(db, f, k, date(2027, 12, 4), 12, 13)
    db.add(
        Sperre(
            feld_id=None,
            beginn=kombiniere(tag, time(8)),
            ende=kombiniere(tag, time(9)),
            grund="Endet vor jetzt",
        )
    )
    db.commit()

    jetzt = kombiniere(tag, time(12))
    monkeypatch.setattr(clock, "now", lambda db: jetzt)
    inhalt = lesestand.baue_hallenplan(db)

    assert inhalt.gueltig_ab == jetzt
    assert inhalt.gueltig_bis == jetzt + timedelta(days=7)
    # genau_am_rand.beginn == gueltig_bis (2027-12-04 12:00): per Filter "beginn < bis"
    # ausgeschlossen.
    assert genau_am_rand.beginn == inhalt.gueltig_bis
    assert [b.buchung_id for b in inhalt.buchungen] == [str(laufend.id)]
    assert inhalt.sperren == []


def test_vertrag_pin_hash_halle_gleich_hauptsystem(db: Session, welt) -> None:
    """Die Halle prüft mit shared.pin_hash und den Parametern aus dem signierten Plan – das
    muss exakt den Hash ergeben, den das Hauptsystem an der Buchung speichert."""
    f, k = welt
    b = _buchung(db, f, k, date(2027, 11, 27), 19, 20, pin_klar="031415")
    db.commit()
    dok = lesestand.publiziere(db, "hallenplan")
    db.commit()
    assert dok.dokument == "hallenplan" and lesestand.pruefe(dok)
    inhalt = HallenplanInhalt.model_validate(dok.inhalt)
    assert pin_hash("031415", inhalt.pin) == b.pin_hash


def test_buchung_markiert_den_plan(db: Session, welt) -> None:
    f, k = welt
    lesestand.publiziere(db, "hallenplan")
    db.commit()
    assert db.get(LesestandVersion, "hallenplan").geaendert is False
    _buchung(db, f, k, date(2027, 11, 27), 19, 20)
    db.commit()
    assert db.get(LesestandVersion, "hallenplan").geaendert is True


def test_konfiguration_markiert_den_plan_nur_bei_hallenwerten(db: Session, welt) -> None:
    lesestand.publiziere(db, "hallenplan")
    db.commit()
    # Negativbeispiel bewusst ohne Hallenbezug: storno_frist_stunden markiert seit dem
    # Portal-Kern selbst "belegung" und damit über die Kopplung auch "hallenplan" (Ruling K3).
    konfiguration.setze(db, "pin_laenge", 8)
    db.commit()
    assert db.get(LesestandVersion, "hallenplan").geaendert is False
    konfiguration.setze(db, "licht_vorlauf_minuten", 10)
    db.commit()
    assert db.get(LesestandVersion, "hallenplan").geaendert is True
    assert lesestand.baue_hallenplan(db).konfig.licht_vorlauf_minuten == 10
