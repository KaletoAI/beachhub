import uuid
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core import auth, clock
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser, Audit, Feld, Gutschein, GutscheinEinloesung, Kunde
from beachhub_core.routes._form import fehlertext, pflicht, t_datum, t_int, t_uuid
from beachhub_core.services import benachrichtigung, gutschein_sperren, gutscheine, konfiguration
from beachhub_core.templating import mit_flash, render

router = APIRouter()
FEHLER = (ValueError, IntegrityError, gutschein_sperren.GutscheinVorbereitungWiederholen)


def _fehler(e: BaseException) -> str:
    if isinstance(e, gutschein_sperren.GutscheinVorbereitungWiederholen):
        return "Die Gutscheinzuordnung hat sich geändert. Bitte die Aktion erneut ausführen."
    return fehlertext(e)


@router.get("/gutscheine", response_class=HTMLResponse)
def liste(
    request: Request,
    status: str = "",
    art: str = "",
    serie: str = "",
    seite: int = Query(1, ge=1),
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    stmt = select(Gutschein).order_by(Gutschein.ausgestellt_am.desc(), Gutschein.id)
    for spalte, wert in (
        (Gutschein.status, status),
        (Gutschein.art, art),
        (Gutschein.serie, serie),
    ):
        if wert:
            stmt = stmt.where(spalte == wert)
    anzahl = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    seiten = max(1, (anzahl + 499) // 500)
    seite = min(seite, seiten)
    bestand = db.scalars(stmt.offset((seite - 1) * 500).limit(500)).all()
    kunde_ids = {
        kid for g in bestand for kid in (g.kaeufer_kunde_id, g.inhaber_kunde_id) if kid is not None
    }
    kunden = {k.id: k for k in db.scalars(select(Kunde).where(Kunde.id.in_(kunde_ids)))}

    def seiten_url(n: int) -> str:
        return "/admin/gutscheine?" + urlencode(
            {"status": status, "art": art, "serie": serie, "seite": n}
        )

    return render(
        request,
        "gutscheine/liste.html",
        admin=admin,
        gutscheine=bestand,
        kunden=kunden,
        anzahl=anzahl,
        seite=seite,
        seiten=seiten,
        vorher=seiten_url(seite - 1) if seite > 1 else None,
        weiter=seiten_url(seite + 1) if seite < seiten else None,
        status=status,
        art=art,
        serie=serie,
    )


@router.get("/gutscheine/freicodes", response_class=HTMLResponse)
def freicodes(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(
        request,
        "gutscheine/freicodes.html",
        admin=admin,
        felder=db.scalars(select(Feld).order_by(Feld.reihenfolge, Feld.name)).all(),
        gueltig_bis=clock.today(db)
        + timedelta(days=konfiguration.hole(db, "freicode_gueltig_tage")),
        minuten=konfiguration.hole(db, "gutschein_minuten"),
    )


@router.post("/gutscheine/freicodes")
async def ausstellen(
    request: Request,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    form = await request.form()
    serie = str(form.get("serie", "")).strip()
    try:
        codes = gutscheine.stelle_freicodes_aus(
            db,
            anzahl=pflicht(t_int(str(form.get("anzahl", ""))), "Anzahl"),
            gueltig_bis=t_datum(str(form.get("gueltig_bis", ""))),
            max_einloesungen=pflicht(t_int(str(form.get("max_einloesungen", "1"))), "Einlösungen"),
            feld_id=t_uuid(str(form.get("feld_id", ""))),
            zeitraum_von=t_datum(str(form.get("zeitraum_von", ""))),
            zeitraum_bis=t_datum(str(form.get("zeitraum_bis", ""))),
            serie=serie,
            notiz=str(form.get("notiz", "")).strip(),
            admin_user_id=admin.id,
        )
        anzahl = len(codes)
        db.commit()
    except FEHLER as e:
        db.rollback()
        return mit_flash(
            RedirectResponse("/admin/gutscheine/freicodes", status_code=303), _fehler(e), "fehler"
        )
    return mit_flash(
        RedirectResponse(
            "/admin/gutscheine?" + urlencode({"serie": serie, "art": "frei"}), status_code=303
        ),
        f"{anzahl} Freischaltcodes ausgestellt",
    )


@router.get("/gutscheine/serie.csv")
def export(
    serie: str = "",
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> Response:
    return Response(
        gutscheine.serie_csv(db, serie),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="freischaltcodes.csv"'},
    )


@router.get("/gutscheine/{gutschein_id}", response_model=None)
def detail(
    request: Request,
    gutschein_id: uuid.UUID,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    g = db.get(Gutschein, gutschein_id)
    if g is None:
        return mit_flash(
            RedirectResponse("/admin/gutscheine", status_code=303), "Nicht gefunden", "fehler"
        )
    return render(
        request,
        "gutscheine/detail.html",
        admin=admin,
        g=g,
        kaeufer=db.get(Kunde, g.kaeufer_kunde_id) if g.kaeufer_kunde_id else None,
        inhaber=db.get(Kunde, g.inhaber_kunde_id) if g.inhaber_kunde_id else None,
        feld=db.get(Feld, g.feld_id) if g.feld_id else None,
        einloesungen=db.scalars(
            select(GutscheinEinloesung)
            .where(GutscheinEinloesung.gutschein_id == g.id)
            .order_by(GutscheinEinloesung.zeitpunkt)
        ).all(),
        audits=db.scalars(
            select(Audit)
            .where(Audit.objekt_typ == "gutschein", Audit.objekt_id == g.id)
            .order_by(Audit.zeitpunkt.desc())
        ).all(),
    )


@router.post("/gutscheine/{gutschein_id}/sperren")
def sperren(
    gutschein_id: uuid.UUID,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    g = db.get(Gutschein, gutschein_id)
    if g is None:
        return mit_flash(
            RedirectResponse("/admin/gutscheine", status_code=303), "Nicht gefunden", "fehler"
        )
    try:
        gutscheine.sperre(db, g, admin_user_id=admin.id)
        db.commit()
    except FEHLER as e:
        db.rollback()
        return mit_flash(
            RedirectResponse(f"/admin/gutscheine/{gutschein_id}", status_code=303),
            _fehler(e),
            "fehler",
        )
    return mit_flash(
        RedirectResponse(f"/admin/gutscheine/{gutschein_id}", status_code=303), "Gutschein gesperrt"
    )


@router.post("/gutscheine/{gutschein_id}/gueltigkeit")
def gueltigkeit(
    gutschein_id: uuid.UUID,
    gueltig_bis: str = Form(""),
    reaktivieren: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    g = db.get(Gutschein, gutschein_id)
    if g is None:
        return mit_flash(
            RedirectResponse("/admin/gutscheine", status_code=303), "Nicht gefunden", "fehler"
        )
    try:
        gutscheine.setze_gueltigkeit(
            db,
            g,
            pflicht(t_datum(gueltig_bis), "Gültigkeit"),
            reaktivieren=reaktivieren == "1",
            admin_user_id=admin.id,
        )
        db.commit()
    except FEHLER as e:
        db.rollback()
        return mit_flash(
            RedirectResponse(f"/admin/gutscheine/{gutschein_id}", status_code=303),
            _fehler(e),
            "fehler",
        )
    return mit_flash(
        RedirectResponse(f"/admin/gutscheine/{gutschein_id}", status_code=303),
        "Gültigkeit gespeichert",
    )


@router.post("/gutscheine/{gutschein_id}/rueckgabe")
def rueckgabe(
    gutschein_id: uuid.UUID,
    grund: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    g = db.get(Gutschein, gutschein_id)
    if g is None:
        return mit_flash(
            RedirectResponse("/admin/gutscheine", status_code=303), "Nicht gefunden", "fehler"
        )
    ziel = f"/admin/gutscheine/{gutschein_id}"
    try:
        ergebnis = gutscheine.gib_zurueck(db, g, grund=grund, admin_user_id=admin.id)
        kaeufer = db.get(Kunde, g.kaeufer_kunde_id)
        assert kaeufer is not None
        betrag = ergebnis.manuell_zu_erstatten
        name = kaeufer.name
        beleg_id = ergebnis.korrekturbeleg.id if ergebnis.korrekturbeleg is not None else None
        db.commit()
    except FEHLER as e:
        db.rollback()
        return mit_flash(RedirectResponse(ziel, status_code=303), _fehler(e), "fehler")
    if beleg_id is not None:
        benachrichtigung.belege_versenden(db, [beleg_id])
    betrag_text = f"{betrag:.2f}".replace(".", ",")
    hinweis = (
        f"Gutschein zurückgegeben. {betrag_text} € anschließend manuell "
        f"an den ursprünglichen Käufer {name} erstatten."
        if betrag > 0
        else (
            "Gutschein bereits zurückgegeben oder Kaufbeleg bereits korrigiert; "
            "keine weitere Erstattung."
        )
    )
    return mit_flash(RedirectResponse(ziel, status_code=303), hinweis)
