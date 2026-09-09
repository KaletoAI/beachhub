import uuid
from datetime import date, datetime, timedelta
from typing import Any

from beachhub_shared.zeit import BERLIN, kombiniere
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core import auth, clock
from beachhub_core.database import get_db
from beachhub_core.models import (
    AdminUser,
    Buchung,
    Dauerbuchung,
    Feld,
    Kunde,
    RechnungPosition,
    Sperre,
)
from beachhub_core.routes._form import fehlertext, pflicht, t_datum, t_zeit
from beachhub_core.services import (
    belegung,
    benachrichtigung,
    buchungen,
    dauerbuchungen,
    pin,
    rechnung_pdf,
    rechnungen,
    slots_db,
    sperren,
    storno,
)
from beachhub_core.services.buchungen import BuchungsFehler
from beachhub_core.services.dauerbuchungen import DauerbuchungsFehler
from beachhub_core.services.rechnungen import RechnungsFehler
from beachhub_core.services.sperren import SperrenFehler
from beachhub_core.services.storno import StornoFehler
from beachhub_core.templating import mit_flash, render

router = APIRouter()

GRUND = {
    "belegt": "Zeitraum ist belegt",
    "ausserhalb_betriebszeit": "Zeitraum passt nicht zu Raster oder Betriebszeit",
    "ausserhalb_fenster": "Außerhalb des Buchungsfensters",
    "kein_tarif": "Kein Tarif hinterlegt",
    "kunde_unbekannt": "Kunde unbekannt",
    "feld_inaktiv": "Feld inaktiv",
    "vergangenheit": "Zeitpunkt liegt in der Vergangenheit",
    "zu_spaet": "Buchung hat bereits begonnen",
    "nicht_aktiv": "Buchung ist nicht aktiv",
    "entscheidung_fehlt": "Bitte für jede betroffene Buchung entscheiden",
    "sperre": "Termin kollidiert mit einer Sperre",
    "keine_termine": "Keine Termine im Zeitraum",
    "zeitraum_ungueltig": "Zeitraum ungültig",
    "ueberlappt": "Überlappt mit einer bestehenden Sperre",
    "bereits_berechnet": "Buchung wurde bereits berechnet",
    "pdf_vorhanden": "Rechnungs-PDF existiert bereits",
    "nicht_offen": "Rechnung ist nicht offen",
}

# Alle vom Admin-Formular her erwartbaren Fehler: Domänenvalidierung der beteiligten Services,
# fehlerhafte/leere Formularwerte (ValueError aus routes._form bzw. uuid.UUID/date.fromisoformat),
# fehlende Formularfelder (KeyError bei form[...]-Zugriffen) sowie DB-Constraint-Verletzungen
# (IntegrityError, z. B. überlappende Zeiträume).
FORM_FEHLER = (
    BuchungsFehler,
    SperrenFehler,
    DauerbuchungsFehler,
    StornoFehler,
    RechnungsFehler,
    ValueError,
    KeyError,
    IntegrityError,
)


def _lokal(v: str) -> datetime:
    """'JJJJ-MM-TTTHH:MM' (lokal) → UTC-aware."""
    naiv = datetime.fromisoformat(v)
    return kombiniere(naiv.date(), naiv.time())


def _woche_url(feld_id: uuid.UUID, tag: datetime) -> str:
    lokal = tag.astimezone(BERLIN).date()
    montag = lokal - timedelta(days=lokal.weekday())
    return f"/admin/belegung?feld={feld_id}&woche={montag.isoformat()}"


def _iso_lokal(dt: datetime) -> str:
    """UTC-aware datetime → 'JJJJ-MM-TTTHH:MM' lokal (Gegenstück zu `_lokal`)."""
    return dt.astimezone(BERLIN).strftime("%Y-%m-%dT%H:%M")


def _aktive_felder(db: Session) -> list[Feld]:
    return list(
        db.scalars(select(Feld).where(Feld.aktiv.is_(True)).order_by(Feld.reihenfolge)).all()
    )


def _kunden_liste(db: Session) -> list[Kunde]:
    return list(
        db.scalars(select(Kunde).where(Kunde.anonymisiert_am.is_(None)).order_by(Kunde.name)).all()
    )


# ---- Woche ----
@router.get("/belegung", response_class=HTMLResponse)
def woche(
    request: Request,
    feld: str = "",
    woche: str = "",
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    felder = _aktive_felder(db)
    if not felder:
        return render(
            request, "belegung/woche.html", admin=admin, felder=[], feld=None, tage=[], montag=None
        )
    f = felder[0]
    if feld:
        try:
            gefunden = db.get(Feld, uuid.UUID(feld))
        except ValueError:
            gefunden = None
        if gefunden is not None:
            f = gefunden
    heute = clock.today(db)
    montag = t_datum(woche) or heute
    montag -= timedelta(days=montag.weekday())
    return render(
        request,
        "belegung/woche.html",
        admin=admin,
        felder=felder,
        feld=f,
        montag=montag,
        tage=belegung.wochenplan(db, f, montag),
        vorher=montag - timedelta(days=7),
        nachher=montag + timedelta(days=7),
        iso_lokal=_iso_lokal,
    )


# ---- Buchungen ----
@router.get("/belegung/buchung/neu", response_class=HTMLResponse, response_model=None)
def buchung_neu(
    request: Request,
    feld: str,
    beginn: str,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    try:
        f = db.get(Feld, uuid.UUID(feld))
    except ValueError:
        f = None
    if f is None:
        return mit_flash(
            RedirectResponse("/admin/belegung", status_code=303), "Feld nicht gefunden", "fehler"
        )
    start = _lokal(beginn)
    tages = slots_db.tages_slots(db, f, start.astimezone(BERLIN).date()) if f else []
    enden = []
    for s in tages:
        if s.beginn >= start and (not enden or s.beginn == enden[-1]):
            enden.append(s.ende)
    return render(
        request,
        "belegung/buchung_neu.html",
        admin=admin,
        feld=f,
        beginn=start,
        beginn_roh=beginn,
        enden=enden,
        kunden=_kunden_liste(db),
        iso_lokal=_iso_lokal,
    )


@router.post("/belegung/buchung", response_model=None)
def buchung_anlegen(
    request: Request,
    feld_id: str = Form(...),
    kunde_id: str = Form(...),
    beginn: str = Form(...),
    ende: str = Form(...),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    r = None
    try:
        b = buchungen.lege_an(
            db,
            feld_id=uuid.UUID(feld_id),
            kunde_id=uuid.UUID(kunde_id),
            beginn=_lokal(beginn),
            ende=_lokal(ende),
            quelle="admin",
            admin_user_id=admin.id,
        )
        if b.zahlungsart == "online":
            r = rechnungen.erzeuge_einzelrechnung(db, b)
            rechnung_pdf.erzeuge(db, r)
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return mit_flash(
            RedirectResponse(
                f"/admin/belegung/buchung/neu?feld={feld_id}&beginn={beginn}", status_code=303
            ),
            fehlertext(e, GRUND),
            "fehler",
        )
    benachrichtigung.buchung_bestaetigt(db, b)
    if r is not None:
        benachrichtigung.rechnung(db, r)
    return mit_flash(
        RedirectResponse(_woche_url(b.feld_id, b.beginn), status_code=303), "Buchung angelegt"
    )


def _buchung_ctx(db: Session, b: Buchung) -> dict[str, Any]:
    position = db.get(RechnungPosition, b.rechnung_position_id) if b.rechnung_position_id else None
    return {
        "b": b,
        "pin": pin.entschluessele(b.pin_verschluesselt) if b.pin_verschluesselt else None,
        "position": position,
        "zurueck": _woche_url(b.feld_id, b.beginn),
    }


@router.get("/belegung/buchung/{buchung_id}", response_class=HTMLResponse)
def buchung_detail(
    request: Request,
    buchung_id: uuid.UUID,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    b = db.get(Buchung, buchung_id)
    if b is None:
        return mit_flash(
            RedirectResponse("/admin/belegung", status_code=303), "Buchung nicht gefunden", "fehler"
        )  # type: ignore[return-value]
    return render(request, "belegung/buchung.html", admin=admin, **_buchung_ctx(db, b))


@router.post("/belegung/buchung/{buchung_id}/storno", response_model=None)
def buchung_storno(
    request: Request,
    buchung_id: uuid.UUID,
    grund: str = Form(""),
    kostenfrei: str = Form("auto"),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    b = db.get(Buchung, buchung_id)
    if b is None:
        return mit_flash(
            RedirectResponse("/admin/belegung", status_code=303), "Buchung nicht gefunden", "fehler"
        )
    try:
        s = storno.storniere(
            db,
            b,
            durch="betreiber",
            admin_user_id=admin.id,
            grund=grund,
            kostenfrei={"ja": True, "nein": False}.get(kostenfrei),
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request,
            "belegung/buchung.html",
            admin=admin,
            fehler=fehlertext(e, GRUND),
            **_buchung_ctx(db, b),
        )
    benachrichtigung.storno(db, s)
    return mit_flash(
        RedirectResponse(f"/admin/belegung/buchung/{b.id}", status_code=303), "Storniert"
    )


@router.post("/belegung/buchung/{buchung_id}/kulanz", response_model=None)
def buchung_kulanz(
    request: Request,
    buchung_id: uuid.UUID,
    grund: str = Form(...),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    b = db.get(Buchung, buchung_id)
    if b is None:
        return mit_flash(
            RedirectResponse("/admin/belegung", status_code=303), "Buchung nicht gefunden", "fehler"
        )
    if b.storno is None:
        return render(
            request,
            "belegung/buchung.html",
            admin=admin,
            fehler="Kein Storno vorhanden",
            **_buchung_ctx(db, b),
        )
    try:
        storno.kulanz(db, b.storno, admin_user_id=admin.id, grund=grund)
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request,
            "belegung/buchung.html",
            admin=admin,
            fehler=fehlertext(e, GRUND),
            **_buchung_ctx(db, b),
        )
    return mit_flash(
        RedirectResponse(f"/admin/belegung/buchung/{b.id}", status_code=303), "Kulanz gebucht"
    )


# ---- Sperren ----
@router.get("/belegung/sperre/neu", response_class=HTMLResponse)
def sperre_neu(
    request: Request,
    beginn: str = "",
    ende: str = "",
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(
        request,
        "belegung/sperre_neu.html",
        admin=admin,
        felder=_aktive_felder(db),
        betroffen=[],
        werte={"beginn": beginn, "ende": ende},
    )


@router.post("/belegung/sperre", response_model=None)
async def sperre_anlegen(
    request: Request,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    feld_ids: list[uuid.UUID] | None = None
    beginn = ende = None
    try:
        alle = str(form.get("alle_felder", "")) == "1"
        feld_ids = None if alle else [uuid.UUID(str(v)) for v in form.getlist("feld_ids")]
        beginn, ende = _lokal(str(form["beginn"])), _lokal(str(form["ende"]))
        grund = str(form.get("grund", "")).strip()
        entscheidungen = {
            uuid.UUID(k.removeprefix("entscheidung_")): str(v)
            for k, v in form.items()
            if k.startswith("entscheidung_")
        }
        sperren.lege_an(
            db,
            feld_ids=feld_ids,
            beginn=beginn,
            ende=ende,
            grund=grund,
            admin_user_id=admin.id,
            entscheidungen=entscheidungen,
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        betroffen = (
            sperren.betroffene_buchungen(db, feld_ids=feld_ids, beginn=beginn, ende=ende)
            if beginn is not None and ende is not None
            else []
        )
        return render(
            request,
            "belegung/sperre_neu.html",
            admin=admin,
            felder=_aktive_felder(db),
            betroffen=betroffen,
            werte={**dict(form), "feld_ids": form.getlist("feld_ids")},
            fehler=fehlertext(e, GRUND),
        )
    ziel_feld = (
        feld_ids[0] if feld_ids else db.scalars(select(Feld.id).order_by(Feld.reihenfolge)).first()
    )
    if ziel_feld is None:
        return mit_flash(RedirectResponse("/admin/belegung", status_code=303), "Sperre angelegt")
    return mit_flash(
        RedirectResponse(_woche_url(ziel_feld, beginn), status_code=303), "Sperre angelegt"
    )


@router.post("/belegung/sperre/{sperre_id}/loeschen", response_model=None)
def sperre_loeschen(
    sperre_id: uuid.UUID,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    s = db.get(Sperre, sperre_id)
    if s is not None:
        try:
            sperren.loesche(db, s, admin_user_id=admin.id)
            db.commit()
        except FORM_FEHLER as e:
            db.rollback()
            return mit_flash(
                RedirectResponse("/admin/belegung", status_code=303), fehlertext(e, GRUND), "fehler"
            )
    return mit_flash(RedirectResponse("/admin/belegung", status_code=303), "Sperre gelöscht")


# ---- Dauerbuchungen ----
def _dauer_args(form: Any) -> dict[str, Any]:
    """Wandelt die rohen Formularwerte in die Argumente von `dauerbuchungen.plane`/`lege_an`.

    Fehlende Pflichtfelder (leer oder ganz abwesend) werden über `pflicht` in einen
    lesbaren ValueError verwandelt, statt als KeyError durchzuschlagen.
    """
    kunde_id = pflicht(form.get("kunde_id") or None, "Kunde")
    feld_id = pflicht(form.get("feld_id") or None, "Feld")
    wochentag = pflicht(form.get("wochentag") or None, "Wochentag")
    start = pflicht(form.get("start") or None, "Startzeit")
    ende = pflicht(form.get("ende") or None, "Endzeit")
    gueltig_von = pflicht(form.get("gueltig_von") or None, "Gültig von")
    gueltig_bis = pflicht(form.get("gueltig_bis") or None, "Gültig bis")
    return dict(
        kunde_id=uuid.UUID(str(kunde_id)),
        feld_id=uuid.UUID(str(feld_id)),
        wochentag=int(str(wochentag)),
        start=t_zeit(str(start)),
        ende=t_zeit(str(ende)),
        gueltig_von=t_datum(str(gueltig_von)),
        gueltig_bis=t_datum(str(gueltig_bis)),
    )


def _dauer_formular_ctx(
    db: Session, *, termine: Any, werte: dict[str, Any], fehler: str | None = None
) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "termine": termine,
        "werte": werte,
        "kunden": _kunden_liste(db),
        "felder": _aktive_felder(db),
    }
    if fehler is not None:
        ctx["fehler"] = fehler
    return ctx


@router.get("/belegung/dauer/neu", response_class=HTMLResponse)
def dauer_neu(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(
        request,
        "belegung/dauer_neu.html",
        admin=admin,
        **_dauer_formular_ctx(db, termine=None, werte={}),
    )


@router.post("/belegung/dauer/planen", response_class=HTMLResponse)
async def dauer_planen(
    request: Request,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    form = await request.form()
    try:
        args = _dauer_args(form)
    except FORM_FEHLER as e:
        return render(
            request,
            "belegung/dauer_neu.html",
            admin=admin,
            **_dauer_formular_ctx(db, termine=None, werte=dict(form), fehler=fehlertext(e, GRUND)),
        )
    try:
        termine = dauerbuchungen.plane(db, **args)
    except FORM_FEHLER as e:
        return render(
            request,
            "belegung/dauer_neu.html",
            admin=admin,
            **_dauer_formular_ctx(db, termine=None, werte=dict(form), fehler=fehlertext(e, GRUND)),
        )
    return render(
        request,
        "belegung/dauer_neu.html",
        admin=admin,
        **_dauer_formular_ctx(db, termine=termine, werte=dict(form)),
    )


@router.post("/belegung/dauer", response_model=None)
async def dauer_anlegen(
    request: Request,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    try:
        args = _dauer_args(form)
    except FORM_FEHLER as e:
        return render(
            request,
            "belegung/dauer_neu.html",
            admin=admin,
            **_dauer_formular_ctx(db, termine=None, werte=dict(form), fehler=fehlertext(e, GRUND)),
        )
    try:
        auslassen = {
            date.fromisoformat(k.removeprefix("auslassen_"))
            for k in form
            if k.startswith("auslassen_")
        }
        entscheidungen = {
            uuid.UUID(k.removeprefix("entscheidung_")): str(v)
            for k, v in form.items()
            if k.startswith("entscheidung_")
        }
        d = dauerbuchungen.lege_an(
            db, **args, admin_user_id=admin.id, auslassen=auslassen, entscheidungen=entscheidungen
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request,
            "belegung/dauer_neu.html",
            admin=admin,
            **_dauer_formular_ctx(
                db,
                termine=dauerbuchungen.plane(db, **args),
                werte=dict(form),
                fehler=fehlertext(e, GRUND),
            ),
        )
    benachrichtigung.dauerbuchung_angelegt(db, d)
    return mit_flash(
        RedirectResponse(f"/admin/belegung/dauer/{d.id}", status_code=303), "Dauerbuchung angelegt"
    )


@router.get("/belegung/dauer/{dauer_id}", response_class=HTMLResponse)
def dauer_detail(
    request: Request,
    dauer_id: uuid.UUID,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    d = db.get(Dauerbuchung, dauer_id)
    if d is None:
        return mit_flash(
            RedirectResponse("/admin/belegung", status_code=303), "Nicht gefunden", "fehler"
        )  # type: ignore[return-value]
    return render(
        request,
        "belegung/dauer.html",
        admin=admin,
        d=d,
        pin=pin.entschluessele(d.pin_verschluesselt),
    )


@router.post("/belegung/dauer/{dauer_id}/beenden", response_model=None)
def dauer_beenden(
    dauer_id: uuid.UUID,
    ab: str = Form(...),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    d = db.get(Dauerbuchung, dauer_id)
    if d is None:
        return mit_flash(
            RedirectResponse("/admin/belegung", status_code=303), "Nicht gefunden", "fehler"
        )
    try:
        dauerbuchungen.beende(db, d, ab=date.fromisoformat(ab), admin_user_id=admin.id)
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return mit_flash(
            RedirectResponse(f"/admin/belegung/dauer/{d.id}", status_code=303),
            fehlertext(e, GRUND),
            "fehler",
        )
    return mit_flash(
        RedirectResponse(f"/admin/belegung/dauer/{d.id}", status_code=303), "Dauerbuchung beendet"
    )
