import uuid
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core import auth
from beachhub_core.database import get_db
from beachhub_core.models import (
    AdminUser,
    Ausnahmetag,
    Betriebszeit,
    Feld,
    FeldRaster,
    Kundengruppe,
    Tarif,
)
from beachhub_core.routes._form import (
    fehlertext,
    pflicht,
    t_betrag,
    t_datum,
    t_fenster,
    t_int,
    t_uuid,
    t_zeit,
)
from beachhub_core.services import konfiguration, stammdaten
from beachhub_core.services.stammdaten import StammdatenFehler
from beachhub_core.templating import mit_flash, render

router = APIRouter()

# Alle vom Admin-Formular her erwartbaren Fehler: Domänenvalidierung (StammdatenFehler),
# fehlerhafte/​leere Formularwerte (ValueError aus routes._form), ungültige Decimal-Literale
# (InvalidOperation, wird von t_betrag zwar schon in ValueError gewandelt, hier zur Sicherheit
# trotzdem mitgefangen) sowie DB-Constraint-Verletzungen (IntegrityError, z. B. doppelter Name).
FORM_FEHLER = (StammdatenFehler, ValueError, InvalidOperation, IntegrityError)


def _redirect(url: str, text: str, art: str = "ok") -> RedirectResponse:
    return mit_flash(RedirectResponse(url, status_code=303), text, art)


# ---- Felder ----
@router.get("/felder", response_class=HTMLResponse)
def felder(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    liste = db.scalars(select(Feld).order_by(Feld.reihenfolge)).all()
    return render(request, "stammdaten/felder.html", admin=admin, felder=liste)


@router.post("/felder", response_model=None)
def feld_anlegen(
    request: Request,
    name: str = Form(...),
    reihenfolge: str = Form("0"),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    try:
        f = stammdaten.feld_anlegen(
            db, admin_user_id=admin.id, name=name, reihenfolge=t_int(reihenfolge) or 0
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        liste = db.scalars(select(Feld).order_by(Feld.reihenfolge)).all()
        return render(
            request, "stammdaten/felder.html", admin=admin, felder=liste, fehler=fehlertext(e)
        )
    return _redirect(f"/admin/felder/{f.id}", "Feld angelegt")


@router.get("/felder/{feld_id}", response_class=HTMLResponse)
def feld(
    request: Request,
    feld_id: uuid.UUID,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    f = db.get(Feld, feld_id)
    if f is None:
        return _redirect("/admin/felder", "Feld nicht gefunden", "fehler")  # type: ignore[return-value]
    return render(request, "stammdaten/feld.html", admin=admin, feld=f)


@router.post("/felder/{feld_id}", response_model=None)
def feld_aendern(
    request: Request,
    feld_id: uuid.UUID,
    name: str = Form(...),
    reihenfolge: str = Form("0"),
    aktiv: str = Form(""),
    ha_licht_entity: str = Form(""),
    ha_praesenz_entity: str = Form(""),
    heizzone: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    f = db.get(Feld, feld_id)
    if f is None:
        return _redirect("/admin/felder", "Feld nicht gefunden", "fehler")
    try:
        stammdaten.feld_aendern(
            db,
            f,
            admin_user_id=admin.id,
            name=name.strip(),
            reihenfolge=t_int(reihenfolge) or 0,
            aktiv=aktiv == "1",
            ha_licht_entity=ha_licht_entity or None,
            ha_praesenz_entity=ha_praesenz_entity or None,
            heizzone=heizzone or None,
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(request, "stammdaten/feld.html", admin=admin, feld=f, fehler=fehlertext(e))
    return _redirect(f"/admin/felder/{f.id}", "Gespeichert")


@router.post("/felder/{feld_id}/raster", response_model=None)
def raster_setzen(
    request: Request,
    feld_id: uuid.UUID,
    wochentag: str = Form(""),
    modus: str = Form(...),
    slot_minuten: str = Form(""),
    fenster: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    f = db.get(Feld, feld_id)
    if f is None:
        return _redirect("/admin/felder", "Feld nicht gefunden", "fehler")
    try:
        stammdaten.raster_setzen(
            db,
            f,
            admin_user_id=admin.id,
            wochentag=t_int(wochentag),
            modus=modus,
            slot_minuten=t_int(slot_minuten),
            fenster=t_fenster(fenster),
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(request, "stammdaten/feld.html", admin=admin, feld=f, fehler=fehlertext(e))
    return _redirect(f"/admin/felder/{f.id}", "Raster gespeichert")


@router.post("/felder/{feld_id}/raster/{raster_id}/loeschen")
def raster_loeschen(
    feld_id: uuid.UUID,
    raster_id: uuid.UUID,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    r = db.get(FeldRaster, raster_id)
    if r is not None and r.feld_id == feld_id:
        try:
            stammdaten.raster_loeschen(db, r, admin_user_id=admin.id)
            db.commit()
        except FORM_FEHLER as e:
            db.rollback()
            return _redirect(f"/admin/felder/{feld_id}", fehlertext(e), "fehler")
    return _redirect(f"/admin/felder/{feld_id}", "Raster gelöscht")


# ---- Betriebszeiten ----
@router.get("/betriebszeiten", response_class=HTMLResponse)
def betriebszeiten(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
    fehler: str | None = None,
) -> HTMLResponse:
    liste = db.scalars(
        select(Betriebszeit).order_by(Betriebszeit.wochentag, Betriebszeit.oeffnet)
    ).all()
    return render(
        request, "stammdaten/betriebszeiten.html", admin=admin, zeiten=liste, fehler=fehler
    )


@router.post("/betriebszeiten", response_model=None)
def betriebszeit_anlegen(
    request: Request,
    wochentag: str = Form(...),
    oeffnet: str = Form(...),
    schliesst: str = Form(...),
    gueltig_von: str = Form(""),
    gueltig_bis: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    try:
        stammdaten.betriebszeit_anlegen(
            db,
            admin_user_id=admin.id,
            wochentag=pflicht(t_int(wochentag), "Wochentag"),
            oeffnet=pflicht(t_zeit(oeffnet), "Öffnungszeit"),
            schliesst=pflicht(t_zeit(schliesst), "Schließzeit"),
            gueltig_von=t_datum(gueltig_von),
            gueltig_bis=t_datum(gueltig_bis),
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return betriebszeiten(request, admin, db, fehler=fehlertext(e))
    return _redirect("/admin/betriebszeiten", "Betriebszeit angelegt")


@router.post("/betriebszeiten/{bz_id}/loeschen")
def betriebszeit_loeschen(
    bz_id: uuid.UUID,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    bz = db.get(Betriebszeit, bz_id)
    if bz:
        try:
            stammdaten.betriebszeit_loeschen(db, bz, admin_user_id=admin.id)
            db.commit()
        except FORM_FEHLER as e:
            db.rollback()
            return _redirect("/admin/betriebszeiten", fehlertext(e), "fehler")
    return _redirect("/admin/betriebszeiten", "Gelöscht")


# ---- Ausnahmetage ----
@router.get("/ausnahmetage", response_class=HTMLResponse)
def ausnahmetage(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
    fehler: str | None = None,
) -> HTMLResponse:
    liste = db.scalars(select(Ausnahmetag).order_by(Ausnahmetag.datum)).all()
    return render(request, "stammdaten/ausnahmetage.html", admin=admin, tage=liste, fehler=fehler)


@router.post("/ausnahmetage", response_model=None)
def ausnahmetag_anlegen(
    request: Request,
    datum: str = Form(...),
    geschlossen: str = Form("1"),
    oeffnet: str = Form(""),
    schliesst: str = Form(""),
    grund: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    try:
        stammdaten.ausnahmetag_anlegen(
            db,
            admin_user_id=admin.id,
            datum=pflicht(t_datum(datum), "Datum"),
            geschlossen=geschlossen == "1",
            oeffnet=t_zeit(oeffnet),
            schliesst=t_zeit(schliesst),
            grund=grund.strip(),
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return ausnahmetage(request, admin, db, fehler=fehlertext(e))
    return _redirect("/admin/ausnahmetage", "Ausnahmetag angelegt")


@router.post("/ausnahmetage/{tag_id}/loeschen")
def ausnahmetag_loeschen(
    tag_id: uuid.UUID,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    a = db.get(Ausnahmetag, tag_id)
    if a:
        try:
            stammdaten.ausnahmetag_loeschen(db, a, admin_user_id=admin.id)
            db.commit()
        except FORM_FEHLER as e:
            db.rollback()
            return _redirect("/admin/ausnahmetage", fehlertext(e), "fehler")
    return _redirect("/admin/ausnahmetage", "Gelöscht")


# ---- Kundengruppen ----
@router.get("/kundengruppen", response_class=HTMLResponse)
def kundengruppen(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
    fehler: str | None = None,
) -> HTMLResponse:
    liste = db.scalars(select(Kundengruppe).order_by(Kundengruppe.name)).all()
    return render(
        request, "stammdaten/kundengruppen.html", admin=admin, gruppen=liste, fehler=fehler
    )


@router.post("/kundengruppen", response_model=None)
def kundengruppe_anlegen(
    request: Request,
    name: str = Form(...),
    standard_zahlungsart: str = Form(...),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    try:
        stammdaten.kundengruppe_anlegen(
            db, admin_user_id=admin.id, name=name, standard_zahlungsart=standard_zahlungsart
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return kundengruppen(request, admin, db, fehler=fehlertext(e))
    return _redirect("/admin/kundengruppen", "Kundengruppe angelegt")


@router.post("/kundengruppen/{gruppe_id}", response_model=None)
def kundengruppe_aendern(
    request: Request,
    gruppe_id: uuid.UUID,
    name: str = Form(...),
    standard_zahlungsart: str = Form(...),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    g = db.get(Kundengruppe, gruppe_id)
    if g is None:
        return _redirect("/admin/kundengruppen", "Gruppe nicht gefunden", "fehler")
    try:
        stammdaten.kundengruppe_aendern(
            db,
            g,
            admin_user_id=admin.id,
            name=name.strip(),
            standard_zahlungsart=standard_zahlungsart,
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return kundengruppen(request, admin, db, fehler=fehlertext(e))
    return _redirect("/admin/kundengruppen", "Gespeichert")


# ---- Tarife ----
def _tarif_ctx(db: Session) -> dict[str, object]:
    felder = db.scalars(select(Feld).order_by(Feld.reihenfolge)).all()
    gruppen = db.scalars(select(Kundengruppe).order_by(Kundengruppe.name)).all()
    return {
        "tarife": db.scalars(select(Tarif).order_by(Tarif.aktiv.desc(), Tarif.name)).all(),
        "felder": felder,
        "gruppen": gruppen,
        "feld_namen": {f.id: f.name for f in felder},
        "gruppen_namen": {g.id: g.name for g in gruppen},
    }


@router.get("/tarife", response_class=HTMLResponse)
def tarife(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "stammdaten/tarife.html", admin=admin, **_tarif_ctx(db))


@router.post("/tarife", response_model=None)
def tarif_anlegen(
    request: Request,
    name: str = Form(...),
    preis: str = Form(...),
    feld_id: str = Form(""),
    wochentag: str = Form(""),
    uhrzeit_von: str = Form(""),
    uhrzeit_bis: str = Form(""),
    kundengruppe_id: str = Form(""),
    gueltig_von: str = Form(""),
    gueltig_bis: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    try:
        stammdaten.tarif_anlegen(
            db,
            admin_user_id=admin.id,
            name=name,
            preis=t_betrag(preis) or Decimal("0.00"),
            feld_id=t_uuid(feld_id),
            wochentag=t_int(wochentag),
            uhrzeit_von=t_zeit(uhrzeit_von),
            uhrzeit_bis=t_zeit(uhrzeit_bis),
            kundengruppe_id=t_uuid(kundengruppe_id),
            gueltig_von=t_datum(gueltig_von),
            gueltig_bis=t_datum(gueltig_bis),
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request, "stammdaten/tarife.html", admin=admin, fehler=fehlertext(e), **_tarif_ctx(db)
        )
    return _redirect("/admin/tarife", "Tarif angelegt")


@router.post("/tarife/{tarif_id}", response_model=None)
def tarif_aendern(
    request: Request,
    tarif_id: uuid.UUID,
    name: str = Form(...),
    preis: str = Form(...),
    feld_id: str = Form(""),
    wochentag: str = Form(""),
    uhrzeit_von: str = Form(""),
    uhrzeit_bis: str = Form(""),
    kundengruppe_id: str = Form(""),
    gueltig_von: str = Form(""),
    gueltig_bis: str = Form(""),
    aktiv: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    t = db.get(Tarif, tarif_id)
    if t is None:
        return _redirect("/admin/tarife", "Tarif nicht gefunden", "fehler")
    try:
        stammdaten.tarif_aendern(
            db,
            t,
            admin_user_id=admin.id,
            name=name.strip(),
            preis=t_betrag(preis) or Decimal("0.00"),
            feld_id=t_uuid(feld_id),
            wochentag=t_int(wochentag),
            uhrzeit_von=t_zeit(uhrzeit_von),
            uhrzeit_bis=t_zeit(uhrzeit_bis),
            kundengruppe_id=t_uuid(kundengruppe_id),
            gueltig_von=t_datum(gueltig_von),
            gueltig_bis=t_datum(gueltig_bis),
            aktiv=aktiv == "1",
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request, "stammdaten/tarife.html", admin=admin, fehler=fehlertext(e), **_tarif_ctx(db)
        )
    return _redirect("/admin/tarife", "Gespeichert")


@router.post("/tarife/{tarif_id}/deaktivieren")
def tarif_deaktivieren(
    tarif_id: uuid.UUID,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    t = db.get(Tarif, tarif_id)
    if t:
        try:
            stammdaten.tarif_deaktivieren(db, t, admin_user_id=admin.id)
            db.commit()
        except FORM_FEHLER as e:
            db.rollback()
            return _redirect("/admin/tarife", fehlertext(e), "fehler")
    return _redirect("/admin/tarife", "Tarif deaktiviert")


# ---- Konfiguration ----
@router.get("/konfiguration", response_class=HTMLResponse)
def konfiguration_seite(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    werte = {k: konfiguration.hole(db, k) for k in konfiguration.DEFAULTS}
    return render(
        request,
        "stammdaten/konfiguration.html",
        admin=admin,
        werte=werte,
        defaults=konfiguration.DEFAULTS,
    )


@router.post("/konfiguration", response_model=None)
async def konfiguration_speichern(
    request: Request,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    schluessel = ""
    try:
        for schluessel in konfiguration.DEFAULTS:
            wert = str(form.get(schluessel, "")).strip()
            if wert:
                konfiguration.setze(db, schluessel, wert.replace(",", "."), admin_user_id=admin.id)
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        werte = {k: konfiguration.hole(db, k) for k in konfiguration.DEFAULTS}
        return render(
            request,
            "stammdaten/konfiguration.html",
            admin=admin,
            werte=werte,
            defaults=konfiguration.DEFAULTS,
            fehler=f"{schluessel}: {fehlertext(e)}",
        )
    return _redirect("/admin/konfiguration", "Konfiguration gespeichert")
