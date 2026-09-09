import logging
import os
import uuid
from datetime import timedelta
from typing import Any

from beachhub_shared import lesestand as schema
from beachhub_shared import signatur
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import (
    Ausnahmetag,
    Betriebszeit,
    Buchung,
    Feld,
    Kunde,
    Kundengruppe,
    LesestandVersion,
    Rechnung,
    Sperre,
    Storno,
    Tarif,
    utcnow,
)
from beachhub_core.services import konfiguration, pin

logger = logging.getLogger(__name__)


def erzeuge_schluessel() -> str:
    pfad = settings.signatur_privatschluessel_pfad
    priv, pub = signatur.erzeuge_schluesselpaar()
    pfad.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL sorgt für ein atomares "nur anlegen, wenn noch nicht vorhanden" inklusive
    # FileExistsError, ohne die Lücke zwischen exists()-Prüfung und write.
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(priv + "\n")
    return pub


def _privat() -> str:
    return signatur.lade_privatschluessel(settings.signatur_privatschluessel_pfad)


def oeffentlicher_schluessel() -> str:
    return signatur.oeffentlicher_schluessel(_privat())


def baue_belegung(db: Session) -> schema.BelegungInhalt:
    heute = clock.today(db)
    fenster = konfiguration.hole(db, "fenster_tage")
    von, bis = clock.now(db) - timedelta(days=1), clock.now(db) + timedelta(days=fenster + 1)
    felder = db.scalars(select(Feld).where(Feld.aktiv.is_(True)).order_by(Feld.reihenfolge)).all()
    belegt: dict[str, list[schema.Zeitraum]] = {}
    for f in felder:
        zr = [
            schema.Zeitraum(beginn=b.beginn, ende=b.ende)
            for b in db.scalars(
                select(Buchung).where(
                    Buchung.feld_id == f.id,
                    Buchung.status.in_(Buchung.AKTIVE_STATUS),
                    Buchung.beginn < bis,
                    Buchung.ende > von,
                )
            )
        ]
        zr += [
            schema.Zeitraum(beginn=s.beginn, ende=s.ende)
            for s in db.scalars(
                select(Sperre).where(
                    or_(Sperre.feld_id == f.id, Sperre.feld_id.is_(None)),
                    Sperre.beginn < bis,
                    Sperre.ende > von,
                )
            )
        ]
        belegt[str(f.id)] = sorted(zr, key=lambda z: z.beginn)
    return schema.BelegungInhalt(
        felder=[
            schema.FeldInfo(
                id=str(f.id),
                name=f.name,
                reihenfolge=f.reihenfolge,
                raster=[
                    schema.RasterInfo(
                        wochentag=r.wochentag,
                        modus=r.modus,
                        slot_minuten=r.slot_minuten,
                        fenster=r.fenster_json,
                    )
                    for r in f.raster
                ],
            )
            for f in felder
        ],
        betriebszeiten=[
            schema.BetriebszeitInfo(
                wochentag=b.wochentag,
                oeffnet=b.oeffnet,
                schliesst=b.schliesst,
                gueltig_von=b.gueltig_von,
                gueltig_bis=b.gueltig_bis,
            )
            for b in db.scalars(select(Betriebszeit))
        ],
        ausnahmetage=[
            schema.AusnahmeInfo(
                datum=a.datum, geschlossen=a.geschlossen, oeffnet=a.oeffnet, schliesst=a.schliesst
            )
            for a in db.scalars(select(Ausnahmetag).where(Ausnahmetag.datum >= heute))
        ],
        fenster_tage=fenster,
        mindestvorlauf_minuten=konfiguration.hole(db, "mindestvorlauf_minuten"),
        belegt=belegt,
    )


def baue_tarife(db: Session) -> schema.TarifeInhalt:
    heute = clock.today(db)
    regeln = db.scalars(
        select(Tarif).where(
            Tarif.aktiv.is_(True), or_(Tarif.gueltig_bis.is_(None), Tarif.gueltig_bis >= heute)
        )
    ).all()
    return schema.TarifeInhalt(
        regeln=[
            schema.TarifInfo(
                name=t.name,
                preis=t.preis,
                feld_id=str(t.feld_id) if t.feld_id else None,
                wochentag=t.wochentag,
                uhrzeit_von=t.uhrzeit_von,
                uhrzeit_bis=t.uhrzeit_bis,
                kundengruppe=(
                    db.get(Kundengruppe, t.kundengruppe_id).name  # type: ignore[union-attr]
                    if t.kundengruppe_id
                    else None
                ),
                gueltig_von=t.gueltig_von,
                gueltig_bis=t.gueltig_bis,
            )
            for t in regeln
        ]
    )


def baue_konto(db: Session, kunde: Kunde) -> schema.KontoInhalt:
    jetzt = clock.now(db)
    buchungen = db.scalars(
        select(Buchung)
        .where(Buchung.kunde_id == kunde.id, Buchung.beginn >= jetzt - timedelta(days=90))
        .order_by(Buchung.beginn)
    ).all()
    out = []
    for b in buchungen:
        s = db.scalar(select(Storno).where(Storno.buchung_id == b.id))
        zeige_pin = b.aktiv and b.ende > jetzt and b.pin_verschluesselt
        out.append(
            schema.KontoBuchung(
                id=str(b.id),
                feld_id=str(b.feld_id),
                feld_name=b.feld.name,
                beginn=b.beginn,
                ende=b.ende,
                status=b.status,
                preis=b.preis,
                pin=pin.entschluessele(b.pin_verschluesselt) if zeige_pin else None,  # type: ignore[arg-type]
                storno=schema.StornoInfo(
                    kostenfrei=s.kostenfrei,
                    nachbuchung_offen=s.nachbuchung_offen,
                    freigestellt_betrag=s.freigestellt_betrag,
                )
                if s
                else None,
            )
        )
    rechnungen = db.scalars(
        select(Rechnung).where(Rechnung.kunde_id == kunde.id).order_by(Rechnung.datum.desc())
    ).all()
    return schema.KontoInhalt(
        kunde_id=str(kunde.id),
        kundengruppe=kunde.kundengruppe.name,
        zahlungsart=kunde.zahlungsart,
        guthaben=kunde.guthaben,
        buchungen=out,
        rechnungen=[
            schema.KontoRechnung(nummer=r.nummer, datum=r.datum, brutto=r.brutto, status=r.status)
            for r in rechnungen
        ],
    )


def _inhalt(db: Session, name: str) -> dict[str, Any]:
    if name == "belegung":
        return baue_belegung(db).model_dump(mode="json")
    if name == "tarife":
        return baue_tarife(db).model_dump(mode="json")
    if name.startswith("konto:"):
        kunde = db.get(Kunde, uuid.UUID(name.split(":", 1)[1]))
        if kunde is None:
            raise KeyError(name)
        return baue_konto(db, kunde).model_dump(mode="json")
    raise KeyError(name)


def publiziere(db: Session, name: str) -> schema.Dokument:
    inhalt = _inhalt(db, name)
    zeile = db.scalar(
        select(LesestandVersion).where(LesestandVersion.dokument == name).with_for_update()
    )
    if zeile is None:
        zeile = LesestandVersion(dokument=name, version=0)
        db.add(zeile)
        db.flush()
    zeile.version += 1
    zeile.signiert_am = utcnow()
    zeile.geaendert = False
    # Signiert wird immer über die JSON-Darstellung (Decimal → String, datetime → ISO), genau wie
    # pruefe() sie bildet.
    entwurf = schema.Dokument(
        dokument=name,
        version=zeile.version,
        erzeugt_am=zeile.signiert_am,
        inhalt=inhalt,
        signatur="",
    )
    sig = signatur.signiere(entwurf.model_dump(mode="json", exclude={"signatur"}), _privat())
    dok = entwurf.model_copy(update={"signatur": sig})
    ordner = settings.data_dir / "lesestand"
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / f"{name.replace(':', '_')}.json"
    tmp = pfad.with_suffix(".json.tmp")
    tmp.write_text(dok.model_dump_json(), encoding="utf-8")
    os.replace(tmp, pfad)  # atomar: Version im Dateiinhalt und die Datei selbst bleiben in Sync
    db.flush()
    return dok


def markiere_geaendert(db: Session, *namen: str) -> None:
    for name in namen:
        zeile = db.get(LesestandVersion, name)
        if zeile is None:
            db.add(LesestandVersion(dokument=name, version=0, geaendert=True))
        else:
            zeile.geaendert = True
    db.flush()


def verarbeite_geaenderte(db: Session) -> list[str]:
    if not settings.signatur_privatschluessel_pfad.exists():
        # Einmal vorab statt erst beim ersten `publiziere()`: dort würde das breite
        # `except Exception:` unten das FileNotFoundError sonst abfangen und nur loggen,
        # wodurch die Meldung "Signaturschlüssel fehlt" in der Route nie ausgelöst würde.
        raise FileNotFoundError(
            f"Signaturschlüssel fehlt: {settings.signatur_privatschluessel_pfad}"
        )
    namen = [
        z.dokument
        for z in db.scalars(
            select(LesestandVersion).where(LesestandVersion.geaendert.is_(True))
        ).all()
    ]
    veroeffentlicht: list[str] = []
    for name in namen:
        try:
            publiziere(db, name)
            db.commit()
            veroeffentlicht.append(name)
        except (KeyError, ValueError):
            # z. B. anonymisierter/gelöschter Kunde oder ein nicht (mehr) gültiger Name
            # (ungültige UUID) – wie einen unbekannten Namen behandeln und die Markierung
            # entfernen, statt sie endlos erneut zu versuchen.
            db.rollback()
            zeile = db.get(LesestandVersion, name)
            if zeile is not None:
                db.delete(zeile)
            db.commit()
        except Exception:
            # Ein einzelnes fehlerhaftes Dokument (z. B. Datei-/Signaturfehler) darf die
            # übrigen nicht blockieren; Markierung bleibt bestehen für den nächsten Lauf.
            db.rollback()
            logger.exception("Lesestand-Dokument %s konnte nicht erzeugt werden", name)
    return veroeffentlicht


def lade(name: str) -> schema.Dokument | None:
    pfad = settings.data_dir / "lesestand" / f"{name.replace(':', '_')}.json"
    return (
        schema.Dokument.model_validate_json(pfad.read_text(encoding="utf-8"))
        if pfad.exists()
        else None
    )


def pruefe(dok: schema.Dokument) -> bool:
    return signatur.pruefe(
        dok.model_dump(mode="json", exclude={"signatur"}), dok.signatur, oeffentlicher_schluessel()
    )
