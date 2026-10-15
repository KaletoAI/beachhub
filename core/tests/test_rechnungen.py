from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Audit,
    Betriebszeit,
    Feld,
    FeldRaster,
    Rechnung,
    RechnungPosition,
    Tarif,
)
from beachhub_core.services import (
    buchungen,
    dauerbuchungen,
    konfiguration,
    kunden,
    rechnung_pdf,
    rechnungen,
)
from beachhub_shared.zeit import kombiniere
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    a = kunden.lege_an(
        db,
        name="A",
        email="a@x.de",
        adresse_strasse="Weg 1",
        adresse_plz="12345",
        adresse_ort="Ort",
    )
    v1 = kunden.lege_an(db, name="TSV", email="v@x.de", rechnungskunde=True)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    konfiguration.setze(
        db, "storno_frist_stunden", 72
    )  # Storno am 14.12. für den 15.12. ist damit sicher kostenpflichtig
    db.commit()
    return f, a, v1


def test_nummern_lueckenlos_je_jahr(db: Session) -> None:
    assert rechnungen.naechste_nummer(db, 2027) == "2027-00001"
    assert rechnungen.naechste_nummer(db, 2027) == "2027-00002"
    assert rechnungen.naechste_nummer(db, 2028) == "2028-00001"


def test_nummernkreise_sind_getrennt(db: Session) -> None:
    assert rechnungen.naechste_nummer(db, 2027, "gutschein") == "GB-2027-00001"
    assert rechnungen.naechste_nummer(db, 2027) == "2027-00001"
    assert rechnungen.naechste_nummer(db, 2027, "gutschein") == "GB-2027-00002"


def test_exakte_gegenbetraege(db: Session, welt) -> None:
    _, kunde, _ = welt
    gegen = rechnungen._neue_rechnung(
        db,
        kunde,
        "storno",
        [
            rechnungen.Posten(
                None,
                "Centrest",
                Decimal("-0.03"),
                Decimal("19"),
                netto_vorgegeben=Decimal("-0.02"),
                ust_vorgegeben=Decimal("-0.01"),
            )
        ],
        date(2027, 11, 25),
        date(2027, 11, 25),
        "bezahlt",
        quelle="system",
    )
    assert (gegen.netto, gegen.ust, gegen.brutto) == (
        Decimal("-0.02"),
        Decimal("-0.01"),
        Decimal("-0.03"),
    )
    assert gegen.netto + gegen.ust == gegen.brutto


def test_korrektur_negiert_exakte_belegbasis(db: Session, welt) -> None:
    _, kunde, _ = welt
    r = rechnungen._neue_rechnung(
        db,
        kunde,
        "einzel",
        [
            rechnungen.Posten(
                None,
                "Centrest",
                Decimal("0.03"),
                Decimal("19"),
                netto_vorgegeben=Decimal("0.02"),
                ust_vorgegeben=Decimal("0.01"),
            )
        ],
        date(2027, 11, 25),
        date(2027, 11, 25),
        "bezahlt",
        quelle="system",
    )
    gegen = rechnungen.korrigiere(db, r.positionen, grund="Test", quelle="system")
    assert (gegen.netto, gegen.ust, gegen.brutto) == (
        Decimal("-0.02"),
        Decimal("-0.01"),
        Decimal("-0.03"),
    )


@pytest.mark.parametrize(
    "netto,ust",
    [
        (Decimal("0.02"), None),
        (None, Decimal("0.01")),
        (Decimal("0.02"), Decimal("0.02")),
        (Decimal("0.021"), Decimal("0.009")),
        (Decimal("NaN"), Decimal("0.01")),
        (Decimal("Infinity"), Decimal("0.01")),
        (0.02, Decimal("0.01")),
        (Decimal("1E100"), Decimal("-1E100")),
    ],
)
def test_ungueltige_exakte_betraege(netto, ust) -> None:
    with pytest.raises(rechnungen.RechnungsFehler, match="ungueltige_belegbasis"):
        rechnungen.Posten(
            None, "Teil", Decimal("0.03"), Decimal("19"), netto_vorgegeben=netto, ust_vorgegeben=ust
        )


def test_gutschein_anlageaudit_vollstaendig(db: Session, welt, admin) -> None:
    import uuid

    _, kunde, _ = welt
    user, _ = admin
    ursprung = rechnungen._neue_rechnung(
        db,
        kunde,
        "gutschein",
        [rechnungen.Posten(None, "Kauf", Decimal("26"), Decimal("7"))],
        date(2027, 11, 25),
        date(2027, 11, 25),
        "bezahlt",
        quelle="system",
        gutschein_rolle="kauf",
    )
    gutschein_id, einloesung_id = uuid.uuid4(), uuid.uuid4()
    r = rechnungen._neue_rechnung(
        db,
        kunde,
        "storno",
        [rechnungen.Posten(None, "Teil", Decimal("-1"), Decimal("7"))],
        date(2027, 11, 25),
        date(2027, 11, 25),
        "bezahlt",
        quelle="admin",
        gutschein_rolle="einloesung",
        korrigiert_rechnung_id=ursprung.id,
        admin_user_id=user.id,
        gutschein_ids=[gutschein_id],
        einloesung_ids=[einloesung_id],
    )
    db.flush()
    eintrag = db.scalars(select(Audit).where(Audit.objekt_id == r.id)).one()
    assert eintrag.vorher_json is None
    assert eintrag.admin_user_id == user.id
    assert eintrag.nachher_json["gutschein_rolle"] == "einloesung"
    assert eintrag.nachher_json["korrigiert_rechnung_id"] == str(ursprung.id)
    assert eintrag.nachher_json["gutschein_ids"] == [str(gutschein_id)]
    assert eintrag.nachher_json["einloesung_ids"] == [str(einloesung_id)]


def test_einzelrechnung_mit_ust(db: Session, welt) -> None:
    f, a, _ = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    assert (
        r.status == "bezahlt"
        and r.brutto == Decimal("30.00")
        and r.netto == Decimal("25.21")
        and r.ust == Decimal("4.79")
    )
    assert r.adresse_snapshot["name"] == "A" and b.rechnung_position_id == r.positionen[0].id
    with pytest.raises(rechnungen.RechnungsFehler, match="bereits_berechnet"):
        rechnungen.erzeuge_einzelrechnung(db, b)


def test_stornorechnung_stellt_einzelrechnung_neu_aus(db: Session, welt) -> None:
    f, _, v1 = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=v1.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b, status="offen")
    db.commit()
    erg = rechnungen.storniere(db, r, admin_user_id=None, grund="Falscher Preis")
    db.commit()
    s = erg.beleg
    assert s.art == "storno" and s.brutto == Decimal("-30.00") and s.storniert_durch_id is None
    assert s.korrigiert_rechnung_id == r.id
    assert r.status == "storniert" and r.storniert_durch_id == s.id
    (neu,) = erg.neu
    assert neu.status == "offen" and b.rechnung_position_id == neu.positionen[0].id
    assert db.query(Rechnung).count() == 3


def test_csv_export(db: Session, welt) -> None:
    f, a, _ = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    csv = rechnungen.csv_export(db, date(2027, 11, 1), date(2027, 12, 31))
    zeilen = csv.strip().splitlines()
    assert zeilen[0].startswith("nummer;datum;kunde;art;status;ust_satz;netto;ust;brutto")
    assert len(zeilen) == 2 and ";19,00;25,21;4,79;30,00;" in zeilen[1]


def test_buchung_kann_nicht_doppelt_berechnet_werden(db: Session, welt) -> None:
    f, a, _ = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    db.add(
        RechnungPosition(
            rechnung_id=r.id,
            reihenfolge=99,
            buchung_id=b.id,
            text="doppelt",
            menge=1,
            einzelpreis_brutto=Decimal("30.00"),
            ust_satz=Decimal("19.00"),
            netto=Decimal("25.21"),
            ust=Decimal("4.79"),
            brutto=Decimal("30.00"),
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_setze_bezahlt_und_nicht_offen(db: Session, welt) -> None:
    f, _, v1 = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=v1.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b, status="offen")
    db.commit()
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    db.commit()
    assert r.status == "bezahlt" and r.bezahlt_am is not None
    with pytest.raises(rechnungen.RechnungsFehler, match="nicht_offen"):
        rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    assert db.query(Audit).filter_by(objekt_typ="rechnung", objekt_id=r.id).count() > 0


def test_nach_stornorechnung_ist_buchung_genau_einmal_berechnet(db: Session, welt) -> None:
    f, a, _ = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b)
    db.commit()
    (r2,) = rechnungen.storniere(db, r, admin_user_id=None, grund="Falscher Preis").neu
    db.commit()
    assert r2.nummer != r.nummer and r2.status == "bezahlt"
    assert b.rechnung_position_id == r2.positionen[0].id
    with pytest.raises(rechnungen.RechnungsFehler, match="bereits_berechnet"):
        rechnungen.erzeuge_einzelrechnung(db, b)
    assert db.query(Rechnung).count() == 3


def _zwei_saetze(db: Session, welt) -> Rechnung:
    """Saisonrechnung mit einem Termin als Mitglied (7 %) und einem danach (19 %), A-RECH-8."""
    f, _, v1 = welt
    konfiguration.setze(db, "abo_nur_mitglieder", "nein")
    v1.mitglied_bis = date(2027, 12, 5)
    db.commit()
    dauerbuchungen.lege_an(
        db,
        kunde_id=v1.id,
        feld_id=f.id,
        wochentag=2,
        start=time(19),
        ende=time(20),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 8),
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
    )
    db.commit()
    return db.scalars(select(Rechnung).where(Rechnung.art == "saison")).one()


def test_rechnung_mit_zwei_saetzen_summiert_je_satz(db: Session, welt) -> None:
    r = _zwei_saetze(db, welt)
    assert [(p.ust_satz, p.netto, p.ust) for p in r.positionen] == [
        (Decimal("7.00"), Decimal("28.04"), Decimal("1.96")),
        (Decimal("19.00"), Decimal("25.21"), Decimal("4.79")),
    ]
    assert (r.netto, r.ust, r.brutto) == (Decimal("53.25"), Decimal("6.75"), Decimal("60.00"))
    assert rechnungen.steuer_je_satz(r) == [
        rechnungen.SteuerZeile(
            Decimal("7.00"), Decimal("28.04"), Decimal("1.96"), Decimal("30.00")
        ),
        rechnungen.SteuerZeile(
            Decimal("19.00"), Decimal("25.21"), Decimal("4.79"), Decimal("30.00")
        ),
    ]


def test_rundung_je_position(db: Session, welt) -> None:
    """Drei Positionen zu 10 € bei 19 %: je Position 8,40 € netto, zusammen 25,20 € – nicht
    25,21 €, wie es die Rundung der Bruttosumme ergäbe (A-RECH-8)."""
    f, _, v1 = welt
    konfiguration.setze(db, "abo_nur_mitglieder", "nein")
    db.query(Tarif).update({Tarif.preis: Decimal("10.00")})
    db.commit()
    dauerbuchungen.lege_an(
        db,
        kunde_id=v1.id,
        feld_id=f.id,
        wochentag=2,
        start=time(19),
        ende=time(20),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 15),
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
    )
    db.commit()
    r = db.scalars(select(Rechnung).where(Rechnung.art == "saison")).one()
    assert [p.netto for p in r.positionen] == [Decimal("8.40")] * 3
    assert (r.netto, r.ust, r.brutto) == (Decimal("25.20"), Decimal("4.80"), Decimal("30.00"))


def test_storno_negiert_je_satz(db: Session, welt) -> None:
    r = _zwei_saetze(db, welt)
    s = rechnungen.storniere(db, r, admin_user_id=None, grund="Test").beleg
    db.commit()
    assert [(z.ust_satz, z.netto, z.ust) for z in rechnungen.steuer_je_satz(s)] == [
        (Decimal("7.00"), Decimal("-28.04"), Decimal("-1.96")),
        (Decimal("19.00"), Decimal("-25.21"), Decimal("-4.79")),
    ]
    assert s.brutto == Decimal("-60.00")


def test_pdf_zeigt_beide_saetze(db: Session, welt) -> None:
    html = rechnung_pdf.html(_zwei_saetze(db, welt))
    assert "Nettobetrag 7 %" in html and "zzgl. 19 % USt" in html
    assert "28,04 €" in html and "4,79 €" in html and "60,00 €" in html
