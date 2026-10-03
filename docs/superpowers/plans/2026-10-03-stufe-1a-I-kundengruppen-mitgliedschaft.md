# Stufe 1a-I: Kundengruppen, Mitgliedschaft und Rechnungskunden – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das Hauptsystem kennt genau zwei Kundengruppen mit eigenem Umsatzsteuersatz, leitet die Gruppe eines Kunden aus seiner Mitgliedschaft zum Termin ab, schreibt Preis und Steuersatz an jede Buchung, weist Rechnungen je Steuersatz aus, verwaltet Mitgliedschaften samt Antrag, Klärungsliste, Jahresabgleich und Erinnerung und sperrt Rechnungskunden für die Onlinebuchung.

**Architecture:** `kundengruppe` wird eine feste Tabelle mit zwei Zeilen (`ist_mitglied` eindeutig, `ust_satz` je Gruppe). Der Kunde verliert `kundengruppe_id` und `zahlungsart` und bekommt `rechnungskunde` und die `mitglied_*`-Felder; seine Gruppe ergibt sich aus `mitglied_bis` und dem Leistungsdatum (`services/kundengruppen.py`). Jede Buchung trägt ihren festgeschriebenen `ust_satz` und ihre `kundengruppe_id`, jede Rechnungsposition ihren Satz samt Netto und Steuer; der Rechnungskopf summiert. Die Mitgliedschaft lebt in `services/mitgliedschaft.py` (Freischalten, Verlängern, Beenden, Antrag, Klärungsliste, Prüfliste, Tageslauf). Kanal und Lesestand bekommen den Anfragetyp `mitgliedschaft_beantragen` und die Konto-Felder für Mitgliedschaft und Rechnungskunden; die Portal-Oberfläche dazu folgt mit dem Rest von Stufe 2.

**Tech Stack:** Python 3.12, FastAPI 0.115.6, SQLAlchemy 2.0.36, Alembic 1.14, PostgreSQL 16, Jinja2, pydantic 2.10, APScheduler 3.11, WeasyPrint 63.1, pytest 8.3, ruff 0.8.4, mypy 1.13.

**Spec:** `docs/superpowers/specs/2026-09-05-beachhub-design.md` – § 3.2 (A-KUND-1 bis -7), § 3.3 (A-TARIF-3), § 3.8 (A-ZAHL-1), § 3.9 (A-RECH-8, A-RECH-9), § 3.12 (A-MAIL-2, Mitgliedschaft), § 3.13 (A-ADM-2, -3, -9), § 3.14, § 5 (Tabellen `kundengruppe`, `kunde`, `buchung`, `rechnung_position`), § 8.1 (Anfragetyp `mitgliedschaft_beantragen`).

## Stufe 1a in drei Plänen

Stufe 1a ist in drei Pläne geteilt. Jeder wird für sich umgesetzt, geprüft und nach `main` gemergt:

1. **1a-I (dieser Plan):** Kundengruppen, Steuersatz je Buchung und Rechnungsposition, Betreiberbuchung mit Event-Steuersatz, Mitgliedschaft mit Antrag, Klärungsliste, Jahresabgleich und Erinnerung, Rechnungskunden im Kanal.
2. **1a-II:** Saisonrechnung statt Monatslauf, Abo nur für Rechnungskunden und Mitglieder, freie Abo-Absagen, Teil-Stornorechnung mit Korrekturbeleg, Guthabenverrechnung auf der Saisonrechnung, Guthabenliste zum Saisonende.
3. **1a-III:** Gutscheine und Freischaltcodes (Kauf, Einlösung in der Buchung, Storno, Verfall, Rückgabe, Admin-Seite).

Die Portal-Oberfläche für Mitgliedsantrag, Gutscheinkauf und Codes beim Buchen gehört zum Rest von Stufe 2; dieser Plan liefert dafür nur die Seite des Hauptsystems und den Vertrag in `shared`.

## Global Constraints

- Python **3.12**; jedes Paket mit eigenem `pyproject.toml`, Abhängigkeiten mit exakten Versionen (`==`). Dieser Plan fügt **keine** neue Abhängigkeit hinzu.
- Geldbeträge und Steuersätze durchgehend `decimal.Decimal` (`DECIMAL(10, 2)` bzw. `DECIMAL(5, 2)`), nie `float` (N-7). Gerundet wird `ROUND_HALF_UP` auf Cent, **je Position** (A-RECH-8).
- Zeitstempel UTC-aware speichern, Anzeige in `Europe/Berlin` (N-7). Fachliche Tage („heute“, Leistungsdatum) immer über `clock.today(db)` bzw. `beachhub_shared.zeit.lokales_datum`.
- Oberfläche des Hauptsystems auf Deutsch, siezt, serverseitig gerendert, **kein Frontend-Build**, keine Inline-Skripte.
- Jede Änderung an Kunden, Buchungen, Rechnungen, Kundengruppen und Einstellungen erzeugt einen Audit-Eintrag (N-6) über `services/audit.py`.
- Mails, Rechnungs-PDFs und Betreiber-Alarme erst **nach** dem Commit (Muster `routes/belegung.py:buchung_anlegen`, `services/ergebnis.py`).
- Jede Nutzlast aus dem Portal ist nicht vertrauenswürdig; den Kunden bestimmt `kunde.portal_konto_id` (Portal-Kern-Design § 4).
- Jede Schemaänderung bekommt eine Alembic-Migration; `core/tests/test_migrationen.py` (upgrade head, downgrade base auf leerer DB) bleibt grün.
- Tests laufen gegen echte PostgreSQL-Datenbanken. **Der Anwendungsserver wird nie gestartet** – auch nicht zum Prüfen; Verifikation ausschließlich über pytest/TestClient.
- Umgebung für alle Befehle (Repo-Wurzel bzw. Worktree):
  ```bash
  source .venv/bin/activate
  export TEST_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test
  export TEST_PORTAL_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal_test
  ```
- Lint nach jedem Task grün: `ruff format .`, dann `ruff check .` und `mypy` (alles aus der Repo-Wurzel). Der Code im Plan ist nicht vorformatiert – `ruff format` bricht lange Aufrufe um; meldet `ruff check` danach noch E501, die Zeichenkette in zwei Literale teilen.
- Commits auf Deutsch mit Präfix `feat(core):`, `feat(shared):`, `test(core):`, `docs:`; jede Commit-Nachricht endet mit den Attributionszeilen der Sitzung.
- Bestehende Tests, die eine Änderung bewusst bricht, werden im **selben** Task angepasst; der Task listet sie einzeln auf.

## Abweichungen von der Spec (vor der Umsetzung bekannt)

- **A-1 `effektive_gruppe` liegt in `services/kundengruppen.py`**, nicht in `kunden` (Spec A-KUND-6 nennt `kunden.effektive_gruppe`). Alles zu den zwei Gruppen steht damit an einer Stelle; `kunden` muss die Gruppe gar nicht kennen.
- **A-2 Fehlende Kundengruppen entstehen bei Bedarf.** Migration 0010 legt beide Zeilen an. Fehlt eine (frische Testdatenbank aus `create_all`), legt `kundengruppen.mitglied()`/`nicht_mitglied()` sie mit den Vorgaben 7 % bzw. 19 % an. Eine dritte Gruppe verhindert der eindeutige Index auf `ist_mitglied`.
- **A-3 Migration 0010 verwirft vorhandene Kundengruppen.** Bis Mitte 2027 gibt es keine produktiven Daten (Projektentscheidung vom 24.09.2026); die Migration entfernt die Gruppenbezüge der Tarife und ersetzt die Gruppen durch die zwei festen. Kunden verlieren ihre Gruppe, Buchungen werden der Gruppe „Nicht-Mitglied“ zugeordnet.
- **A-4 Der Monatslauf bleibt bis Plan 1a-II**, rechnet ab Task 3 aber nur noch Termine von Dauerbuchungen (Zahlungsart `saison`) ab. Betreiberbuchungen (Zahlungsart `manuell`) bekommen sofort eine eigene, offene Rechnung (A-ZAHL-1, A-KUND-7).
- **A-5 Beenden einer Mitgliedschaft wirkt sofort:** `mitglied_bis` wird auf gestern gesetzt. Ist die Mitgliedschaft schon abgelaufen, vermerkt Beenden nur `mitglied_beendet_am` – so verschwindet der Kunde aus der Prüfliste des Jahresabgleichs.
- **A-6 `kunde.stripe_customer_id` bleibt** unter diesem Namen; die Umbenennung in `provider_kunde_id` (Spec § 5) gehört zum Stripe-Plugin in Stufe 2.
- **A-7 „Mitglied können nur Privatpersonen sein“** prüft der Betreiber beim Freischalten; es gibt dafür kein Feld.
- **A-8 Konto-Lesestand:** `kundengruppe` ist die Gruppe von heute. Neue Felder `mitgliedschaft`, `mitglied_bis`, `antrag_am`, `rechnungskunde`, `online_buchen` liegen bereit; dass das Portal Preise künftiger Termine nach `mitglied_bis` rechnet, kommt mit Stufe 2. Das Feld `zahlungsart` entfällt.
- **A-9 Erinnerung vor Ablauf der Mitgliedschaft** (A-MAIL-2 nennt keinen Zeitpunkt): neue Einstellung `mitglied_erinnerung_tage`, Vorgabe 14, `0` schaltet die Erinnerung ab. Je Ablaufdatum geht höchstens eine Erinnerung hinaus (`kunde.mitglied_erinnert_fuer`).
- **A-10 Umfang der Prüfliste** (A-KUND-5 „zum letzten Ablauftag ausgelaufen oder bis zum nächsten auslaufend“): alle Kunden mit `mitglied_bis` im Jahr vor dem letzten Ablauftag bis einschließlich zum nächsten Ablauftag, ohne ausdrücklich beendete.
- **A-11 Mitgliedsanträge haben eine eigene Seite** `/admin/kunden/antraege` (Unterpunkt „Mitgliedsanträge“ im Bereich Kunden); Freischalten und Verwerfen geschieht auf der Kundenseite.
- **A-12 „Saisonstart“ für die Warnung zum Abgleich-Stichtag** (A-KUND-5) ist das früheste künftige `betriebszeit.gueltig_von`. Ohne befristete Betriebszeiten gibt es keine Warnung.
- **A-13 Konfiguration heißt in der Oberfläche „Einstellungen“** (A-ADM-9); der Pfad `/admin/konfiguration` bleibt. Speichern ohne Änderung schreibt keinen Audit-Eintrag mehr.

## Review Focus

1. **Mitgliedschaft endet zwischen Buchung und Termin** (Spec-Beispiel A-KUND-6: `mitglied_bis = 31.03.`, gebucht am 20.03. für den 05.04.) – erwartet: Preis und Steuersatz der Nicht-Mitglieder, festgeschrieben an der Buchung. *Test: Task 1 `test_preis_und_satz_nach_gruppe_am_termin`.*
2. **Rechnung mit 7 % und 19 %** (Status wechselt innerhalb eines Abrechnungszeitraums) – erwartet: je Satz eigene Netto- und Steuersumme, gerundet je Position, Gesamtbetrag gleich Summe der Bruttopositionen. *Tests: Task 2 `test_rechnung_mit_zwei_saetzen_summiert_je_satz`, `test_rundung_je_position`.*
3. **Mitgliedschaft wird vorzeitig beendet, obwohl künftige Termine zum Mitgliedspreis gebucht sind** – erwartet: die Buchungen bleiben unverändert und erscheinen in der Klärungsliste, bis der Betreiber sie klärt. *Test: Task 6 `test_beenden_bringt_kuenftige_mitgliedsbuchung_in_die_klaerung`.*
4. **Ungültiger Stichtag in den Einstellungen** (`31.02.`, `29.02.`, `30-04`) – erwartet: verständliche Fehlermeldung, nichts gespeichert, keine 500. *Tests: Task 4 `test_tagmonat_lehnt_ungueltige_tage_ab`, `test_einstellungen_ungueltiger_stichtag_zeigt_meldung`.*
5. **Rechnungskunde fragt trotz Sperre online an, oder der Betreiber schaltet die Sperre um** – erwartet: `abgelehnt/rechnungskunde` ohne Buchung und ohne Guthabenbewegung; das Konto-Dokument zeigt danach den neuen Wert von `online_buchen`. *Tests: Task 5 `test_rechnungskunde_wird_abgelehnt`, `test_umschalten_markiert_konten_der_rechnungskunden`.*

---

## Dateistruktur

```
shared/beachhub_shared/
  kanal.py                 ÄND  Anfragetyp mitgliedschaft_beantragen (MitgliedschaftBeantragen)
  lesestand.py             ÄND  KontoInhalt: rechnungskunde, online_buchen, mitgliedschaft, mitglied_bis,
                                antrag_am (zahlungsart entfällt); TarifeInhalt: gruppe_mitglied,
                                gruppe_nichtmitglied
shared/tests/test_kanal.py, test_lesestand_schema.py  ÄND

core/beachhub_core/
  models/stammdaten.py     ÄND  Kundengruppe: ust_satz, ist_mitglied (statt standard_zahlungsart)
  models/kunden.py         ÄND  Kunde: rechnungskunde, mitglied_* (statt kundengruppe_id, zahlungsart)
  models/buchungen.py      ÄND  Buchung: ust_satz, kundengruppe_id, gruppe_geklaert_am
  models/rechnungen.py     ÄND  Rechnung ohne ust_satz; RechnungPosition: netto, ust
  services/kundengruppen.py NEU feste Gruppen, effektive_gruppe, aendere
  services/mitgliedschaft.py NEU Ablauftage, Freischalten/Beenden/Antrag, Klärung, Prüfliste, Tageslauf
  services/kunden.py       ÄND  lege_an ohne Gruppe/Zahlungsart, mit rechnungskunde
  services/buchungen.py    ÄND  Gruppe zum Leistungsdatum, ust_satz, zahlungsart (Vorgabe manuell)
  services/dauerbuchungen.py ÄND Preis je Termin nach Gruppe am Termin, Zahlungsart saison
  services/rechnungen.py   ÄND  Posten mit Satz, Summen je Satz, steuer_je_satz(), Einzelrechnung offen,
                                Monatslauf nur saison, csv_sicher öffentlich
  services/rechnung_pdf.py ÄND  html() mit Steuerzeilen
  services/konfiguration.py ÄND TagMonat, bool streng, Gruppen/Beschreibungen, neue Schlüssel,
                                Speichern ohne Änderung ohne Audit
  services/stammdaten.py   ÄND  Kundengruppen-Funktionen entfallen
  services/anfragen.py     ÄND  konto_angelegt ohne Gruppe, mitgliedschaft_beantragen
  services/online_buchung.py ÄND Rechnungskunden-Sperre
  services/lesestand.py    ÄND  Konto- und Tarif-Felder, markiere_rechnungskunden()
  services/benachrichtigung.py ÄND Mitgliedschaft freigeschaltet/beendet/Erinnerung, Antrag an Betreiber
  jobs.py                  ÄND  Tageslauf Mitgliedschaft (07:00)
  navigation.py            ÄND  Kunden: Kundenliste, Mitgliedsanträge, Mitglieder-Abgleich;
                                Stammdaten: Einstellungen; System: Klärung Mitgliedschaft
  routes/kunden.py         ÄND  Rechnungskunde, Mitgliedschaft, Anträge, Abgleich
  routes/stammdaten.py     ÄND  Kundengruppen nur ändern; Einstellungen
  routes/belegung.py       ÄND  Betreiberbuchung manuell mit Steuersatz und offener Rechnung
  routes/rechnungen.py     ÄND  Steuerzeilen im Detail
  routes/system.py         ÄND  Klärungsliste
  templates/…              ÄND/NEU (je Task aufgeführt)
  templates/mail/mitgliedschaft_freigeschaltet.txt, mitgliedschaft_beendet.txt,
                 mitgliedschaft_erinnerung.txt  NEU
alembic/versions/0010_kundengruppen_mitgliedschaft.py, 0011_steuer_je_position.py,
                 0012_gruppe_geklaert.py, 0013_mitglied_erinnert.py  NEU
core/tests/test_kundengruppen.py, test_mitgliedschaft.py, test_ui_mitgliedschaft.py  NEU
core/tests/… (bestehende Tests, je Task aufgeführt)  ÄND
portal/tests/hilfen.py     ÄND  Konto-Lesestand ohne zahlungsart
e2e/test_ablauf.py         ÄND  feste Gruppen
docs/betrieb/hauptsystem.md, README.md  ÄND
```

---
## Task 1: Feste Kundengruppen, Rechnungskunde, Gruppe zum Leistungsdatum

Der Kunde trägt keine Gruppe mehr. Seine Gruppe ergibt sich aus `mitglied_bis` und dem Tag des Termins; Preis, Gruppe und Steuersatz werden an der Buchung festgeschrieben (A-KUND-1, -2, -3, -6, A-TARIF-3). Die Zahlungsart wandert vom Kunden an die Buchung (A-ZAHL-1); am Kunden bleibt nur das Kennzeichen `rechnungskunde` (A-KUND-7).

**Files:**
- Modify: `core/beachhub_core/models/stammdaten.py` (Klasse `Kundengruppe`)
- Modify: `core/beachhub_core/models/kunden.py` (Klasse `Kunde`)
- Modify: `core/beachhub_core/models/buchungen.py` (Klasse `Buchung`)
- Create: `core/alembic/versions/0010_kundengruppen_mitgliedschaft.py`
- Create: `core/beachhub_core/services/kundengruppen.py`
- Modify: `core/beachhub_core/services/kunden.py`, `buchungen.py`, `dauerbuchungen.py`, `rechnungen.py` (Monatslauf), `anfragen.py`, `konfiguration.py`, `lesestand.py` (`baue_konto`), `stammdaten.py`
- Modify: `core/beachhub_core/routes/kunden.py`, `routes/stammdaten.py`, `routes/belegung.py` (`buchung_anlegen`)
- Modify: `core/beachhub_core/templates/kunden/liste.html`, `kunden/detail.html`, `stammdaten/kundengruppen.html`
- Create: `core/tests/test_kundengruppen.py`
- Modify (Tests, Liste in Step 9): `core/tests/*.py`, `e2e/test_ablauf.py`

**Interfaces:**
- Produces: `Kundengruppe(name: str, ust_satz: Decimal, ist_mitglied: bool)` – `ist_mitglied` eindeutig; `standard_zahlungsart` entfällt.
- Produces: `Kunde.rechnungskunde: bool`, `Kunde.mitglied_bis: date | None`, `mitglied_antrag_am: datetime | None`, `mitglied_antrag_hinweis: str`, `mitglied_freigeschaltet_am: datetime | None`, `mitglied_freigeschaltet_von: UUID | None`, `mitglied_beendet_am: datetime | None`, `mitglied_beendet_grund: str`. `Kunde.kundengruppe_id`, `Kunde.kundengruppe` und `Kunde.zahlungsart` entfallen.
- Produces: `Buchung.ust_satz: Decimal`, `Buchung.kundengruppe_id: UUID` (beide Pflicht); `Buchung.zahlungsart ∈ {"online", "saison", "manuell", "gutschein"}`.
- Produces: `services.kundengruppen` mit `VORGABEN`, `GruppenFehler`, `mitglied(db) -> Kundengruppe`, `nicht_mitglied(db) -> Kundengruppe`, `beide(db) -> list[Kundengruppe]` (Nicht-Mitglied zuerst), `ist_mitglied_am(kunde, datum: date) -> bool`, `effektive_gruppe(db, kunde, datum: date) -> Kundengruppe`, `aendere(db, gruppe, *, name: str, ust_satz: Decimal, admin_user_id) -> Kundengruppe`.
- Produces: `kunden.lege_an(db, *, name, email, rechnungskunde=False, adresse_strasse="", adresse_plz="", adresse_ort="", quelle="admin", admin_user_id=None) -> Kunde`; `kunden.aendere(...)` markiert zusätzlich `konto:<id>` im Lesestand.
- Produces: `buchungen.ZAHLUNGSARTEN`, `buchungen.lege_an(..., zahlungsart: str = "manuell", ust_satz: Decimal | None = None)` – Grund `zahlungsart_unbekannt` bei fremdem Wert.
- Produces: `rechnungen.MONATSLAUF_ZAHLUNGSARTEN = ("saison", "manuell")` (Task 3 kürzt auf `("saison",)`).
- Entfällt: Einstellung `portal_kundengruppe`, `anfragen._portal_gruppe`, Ablehnungsgrund `keine_kundengruppe`, `stammdaten.kundengruppe_anlegen/_aendern`, Route `POST /admin/kundengruppen`.

- [ ] **Step 1: Failing Tests für die Gruppen schreiben**

`core/tests/test_kundengruppen.py`:
```python
from datetime import date
from decimal import Decimal

import pytest
from beachhub_core.models import Audit, Kunde, Kundengruppe, LesestandVersion
from beachhub_core.services import kundengruppen
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_zwei_feste_gruppen_mit_vorgaben(db: Session) -> None:
    mitglied = kundengruppen.mitglied(db)
    nicht = kundengruppen.nicht_mitglied(db)
    db.commit()
    assert mitglied.ist_mitglied and mitglied.ust_satz == Decimal("7.00")
    assert not nicht.ist_mitglied and nicht.ust_satz == Decimal("19.00")
    assert kundengruppen.mitglied(db).id == mitglied.id  # kein zweites Anlegen
    assert db.query(Kundengruppe).count() == 2
    assert [g.id for g in kundengruppen.beide(db)] == [nicht.id, mitglied.id]


def test_dritte_gruppe_scheitert_am_eindeutigen_index(db: Session) -> None:
    kundengruppen.beide(db)
    db.commit()
    db.add(Kundengruppe(name="Verein", ust_satz=Decimal("19.00"), ist_mitglied=False))
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


@pytest.mark.parametrize(
    "mitglied_bis,datum,erwartet",
    [
        (date(2028, 3, 31), date(2028, 3, 31), True),
        (date(2028, 3, 31), date(2028, 4, 1), False),
        (None, date(2028, 3, 1), False),
    ],
)
def test_effektive_gruppe_folgt_mitglied_bis(
    db: Session, mitglied_bis: date | None, datum: date, erwartet: bool
) -> None:
    k = Kunde(name="A", email="a@x.de", mitglied_bis=mitglied_bis)
    assert kundengruppen.ist_mitglied_am(k, datum) is erwartet
    assert kundengruppen.effektive_gruppe(db, k, datum).ist_mitglied is erwartet


def test_aendere_name_und_satz_protokolliert(db: Session) -> None:
    g = kundengruppen.mitglied(db)
    db.commit()
    kundengruppen.aendere(
        db, g, name=" DJK Augsburg ", ust_satz=Decimal("7.50"), admin_user_id=None
    )
    db.commit()
    assert g.name == "DJK Augsburg" and g.ust_satz == Decimal("7.50")
    a = db.query(Audit).filter_by(objekt_typ="kundengruppe", objekt_id=g.id).one()
    assert a.vorher_json["ust_satz"] == "7.00" and a.nachher_json["ust_satz"] == "7.50"
    # Gruppennamen stehen im Tarif-Dokument des Portals.
    assert db.get(LesestandVersion, "tarife").geaendert


@pytest.mark.parametrize(
    "name,satz", [("", Decimal("7")), ("X", Decimal("100")), ("X", Decimal("-1"))]
)
def test_aendere_lehnt_ungueltiges_ab(db: Session, name: str, satz: Decimal) -> None:
    g = kundengruppen.nicht_mitglied(db)
    with pytest.raises(kundengruppen.GruppenFehler):
        kundengruppen.aendere(db, g, name=name, ust_satz=satz, admin_user_id=None)
```

In `core/tests/test_buchungen_service.py` ergänzen (Importe: `from beachhub_core.services import buchungen, kunden, kundengruppen, pin`):
```python
def test_preis_und_satz_nach_gruppe_am_termin(db: Session, welt) -> None:
    """Spec-Beispiel A-KUND-6: Mitglied bis 31.03., gebucht am 20.03. für den 05.04. – es gelten
    Preis und Steuersatz der Nicht-Mitglieder, festgeschrieben an der Buchung."""
    f, k = welt
    mitglied = kundengruppen.mitglied(db)
    db.add(Tarif(name="Mitglieder", preis=Decimal("20.00"), kundengruppe_id=mitglied.id))
    k.mitglied_bis = date(2028, 3, 31)
    db.commit()
    clock.set_override(db, date(2028, 3, 20))
    vorher = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(date(2028, 3, 31), time(19)),
        ende=kombiniere(date(2028, 3, 31), time(20)),
    )
    nachher = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(date(2028, 4, 5), time(19)),
        ende=kombiniere(date(2028, 4, 5), time(20)),
    )
    db.commit()
    assert (vorher.preis, vorher.ust_satz) == (Decimal("20.00"), Decimal("7.00"))
    assert vorher.kundengruppe_id == mitglied.id
    assert (nachher.preis, nachher.ust_satz) == (Decimal("30.00"), Decimal("19.00"))
    assert nachher.kundengruppe_id == kundengruppen.nicht_mitglied(db).id
    # Eine spätere Änderung des Gruppensatzes ändert bestehende Buchungen nicht (A-TARIF-3).
    kundengruppen.aendere(
        db, mitglied, name=mitglied.name, ust_satz=Decimal("5.00"), admin_user_id=None
    )
    db.commit()
    db.refresh(vorher)
    assert vorher.ust_satz == Decimal("7.00")


def test_unbekannte_zahlungsart_wirft(db: Session, welt) -> None:
    f, k = welt
    with pytest.raises(buchungen.BuchungsFehler, match="zahlungsart_unbekannt"):
        buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=k.id,
            beginn=kombiniere(D, time(19)),
            ende=kombiniere(D, time(20)),
            zahlungsart="rechnung",
        )
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_kundengruppen.py tests/test_buchungen_service.py`
Expected: FAIL – `ImportError: cannot import name 'kundengruppen'`.

- [ ] **Step 3: Modelle umstellen**

`core/beachhub_core/models/stammdaten.py`, Klasse `Kundengruppe` ersetzen:
```python
class Kundengruppe(UUIDMixin, ZeitstempelMixin, Base):
    """Genau zwei Zeilen: DJK-Mitglied und Nicht-Mitglied (A-KUND-2). Der Betreiber ändert Name
    und Steuersatz; eine dritte Gruppe verhindert der eindeutige Index auf `ist_mitglied`."""

    __tablename__ = "kundengruppe"
    name: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    ust_satz: Mapped[Decimal] = mapped_column(DECIMAL(5, 2), nullable=False)
    ist_mitglied: Mapped[bool] = mapped_column(Boolean, nullable=False, unique=True)
```

`core/beachhub_core/models/kunden.py` vollständig:
```python
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, Boolean, CheckConstraint, Date, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin


class Kunde(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "kunde"
    __table_args__ = (CheckConstraint("guthaben >= 0", name="kunde_guthaben_nicht_negativ"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    adresse_strasse: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    adresse_plz: Mapped[str] = mapped_column(String(10), default="", nullable=False)
    adresse_ort: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    # Rechnungskunden buchen nicht online; ihre Buchungen legt der Betreiber an (A-KUND-7).
    # Die Zahlungsart steht an der Buchung (A-ZAHL-1).
    rechnungskunde: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    guthaben: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2), default=Decimal("0.00"), nullable=False
    )
    portal_konto_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), unique=True)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100))
    anonymisiert_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Mitgliedschaft (A-KUND-4): Die Kundengruppe ist nicht gespeichert, sondern ergibt sich aus
    # mitglied_bis und dem Leistungsdatum (services/kundengruppen.effektive_gruppe).
    mitglied_bis: Mapped[date | None] = mapped_column(Date)
    mitglied_antrag_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mitglied_antrag_hinweis: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    mitglied_freigeschaltet_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mitglied_freigeschaltet_von: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    mitglied_beendet_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mitglied_beendet_grund: Mapped[str] = mapped_column(String(300), default="", nullable=False)


class GuthabenBuchung(UUIDMixin, ZeitstempelMixin, Base):
    __tablename__ = "guthaben_buchung"
    kunde_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False
    )
    betrag: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    art: Mapped[str] = mapped_column(String(20), nullable=False)
    bezug_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    notiz: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
```

`core/beachhub_core/models/buchungen.py`, in `Buchung` die Zeile `zahlungsart: …` ersetzen durch:
```python
    # Festgeschrieben bei der Anlage (A-TARIF-3): die Gruppe zum Leistungsdatum (A-KUND-6) und
    # ihr Steuersatz – bei Betreiberbuchungen der im Formular gewählte Satz (A-RECH-9).
    ust_satz: Mapped[Decimal] = mapped_column(DECIMAL(5, 2), nullable=False)
    kundengruppe_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kundengruppe.id"), nullable=False
    )
    zahlungsart: Mapped[str] = mapped_column(String(10), nullable=False)  # online|saison|manuell|gutschein
```

- [ ] **Step 4: Migration 0010 schreiben**

`core/alembic/versions/0010_kundengruppen_mitgliedschaft.py`:
```python
"""kundengruppen_mitgliedschaft: zwei feste Kundengruppen mit Steuersatz, Rechnungskunde und
Mitgliedschaft am Kunden, Gruppe und Steuersatz an der Buchung (Stufe 1a, A-KUND-1 bis -7).

Vorhandene Kundengruppen werden verworfen – bis Mitte 2027 gibt es keine produktiven Daten
(Projektentscheidung 24.09.2026): Tarife verlieren ihren Gruppenbezug, Buchungen kommen in die
Gruppe „Nicht-Mitglied“, Termine von Dauerbuchungen bekommen die Zahlungsart `saison`, übrige
Rechnungsbuchungen `manuell`.

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def _mitglied_spalten() -> list[sa.Column]:
    # Je Aufruf neue Column-Objekte: Alembic hängt eine Spalte an ihre Tabelle.
    return [
        sa.Column("mitglied_bis", sa.Date(), nullable=True),
        sa.Column("mitglied_antrag_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "mitglied_antrag_hinweis", sa.String(length=500), server_default="", nullable=False
        ),
        sa.Column("mitglied_freigeschaltet_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mitglied_freigeschaltet_von", sa.UUID(), nullable=True),
        sa.Column("mitglied_beendet_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "mitglied_beendet_grund", sa.String(length=300), server_default="", nullable=False
        ),
    ]


def upgrade() -> None:
    # Kunden und Tarife lösen sich von den alten Gruppen, bevor diese verschwinden.
    op.drop_column("kunde", "kundengruppe_id")
    op.drop_column("kunde", "zahlungsart")
    op.execute("UPDATE tarif SET kundengruppe_id = NULL")
    op.execute("DELETE FROM kundengruppe")
    op.drop_column("kundengruppe", "standard_zahlungsart")
    op.add_column(
        "kundengruppe", sa.Column("ust_satz", sa.DECIMAL(precision=5, scale=2), nullable=False)
    )
    op.add_column("kundengruppe", sa.Column("ist_mitglied", sa.Boolean(), nullable=False))
    op.create_unique_constraint("kundengruppe_ist_mitglied_key", "kundengruppe", ["ist_mitglied"])
    op.execute(
        "INSERT INTO kundengruppe (id, name, ust_satz, ist_mitglied, created_at, updated_at) "
        "VALUES (gen_random_uuid(), 'DJK-Mitglied', 7.00, true, now(), now()), "
        "(gen_random_uuid(), 'Nicht-Mitglied', 19.00, false, now(), now())"
    )

    op.add_column(
        "kunde",
        sa.Column("rechnungskunde", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.alter_column("kunde", "rechnungskunde", server_default=None)
    for spalte in _mitglied_spalten():
        op.add_column("kunde", spalte)
    op.alter_column("kunde", "mitglied_antrag_hinweis", server_default=None)
    op.alter_column("kunde", "mitglied_beendet_grund", server_default=None)

    # Buchungen: Gruppe und Satz festschreiben, Zahlungsarten auf die neue Bedeutung bringen.
    op.add_column("buchung", sa.Column("kundengruppe_id", sa.UUID(), nullable=True))
    op.add_column("buchung", sa.Column("ust_satz", sa.DECIMAL(precision=5, scale=2), nullable=True))
    op.execute(
        "UPDATE buchung SET ust_satz = 19.00, "
        "kundengruppe_id = (SELECT id FROM kundengruppe WHERE NOT ist_mitglied)"
    )
    op.alter_column("buchung", "kundengruppe_id", nullable=False)
    op.alter_column("buchung", "ust_satz", nullable=False)
    op.create_foreign_key(
        "buchung_kundengruppe_id_fkey", "buchung", "kundengruppe", ["kundengruppe_id"], ["id"]
    )
    op.execute("UPDATE buchung SET zahlungsart = 'saison' WHERE dauerbuchung_id IS NOT NULL")
    op.execute("UPDATE buchung SET zahlungsart = 'manuell' WHERE zahlungsart = 'rechnung'")
    op.execute("DELETE FROM konfiguration WHERE schluessel = 'portal_kundengruppe'")


def downgrade() -> None:
    op.drop_constraint("buchung_kundengruppe_id_fkey", "buchung", type_="foreignkey")
    op.drop_column("buchung", "ust_satz")
    op.drop_column("buchung", "kundengruppe_id")
    op.execute("UPDATE buchung SET zahlungsart = 'rechnung' WHERE zahlungsart IN ('saison', 'manuell')")
    for spalte in reversed(_mitglied_spalten()):
        op.drop_column("kunde", spalte.name)
    op.drop_column("kunde", "rechnungskunde")

    op.drop_constraint("kundengruppe_ist_mitglied_key", "kundengruppe", type_="unique")
    op.drop_column("kundengruppe", "ist_mitglied")
    op.drop_column("kundengruppe", "ust_satz")
    op.add_column(
        "kundengruppe",
        sa.Column(
            "standard_zahlungsart", sa.String(length=10), server_default="online", nullable=False
        ),
    )
    op.alter_column("kundengruppe", "standard_zahlungsart", server_default=None)
    op.add_column(
        "kunde",
        sa.Column("zahlungsart", sa.String(length=10), server_default="online", nullable=False),
    )
    op.alter_column("kunde", "zahlungsart", server_default=None)
    op.add_column("kunde", sa.Column("kundengruppe_id", sa.UUID(), nullable=True))
    op.execute("UPDATE kunde SET kundengruppe_id = (SELECT id FROM kundengruppe ORDER BY name LIMIT 1)")
    op.alter_column("kunde", "kundengruppe_id", nullable=False)
    op.create_foreign_key(
        "kunde_kundengruppe_id_fkey", "kunde", "kundengruppe", ["kundengruppe_id"], ["id"]
    )
```

- [ ] **Step 5: Dienst `kundengruppen` anlegen**

`core/beachhub_core/services/kundengruppen.py`:
```python
"""Die zwei festen Kundengruppen (A-KUND-2) und die Gruppe eines Kunden zum Leistungsdatum
(A-KUND-6). Der Kunde trägt keine Gruppe: Mitglied ist, wessen `mitglied_bis` den Tag des Termins
noch einschließt."""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core.models import Kunde, Kundengruppe
from beachhub_core.services import audit

# Gilt, wenn eine Gruppe fehlt (frische Datenbank ohne Migration, etwa in Tests). Im Betrieb legt
# Migration 0010 beide an.
VORGABEN: dict[bool, tuple[str, Decimal]] = {
    True: ("DJK-Mitglied", Decimal("7.00")),
    False: ("Nicht-Mitglied", Decimal("19.00")),
}


class GruppenFehler(Exception):  # noqa: N818
    pass


def _gruppe(db: Session, ist_mitglied: bool) -> Kundengruppe:
    g = db.scalar(select(Kundengruppe).where(Kundengruppe.ist_mitglied == ist_mitglied))
    if g is None:
        name, satz = VORGABEN[ist_mitglied]
        g = Kundengruppe(name=name, ust_satz=satz, ist_mitglied=ist_mitglied)
        db.add(g)
        db.flush()
    return g


def mitglied(db: Session) -> Kundengruppe:
    return _gruppe(db, True)


def nicht_mitglied(db: Session) -> Kundengruppe:
    return _gruppe(db, False)


def beide(db: Session) -> list[Kundengruppe]:
    """Beide Gruppen in fester Reihenfolge für Auswahllisten: Nicht-Mitglied zuerst."""
    return [nicht_mitglied(db), mitglied(db)]


def ist_mitglied_am(kunde: Kunde, datum: date) -> bool:
    return kunde.mitglied_bis is not None and datum <= kunde.mitglied_bis


def effektive_gruppe(db: Session, kunde: Kunde, datum: date) -> Kundengruppe:
    return mitglied(db) if ist_mitglied_am(kunde, datum) else nicht_mitglied(db)


def aendere(
    db: Session,
    gruppe: Kundengruppe,
    *,
    name: str,
    ust_satz: Decimal,
    admin_user_id: uuid.UUID | None,
) -> Kundengruppe:
    """Name und Steuersatz ändern. Der Satz gilt für danach entstehende Buchungen; bestehende
    tragen ihren eigenen (A-TARIF-3)."""
    name = name.strip()
    if not name:
        raise GruppenFehler("Name fehlt")
    if not Decimal("0") <= ust_satz < Decimal("100"):
        raise GruppenFehler("Steuersatz ungültig")
    vorher = audit.als_dict(gruppe)
    gruppe.name, gruppe.ust_satz = name, ust_satz
    db.flush()
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="kundengruppe",
        objekt_id=gruppe.id,
        vorher=vorher,
        nachher=audit.als_dict(gruppe),
        admin_user_id=admin_user_id,
    )
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, "tarife")
    return gruppe
```

- [ ] **Step 6: Dienste umstellen**

`core/beachhub_core/services/kunden.py` – Import `Kundengruppe` entfernen und `lege_an` ersetzen; am Ende von `aendere` (vor `return kunde`) das Konto-Dokument markieren:
```python
def lege_an(
    db: Session,
    *,
    name: str,
    email: str,
    rechnungskunde: bool = False,
    adresse_strasse: str = "",
    adresse_plz: str = "",
    adresse_ort: str = "",
    quelle: str = "admin",
    admin_user_id: uuid.UUID | None = None,
) -> Kunde:
    """Neue Kunden sind Nicht-Mitglied (A-KUND-3); die Mitgliedschaft schaltet der Betreiber frei."""
    email = email.strip().lower()
    if db.scalar(select(Kunde).where(Kunde.email == email)):
        raise KundenFehler("email_vergeben")
    k = Kunde(
        name=name.strip(),
        email=email,
        rechnungskunde=rechnungskunde,
        adresse_strasse=adresse_strasse,
        adresse_plz=adresse_plz,
        adresse_ort=adresse_ort,
    )
    db.add(k)
    db.flush()
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="kunde",
        objekt_id=k.id,
        vorher=None,
        nachher=audit.als_dict(k),
        admin_user_id=admin_user_id,
    )
    return k
```
```python
    # in aendere, nach audit.protokolliere(...):
    from beachhub_core.services import lesestand

    # Das Konto-Dokument trägt Rechnungskunde und Gruppe.
    lesestand.markiere_geaendert(db, f"konto:{kunde.id}")
    return kunde
```
Der Import lautet danach `from beachhub_core.models import Kunde, utcnow`.

`core/beachhub_core/services/buchungen.py`:
- Importe: `from decimal import Decimal`; `from beachhub_core.services import audit, konfiguration, kundengruppen, pin, slots_db, tarife`.
- Unter `NACH_ANLAGE` ergänzen:
```python
# Zahlungsart je Buchung (A-ZAHL-1): online über den Zahlungsdienst, saison über die Saisonrechnung
# einer Dauerbuchung, manuell vom Betreiber angelegt (Rechnung offen), gutschein vollständig durch
# Gutscheine gedeckt.
ZAHLUNGSARTEN: tuple[str, ...] = ("online", "saison", "manuell", "gutschein")
```
- Signatur von `lege_an`: Parameter `zahlungsart: str | None = None` ersetzen durch `zahlungsart: str = "manuell", ust_satz: Decimal | None = None`.
- Am Anfang von `lege_an`:
```python
    if zahlungsart not in ZAHLUNGSARTEN:
        raise BuchungsFehler("zahlungsart_unbekannt")
```
- Den Block `preis = tarife.ermittle_preis(...)` ersetzen durch:
```python
    # Preis und Satz nach der Gruppe am Tag des Termins, nicht am Buchungstag (A-KUND-6).
    gruppe = kundengruppen.effektive_gruppe(db, kunde, lokales_datum(beginn))
    preis = tarife.ermittle_preis(
        db, feld_id=feld_id, beginn=beginn, ende=ende, kundengruppe_id=gruppe.id
    )
```
- Im Konstruktor `Buchung(...)` die Zeile `zahlungsart=zahlungsart or kunde.zahlungsart,` ersetzen durch:
```python
        zahlungsart=zahlungsart,
        kundengruppe_id=gruppe.id,
        ust_satz=gruppe.ust_satz if ust_satz is None else ust_satz,
```

`core/beachhub_core/services/dauerbuchungen.py`:
- Import `kundengruppen` ergänzen (`from beachhub_core.services import audit, buchungen, konfiguration, kundengruppen, pin, sperren, tarife`).
- In `plane` den Aufruf von `tarife.ermittle_preis` ersetzen:
```python
                preis=tarife.ermittle_preis(
                    db,
                    feld_id=feld_id,
                    beginn=b,
                    ende=e,
                    kundengruppe_id=kundengruppen.effektive_gruppe(db, kunde, d).id,
                ),
```
- In `lege_an` beim Aufruf `buchungen.lege_an(...)` den Parameter `zahlungsart="saison",` ergänzen.

`core/beachhub_core/services/rechnungen.py` (Monatslauf, bis Plan 1a-II):
```python
# Zahlungsarten, die der Monatslauf sammelt. Plan 1a-II ersetzt den Monatslauf durch die
# Saisonrechnung (A-RECH-3).
MONATSLAUF_ZAHLUNGSARTEN: tuple[str, ...] = ("saison", "manuell")
```
In `abrechenbare_buchungen` `Buchung.zahlungsart == "rechnung",` ersetzen durch `Buchung.zahlungsart.in_(MONATSLAUF_ZAHLUNGSARTEN),`. `monatslauf` ersetzen:
```python
def monatslauf(db: Session, jahr: int, monat: int) -> list[Rechnung]:
    offen = select(Buchung.kunde_id).where(
        Buchung.zahlungsart.in_(MONATSLAUF_ZAHLUNGSARTEN),
        Buchung.rechnung_position_id.is_(None),
    )
    erzeugt = []
    for kunde in db.scalars(
        select(Kunde)
        .where(Kunde.id.in_(offen), Kunde.anonymisiert_am.is_(None))
        .order_by(Kunde.name)
    ).all():
        r = erzeuge_sammelrechnung(db, kunde, jahr, monat)
        if r:
            erzeugt.append(r)
    return erzeugt
```

`core/beachhub_core/services/anfragen.py`:
- `_portal_gruppe` löschen; Importe `Kundengruppe` und `konfiguration` entfernen.
- In `_konto_angelegt` den Block `if k is None: gruppe = _portal_gruppe(db) … k = kunden.lege_an(…)` ersetzen durch:
```python
        if k is None:
            # Neue Portalkonten sind Nicht-Mitglied und kein Rechnungskunde (A-KUND-3, A-KUND-7).
            k = kunden.lege_an(db, name=n.anzeigename.strip(), email=email, quelle="portal")
```

`core/beachhub_core/services/konfiguration.py`: Einträge `"portal_kundengruppe"` in `DEFAULTS` und `BESCHREIBUNGEN` löschen.

`core/beachhub_core/services/lesestand.py`:
- Import `from beachhub_core.services import konfiguration, kundengruppen, pin`.
- In `baue_konto` den `return` ersetzen:
```python
    gruppe = kundengruppen.effektive_gruppe(db, kunde, clock.today(db))
    return schema.KontoInhalt(
        kunde_id=str(kunde.id),
        kundengruppe=gruppe.name,
        zahlungsart="rechnung" if kunde.rechnungskunde else "online",
        guthaben=kunde.guthaben,
        buchungen=out,
        rechnungen=[
            schema.KontoRechnung(nummer=r.nummer, datum=r.datum, brutto=r.brutto, status=r.status)
            for r in rechnungen
        ],
    )
```

`core/beachhub_core/services/stammdaten.py`: `kundengruppe_anlegen` und `kundengruppe_aendern` löschen, `Kundengruppe` aus dem Import entfernen.

- [ ] **Step 7: Admin-Oberfläche umstellen**

`core/beachhub_core/routes/stammdaten.py`:
- Importe: `from beachhub_core.services import konfiguration, kundengruppen, stammdaten`; `from beachhub_core.services.kundengruppen import GruppenFehler`; `FORM_FEHLER = (StammdatenFehler, GruppenFehler, ValueError, InvalidOperation, IntegrityError)`.
- Den Abschnitt `# ---- Kundengruppen ----` vollständig ersetzen:
```python
# ---- Kundengruppen ----
@router.get("/kundengruppen", response_class=HTMLResponse)
def kundengruppen_seite(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
    fehler: str | None = None,
) -> HTMLResponse:
    gruppen = kundengruppen.beide(db)
    db.commit()  # legt fehlende Gruppen dauerhaft an (Abweichung A-2)
    return render(
        request, "stammdaten/kundengruppen.html", admin=admin, gruppen=gruppen, fehler=fehler
    )


@router.post("/kundengruppen/{gruppe_id}", response_model=None)
def kundengruppe_aendern(
    request: Request,
    gruppe_id: uuid.UUID,
    name: str = Form(...),
    ust_satz: str = Form(...),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    g = db.get(Kundengruppe, gruppe_id)
    if g is None:
        return _redirect("/admin/kundengruppen", "Gruppe nicht gefunden", "fehler")
    try:
        kundengruppen.aendere(
            db,
            g,
            name=name,
            ust_satz=pflicht(t_betrag(ust_satz), "Steuersatz"),
            admin_user_id=admin.id,
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return kundengruppen_seite(request, admin, db, fehler=fehlertext(e))
    return _redirect("/admin/kundengruppen", "Gespeichert")
```
- In `_tarif_ctx` `gruppen = db.scalars(select(Kundengruppe).order_by(Kundengruppe.name)).all()` ersetzen durch `gruppen = kundengruppen.beide(db)`.

`core/beachhub_core/templates/stammdaten/kundengruppen.html` vollständig:
```html
{% extends "base.html" %}{% block title %}Kundengruppen{% endblock %}
{% block content %}
<h1>Kundengruppen</h1>
<p class="hinweis">Es gibt genau zwei Gruppen. Welche für einen Kunden gilt, ergibt sich aus seiner Mitgliedschaft am Tag des Termins. Name und Steuersatz können Sie ändern; ein neuer Satz gilt für Buchungen, die danach entstehen.</p>
{% if fehler %}<p class="fehler">{{ fehler }}</p>{% endif %}
<div class="karte">
{% for g in gruppen %}<form id="kg-{{ g.id }}" method="post" action="/admin/kundengruppen/{{ g.id }}"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"></form>{% endfor %}
<table>
  <tr><th>Gilt für</th><th>Name</th><th>Umsatzsteuer (Prozent)</th><th></th></tr>
  {% for g in gruppen %}<tr>
    <td>{{ "Mitglieder" if g.ist_mitglied else "alle anderen" }}</td>
    <td><input form="kg-{{ g.id }}" name="name" value="{{ g.name }}" required></td>
    <td><input form="kg-{{ g.id }}" name="ust_satz" value="{{ g.ust_satz|wert }}" required></td>
    <td><button form="kg-{{ g.id }}">Speichern</button></td>
  </tr>{% endfor %}
</table>
</div>
{% endblock %}
```

`core/beachhub_core/routes/kunden.py`:
- Importe: `Kundengruppe` aus der Modell-Liste entfernen; `from beachhub_core.services import guthaben, kunden, kundengruppen`.
- `FEHLERTEXT`: Eintrag `"gruppe_unbekannt"` löschen.
- `_liste_ctx` ersetzen:
```python
def _liste_ctx(db: Session, q: str) -> dict:  # type: ignore[type-arg]
    stmt = select(Kunde).where(Kunde.anonymisiert_am.is_(None)).order_by(Kunde.name)
    if q:
        stmt = stmt.where(or_(Kunde.name.ilike(f"%{q}%"), Kunde.email.ilike(f"%{q}%")))
    return {"kunden": db.scalars(stmt.limit(200)).all(), "q": q}
```
- In `anlegen` die Formularparameter `kundengruppe_id` und `zahlungsart` durch `rechnungskunde: str = Form(""),` ersetzen und den Aufruf anpassen:
```python
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
```
- In `_detail_ctx` den Eintrag `"gruppen": …` ersetzen durch `"gruppe_heute": kundengruppen.effektive_gruppe(db, k, clock.today(db)),`.
- In `aendern` ebenso `kundengruppe_id`/`zahlungsart` durch `rechnungskunde: str = Form(""),` ersetzen; der Aufruf wird:
```python
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
```

`core/beachhub_core/templates/kunden/liste.html`, Tabelle und Formular ersetzen:
```html
<div class="karte">
<table>
  <tr><th>Name</th><th>E-Mail</th><th>Mitglied bis</th><th>Rechnungskunde</th><th>Guthaben</th><th></th></tr>
  {% for k in kunden %}<tr>
    <td>{{ k.name }}</td>
    <td>{{ k.email }}</td>
    <td>{{ k.mitglied_bis|datum if k.mitglied_bis else "–" }}</td>
    <td>{{ "ja" if k.rechnungskunde else "" }}</td>
    <td>{{ k.guthaben|euro }}</td>
    <td><a class="aktion" href="/admin/kunden/{{ k.id }}">öffnen</a></td>
  </tr>{% endfor %}
</table>
</div>
<div class="karte schmal">
<h2>Neuer Kunde</h2>
<p class="small">Neue Kunden sind Nicht-Mitglieder. Eine Mitgliedschaft schalten Sie auf der Kundenseite frei.</p>
<form method="post" action="/admin/kunden">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <label>Name <input name="name" required></label>
  <label>E-Mail <input name="email" type="email" required></label>
  <label><input type="checkbox" name="rechnungskunde" value="1"> Rechnungskunde – bucht nicht online, Buchungen legen Sie an</label>
  <label>Straße <input name="adresse_strasse"></label>
  <label>PLZ <input name="adresse_plz"></label>
  <label>Ort <input name="adresse_ort"></label>
  <button>Kunde anlegen</button>
</form>
</div>
```

`core/beachhub_core/templates/kunden/detail.html`, die beiden Auswahlfelder „Kundengruppe“ und „Zahlungsart“ ersetzen durch:
```html
  <p>Gruppe heute: <strong>{{ gruppe_heute.name }}</strong></p>
  <label><input type="checkbox" name="rechnungskunde" value="1" {% if kunde.rechnungskunde %}checked{% endif %}> Rechnungskunde – bucht nicht online</label>
```

`core/beachhub_core/routes/belegung.py`, in `buchung_anlegen` den Block `b = buchungen.lege_an(...)` bis `r = rechnungen.erzeuge_einzelrechnung(db, b)` ersetzen:
```python
        b = buchungen.lege_an(
            db,
            feld_id=uuid.UUID(feld_id),
            kunde_id=uuid.UUID(kunde_id),
            beginn=_lokal(beginn),
            ende=_lokal(ende),
            quelle="admin",
            admin_user_id=admin.id,
            zahlungsart="manuell",
        )
        # Rechnungskunden rechnet der Monatslauf ab; alle anderen bekommen sofort eine Rechnung.
        if not b.kunde.rechnungskunde:
            r = rechnungen.erzeuge_einzelrechnung(db, b)
```

- [ ] **Step 8: Bestehende Tests auf feste Gruppen umstellen**

Allgemeine Regeln für alle genannten Dateien:
1. `Kundengruppe(name=…)`-Objekte nicht mehr anlegen und aus `db.add_all([...])` entfernen. Wo ein Test die Gruppe selbst braucht: `kundengruppen.nicht_mitglied(db)` bzw. `kundengruppen.mitglied(db)` (`from beachhub_core.services import kundengruppen`).
2. Aus `kunden.lege_an(...)` das Argument `kundengruppe_id=…` streichen. War die Gruppe die frühere „Verein“-Gruppe mit `standard_zahlungsart="rechnung"`, stattdessen `rechnungskunde=True` übergeben.
3. Direkt gebaute `Kunde(...)` verlieren `kundengruppe_id=…` und `zahlungsart=…`.
4. Direkt gebaute `Buchung(...)` bekommen `ust_satz=Decimal("19.00"), kundengruppe_id=kundengruppen.nicht_mitglied(db).id`.
5. Nicht mehr benutzte Importe entfernt `ruff check --fix` (Regel F401).

Dateien:
- `core/tests/hilfen_halle.py`, `test_auth.py`, `test_lesestand.py`, `test_sperren.py`, `test_benachrichtigung.py`, `test_rechnung_pdf.py`, `test_ui_system.py`, `test_online_buchung.py`, `test_buchungen_service.py`: Regeln 1 und 2. Zusätzlich:
  - `test_buchungen_service.py::test_anlegen_setzt_preis_pin_status`: `b.zahlungsart == "online"` → `b.zahlungsart == "manuell"` (Vorgabe für Betreiberbuchungen).
  - `test_online_buchung.py::test_storno_dauerbuchungstermin_nicht_stornierbar`: `termin.zahlungsart == "online"` → `termin.zahlungsart == "saison"`.
- `test_jobs.py::test_monatslauf_fuer_erzeugt_pdf_und_mail`, `test_ui_rechnungen.py` (Fixture `welt`), `test_dauerbuchungen.py` (Fixture `welt`: nur `k` wird Rechnungskunde, `k2` nicht), `test_rechnungen.py`, `test_storno.py`, `test_ui_belegung.py`, `test_portal_grundlagen.py`: Regeln 1 und 2. Zusätzlich:
  - `test_dauerbuchungen.py::test_anlegen_mit_auslassen_und_gemeinsamer_pin`: `b.zahlungsart == "rechnung"` → `b.zahlungsart == "saison"`.
  - `test_storno.py::test_vor_frist_kostenfrei_mit_gutschrift`: im Aufruf `buchungen.lege_an(...)` `zahlungsart="online",` ergänzen – nur Onlinezahler bekommen Guthaben, und die Vorgabe ist jetzt `manuell`.
  - `test_portal_grundlagen.py`: `test_lege_an_mit_abweichender_zahlungsart` ersetzen durch
    ```python
    def test_zahlungsart_steht_an_der_buchung(db: Session, welt) -> None:
        f, k = welt
        b = buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=k.id,
            beginn=kombiniere(D, time(19)),
            ende=kombiniere(D, time(20)),
            zahlungsart="online",
        )
        assert k.rechnungskunde is True
        assert b.zahlungsart == "online"
    ```
    und `test_portal_kundengruppe_ist_konfigurierbar` löschen.
- `test_tarife.py`, Fixture `basis`: `privat, verein = kundengruppen.nicht_mitglied(db), kundengruppen.mitglied(db)` und `db.add(f)` statt `db.add_all([f, privat, verein])`.
- `test_kanal.py`: in `welt` `db.add(Kundengruppe(name="Privat"))` → `kundengruppen.beide(db)`; in `test_verteilen_ohne_portal_konto_wird_nicht_gesendet` und `test_abgleich_ohne_portal_konto_wird_nicht_gesendet` die Zeile `gruppe = db.scalar(select(Kundengruppe))` löschen und `kunde = kunden.lege_an(db, name="Abo", email="abo@x.de")` schreiben.
- `test_anfragen.py`:
  - Fixture `welt`: `db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])`; vor `clock.set_override(...)`:
    ```python
    gruppen = kundengruppen.nicht_mitglied(db), kundengruppen.mitglied(db)
    db.commit()
    ```
    und `return f, *gruppen`.
  - `test_konto_angelegt_legt_kunden_an` ersetzen:
    ```python
    def test_konto_angelegt_legt_kunden_an(db: Session, welt) -> None:
        konto = uuid.uuid4()
        k = _angelegt(db, konto, email="  Anna@X.de ")
        assert k.email == "anna@x.de" and k.name == "Anna"
        # Neue Portalkonten sind Nicht-Mitglied und kein Rechnungskunde (A-KUND-3, A-KUND-7).
        assert k.portal_konto_id == konto and k.rechnungskunde is False and k.mitglied_bis is None
        assert db.get(LesestandVersion, f"konto:{k.id}").geaendert
    ```
  - `test_konto_angelegt_nutzt_konfigurierte_gruppe` löschen.
  - Übrige `kunden.lege_an(..., kundengruppe_id=…)`: Regel 2.
- `test_buchungen_modell.py` vollständig:
  ```python
  from datetime import date, time
  from decimal import Decimal

  import pytest
  from beachhub_core.models import Buchung, Feld, Kunde
  from beachhub_core.services import kundengruppen
  from beachhub_shared.zeit import kombiniere
  from sqlalchemy.exc import IntegrityError
  from sqlalchemy.orm import Session


  def _basis(db: Session) -> tuple[Feld, Kunde]:
      f = Feld(name="F1", reihenfolge=1)
      k = Kunde(name="A", email="a@x.de")
      db.add_all([f, k])
      db.flush()
      return f, k


  def _buchung(
      db: Session, f: Feld, k: Kunde, von: int, bis: int, status: str = "bestaetigt"
  ) -> Buchung:
      d = date(2027, 12, 1)
      return Buchung(
          feld_id=f.id,
          kunde_id=k.id,
          beginn=kombiniere(d, time(von)),
          ende=kombiniere(d, time(bis)),
          status=status,
          preis=Decimal("30"),
          ust_satz=Decimal("19.00"),
          kundengruppe_id=kundengruppen.nicht_mitglied(db).id,
          zahlungsart="manuell",
          quelle="admin",
      )


  def test_ueberlappung_wird_von_datenbank_abgelehnt(db: Session) -> None:
      f, k = _basis(db)
      db.add(_buchung(db, f, k, 19, 21))
      db.commit()
      db.add(_buchung(db, f, k, 20, 22))
      with pytest.raises(IntegrityError):
          db.commit()
      db.rollback()


  def test_angrenzend_und_storniert_erlaubt(db: Session) -> None:
      f, k = _basis(db)
      db.add(_buchung(db, f, k, 19, 21))
      db.add(_buchung(db, f, k, 21, 23))
      db.add(_buchung(db, f, k, 20, 22, status="storniert"))
      db.commit()
      assert db.query(Buchung).count() == 3
  ```
- `test_pin.py`: in beiden Tests `g = Kundengruppe(name="Privat")` löschen, `db.add_all([g, f])` → `db.add(f)`, `Kunde(name="A", email="a@x.de")`; in `test_finde_freien_vermeidet_kollision` bekommt `Buchung(...)` `ust_satz=Decimal("19.00"), kundengruppe_id=kundengruppen.nicht_mitglied(db).id` und `zahlungsart="manuell"`.
- `test_kunden.py` vollständig:
  ```python
  from decimal import Decimal

  import pytest
  from beachhub_core.models import Audit, GuthabenBuchung, Kunde, LesestandVersion
  from beachhub_core.services import guthaben, kunden
  from sqlalchemy import update
  from sqlalchemy.exc import IntegrityError
  from sqlalchemy.orm import Session


  def test_anlegen_normalisiert_und_ist_kein_rechnungskunde(db: Session) -> None:
      k = kunden.lege_an(db, name=" TSV ", email="Info@TSV.de")
      db.commit()
      assert k.name == "TSV" and k.email == "info@tsv.de" and k.guthaben == Decimal("0.00")
      assert k.rechnungskunde is False and k.mitglied_bis is None


  def test_anlegen_als_rechnungskunde(db: Session) -> None:
      k = kunden.lege_an(db, name="TSV", email="v@x.de", rechnungskunde=True)
      db.commit()
      assert k.rechnungskunde is True


  def test_email_doppelt_wirft(db: Session) -> None:
      kunden.lege_an(db, name="A", email="a@x.de")
      db.commit()
      with pytest.raises(kunden.KundenFehler, match="email_vergeben"):
          kunden.lege_an(db, name="B", email="A@x.de")


  def test_guthaben_buchen_und_deckung(db: Session) -> None:
      k = kunden.lege_an(db, name="A", email="a@x.de")
      guthaben.buche(db, kunde=k, betrag=Decimal("30.00"), art="storno_gutschrift")
      guthaben.buche(db, kunde=k, betrag=Decimal("-10.00"), art="verrechnung")
      db.commit()
      assert k.guthaben == Decimal("20.00") == guthaben.saldo(db, k.id)
      with pytest.raises(guthaben.GuthabenFehler, match="nicht_gedeckt"):
          guthaben.buche(db, kunde=k, betrag=Decimal("-25.00"), art="auszahlung")
      assert db.query(GuthabenBuchung).count() == 2
      assert db.query(Audit).filter_by(objekt_typ="guthaben").count() == 2


  def test_aendern_protokolliert_und_markiert_konto(db: Session) -> None:
      k = kunden.lege_an(db, name="A", email="a@x.de")
      db.commit()
      kunden.aendere(db, k, admin_user_id=None, rechnungskunde=True)
      db.commit()
      a = db.query(Audit).filter_by(objekt_typ="kunde").order_by(Audit.zeitpunkt.desc()).first()
      assert a.vorher_json["rechnungskunde"] is False and a.nachher_json["rechnungskunde"] is True
      assert db.get(LesestandVersion, f"konto:{k.id}").geaendert


  def test_anonymisieren(db: Session) -> None:
      k = kunden.lege_an(db, name="Anna Müller", email="anna@x.de")
      kunden.anonymisiere(db, k)
      db.commit()
      assert k.name == "Gelöschter Kunde" and "@" not in k.email and k.anonymisiert_am is not None


  def test_aendern_auf_vergebene_email_wirft(db: Session) -> None:
      _ = kunden.lege_an(db, name="A", email="a@x.de")
      k2 = kunden.lege_an(db, name="B", email="b@x.de")
      db.commit()
      with pytest.raises(kunden.KundenFehler, match="email_vergeben"):
          kunden.aendere(db, k2, admin_user_id=None, email="A@x.de")
      db.rollback()
      db.refresh(k2)
      assert k2.email == "b@x.de"


  def test_check_constraint_verhindert_negatives_guthaben(db: Session) -> None:
      k = kunden.lege_an(db, name="A", email="a@x.de")
      db.commit()
      with pytest.raises(IntegrityError):
          db.execute(update(Kunde).values(guthaben=Decimal("-1")).where(Kunde.id == k.id))
          db.commit()
      db.rollback()
  ```
- `test_ui_kunden.py`: die vier bestehenden Tests ohne Gruppen- und Zahlungsartfelder; Hilfsfunktion und neuer Test:
  ```python
  def _neu(c: TestClient, **extra: str) -> dict[str, str]:
      return {
          "csrf_token": c.csrf,
          "name": "Anna Müller",
          "email": "Anna@X.de",
          "adresse_strasse": "Weg 1",
          "adresse_plz": "12345",
          "adresse_ort": "Ort",
          **extra,
      }


  def test_rechnungskunde_anlegen_und_aendern(eingeloggt: TestClient, db: Session) -> None:
      c = eingeloggt
      c.post("/admin/kunden", data=_neu(c, rechnungskunde="1"))
      k = db.query(Kunde).one()
      assert k.rechnungskunde is True
      seite = c.get(f"/admin/kunden/{k.id}")
      assert 'name="rechnungskunde" value="1" checked' in seite.text
      assert "Gruppe heute: <strong>Nicht-Mitglied</strong>" in seite.text
      r = c.post(
          f"/admin/kunden/{k.id}",
          data={
              "csrf_token": c.csrf,
              "name": k.name,
              "email": k.email,
              "adresse_strasse": "",
              "adresse_plz": "",
              "adresse_ort": "",
          },
          follow_redirects=False,
      )
      assert r.status_code == 303
      db.refresh(k)
      assert k.rechnungskunde is False
  ```
  `test_kunde_anlegen_suchen_guthaben` postet `_neu(c)` und prüft `k.email == "anna@x.de" and k.rechnungskunde is False`; `test_doppelte_email_zeigt_fehler` und `test_kunde_aendern_und_anonymisieren` verlieren die Schlüssel `kundengruppe_id` und `zahlungsart` sowie das Anlegen der Gruppe; in `test_detail_seite_mit_buchungen_rechnungen_guthaben` wird `Kunde(name="Carla Voss", email="carla@x.de")` gebaut und `Buchung(...)` nach Regel 4 ergänzt (`zahlungsart="manuell"`).
- `test_ui_stammdaten.py`: `test_kundenseite_verweist_ohne_gruppen_auf_kundengruppen` ersetzen durch
  ```python
  def test_kundengruppen_name_und_satz_aendern(eingeloggt: TestClient, db: Session) -> None:
      c = eingeloggt
      seite = c.get("/admin/kundengruppen")
      assert "Nicht-Mitglied" in seite.text and "DJK-Mitglied" in seite.text
      g = kundengruppen.mitglied(db)
      r = c.post(
          f"/admin/kundengruppen/{g.id}",
          data={"csrf_token": c.csrf, "name": "DJK", "ust_satz": "7,5"},
          follow_redirects=False,
      )
      assert r.status_code == 303
      db.refresh(g)
      assert g.name == "DJK" and g.ust_satz == Decimal("7.50")
      r = c.post(
          f"/admin/kundengruppen/{g.id}",
          data={"csrf_token": c.csrf, "name": "DJK", "ust_satz": "120"},
      )
      assert r.status_code == 200 and "Steuersatz ungültig" in r.text
      # Eine dritte Gruppe lässt sich nicht anlegen.
      assert c.post("/admin/kundengruppen", data={"csrf_token": c.csrf}).status_code == 405
  ```
  (Importe: `from beachhub_core.services import kundengruppen`.)
- `e2e/test_ablauf.py`: in `feld_id` `db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])`; im Tarif-Szenario `privat = kundengruppen.nicht_mitglied(db)`, `verein = kundengruppen.mitglied(db)` und `db.add_all([f, feld2])` (Import `from beachhub_core.services import kundengruppen`; die Kunden des Portals sind Nicht-Mitglieder, die Regel für `privat` greift also weiterhin).

- [ ] **Step 9: Neue Tests grün**

Run: `cd core && pytest -q tests/test_kundengruppen.py tests/test_buchungen_service.py`
Expected: PASS.

- [ ] **Step 10: Gesamte Suite und Lint**

Run:
```bash
(cd shared && pytest -q) && (cd core && pytest -q) && (cd portal && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS, keine Lint- oder Typfehler. Schlägt `test_migrationen.py` fehl, Spaltennamen und Constraint-Namen in 0010 mit den Modellen vergleichen.

- [ ] **Step 11: Commit**

```bash
git add core e2e
git commit -m "feat(core): feste Kundengruppen, Rechnungskunde und Gruppe zum Leistungsdatum"
```

---
## Task 2: Steuersatz je Rechnungsposition

Eine Rechnung kann Positionen mit 7 % und 19 % enthalten. Der Satz steht an der Position, Netto und Steuer werden je Position gerundet und je Satz summiert (A-RECH-8). Der Rechnungskopf verliert seinen Satz; die globale Einstellung `ust_satz` entfällt, weil jeder Satz aus der Buchung kommt.

**Files:**
- Modify: `core/beachhub_core/models/rechnungen.py`
- Create: `core/alembic/versions/0011_steuer_je_position.py`
- Modify: `core/beachhub_core/services/rechnungen.py`, `services/rechnung_pdf.py`, `services/konfiguration.py`
- Modify: `core/beachhub_core/routes/rechnungen.py` (`detail`)
- Modify: `core/beachhub_core/templates/rechnung_pdf.html`, `templates/rechnungen/detail.html`
- Test: `core/tests/test_rechnungen.py`, `test_ui_rechnungen.py`, `test_konfiguration.py`, `test_ui_stammdaten.py`, `test_ui_kunden.py`

**Interfaces:**
- Consumes: `Buchung.ust_satz` (Task 1).
- Produces: `RechnungPosition.netto: Decimal`, `RechnungPosition.ust: Decimal`; `Rechnung.ust_satz` entfällt.
- Produces: `rechnungen.Posten(buchung: Buchung | None, text: str, brutto: Decimal, ust_satz: Decimal)` (frozen dataclass), `rechnungen.SteuerZeile(ust_satz, netto, ust, brutto)` (frozen dataclass), `rechnungen.netto_ust(brutto: Decimal, satz: Decimal) -> tuple[Decimal, Decimal]`, `rechnungen.steuer_je_satz(r: Rechnung) -> list[SteuerZeile]` (aufsteigend nach Satz), `rechnungen._neue_rechnung(db, kunde, art, posten: Sequence[Posten], leistung_von, leistung_bis, status, *, quelle) -> Rechnung`.
- Produces: `rechnung_pdf.html(rechnung: Rechnung) -> str`.
- Produces: CSV-Export mit **einer Zeile je Rechnung und Steuersatz**, Spalten `nummer;datum;kunde;art;status;ust_satz;netto;ust;brutto;faellig_am;bezahlt_am;leistung_von;leistung_bis`.
- Entfällt: Einstellung `ust_satz`.

- [ ] **Step 1: Failing Tests schreiben**

In `core/tests/test_rechnungen.py` (Importe ergänzen: `from beachhub_core.services import buchungen, konfiguration, kunden, rechnung_pdf, rechnungen, storno`):
```python
def _zwei_saetze(db: Session, welt) -> Rechnung:
    """Sammelrechnung mit einem Termin als Mitglied (7 %) und einem danach (19 %)."""
    f, _, v1 = welt
    v1.mitglied_bis = date(2027, 12, 5)
    db.commit()
    for tag in (1, 8):
        buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=v1.id,
            beginn=kombiniere(date(2027, 12, tag), time(19)),
            ende=kombiniere(date(2027, 12, tag), time(20)),
            zahlungsart="saison",
        )
    db.commit()
    clock.set_override(db, date(2028, 1, 3))
    r = rechnungen.erzeuge_sammelrechnung(db, v1, 2027, 12)
    db.commit()
    assert r is not None
    return r


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
    db.query(Tarif).update({Tarif.preis: Decimal("10.00")})
    db.commit()
    for stunde in (17, 18, 19):
        buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=v1.id,
            beginn=kombiniere(date(2027, 12, 1), time(stunde)),
            ende=kombiniere(date(2027, 12, 1), time(stunde + 1)),
            zahlungsart="saison",
        )
    db.commit()
    clock.set_override(db, date(2028, 1, 3))
    r = rechnungen.erzeuge_sammelrechnung(db, v1, 2027, 12)
    db.commit()
    assert [p.netto for p in r.positionen] == [Decimal("8.40")] * 3
    assert (r.netto, r.ust, r.brutto) == (Decimal("25.20"), Decimal("4.80"), Decimal("30.00"))


def test_storno_negiert_je_satz(db: Session, welt) -> None:
    r = _zwei_saetze(db, welt)
    s = rechnungen.storniere(db, r, admin_user_id=None, grund="Test")
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
```
Bestehende Tests in derselben Datei anpassen:
- `test_csv_export`: Kopfzeile und Betragsspalten:
  ```python
  assert zeilen[0].startswith("nummer;datum;kunde;art;status;ust_satz;netto;ust;brutto")
  assert len(zeilen) == 2 and ";19,00;25,21;4,79;30,00;" in zeilen[1]
  ```
- `test_buchung_kann_nicht_doppelt_berechnet_werden`: `RechnungPosition(...)` bekommt `netto=Decimal("25.21"), ust=Decimal("4.79"),`.

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_rechnungen.py`
Expected: FAIL – `AttributeError: 'RechnungPosition' object has no attribute 'netto'` bzw. `module 'beachhub_core.services.rechnung_pdf' has no attribute 'html'`.

- [ ] **Step 3: Modell und Migration**

`core/beachhub_core/models/rechnungen.py`:
- In `Rechnung` die Zeile `ust_satz: Mapped[Decimal] = …` löschen. Die Kommentarzeile an `art` lautet `# einzel | sammel | storno`.
- In `RechnungPosition` nach `ust_satz` ergänzen:
```python
    # Je Position gerundet (A-RECH-8); der Rechnungskopf summiert Netto, Steuer und Brutto.
    netto: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    ust: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
```

`core/alembic/versions/0011_steuer_je_position.py`:
```python
"""steuer_je_position: Netto und Steuer je Rechnungsposition, kein Satz am Rechnungskopf
(A-RECH-8). Die Einstellung ust_satz entfällt; jeder Satz kommt aus der Buchung.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for spalte in ("netto", "ust"):
        op.add_column(
            "rechnung_position",
            sa.Column(spalte, sa.DECIMAL(precision=10, scale=2), server_default="0", nullable=False),
        )
    # round() auf numeric rundet in PostgreSQL kaufmännisch – wie ROUND_HALF_UP im Code.
    op.execute("UPDATE rechnung_position SET netto = round(brutto / (1 + ust_satz / 100), 2)")
    op.execute("UPDATE rechnung_position SET ust = brutto - netto")
    for spalte in ("netto", "ust"):
        op.alter_column("rechnung_position", spalte, server_default=None)
    op.drop_column("rechnung", "ust_satz")
    op.execute("DELETE FROM konfiguration WHERE schluessel = 'ust_satz'")


def downgrade() -> None:
    op.add_column(
        "rechnung",
        sa.Column(
            "ust_satz", sa.DECIMAL(precision=5, scale=2), server_default="19.00", nullable=False
        ),
    )
    op.execute(
        "UPDATE rechnung r SET ust_satz = p.satz FROM (SELECT rechnung_id, max(ust_satz) AS satz "
        "FROM rechnung_position GROUP BY rechnung_id) p WHERE p.rechnung_id = r.id"
    )
    op.alter_column("rechnung", "ust_satz", server_default=None)
    op.drop_column("rechnung_position", "ust")
    op.drop_column("rechnung_position", "netto")
```

- [ ] **Step 4: Rechnungsdienst auf Posten mit Satz umstellen**

`core/beachhub_core/services/rechnungen.py`:
- Importe ergänzen: `from dataclasses import dataclass`.
- Unter `CENT = Decimal("0.01")`:
```python
NULL = Decimal("0.00")


@dataclass(frozen=True)
class Posten:
    """Eine Rechnungsposition vor dem Anlegen. Netto und Steuer rechnet `_neue_rechnung` je
    Position (A-RECH-8)."""

    buchung: Buchung | None
    text: str
    brutto: Decimal
    ust_satz: Decimal


@dataclass(frozen=True)
class SteuerZeile:
    ust_satz: Decimal
    netto: Decimal
    ust: Decimal
    brutto: Decimal
```
- `_netto_ust` umbenennen in die öffentliche Funktion `netto_ust` (gleicher Rumpf) und darunter ergänzen:
```python
def steuer_je_satz(r: Rechnung) -> list[SteuerZeile]:
    """Netto, Steuer und Brutto je Steuersatz, aufsteigend nach Satz – für PDF, Detailseite und
    CSV-Export. Summiert die je Position gerundeten Beträge."""
    summen: dict[Decimal, tuple[Decimal, Decimal, Decimal]] = {}
    for p in r.positionen:
        netto, ust, brutto = summen.get(p.ust_satz, (NULL, NULL, NULL))
        summen[p.ust_satz] = (netto + p.netto, ust + p.ust, brutto + p.brutto)
    return [SteuerZeile(satz, *werte) for satz, werte in sorted(summen.items())]
```
- `_neue_rechnung` ersetzen:
```python
def _neue_rechnung(
    db: Session,
    kunde: Kunde,
    art: str,
    posten: Sequence[Posten],
    leistung_von: date,
    leistung_bis: date,
    status: str,
    *,
    quelle: str,
) -> Rechnung:
    heute = clock.today(db)
    zeilen = [(p, *netto_ust(p.brutto, p.ust_satz)) for p in posten]
    r = Rechnung(
        nummer=naechste_nummer(db, heute.year),
        kunde_id=kunde.id,
        art=art,
        datum=heute,
        leistung_von=leistung_von,
        leistung_bis=leistung_bis,
        faellig_am=heute + timedelta(days=konfiguration.hole(db, "rechnung_zahlungsziel_tage")),
        netto=sum((netto for _, netto, _ in zeilen), NULL),
        ust=sum((ust for _, _, ust in zeilen), NULL),
        brutto=sum((p.brutto for p in posten), NULL),
        status=status,
        bezahlt_am=utcnow() if status == "bezahlt" else None,
        adresse_snapshot=_snapshot(kunde),
    )
    db.add(r)
    db.flush()
    for i, (p, netto, ust) in enumerate(zeilen, start=1):
        pos = RechnungPosition(
            rechnung_id=r.id,
            reihenfolge=i,
            buchung_id=p.buchung.id if p.buchung else None,
            text=p.text,
            menge=1,
            einzelpreis_brutto=p.brutto,
            ust_satz=p.ust_satz,
            netto=netto,
            ust=ust,
            brutto=p.brutto,
        )
        db.add(pos)
        db.flush()
        if p.buchung is not None and art != "storno":
            p.buchung.rechnung_position_id = pos.id
    db.flush()
    db.refresh(r)
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="rechnung",
        objekt_id=r.id,
        vorher=None,
        nachher=audit.als_dict(r),
    )
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{kunde.id}")
    return r
```
- In `erzeuge_einzelrechnung` die Positionsliste: `[Posten(buchung, _positionstext(buchung), buchung.preis, buchung.ust_satz)]`.
- In `erzeuge_sammelrechnung`:
```python
    positionen = [
        Posten(
            b,
            _positionstext(b, " (Storno nach Frist)" if b.status == Buchung.STORNIERT else ""),
            betrag,
            b.ust_satz,
        )
        for b, betrag in posten
    ]
```
- In `storniere`:
```python
    positionen = [
        Posten(None, f"Storno zu Rechnung {rechnung.nummer}: {p.text}", -p.brutto, p.ust_satz)
        for p in rechnung.positionen
    ]
```
- `csv_export` ersetzen:
```python
def csv_export(db: Session, von: date, bis: date) -> str:
    """Eine Zeile je Rechnung und Steuersatz, damit die Buchhaltung beide Sätze getrennt erhält."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(
        [
            "nummer",
            "datum",
            "kunde",
            "art",
            "status",
            "ust_satz",
            "netto",
            "ust",
            "brutto",
            "faellig_am",
            "bezahlt_am",
            "leistung_von",
            "leistung_bis",
        ]
    )
    for r in db.scalars(
        select(Rechnung)
        .where(Rechnung.datum >= von, Rechnung.datum <= bis)
        .order_by(Rechnung.nummer)
    ):
        for z in steuer_je_satz(r):
            w.writerow(
                [
                    r.nummer,
                    r.datum.isoformat(),
                    _csv_sicher(r.adresse_snapshot.get("name", "")),
                    r.art,
                    r.status,
                    _de(z.ust_satz),
                    _de(z.netto),
                    _de(z.ust),
                    _de(z.brutto),
                    r.faellig_am.isoformat(),
                    r.bezahlt_am.isoformat() if r.bezahlt_am else "",
                    r.leistung_von.isoformat(),
                    r.leistung_bis.isoformat(),
                ]
            )
    return buf.getvalue()
```

`core/beachhub_core/services/konfiguration.py`: Einträge `"ust_satz"` in `DEFAULTS` und `BESCHREIBUNGEN` löschen.

- [ ] **Step 5: PDF und Detailseite**

`core/beachhub_core/services/rechnung_pdf.py`:
- Import `from beachhub_core.services import rechnungen` ergänzen.
- Neue Funktion und Aufruf in `erzeuge`:
```python
def html(rechnung: Rechnung) -> str:
    """Das HTML der Rechnung, aus dem `erzeuge` das PDF macht."""
    return templates.env.get_template("rechnung_pdf.html").render(
        r=rechnung, steuer=rechnungen.steuer_je_satz(rechnung), betreiber=_betreiber()
    )
```
In `erzeuge` die Zeilen `html = templates.env.get_template(...).render(...)` und `daten = HTML(string=html).write_pdf()` ersetzen durch `daten = HTML(string=html(rechnung)).write_pdf()`.

`core/beachhub_core/templates/rechnung_pdf.html`, die beiden Tabellen ersetzen:
```html
<table>
  <thead><tr><th>Pos.</th><th>Leistung</th><th class="r">USt</th><th class="r">Betrag</th></tr></thead>
  <tbody>{% for p in r.positionen %}<tr><td>{{ p.reihenfolge }}</td><td>{{ p.text }}</td><td class="r">{{ p.ust_satz|prozent }}</td><td class="r">{{ p.brutto|euro }}</td></tr>{% endfor %}</tbody>
</table>
<table class="summe">
  {% for z in steuer %}
  <tr><td></td><td class="r">Nettobetrag {{ z.ust_satz|prozent }}</td><td class="r">{{ z.netto|euro }}</td></tr>
  <tr><td></td><td class="r">zzgl. {{ z.ust_satz|prozent }} USt</td><td class="r">{{ z.ust|euro }}</td></tr>
  {% endfor %}
  <tr class="gesamt"><td></td><td class="r">Gesamtbetrag</td><td class="r">{{ r.brutto|euro }}</td></tr>
</table>
```

`core/beachhub_core/routes/rechnungen.py`, in `detail` an `render(...)` `steuer=rechnungen.steuer_je_satz(r),` übergeben.

`core/beachhub_core/templates/rechnungen/detail.html`, die Karte „Positionen“ ersetzen:
```html
<div class="karte">
  <h2>Positionen</h2>
  <table>
    <tr><th>Text</th><th class="rechts">USt</th><th class="rechts">Menge</th><th class="rechts">Brutto</th></tr>
    {% for p in r.positionen %}<tr>
      <td>{{ p.text }}</td>
      <td class="rechts">{{ p.ust_satz|prozent }}</td>
      <td class="rechts">{{ p.menge }}</td>
      <td class="rechts">{{ p.brutto|euro }}</td>
    </tr>{% endfor %}
  </table>
  <table>
    <tr><th>Steuersatz</th><th class="rechts">Netto</th><th class="rechts">USt</th><th class="rechts">Brutto</th></tr>
    {% for z in steuer %}<tr>
      <td>{{ z.ust_satz|prozent }}</td>
      <td class="rechts">{{ z.netto|euro }}</td>
      <td class="rechts">{{ z.ust|euro }}</td>
      <td class="rechts">{{ z.brutto|euro }}</td>
    </tr>{% endfor %}
    <tr>
      <td><strong>Gesamt</strong></td>
      <td class="rechts">{{ r.netto|euro }}</td>
      <td class="rechts">{{ r.ust|euro }}</td>
      <td class="rechts"><strong>{{ r.brutto|euro }}</strong></td>
    </tr>
  </table>
</div>
```

- [ ] **Step 6: Übrige Tests anpassen**

- `core/tests/test_konfiguration.py`: statt `ust_satz` die Dezimal-Einstellung `spiel_temperatur` verwenden – in `test_default_wird_typisiert_geliefert` `assert konfiguration.hole(db, "spiel_temperatur") == Decimal("18.0")`; in `test_dezimalwert_nimmt_komma_und_punkt` überall `"ust_satz"` → `"spiel_temperatur"` (Werte und Erwartungen bleiben).
- `core/tests/test_ui_stammdaten.py::test_tarif_und_konfiguration`: den Schlüssel `"ust_satz": "19.00",` aus den Formulardaten löschen.
- `core/tests/test_ui_kunden.py::test_detail_seite_mit_buchungen_rechnungen_guthaben`: im `Rechnung(...)` das Argument `ust_satz=Decimal("19.00"),` löschen.
- `core/tests/test_ui_rechnungen.py::test_rechnungen_detail_zeigt_positionen_und_integritaet`: zusätzlich `assert "19 %" in seite.text`.

- [ ] **Step 7: Tests und Lint**

Run:
```bash
(cd core && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 8: Commit**

```bash
git add core
git commit -m "feat(core): Steuersatz je Rechnungsposition, Summen je Satz in PDF und Export"
```

---
## Task 3: Betreiberbuchung mit Event-Steuersatz und offener Rechnung, Seite „Einstellungen“

Legt der Betreiber eine Buchung an, hat sie die Zahlungsart `manuell` und bekommt sofort eine offene Rechnung (A-ZAHL-1, A-KUND-7). Der Steuersatz ist mit `event_ust_satz` (19 %) vorbelegt und im Formular änderbar – auch für Mitglieder (A-RECH-9, vom Steuerberater bestätigt). Der Monatslauf sammelt ab jetzt nur noch Termine von Dauerbuchungen. Die Konfigurationsseite heißt „Einstellungen“ (A-ADM-9), meldet Eingabefehler verständlich und protokolliert nur echte Änderungen.

**Files:**
- Modify: `core/beachhub_core/services/konfiguration.py`, `services/rechnungen.py`
- Modify: `core/beachhub_core/routes/belegung.py` (`buchung_neu`, `buchung_anlegen`), `routes/stammdaten.py` (Konfiguration)
- Modify: `core/beachhub_core/navigation.py`
- Modify: `core/beachhub_core/templates/belegung/buchung_neu.html`, `belegung/buchung.html`, `stammdaten/konfiguration.html`
- Test: `core/tests/test_ui_belegung.py`, `test_konfiguration.py`, `test_ui_stammdaten.py`, `test_rechnungen.py`, `test_jobs.py`, `test_ui_rechnungen.py`

**Interfaces:**
- Consumes: `buchungen.lege_an(..., zahlungsart, ust_satz)` (Task 1), `rechnungen.Posten` (Task 2).
- Produces: Einstellung `event_ust_satz: Decimal = 19.00` (Gruppe „Zahlung und Rechnung“).
- Produces: `rechnungen.erzeuge_einzelrechnung(db, buchung, *, quelle="system", status="bezahlt") -> Rechnung` – `status="offen"` für Betreiberbuchungen.
- Produces: `rechnungen.MONATSLAUF_ZAHLUNGSARTEN = ("saison",)`.
- Produces: `konfiguration.setze` schreibt nichts (keine Zeile, kein Audit), wenn sich der wirksame Wert nicht ändert; `konfiguration._parse` meldet `ValueError("Bitte eine ganze Zahl angeben")` bzw. `ValueError("Bitte eine Zahl angeben")`.
- Produces: Formularfeld `ust_satz` in `POST /admin/belegung/buchung` (leer = `event_ust_satz`; erlaubt 0 bis unter 100).

- [ ] **Step 1: Failing Tests schreiben**

In `core/tests/test_ui_belegung.py` (Importe ergänzen: `Rechnung` aus `beachhub_core.models`):
```python
def test_betreiberbuchung_mit_eigenem_steuersatz(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    a.mitglied_bis = date(2028, 4, 30)  # auch für Mitglieder gilt der Event-Satz (A-RECH-9)
    db.commit()
    c = eingeloggt
    formular = c.get(f"/admin/belegung/buchung/neu?feld={f.id}&beginn=2027-12-01T19:00")
    assert 'name="ust_satz" value="19"' in formular.text
    r = c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
            "ust_satz": "7",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    b = db.query(Buchung).one()
    assert b.zahlungsart == "manuell" and b.ust_satz == Decimal("7.00")
    rechnung = db.query(Rechnung).one()
    assert rechnung.status == "offen" and rechnung.positionen[0].ust_satz == Decimal("7.00")


def test_betreiberbuchung_ungueltiger_steuersatz(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    c = eingeloggt
    r = c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
            "ust_satz": "120",
        },
    )
    assert "Steuersatz ungültig" in r.text
    assert db.query(Buchung).count() == 0
```
`test_woche_zeigt_slots_und_buchung`: die Prüfung nach dem Anlegen ersetzen durch
```python
    b = db.query(Buchung).one()
    # Betreiberbuchung: Zahlungsart manuell, Event-Satz, sofort eine offene Rechnung.
    assert b.preis == Decimal("60.00") and b.zahlungsart == "manuell"
    assert b.ust_satz == Decimal("19.00") and b.rechnung_position_id is not None
    assert db.query(Rechnung).one().status == "offen"
```
(die Mail-Prüfung danach bleibt).

In `core/tests/test_konfiguration.py`:
```python
def test_speichern_ohne_aenderung_schreibt_nichts(db: Session) -> None:
    konfiguration.setze(db, "storno_frist_stunden", 24)  # entspricht der Vorgabe
    konfiguration.setze(db, "storno_frist_stunden", 48)
    konfiguration.setze(db, "storno_frist_stunden", "48")
    db.commit()
    assert db.query(Audit).filter_by(objekt_typ="konfiguration").count() == 1


def test_event_ust_satz_hat_vorgabe_19(db: Session) -> None:
    assert konfiguration.hole(db, "event_ust_satz") == Decimal("19.00")
    assert konfiguration.BESCHREIBUNGEN["event_ust_satz"].gruppe == "Zahlung und Rechnung"


@pytest.mark.parametrize(
    "schluessel,roh,meldung",
    [("storno_frist_stunden", "abc", "ganze Zahl"), ("event_ust_satz", "x", "Zahl angeben")],
)
def test_ungueltige_werte_mit_verstaendlicher_meldung(
    db: Session, schluessel: str, roh: str, meldung: str
) -> None:
    with pytest.raises(ValueError, match=meldung):
        konfiguration.setze(db, schluessel, roh)
```
(`import pytest` an den Dateianfang verschieben.)

In `core/tests/test_ui_stammdaten.py`:
```python
def test_einstellungen_seite(eingeloggt: TestClient) -> None:
    text = eingeloggt.get("/admin/konfiguration").text
    assert "<h1>Einstellungen</h1>" in text
    assert "Steuersatz für Betreiberbuchungen" in text
```
und in `test_konfiguration_ungueltiger_wert` zusätzlich `assert "Stornofrist" in r.text and "ganze Zahl" in r.text`.

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_ui_belegung.py tests/test_konfiguration.py tests/test_ui_stammdaten.py`
Expected: FAIL – u. a. `KeyError: 'event_ust_satz'`, `'name="ust_satz"' not in …`.

- [ ] **Step 3: Einstellungen**

`core/beachhub_core/services/konfiguration.py`:
- Imports: `from decimal import Decimal, InvalidOperation`.
- In `DEFAULTS` nach `"rechnung_zahlungsziel_tage"`: `"event_ust_satz": (Decimal, Decimal("19.00")),`
- In `BESCHREIBUNGEN` nach `"rechnung_zahlungsziel_tage"`:
```python
    "event_ust_satz": Beschreibung(
        "Zahlung und Rechnung",
        "Steuersatz für Betreiberbuchungen",
        "Prozent",
        "Vorbelegung, wenn Sie eine Buchung oder ein Event selbst anlegen – auch wenn ein "
        "Mitglied bucht. Im Buchungsformular können Sie ihn im Einzelfall ändern.",
    ),
```
- `_parse` ersetzen:
```python
def _parse(typ: type, roh: str) -> Any:
    if typ is bool:
        return roh.lower() in ("1", "true", "ja")
    if typ is Decimal:
        # Die Oberfläche zeigt Dezimalzahlen deutsch mit Komma und bekommt sie so zurück.
        try:
            return Decimal(roh.strip().replace(",", "."))
        except InvalidOperation as e:
            raise ValueError("Bitte eine Zahl angeben") from e
    if typ is int:
        try:
            return int(roh.strip())
        except ValueError as e:
            raise ValueError("Bitte eine ganze Zahl angeben") from e
    return typ(roh)
```
- In `setze` nach `neu = str(_parse(typ, str(wert)))`:
```python
    aktuell = zeile.wert if zeile else str(_parse(typ, str(default)))
    if neu == aktuell:
        # Unverändert: keine Zeile, kein Audit-Eintrag. Ein leeres Formularfeld und der
        # Vorgabewert bleiben dadurch gleichwertig.
        return
```

`core/beachhub_core/routes/stammdaten.py`, Abschnitt `# ---- Konfiguration ----` ersetzen:
```python
# ---- Einstellungen (A-ADM-9) ----
def _einstellungen(request: Request, admin: AdminUser, db: Session, fehler: str | None = None) -> HTMLResponse:
    werte = {k: konfiguration.hole(db, k) for k in konfiguration.DEFAULTS}
    return render(
        request,
        "stammdaten/konfiguration.html",
        admin=admin,
        gruppen=konfiguration.gruppiert(werte),
        defaults=konfiguration.DEFAULTS,
        fehler=fehler,
    )


@router.get("/konfiguration", response_class=HTMLResponse)
def konfiguration_seite(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return _einstellungen(request, admin, db)


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
                konfiguration.setze(db, schluessel, wert, admin_user_id=admin.id)
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        name = konfiguration.BESCHREIBUNGEN[schluessel].name
        return _einstellungen(request, admin, db, fehler=f"{name}: {fehlertext(e)}")
    return _redirect("/admin/konfiguration", "Einstellungen gespeichert")
```
(`setze` wandelt Komma selbst; das frühere `wert.replace(",", ".")` hier entfällt.)

`core/beachhub_core/templates/stammdaten/konfiguration.html`, Kopf ersetzen:
```html
{% extends "base.html" %}{% block title %}Einstellungen{% endblock %}
{% block content %}
<h1>Einstellungen</h1>
<p class="hinweis">Alle Fristen, Werte und Grundsatzentscheidungen des Betriebs. Ein leeres Feld bedeutet: Es gilt der Vorgabewert, der als hellgrauer Text im Feld steht. Jede Änderung wird protokolliert und gilt für Buchungen und Belege, die danach entstehen.</p>
```
(der Rest der Vorlage bleibt).

`core/beachhub_core/navigation.py`: `Punkt("Konfiguration", "/admin/konfiguration")` → `Punkt("Einstellungen", "/admin/konfiguration")`.

- [ ] **Step 4: Betreiberbuchung**

`core/beachhub_core/services/rechnungen.py`:
```python
# Zahlungsarten, die der Monatslauf sammelt: nur noch Termine von Dauerbuchungen. Plan 1a-II
# ersetzt ihn durch die Saisonrechnung (A-RECH-3).
MONATSLAUF_ZAHLUNGSARTEN: tuple[str, ...] = ("saison",)
```
und `erzeuge_einzelrechnung`:
```python
def erzeuge_einzelrechnung(
    db: Session, buchung: Buchung, *, quelle: str = "system", status: str = "bezahlt"
) -> Rechnung:
    """Online bezahlte Buchungen bekommen eine bezahlte Rechnung (A-RECH-2), Betreiberbuchungen
    eine offene mit Zahlungsziel (A-ZAHL-1)."""
    if buchung.rechnung_position_id is not None:
        raise RechnungsFehler("bereits_berechnet")
    d = lokales_datum(buchung.beginn)
    return _neue_rechnung(
        db,
        buchung.kunde,
        "einzel",
        [Posten(buchung, _positionstext(buchung), buchung.preis, buchung.ust_satz)],
        d,
        d,
        status,
        quelle=quelle,
    )
```

`core/beachhub_core/routes/belegung.py`:
- Importe: `from decimal import Decimal`; `from beachhub_core.routes._form import fehlertext, pflicht, t_betrag, t_datum, t_zeit`; `konfiguration` in die Service-Importe.
- `GRUND` ergänzen: `"zahlungsart_unbekannt": "Zahlungsart unbekannt",`.
- Hilfsfunktion über `# ---- Woche ----`:
```python
def _satz(db: Session, roh: str) -> Decimal:
    """Steuersatz aus dem Buchungsformular; leer heißt Vorgabe für Betreiberbuchungen."""
    satz = t_betrag(roh)
    if satz is None:
        return Decimal(konfiguration.hole(db, "event_ust_satz"))
    if not Decimal("0") <= satz < Decimal("100"):
        raise ValueError("Steuersatz ungültig")
    return satz
```
- In `buchung_neu` an `render(...)` `ust_vorgabe=konfiguration.hole(db, "event_ust_satz"),` übergeben.
- `buchung_anlegen`: Parameter `ust_satz: str = Form(""),` ergänzen; der `try`-Block wird:
```python
    try:
        b = buchungen.lege_an(
            db,
            feld_id=uuid.UUID(feld_id),
            kunde_id=uuid.UUID(kunde_id),
            beginn=_lokal(beginn),
            ende=_lokal(ende),
            quelle="admin",
            admin_user_id=admin.id,
            zahlungsart="manuell",
            ust_satz=_satz(db, ust_satz),
        )
        # Betreiberbuchungen werden nicht online bezahlt: sofort eine offene Rechnung (A-ZAHL-1).
        r = rechnungen.erzeuge_einzelrechnung(db, b, status="offen")
        db.commit()
```
  Die Variable `r = None` vor dem `try` und die Bedingung `if r is not None:` beim PDF bleiben unverändert.

`core/beachhub_core/templates/belegung/buchung_neu.html`, vor dem Knopf:
```html
  <label>Steuersatz (Prozent) <input name="ust_satz" value="{{ ust_vorgabe|wert }}" required></label>
  <p class="small">Vorbelegt mit dem Satz für Betreiberbuchungen aus den Einstellungen. Die Buchung bekommt sofort eine offene Rechnung.</p>
```

`core/beachhub_core/templates/belegung/buchung.html`, unter der Zeile mit dem Preis:
```html
  <p>Steuersatz: {{ b.ust_satz|prozent }}</p>
```

- [ ] **Step 5: Tests, die den Monatslauf für Betreiberbuchungen erwarteten**

Der Monatslauf sammelt nur noch `saison`. In diesen Aufrufen von `buchungen.lege_an(...)` `zahlungsart="saison",` ergänzen:
- `core/tests/test_rechnungen.py`: alle Buchungen in `test_sammelrechnung_und_monatslauf_idempotent` (vier Aufrufe), `test_stornorechnung_gibt_buchungen_frei`, `test_setze_bezahlt_und_nicht_offen`.
- `core/tests/test_jobs.py::test_monatslauf_fuer_erzeugt_pdf_und_mail`.
- `core/tests/test_ui_rechnungen.py`, Fixture `welt`.

- [ ] **Step 6: Tests und Lint**

Run:
```bash
(cd core && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 7: Commit**

```bash
git add core
git commit -m "feat(core): Betreiberbuchung mit Event-Steuersatz und offener Rechnung, Einstellungen"
```

---
## Task 4: Mitgliedschaft verwalten – Freischalten, Verlängern, Beenden, Anträge

Der Betreiber schaltet die Mitgliedschaft auf der Kundenseite frei oder verlängert sie (vorbelegt mit dem nächsten Ablauftag, Einstellung `mitgliedschaft_ablauf`, Vorgabe 30.04.), beendet sie vorzeitig oder verwirft einen offenen Antrag (A-KUND-4). Der Kunde bekommt eine Mail bei Freischaltung und Ende (A-MAIL-2). Offene Anträge stehen auf einer eigenen Seite. Für jährliche Stichtage bekommen die Einstellungen den Typ `TagMonat`.

**Files:**
- Modify: `core/beachhub_core/services/konfiguration.py`
- Create: `core/beachhub_core/services/mitgliedschaft.py`
- Modify: `core/beachhub_core/services/benachrichtigung.py`
- Create: `core/beachhub_core/templates/mail/mitgliedschaft_freigeschaltet.txt`, `mail/mitgliedschaft_beendet.txt`, `templates/kunden/antraege.html`
- Modify: `core/beachhub_core/routes/kunden.py`, `templates/kunden/detail.html`, `navigation.py`
- Create: `core/tests/test_mitgliedschaft.py`, `core/tests/test_ui_mitgliedschaft.py`
- Modify: `core/tests/test_konfiguration.py`, `test_ui_stammdaten.py`, `test_ui_navigation.py`

**Interfaces:**
- Consumes: `kundengruppen.ist_mitglied_am` (Task 1), `konfiguration.setze` ohne Leeränderung (Task 3).
- Produces: `konfiguration.TagMonat(str)` – Konstruktor validiert `"T.M."`/`"TT.MM."` und normalisiert auf `"TT.MM."`; Eigenschaften `tag`, `monat`; `im_jahr(jahr: int) -> date`; `ValueError` mit deutscher Meldung bei ungültiger Eingabe (auch `29.02.`).
- Produces: Einstellung `mitgliedschaft_ablauf: TagMonat = "30.04."` (Gruppe „Mitgliedschaft“); `GRUPPEN` enthält „Mitgliedschaft“ nach „Zahlung und Rechnung“.
- Produces: `services.mitgliedschaft` mit `MITGLIED`, `BEANTRAGT`, `NICHT_MITGLIED` (Strings `"mitglied"`, `"beantragt"`, `"nicht_mitglied"`), `MitgliedschaftsFehler`, `naechster_ablauf(db, nach: date) -> date` (echt nach `nach`), `letzter_ablauf(db, bis: date) -> date` (am oder vor `bis`), `status(kunde, heute: date) -> str`, `freischalten(db, kunde, *, bis: date, admin_user_id) -> None`, `beende(db, kunde, *, grund: str, admin_user_id) -> bool` (True, wenn die Mitgliedschaft bis heute galt), `verwerfe_antrag(db, kunde, *, admin_user_id) -> None`, `offene_antraege(db) -> list[Kunde]`, `_protokolliere(db, kunde, vorher, aktion, *, quelle, admin_user_id) -> None`. Fehlergründe: `kunde_anonymisiert`, `bis_vergangen`, `kein_mitglied`, `kein_antrag`.
- Produces: `benachrichtigung.mitgliedschaft_freigeschaltet(db, kunde)`, `benachrichtigung.mitgliedschaft_beendet(db, kunde)` – Betreffs „Ihre Mitgliedschaft ist freigeschaltet“ bzw. „Ihre Mitgliedschaft wurde beendet“.
- Produces: Routen `GET /admin/kunden/antraege`, `POST /admin/kunden/{id}/mitgliedschaft` (Feld `bis`), `POST /admin/kunden/{id}/mitgliedschaft/beenden` (Feld `grund`), `POST /admin/kunden/{id}/mitgliedschaft/antrag-verwerfen`.
- Produces: Navigation „Kunden“ mit Unterpunkten „Kundenliste“ (`/admin/kunden`) und „Mitgliedsanträge“ (`/admin/kunden/antraege`).

- [ ] **Step 1: Failing Tests schreiben**

In `core/tests/test_konfiguration.py` (Import `from datetime import date`):
```python
@pytest.mark.parametrize(
    "roh,erwartet", [("30.04.", "30.04."), ("1.5", "01.05."), (" 31.08 ", "31.08.")]
)
def test_tagmonat_normalisiert(roh: str, erwartet: str) -> None:
    tm = konfiguration.TagMonat(roh)
    assert tm == erwartet
    assert tm.im_jahr(2028) == date(2028, tm.monat, tm.tag)


@pytest.mark.parametrize("roh", ["31.02.", "29.02.", "30-04", "", "13.13."])
def test_tagmonat_lehnt_ungueltige_tage_ab(roh: str) -> None:
    with pytest.raises(ValueError):
        konfiguration.TagMonat(roh)


def test_stichtag_speichern_und_lesen(db: Session) -> None:
    assert konfiguration.hole(db, "mitgliedschaft_ablauf") == "30.04."
    konfiguration.setze(db, "mitgliedschaft_ablauf", "1.5.")
    db.commit()
    wert = konfiguration.hole(db, "mitgliedschaft_ablauf")
    assert isinstance(wert, konfiguration.TagMonat)
    assert wert.im_jahr(2028) == date(2028, 5, 1)
```

In `core/tests/test_ui_stammdaten.py`:
```python
def test_einstellungen_ungueltiger_stichtag_zeigt_meldung(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    r = c.post(
        "/admin/konfiguration", data={"csrf_token": c.csrf, "mitgliedschaft_ablauf": "31.02."}
    )
    assert r.status_code == 200
    assert "Ablauf der Mitgliedschaft" in r.text and "nicht in jedem Jahr" in r.text
    assert db.query(Konfiguration).filter_by(schluessel="mitgliedschaft_ablauf").first() is None
```

`core/tests/test_mitgliedschaft.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Audit,
    Betriebszeit,
    Feld,
    FeldRaster,
    LesestandVersion,
    Tarif,
    utcnow,
)
from beachhub_core.services import buchungen, konfiguration, kunden, kundengruppen, mitgliedschaft
from beachhub_shared.zeit import kombiniere
from sqlalchemy.orm import Session

HEUTE = date(2027, 11, 25)


@pytest.fixture
def welt(db: Session):
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    mitglied = kundengruppen.mitglied(db)
    db.add_all(
        [
            f,
            Tarif(name="Std", preis=Decimal("30.00")),
            Tarif(name="Mitglieder", preis=Decimal("20.00"), kundengruppe_id=mitglied.id),
        ]
    )
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="Anna", email="anna@x.de")
    db.commit()
    clock.set_override(db, HEUTE)
    return f, k


@pytest.mark.parametrize(
    "tag,naechster,letzter",
    [
        (date(2027, 11, 25), date(2028, 4, 30), date(2027, 4, 30)),
        (date(2028, 4, 30), date(2029, 4, 30), date(2028, 4, 30)),
        (date(2028, 1, 1), date(2028, 4, 30), date(2027, 4, 30)),
    ],
)
def test_ablauftage(db: Session, tag: date, naechster: date, letzter: date) -> None:
    assert mitgliedschaft.naechster_ablauf(db, tag) == naechster
    assert mitgliedschaft.letzter_ablauf(db, tag) == letzter


def test_ablauftag_folgt_der_einstellung(db: Session) -> None:
    konfiguration.setze(db, "mitgliedschaft_ablauf", "31.03.")
    assert mitgliedschaft.naechster_ablauf(db, date(2027, 11, 25)) == date(2028, 3, 31)


def test_freischalten_setzt_status_und_preis(db: Session, welt) -> None:
    f, k = welt
    k.mitglied_antrag_am = utcnow()
    k.mitglied_antrag_hinweis = "Nr. 4711"
    db.commit()
    assert mitgliedschaft.status(k, HEUTE) == "beantragt"
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    db.commit()
    assert mitgliedschaft.status(k, HEUTE) == "mitglied"
    # Der Antrag ist erledigt, die Angaben daraus (Mitgliedsnummer) bleiben stehen.
    assert k.mitglied_antrag_am is None and k.mitglied_antrag_hinweis == "Nr. 4711"
    assert k.mitglied_freigeschaltet_am is not None
    a = (
        db.query(Audit)
        .filter_by(objekt_typ="kunde", objekt_id=k.id)
        .order_by(Audit.zeitpunkt.desc())
        .first()
    )
    assert a.nachher_json["aktion"] == "mitglied_freigeschaltet"
    assert a.nachher_json["mitglied_bis"] == "2028-04-30"
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
        zahlungsart="online",
    )
    assert b.preis == Decimal("20.00") and b.ust_satz == Decimal("7.00")


def test_freischalten_lehnt_vergangenes_datum_ab(db: Session, welt) -> None:
    _, k = welt
    with pytest.raises(mitgliedschaft.MitgliedschaftsFehler, match="bis_vergangen"):
        mitgliedschaft.freischalten(db, k, bis=date(2027, 11, 24), admin_user_id=None)


def test_beenden_wirkt_sofort(db: Session, welt) -> None:
    _, k = welt
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    db.commit()
    assert mitgliedschaft.beende(db, k, grund="ausgetreten", admin_user_id=None) is True
    db.commit()
    assert k.mitglied_bis == date(2027, 11, 24) and k.mitglied_beendet_grund == "ausgetreten"
    assert mitgliedschaft.status(k, HEUTE) == "nicht_mitglied"


def test_beenden_einer_abgelaufenen_mitgliedschaft_vermerkt_nur(db: Session, welt) -> None:
    _, k = welt
    k.mitglied_bis = date(2027, 4, 30)
    db.commit()
    assert mitgliedschaft.beende(db, k, grund="nicht verlängert", admin_user_id=None) is False
    assert k.mitglied_bis == date(2027, 4, 30) and k.mitglied_beendet_am is not None


def test_beenden_ohne_mitgliedschaft_wirft(db: Session, welt) -> None:
    _, k = welt
    with pytest.raises(mitgliedschaft.MitgliedschaftsFehler, match="kein_mitglied"):
        mitgliedschaft.beende(db, k, grund="x", admin_user_id=None)


def test_antrag_verwerfen(db: Session, welt) -> None:
    _, k = welt
    with pytest.raises(mitgliedschaft.MitgliedschaftsFehler, match="kein_antrag"):
        mitgliedschaft.verwerfe_antrag(db, k, admin_user_id=None)
    k.mitglied_antrag_am = utcnow()
    db.commit()
    assert mitgliedschaft.offene_antraege(db) == [k]
    mitgliedschaft.verwerfe_antrag(db, k, admin_user_id=None)
    db.commit()
    assert mitgliedschaft.offene_antraege(db) == []
    assert mitgliedschaft.status(k, HEUTE) == "nicht_mitglied"
```

`core/tests/test_ui_mitgliedschaft.py`:
```python
from datetime import date

from beachhub_core import clock
from beachhub_core.models import Kunde, utcnow
from beachhub_core.services import kunden, mitgliedschaft
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _kunde(db: Session) -> Kunde:
    clock.set_override(db, date(2027, 11, 25))
    k = kunden.lege_an(db, name="Anna", email="anna@x.de")
    db.commit()
    return k


def test_freischalten_ueber_kundenseite(
    eingeloggt: TestClient, db: Session, mail_ausgang: list
) -> None:
    c = eingeloggt
    k = _kunde(db)
    seite = c.get(f"/admin/kunden/{k.id}")
    assert 'name="bis" value="2028-04-30"' in seite.text and "Freischalten" in seite.text
    r = c.post(
        f"/admin/kunden/{k.id}/mitgliedschaft",
        data={"csrf_token": c.csrf, "bis": "2028-04-30"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(k)
    assert k.mitglied_bis == date(2028, 4, 30)
    assert [m["betreff"] for m in mail_ausgang] == ["Ihre Mitgliedschaft ist freigeschaltet"]
    assert "30.04.2028" in mail_ausgang[0]["text"]
    assert "Mitglied bis <strong>30.04.2028</strong>" in c.get(f"/admin/kunden/{k.id}").text


def test_freischalten_mit_vergangenem_datum_zeigt_meldung(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    k = _kunde(db)
    r = c.post(
        f"/admin/kunden/{k.id}/mitgliedschaft", data={"csrf_token": c.csrf, "bis": "2027-01-01"}
    )
    assert r.status_code == 200 and "Vergangenheit" in r.text
    db.refresh(k)
    assert k.mitglied_bis is None


def test_beenden_ueber_kundenseite(
    eingeloggt: TestClient, db: Session, mail_ausgang: list
) -> None:
    c = eingeloggt
    k = _kunde(db)
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    db.commit()
    r = c.post(
        f"/admin/kunden/{k.id}/mitgliedschaft/beenden",
        data={"csrf_token": c.csrf, "grund": "ausgetreten"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(k)
    assert k.mitglied_bis == date(2027, 11, 24)
    assert [m["betreff"] for m in mail_ausgang] == ["Ihre Mitgliedschaft wurde beendet"]


def test_antraege_seite_und_verwerfen(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    k = _kunde(db)
    k.mitglied_antrag_am = utcnow()
    k.mitglied_antrag_hinweis = "Mitgliedsnummer 4711"
    db.commit()
    seite = c.get("/admin/kunden/antraege")
    assert "Anna" in seite.text and "4711" in seite.text
    assert "Antrag offen" in c.get(f"/admin/kunden/{k.id}").text
    r = c.post(
        f"/admin/kunden/{k.id}/mitgliedschaft/antrag-verwerfen",
        data={"csrf_token": c.csrf},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "Keine offenen Anträge" in c.get("/admin/kunden/antraege").text
```

In `core/tests/test_ui_navigation.py`: `"/admin/kunden/antraege"` in `SEITEN` ergänzen; `test_bereich_ohne_unterpunkte_zeigt_keine_zweite_zeile` prüft künftig `/admin/rechnungen` statt `/admin/kunden`.

- [ ] **Step 2: Tests laufen lassen, Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_konfiguration.py tests/test_mitgliedschaft.py tests/test_ui_mitgliedschaft.py tests/test_ui_navigation.py`
Expected: FAIL – `AttributeError: module 'beachhub_core.services.konfiguration' has no attribute 'TagMonat'`, `ImportError: cannot import name 'mitgliedschaft'`.

- [ ] **Step 3: Typ `TagMonat` und Einstellung**

`core/beachhub_core/services/konfiguration.py`:
- Importe: `import re`, `from datetime import date`.
- Vor `DEFAULTS`:
```python
class TagMonat(str):
    """Ein jährlicher Stichtag ohne Jahr, gespeichert als "TT.MM." (etwa "30.04.")."""

    __slots__ = ()

    def __new__(cls, roh: str) -> "TagMonat":
        m = re.fullmatch(r"\s*(\d{1,2})\.(\d{1,2})\.?\s*", roh)
        if m is None:
            raise ValueError("Bitte als Tag und Monat angeben, etwa 30.04.")
        tag, monat = int(m.group(1)), int(m.group(2))
        try:
            # 2027 ist kein Schaltjahr: Der 29.02. taugt nicht als jährlicher Stichtag.
            date(2027, monat, tag)
        except ValueError as e:
            raise ValueError("Diesen Tag gibt es nicht in jedem Jahr") from e
        return super().__new__(cls, f"{tag:02d}.{monat:02d}.")

    @property
    def tag(self) -> int:
        return int(self[:2])

    @property
    def monat(self) -> int:
        return int(self[3:5])

    def im_jahr(self, jahr: int) -> date:
        return date(jahr, self.monat, self.tag)
```
- In `DEFAULTS` nach `"event_ust_satz"`: `"mitgliedschaft_ablauf": (TagMonat, TagMonat("30.04.")),`
- `_TYP_NAME` ergänzen: `TagMonat: "tagmonat"`.
- In `BESCHREIBUNGEN` nach `"event_ust_satz"`:
```python
    "mitgliedschaft_ablauf": Beschreibung(
        "Mitgliedschaft",
        "Ablauf der Mitgliedschaft",
        "Tag und Monat",
        "Bis zu diesem Tag gilt eine Freischaltung als Mitglied (Ende der Wintermitgliedschaft). "
        "Beim Freischalten ist der nächste dieser Tage vorbelegt.",
    ),
```
- `GRUPPEN = ["Buchung und Storno", "Zahlung und Rechnung", "Mitgliedschaft", "Halle", "Portal und Zugang"]`.

`_parse` braucht keine Änderung: für `TagMonat` greift `return typ(roh)`.

- [ ] **Step 4: Dienst `mitgliedschaft`**

`core/beachhub_core/services/mitgliedschaft.py`:
```python
"""Mitgliedschaft im Verein (A-KUND-4): Freischalten, Verlängern, Beenden, Antrag.

Der Status ist kein gespeichertes Kennzeichen, sondern `kunde.mitglied_bis`: Bis zu diesem Tag gilt
die Gruppe der Mitglieder, danach läuft die Mitgliedschaft von selbst aus
(services/kundengruppen.effektive_gruppe). Es gibt keinen Job, der Gruppen umschreibt.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Kunde, utcnow
from beachhub_core.services import audit, konfiguration, kundengruppen

MITGLIED, BEANTRAGT, NICHT_MITGLIED = "mitglied", "beantragt", "nicht_mitglied"


class MitgliedschaftsFehler(Exception):  # noqa: N818
    pass


def naechster_ablauf(db: Session, nach: date) -> date:
    """Der nächste Ablauftag (Einstellung `mitgliedschaft_ablauf`) echt nach `nach`."""
    stichtag = konfiguration.hole(db, "mitgliedschaft_ablauf")
    d: date = stichtag.im_jahr(nach.year)
    return d if d > nach else stichtag.im_jahr(nach.year + 1)


def letzter_ablauf(db: Session, bis: date) -> date:
    """Der letzte Ablauftag am oder vor `bis`."""
    stichtag = konfiguration.hole(db, "mitgliedschaft_ablauf")
    d: date = stichtag.im_jahr(bis.year)
    return d if d <= bis else stichtag.im_jahr(bis.year - 1)


def status(kunde: Kunde, heute: date) -> str:
    if kundengruppen.ist_mitglied_am(kunde, heute):
        return MITGLIED
    if kunde.mitglied_antrag_am is not None:
        return BEANTRAGT
    return NICHT_MITGLIED


def _protokolliere(
    db: Session,
    kunde: Kunde,
    vorher: dict[str, Any],
    aktion: str,
    *,
    quelle: str,
    admin_user_id: uuid.UUID | None,
) -> None:
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="kunde",
        objekt_id=kunde.id,
        vorher=vorher,
        nachher={**audit.als_dict(kunde), "aktion": aktion},
        admin_user_id=admin_user_id,
    )
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{kunde.id}")


def freischalten(
    db: Session, kunde: Kunde, *, bis: date, admin_user_id: uuid.UUID | None
) -> None:
    """Schaltet frei oder verlängert bis `bis`. Ein offener Antrag ist damit erledigt; seine
    Angaben (Mitgliedsnummer) bleiben zur Nachvollziehbarkeit stehen."""
    if kunde.anonymisiert_am is not None:
        raise MitgliedschaftsFehler("kunde_anonymisiert")
    if bis < clock.today(db):
        raise MitgliedschaftsFehler("bis_vergangen")
    vorher = audit.als_dict(kunde)
    kunde.mitglied_bis = bis
    kunde.mitglied_freigeschaltet_am = utcnow()
    kunde.mitglied_freigeschaltet_von = admin_user_id
    kunde.mitglied_antrag_am = None
    kunde.mitglied_beendet_am = None
    kunde.mitglied_beendet_grund = ""
    db.flush()
    _protokolliere(
        db, kunde, vorher, "mitglied_freigeschaltet", quelle="admin", admin_user_id=admin_user_id
    )


def beende(db: Session, kunde: Kunde, *, grund: str, admin_user_id: uuid.UUID | None) -> bool:
    """Beendet die Mitgliedschaft sofort (Abweichung A-5) und liefert, ob sie bis heute galt –
    nur dann bekommt der Kunde eine Mail. Bestätigte Buchungen behalten ihre Konditionen
    (A-TARIF-3); künftige Termine zum Mitgliedspreis zeigt die Klärungsliste."""
    if kunde.mitglied_bis is None:
        raise MitgliedschaftsFehler("kein_mitglied")
    heute = clock.today(db)
    war_mitglied = kundengruppen.ist_mitglied_am(kunde, heute)
    vorher = audit.als_dict(kunde)
    if war_mitglied:
        kunde.mitglied_bis = heute - timedelta(days=1)
    kunde.mitglied_beendet_am = utcnow()
    kunde.mitglied_beendet_grund = grund.strip()[:300]
    db.flush()
    _protokolliere(
        db, kunde, vorher, "mitglied_beendet", quelle="admin", admin_user_id=admin_user_id
    )
    return war_mitglied


def verwerfe_antrag(db: Session, kunde: Kunde, *, admin_user_id: uuid.UUID | None) -> None:
    if kunde.mitglied_antrag_am is None:
        raise MitgliedschaftsFehler("kein_antrag")
    vorher = audit.als_dict(kunde)
    kunde.mitglied_antrag_am = None
    db.flush()
    _protokolliere(
        db, kunde, vorher, "antrag_verworfen", quelle="admin", admin_user_id=admin_user_id
    )


def offene_antraege(db: Session) -> list[Kunde]:
    return list(
        db.scalars(
            select(Kunde)
            .where(Kunde.mitglied_antrag_am.is_not(None), Kunde.anonymisiert_am.is_(None))
            .order_by(Kunde.mitglied_antrag_am)
        ).all()
    )
```

- [ ] **Step 5: Mails**

`core/beachhub_core/services/benachrichtigung.py` (Import `Kunde` ergänzen):
```python
def mitgliedschaft_freigeschaltet(db: Session, k: Kunde) -> None:
    mail.sende(
        k.email,
        "Ihre Mitgliedschaft ist freigeschaltet",
        _text("mitgliedschaft_freigeschaltet", k=k),
    )


def mitgliedschaft_beendet(db: Session, k: Kunde) -> None:
    mail.sende(k.email, "Ihre Mitgliedschaft wurde beendet", _text("mitgliedschaft_beendet", k=k))
```

`core/beachhub_core/templates/mail/mitgliedschaft_freigeschaltet.txt`:
```
Hallo {{ k.name }},

Ihre Mitgliedschaft im Verein ist bei uns bis zum {{ k.mitglied_bis|datum }} freigeschaltet.
Für Termine bis zu diesem Tag gelten ab jetzt die Preise für Mitglieder. Bereits bestätigte
Buchungen behalten ihren Preis.

Viele Grüße
{{ betreiber }}
```

`core/beachhub_core/templates/mail/mitgliedschaft_beendet.txt`:
```
Hallo {{ k.name }},

Ihre Mitgliedschaft im Verein ist bei uns seit dem {{ k.mitglied_beendet_am|datum }} beendet.
Für neue Buchungen gelten die Preise für Nicht-Mitglieder. Bereits bestätigte Buchungen behalten
ihren Preis; falls zu einzelnen Terminen etwas zu klären ist, melden wir uns bei Ihnen.

Viele Grüße
{{ betreiber }}
```

- [ ] **Step 6: Kundenseite, Antragsliste, Navigation**

`core/beachhub_core/routes/kunden.py`:
- Importe: `from beachhub_core.routes._form import fehlertext, pflicht, t_betrag, t_datum, t_uuid`; `from beachhub_core.services import benachrichtigung, guthaben, kunden, kundengruppen, mitgliedschaft`; `from beachhub_core.services.mitgliedschaft import MitgliedschaftsFehler`.
- `FEHLERTEXT` ergänzen:
```python
    "bis_vergangen": "Das Datum liegt in der Vergangenheit",
    "kein_mitglied": "Der Kunde ist kein Mitglied",
    "kein_antrag": "Es liegt kein Antrag vor",
    "kunde_anonymisiert": "Der Kunde ist anonymisiert",
```
- `FORM_FEHLER = (KundenFehler, GuthabenFehler, MitgliedschaftsFehler, ValueError, InvalidOperation, IntegrityError)`.
- Hilfsfunktion unter `FORM_FEHLER`:
```python
def _nicht_gefunden() -> RedirectResponse:
    return mit_flash(
        RedirectResponse("/admin/kunden", status_code=303), "Kunde nicht gefunden", "fehler"
    )
```
- **Vor** der Route `GET /kunden/{kunde_id}` (sonst fängt sie den Pfad ab und antwortet 422):
```python
@router.get("/kunden/antraege", response_class=HTMLResponse)
def antraege(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(
        request, "kunden/antraege.html", admin=admin, kunden=mitgliedschaft.offene_antraege(db)
    )
```
- In `_detail_ctx` am Anfang `heute = clock.today(db)` und im Rückgabewert ergänzen:
```python
        "gruppe_heute": kundengruppen.effektive_gruppe(db, k, heute),
        "mitgliedschaft": mitgliedschaft.status(k, heute),
        "vorschlag_bis": mitgliedschaft.naechster_ablauf(db, heute),
```
- Am Dateiende:
```python
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
    return mit_flash(
        RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), "Mitgliedschaft beendet"
    )


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
    return mit_flash(
        RedirectResponse(f"/admin/kunden/{k.id}", status_code=303), "Antrag verworfen"
    )
```

`core/beachhub_core/templates/kunden/detail.html`, neue Karte direkt nach dem ersten `</div>` der Zeile (nach der Guthaben-Karte, vor „Buchungen“):
```html
<div class="karte">
  <h2>Mitgliedschaft</h2>
  {% if mitgliedschaft == "mitglied" %}
  <p>Mitglied bis <strong>{{ kunde.mitglied_bis|datum }}</strong>{% if kunde.mitglied_freigeschaltet_am %} · freigeschaltet am {{ kunde.mitglied_freigeschaltet_am|datum }}{% endif %}</p>
  {% elif mitgliedschaft == "beantragt" %}
  <p><span class="badge">Antrag offen</span> seit {{ kunde.mitglied_antrag_am|lokal }}</p>
  {% else %}
  <p>Kein Mitglied{% if kunde.mitglied_bis %} (zuletzt bis {{ kunde.mitglied_bis|datum }}){% endif %}{% if kunde.mitglied_beendet_am %} · beendet am {{ kunde.mitglied_beendet_am|datum }}{% if kunde.mitglied_beendet_grund %}: {{ kunde.mitglied_beendet_grund }}{% endif %}{% endif %}</p>
  {% endif %}
  {% if kunde.mitglied_antrag_hinweis %}<p>Angaben im Antrag: {{ kunde.mitglied_antrag_hinweis }}</p>{% endif %}
  <p class="small">Prüfen Sie die Angaben gegen die Mitgliederliste des Vereins. Mitglied können nur Privatpersonen sein.</p>
  <form method="post" action="/admin/kunden/{{ kunde.id }}/mitgliedschaft">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <label>Mitglied bis <input type="date" name="bis" value="{{ vorschlag_bis.isoformat() }}" required></label>
    <button>{{ "Verlängern" if mitgliedschaft == "mitglied" else "Freischalten" }}</button>
  </form>
  {% if mitgliedschaft == "beantragt" %}
  <form method="post" action="/admin/kunden/{{ kunde.id }}/mitgliedschaft/antrag-verwerfen">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button class="gefahr">Antrag verwerfen</button>
  </form>
  {% endif %}
  {% if mitgliedschaft == "mitglied" %}
  <form method="post" action="/admin/kunden/{{ kunde.id }}/mitgliedschaft/beenden">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <label>Grund <input name="grund" required></label>
    <button class="gefahr">Mitgliedschaft beenden</button>
  </form>
  {% endif %}
</div>
```

`core/beachhub_core/templates/kunden/antraege.html`:
```html
{% extends "base.html" %}{% block title %}Mitgliedsanträge{% endblock %}
{% block content %}
<h1>Mitgliedsanträge</h1>
<p class="hinweis">Kunden, die im Portal die Freischaltung als Mitglied beantragt haben. Prüfen Sie die Angaben gegen die Mitgliederliste des Vereins; freischalten oder verwerfen können Sie auf der Kundenseite.</p>
<div class="karte">
<table>
  <thead><tr><th>Beantragt am</th><th>Name</th><th>E-Mail</th><th>Angaben</th><th class="rechts"></th></tr></thead>
  <tbody>
  {% for k in kunden %}<tr>
    <td>{{ k.mitglied_antrag_am|lokal }}</td>
    <td>{{ k.name }}</td>
    <td>{{ k.email }}</td>
    <td>{{ k.mitglied_antrag_hinweis }}</td>
    <td class="rechts"><a class="aktion" href="/admin/kunden/{{ k.id }}">öffnen</a></td>
  </tr>{% else %}
    <tr><td colspan="5" class="leer">Keine offenen Anträge.</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endblock %}
```

`core/beachhub_core/navigation.py`, Bereich „Kunden“ ersetzen:
```python
    Bereich(
        "Kunden",
        "/admin/kunden",
        [
            Punkt("Kundenliste", "/admin/kunden"),
            Punkt("Mitgliedsanträge", "/admin/kunden/antraege"),
        ],
    ),
```

- [ ] **Step 7: Tests und Lint**

Run:
```bash
(cd core && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 8: Commit**

```bash
git add core
git commit -m "feat(core): Mitgliedschaft freischalten, verlängern, beenden und Anträge verwalten"
```

---
## Task 5: Kanal und Lesestand – Mitgliedsantrag, Rechnungskunden, Konto-Felder

Das Portal kann künftig die Freischaltung als Mitglied beantragen (`mitgliedschaft_beantragen`, A-KUND-4); das Hauptsystem vermerkt den Antrag und schreibt dem Betreiber eine Mail. Rechnungskunden werden bei `buchung_anfragen` mit Grund `rechnungskunde` abgelehnt, solange die Einstellung `rechnungskunden_online_buchen` aus ist (A-KUND-7). Das Konto-Dokument trägt Mitgliedschaft und Rechnungskunden-Status, das Tarif-Dokument die Namen beider Gruppen. Ja/Nein-Einstellungen erscheinen als Auswahl. Die Portal-Oberfläche dazu kommt mit Stufe 2.

**Files:**
- Modify: `shared/beachhub_shared/kanal.py`, `shared/beachhub_shared/lesestand.py`
- Test: `shared/tests/test_kanal.py`, `shared/tests/test_lesestand_schema.py`
- Modify: `core/beachhub_core/services/konfiguration.py`, `services/kunden.py`, `services/mitgliedschaft.py`, `services/anfragen.py`, `services/online_buchung.py`, `services/lesestand.py`, `services/benachrichtigung.py`
- Modify: `core/beachhub_core/templates/stammdaten/konfiguration.html`
- Test: `core/tests/test_anfragen.py`, `test_online_buchung.py`, `test_lesestand.py`, `test_konfiguration.py`, `test_ui_stammdaten.py`
- Modify: `portal/tests/hilfen.py`

**Interfaces:**
- Consumes: `mitgliedschaft._protokolliere`, `mitgliedschaft.status` (Task 4).
- Produces (shared): Anfragetyp `"mitgliedschaft_beantragen"` mit Nutzlast `MitgliedschaftBeantragen(hinweis_text: str)` (1–500 Zeichen).
- Produces (shared): `KontoInhalt` ohne `zahlungsart`, neu mit Vorgaben `rechnungskunde: bool = False`, `online_buchen: bool = True`, `mitgliedschaft: Literal["mitglied", "beantragt", "nicht_mitglied"] = "nicht_mitglied"`, `mitglied_bis: date | None = None`, `antrag_am: datetime | None = None`; `TarifeInhalt.gruppe_mitglied: str | None = None`, `TarifeInhalt.gruppe_nichtmitglied: str | None = None`.
- Produces: Einstellung `rechnungskunden_online_buchen: bool = False` (Gruppe „Portal und Zugang“); Ändern markiert die Konto-Dokumente aller Rechnungskunden.
- Produces: `konfiguration.gruppiert(werte) -> list[tuple[str, list[tuple[str, Any, Beschreibung, bool]]]]` – viertes Element: Ja/Nein-Wert; `_parse(bool, roh)` akzeptiert nur `ja/nein/true/false/1/0`, sonst `ValueError("Bitte ja oder nein wählen")`.
- Produces: `kunden.darf_online_buchen(db, kunde) -> bool`, `mitgliedschaft.beantrage(db, kunde, *, hinweis: str) -> None`, `lesestand.markiere_rechnungskunden(db) -> None`, `benachrichtigung.mitgliedsantrag(db, kunde) -> None` (Betreff „[Beachhub] Antrag auf Vereinsmitgliedschaft“ an den Betreiber).
- Produces: Ablehnungsgrund `rechnungskunde` bei `buchung_anfragen`.

- [ ] **Step 1: Failing Tests in `shared` schreiben**

`shared/tests/test_kanal.py`:
```python
def test_mitgliedschaft_beantragen_validiert() -> None:
    n = kanal.NUTZLAST["mitgliedschaft_beantragen"].model_validate({"hinweis_text": "Nr. 4711"})
    assert isinstance(n, kanal.MitgliedschaftBeantragen) and n.hinweis_text == "Nr. 4711"
    for falsch in ({"hinweis_text": ""}, {"hinweis_text": "x" * 501}, {}):
        with pytest.raises(ValidationError):
            kanal.MitgliedschaftBeantragen.model_validate(falsch)
```

`shared/tests/test_lesestand_schema.py` (Importe: `from datetime import UTC, date, datetime`; `from beachhub_shared.lesestand import Dokument, KontoInhalt, TarifeInhalt, TarifInfo`):
```python
def test_konto_neue_felder_mit_vorgabe_und_ohne_zahlungsart() -> None:
    alt = {
        "kunde_id": "k",
        "kundengruppe": "Nicht-Mitglied",
        "zahlungsart": "online",
        "guthaben": "0.00",
        "buchungen": [],
        "rechnungen": [],
    }
    k = KontoInhalt.model_validate(alt)
    assert k.rechnungskunde is False and k.online_buchen is True
    assert k.mitgliedschaft == "nicht_mitglied" and k.mitglied_bis is None and k.antrag_am is None
    assert "zahlungsart" not in k.model_dump()
    neu = KontoInhalt.model_validate({**alt, "mitgliedschaft": "mitglied", "mitglied_bis": "2028-04-30"})
    assert neu.mitglied_bis == date(2028, 4, 30)


def test_tarife_gruppennamen_optional() -> None:
    t = TarifeInhalt(regeln=[])
    assert t.gruppe_mitglied is None and t.gruppe_nichtmitglied is None
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd shared && pytest -q`
Expected: FAIL – `KeyError: 'mitgliedschaft_beantragen'`, `AttributeError: … 'rechnungskunde'`.

- [ ] **Step 3: Vertrag in `shared` erweitern**

`shared/beachhub_shared/kanal.py`:
- In `ANFRAGETYPEN` nach `"rechnung_anfordern",` ergänzen: `"mitgliedschaft_beantragen",`.
- Nach `RechnungAnfordern`:
```python
class MitgliedschaftBeantragen(BaseModel):
    # Mitgliedsnummer oder Name in der Mitgliederliste des Vereins (A-KUND-4).
    hinweis_text: str = Field(min_length=1, max_length=500)
```
- In `NUTZLAST`: `"mitgliedschaft_beantragen": MitgliedschaftBeantragen,`.

`shared/beachhub_shared/lesestand.py`:
- Import `from typing import Any, Literal`.
- `TarifeInhalt` ersetzen:
```python
class TarifeInhalt(BaseModel):
    regeln: list[TarifInfo]
    # Namen der beiden festen Gruppen (A-KUND-2), damit das Portal die Gruppe eines Termins aus
    # mitglied_bis ableiten kann. Mit Vorgabe, damit ältere gespeicherte Dokumente gültig bleiben.
    gruppe_mitglied: str | None = None
    gruppe_nichtmitglied: str | None = None
```
- `KontoInhalt` ersetzen:
```python
class KontoInhalt(BaseModel):
    kunde_id: str
    # Gruppe am heutigen Tag. Für künftige Termine gilt die Gruppe an deren Tag (A-KUND-6):
    # Mitglied, solange mitglied_bis den Tag einschließt.
    kundengruppe: str
    guthaben: Decimal
    buchungen: list[KontoBuchung]
    rechnungen: list[KontoRechnung]
    # Seit Stufe 1a; mit Vorgaben, damit ältere gespeicherte Dokumente gültig bleiben. Das frühere
    # Feld zahlungsart entfällt (die Zahlungsart steht an der Buchung, A-ZAHL-1).
    rechnungskunde: bool = False
    online_buchen: bool = True
    mitgliedschaft: Literal["mitglied", "beantragt", "nicht_mitglied"] = "nicht_mitglied"
    mitglied_bis: date | None = None
    antrag_am: datetime | None = None
```

Run: `cd shared && pytest -q` – Expected: PASS.

- [ ] **Step 4: Failing Tests im Hauptsystem schreiben**

`core/tests/test_anfragen.py` (Importe: `Audit` und `LesestandVersion` sind schon da):
```python
def test_mitgliedschaft_beantragen(db: Session, welt, mail_ausgang: list) -> None:
    konto = uuid.uuid4()
    k = _angelegt(db, konto)
    db.get(LesestandVersion, f"konto:{k.id}").geaendert = False
    db.commit()
    antwort, nachlauf = anfragen.bearbeite(
        db, anfrage("mitgliedschaft_beantragen", konto, hinweis_text=" Nr. 4711 ")
    )
    assert antwort.status == "ok"
    db.refresh(k)
    assert k.mitglied_antrag_am is not None and k.mitglied_antrag_hinweis == "Nr. 4711"
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert
    assert db.scalars(select(Audit).where(Audit.objekt_id == k.id, Audit.quelle == "portal")).all()
    assert mail_ausgang == []  # die Mail geht erst nach dem Commit
    for schritt in nachlauf:
        schritt(db)
    assert [m["betreff"] for m in mail_ausgang] == ["[Beachhub] Antrag auf Vereinsmitgliedschaft"]
    assert "Nr. 4711" in mail_ausgang[0]["text"] and "anna@x.de" in mail_ausgang[0]["text"]


def test_mitgliedschaft_beantragen_ohne_angaben_ungueltig(db: Session, welt) -> None:
    konto = uuid.uuid4()
    _angelegt(db, konto)
    antwort, _ = anfragen.bearbeite(
        db, anfrage("mitgliedschaft_beantragen", konto, hinweis_text="")
    )
    assert antwort.status == "abgelehnt" and antwort.grund == "ungueltig"
```

`core/tests/test_online_buchung.py` (Import `konfiguration` in die Service-Importe):
```python
def test_rechnungskunde_wird_abgelehnt(db: Session, welt) -> None:
    f, k, _ = welt
    k.rechnungskunde = True
    guthaben.buche(db, kunde=k, betrag=Decimal("30.00"), art="manuell")
    db.commit()
    a = _anfragen(db, f, k).antwort
    db.commit()
    assert a.status == "abgelehnt" and a.grund == "rechnungskunde"
    assert db.query(Buchung).count() == 0 and k.guthaben == Decimal("30.00")
    konfiguration.setze(db, "rechnungskunden_online_buchen", "ja")
    db.commit()
    assert _anfragen(db, f, k).antwort.status == "bestaetigt"
```

`core/tests/test_lesestand.py` (Importe: `from datetime import date`; `konfiguration` in die Service-Importe):
```python
def test_konto_zeigt_mitgliedschaft_und_rechnungskunde(db: Session, welt) -> None:
    _, a, _ = welt
    a.mitglied_bis = date(2028, 4, 30)
    a.rechnungskunde = True
    db.commit()
    k = lesestand.baue_konto(db, a)
    assert (k.kundengruppe, k.mitgliedschaft) == ("DJK-Mitglied", "mitglied")
    assert k.mitglied_bis == date(2028, 4, 30)
    assert k.rechnungskunde is True and k.online_buchen is False
    t = lesestand.baue_tarife(db)
    assert (t.gruppe_mitglied, t.gruppe_nichtmitglied) == ("DJK-Mitglied", "Nicht-Mitglied")


def test_umschalten_markiert_konten_der_rechnungskunden(db: Session, welt) -> None:
    _, a, b = welt
    a.rechnungskunde = True
    db.commit()
    lesestand.verarbeite_geaenderte(db)
    konfiguration.setze(db, "rechnungskunden_online_buchen", "ja")
    db.commit()
    assert db.get(LesestandVersion, f"konto:{a.id}").geaendert
    zeile_b = db.get(LesestandVersion, f"konto:{b.id}")
    assert zeile_b is None or not zeile_b.geaendert
    assert lesestand.baue_konto(db, a).online_buchen is True
```

`core/tests/test_konfiguration.py`:
```python
def test_ja_nein_nur_eindeutig(db: Session) -> None:
    assert konfiguration.hole(db, "rechnungskunden_online_buchen") is False
    konfiguration.setze(db, "rechnungskunden_online_buchen", "ja")
    assert konfiguration.hole(db, "rechnungskunden_online_buchen") is True
    with pytest.raises(ValueError, match="ja oder nein"):
        konfiguration.setze(db, "rechnungskunden_online_buchen", "vielleicht")
```

`core/tests/test_ui_stammdaten.py` (Import `from beachhub_core.services import konfiguration, kundengruppen`):
```python
def test_einstellung_ja_nein_als_auswahl(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    assert '<select name="rechnungskunden_online_buchen">' in c.get("/admin/konfiguration").text
    r = c.post(
        "/admin/konfiguration",
        data={"csrf_token": c.csrf, "rechnungskunden_online_buchen": "ja"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert konfiguration.hole(db, "rechnungskunden_online_buchen") is True
```

- [ ] **Step 5: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_anfragen.py tests/test_online_buchung.py tests/test_lesestand.py tests/test_konfiguration.py tests/test_ui_stammdaten.py`
Expected: FAIL – u. a. `KeyError: 'rechnungskunden_online_buchen'`, Anfrage `mitgliedschaft_beantragen` liefert `KeyError` in `_MIT_KUNDE`.

- [ ] **Step 6: Einstellung und Ja/Nein-Auswahl**

`core/beachhub_core/services/konfiguration.py`:
- In `DEFAULTS` nach `"pin_laenge"`: `"rechnungskunden_online_buchen": (bool, False),`
- In `BESCHREIBUNGEN` nach `"pin_laenge"`:
```python
    "rechnungskunden_online_buchen": Beschreibung(
        "Portal und Zugang",
        "Rechnungskunden buchen online",
        "",
        "Nein: Rechnungskunden sehen im Portal ihre Termine, Zahlencodes und Rechnungen und "
        "sagen Abo-Termine ab, buchen aber nicht selbst. Ihre Buchungen legen Sie an.",
    ),
```
- In `_parse` den Zweig für `bool` ersetzen:
```python
    if typ is bool:
        wert = roh.strip().lower()
        if wert in ("1", "true", "ja"):
            return True
        if wert in ("0", "false", "nein"):
            return False
        raise ValueError("Bitte ja oder nein wählen")
```
- `gruppiert` ersetzen:
```python
def gruppiert(
    werte: dict[str, Any],
) -> list[tuple[str, list[tuple[str, Any, Beschreibung, bool]]]]:
    """Ordnet die Werte den Gruppen zu, in der Reihenfolge von GRUPPEN. Das vierte Element sagt,
    ob der Wert ja/nein ist – die Seite zeigt dafür eine Auswahl statt eines Textfelds."""
    return [
        (
            gruppe,
            [
                (schluessel, werte[schluessel], BESCHREIBUNGEN[schluessel], DEFAULTS[schluessel][0] is bool)
                for schluessel in DEFAULTS
                if schluessel in werte and BESCHREIBUNGEN[schluessel].gruppe == gruppe
            ],
        )
        for gruppe in GRUPPEN
    ]
```
- Am Ende von `setze`:
```python
    if schluessel == "rechnungskunden_online_buchen":
        from beachhub_core.services import lesestand

        # Der Wert steht als online_buchen im Konto-Dokument jedes Rechnungskunden.
        lesestand.markiere_rechnungskunden(db)
```

`core/beachhub_core/templates/stammdaten/konfiguration.html`, die Schleife über die Einträge ersetzen:
```html
    {% for schluessel, wert, b, ist_bool in eintraege %}
      {% if ist_bool %}
      <label><span class="bezeichnung">{{ b.name }}</span><select name="{{ schluessel }}"><option value="ja" {% if wert %}selected{% endif %}>ja</option><option value="nein" {% if not wert %}selected{% endif %}>nein</option></select><span class="hinweis">Vorgabe: {{ "ja" if defaults[schluessel][1] else "nein" }}. {{ b.hilfe }}</span></label>
      {% else %}
      <label><span class="bezeichnung">{{ b.name }}{% if b.einheit %} <span class="einheit">({{ b.einheit }})</span>{% endif %}</span><input name="{{ schluessel }}" value="{{ wert|wert }}" placeholder="{{ defaults[schluessel][1]|wert }}"><span class="hinweis">{{ b.hilfe }}</span></label>
      {% endif %}
    {% endfor %}
```

- [ ] **Step 7: Antrag, Sperre, Lesestand**

`core/beachhub_core/services/kunden.py` (Import `from beachhub_core.services import audit, konfiguration`):
```python
def darf_online_buchen(db: Session, kunde: Kunde) -> bool:
    """Rechnungskunden buchen nur online, wenn der Betreiber es erlaubt (A-KUND-7)."""
    return not kunde.rechnungskunde or bool(konfiguration.hole(db, "rechnungskunden_online_buchen"))
```

`core/beachhub_core/services/mitgliedschaft.py`:
```python
def beantrage(db: Session, kunde: Kunde, *, hinweis: str) -> None:
    """Vermerkt einen Antrag aus dem Portal. Ein erneuter Antrag ersetzt die Angaben des
    vorigen; entschieden wird im Admin-UI."""
    vorher = audit.als_dict(kunde)
    kunde.mitglied_antrag_am = utcnow()
    kunde.mitglied_antrag_hinweis = hinweis.strip()[:500]
    db.flush()
    _protokolliere(db, kunde, vorher, "antrag_gestellt", quelle="portal", admin_user_id=None)
```

`core/beachhub_core/services/benachrichtigung.py`:
```python
def mitgliedsantrag(db: Session, k: Kunde) -> None:
    betreiber_alarm(
        "Antrag auf Vereinsmitgliedschaft",
        f"{k.name} <{k.email}> beantragt im Portal die Freischaltung als Mitglied.\n"
        f"Angaben: {k.mitglied_antrag_hinweis}\n\n"
        "Bitte gegen die Mitgliederliste prüfen und in der Verwaltung unter "
        "Kunden → Mitgliedsanträge freischalten oder verwerfen.",
    )
```

`core/beachhub_core/services/anfragen.py`:
- Importe: `benachrichtigung` und `mitgliedschaft` in die Service-Importe; `from beachhub_core.services.ergebnis import Ergebnis, Nachlauf, abgelehnt, ok` (bleibt).
- Neu, vor `_MIT_KUNDE`:
```python
def _antrag_mail(kunde_id: Any) -> Nachlauf:
    def lauf(db: Session) -> None:
        k = db.get(Kunde, kunde_id)
        if k is not None:
            benachrichtigung.mitgliedsantrag(db, k)

    return lauf


def _mitgliedschaft_beantragen(
    db: Session, kunde: Kunde, anfrage: kanal.Anfrage, n: kanal.MitgliedschaftBeantragen
) -> Ergebnis:
    if kunde.anonymisiert_am is not None:
        return abgelehnt("konto_gesperrt")
    mitgliedschaft.beantrage(db, kunde, hinweis=n.hinweis_text)
    return Ergebnis(kanal.Antwort(status="ok"), [_antrag_mail(kunde.id)])
```
- In `_MIT_KUNDE`: `"mitgliedschaft_beantragen": _mitgliedschaft_beantragen,`.

`core/beachhub_core/services/online_buchung.py` (Import `kunden` in die Service-Importe), in `anfragen` direkt nach der Prüfung auf `anonymisiert_am`:
```python
    if not kunden.darf_online_buchen(db, kunde):
        # Rechnungskunden buchen nicht online; ihre Buchungen legt der Betreiber an (A-KUND-7).
        return abgelehnt("rechnungskunde")
```

`core/beachhub_core/services/lesestand.py`:
- Importe: `from beachhub_core.services import konfiguration, kunden, kundengruppen, mitgliedschaft, pin`.
- `baue_tarife`: im `return schema.TarifeInhalt(...)` nach `regeln=[...]` ergänzen:
```python
        gruppe_mitglied=kundengruppen.mitglied(db).name,
        gruppe_nichtmitglied=kundengruppen.nicht_mitglied(db).name,
```
- `baue_konto`, den `return` ersetzen:
```python
    heute = clock.today(db)
    return schema.KontoInhalt(
        kunde_id=str(kunde.id),
        kundengruppe=kundengruppen.effektive_gruppe(db, kunde, heute).name,
        guthaben=kunde.guthaben,
        buchungen=out,
        rechnungen=[
            schema.KontoRechnung(nummer=r.nummer, datum=r.datum, brutto=r.brutto, status=r.status)
            for r in rechnungen
        ],
        rechnungskunde=kunde.rechnungskunde,
        online_buchen=kunden.darf_online_buchen(db, kunde),
        mitgliedschaft=mitgliedschaft.status(kunde, heute),
        mitglied_bis=kunde.mitglied_bis,
        antrag_am=kunde.mitglied_antrag_am,
    )
```
- Nach `markiere_geaendert`:
```python
def markiere_rechnungskunden(db: Session) -> None:
    """Markiert die Konto-Dokumente aller Rechnungskunden – sie tragen online_buchen."""
    ids = db.scalars(
        select(Kunde.id).where(Kunde.rechnungskunde.is_(True), Kunde.anonymisiert_am.is_(None))
    ).all()
    if ids:
        markiere_geaendert(db, *(f"konto:{i}" for i in ids))
```

`mitgliedschaft.status` liefert einen `str`; für mypy den Rückgabetyp in `mitgliedschaft.py` auf `Literal["mitglied", "beantragt", "nicht_mitglied"]` setzen (`from typing import Any, Literal`) und die Konstanten so annotieren:
```python
Status = Literal["mitglied", "beantragt", "nicht_mitglied"]
MITGLIED: Status = "mitglied"
BEANTRAGT: Status = "beantragt"
NICHT_MITGLIED: Status = "nicht_mitglied"
```
(`def status(kunde: Kunde, heute: date) -> Status:`).

`portal/tests/hilfen.py`, in `konto()` die Zeile `"zahlungsart": "online",` ersetzen durch `"rechnungskunde": False,`.

- [ ] **Step 8: Tests und Lint**

Run:
```bash
(cd shared && pytest -q) && (cd core && pytest -q) && (cd portal && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 9: Commit**

```bash
git add shared core portal/tests/hilfen.py
git commit -m "feat(core): Mitgliedsantrag über den Kanal, Rechnungskunden ohne Onlinebuchung, Konto-Felder"
```

---
## Task 6: Klärungsliste nach vorzeitigem Ende der Mitgliedschaft

Endet eine Mitgliedschaft vorzeitig, behalten bestätigte Buchungen ihre Konditionen (A-TARIF-3). Künftige Termine, die zum Mitgliedspreis gebucht sind, deren Kunde am Termin aber kein Mitglied mehr ist, erscheinen in einer Klärungsliste; der Betreiber belässt sie („geklärt“) oder storniert sie über die Buchung (A-KUND-4, A-DAUER-5). Die Liste wird berechnet; gespeichert wird nur, dass eine Buchung geklärt ist.

**Files:**
- Modify: `core/beachhub_core/models/buchungen.py`
- Create: `core/alembic/versions/0012_gruppe_geklaert.py`
- Modify: `core/beachhub_core/services/mitgliedschaft.py`
- Modify: `core/beachhub_core/routes/system.py`, `routes/kunden.py` (`mitgliedschaft_beenden`), `navigation.py`
- Create: `core/beachhub_core/templates/system/klaerung.html`
- Test: `core/tests/test_mitgliedschaft.py`, `test_ui_mitgliedschaft.py`, `test_ui_navigation.py`

**Interfaces:**
- Consumes: `Buchung.kundengruppe_id` (Task 1), `mitgliedschaft.beende` (Task 4).
- Produces: `Buchung.gruppe_geklaert_am: datetime | None`.
- Produces: `mitgliedschaft.klaerungsfaelle(db) -> list[Buchung]` (künftige Buchungen mit Status `reserviert`/`bestaetigt` in der Mitgliedergruppe, deren Kunde am Termin kein Mitglied ist, ungeklärt, nach Beginn sortiert), `mitgliedschaft.klaere(db, buchung, *, admin_user_id) -> None`.
- Produces: Routen `GET /admin/system/klaerung`, `POST /admin/system/klaerung/{buchung_id}`; Navigation „System“ → „Klärung Mitgliedschaft“.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_mitgliedschaft.py` (Importe ergänzen: `storno` in die Service-Importe):
```python
def _termin(db: Session, f, k, tag: date, stunde: int = 19):
    return buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(tag, time(stunde)),
        ende=kombiniere(tag, time(stunde + 1)),
        zahlungsart="online",
    )


def test_beenden_bringt_kuenftige_mitgliedsbuchung_in_die_klaerung(db: Session, welt) -> None:
    f, k = welt
    andere = kunden.lege_an(db, name="Bea", email="bea@x.de")
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    vergangen = _termin(db, f, k, date(2027, 11, 26))
    kuenftig = _termin(db, f, k, date(2027, 12, 10))
    _termin(db, f, andere, date(2027, 12, 10), stunde=20)  # Nicht-Mitglied: nie in der Liste
    db.commit()
    assert mitgliedschaft.klaerungsfaelle(db) == []

    clock.set_override(db, date(2027, 12, 1))
    mitgliedschaft.beende(db, k, grund="ausgetreten", admin_user_id=None)
    db.commit()
    assert mitgliedschaft.klaerungsfaelle(db) == [kuenftig]
    assert vergangen not in mitgliedschaft.klaerungsfaelle(db)
    # Die Buchung behält ihre Konditionen (A-TARIF-3).
    assert (kuenftig.preis, kuenftig.ust_satz) == (Decimal("20.00"), Decimal("7.00"))

    mitgliedschaft.klaere(db, kuenftig, admin_user_id=None)
    db.commit()
    assert mitgliedschaft.klaerungsfaelle(db) == []
    assert kuenftig.gruppe_geklaert_am is not None


def test_storno_nimmt_buchung_aus_der_klaerung(db: Session, welt) -> None:
    f, k = welt
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    b = _termin(db, f, k, date(2027, 12, 10))
    db.commit()
    mitgliedschaft.beende(db, k, grund="ausgetreten", admin_user_id=None)
    db.commit()
    assert mitgliedschaft.klaerungsfaelle(db) == [b]
    storno.storniere(db, b, durch="betreiber", kostenfrei=True, grund="Mitgliedschaft beendet")
    db.commit()
    assert mitgliedschaft.klaerungsfaelle(db) == []
```

`core/tests/test_ui_mitgliedschaft.py` (Importe ergänzen: `from datetime import date, time`, `from decimal import Decimal`, `from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kunde, Tarif, utcnow`, `from beachhub_core.services import buchungen, kunden, mitgliedschaft`, `from beachhub_shared.zeit import kombiniere`):
```python
def _feld(db: Session) -> Feld:
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.commit()
    return f


def test_klaerungsliste(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    f = _feld(db)
    k = _kunde(db)
    mitgliedschaft.freischalten(db, k, bis=date(2028, 4, 30), admin_user_id=None)
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=k.id,
        beginn=kombiniere(date(2027, 12, 10), time(19)),
        ende=kombiniere(date(2027, 12, 10), time(20)),
    )
    db.commit()
    seite = c.post(
        f"/admin/kunden/{k.id}/mitgliedschaft/beenden",
        data={"csrf_token": c.csrf, "grund": "ausgetreten"},
    )
    assert "1 künftige Buchung zum Mitgliedspreis" in seite.text
    liste = c.get("/admin/system/klaerung")
    assert "Anna" in liste.text and f"/admin/belegung/buchung/{b.id}" in liste.text
    r = c.post(
        f"/admin/system/klaerung/{b.id}", data={"csrf_token": c.csrf}, follow_redirects=False
    )
    assert r.status_code == 303
    assert "Nichts zu klären" in c.get("/admin/system/klaerung").text
```

`core/tests/test_ui_navigation.py`: `"/admin/system/klaerung"` in `SEITEN` ergänzen.

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_mitgliedschaft.py tests/test_ui_mitgliedschaft.py tests/test_ui_navigation.py`
Expected: FAIL – `AttributeError: module 'beachhub_core.services.mitgliedschaft' has no attribute 'klaerungsfaelle'`.

- [ ] **Step 3: Modell und Migration**

`core/beachhub_core/models/buchungen.py`, in `Buchung` nach `quelle`:
```python
    # Mitgliedschaft endete vor dem Termin, der Betreiber belässt die Buchung zu ihren
    # Konditionen (Klärungsliste, A-KUND-4).
    gruppe_geklaert_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

`core/alembic/versions/0012_gruppe_geklaert.py`:
```python
"""gruppe_geklaert: Buchung als geklärt markieren, wenn die Mitgliedschaft vor dem Termin endete
(Klärungsliste, A-KUND-4).

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "buchung", sa.Column("gruppe_geklaert_am", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("buchung", "gruppe_geklaert_am")
```

- [ ] **Step 4: Dienst**

`core/beachhub_core/services/mitgliedschaft.py` (Importe: `from beachhub_shared.zeit import lokales_datum`; `from beachhub_core.models import Buchung, Kunde, utcnow`):
```python
def klaerungsfaelle(db: Session) -> list[Buchung]:
    """Künftige Buchungen zum Mitgliedspreis, deren Kunde am Termin kein Mitglied mehr ist
    (A-KUND-4, A-DAUER-5). Sie behalten ihre Konditionen, bis der Betreiber entscheidet."""
    kandidaten = db.scalars(
        select(Buchung)
        .where(
            Buchung.kundengruppe_id == kundengruppen.mitglied(db).id,
            Buchung.status.in_((Buchung.RESERVIERT, Buchung.BESTAETIGT)),
            Buchung.beginn > clock.now(db),
            Buchung.gruppe_geklaert_am.is_(None),
        )
        .order_by(Buchung.beginn)
    ).all()
    return [
        b
        for b in kandidaten
        if not kundengruppen.ist_mitglied_am(b.kunde, lokales_datum(b.beginn))
    ]


def klaere(db: Session, buchung: Buchung, *, admin_user_id: uuid.UUID | None) -> None:
    """Der Betreiber belässt die Buchung zu ihren Konditionen."""
    if buchung.gruppe_geklaert_am is not None:
        return
    vorher = audit.als_dict(buchung)
    buchung.gruppe_geklaert_am = utcnow()
    db.flush()
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="buchung",
        objekt_id=buchung.id,
        vorher=vorher,
        nachher={**audit.als_dict(buchung), "aktion": "gruppe_geklaert"},
        admin_user_id=admin_user_id,
    )
```

- [ ] **Step 5: Seite, Hinweis beim Beenden, Navigation**

`core/beachhub_core/routes/system.py` (Importe: `import uuid`; `from beachhub_core.services import halle, lesestand, mitgliedschaft`):
```python
@router.get("/system/klaerung", response_class=HTMLResponse)
def klaerung(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return render(
        request, "system/klaerung.html", admin=admin, faelle=mitgliedschaft.klaerungsfaelle(db)
    )


@router.post("/system/klaerung/{buchung_id}")
def klaerung_erledigt(
    buchung_id: uuid.UUID,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    b = db.get(Buchung, buchung_id)
    if b is not None:
        mitgliedschaft.klaere(db, b, admin_user_id=admin.id)
        db.commit()
    return mit_flash(RedirectResponse("/admin/system/klaerung", status_code=303), "Geklärt")
```

`core/beachhub_core/templates/system/klaerung.html`:
```html
{% extends "base.html" %}{% block title %}Klärung Mitgliedschaft{% endblock %}
{% block content %}
<h1>Klärung Mitgliedschaft</h1>
<p class="hinweis">Künftige Buchungen zum Mitgliedspreis, deren Kunde am Tag des Termins kein Mitglied mehr ist – etwa weil eine Mitgliedschaft vorzeitig beendet wurde. Die Buchungen behalten Preis und Steuersatz. Entscheiden Sie je Buchung: belassen („geklärt“) oder über die Buchung stornieren.</p>
<div class="karte">
<table>
  <thead><tr><th>Termin</th><th>Feld</th><th>Kunde</th><th>Mitglied bis</th><th class="rechts">Preis</th><th>Steuersatz</th><th class="rechts"></th></tr></thead>
  <tbody>
  {% for b in faelle %}<tr>
    <td>{{ b.beginn|lokal }} – {{ b.ende|uhrzeit }}</td>
    <td>{{ b.feld.name }}</td>
    <td>{{ b.kunde.name }}</td>
    <td>{{ b.kunde.mitglied_bis|datum if b.kunde.mitglied_bis else "–" }}</td>
    <td class="rechts">{{ b.preis|euro }}</td>
    <td>{{ b.ust_satz|prozent }}</td>
    <td class="rechts"><span class="aktionen">
      <a class="aktion" href="/admin/belegung/buchung/{{ b.id }}">öffnen</a>
      <form method="post" action="/admin/system/klaerung/{{ b.id }}" class="inline"><input type="hidden" name="csrf_token" value="{{ csrf_token }}"><button class="aktion">geklärt</button></form>
    </span></td>
  </tr>{% else %}
    <tr><td colspan="7" class="leer">Nichts zu klären.</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endblock %}
```

`core/beachhub_core/routes/kunden.py`, in `mitgliedschaft_beenden` den Rückgabewert ersetzen:
```python
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
```

`core/beachhub_core/navigation.py`, Bereich „System“: nach „Kostenpflichtige Stornos“ `Punkt("Klärung Mitgliedschaft", "/admin/system/klaerung"),` einfügen.

- [ ] **Step 6: Tests und Lint**

Run:
```bash
(cd core && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 7: Commit**

```bash
git add core
git commit -m "feat(core): Klärungsliste für Buchungen nach vorzeitigem Ende der Mitgliedschaft"
```

---
## Task 7: Jahresabgleich, Erinnerung vor Ablauf und Tageslauf

Am Stichtag `mitglieder_abgleich` (Vorgabe 31.08.) bekommt der Betreiber eine Mail; die Prüfliste listet alle Kunden, deren Mitgliedschaft zum letzten Ablauftag ausgelaufen ist oder bis zum nächsten ausläuft, mit CSV-Export und den Sammelaktionen „verlängern“ und „beenden“ (A-KUND-5, Abweichung A-10). Liegt der Stichtag nach dem ersten Buchungsfenster der nächsten Saison, warnen Prüfliste und Einstellungen (Abweichung A-12). Vor Ablauf der Mitgliedschaft bekommt der Kunde eine Erinnerung (A-MAIL-2, Abweichung A-9). Beides erledigt ein täglicher Lauf um 07:00.

**Files:**
- Modify: `core/beachhub_core/models/kunden.py`
- Create: `core/alembic/versions/0013_mitglied_erinnert.py`
- Modify: `core/beachhub_core/services/konfiguration.py`, `services/mitgliedschaft.py`, `services/rechnungen.py` (`csv_sicher`), `services/benachrichtigung.py`
- Modify: `core/beachhub_core/jobs.py`
- Create: `core/beachhub_core/templates/mail/mitgliedschaft_erinnerung.txt`, `templates/kunden/abgleich.html`
- Modify: `core/beachhub_core/routes/kunden.py`, `routes/stammdaten.py` (`_einstellungen`), `templates/stammdaten/konfiguration.html`, `navigation.py`
- Test: `core/tests/test_mitgliedschaft.py`, `test_ui_mitgliedschaft.py`, `test_jobs.py`, `test_ui_stammdaten.py`, `test_ui_navigation.py`

**Interfaces:**
- Consumes: `mitgliedschaft.freischalten`, `beende`, `naechster_ablauf`, `letzter_ablauf` (Task 4), `konfiguration.TagMonat` (Task 4).
- Produces: `Kunde.mitglied_erinnert_fuer: date | None` – das `mitglied_bis`, an das zuletzt erinnert wurde.
- Produces: Einstellungen `mitglieder_abgleich: TagMonat = "31.08."`, `mitglied_erinnerung_tage: int = 14` (Gruppe „Mitgliedschaft“).
- Produces: `mitgliedschaft.pruefliste(db, heute: date) -> list[Kunde]`, `mitgliedschaft.pruefliste_csv(kunden: Sequence[Kunde]) -> str`, `mitgliedschaft.verlaengere_alle(db, kunden_ids: Sequence[UUID], *, admin_user_id) -> list[Kunde]` (nur tatsächlich verlängerte), `mitgliedschaft.beende_alle(db, kunden_ids, *, grund: str, admin_user_id) -> list[tuple[Kunde, bool]]` (bool: galt bis heute → Mail), `mitgliedschaft.abgleich_warnung(db, heute: date) -> str | None`, `mitgliedschaft.Tageslauf(abgleich: int | None, erinnert: list[UUID])`, `mitgliedschaft.tageslauf(db) -> Tageslauf`, `mitgliedschaft.ABGLEICH_MARKER = "mitglieder_abgleich_letzter"`.
- Produces: `rechnungen.csv_sicher(wert: str) -> str` (bisher `_csv_sicher`).
- Produces: `benachrichtigung.mitgliedschaft_erinnerung(db, kunde)` (Betreff „Ihre Mitgliedschaft läuft bald ab“), `benachrichtigung.abgleich_faellig(anzahl: int)` (Betreff „[Beachhub] Jahresabgleich der Mitglieder“).
- Produces: `jobs.mitgliedschaft_ausfuehren(db) -> None`; Scheduler-Job `mitgliedschaft` täglich 07:00.
- Produces: Routen `GET /admin/kunden/abgleich`, `GET /admin/kunden/abgleich.csv`, `POST /admin/kunden/abgleich` (Felder `aktion` = `verlaengern`|`beenden`, `kunde_ids` mehrfach, `grund`); Navigation „Kunden“ → „Mitglieder-Abgleich“.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_mitgliedschaft.py`:
```python
def _mitglied(db: Session, name: str, bis: date, beendet: bool = False):
    k = kunden.lege_an(db, name=name, email=f"{name.lower()}@x.de")
    k.mitglied_bis = bis
    if beendet:
        k.mitglied_beendet_am = utcnow()
    return k


def test_pruefliste_umfasst_auslaufende_und_ausgelaufene(db: Session, welt) -> None:
    heute = date(2027, 8, 31)
    clock.set_override(db, heute)
    ausgelaufen = _mitglied(db, "Alt", date(2027, 4, 30))
    laufend = _mitglied(db, "Neu", date(2028, 4, 30))
    _mitglied(db, "Uralt", date(2026, 4, 30))  # vorletzte Saison: schon abgeglichen
    _mitglied(db, "Weg", date(2027, 4, 30), beendet=True)  # ausdrücklich beendet
    db.commit()
    assert mitgliedschaft.pruefliste(db, heute) == [ausgelaufen, laufend]


def test_sammelaktionen(db: Session, welt) -> None:
    heute = date(2027, 8, 31)
    clock.set_override(db, heute)
    a = _mitglied(db, "A", date(2027, 4, 30))
    b = _mitglied(db, "B", date(2028, 4, 30))
    db.commit()
    assert mitgliedschaft.verlaengere_alle(db, [a.id, b.id], admin_user_id=None) == [a]
    db.commit()
    assert a.mitglied_bis == date(2028, 4, 30)  # b war schon bis dahin freigeschaltet
    c = _mitglied(db, "C", date(2027, 4, 30))
    db.commit()
    ergebnis = mitgliedschaft.beende_alle(
        db, [b.id, c.id], grund="kein Mitglied mehr", admin_user_id=None
    )
    db.commit()
    assert [(k.name, galt) for k, galt in ergebnis] == [("B", True), ("C", False)]
    assert mitgliedschaft.pruefliste(db, heute) == [a]


def test_pruefliste_csv(db: Session, welt) -> None:
    k = _mitglied(db, "=Anna", date(2027, 4, 30))
    k.mitglied_antrag_hinweis = "Nr. 4711"
    db.commit()
    zeilen = mitgliedschaft.pruefliste_csv([k]).strip().splitlines()
    # Werte, die mit = beginnen, entschärft csv_sicher mit einem Hochkomma (Formel-Injection).
    assert zeilen == [
        "name;email;mitglied_bis;angaben_im_antrag",
        "'=Anna;'=anna@x.de;2027-04-30;Nr. 4711",
    ]


@pytest.mark.parametrize("saisonstart,warnt", [(date(2027, 9, 15), False), (date(2027, 9, 10), True)])
def test_abgleich_warnung(db: Session, saisonstart: date, warnt: bool) -> None:
    db.add(Betriebszeit(wochentag=0, oeffnet=time(9), schliesst=time(23), gueltig_von=saisonstart))
    db.commit()
    assert (mitgliedschaft.abgleich_warnung(db, date(2027, 6, 1)) is not None) is warnt


def test_ohne_befristete_betriebszeiten_keine_warnung(db: Session, welt) -> None:
    assert mitgliedschaft.abgleich_warnung(db, date(2027, 6, 1)) is None


def test_tageslauf_meldet_abgleich_einmal_und_erinnert_einmal(db: Session, welt) -> None:
    _, k = welt
    k.mitglied_bis = date(2028, 4, 30)
    db.commit()
    clock.set_override(db, date(2027, 8, 30))
    lauf = mitgliedschaft.tageslauf(db)
    db.commit()
    assert lauf.abgleich is None and lauf.erinnert == []
    clock.set_override(db, date(2027, 8, 31))
    assert mitgliedschaft.tageslauf(db).abgleich == 1
    db.commit()
    assert mitgliedschaft.tageslauf(db).abgleich is None  # nur einmal im Jahr
    db.commit()
    clock.set_override(db, date(2028, 4, 16))  # 14 Tage vor Ablauf
    assert mitgliedschaft.tageslauf(db).erinnert == [k.id]
    db.commit()
    assert mitgliedschaft.tageslauf(db).erinnert == []  # je Ablaufdatum nur einmal
    assert k.mitglied_erinnert_fuer == date(2028, 4, 30)
```
`core/tests/test_jobs.py` (Importe: `from beachhub_core.services import buchungen, kunden`):
```python
def test_mitgliedschaft_tageslauf_schickt_mails_nach_commit(db: Session, mail_ausgang: list) -> None:
    k = kunden.lege_an(db, name="Anna", email="anna@x.de")
    k.mitglied_bis = date(2028, 4, 30)
    db.commit()
    clock.set_override(db, date(2028, 4, 20))
    jobs.mitgliedschaft_ausfuehren(db)
    assert [m["betreff"] for m in mail_ausgang] == ["Ihre Mitgliedschaft läuft bald ab"]
    db.refresh(k)
    assert k.mitglied_erinnert_fuer == date(2028, 4, 30)


def test_mitgliedschaft_tageslauf_meldet_abgleich(db: Session, mail_ausgang: list) -> None:
    clock.set_override(db, date(2027, 8, 31))
    jobs.mitgliedschaft_ausfuehren(db)
    assert [m["betreff"] for m in mail_ausgang] == ["[Beachhub] Jahresabgleich der Mitglieder"]
```

`core/tests/test_ui_mitgliedschaft.py`:
```python
def test_abgleich_seite_csv_und_verlaengern(
    eingeloggt: TestClient, db: Session, mail_ausgang: list
) -> None:
    c = eingeloggt
    clock.set_override(db, date(2027, 8, 31))
    k = kunden.lege_an(db, name="Anna", email="anna@x.de")
    k.mitglied_bis = date(2027, 4, 30)
    k.mitglied_antrag_hinweis = "Nr. 4711"
    db.commit()
    seite = c.get("/admin/kunden/abgleich")
    assert "Anna" in seite.text and "30.04.2027" in seite.text
    assert "bis 30.04.2028 verlängern" in seite.text
    csv = c.get("/admin/kunden/abgleich.csv")
    assert csv.headers["content-type"].startswith("text/csv")
    assert "Anna;anna@x.de;2027-04-30;Nr. 4711" in csv.text
    r = c.post(
        "/admin/kunden/abgleich",
        data={"csrf_token": c.csrf, "aktion": "verlaengern", "kunde_ids": [str(k.id)]},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(k)
    assert k.mitglied_bis == date(2028, 4, 30)
    assert [m["betreff"] for m in mail_ausgang] == ["Ihre Mitgliedschaft ist freigeschaltet"]


def test_abgleich_ohne_auswahl_meldet_fehler(eingeloggt: TestClient) -> None:
    c = eingeloggt
    seite = c.post("/admin/kunden/abgleich", data={"csrf_token": c.csrf, "aktion": "beenden"})
    assert "mindestens einen Kunden" in seite.text
```

`core/tests/test_ui_stammdaten.py` (Importe: `from datetime import date, time`, `from beachhub_core import clock`, `Betriebszeit` in die Modell-Importe):
```python
def test_einstellungen_warnen_vor_spaetem_abgleich(eingeloggt: TestClient, db: Session) -> None:
    clock.set_override(db, date(2027, 6, 1))
    db.add(
        Betriebszeit(
            wochentag=0, oeffnet=time(9), schliesst=time(23), gueltig_von=date(2027, 9, 10)
        )
    )
    db.commit()
    assert "liegt nach dem ersten Buchungsfenster" in eingeloggt.get("/admin/konfiguration").text
```

`core/tests/test_ui_navigation.py`: `"/admin/kunden/abgleich"` in `SEITEN` ergänzen.

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_mitgliedschaft.py tests/test_jobs.py tests/test_ui_mitgliedschaft.py tests/test_ui_stammdaten.py tests/test_ui_navigation.py`
Expected: FAIL – `AttributeError: … has no attribute 'pruefliste'`, `KeyError: 'mitglieder_abgleich'`.

- [ ] **Step 3: Modell, Migration, Einstellungen**

`core/beachhub_core/models/kunden.py`, nach `mitglied_beendet_grund`:
```python
    # Das mitglied_bis, an dessen Ablauf zuletzt erinnert wurde – je Ablauf höchstens eine Mail.
    mitglied_erinnert_fuer: Mapped[date | None] = mapped_column(Date)
```

`core/alembic/versions/0013_mitglied_erinnert.py`:
```python
"""mitglied_erinnert: merkt, an welchen Ablauf der Mitgliedschaft zuletzt erinnert wurde
(A-MAIL-2).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("kunde", sa.Column("mitglied_erinnert_fuer", sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column("kunde", "mitglied_erinnert_fuer")
```

`core/beachhub_core/services/konfiguration.py`, in `DEFAULTS` nach `"mitgliedschaft_ablauf"`:
```python
    "mitglieder_abgleich": (TagMonat, TagMonat("31.08.")),
    "mitglied_erinnerung_tage": (int, 14),
```
und in `BESCHREIBUNGEN` nach `"mitgliedschaft_ablauf"`:
```python
    "mitglieder_abgleich": Beschreibung(
        "Mitgliedschaft",
        "Jährlicher Abgleich",
        "Tag und Monat",
        "An diesem Tag bekommen Sie eine E-Mail mit der Prüfliste (Kunden → Mitglieder-Abgleich). "
        "Er muss vor dem ersten Buchungsfenster der Saison liegen, sonst buchen Mitglieder die "
        "ersten Termine zu Preisen für Nicht-Mitglieder.",
    ),
    "mitglied_erinnerung_tage": Beschreibung(
        "Mitgliedschaft",
        "Erinnerung vor Ablauf",
        "Tage",
        "So viele Tage vor Ablauf der Mitgliedschaft bekommt der Kunde eine Erinnerung. "
        "0 schaltet die Erinnerung ab.",
    ),
```

`core/beachhub_core/services/rechnungen.py`: `_csv_sicher` in `csv_sicher` umbenennen (Definition und Aufruf in `csv_export`).

- [ ] **Step 4: Prüfliste, Sammelaktionen, Warnung, Tageslauf**

`core/beachhub_core/services/mitgliedschaft.py`:
- Importe ergänzen: `import csv`, `import io`, `from collections.abc import Sequence`, `from dataclasses import dataclass, field`, `from sqlalchemy import func, select`, `from beachhub_core.models import AppSetting, Betriebszeit, Buchung, Kunde, utcnow`, `from beachhub_core.services.rechnungen import csv_sicher`.
- Am Dateiende:
```python
ABGLEICH_MARKER = "mitglieder_abgleich_letzter"


def pruefliste(db: Session, heute: date) -> list[Kunde]:
    """Prüfliste des Jahresabgleichs (A-KUND-5, Abweichung A-10): Mitgliedschaften, die seit dem
    Jahr vor dem letzten Ablauftag geendet haben oder bis zum nächsten Ablauftag enden – ohne
    ausdrücklich beendete."""
    letzter = letzter_ablauf(db, heute)
    von = letzter.replace(year=letzter.year - 1)
    bis = naechster_ablauf(db, heute)
    return list(
        db.scalars(
            select(Kunde)
            .where(
                Kunde.anonymisiert_am.is_(None),
                Kunde.mitglied_beendet_am.is_(None),
                Kunde.mitglied_bis > von,
                Kunde.mitglied_bis <= bis,
            )
            .order_by(Kunde.name)
        ).all()
    )


def pruefliste_csv(kunden: Sequence[Kunde]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(["name", "email", "mitglied_bis", "angaben_im_antrag"])
    for k in kunden:
        w.writerow(
            [
                csv_sicher(k.name),
                csv_sicher(k.email),
                k.mitglied_bis.isoformat() if k.mitglied_bis else "",
                csv_sicher(k.mitglied_antrag_hinweis),
            ]
        )
    return buf.getvalue()


def verlaengere_alle(
    db: Session, kunden_ids: Sequence[uuid.UUID], *, admin_user_id: uuid.UUID | None
) -> list[Kunde]:
    """Sammelaktion „bis zum nächsten Ablauftag verlängern“. Wer schon so lange freigeschaltet
    ist, bleibt unberührt und bekommt keine Mail."""
    bis = naechster_ablauf(db, clock.today(db))
    verlaengert = []
    for k in db.scalars(
        select(Kunde)
        .where(Kunde.id.in_(kunden_ids), Kunde.anonymisiert_am.is_(None))
        .order_by(Kunde.name)
    ).all():
        if k.mitglied_bis is not None and k.mitglied_bis >= bis:
            continue
        freischalten(db, k, bis=bis, admin_user_id=admin_user_id)
        verlaengert.append(k)
    return verlaengert


def beende_alle(
    db: Session,
    kunden_ids: Sequence[uuid.UUID],
    *,
    grund: str,
    admin_user_id: uuid.UUID | None,
) -> list[tuple[Kunde, bool]]:
    """Sammelaktion „beenden“. Liefert je Kunde, ob die Mitgliedschaft bis heute galt – nur
    diese Kunden bekommen eine Mail."""
    return [
        (k, beende(db, k, grund=grund, admin_user_id=admin_user_id))
        for k in db.scalars(
            select(Kunde)
            .where(
                Kunde.id.in_(kunden_ids),
                Kunde.anonymisiert_am.is_(None),
                Kunde.mitglied_bis.is_not(None),
            )
            .order_by(Kunde.name)
        ).all()
    ]


def abgleich_warnung(db: Session, heute: date) -> str | None:
    """Warnt, wenn der Stichtag des Abgleichs nach dem ersten Buchungsfenster der nächsten Saison
    liegt (A-KUND-5). Saisonstart ist das früheste künftige `betriebszeit.gueltig_von`
    (Abweichung A-12)."""
    saisonstart = db.scalar(
        select(func.min(Betriebszeit.gueltig_von)).where(Betriebszeit.gueltig_von > heute)
    )
    if saisonstart is None:
        return None
    tag_monat = konfiguration.hole(db, "mitglieder_abgleich")
    stichtag: date = tag_monat.im_jahr(saisonstart.year)
    if stichtag > saisonstart:
        stichtag = tag_monat.im_jahr(saisonstart.year - 1)
    fenster_beginn = saisonstart - timedelta(days=konfiguration.hole(db, "fenster_tage"))
    if stichtag <= fenster_beginn:
        return None
    return (
        f"Der Mitglieder-Abgleich am {stichtag:%d.%m.%Y} liegt nach dem ersten Buchungsfenster "
        f"der Saison ab {saisonstart:%d.%m.%Y} (gebucht wird ab {fenster_beginn:%d.%m.%Y}). "
        "Mitglieder würden die ersten Termine zu Preisen für Nicht-Mitglieder buchen. Bitte den "
        "Stichtag in den Einstellungen vorziehen."
    )


@dataclass
class Tageslauf:
    # Anzahl Kunden auf der Prüfliste, wenn der Stichtag in diesem Jahr erreicht und noch nicht
    # gemeldet war; sonst None.
    abgleich: int | None = None
    erinnert: list[uuid.UUID] = field(default_factory=list)


def tageslauf(db: Session) -> Tageslauf:
    """Täglich: den Abgleich einmal im Jahr melden (auch nachträglich, falls der Lauf am Stichtag
    ausfiel) und vor Ablauf an die Mitgliedschaft erinnern. Committet nicht; die Mails verschickt
    jobs.mitgliedschaft_ausfuehren nach dem Commit."""
    heute = clock.today(db)
    lauf = Tageslauf()
    stichtag = konfiguration.hole(db, "mitglieder_abgleich").im_jahr(heute.year)
    marker = db.get(AppSetting, ABGLEICH_MARKER)
    if heute >= stichtag and (marker is None or marker.value != str(heute.year)):
        lauf.abgleich = len(pruefliste(db, heute))
        if marker is None:
            db.add(AppSetting(key=ABGLEICH_MARKER, value=str(heute.year)))
        else:
            marker.value = str(heute.year)
    tage = konfiguration.hole(db, "mitglied_erinnerung_tage")
    if tage > 0:
        for k in db.scalars(
            select(Kunde).where(
                Kunde.anonymisiert_am.is_(None),
                Kunde.mitglied_bis >= heute,
                Kunde.mitglied_bis <= heute + timedelta(days=tage),
            )
        ).all():
            if k.mitglied_erinnert_fuer != k.mitglied_bis:
                k.mitglied_erinnert_fuer = k.mitglied_bis
                lauf.erinnert.append(k.id)
    db.flush()
    return lauf
```

- [ ] **Step 5: Mails und Job**

`core/beachhub_core/services/benachrichtigung.py`:
```python
def mitgliedschaft_erinnerung(db: Session, k: Kunde) -> None:
    mail.sende(
        k.email, "Ihre Mitgliedschaft läuft bald ab", _text("mitgliedschaft_erinnerung", k=k)
    )


def abgleich_faellig(anzahl: int) -> None:
    betreiber_alarm(
        "Jahresabgleich der Mitglieder",
        "Der Stichtag für den jährlichen Abgleich der Mitglieder ist erreicht. Auf der Prüfliste "
        f"stehen {anzahl} Kunden.\n\nBitte in der Verwaltung unter Kunden → Mitglieder-Abgleich "
        "gegen die Mitgliederliste des Vereins prüfen und verlängern oder beenden.",
    )
```

`core/beachhub_core/templates/mail/mitgliedschaft_erinnerung.txt`:
```
Hallo {{ k.name }},

Ihre Mitgliedschaft im Verein ist bei uns bis zum {{ k.mitglied_bis|datum }} freigeschaltet.
Danach gelten für neue Termine die Preise für Nicht-Mitglieder.

Beim jährlichen Abgleich mit der Mitgliederliste des Vereins verlängern wir die Freischaltung.
Möchten Sie vorher Termine nach diesem Tag buchen und sind weiterhin Mitglied, können Sie die
Verlängerung im Portal beantragen.

Viele Grüße
{{ betreiber }}
```

`core/beachhub_core/jobs.py`:
- Importe: `Kunde` in `from beachhub_core.models import AppSetting, Kunde, Rechnung`; `mitgliedschaft` in die Service-Importe.
- Nach `verfall_ausfuehren`:
```python
def mitgliedschaft_ausfuehren(db: Session) -> None:
    """Jahresabgleich melden und an den Ablauf der Mitgliedschaft erinnern (A-KUND-5, A-MAIL-2).
    Erst committen, dann Mails – ein Rollback darf keine Mail zurücklassen."""
    lauf = mitgliedschaft.tageslauf(db)
    db.commit()
    if lauf.abgleich is not None:
        benachrichtigung.abgleich_faellig(lauf.abgleich)
    for kunde_id in lauf.erinnert:
        k = db.get(Kunde, kunde_id)
        if k is not None:
            benachrichtigung.mitgliedschaft_erinnerung(db, k)


def _job_mitgliedschaft() -> None:
    with SessionLocal() as db:
        try:
            mitgliedschaft_ausfuehren(db)
        except Exception:
            logger.exception("Tageslauf der Mitgliedschaft fehlgeschlagen")
```
- In `starte_scheduler` nach dem Job `verfall`:
```python
    s.add_job(
        _job_mitgliedschaft,
        CronTrigger(hour=7, minute=0),
        id="mitgliedschaft",
        replace_existing=True,
    )
```

- [ ] **Step 6: Seite „Mitglieder-Abgleich“ und Warnung in den Einstellungen**

`core/beachhub_core/routes/kunden.py`:
- Importe: `from fastapi.responses import HTMLResponse, RedirectResponse, Response`; `konfiguration` in die Service-Importe.
- **Vor** `GET /kunden/{kunde_id}` (neben `antraege`):
```python
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
```

`core/beachhub_core/templates/kunden/abgleich.html`:
```html
{% extends "base.html" %}{% block title %}Mitglieder-Abgleich{% endblock %}
{% block content %}
<h1>Mitglieder-Abgleich</h1>
<p class="hinweis">Einmal im Jahr (Stichtag {{ stichtag }}, siehe Einstellungen) gleichen Sie die freigeschalteten Mitglieder mit der Mitgliederliste des Vereins ab. Hier stehen alle Kunden, deren Mitgliedschaft zum {{ letzter|datum }} ausgelaufen ist oder bis zum {{ naechster|datum }} ausläuft.</p>
{% if warnung %}<p class="fehler">{{ warnung }}</p>{% endif %}
<p><a class="aktion" href="/admin/kunden/abgleich.csv">Als CSV herunterladen</a></p>
<form method="post" action="/admin/kunden/abgleich" class="karte">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <table>
    <thead><tr><th></th><th>Name</th><th>E-Mail</th><th>Mitglied bis</th><th>Angaben im Antrag</th></tr></thead>
    <tbody>
    {% for k in kunden %}<tr>
      <td><input type="checkbox" name="kunde_ids" value="{{ k.id }}" aria-label="{{ k.name }} auswählen"></td>
      <td><a href="/admin/kunden/{{ k.id }}">{{ k.name }}</a></td>
      <td>{{ k.email }}</td>
      <td>{{ k.mitglied_bis|datum }}</td>
      <td>{{ k.mitglied_antrag_hinweis }}</td>
    </tr>{% else %}
      <tr><td colspan="5" class="leer">Niemand zu prüfen.</td></tr>
    {% endfor %}
    </tbody>
  </table>
  {% if kunden %}
  <div class="zeile">
    <button name="aktion" value="verlaengern">Ausgewählte bis {{ naechster|datum }} verlängern</button>
    <label>Grund <input name="grund" placeholder="Jahresabgleich"></label>
    <button name="aktion" value="beenden" class="gefahr">Ausgewählte beenden</button>
  </div>
  {% endif %}
</form>
{% endblock %}
```

`core/beachhub_core/routes/stammdaten.py`:
- Importe: `from beachhub_core import auth, clock`; `mitgliedschaft` in die Service-Importe.
- In `_einstellungen` an `render(...)` `warnung=mitgliedschaft.abgleich_warnung(db, clock.today(db)),` übergeben.

`core/beachhub_core/templates/stammdaten/konfiguration.html`, nach der Zeile mit `fehler`:
```html
{% if warnung %}<p class="fehler">{{ warnung }}</p>{% endif %}
```

`core/beachhub_core/navigation.py`, Bereich „Kunden“: `Punkt("Mitglieder-Abgleich", "/admin/kunden/abgleich"),` als dritten Unterpunkt ergänzen.

- [ ] **Step 7: Tests und Lint**

Run:
```bash
(cd core && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 8: Commit**

```bash
git add core
git commit -m "feat(core): Jahresabgleich der Mitglieder, Erinnerung vor Ablauf und Tageslauf"
```

---
## Task 8: Betriebshandbuch, README, Spec-Abgleich und Gesamtprüfung

**Files:**
- Modify: `docs/betrieb/hauptsystem.md` (§ 4, § 5)
- Modify: `README.md` (Statuszeile)
- Modify: `docs/superpowers/specs/2026-09-05-beachhub-design.md` (A-KUND-5, A-KUND-6, A-MAIL-2, § 3.14, § 5)

**Interfaces:** keine neuen.

- [ ] **Step 1: Betriebshandbuch**

`docs/betrieb/hauptsystem.md`, in § 4 die Punkte 3 bis 5 ersetzen:
```markdown
3. **Kundengruppen** prüfen: Es gibt genau zwei feste Gruppen, „DJK-Mitglied“ (7 %) und
   „Nicht-Mitglied“ (19 %). Name und Steuersatz lassen sich ändern, eine dritte Gruppe nicht.
   Welche Gruppe für einen Kunden gilt, ergibt sich aus seiner Mitgliedschaft am Tag des Termins.
4. **Tarife** hinterlegen – ohne Gruppe für alle, mit Gruppe für Mitglieder bzw. Nicht-Mitglieder.
5. **Einstellungen** prüfen/anpassen, u. a. `rechnung_zahlungsziel_tage` (Standard: 14),
   `event_ust_satz` (19 % – Vorbelegung für Buchungen, die der Betreiber anlegt),
   `mitgliedschaft_ablauf` (30.04.), `mitglieder_abgleich` (31.08., muss vor dem ersten
   Buchungsfenster der Saison liegen – die Seite warnt sonst), `mitglied_erinnerung_tage` (14)
   und `rechnungskunden_online_buchen` (nein).
```

In § 5 den ersten Absatz zum Monatslauf ersetzen:
```markdown
Der Monatslauf erzeugt bis zur Einführung der Saisonrechnung (Stufe 1a-II) Sammelrechnungen für
die **Termine von Dauerbuchungen** des Vormonats: Der APScheduler-Job prüft **täglich um 06:00
Uhr**, ob der aktuelle Tag den Wert `rechnung_tag_im_folgemonat` erreicht hat. Buchungen, die der
Betreiber selbst anlegt, bekommen sofort eine eigene, offene Rechnung mit dem Zahlungsziel
`rechnung_zahlungsziel_tage`.
```

Nach dem Absatz zu den Logs (vor „## 5a. Kanal zum Portal“) einfügen:
```markdown
### Mitgliedschaft

- **Anträge** aus dem Portal kommen per Mail und stehen unter Kunden → Mitgliedsanträge.
  Freischalten, Verlängern, Beenden und Verwerfen geschieht auf der Kundenseite; der Kunde bekommt
  jeweils eine Mail.
- **Tageslauf um 07:00 Uhr:** Am Stichtag `mitglieder_abgleich` (einmal im Jahr, bei Ausfall am
  nächsten Lauf nachgeholt) kommt eine Mail an den Betreiber; die Prüfliste steht unter Kunden →
  Mitglieder-Abgleich (mit CSV und Sammelaktionen). Kunden, deren Mitgliedschaft in
  `mitglied_erinnerung_tage` Tagen endet, bekommen eine Erinnerung – je Ablaufdatum höchstens eine.
- **Klärungsliste** (System → Klärung Mitgliedschaft): künftige Buchungen zum Mitgliedspreis, deren
  Kunde am Termin kein Mitglied mehr ist. Die Buchungen behalten Preis und Steuersatz, bis sie
  geklärt oder storniert sind.
- **Rechnungskunden** buchen nicht online, solange `rechnungskunden_online_buchen` aus ist; ihre
  Buchungen legt der Betreiber an.
```

- [ ] **Step 2: README**

`README.md`, die Statuszeile ersetzen:
```markdown
Status: Stufe 1 (Hauptsystem) fertig; Stufe 1a-I (feste Kundengruppen mit Steuersatz,
Mitgliedschaft mit Antrag, Abgleich und Klärungsliste, Rechnungskunden) umgesetzt, 1a-II
(Saisonrechnung) und 1a-III (Gutscheine) folgen; Portal-Kern (Stufe 2 ohne Mitgliedschaft,
Gutscheine und echten Zahlungsanbieter) implementiert; Stufe 3 (Hallendienst) implementiert und
gegen simuliertes Home Assistant getestet, die Anbindung an die echte Hallentechnik folgt, sobald
der Hallenhersteller die Schnittstellen festlegt.
```

- [ ] **Step 3: Spec an die Umsetzung angleichen**

`docs/superpowers/specs/2026-09-05-beachhub-design.md`:
- A-KUND-6: `kunden.effektive_gruppe(kunde, leistungsdatum)` → `kundengruppen.effektive_gruppe(db, kunde, leistungsdatum)`.
- A-KUND-5: an den Absatz anhängen: „Saisonstart ist dabei das früheste künftige ‚gültig ab‘ der Betriebszeiten; ohne befristete Betriebszeiten entfällt die Warnung. Die Prüfliste umfasst Mitgliedschaften, die im Jahr vor dem letzten Ablauftag bis einschließlich zum nächsten Ablauftag enden, ohne ausdrücklich beendete.“
- A-MAIL-2: „Erinnerung vor Ablauf der Mitgliedschaft“ → „Erinnerung vor Ablauf der Mitgliedschaft (`mitglied_erinnerung_tage` vorher, je Ablauf einmal)“.
- § 3.14, Tabelle: nach der Zeile `mitglieder_abgleich` einfügen:
  `| \`mitglied_erinnerung_tage\` | 14 | 0 (keine Erinnerung) bis beliebig | A-MAIL-2 |`
- § 5, Zeile `kunde`: am Ende `, mitglied_erinnert_fuer?` ergänzen; Zeile `buchung`: am Ende `, gruppe_geklaert_am? (Klärungsliste, A-KUND-4)` ergänzen; Zeile `rechnung_position`: `ust_satz (Summen je Satz, A-RECH-8)` → `ust_satz, netto, ust (je Position gerundet, Summen je Satz, A-RECH-8)`.

- [ ] **Step 4: Reste der alten Modelle suchen**

Run:
```bash
grep -rn "standard_zahlungsart\|portal_kundengruppe\|kunde\.zahlungsart\|zahlungsart == \"rechnung\"\|\"ust_satz\": (\|keine_kundengruppe" core shared portal e2e --include=*.py --include=*.html
```
Expected: keine Treffer außer in `core/alembic/versions/` (Migrationen 0001–0011 dürfen die alten Namen enthalten).

- [ ] **Step 5: Gesamtprüfung**

Run:
```bash
(cd shared && pytest -q) && (cd hall && pytest -q) && (cd core && pytest -q) && (cd portal && pytest -q) && pytest -q e2e
ruff format --check . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 6: Commit**

```bash
git add README.md docs
git commit -m "docs: Kundengruppen, Mitgliedschaft und Rechnungskunden im Betriebshandbuch und in der Spec"
```

Danach den Branch mit superpowers:finishing-a-development-branch abschließen: nach `main` mergen (`--no-ff`), pushen, Worktree und Branch entfernen.
