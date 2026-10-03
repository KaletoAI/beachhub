from datetime import date, time, timedelta
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Audit,
    Betriebszeit,
    Feld,
    FeldRaster,
    LesestandVersion,
    Tarif,
    utcnow,
)
from beachhub_core.services import (
    buchungen,
    konfiguration,
    kunden,
    kundengruppen,
    mitgliedschaft,
    storno,
)
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session

HEUTE = date(2027, 11, 25)


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    mitglied = kundengruppen.mitglied(db)
    db.add_all(
        [
            f,
            Tarif(name="Std", preis=Decimal("30.00")),
            Tarif(name="Mitglieder", preis=Decimal("20.00"), kundengruppe_id=mitglied.id),
        ]
    )
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="Anna", email="anna@x.de")
    db.commit()
    clock.set_override(db, HEUTE)
    return f, k


@pytest.mark.parametrize(
    "tag,naechster,letzter",
    [
        (date(2027, 11, 25), date(2028, 4, 30), date(2027, 4, 30)),
        (date(2028, 4, 30), date(2029, 4, 30), date(2028, 4, 30)),
        (date(2028, 1, 1), date(2028, 4, 30), date(2027, 4, 30)),
    ],
)
def test_ablauftage(db: Session, tag: date, naechster: date, letzter: date) -> None:
    assert mitgliedschaft.naechster_ablauf(db, tag) == naechster
    assert mitgliedschaft.letzter_ablauf(db, tag) == letzter


def test_ablauftag_folgt_der_einstellung(db: Session) -> None:
    konfiguration.setze(db, "mitgliedschaft_ablauf", "31.03.")
    assert mitgliedschaft.naechster_ablauf(db, date(2027, 11, 25)) == date(2028, 3, 31)


def test_freischalten_setzt_status_und_preis(db: Session, welt) -> None:
    f, k = welt
    k.mitglied_antrag_am = utcnow()
    k.mitglied_antrag_hinweis = "Nr. 4711"
    db.commit()
    assert mitgliedschaft.status(k, HEUTE) == "beantragt"
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    db.commit()
    assert mitgliedschaft.status(k, HEUTE) == "mitglied"
    # Der Antrag ist erledigt, die Angaben daraus (Mitgliedsnummer) bleiben stehen.
    assert k.mitglied_antrag_am is None and k.mitglied_antrag_hinweis == "Nr. 4711"
    assert k.mitglied_freigeschaltet_am is not None
    a = (
        db.query(Audit)
        .filter_by(objekt_typ="kunde", objekt_id=k.id)
        .order_by(Audit.zeitpunkt.desc())
        .first()
    )
    assert a.nachher_json["aktion"] == "mitglied_freigeschaltet"
    assert a.nachher_json["mitglied_bis"] == "2028-04-30"
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
        zahlungsart="online",
    )
    assert b.preis == Decimal("20.00") and b.ust_satz == Decimal("7.00")


def test_freischalten_lehnt_vergangenes_datum_ab(db: Session, welt) -> None:
    _, k = welt
    with pytest.raises(mitgliedschaft.MitgliedschaftsFehler, match="bis_vergangen"):
        mitgliedschaft.freischalten(db, k, bis=date(2027, 11, 24), admin_user_id=None)


def test_beenden_wirkt_sofort(db: Session, welt) -> None:
    _, k = welt
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    db.commit()
    assert mitgliedschaft.beende(db, k, grund="ausgetreten", admin_user_id=None) is True
    db.commit()
    assert k.mitglied_bis == date(2027, 11, 24) and k.mitglied_beendet_grund == "ausgetreten"
    assert mitgliedschaft.status(k, HEUTE) == "nicht_mitglied"


def test_beenden_einer_abgelaufenen_mitgliedschaft_vermerkt_nur(db: Session, welt) -> None:
    _, k = welt
    k.mitglied_bis = date(2027, 4, 30)
    db.commit()
    assert mitgliedschaft.beende(db, k, grund="nicht verlängert", admin_user_id=None) is False
    assert k.mitglied_bis == date(2027, 4, 30) and k.mitglied_beendet_am is not None


def test_beenden_ohne_mitgliedschaft_wirft(db: Session, welt) -> None:
    _, k = welt
    with pytest.raises(mitgliedschaft.MitgliedschaftsFehler, match="kein_mitglied"):
        mitgliedschaft.beende(db, k, grund="x", admin_user_id=None)


def test_antrag_verwerfen(db: Session, welt) -> None:
    _, k = welt
    with pytest.raises(mitgliedschaft.MitgliedschaftsFehler, match="kein_antrag"):
        mitgliedschaft.verwerfe_antrag(db, k, admin_user_id=None)
    k.mitglied_antrag_am = utcnow()
    db.commit()
    assert mitgliedschaft.offene_antraege(db) == [k]
    mitgliedschaft.verwerfe_antrag(db, k, admin_user_id=None)
    db.commit()
    assert mitgliedschaft.offene_antraege(db) == []
    assert mitgliedschaft.status(k, HEUTE) == "nicht_mitglied"


def _termin(db: Session, f, k, tag: date, stunde: int = 19):
    return buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(tag, time(stunde)),
        ende=kombiniere(tag, time(stunde + 1)),
        zahlungsart="online",
    )


def test_beenden_bringt_kuenftige_mitgliedsbuchung_in_die_klaerung(db: Session, welt) -> None:
    f, k = welt
    andere = kunden.lege_an(db, name="Bea", email="bea@x.de")
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    vergangen = _termin(db, f, k, date(2027, 11, 26))
    kuenftig = _termin(db, f, k, date(2027, 12, 10))
    _termin(db, f, andere, date(2027, 12, 10), stunde=20)  # Nicht-Mitglied: nie in der Liste
    db.commit()
    assert mitgliedschaft.klaerungsfaelle(db) == []

    clock.set_override(db, date(2027, 12, 1))
    mitgliedschaft.beende(db, k, grund="ausgetreten", admin_user_id=None)
    db.commit()
    assert mitgliedschaft.klaerungsfaelle(db) == [kuenftig]
    assert vergangen not in mitgliedschaft.klaerungsfaelle(db)
    # Die Buchung behält ihre Konditionen (A-TARIF-3).
    assert (kuenftig.preis, kuenftig.ust_satz) == (Decimal("20.00"), Decimal("7.00"))

    mitgliedschaft.klaere(db, kuenftig, admin_user_id=None)
    db.commit()
    assert mitgliedschaft.klaerungsfaelle(db) == []
    assert kuenftig.gruppe_geklaert_am is not None


def test_storno_nimmt_buchung_aus_der_klaerung(db: Session, welt) -> None:
    f, k = welt
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    b = _termin(db, f, k, date(2027, 12, 10))
    db.commit()
    mitgliedschaft.beende(db, k, grund="ausgetreten", admin_user_id=None)
    db.commit()
    assert mitgliedschaft.klaerungsfaelle(db) == [b]
    storno.storniere(db, b, durch="betreiber", kostenfrei=True, grund="Mitgliedschaft beendet")
    db.commit()
    assert mitgliedschaft.klaerungsfaelle(db) == []


def _mitglied(db: Session, name: str, bis: date, beendet: bool = False):
    k = kunden.lege_an(db, name=name, email=f"{name.lower()}@x.de")
    k.mitglied_bis = bis
    if beendet:
        k.mitglied_beendet_am = utcnow()
    return k


def test_pruefliste_umfasst_auslaufende_und_ausgelaufene(db: Session, welt) -> None:
    heute = date(2027, 8, 31)
    clock.set_override(db, heute)
    ausgelaufen = _mitglied(db, "Alt", date(2027, 4, 30))
    laufend = _mitglied(db, "Neu", date(2028, 4, 30))
    _mitglied(db, "Uralt", date(2026, 4, 30))  # vorletzte Saison: schon abgeglichen
    _mitglied(db, "Weg", date(2027, 4, 30), beendet=True)  # ausdrücklich beendet
    db.commit()
    assert mitgliedschaft.pruefliste(db, heute) == [ausgelaufen, laufend]


def test_sammelaktionen(db: Session, welt) -> None:
    heute = date(2027, 8, 31)
    clock.set_override(db, heute)
    a = _mitglied(db, "A", date(2027, 4, 30))
    b = _mitglied(db, "B", date(2028, 4, 30))
    db.commit()
    assert mitgliedschaft.verlaengere_alle(db, [a.id, b.id], admin_user_id=None) == [a]
    db.commit()
    assert a.mitglied_bis == date(2028, 4, 30)  # b war schon bis dahin freigeschaltet
    c = _mitglied(db, "C", date(2027, 4, 30))
    db.commit()
    ergebnis = mitgliedschaft.beende_alle(
        db, [b.id, c.id], grund="kein Mitglied mehr", admin_user_id=None
    )
    db.commit()
    assert [(k.name, galt) for k, galt in ergebnis] == [("B", True), ("C", False)]
    assert mitgliedschaft.pruefliste(db, heute) == [a]


def test_pruefliste_csv(db: Session, welt) -> None:
    k = _mitglied(db, "=Anna", date(2027, 4, 30))
    k.mitglied_antrag_hinweis = "Nr. 4711"
    db.commit()
    zeilen = mitgliedschaft.pruefliste_csv([k]).strip().splitlines()
    # Werte, die mit = beginnen, entschärft csv_sicher mit einem Hochkomma (Formel-Injection).
    assert zeilen == [
        "name;email;mitglied_bis;angaben_im_antrag",
        "'=Anna;'=anna@x.de;2027-04-30;Nr. 4711",
    ]


@pytest.mark.parametrize(
    "saisonstart,warnt", [(date(2027, 9, 15), False), (date(2027, 9, 10), True)]
)
def test_abgleich_warnung(db: Session, saisonstart: date, warnt: bool) -> None:
    db.add(Betriebszeit(wochentag=0, oeffnet=time(9), schliesst=time(23), gueltig_von=saisonstart))
    db.commit()
    assert (mitgliedschaft.abgleich_warnung(db, date(2027, 6, 1)) is not None) is warnt


def test_ohne_befristete_betriebszeiten_keine_warnung(db: Session, welt) -> None:
    assert mitgliedschaft.abgleich_warnung(db, date(2027, 6, 1)) is None


def test_tageslauf_meldet_abgleich_einmal_und_erinnert_einmal(db: Session, welt) -> None:
    _, k = welt
    k.mitglied_bis = date(2028, 4, 30)
    db.commit()
    clock.set_override(db, date(2027, 8, 30))
    lauf = mitgliedschaft.tageslauf(db)
    db.commit()
    assert lauf.abgleich is None and lauf.erinnert == []
    clock.set_override(db, date(2027, 8, 31))
    assert mitgliedschaft.tageslauf(db).abgleich == 1
    db.commit()
    assert mitgliedschaft.tageslauf(db).abgleich is None  # nur einmal im Jahr
    db.commit()
    clock.set_override(db, date(2028, 4, 16))  # 14 Tage vor Ablauf
    assert mitgliedschaft.tageslauf(db).erinnert == [k.id]
    db.commit()
    assert mitgliedschaft.tageslauf(db).erinnert == []  # je Ablaufdatum nur einmal
    assert k.mitglied_erinnert_fuer == date(2028, 4, 30)


def test_tageslauf_markiert_konto_nach_ablauf(db: Session, welt) -> None:
    abgelaufen = _mitglied(db, "Abgelaufen", date(2028, 4, 30))
    laufend = _mitglied(db, "Laufend", date(2029, 4, 30))
    db.commit()
    for k in (abgelaufen, laufend):
        db.merge(LesestandVersion(dokument=f"konto:{k.id}", version=1, geaendert=False))
    db.commit()
    clock.set_override(db, date(2028, 5, 1))
    mitgliedschaft.tageslauf(db)
    db.commit()
    db.expire_all()
    assert db.get(LesestandVersion, f"konto:{abgelaufen.id}").geaendert is True
    assert db.get(LesestandVersion, f"konto:{laufend.id}").geaendert is False


def test_tageslauf_holt_verpassten_tag_nach(db: Session, welt) -> None:
    ende = _mitglied(db, "Ende", date(2028, 4, 30))
    spaeter = _mitglied(db, "Spaeter", date(2029, 4, 30))
    db.commit()
    for k in (ende, spaeter):
        db.merge(LesestandVersion(dokument=f"konto:{k.id}", version=1, geaendert=False))
    db.commit()
    clock.set_override(db, date(2028, 4, 29))
    mitgliedschaft.tageslauf(db)
    db.commit()
    clock.set_override(db, date(2028, 5, 3))  # 30.04., 01.05. und 02.05. ausgefallen
    mitgliedschaft.tageslauf(db)
    db.commit()
    db.expire_all()
    assert db.get(LesestandVersion, f"konto:{ende.id}").geaendert is True
    assert db.get(LesestandVersion, f"konto:{spaeter.id}").geaendert is False


def test_freischalten_im_erinnerungsfenster_setzt_erinnert_fuer(db: Session, welt) -> None:
    _, k = welt
    mitgliedschaft.freischalten(db, k, bis=HEUTE + timedelta(days=5), admin_user_id=None)
    assert k.mitglied_erinnert_fuer == HEUTE + timedelta(days=5)
    k2 = _mitglied(db, "Weit", HEUTE)
    mitgliedschaft.freischalten(db, k2, bis=date(2028, 4, 30), admin_user_id=None)
    assert k2.mitglied_erinnert_fuer is None
