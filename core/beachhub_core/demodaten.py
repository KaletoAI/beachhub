"""Demodaten für den manuellen Test in der Entwicklung (`beachhub-core demodaten`).

Ergänzt fehlende Stammdaten (Felder 1–3 mit Raster, Tarif), stellt die Halle auf täglich
15–23 Uhr mit Sonderschließung an Weihnachten und Silvester und legt über die Fachdienste
Kunden, Buchungen, ein Abo und Guthaben an. Läuft nur außerhalb von `production` und nur,
solange es noch keine Kunden gibt.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from decimal import Decimal

from beachhub_shared import kanal
from beachhub_shared.slots import Slot
from beachhub_shared.zeit import BERLIN
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.config import settings
from beachhub_core.models import (
    Ausnahmetag,
    Betriebszeit,
    Buchung,
    Feld,
    Kunde,
    Rechnung,
    Tarif,
    Zahlung,
)
from beachhub_core.services import (
    benachrichtigung,
    buchungen,
    dauerbuchungen,
    guthaben,
    gutschein_sperren,
    konfiguration,
    kunden,
    kundengruppen,
    mitgliedschaft,
    online_buchung,
    rechnungen,
    slots_db,
    stammdaten,
)

FELDER = ("Feld 1", "Feld 2", "Feld 3")
WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
ABO_WOCHEN = 8
DOMAIN = "example.org"
OEFFNUNG = (time(15, 0), time(23, 0))
# Raster für Felder ohne eigenes: Feld 3 behält den bisherigen Entwicklungsstand (120 Minuten).
RASTER_MINUTEN = {"Feld 1": 60, "Feld 2": 60, "Feld 3": 120}
SCHLIESSTAGE = (
    (12, 24, "Heiligabend"),
    (12, 25, "1. Weihnachtstag"),
    (12, 26, "2. Weihnachtstag"),
    (12, 31, "Silvester"),
)


class DemodatenFehler(Exception):  # noqa: N818
    pass


@dataclass
class Bericht:
    zeilen: list[str] = field(default_factory=list)
    belege: list[uuid.UUID] = field(default_factory=list)

    def __call__(self, text: str) -> None:
        self.zeilen.append(text)


def _stammdaten(db: Session, bericht: Bericht) -> Feld:
    kundengruppen.beide(db)
    vorhanden = {f.name: f for f in db.scalars(select(Feld))}
    for i, name in enumerate(FELDER, start=1):
        if name not in vorhanden:
            vorhanden[name] = stammdaten.feld_anlegen(
                db, admin_user_id=None, name=name, reihenfolge=i
            )
            bericht(f"Stammdaten: {name} angelegt")
    for name, minuten in RASTER_MINUTEN.items():
        if not vorhanden[name].raster:
            stammdaten.raster_setzen(
                db,
                vorhanden[name],
                admin_user_id=None,
                wochentag=None,
                modus="dauer",
                slot_minuten=minuten,
                fenster=[],
            )
            bericht(f"Stammdaten: Raster für {name} ({minuten} Minuten) angelegt")
    _oeffnungszeiten(db, bericht)
    if db.scalar(select(func.count(Tarif.id)).where(Tarif.aktiv.is_(True))) == 0:
        stammdaten.tarif_anlegen(
            db, admin_user_id=None, name="Demo je Slot", preis=Decimal("40.00")
        )
        bericht("Stammdaten: Tarif „Demo je Slot“ 40,00 € angelegt")
    db.flush()
    feld = vorhanden["Feld 3"]
    db.refresh(feld)
    return feld


def _oeffnungszeiten(db: Session, bericht: Bericht) -> None:
    """Täglich 15–23 Uhr ohne Befristung. Abweichende Betriebszeiten werden ersetzt, weil bei
    mehreren Einträgen für einen Wochentag nicht feststeht, welcher gilt."""
    oeffnet, schliesst = OEFFNUNG
    passend: set[int] = set()
    ersetzt = 0
    for bz in db.scalars(select(Betriebszeit)).all():
        if (bz.oeffnet, bz.schliesst, bz.gueltig_von, bz.gueltig_bis) == (
            oeffnet,
            schliesst,
            None,
            None,
        ) and bz.wochentag not in passend:
            passend.add(bz.wochentag)
            continue
        stammdaten.betriebszeit_loeschen(db, bz, admin_user_id=None)
        ersetzt += 1
    for wt in sorted(set(range(7)) - passend):
        stammdaten.betriebszeit_anlegen(
            db,
            admin_user_id=None,
            wochentag=wt,
            oeffnet=oeffnet,
            schliesst=schliesst,
            gueltig_von=None,
            gueltig_bis=None,
        )
    if len(passend) < 7:
        bericht(
            f"Stammdaten: Betriebszeit täglich {oeffnet:%H:%M}–{schliesst:%H:%M}"
            + (f" (ersetzt {ersetzt} abweichende)" if ersetzt else "")
        )
    jahr = clock.today(db).year
    neu = []
    for monat, tag, grund in SCHLIESSTAGE:
        d = date(jahr, monat, tag)
        if db.scalar(select(Ausnahmetag.id).where(Ausnahmetag.datum == d)) is None:
            stammdaten.ausnahmetag_anlegen(
                db,
                admin_user_id=None,
                datum=d,
                geschlossen=True,
                oeffnet=None,
                schliesst=None,
                grund=grund,
            )
            neu.append(f"{d:%d.%m.}")
    if neu:
        bericht(f"Stammdaten: Sonderschließung {jahr} am {', '.join(neu)}")


def _abo_beginn(db: Session, feld: Feld, ab: date) -> tuple[date, Slot]:
    """Erster Tag ab `ab`, an dem der erste Slot in allen Abo-Wochen buchbar ist."""
    d = ab
    for _ in range(366):
        slots = slots_db.tages_slots(db, feld, d)
        if len(slots) >= 3 and all(
            any(
                s.beginn.astimezone(BERLIN).time() == slots[0].beginn.astimezone(BERLIN).time()
                for s in slots_db.tages_slots(db, feld, d + timedelta(weeks=w))
            )
            for w in range(1, ABO_WOCHEN)
        ):
            return d, slots[0]
        d += timedelta(days=1)
    raise DemodatenFehler("Kein Abo-Zeitraum ohne Schließtage gefunden")


def _termintage(db: Session, feld: Feld, anzahl: int) -> list[tuple[date, list[Slot]]]:
    """Die nächsten Tage ab morgen mit mindestens drei Slots auf dem Feld."""
    tage = []
    d = clock.today(db) + timedelta(days=1)
    for _ in range(366):
        slots = slots_db.tages_slots(db, feld, d)
        if len(slots) >= 3:
            tage.append((d, slots))
            if len(tage) == anzahl:
                return tage
        d += timedelta(days=1)
    raise DemodatenFehler("Keine Tage mit mindestens drei Slots auf Feld 3 gefunden")


def _uhr(s: Slot) -> str:
    return f"{s.beginn.astimezone(BERLIN):%d.%m.%Y %H:%M}"


def _betreiberbuchung(db: Session, feld: Feld, kunde: Kunde, slot: Slot) -> Rechnung:
    """Wie `POST /admin/belegung/buchung`: Event-Steuersatz, sofort offene Einzelrechnung."""
    b = buchungen.lege_an(
        db,
        feld_id=feld.id,
        kunde_id=kunde.id,
        beginn=slot.beginn,
        ende=slot.ende,
        quelle="admin",
        zahlungsart="manuell",
        ust_satz=konfiguration.hole(db, "event_ust_satz"),
    )
    return rechnungen.erzeuge_einzelrechnung(db, b, quelle="admin", status="offen")


def _onlinebuchung(db: Session, feld: Feld, kunde: Kunde, slot: Slot) -> Buchung:
    """Online-Anfrage wie aus dem Portal, bezahlt über den Fakeanbieter."""
    erg = online_buchung.anfragen(
        db,
        kunde=kunde,
        anfrage_id=uuid.uuid4(),
        feld_id=feld.id,
        beginn=slot.beginn,
        ende=slot.ende,
        rueckkehr_url="/zahlung/zurueck",
    )
    if erg.antwort.status != "reserviert" or erg.antwort.buchung_id is None:
        raise DemodatenFehler(f"Online-Anfrage nicht angenommen: {erg.antwort.status}")
    db.commit()
    z = db.scalar(select(Zahlung).where(Zahlung.buchung_id == erg.antwort.buchung_id))
    assert z is not None
    roh = {"ref": z.provider_ref, "ergebnis": "bezahlt", "betrag": str(z.betrag)}
    erg = online_buchung.zahlung_eingegangen(
        db, kanal.ZahlungEingegangen(provider="fake", rohdaten=json.dumps(roh))
    )
    db.commit()
    for nachlauf in erg.nach_commit:
        nachlauf(db)
    b = db.get(Buchung, z.buchung_id)
    assert b is not None and b.status == Buchung.BESTAETIGT
    return b


def lege_an(db: Session) -> Bericht:
    if settings.app_env == "production":
        raise DemodatenFehler("Demodaten sind in production nicht erlaubt")
    if db.scalar(select(func.count(Kunde.id))):
        raise DemodatenFehler("Es gibt bereits Kunden – Demodaten nur für eine leere Datenbank")
    bericht = Bericht()
    feld = _stammdaten(db, bericht)
    (tag1, slots1), (tag2, slots2) = _termintage(db, feld, 2)

    def kunde(name: str, local: str, **werte: bool) -> Kunde:
        return kunden.lege_an(db, name=name, email=f"{local}@{DOMAIN}", quelle="admin", **werte)

    anna = kunde("Anna Online (Demo)", "anna.demo")
    ben = kunde("Ben Betreiber (Demo)", "ben.demo")
    carla = kunde("Carla Abo (Demo)", "carla.demo", rechnungskunde=True)
    dora = kunde("Dora Guthaben (Demo)", "dora.demo")
    db.flush()
    mitgliedschaft.freischalten(db, carla, bis=date(tag1.year + 1, 12, 31), admin_user_id=None)
    guthaben.buche(db, kunde=dora, betrag=Decimal("50.00"), art="manuell", notiz="Demo-Guthaben")
    bericht(f"Kunden: Anna, Ben, Carla (Mitglied, Rechnungskunde), Dora – alle @{DOMAIN}")
    bericht("Guthaben: Dora 50,00 € (manuell)")

    r_ben1 = _betreiberbuchung(db, feld, ben, slots1[2])
    r_ben2 = _betreiberbuchung(db, feld, ben, slots2[1])
    bericht.belege += [r_ben1.id, r_ben2.id]
    bericht(f"Betreiberbuchungen Ben: {_uhr(slots1[2])} und {_uhr(slots2[1])}, je offene Rechnung")

    abo_tag, abo_slot = _abo_beginn(db, feld, tag1)
    abo_start = abo_slot.beginn.astimezone(BERLIN)
    abo_ende = abo_slot.ende.astimezone(BERLIN)
    bis = abo_tag + timedelta(weeks=ABO_WOCHEN - 1)

    def abo_vorgang() -> uuid.UUID:
        d = dauerbuchungen.lege_an(
            db,
            kunde_id=carla.id,
            feld_id=feld.id,
            wochentag=abo_tag.weekday(),
            start=abo_start.time(),
            ende=abo_ende.time(),
            gueltig_von=abo_tag,
            gueltig_bis=bis,
            admin_user_id=None,
            auslassen=set(),
            entscheidungen={},
        )
        return d.id

    dauer_id = gutschein_sperren.fuehre_transaktion_aus(db, abo_vorgang)
    saison = db.scalars(select(Rechnung.id).where(Rechnung.dauerbuchung_id == dauer_id)).all()
    bericht.belege += list(saison)
    bericht(
        f"Abo Carla: {WOCHENTAGE[abo_tag.weekday()]}s {abo_start:%H:%M}–{abo_ende:%H:%M}, "
        f"{abo_tag:%d.%m.%Y} bis {bis:%d.%m.%Y}, Saisonrechnung offen"
    )

    # Die Online-Buchung schreibt selbst fest und versendet ihre Rechnung im Nachlauf.
    _onlinebuchung(db, feld, anna, slots1[1])
    bericht(f"Online-Buchung Anna: {_uhr(slots1[1])}, über den Fakeanbieter bezahlt")
    return bericht


def main(db: Session) -> list[str]:
    bericht = lege_an(db)
    db.commit()
    benachrichtigung.belege_versenden(db, bericht.belege)
    return bericht.zeilen
