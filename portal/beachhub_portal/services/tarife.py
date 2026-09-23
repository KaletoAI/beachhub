"""Preis zur Anzeige im Portal – dieselbe Auflösung wie im Hauptsystem (A-TARIF-2).

Verbindlich ist der Preis, den das Hauptsystem beim Buchen festschreibt; hier geht es nur
darum, dem Kunden vorher den richtigen Betrag zu zeigen. `_passt`/`_spezifitaet` spiegeln
absichtlich `core/beachhub_core/services/tarife.py` (gleiche Regeln, andere Datenquelle:
Lesestand-Dokument statt ORM/DB) – siehe Preflight-Befund R7; eine gemeinsame Funktion
bräuchte core- und shared-Typen zugleich, was die Trennung Hauptsystem/Portal (N-1)
aufweichen würde.
"""

from decimal import Decimal

from beachhub_shared.lesestand import TarifeInhalt, TarifInfo
from beachhub_shared.slots import Slot
from beachhub_shared.zeit import lokal


def _passt(t: TarifInfo, feld_id: str, slot: Slot, gruppe: str | None) -> bool:
    lok = lokal(slot.beginn)
    if t.feld_id is not None and t.feld_id != feld_id:
        return False
    if t.wochentag is not None and t.wochentag != lok.weekday():
        return False
    if t.uhrzeit_von is not None and t.uhrzeit_bis is not None:
        if not (t.uhrzeit_von <= lok.time() < t.uhrzeit_bis):
            return False
    if t.kundengruppe is not None and t.kundengruppe != gruppe:
        return False
    if t.gueltig_von is not None and lok.date() < t.gueltig_von:
        return False
    if t.gueltig_bis is not None and lok.date() > t.gueltig_bis:
        return False
    return True


def _spezifitaet(t: TarifInfo) -> int:
    return sum(
        [
            t.feld_id is not None,
            t.wochentag is not None,
            t.uhrzeit_von is not None and t.uhrzeit_bis is not None,
            t.kundengruppe is not None,
            t.gueltig_von is not None or t.gueltig_bis is not None,
        ]
    )


def regel(t: TarifeInhalt, feld_id: str, slot: Slot, gruppe: str | None) -> TarifInfo | None:
    kandidaten = [(i, r) for i, r in enumerate(t.regeln) if _passt(r, feld_id, slot, gruppe)]
    if not kandidaten:
        return None
    # Das Hauptsystem liefert die Regeln nach Anlage sortiert (baue_tarife: order_by(created_at)):
    # der höhere Listenindex ist die neuere Regel.
    return max(kandidaten, key=lambda paar: (_spezifitaet(paar[1]), paar[0]))[1]


def preis(t: TarifeInhalt, feld_id: str, slots: list[Slot], gruppe: str | None) -> Decimal | None:
    if not slots:
        return None
    summe = Decimal("0.00")
    for slot in slots:
        r = regel(t, feld_id, slot, gruppe)
        if r is None:
            return None
        summe += r.preis
    return summe
