import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session
from weasyprint import HTML

from beachhub_core.config import settings
from beachhub_core.models import Rechnung
from beachhub_core.services.rechnungen import RechnungsFehler
from beachhub_core.templating import templates


def _betreiber() -> dict[str, str]:
    return {
        "name": settings.betreiber_name,
        "adresse": settings.betreiber_adresse,
        "ust_id": settings.betreiber_ust_id,
        "bank": settings.betreiber_bank,
    }


def erzeuge(db: Session, rechnung: Rechnung) -> Path:
    # Zeile sperren und frisch lesen, damit ein gleichzeitiger Aufruf für dieselbe Rechnung
    # nicht zweimal an `pdf_pfad is None` vorbeikommt (TOCTOU).
    db.execute(select(Rechnung).where(Rechnung.id == rechnung.id).with_for_update())
    db.refresh(rechnung)
    if rechnung.pdf_pfad:
        raise RechnungsFehler("pdf_vorhanden")
    html = templates.env.get_template("rechnung_pdf.html").render(
        r=rechnung, betreiber=_betreiber()
    )
    daten = HTML(string=html).write_pdf()
    ordner = settings.data_dir / "rechnungen"
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / f"{rechnung.nummer}.pdf"
    rechnung.pdf_pfad = str(pfad)
    rechnung.pdf_sha256 = hashlib.sha256(daten).hexdigest()
    db.flush()
    try:
        with open(pfad, "xb") as datei:  # exklusiv anlegen: eine bestehende Datei bleibt unberührt
            datei.write(daten)
    except FileExistsError as exc:
        raise RechnungsFehler("pdf_vorhanden") from exc
    return pfad


def pruefe_integritaet(rechnung: Rechnung) -> bool:
    if not rechnung.pdf_pfad or not rechnung.pdf_sha256:
        return False
    try:
        daten = Path(rechnung.pdf_pfad).read_bytes()
    except OSError:
        return False
    return hashlib.sha256(daten).hexdigest() == rechnung.pdf_sha256
