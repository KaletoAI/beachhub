import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core import auth, clock
from beachhub_core.database import get_db
from beachhub_core.models import (
    AdminUser,
    Audit,
    Buchung,
    GuthabenBuchung,
    Kunde,
    Rechnung,
)
from beachhub_core.routes._form import fehlertext, pflicht, t_betrag, t_datum
from beachhub_core.services import (
    benachrichtigung,
    guthaben,
    konfiguration,
    kunden,
    kundengruppen,
    mitgliedschaft,
)
from beachhub_core.services.guthaben import GuthabenFehler
from beachhub_core.services.kunden import KundenFehler
from beachhub_core.services.mitgliedschaft import MitgliedschaftsFehler
from beachhub_core.templating import mit_flash, render

router = APIRouter()

FEHLERTEXT = {
    "email_vergeben": "E-Mail-Adresse ist bereits vergeben",
    "nicht_gedeckt": "Guthaben nicht gedeckt",
    "betrag_muss_negativ_sein": "Auszahlung muss negativ sein",
    "art_unbekannt": "Art unbekannt",
    "unique": "E-Mail-Adresse ist bereits vergeben",
    "bis_vergangen": "Das Datum liegt in der Vergangenheit",
    "kein_mitglied": "Der Kunde ist kein Mitglied",
    "kein_antrag": "Es liegt kein Antrag vor",
    "kunde_anonymisiert": "Der Kunde ist anonymisiert",
}

# Alle vom Admin-Formular her erwartbaren Fehler: Domänenvalidierung (KundenFehler/GuthabenFehler),
# fehlerhafte/​leere Formularwerte (ValueError aus routes._form), ungültige Decimal-Literale
# (InvalidOperation, wird von t_betrag zwar schon in ValueError gewandelt, hier zur Sicherheit
# trotzdem mitgefangen) sowie DB-Constraint-Verletzungen (IntegrityError, z. B. doppelte E-Mail
# bei einem Wettlauf zweier gleichzeitiger Anfragen).
FORM_FEHLER = (
    KundenFehler,
    GuthabenFehler,
    MitgliedschaftsFehler,
    ValueError,
    InvalidOperation,
    IntegrityError,
)


def _nicht_gefunden() -> RedirectResponse:
    return mit_flash(
        RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler"
    )


def _liste_ctx(db: Session, q: str) -> dict:  # type: ignore[type-arg]
    stmt = select(Kunde).where(Kunde.anonymisiert_am.is_(None)).order_by(Kunde.name)
    if q:
        stmt = stmt.where(or_(Kunde.name.ilike(f"%{q}%"), Kunde.email.ilike(f"%{q}%")))
    return {"kunden": db.scalars(stmt.limit(200)).all(), "q": q}


@router.get("/kunden", response_class=HTMLResponse)
def liste(
    request: Request,
    q: str = "",
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(request, "kunden/liste.html", admin=admin, **_liste_ctx(db, q))


@router.post("/kunden", response_model=None)
def anlegen(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    rechnungskunde: str = Form(""),
    adresse_strasse: str = Form(""),
    adresse_plz: str = Form(""),
    adresse_ort: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    try:
        k = kunden.lege_an(
            db,
            name=pflicht(name.strip() or None, "Name"),
            email=email,
            rechnungskunde=rechnungskunde == "1",
            adresse_strasse=adresse_strasse,
            adresse_plz=adresse_plz,
            adresse_ort=adresse_ort,
            admin_user_id=admin.id,
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request,
            "kunden/liste.html",
            admin=admin,
            fehler=fehlertext(e, FEHLERTEXT),
            **_liste_ctx(db, ""),
        )
    return mit_flash(RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), "Kunde angelegt")


@router.get("/kunden/abgleich", response_class=HTMLResponse)
def abgleich(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    heute = clock.today(db)
    return render(
        request,
        "kunden/abgleich.html",
        admin=admin,
        kunden=mitgliedschaft.pruefliste(db, heute),
        letzter=mitgliedschaft.letzter_ablauf(db, heute),
        naechster=mitgliedschaft.naechster_ablauf(db, heute),
        stichtag=konfiguration.hole(db, "mitglieder_abgleich"),
        warnung=mitgliedschaft.abgleich_warnung(db, heute),
    )


@router.get("/kunden/abgleich.csv")
def abgleich_csv(
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> Response:
    heute = clock.today(db)
    return Response(
        mitgliedschaft.pruefliste_csv(mitgliedschaft.pruefliste(db, heute)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="mitglieder_abgleich_{heute}.csv"'},
    )


@router.post("/kunden/abgleich", response_model=None)
async def abgleich_aktion(
    request: Request,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    form = await request.form()
    zurueck = RedirectResponse("/admin/kunden/abgleich", status_code=303)
    try:
        ids = [uuid.UUID(str(v)) for v in form.getlist("kunde_ids")]
        if not ids:
            raise ValueError("Bitte mindestens einen Kunden auswählen")
        aktion = str(form.get("aktion", ""))
        if aktion == "verlaengern":
            verlaengert = mitgliedschaft.verlaengere_alle(db, ids, admin_user_id=admin.id)
            mails = [(benachrichtigung.mitgliedschaft_freigeschaltet, k) for k in verlaengert]
            text = f"{len(verlaengert)} Mitgliedschaften verlängert"
        elif aktion == "beenden":
            grund = str(form.get("grund", "")).strip() or "Jahresabgleich"
            ergebnis = mitgliedschaft.beende_alle(db, ids, grund=grund, admin_user_id=admin.id)
            mails = [(benachrichtigung.mitgliedschaft_beendet, k) for k, galt in ergebnis if galt]
            text = f"{len(ergebnis)} Mitgliedschaften beendet"
        else:
            raise ValueError("Aktion unbekannt")
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return mit_flash(zurueck, fehlertext(e, FEHLERTEXT), "fehler")
    for senden, k in mails:
        senden(db, k)
    return mit_flash(zurueck, text)


@router.get("/kunden/antraege", response_class=HTMLResponse)
def antraege(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(
        request, "kunden/antraege.html", admin=admin, kunden=mitgliedschaft.offene_antraege(db)
    )


def _detail_ctx(db: Session, k: Kunde) -> dict:  # type: ignore[type-arg]
    heute = clock.today(db)
    seit = clock.now(db) - timedelta(days=365)
    return {
        "kunde": k,
        "gruppe_heute": kundengruppen.effektive_gruppe(db, k, heute),
        "mitgliedschaft": mitgliedschaft.status(k, heute),
        "vorschlag_bis": mitgliedschaft.naechster_ablauf(db, heute),
        "buchungen": db.scalars(
            select(Buchung)
            .where(Buchung.kunde_id == k.id, Buchung.beginn >= seit)
            .order_by(Buchung.beginn.desc())
        ).all(),
        "rechnungen": db.scalars(
            select(Rechnung).where(Rechnung.kunde_id == k.id).order_by(Rechnung.datum.desc())
        ).all(),
        "guthaben": db.scalars(
            select(GuthabenBuchung)
            .where(GuthabenBuchung.kunde_id == k.id)
            .order_by(GuthabenBuchung.created_at.desc())
        ).all(),
        "audit": db.scalars(
            select(Audit)
            .where(Audit.objekt_typ == "kunde", Audit.objekt_id == k.id)
            .order_by(Audit.zeitpunkt.desc())
            .limit(50)
        ).all(),
    }


@router.get("/kunden/guthabenliste", response_class=HTMLResponse)
def guthabenliste(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    liste = guthaben.guthabenliste(db)
    return render(
        request,
        "kunden/guthabenliste.html",
        admin=admin,
        kunden=liste,
        summe=sum((k.guthaben for k in liste), Decimal("0.00")),
        stichtag=konfiguration.hole(db, "saisonende_guthabenliste"),
    )


@router.post("/kunden/guthabenliste/{kunde_id}/auszahlung", response_model=None)
def guthaben_auszahlen(
    kunde_id: uuid.UUID,
    betrag: str = Form(""),
    notiz: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Hakt eine Auszahlung ab, die der Betreiber selbst überwiesen hat (A-ZAHL-6)."""
    zurueck = RedirectResponse("/admin/kunden/guthabenliste", status_code=303)
    k = db.scalar(
        select(Kunde)
        .where(Kunde.id == kunde_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if k is None or k.anonymisiert_am is not None:
        return mit_flash(zurueck, "Kunde nicht gefunden", "fehler")
    try:
        wert = pflicht(t_betrag(betrag), "Betrag")
        if not wert.is_finite() or wert <= 0:
            raise ValueError("Betrag muss größer als 0 sein")
        guthaben.buche(
            db, kunde=k, betrag=-wert, art="auszahlung", notiz=notiz, admin_user_id=admin.id
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return mit_flash(zurueck, fehlertext(e, FEHLERTEXT), "fehler")
    return mit_flash(zurueck, f"Auszahlung an {k.name} abgehakt")


@router.get("/kunden/{kunde_id}", response_class=HTMLResponse)
def detail(
    request: Request,
    kunde_id: uuid.UUID,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return mit_flash(
            RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler"
        )  # type: ignore[return-value]
    return render(request, "kunden/detail.html", admin=admin, **_detail_ctx(db, k))


@router.post("/kunden/{kunde_id}", response_model=None)
def aendern(
    request: Request,
    kunde_id: uuid.UUID,
    name: str = Form(...),
    email: str = Form(...),
    rechnungskunde: str = Form(""),
    adresse_strasse: str = Form(""),
    adresse_plz: str = Form(""),
    adresse_ort: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return mit_flash(
            RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler"
        )
    try:
        kunden.aendere(
            db,
            k,
            admin_user_id=admin.id,
            name=pflicht(name.strip() or None, "Name"),
            email=email,
            rechnungskunde=rechnungskunde == "1",
            adresse_strasse=adresse_strasse,
            adresse_plz=adresse_plz,
            adresse_ort=adresse_ort,
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request,
            "kunden/detail.html",
            admin=admin,
            fehler=fehlertext(e, FEHLERTEXT),
            **_detail_ctx(db, k),
        )
    return mit_flash(RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), "Gespeichert")


@router.post("/kunden/{kunde_id}/guthaben", response_model=None)
def guthaben_buchen(
    request: Request,
    kunde_id: uuid.UUID,
    betrag: str = Form(...),
    art: str = Form(...),
    notiz: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return mit_flash(
            RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler"
        )
    try:
        if art not in ("manuell", "auszahlung"):
            raise GuthabenFehler("art_unbekannt")
        guthaben.buche(
            db,
            kunde=k,
            betrag=pflicht(t_betrag(betrag), "Betrag"),
            art=art,
            notiz=notiz,
            admin_user_id=admin.id,
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request,
            "kunden/detail.html",
            admin=admin,
            fehler=fehlertext(e, FEHLERTEXT),
            **_detail_ctx(db, k),
        )
    return mit_flash(RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), "Guthaben gebucht")


@router.post("/kunden/{kunde_id}/anonymisieren", response_model=None)
def anonymisieren(
    request: Request,
    kunde_id: uuid.UUID,
    bestaetigt: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return mit_flash(
            RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler"
        )
    try:
        if bestaetigt != "1":
            raise ValueError("Bitte die Anonymisierung bestätigen")
        kunden.anonymisiere(db, k, admin_user_id=admin.id)
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return render(
            request,
            "kunden/detail.html",
            admin=admin,
            fehler=fehlertext(e, FEHLERTEXT),
            **_detail_ctx(db, k),
        )
    return mit_flash(RedirectResponse("/admin/kunden", status_code=303), "Kunde anonymisiert")


def _detail_mit_fehler(
    request: Request, admin: AdminUser, db: Session, k: Kunde, e: BaseException
) -> HTMLResponse:
    db.rollback()
    return render(
        request,
        "kunden/detail.html",
        admin=admin,
        fehler=fehlertext(e, FEHLERTEXT),
        **_detail_ctx(db, k),
    )


@router.post("/kunden/{kunde_id}/mitgliedschaft", response_model=None)
def mitgliedschaft_freischalten(
    request: Request,
    kunde_id: uuid.UUID,
    bis: str = Form(...),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return _nicht_gefunden()
    try:
        mitgliedschaft.freischalten(
            db, k, bis=pflicht(t_datum(bis), "Mitglied bis"), admin_user_id=admin.id
        )
        db.commit()
    except FORM_FEHLER as e:
        return _detail_mit_fehler(request, admin, db, k, e)
    benachrichtigung.mitgliedschaft_freigeschaltet(db, k)
    return mit_flash(
        RedirectResponse(f"/admin/kunden/{k.id}", status_code=303),
        f"Mitgliedschaft bis {k.mitglied_bis:%d.%m.%Y} freigeschaltet",
    )


@router.post("/kunden/{kunde_id}/mitgliedschaft/beenden", response_model=None)
def mitgliedschaft_beenden(
    request: Request,
    kunde_id: uuid.UUID,
    grund: str = Form(...),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return _nicht_gefunden()
    try:
        war_mitglied = mitgliedschaft.beende(db, k, grund=grund, admin_user_id=admin.id)
        db.commit()
    except FORM_FEHLER as e:
        return _detail_mit_fehler(request, admin, db, k, e)
    if war_mitglied:
        benachrichtigung.mitgliedschaft_beendet(db, k)
    offen = sum(1 for b in mitgliedschaft.klaerungsfaelle(db) if b.kunde_id == k.id)
    text = "Mitgliedschaft beendet."
    if offen:
        mehrere = offen != 1
        text += (
            f" {offen} künftige Buchung{'en' if mehrere else ''} zum Mitgliedspreis "
            f"{'stehen' if mehrere else 'steht'} in der Klärungsliste "
            "(System → Klärung Mitgliedschaft)."
        )
    return mit_flash(RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), text)


@router.post("/kunden/{kunde_id}/mitgliedschaft/antrag-verwerfen", response_model=None)
def antrag_verwerfen(
    request: Request,
    kunde_id: uuid.UUID,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    k = db.get(Kunde, kunde_id)
    if k is None:
        return _nicht_gefunden()
    try:
        mitgliedschaft.verwerfe_antrag(db, k, admin_user_id=admin.id)
        db.commit()
    except FORM_FEHLER as e:
        return _detail_mit_fehler(request, admin, db, k, e)
    return mit_flash(RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), "Antrag verworfen")
