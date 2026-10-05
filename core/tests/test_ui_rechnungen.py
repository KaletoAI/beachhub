import uuid
from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock, mail
from beachhub_core.models import Betriebszeit, Buchung, Feld, FeldRaster, Kunde, Rechnung, Tarif
from beachhub_core.services import buchungen, kunden, rechnungen
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_review_8_rechnungsliste_aggregiert_unabhaengig_von_zeilenzahl(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    from beachhub_core.database import engine
    from beachhub_core.services import guthaben, storno
    from sqlalchemy import event

    _, k = welt
    tag = date(2027, 12, 1)

    def anlegen():
        return rechnungen._neue_rechnung(
            db,
            k,
            "einzel",
            [
                rechnungen.Posten(None, "A", Decimal("30"), Decimal("19")),
                rechnungen.Posten(None, "B", Decimal("30"), Decimal("19")),
            ],
            tag,
            tag,
            "offen",
            quelle="admin",
        )

    r = anlegen()
    anlegen()
    guthaben.buche(db, kunde=k, betrag=Decimal("20"), art="manuell")
    rechnungen.verrechne_guthaben(db, r, quelle="admin")
    storno.gutschreiben_positionen(db, [r.positionen[0]], grund="Ausfall", quelle="admin")
    db.commit()

    def messen() -> tuple[int, str]:
        selects = []

        def zaehlen(conn, cursor, statement, parameters, context, executemany):
            sql = statement.lower()
            if sql.lstrip().startswith("select") and (
                "from rechnung" in sql or "from zahlung" in sql
            ):
                selects.append(sql)

        event.listen(engine, "before_cursor_execute", zaehlen)
        try:
            db.expire_all()
            seite = eingeloggt.get("/admin/rechnungen?status=offen")
            assert seite.status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", zaehlen)
        return len(selects), seite.text

    klein, html = messen()
    assert "10,00 €" in html and "60,00 €" in html
    for _ in range(18):
        anlegen()
    db.commit()
    gross, html = messen()
    assert "10,00 €" in html and "60,00 €" in html
    assert gross == klein, f"Rechnungs-SELECTs wachsen von {klein} auf {gross}"


@pytest.fixture
def welt(db: Session) -> tuple[Feld, Kunde]:
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    v1 = kunden.lege_an(db, name="TSV", email="v@x.de", rechnungskunde=True)
    v1.mitglied_bis = date(2028, 4, 30)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, v1


def _abo(c: TestClient, f: Feld, v1: Kunde) -> None:
    r = c.post(
        "/admin/belegung/dauer",
        data={
            "csrf_token": c.csrf,
            "kunde_id": str(v1.id),
            "feld_id": str(f.id),
            "wochentag": "2",
            "start": "19:00",
            "ende": "20:00",
            "gueltig_von": "2027-12-01",
            "gueltig_bis": "2027-12-08",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303


def test_saisonrechnung_liste_pdf_bezahlt_storno_export(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    c = eingeloggt
    _abo(c, *welt)
    rechnung = db.query(Rechnung).one()
    assert rechnung.art == "saison" and rechnung.pdf_sha256
    assert any(m["betreff"] == f"Rechnung {rechnung.nummer}" for m in mail.TEST_AUSGANG)
    seite = c.get("/admin/rechnungen?status=offen")
    assert rechnung.nummer in seite.text and "TSV" in seite.text
    pdf = c.get(f"/admin/rechnungen/{rechnung.id}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    r = c.post(
        f"/admin/rechnungen/{rechnung.id}/bezahlt",
        data={"csrf_token": c.csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(rechnung)
    assert rechnung.status == "bezahlt"
    r = c.post(
        f"/admin/rechnungen/{rechnung.id}/storno",
        data={"csrf_token": c.csrf, "grund": "Fehler"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    assert db.query(Rechnung).count() == 2 and db.get(Rechnung, rechnung.id).status == "storniert"
    csv = c.get("/admin/rechnungen/export.csv?von=2027-11-01&bis=2027-11-30")
    assert (
        csv.status_code == 200
        and csv.headers["content-type"].startswith("text/csv")
        and len(csv.text.strip().splitlines()) == 3
    )


def test_rechnungen_detail_zeigt_positionen_und_integritaet(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    c = eingeloggt
    _abo(c, *welt)
    rechnung = db.query(Rechnung).one()
    seite = c.get(f"/admin/rechnungen/{rechnung.id}")
    assert seite.status_code == 200
    assert rechnung.nummer in seite.text and "TSV" in seite.text and "F1" in seite.text
    assert "PDF unverändert" in seite.text and "Offener Betrag" in seite.text


def test_pdf_get_ohne_pdf_redirect_und_post_erzeugt(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    c = eingeloggt
    f, v1 = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=v1.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b, status="offen")
    db.commit()
    ohne_pdf = c.get(f"/admin/rechnungen/{r.id}/pdf", follow_redirects=False)
    assert ohne_pdf.status_code == 303
    seite = c.get(ohne_pdf.headers["location"])
    assert "PDF noch nicht erzeugt" in seite.text
    erzeugen = c.post(
        f"/admin/rechnungen/{r.id}/pdf", data={"csrf_token": c.csrf}, follow_redirects=False
    )
    assert erzeugen.status_code == 303
    db.refresh(r)
    assert r.pdf_pfad
    pdf = c.get(f"/admin/rechnungen/{r.id}/pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"


def test_kein_monatslauf_mehr(eingeloggt: TestClient) -> None:
    c = eingeloggt
    assert "Monatslauf" not in c.get("/admin/rechnungen").text
    r = c.post(
        "/admin/rechnungen/monatslauf",
        data={"csrf_token": c.csrf, "jahr": "2027", "monat": "12"},
    )
    assert r.status_code == 405


@pytest.mark.parametrize("bezahlt", [False, True])
def test_teilstorno_ueber_ausgewaehlte_positionen(
    eingeloggt: TestClient, db: Session, welt, bezahlt: bool
) -> None:
    c = eingeloggt
    _abo(c, *welt)
    r = db.query(Rechnung).one()
    if bezahlt:
        rechnungen.setze_bezahlt(db, r, admin_user_id=None)
        db.commit()
    erste, zweite = r.positionen
    erste_id, zweite_id, rechnung_id = erste.id, zweite.id, r.id
    buchung_id, position_betrag = erste.buchung_id, erste.brutto
    original_pdf = r.pdf_sha256
    mail.TEST_AUSGANG.clear()
    seite = c.get(f"/admin/rechnungen/{rechnung_id}")
    assert f'name="positionen" value="{erste_id}"' in seite.text
    antwort = c.post(
        f"/admin/rechnungen/{rechnung_id}/teilstorno",
        data={"csrf_token": c.csrf, "grund": "Halle gesperrt", "positionen": [str(erste_id)]},
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    db.expire_all()
    beleg = db.scalars(select(Rechnung).where(Rechnung.korrigiert_rechnung_id == rechnung_id)).one()
    assert antwort.headers["location"] == f"/admin/rechnungen/{beleg.id}"
    assert beleg.brutto == -position_betrag and len(beleg.positionen) == 1
    assert beleg.pdf_sha256
    assert db.get(Kunde, welt[1].id).guthaben == (position_betrag if bezahlt else Decimal("0.00"))
    assert rechnungen.offener_betrag(db, db.get(Rechnung, rechnung_id)) == (
        Decimal("0.00") if bezahlt else position_betrag
    )
    assert db.get(Rechnung, rechnung_id).pdf_sha256 == original_pdf
    assert db.get(Buchung, buchung_id).status == Buchung.BESTAETIGT
    assert db.get(Buchung, buchung_id).rechnung_position_id == erste_id
    assert [m["betreff"] for m in mail.TEST_AUSGANG] == [f"Stornorechnung {beleg.nummer}"]
    seite = c.get(f"/admin/rechnungen/{rechnung_id}")
    assert f'name="positionen" value="{erste_id}"' not in seite.text
    assert f'name="positionen" value="{zweite_id}"' in seite.text
    # Direkter Wiederholungs-POST bleibt ohne weiteren Beleg oder Guthaben.
    seite = c.post(
        f"/admin/rechnungen/{rechnung_id}/teilstorno",
        data={"csrf_token": c.csrf, "positionen": str(erste_id)},
    )
    assert "bereits korrigiert" in seite.text
    db.expire_all()
    assert db.query(Rechnung).count() == 2
    assert db.get(Kunde, welt[1].id).guthaben == (position_betrag if bezahlt else Decimal("0.00"))
    assert len(mail.TEST_AUSGANG) == 1


@pytest.mark.parametrize("auswahl", [[], ["falsch"], ["fremd"], ["doppelt"]])
def test_teilstorno_ungueltige_auswahl_meldet_fehler(
    eingeloggt: TestClient, db: Session, welt, auswahl: list[str]
) -> None:
    c = eingeloggt
    _abo(c, *welt)
    r = db.query(Rechnung).one()
    positionen = auswahl
    if auswahl == ["fremd"]:
        positionen = [str(uuid.uuid4())]
    elif auswahl == ["doppelt"]:
        positionen = [str(r.positionen[0].id)] * 2
    mail.TEST_AUSGANG.clear()
    seite = c.post(
        f"/admin/rechnungen/{r.id}/teilstorno",
        data={"csrf_token": c.csrf, "grund": "x", "positionen": positionen},
    )
    erwartet = (
        "mindestens eine Position"
        if not auswahl
        else "Ungültige Auswahl"
        if auswahl == ["falsch"]
        else "gehören nicht zu dieser Rechnung"
    )
    assert erwartet in seite.text
    db.expire_all()
    assert db.query(Rechnung).count() == 1
    assert db.get(Kunde, welt[1].id).guthaben == Decimal("0.00")
    assert not mail.TEST_AUSGANG


@pytest.mark.parametrize("historisch", [False, True])
def test_teilstorno_auf_vollstornierter_rechnung_bleibt_ohne_zweite_gutschrift(
    eingeloggt: TestClient, db: Session, welt, historisch: bool
) -> None:
    c = eingeloggt
    _abo(c, *welt)
    r = db.query(Rechnung).one()
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    rechnungen.storniere(db, r, admin_user_id=None, grund="Fehler")
    if historisch:
        # Altbestand aus 0013: Vollstorno verknüpft, Positionsmarker fehlen.
        for p in r.positionen:
            p.korrigiert_durch_id = None
    rechnung_id, position_id = r.id, r.positionen[0].id
    db.commit()
    mail.TEST_AUSGANG.clear()
    assert 'name="positionen"' not in c.get(f"/admin/rechnungen/{rechnung_id}").text
    seite = c.post(
        f"/admin/rechnungen/{rechnung_id}/teilstorno",
        data={"csrf_token": c.csrf, "grund": "nochmals", "positionen": str(position_id)},
    )
    assert "bereits storniert" in seite.text
    db.expire_all()
    assert db.query(Rechnung).count() == 2
    assert db.get(Kunde, welt[1].id).guthaben == Decimal("60.00")
    assert not mail.TEST_AUSGANG


def test_liste_zeigt_offenen_betrag(eingeloggt: TestClient, db: Session, welt) -> None:
    c = eingeloggt
    _abo(c, *welt)
    seite = c.get("/admin/rechnungen")
    assert '<th class="rechts">Offen</th>' in seite.text
    assert seite.text.count("60,00 €") == 2
    r = db.query(Rechnung).one()
    c.post(
        f"/admin/rechnungen/{r.id}/teilstorno",
        data={"csrf_token": c.csrf, "positionen": str(r.positionen[0].id)},
    )
    seite = c.get("/admin/rechnungen?status=offen")
    assert "60,00 €" in seite.text and "30,00 €" in seite.text
