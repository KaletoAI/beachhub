# Stufe 1a-II: Saisonrechnung, Abo-Absagen und Korrekturbelege – Implementierungsplan

> Historischer Plan. Korrektur vom 05.10.2026 nach externem Review: B-8 „Vollstorno ohne Guthaben“
> ist ersetzt. Vollstorno erstattet den verbleibenden bezahlten Anteil mit Korrekturbeleg,
> bewahrt vorherige Teilkorrekturen und erlaubt die Neuberechnung kostenpflichtiger Absagen.
> Kontingent verbraucht jede kostenfreie Kundenabsage außer für bereits gutgeschriebene
> Termine. Die aktuelle Fachregel
> steht in der Spezifikation A-DAUER-3/A-RECH-3/A-RECH-4/A-RECH-7; die ursprünglichen
> Planentscheidungen und Codeblöcke bleiben hier zur Nachvollziehbarkeit erhalten.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jede Dauerbuchung bekommt bei der Anlage genau eine Saisonrechnung (mit Verrechnung vorhandenen Guthabens) statt des Monatslaufs; Abos gibt es nur für Rechnungskunden und – einstellbar – nur für Mitglieder; Kunden sagen Abo-Termine im Portal ab, bis zu drei davon kostenfrei; jede Gutschrift entsteht nur noch zusammen mit einer (Teil-)Stornorechnung, und zum Saisonende gibt es eine Guthabenliste.

**Architecture:** Kern ist der Korrekturbeleg: `rechnungen.korrigiere()` erzeugt eine (Teil-)Stornorechnung über ausgewählte Positionen, `rechnungen.offener_betrag()` rechnet Korrekturen und Zahlungen gegen, und `storno.gutschreiben*()` ist der einzige Weg zu Guthaben der Art `storno_gutschrift` (A-STORNO-6): Gutgeschrieben wird nur, was schon bezahlt war; sonst sinkt die Forderung. Darauf bauen die Saisonrechnung (`rechnungen.erzeuge_saisonrechnung`, Guthaben als `Zahlung` mit `provider = "guthaben"`), das Beenden von Dauerbuchungen (ein Beleg für alle betroffenen Termine) und die freien Abo-Absagen in `storno.storniere` auf. Der Monatslauf entfällt vollständig. Kanal und Lesestand bekommen die Felder für Abo-Absagen; die Portal-Oberfläche dazu folgt mit Stufe 2.

**Tech Stack:** Python 3.12, FastAPI 0.115.6, SQLAlchemy 2.0.36, Alembic 1.14, PostgreSQL 16, Jinja2, pydantic 2.10, APScheduler 3.11, WeasyPrint 63.1, pytest 8.3, ruff 0.8.4, mypy 1.13.

**Spec:** `docs/superpowers/specs/2026-09-05-beachhub-design.md` – § 3.5 (A-DAUER-2, -3, -4, -5), § 3.7 (A-STORNO-1, -2, -4, -6), § 3.8 (A-ZAHL-4, A-ZAHL-7), § 3.9 (A-RECH-3, -4, -7), § 3.12 (A-MAIL-2: Stornobestätigung mit freien Absagen, Saisonrechnung), § 3.13 (A-ADM-3 Guthabenliste, A-ADM-4 Teil-Stornorechnung, kein Monatslauf), § 3.14, § 5 (Tabellen `rechnung`, `storno`, `zahlung`), § 8.1 (`buchung_stornieren`).

**Setzt voraus:** Plan 1a-I ist umgesetzt und gemergt (`docs/superpowers/plans/2026-10-03-stufe-1a-I-kundengruppen-mitgliedschaft.md`): feste Kundengruppen, `Buchung.ust_satz`, Steuersatz je Position (`rechnungen.Posten`, `steuer_je_satz`), `konfiguration.TagMonat`, Ja/Nein-Einstellungen, `services/mitgliedschaft.py`, Migrationen 0010–0013.

## Stufe 1a in drei Plänen

Stufe 1a ist in drei Pläne geteilt. Jeder wird für sich umgesetzt, geprüft und nach `main` gemergt:

1. **1a-I:** Kundengruppen, Steuersatz je Buchung und Rechnungsposition, Betreiberbuchung mit Event-Steuersatz, Mitgliedschaft mit Antrag, Klärungsliste, Jahresabgleich und Erinnerung, Rechnungskunden im Kanal.
2. **1a-II (dieser Plan):** Saisonrechnung statt Monatslauf, Abo nur für Rechnungskunden und Mitglieder, freie Abo-Absagen, Teil-Stornorechnung mit Korrekturbeleg, Guthabenverrechnung auf der Saisonrechnung, Guthabenliste zum Saisonende.
3. **1a-III:** Gutscheine und Freischaltcodes (Kauf, Einlösung in der Buchung, Storno, Verfall, Rückgabe, Admin-Seite).

Die Portal-Oberfläche (Anzeige der verbleibenden freien Absagen, Absage-Knopf an Abo-Terminen) gehört zum Rest von Stufe 2; dieser Plan liefert die Seite des Hauptsystems und den Vertrag in `shared`.

## Global Constraints

- Python **3.12**; jedes Paket mit eigenem `pyproject.toml`, Abhängigkeiten mit exakten Versionen (`==`). Dieser Plan fügt **keine** neue Abhängigkeit hinzu.
- Geldbeträge und Steuersätze durchgehend `decimal.Decimal` (`DECIMAL(10, 2)` bzw. `DECIMAL(5, 2)`), nie `float` (N-7). Gerundet wird `ROUND_HALF_UP` auf Cent, **je Position** (A-RECH-8).
- Zeitstempel UTC-aware speichern, Anzeige in `Europe/Berlin` (N-7). Fachliche Tage („heute“, Leistungsdatum) immer über `clock.today(db)` bzw. `beachhub_shared.zeit.lokales_datum`.
- Oberfläche des Hauptsystems auf Deutsch, siezt, serverseitig gerendert, **kein Frontend-Build**, keine Inline-Skripte.
- Jede Änderung an Kunden, Buchungen, Rechnungen, Kundengruppen und Einstellungen erzeugt einen Audit-Eintrag (N-6) über `services/audit.py`.
- Mails, Rechnungs-PDFs und Betreiber-Alarme erst **nach** dem Commit (Muster `routes/belegung.py:buchung_anlegen`, `services/ergebnis.py`).
- Jede Nutzlast aus dem Portal ist nicht vertrauenswürdig; den Kunden bestimmt `kunde.portal_konto_id` (Portal-Kern-Design § 4).
- Jede Schemaänderung bekommt eine Alembic-Migration; `core/tests/test_migrationen.py` (upgrade head, downgrade base auf leerer DB) bleibt grün. **Alle Schemaänderungen dieses Plans stehen in genau einer Migration `0014_saison_und_korrektur.py`** (Task 1), damit Plan 1a-III verlässlich auf `0014` aufsetzt.
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

## Abweichungen von der Spec und vom Vertrag zwischen den Plänen

- **B-1 Zusätzliche Spalte `storno.korrektur_rechnung_id`** (in Migration 0014, über den Vertrag hinaus, rein additiv). Sie verknüpft ein Storno mit seinem Korrekturbeleg, damit Buchungsseite und Mailversand den Beleg finden.
- **B-2 Zusätzliche optionale Parameter** gegenüber dem Vertrag: `rechnungen.korrigiere(..., admin_user_id=None)`, `storno.storniere(..., korrigieren=True)`, `storno.gutschreiben(..., admin_user_id=None)`; neu `storno.gutschreiben_positionen()` und `storno.gutschreiben_alle()`; `dauerbuchungen.beende()` liefert jetzt die Korrekturbelege (bisher `None`), `dauerbuchungen.lege_an()` bekommt `rechnungskunde_setzen`. Die Vertragssignaturen bleiben aufrufbar wie vereinbart.
- **B-3 Teilweise bezahlte Rechnungen** (die Spec kennt nur „bezahlt“ und „offen“): Eine Korrektur senkt zuerst die offene Forderung; als Guthaben gutgeschrieben wird nur, was die Korrektur über den offenen Betrag hinaus ausmacht (`max(0, Korrektur − offener Betrag vorher)`). Für „bezahlt“ (offener Betrag 0) ergibt das den vollen Betrag, für „offen“ ohne Zahlung nichts – wie in A-RECH-7.
- **B-4 Eine Rechnung gilt als `storniert`, sobald alle ihre Positionen korrigiert sind**, auch durch mehrere Teil-Stornos.
- **B-5 `saisonende_guthabenliste` ist ein Stichtag (`TagMonat`, Vorgabe 30.04.)** statt „letzter Tag der Saison“: Eine Saison ist im System nicht als Datum hinterlegt (vgl. 1a-I, Abweichung A-12). Ab dem Stichtag geht einmal im Jahr eine Mail an den Betreiber; die Liste selbst ist jederzeit unter Kunden → Guthabenliste abrufbar.
- **B-6 Kunden stornieren Termine ihrer Dauerbuchungen im Portal** (bisher „nicht stornierbar“). Vom Betreiber angelegte Einzelbuchungen (Zahlungsart `manuell`) bleiben im Portal nicht stornierbar.
- **B-7 Guthaben wird nur bei der Anlage der Saisonrechnung verrechnet.** Später entstandenes Guthaben wird nicht automatisch auf bereits bestehende offene Rechnungen umgebucht; es bleibt für die nächste Saisonrechnung oder Onlinebuchung stehen.
- **B-8 Voll-Storno einer Rechnung durch den Betreiber** (Rechnungsseite „Stornorechnung erzeugen“) schreibt wie bisher **kein** Guthaben gut – es dient der Korrektur mit Neuausstellung (A-RECH-4) und gibt die Buchungen zur Neuberechnung frei. Die **Teil-Stornorechnung** über ausgewählte Positionen (A-ADM-4) schreibt den bezahlten Anteil gut wie jedes kostenfreie Storno.

## Review Focus

1. **Doppelte Gutschrift** (Kulanz auf ein bereits kostenfreies Storno, zweite Korrektur derselben Position, Storno einer Buchung, deren Rechnung schon voll storniert ist) – erwartet: höchstens ein Korrekturbeleg je Position, Guthaben höchstens einmal. *Tests: Task 1 `test_position_wird_nur_einmal_korrigiert`, `test_storno_nach_vollstorno_der_rechnung_ohne_gutschrift`; Bestand `test_storno.py::test_rechnungskunde_ohne_gutschrift_und_kulanz` (Kulanz auf kostenfreies Storno bleibt folgenlos).*
2. **Kostenfreie Absage bei offener, teilweise bezahlter und bezahlter Saisonrechnung** – erwartet: offen → nur die Forderung sinkt; teilweise (Guthaben verrechnet) → erst Forderung, dann Guthaben; bezahlt → voller Betrag als Guthaben, immer mit Korrekturbeleg. *Tests: Task 1 `test_gutschrift_nur_fuer_bezahlten_anteil`, Task 2 `test_beenden_mit_bezahlter_saisonrechnung_schreibt_gut`.*
3. **Vierte Absage, Absage nach der Frist, Einstellung `abo_freie_absagen = 0`** – erwartet: kostenpflichtig, Slot trotzdem frei, Zähler nur für echte freie Absagen; Kulanz und Betreiber-Storno zählen nicht. *Tests: Task 4 `test_freie_absagen_werden_gezaehlt`, `test_absage_nach_frist_kostet_und_zaehlt_nicht`, `test_null_freie_absagen_heisst_saison_fest_bezahlt`, `test_kulanz_zaehlt_nicht_als_freie_absage`.*
4. **Abo für einen Kunden ohne Rechnungskennzeichen oder mit Mitgliedschaft, die vor dem letzten Termin endet** – erwartet: verständliche Meldung, nichts angelegt, keine Rechnung; mit Häkchen „als Rechnungskunde markieren“ wird das Kennzeichen gesetzt. *Tests: Task 3 `test_abo_nur_fuer_rechnungskunden`, `test_abo_nur_fuer_mitglieder_bis_zum_letzten_termin`, `test_ui_abo_ohne_mitgliedschaft_zeigt_link_zur_kundenseite`.*
5. **Guthaben größer als die Saisonrechnung** – erwartet: verrechnet wird nur der Rechnungsbetrag, die Rechnung ist bezahlt, der Rest bleibt Guthaben; mit `guthaben_auf_saisonrechnung = nein` wird nichts verrechnet. *Tests: Task 2 `test_saisonrechnung_verrechnet_guthaben`.*

---

## Dateistruktur

```
shared/beachhub_shared/
  kanal.py                 ÄND  Antwort: freie_absage, verbleibende_freie_absagen
  lesestand.py             ÄND  KontoBuchung: abo, freie_absagen_rest
shared/tests/test_kanal.py ÄND

core/beachhub_core/
  models/rechnungen.py     ÄND  Rechnung: dauerbuchung_id, korrigiert_rechnung_id, korrigiert;
                                RechnungPosition: korrigiert_durch_id
  models/buchungen.py      ÄND  Storno: freie_absage, korrektur_rechnung_id; im_portal_stornierbar
  models/portal.py         ÄND  Zahlung: rechnung_id
  services/rechnungen.py   ÄND  korrigiere, offener_betrag, verrechnet, verrechne_guthaben,
                                erzeuge_saisonrechnung; Monatslauf und Sammelrechnung entfallen
  services/storno.py       ÄND  gutschreiben*, freie Absagen, freie_absagen_rest; _gutschrift entfällt
  services/dauerbuchungen.py ÄND Saisonrechnung bei Anlage, Rechnungskunde/Mitgliedschaft, Beenden mit
                                einem Korrekturbeleg
  services/online_buchung.py ÄND Abo-Termine im Portal absagen, Belege nach dem Commit
  services/lesestand.py    ÄND  Konto: abo, freie_absagen_rest
  services/guthaben.py     ÄND  guthabenliste()
  services/benachrichtigung.py ÄND belege_versenden(), Rechnungs-/Stornomail mit Beträgen,
                                saisonende_faellig()
  services/rechnung_pdf.py ÄND  verrechnetes Guthaben, offener Betrag, Text der Teil-Stornorechnung
  services/konfiguration.py ÄND abo_nur_mitglieder, abo_freie_absagen, guthaben_auf_saisonrechnung,
                                saison_zahlungsziel_tage, saisonende_guthabenliste;
                                rechnung_tag_im_folgemonat entfällt
  jobs.py                  ÄND  Monatslauf entfällt; Saisonende-Job 07:05
  cli.py                   ÄND  Befehl monatslauf entfällt
  navigation.py            ÄND  Kunden → Guthabenliste
  routes/belegung.py       ÄND  Belege versenden; Dauerbuchung: Rechnungskunde, Mitgliedschaft
  routes/rechnungen.py     ÄND  Monatslauf entfällt; offener Betrag, Korrekturen, Teil-Storno
  routes/kunden.py         ÄND  Guthabenliste mit Auszahlung
  templates/…              ÄND/NEU (je Task aufgeführt)
alembic/versions/0014_saison_und_korrektur.py  NEU
core/tests/test_korrektur.py, test_saisonrechnung.py, test_abo_absagen.py, test_ui_guthaben.py  NEU
core/tests/… (bestehende Tests, je Task aufgeführt)  ÄND
docs/betrieb/hauptsystem.md, README.md, docs/superpowers/specs/2026-09-05-beachhub-design.md  ÄND
```

---
## Task 1: Korrekturbeleg als einziger Weg zur Gutschrift (Migration 0014)

Wird eine bereits berechnete Buchung kostenfrei (Storno innerhalb der Frist, Kulanz, Sperre), entsteht eine (Teil-)Stornorechnung über ihre Position; als Guthaben gutgeschrieben wird nur, was schon bezahlt war (A-STORNO-6, A-RECH-7, Abweichung B-3). `storno._gutschrift` entfällt. Ohne Rechnung entsteht weder Beleg noch Guthaben. Die Migration 0014 legt alle Spalten dieses Plans an.

**Files:**
- Modify: `core/beachhub_core/models/rechnungen.py`, `models/buchungen.py` (`Storno`), `models/portal.py` (`Zahlung`)
- Create: `core/alembic/versions/0014_saison_und_korrektur.py`
- Modify: `core/beachhub_core/services/rechnungen.py`, `services/storno.py`, `services/benachrichtigung.py`, `services/rechnung_pdf.py`, `services/online_buchung.py`
- Modify: `core/beachhub_core/routes/belegung.py`, `routes/rechnungen.py` (`detail`)
- Modify: `core/beachhub_core/templates/mail/rechnung.txt`, `templates/rechnung_pdf.html`, `templates/rechnungen/detail.html`, `templates/belegung/buchung.html`
- Create: `core/tests/test_korrektur.py`
- Modify: `core/tests/test_storno.py`, `test_online_buchung.py`, `test_ui_belegung.py`

**Interfaces:**
- Consumes: `rechnungen.Posten`, `rechnungen._neue_rechnung`, `rechnungen.steuer_je_satz` (1a-I Task 2), `guthaben.buche`.
- Produces (Schema, alles in 0014): `Rechnung.dauerbuchung_id: UUID | None`, `Rechnung.korrigiert_rechnung_id: UUID | None`, Beziehung `Rechnung.korrigiert: Rechnung | None`; `RechnungPosition.korrigiert_durch_id: UUID | None`; `Storno.freie_absage: bool` (Vorgabe `False`), `Storno.korrektur_rechnung_id: UUID | None`; `Zahlung.rechnung_id: UUID | None`.
- Produces: `rechnungen.korrigiere(db, positionen: Sequence[RechnungPosition], *, grund: str, quelle: str, admin_user_id: UUID | None = None) -> Rechnung` – Fehler `keine_positionen`, `nicht_stornierbar`, `verschiedene_rechnungen`, `bereits_korrigiert`; setzt die Rechnung auf `storniert`, wenn danach alle Positionen korrigiert sind.
- Produces: `rechnungen.verrechnet(db, r) -> Decimal` (bezahlte Zahlungen mit `rechnung_id = r.id`), `rechnungen.offener_betrag(db, r) -> Decimal` (0 für Stornorechnungen und Rechnungen, die nicht `offen` sind; sonst Brutto + Korrekturen − verrechnet, nie negativ).
- Produces: `rechnungen.storniere(db, rechnung, *, admin_user_id, grund) -> Rechnung` – Voll-Storno über alle noch nicht korrigierten Positionen, gibt die Buchungen frei, kein Guthaben (Abweichung B-8).
- Produces: `storno.gutschreiben_positionen(db, positionen, *, grund, quelle, admin_user_id=None) -> Rechnung`, `storno.gutschreiben_alle(db, gebucht: Sequence[Buchung], *, grund, quelle, admin_user_id=None) -> list[Rechnung]` (ein Beleg je Rechnung, setzt `Storno.korrektur_rechnung_id`), `storno.gutschreiben(db, buchung, *, grund, quelle, admin_user_id=None) -> Rechnung | None`.
- Produces: `storno.storniere(db, buchung, *, durch, admin_user_id=None, grund="", kostenfrei=None, korrigieren=True) -> Storno` – bei kostenfreiem Storno `gutschreiben`, außer `korrigieren=False` (der Aufrufer bündelt die Belege).
- Produces: `benachrichtigung.belege_versenden(db, rechnung_ids: Iterable[UUID | None]) -> None` – nach dem Commit: PDF erzeugen, committen, Mail; Fehler je Beleg führen zu einer Alarm-Mail. Mail-Betreff für Stornorechnungen „Stornorechnung <Nummer>“.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_korrektur.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    GuthabenBuchung,
    Kunde,
    Rechnung,
    Tarif,
    Zahlung,
    utcnow,
)
from beachhub_core.services import buchungen, kunden, rechnungen, storno
from beachhub_shared.zeit import kombiniere
from sqlalchemy import select
from sqlalchemy.orm import Session

D = date(2027, 12, 1)


@pytest.fixture
def welt(db: Session) -> tuple[Feld, Kunde]:
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="TSV", email="tsv@x.de", rechnungskunde=True)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k


def _rechnung(
    db: Session, f: Feld, k: Kunde, stunden: tuple[int, ...] = (19, 20), status: str = "offen"
) -> tuple[list[Buchung], Rechnung]:
    """Eine Rechnung mit einer Position je Stunde – gebaut wie die Saisonrechnung."""
    gebucht = [
        buchungen.lege_an(
            db,
            feld_id=f.id,
            kunde_id=k.id,
            beginn=kombiniere(D, time(s)),
            ende=kombiniere(D, time(s + 1)),
        )
        for s in stunden
    ]
    r = rechnungen._neue_rechnung(
        db,
        k,
        "einzel",
        [rechnungen.Posten(b, f"Termin {b.beginn:%H} Uhr", b.preis, b.ust_satz) for b in gebucht],
        D,
        D,
        status,
        quelle="admin",
    )
    db.commit()
    return gebucht, r


def _zahlung(db: Session, k: Kunde, r: Rechnung, betrag: Decimal) -> None:
    db.add(
        Zahlung(
            kunde_id=k.id,
            rechnung_id=r.id,
            provider="guthaben",
            provider_ref=f"guthaben:{r.id}",
            betrag=betrag,
            status=Zahlung.BEZAHLT,
            empfangen_am=utcnow(),
        )
    )


def test_teilstorno_korrigiert_nur_die_gewaehlte_position(db: Session, welt) -> None:
    f, k = welt
    (b1, _), r = _rechnung(db, f, k)
    beleg = rechnungen.korrigiere(db, [r.positionen[0]], grund="Absage", quelle="admin")
    db.commit()
    assert beleg.art == "storno" and beleg.status == "bezahlt"
    assert beleg.korrigiert_rechnung_id == r.id and beleg.korrigiert.nummer == r.nummer
    assert beleg.brutto == Decimal("-30.00") and beleg.positionen[0].ust_satz == Decimal("19.00")
    assert r.positionen[0].korrigiert_durch_id == beleg.positionen[0].id
    assert r.positionen[1].korrigiert_durch_id is None
    assert r.status == "offen" and rechnungen.offener_betrag(db, r) == Decimal("30.00")
    assert b1.rechnung_position_id == r.positionen[0].id  # die Buchung bleibt verknüpft


def test_position_wird_nur_einmal_korrigiert(db: Session, welt) -> None:
    f, k = welt
    _, r = _rechnung(db, f, k)
    rechnungen.korrigiere(db, [r.positionen[0]], grund="x", quelle="admin")
    db.commit()
    with pytest.raises(rechnungen.RechnungsFehler, match="bereits_korrigiert"):
        rechnungen.korrigiere(db, [r.positionen[0]], grund="x", quelle="admin")
    db.rollback()
    rechnungen.korrigiere(db, [r.positionen[1]], grund="x", quelle="admin")
    db.commit()
    db.refresh(r)
    # Alle Positionen korrigiert: Die Rechnung gilt als storniert (Abweichung B-4).
    assert r.status == "storniert" and rechnungen.offener_betrag(db, r) == Decimal("0.00")


def test_korrektur_nur_innerhalb_einer_rechnung(db: Session, welt) -> None:
    f, k = welt
    _, r1 = _rechnung(db, f, k, stunden=(19,))
    _, r2 = _rechnung(db, f, k, stunden=(20,))
    with pytest.raises(rechnungen.RechnungsFehler, match="verschiedene_rechnungen"):
        rechnungen.korrigiere(
            db, [r1.positionen[0], r2.positionen[0]], grund="x", quelle="admin"
        )


def test_offener_betrag_rechnet_zahlungen_gegen(db: Session, welt) -> None:
    f, k = welt
    _, r = _rechnung(db, f, k)
    _zahlung(db, k, r, Decimal("20.00"))
    db.commit()
    assert rechnungen.verrechnet(db, r) == Decimal("20.00")
    assert rechnungen.offener_betrag(db, r) == Decimal("40.00")
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    assert rechnungen.offener_betrag(db, r) == Decimal("0.00")


@pytest.mark.parametrize(
    "bezahlt,gutschrift",
    [
        (Decimal("0"), Decimal("0.00")),
        (Decimal("40.00"), Decimal("10.00")),
        (None, Decimal("30.00")),
    ],
)
def test_gutschrift_nur_fuer_bezahlten_anteil(
    db: Session, welt, bezahlt: Decimal | None, gutschrift: Decimal
) -> None:
    """Rechnung über 60 €, eine Position über 30 € wird kostenfrei: offen ohne Zahlung → nur die
    Forderung sinkt; 40 € schon bezahlt → 20 € decken die verbleibende Forderung, 10 € werden
    Guthaben; ganz bezahlt (None) → der volle Betrag (Abweichung B-3)."""
    f, k = welt
    (b1, _), r = _rechnung(db, f, k)
    if bezahlt is None:
        rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    elif bezahlt:
        _zahlung(db, k, r, bezahlt)
    db.commit()
    beleg = storno.gutschreiben(db, b1, grund="Absage", quelle="admin")
    db.commit()
    assert beleg is not None and beleg.korrigiert_rechnung_id == r.id
    db.refresh(k)
    assert k.guthaben == gutschrift
    gutschriften = db.scalars(
        select(GuthabenBuchung).where(GuthabenBuchung.art == "storno_gutschrift")
    ).all()
    # Jede Gutschrift verweist auf ihren Korrekturbeleg (A-STORNO-6).
    assert [g.bezug_id for g in gutschriften] == ([beleg.id] if gutschrift else [])


def test_gutschreiben_ohne_rechnung_ist_folgenlos(db: Session, welt) -> None:
    f, k = welt
    b = buchungen.lege_an(
        db, feld_id=f.id, kunde_id=k.id, beginn=kombiniere(D, time(19)), ende=kombiniere(D, time(20))
    )
    db.commit()
    assert storno.gutschreiben(db, b, grund="x", quelle="admin") is None
    assert k.guthaben == Decimal("0.00") and db.query(Rechnung).count() == 0


def test_storno_nach_vollstorno_der_rechnung_ohne_gutschrift(db: Session, welt) -> None:
    f, k = welt
    (b1, _), r = _rechnung(db, f, k)
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    rechnungen.storniere(db, r, admin_user_id=None, grund="falsch berechnet")
    db.commit()
    s = storno.storniere(db, b1, durch="betreiber", kostenfrei=True)
    db.commit()
    assert s.korrektur_rechnung_id is None
    db.refresh(k)
    assert k.guthaben == Decimal("0.00") and db.query(Rechnung).count() == 2


def test_gutschreiben_alle_ein_beleg_je_rechnung(db: Session, welt) -> None:
    f, k = welt
    (b1, b2), _ = _rechnung(db, f, k, status="bezahlt")
    for b in (b1, b2):
        storno.storniere(db, b, durch="betreiber", kostenfrei=True, korrigieren=False)
    belege = storno.gutschreiben_alle(db, [b1, b2], grund="Ende", quelle="admin")
    db.commit()
    assert len(belege) == 1 and belege[0].brutto == Decimal("-60.00")
    assert {b.storno.korrektur_rechnung_id for b in (b1, b2)} == {belege[0].id}
    db.refresh(k)
    assert k.guthaben == Decimal("60.00")
```

In `core/tests/test_storno.py` (Importe ergänzen: `Rechnung` aus `beachhub_core.models`, `rechnungen` in die Service-Importe) `test_vor_frist_kostenfrei_mit_gutschrift` ersetzen und einen Test ergänzen:
```python
def test_vor_frist_kostenfrei_mit_gutschrift(db: Session, welt) -> None:
    f, a, _, _ = welt
    bu = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(21)),
        zahlungsart="online",
    )
    r = rechnungen.erzeuge_einzelrechnung(db, bu)  # online bezahlt
    db.commit()
    s = storno.storniere(db, bu, durch="kunde")
    db.commit()
    assert s.kostenfrei and bu.status == "storniert"
    # Das Guthaben entsteht nur zusammen mit dem Korrekturbeleg (A-STORNO-6).
    beleg = db.get(Rechnung, s.korrektur_rechnung_id)
    assert beleg.art == "storno" and beleg.korrigiert_rechnung_id == r.id
    assert beleg.brutto == Decimal("-60.00")
    assert a.guthaben == Decimal("60.00")


def test_kulanz_mit_bezahlter_rechnung_schreibt_mit_beleg_gut(db: Session, welt) -> None:
    f, a, _, _ = welt
    bu = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=a.id,
        beginn=kombiniere(D, time(19)),
        ende=kombiniere(D, time(21)),
        zahlungsart="online",
    )
    rechnungen.erzeuge_einzelrechnung(db, bu)
    db.commit()
    clock.set_override(db, date(2027, 11, 30))  # innerhalb der 48-h-Frist
    s = storno.storniere(db, bu, durch="kunde")
    db.commit()
    assert not s.kostenfrei and s.korrektur_rechnung_id is None and a.guthaben == Decimal("0.00")
    storno.kulanz(db, s, admin_user_id=None, grund="Krankheit")
    db.commit()
    assert s.kostenfrei and s.korrektur_rechnung_id is not None
    assert a.guthaben == Decimal("60.00")
    storno.kulanz(db, s, admin_user_id=None, grund="nochmal")  # bleibt folgenlos
    db.commit()
    assert a.guthaben == Decimal("60.00")
```

In `core/tests/test_online_buchung.py::test_storno_bestaetigt_vor_frist_mit_gutschrift` am Ende ergänzen:
```python
    assert any(m["betreff"].startswith("Stornorechnung ") for m in mail_ausgang)
```

In `core/tests/test_ui_belegung.py` (Importe: `Kunde`, `Rechnung` aus `beachhub_core.models`):
```python
def test_storno_mit_rechnung_verschickt_stornorechnung(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    f, a, _ = welt
    c = eingeloggt
    c.post(
        "/admin/belegung/buchung",
        data={
            "csrf_token": c.csrf,
            "feld_id": str(f.id),
            "kunde_id": str(a.id),
            "beginn": "2027-12-01T19:00",
            "ende": "2027-12-01T20:00",
        },
    )
    b = db.query(Buchung).one()
    mail.TEST_AUSGANG.clear()
    r = c.post(
        f"/admin/belegung/buchung/{b.id}/storno",
        data={"csrf_token": c.csrf, "grund": "Test", "kostenfrei": "ja"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(b)
    beleg = db.get(Rechnung, b.storno.korrektur_rechnung_id)
    assert beleg.art == "storno" and beleg.pdf_pfad
    assert [m["betreff"] for m in mail.TEST_AUSGANG] == [
        "Stornierung Ihrer Buchung",
        f"Stornorechnung {beleg.nummer}",
    ]
    assert "Stornorechnung" in c.get(f"/admin/belegung/buchung/{b.id}").text
    # Betreiberbuchung mit offener Rechnung: Die Forderung sinkt, Guthaben entsteht keins.
    assert db.get(Kunde, a.id).guthaben == Decimal("0.00")
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_korrektur.py tests/test_storno.py tests/test_ui_belegung.py`
Expected: FAIL – `TypeError: 'rechnung_id' is an invalid keyword argument for Zahlung`, `AttributeError: … 'korrigiere'`.

- [ ] **Step 3: Modelle und Migration 0014**

`core/beachhub_core/models/rechnungen.py`:
- In `Rechnung` die Kommentarzeile an `art` auf `# einzel | saison | storno` ändern und nach `storniert_durch_id` ergänzen:
```python
    # Saisonrechnung einer Dauerbuchung (A-RECH-3).
    dauerbuchung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dauerbuchung.id")
    )
    # Bei art = storno: die Rechnung, die diese (Teil-)Stornorechnung korrigiert (A-RECH-7).
    korrigiert_rechnung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rechnung.id")
    )
```
und nach `kunde: Mapped[Kunde] = relationship()`:
```python
    korrigiert: Mapped["Rechnung | None"] = relationship(
        remote_side="Rechnung.id", foreign_keys=[korrigiert_rechnung_id]
    )
```
- In `RechnungPosition` nach `brutto`:
```python
    # Gegenposition der (Teil-)Stornorechnung; eine Position wird höchstens einmal korrigiert.
    korrigiert_durch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rechnung_position.id")
    )
```

`core/beachhub_core/models/buchungen.py`, in `Storno` nach `grund`:
```python
    # Kostenfreie Absage eines Abo-Termins durch den Kunden (A-DAUER-3); nur diese zählen.
    freie_absage: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Korrekturbeleg, mit dem das Storno die Rechnung der Buchung korrigiert hat (A-STORNO-6).
    korrektur_rechnung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rechnung.id")
    )
```

`core/beachhub_core/models/portal.py`, in `Zahlung` nach `buchung_id`:
```python
    # Verrechnung von Guthaben auf eine Rechnung (provider = "guthaben", A-ZAHL-4).
    rechnung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rechnung.id"), index=True
    )
```

`core/alembic/versions/0014_saison_und_korrektur.py`:
```python
"""saison_und_korrektur: Saisonrechnung je Dauerbuchung, (Teil-)Stornorechnungen mit Bezug auf
die korrigierte Rechnung und Position, freie Abo-Absagen, Guthabenverrechnung auf Rechnungen
(Stufe 1a-II). Der Monatslauf entfällt: Marker und Einstellung werden gelöscht.

Enthält alle Schemaänderungen von Plan 1a-II.

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (Tabelle, Spalte, Zieltabelle) – jeweils UUID, NULL erlaubt, Fremdschlüssel auf <Ziel>.id
_VERWEISE = (
    ("rechnung", "dauerbuchung_id", "dauerbuchung"),
    ("rechnung", "korrigiert_rechnung_id", "rechnung"),
    ("rechnung_position", "korrigiert_durch_id", "rechnung_position"),
    ("storno", "korrektur_rechnung_id", "rechnung"),
    ("zahlung", "rechnung_id", "rechnung"),
)


def upgrade() -> None:
    for tabelle, spalte, ziel in _VERWEISE:
        op.add_column(tabelle, sa.Column(spalte, sa.UUID(), nullable=True))
        op.create_foreign_key(f"{tabelle}_{spalte}_fkey", tabelle, ziel, [spalte], ["id"])
    op.create_index("ix_zahlung_rechnung_id", "zahlung", ["rechnung_id"])
    op.add_column(
        "storno",
        sa.Column("freie_absage", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.alter_column("storno", "freie_absage", server_default=None)
    # Bestehende Stornorechnungen verweisen künftig auf die Rechnung, die sie aufheben.
    op.execute(
        "UPDATE rechnung s SET korrigiert_rechnung_id = r.id "
        "FROM rechnung r WHERE r.storniert_durch_id = s.id"
    )
    op.execute("DELETE FROM app_setting WHERE key = 'monatslauf_letzter'")
    op.execute("DELETE FROM konfiguration WHERE schluessel = 'rechnung_tag_im_folgemonat'")


def downgrade() -> None:
    op.drop_column("storno", "freie_absage")
    op.drop_index("ix_zahlung_rechnung_id", table_name="zahlung")
    for tabelle, spalte, _ in reversed(_VERWEISE):
        op.drop_constraint(f"{tabelle}_{spalte}_fkey", tabelle, type_="foreignkey")
        op.drop_column(tabelle, spalte)
```

- [ ] **Step 4: Korrektur und offener Betrag im Rechnungsdienst**

`core/beachhub_core/services/rechnungen.py`:
- Importe: `from sqlalchemy import func, select`; in die Modell-Importe `Zahlung`.
- Nach `steuer_je_satz` ergänzen:
```python
def verrechnet(db: Session, r: Rechnung) -> Decimal:
    """Auf die Rechnung verbuchte Zahlungen, etwa verrechnetes Guthaben (A-ZAHL-4)."""
    summe = db.scalar(
        select(func.coalesce(func.sum(Zahlung.betrag), 0)).where(
            Zahlung.rechnung_id == r.id, Zahlung.status == Zahlung.BEZAHLT
        )
    )
    return Decimal(str(summe)).quantize(CENT)


def offener_betrag(db: Session, r: Rechnung) -> Decimal:
    """Was der Kunde auf eine offene Rechnung noch zahlen muss: Brutto abzüglich Korrekturen und
    verrechneter Zahlungen. Bezahlte und stornierte Rechnungen sowie Stornorechnungen sind nie
    offen; den Zahlungseingang per Überweisung hakt der Betreiber ab (A-ZAHL-7)."""
    if r.art == "storno" or r.status != "offen":
        return NULL
    korrigiert = db.scalar(
        select(func.coalesce(func.sum(Rechnung.brutto), 0)).where(
            Rechnung.korrigiert_rechnung_id == r.id
        )
    )
    offen = r.brutto + Decimal(str(korrigiert)) - verrechnet(db, r)
    return max(NULL, offen.quantize(CENT))


def korrigiere(
    db: Session,
    positionen: Sequence[RechnungPosition],
    *,
    grund: str,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> Rechnung:
    """(Teil-)Stornorechnung über genau diese Positionen einer Rechnung (A-RECH-7). Die
    ursprüngliche Rechnung bleibt unverändert (A-RECH-4); sind danach alle ihre Positionen
    korrigiert, gilt sie als storniert (Abweichung B-4). Guthaben entsteht hier nicht – das
    entscheidet `storno.gutschreiben_positionen`."""
    if not positionen:
        raise RechnungsFehler("keine_positionen")
    rechnung = positionen[0].rechnung
    if rechnung.art == "storno":
        raise RechnungsFehler("nicht_stornierbar")
    if any(p.rechnung_id != rechnung.id for p in positionen):
        raise RechnungsFehler("verschiedene_rechnungen")
    if any(p.korrigiert_durch_id is not None for p in positionen):
        raise RechnungsFehler("bereits_korrigiert")
    tage = [
        lokales_datum(b.beginn)
        for p in positionen
        if p.buchung_id is not None and (b := db.get(Buchung, p.buchung_id)) is not None
    ]
    von, bis = (min(tage), max(tage)) if tage else (rechnung.leistung_von, rechnung.leistung_bis)
    vorher = audit.als_dict(rechnung)
    beleg = _neue_rechnung(
        db,
        rechnung.kunde,
        "storno",
        [
            Posten(None, f"Storno zu Rechnung {rechnung.nummer}: {p.text}", -p.brutto, p.ust_satz)
            for p in positionen
        ],
        von,
        bis,
        "bezahlt",
        quelle=quelle,
    )
    beleg.korrigiert_rechnung_id = rechnung.id
    for p, gegen in zip(positionen, beleg.positionen, strict=True):
        p.korrigiert_durch_id = gegen.id
    if all(p.korrigiert_durch_id is not None for p in rechnung.positionen):
        rechnung.status = "storniert"
        rechnung.storniert_durch_id = beleg.id
    db.flush()
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="rechnung",
        objekt_id=rechnung.id,
        vorher=vorher,
        nachher={**audit.als_dict(rechnung), "korrekturbeleg": beleg.nummer, "grund": grund},
        admin_user_id=admin_user_id,
    )
    return beleg
```
- `storniere` ersetzen:
```python
def storniere(
    db: Session, rechnung: Rechnung, *, admin_user_id: uuid.UUID | None, grund: str
) -> Rechnung:
    """Voll-Storno zur Korrektur mit Neuausstellung (A-RECH-4): korrigiert alle noch nicht
    korrigierten Positionen und gibt die Buchungen zur Neuberechnung frei. Schreibt kein Guthaben
    gut (Abweichung B-8)."""
    if rechnung.status == "storniert" or rechnung.art == "storno":
        raise RechnungsFehler("nicht_stornierbar")
    offen = [p for p in rechnung.positionen if p.korrigiert_durch_id is None]
    if not offen:
        raise RechnungsFehler("nicht_stornierbar")
    beleg = korrigiere(db, offen, grund=grund, quelle="admin", admin_user_id=admin_user_id)
    for p in rechnung.positionen:
        if p.buchung_id:
            b = db.get(Buchung, p.buchung_id)
            if b is not None and b.rechnung_position_id == p.id:
                b.rechnung_position_id = None
            p.buchung_id = None
    db.flush()
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{rechnung.kunde_id}")
    return beleg
```
(`storniert_durch_id` und Status setzt `korrigiere`, weil danach alle Positionen korrigiert sind.)

- [ ] **Step 5: Gutschrift nur über den Korrekturbeleg**

`core/beachhub_core/services/storno.py` vollständig:
```python
import uuid
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import clock
from beachhub_core.models import Buchung, Rechnung, RechnungPosition, Storno
from beachhub_core.services import audit, buchungen, guthaben, konfiguration, rechnungen, sperren

NULL = Decimal("0.00")


class StornoFehler(Exception):  # noqa: N818
    pass


def gutschreiben_positionen(
    db: Session,
    positionen: Sequence[RechnungPosition],
    *,
    grund: str,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> Rechnung:
    """Korrigiert Positionen einer Rechnung und schreibt den bereits bezahlten Anteil als Guthaben
    gut. Der einzige Weg zu Guthaben der Art `storno_gutschrift` (A-STORNO-6): Ohne
    Korrekturbeleg bliebe Umsatzsteuer auf eine nicht erbrachte Leistung abgeführt (§ 17 UStG).
    Bei offener Rechnung sinkt zuerst die Forderung (A-RECH-7, Abweichung B-3)."""
    if not positionen:
        raise rechnungen.RechnungsFehler("keine_positionen")
    rechnung = positionen[0].rechnung
    offen_vorher = rechnungen.offener_betrag(db, rechnung)
    beleg = rechnungen.korrigiere(
        db, positionen, grund=grund, quelle=quelle, admin_user_id=admin_user_id
    )
    betrag = sum((p.brutto for p in positionen), NULL)
    gutschrift = max(NULL, betrag - offen_vorher)
    if gutschrift > NULL:
        guthaben.buche(
            db,
            kunde=rechnung.kunde,
            betrag=gutschrift,
            art="storno_gutschrift",
            bezug_id=beleg.id,
            notiz=f"Stornorechnung {beleg.nummer}",
            admin_user_id=admin_user_id,
            quelle=quelle,
        )
    return beleg


def _offene_position(db: Session, buchung: Buchung) -> RechnungPosition | None:
    if buchung.rechnung_position_id is None:
        return None
    pos = db.get(RechnungPosition, buchung.rechnung_position_id)
    return pos if pos is not None and pos.korrigiert_durch_id is None else None


def gutschreiben_alle(
    db: Session,
    gebucht: Sequence[Buchung],
    *,
    grund: str,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> list[Rechnung]:
    """Korrekturbelege für kostenfrei gewordene Buchungen – einer je Rechnung, etwa beim Beenden
    einer Dauerbuchung (A-RECH-7). Verknüpft jedes Storno mit seinem Beleg. Buchungen ohne
    (noch nicht korrigierte) Rechnungsposition bleiben ohne Beleg und ohne Guthaben."""
    gruppen: dict[uuid.UUID, list[tuple[Buchung, RechnungPosition]]] = {}
    for b in gebucht:
        pos = _offene_position(db, b)
        if pos is not None:
            gruppen.setdefault(pos.rechnung_id, []).append((b, pos))
    belege = []
    for paare in gruppen.values():
        beleg = gutschreiben_positionen(
            db,
            [pos for _, pos in paare],
            grund=grund,
            quelle=quelle,
            admin_user_id=admin_user_id,
        )
        for b, _ in paare:
            s = db.scalar(select(Storno).where(Storno.buchung_id == b.id))
            if s is not None:
                s.korrektur_rechnung_id = beleg.id
        belege.append(beleg)
    db.flush()
    return belege


def gutschreiben(
    db: Session,
    buchung: Buchung,
    *,
    grund: str,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> Rechnung | None:
    belege = gutschreiben_alle(
        db, [buchung], grund=grund, quelle=quelle, admin_user_id=admin_user_id
    )
    return belege[0] if belege else None


def storniere(
    db: Session,
    buchung: Buchung,
    *,
    durch: str,
    admin_user_id: uuid.UUID | None = None,
    grund: str = "",
    kostenfrei: bool | None = None,
    korrigieren: bool = True,
) -> Storno:
    """Storniert eine Buchung. Ist das Storno kostenfrei, korrigiert es die Rechnung der Buchung
    (`gutschreiben`) – außer mit `korrigieren=False`: Dann bündelt der Aufrufer die Belege mit
    `gutschreiben_alle`."""
    if not buchung.aktiv:
        raise StornoFehler("nicht_aktiv")
    jetzt = clock.now(db)
    # A-STORNO-5: Kunden dürfen nur vor Beginn stornieren. Betreiber/System dürfen
    # auch eine bereits laufende Buchung stornieren (z. B. Notfall-Sperre), aber
    # niemand eine bereits beendete Buchung.
    if durch == "kunde" and buchung.beginn <= jetzt:
        raise StornoFehler("zu_spaet")
    if buchung.ende <= jetzt:
        raise StornoFehler("zu_spaet")
    if kostenfrei is None:
        frist = timedelta(hours=konfiguration.hole(db, "storno_frist_stunden"))
        kostenfrei = jetzt <= buchung.beginn - frist
    s = Storno(buchung_id=buchung.id, durch=durch, kostenfrei=kostenfrei, grund=grund)
    db.add(s)
    quelle = "admin" if durch == "betreiber" else ("portal" if durch == "kunde" else "system")
    buchungen.setze_status(
        db, buchung, Buchung.STORNIERT, quelle=quelle, admin_user_id=admin_user_id
    )
    db.flush()
    if kostenfrei and korrigieren:
        gutschreiben(
            db,
            buchung,
            grund=f"Storno: {grund}" if grund else "Storno",
            quelle=quelle,
            admin_user_id=admin_user_id,
        )
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="storno",
        objekt_id=s.id,
        vorher=None,
        nachher=audit.als_dict(s),
        admin_user_id=admin_user_id,
    )
    return s


def kulanz(db: Session, s: Storno, *, admin_user_id: uuid.UUID | None, grund: str) -> None:
    """Stellt ein kostenpflichtiges Storno nachträglich frei – mit Korrekturbeleg (A-STORNO-4,
    A-STORNO-6). Ein bereits kostenfreies Storno bleibt unberührt – sonst entstünde ein zweites
    Mal Guthaben."""
    if s.kostenfrei:
        return
    vorher = audit.als_dict(s)
    s.kostenfrei = True
    s.grund = (s.grund + " | " if s.grund else "") + f"Kulanz: {grund}"
    db.flush()
    gutschreiben(
        db, s.buchung, grund=f"Kulanz: {grund}", quelle="admin", admin_user_id=admin_user_id
    )
    db.flush()
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="storno",
        objekt_id=s.id,
        vorher=vorher,
        nachher=audit.als_dict(s),
        admin_user_id=admin_user_id,
    )
    from beachhub_core.services import lesestand

    lesestand.markiere_geaendert(db, f"konto:{s.buchung.kunde_id}")


# Verdrahtung der Hooks – einmalig beim Import
sperren.STORNIERE = storniere
```

`core/beachhub_core/services/guthaben.py`, Kommentar über `ARTEN` ergänzen (Inhalt unverändert):
```python
# storno_gutschrift entsteht ausschließlich über storno.gutschreiben_positionen, also nur zusammen
# mit einem Korrekturbeleg (A-STORNO-6).
```

- [ ] **Step 6: Belege versenden, Rechnungsmail und PDF**

`core/beachhub_core/services/benachrichtigung.py`:
- Importe: `import logging`, `import uuid`, `from collections.abc import Iterable`, `from decimal import Decimal`, `from sqlalchemy import func, select`; in die Modell-Importe `GuthabenBuchung`; `logger = logging.getLogger(__name__)`.
- `rechnung` ersetzen und `belege_versenden` ergänzen:
```python
def rechnung(db: Session, r: Rechnung) -> None:
    from beachhub_core.services import rechnung_pdf, rechnungen  # Zyklus vermeiden

    if not r.pdf_pfad:
        rechnung_pdf.erzeuge(db, r)
    anhang = [(f"{r.nummer}.pdf", Path(r.pdf_pfad).read_bytes())] if r.pdf_pfad else []
    gutschrift = db.scalar(
        select(func.coalesce(func.sum(GuthabenBuchung.betrag), 0)).where(
            GuthabenBuchung.bezug_id == r.id, GuthabenBuchung.art == "storno_gutschrift"
        )
    )
    betreff = f"Stornorechnung {r.nummer}" if r.art == "storno" else f"Rechnung {r.nummer}"
    mail.sende(
        r.kunde.email,
        betreff,
        _text(
            "rechnung",
            r=r,
            offen=rechnungen.offener_betrag(db, r),
            verrechnet=rechnungen.verrechnet(db, r),
            gutschrift=Decimal(str(gutschrift)),
        ),
        anhaenge=anhang,
    )


def belege_versenden(db: Session, rechnung_ids: Iterable[uuid.UUID | None]) -> None:
    """Nach dem Commit: PDF der Rechnung erzeugen, festschreiben und per Mail schicken. Ein
    Fehler bei einem Beleg hält die übrigen nicht auf; der Betreiber bekommt dann eine Mail."""
    from beachhub_core.services import rechnung_pdf  # Zyklus vermeiden

    for rid in dict.fromkeys(i for i in rechnung_ids if i is not None):
        r = db.get(Rechnung, rid)
        if r is None:
            continue
        nummer = r.nummer
        if not r.pdf_pfad:
            try:
                rechnung_pdf.erzeuge(db, r)
                db.commit()
            except Exception:
                logger.exception("PDF-Erzeugung für Rechnung %s fehlgeschlagen", nummer)
                db.rollback()
                betreiber_alarm(
                    "Rechnungs-PDF nicht erzeugt",
                    f"Das PDF der Rechnung {nummer} konnte nicht erzeugt werden; der Kunde hat "
                    "sie nicht per Mail bekommen. Details im Log des Hauptsystems.",
                )
                continue
        rechnung(db, r)
```
(`betreiber_alarm` steht weiter unten in derselben Datei; der Aufruf erfolgt erst zur Laufzeit.)

`core/beachhub_core/templates/mail/rechnung.txt` vollständig:
```
Hallo {{ r.kunde.name }},

{% if r.art == "storno" -%}
anbei erhalten Sie die Stornorechnung {{ r.nummer }}{% if r.korrigiert %} zur Rechnung {{ r.korrigiert.nummer }}{% endif %} über {{ (-r.brutto)|euro }}.
{% if gutschrift > 0 %}{{ gutschrift|euro }} haben wir Ihrem Guthaben gutgeschrieben; wir verrechnen es mit Ihrer nächsten Buchung oder Rechnung.
{% endif %}
{%- else -%}
anbei erhalten Sie die Rechnung {{ r.nummer }} über {{ r.brutto|euro }}.
{% if verrechnet > 0 %}Davon haben wir {{ verrechnet|euro }} mit Ihrem Guthaben verrechnet.
{% endif %}{% if offen > 0 %}Bitte überweisen Sie {{ offen|euro }} bis zum {{ r.faellig_am|datum }} unter Angabe der Rechnungsnummer.
{% endif %}
{%- endif %}
Die Rechnung finden Sie als PDF im Anhang dieser E-Mail.

Viele Grüße
{{ betreiber }}
```

`core/beachhub_core/services/rechnung_pdf.py`:
- Importe: `from decimal import Decimal`, `from sqlalchemy.orm import Session, object_session`.
- `html` ersetzen:
```python
def html(rechnung: Rechnung) -> str:
    """Das HTML der Rechnung, aus dem `erzeuge` das PDF macht."""
    db = object_session(rechnung)
    verrechnet = rechnungen.verrechnet(db, rechnung) if db is not None else Decimal("0.00")
    offen = rechnungen.offener_betrag(db, rechnung) if db is not None else rechnung.brutto
    return templates.env.get_template("rechnung_pdf.html").render(
        r=rechnung,
        steuer=rechnungen.steuer_je_satz(rechnung),
        verrechnet=verrechnet,
        offen=offen,
        betreiber=_betreiber(),
    )
```

`core/beachhub_core/templates/rechnung_pdf.html`:
- Überschrift: `<h1>{% if r.art == "storno" %}Stornorechnung{% elif r.art == "saison" %}Saisonrechnung{% else %}Rechnung{% endif %} {{ r.nummer }}</h1>`
- Den Absatz nach der Summentabelle (`{% if r.art == "storno" %}…{% endif %}`) ersetzen:
```html
{% if verrechnet > 0 %}
<table class="summe">
  <tr><td></td><td class="r">abzüglich verrechnetes Guthaben</td><td class="r">{{ (-verrechnet)|euro }}</td></tr>
  <tr class="gesamt"><td></td><td class="r">Offener Betrag</td><td class="r">{{ offen|euro }}</td></tr>
</table>
{% endif %}
{% if r.art == "storno" %}<p>Diese Stornorechnung korrigiert {% if r.korrigiert %}die Rechnung {{ r.korrigiert.nummer }} vom {{ r.korrigiert.datum|datum }}{% else %}eine frühere Rechnung{% endif %} um die aufgeführten Positionen.</p>
{% elif offen == 0 %}<p>Der Betrag ist bereits beglichen. Vielen Dank.</p>
{% else %}<p>Bitte überweisen Sie {{ offen|euro }} bis zum {{ r.faellig_am|datum }} unter Angabe der Rechnungsnummer auf:<br>{{ betreiber.bank }}</p>{% endif %}
```

- [ ] **Step 7: Belege in Oberfläche und Kanal**

`core/beachhub_core/services/online_buchung.py`:
- `_storno_mail` ersetzen:
```python
def _storno_mail(storno_id: uuid.UUID) -> Nachlauf:
    def lauf(db: Session) -> None:
        s = db.get(Storno, storno_id)
        if s is not None:
            benachrichtigung.storno(db, s)
            benachrichtigung.belege_versenden(db, [s.korrektur_rechnung_id])

    return lauf
```
- In `storniere_fuer_kunde` den Kommentar vor `return abgelehnt("nicht_stornierbar")` ersetzen durch: `# Betreiberbuchungen rechnet der Betreiber außerhalb des Portals ab; ihr Storno entscheidet er.`

`core/beachhub_core/routes/belegung.py`:
- Import `Storno` in die Modell-Importe.
- Hilfsfunktion unter `_kunden_liste`:
```python
def _belege_der_buchungen(db: Session, buchung_ids: list[uuid.UUID]) -> list[uuid.UUID]:
    """Korrekturbelege, die beim Stornieren dieser Buchungen entstanden sind."""
    if not buchung_ids:
        return []
    return list(
        db.scalars(
            select(Storno.korrektur_rechnung_id)
            .where(Storno.buchung_id.in_(buchung_ids), Storno.korrektur_rechnung_id.is_not(None))
            .distinct()
        ).all()
    )
```
- `buchung_storno`: nach `benachrichtigung.storno(db, s)` ergänzen `benachrichtigung.belege_versenden(db, [s.korrektur_rechnung_id])`.
- `buchung_kulanz`: vor dem `return mit_flash(...)` am Ende ergänzen `benachrichtigung.belege_versenden(db, [b.storno.korrektur_rechnung_id])`.
- `sperre_anlegen`: nach `db.commit()` im `try`-Block (also nach dem erfolgreichen Anlegen, vor dem Bestimmen von `ziel_feld`) ergänzen:
```python
    stornierte = [bid for bid, wahl in entscheidungen.items() if wahl == "stornieren"]
    benachrichtigung.belege_versenden(db, _belege_der_buchungen(db, stornierte))
```
- `dauer_anlegen`: nach `benachrichtigung.dauerbuchung_angelegt(db, d)` ergänzen:
```python
    stornierte = [bid for bid, wahl in entscheidungen.items() if wahl == "stornieren"]
    benachrichtigung.belege_versenden(db, _belege_der_buchungen(db, stornierte))
```

`core/beachhub_core/templates/belegung/buchung.html`, in der Storno-Karte nach „Kostenfrei: …“:
```html
  {% if b.storno.korrektur_rechnung_id %}<p>Stornorechnung: <a href="/admin/rechnungen/{{ b.storno.korrektur_rechnung_id }}">anzeigen</a></p>{% endif %}
```

`core/beachhub_core/routes/rechnungen.py`, in `detail` an `render(...)` übergeben:
```python
        offen=rechnungen.offener_betrag(db, r),
        verrechnet=rechnungen.verrechnet(db, r),
        korrekturen=db.scalars(
            select(Rechnung)
            .where(Rechnung.korrigiert_rechnung_id == r.id)
            .order_by(Rechnung.nummer)
        ).all(),
```

`core/beachhub_core/templates/rechnungen/detail.html`:
- In der Karte „Kopfdaten“ nach „Fällig am“:
```html
    {% if r.art != "storno" %}Offener Betrag: <strong>{{ offen|euro }}</strong><br>{% endif %}
    {% if verrechnet > 0 %}Verrechnetes Guthaben: {{ verrechnet|euro }}<br>{% endif %}
    {% if r.korrigiert %}Korrigiert: <a href="/admin/rechnungen/{{ r.korrigiert_rechnung_id }}">Rechnung {{ r.korrigiert.nummer }}</a><br>{% endif %}
```
- Nach der Karte „Positionen“:
```html
{% if korrekturen %}
<div class="karte">
  <h2>Korrekturen</h2>
  <table>
    <tr><th>Stornorechnung</th><th>Datum</th><th class="rechts">Betrag</th></tr>
    {% for k in korrekturen %}<tr>
      <td><a href="/admin/rechnungen/{{ k.id }}">{{ k.nummer }}</a></td>
      <td>{{ k.datum|datum }}</td>
      <td class="rechts">{{ k.brutto|euro }}</td>
    </tr>{% endfor %}
  </table>
</div>
{% endif %}
```

- [ ] **Step 8: Tests und Lint**

Run:
```bash
(cd core && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS. Der E2E-Test bekommt beim kostenfreien Storno weiterhin 30 € Guthaben – jetzt über die Stornorechnung zur online bezahlten Einzelrechnung.

- [ ] **Step 9: Commit**

```bash
git add core
git commit -m "feat(core): Teil-Stornorechnung als einziger Weg zur Gutschrift, Migration 0014"
```

---
## Task 2: Saisonrechnung statt Monatslauf

Jede Dauerbuchung bekommt bei ihrer Anlage genau eine Saisonrechnung über alle Termine (Status `offen`, Zahlungsziel `saison_zahlungsziel_tage`, Vorgabe 14), die sofort mit PDF an den Kunden geht (A-RECH-3). Vorhandenes Guthaben wird – Einstellung `guthaben_auf_saisonrechnung`, Vorgabe ja – als Zahlung verrechnet; die Rechnung weist den vollen Betrag, das verrechnete Guthaben und den offenen Betrag aus (A-ZAHL-4). Wird die Dauerbuchung ab einem Datum beendet, korrigiert **ein** Korrekturbeleg alle betroffenen Termine (A-DAUER-4, A-RECH-7). Der Monatslauf entfällt ersatzlos: Sammelrechnung, Job, Marker, Einstellung `rechnung_tag_im_folgemonat`, CLI-Befehl und Admin-Formular.

**Files:**
- Modify: `core/beachhub_core/services/rechnungen.py`, `services/dauerbuchungen.py`, `services/konfiguration.py`
- Modify: `core/beachhub_core/jobs.py`, `core/beachhub_core/cli.py`
- Modify: `core/beachhub_core/routes/rechnungen.py`, `routes/belegung.py` (`dauer_anlegen`, `dauer_detail`, `dauer_beenden`)
- Modify: `core/beachhub_core/templates/rechnungen/liste.html`, `templates/belegung/dauer.html`, `templates/mail/dauerbuchung.txt`
- Create: `core/tests/test_saisonrechnung.py`
- Modify: `core/tests/test_rechnungen.py`, `test_jobs.py`, `test_ui_rechnungen.py`, `test_dauerbuchungen.py`

**Interfaces:**
- Consumes: `rechnungen.korrigiere`, `offener_betrag`, `verrechnet`, `storno.storniere(..., korrigieren=False)`, `storno.gutschreiben_alle`, `benachrichtigung.belege_versenden` (Task 1).
- Produces: `rechnungen.erzeuge_saisonrechnung(db, dauer: Dauerbuchung, *, quelle: str = "admin") -> Rechnung` – Fehler `bereits_berechnet` (Dauerbuchung hat schon eine), `keine_termine`; setzt `dauerbuchung_id` und `rechnung_position_id` jedes Termins.
- Produces: `rechnungen.verrechne_guthaben(db, r: Rechnung, *, quelle: str) -> Decimal` – bucht `min(Guthaben, offener Betrag)` als `GuthabenBuchung(art="verrechnung", bezug_id=r.id)` und `Zahlung(provider="guthaben", provider_ref=f"guthaben:{r.id}", rechnung_id=r.id, status="bezahlt")`; deckt es alles, wird die Rechnung `bezahlt`.
- Produces: `rechnungen._neue_rechnung(..., zahlungsziel_tage: int | None = None)` (ohne Angabe gilt `rechnung_zahlungsziel_tage`).
- Produces: `dauerbuchungen.lege_an(...)` erzeugt die Saisonrechnung in derselben Transaktion; `dauerbuchungen.beende(db, dauer, *, ab, admin_user_id) -> list[Rechnung]` (die Korrekturbelege).
- Produces: Einstellungen `saison_zahlungsziel_tage: int = 14`, `guthaben_auf_saisonrechnung: bool = True` (Gruppe „Zahlung und Rechnung“).
- Entfällt: `rechnungen.monatslauf`, `erzeuge_sammelrechnung`, `abrechenbare_buchungen`, `MONATSLAUF_ZAHLUNGSARTEN`; `jobs.MARKER`, `monatslauf_faellig`, `monatslauf_fuer`, `monatslauf_ausfuehren`, Job `monatslauf`; CLI `beachhub-core monatslauf`; Route `POST /admin/rechnungen/monatslauf`; Einstellung `rechnung_tag_im_folgemonat`.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_saisonrechnung.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock, jobs
from beachhub_core.models import (
    Betriebszeit,
    Dauerbuchung,
    Feld,
    FeldRaster,
    GuthabenBuchung,
    Kunde,
    Rechnung,
    Tarif,
    Zahlung,
)
from beachhub_core.services import dauerbuchungen, guthaben, konfiguration, kunden, rechnungen
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session) -> tuple[Feld, Kunde]:
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="TSV", email="tsv@x.de", rechnungskunde=True)
    k.mitglied_bis = date(2028, 4, 30)  # Saisonabo nur für Mitglieder (ab Task 3)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k


def _abo(db: Session, f: Feld, k: Kunde, bis: date = date(2027, 12, 22)) -> Dauerbuchung:
    """Mittwochs 19–20 Uhr ab dem 01.12.2027 (bis zum 22.12. vier Termine)."""
    d = dauerbuchungen.lege_an(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=2,
        start=time(19),
        ende=time(20),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=bis,
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
    )
    db.commit()
    return d


def _saison(db: Session, d: Dauerbuchung) -> Rechnung:
    return db.scalars(select(Rechnung).where(Rechnung.dauerbuchung_id == d.id)).one()


def test_saisonrechnung_bei_anlage(db: Session, welt) -> None:
    f, k = welt
    d = _abo(db, f, k)
    r = _saison(db, d)
    assert r.art == "saison" and r.status == "offen" and r.brutto == Decimal("120.00")
    assert (r.leistung_von, r.leistung_bis) == (date(2027, 12, 1), date(2027, 12, 22))
    assert r.faellig_am == date(2027, 12, 9)  # 14 Tage nach dem 25.11.
    assert len(r.positionen) == 4 and {p.ust_satz for p in r.positionen} == {Decimal("7.00")}
    assert all(b.rechnung_position_id is not None for b in d.buchungen)
    with pytest.raises(rechnungen.RechnungsFehler, match="bereits_berechnet"):
        rechnungen.erzeuge_saisonrechnung(db, d)


def test_saison_zahlungsziel_einstellbar(db: Session, welt) -> None:
    f, k = welt
    konfiguration.setze(db, "saison_zahlungsziel_tage", 30)
    db.commit()
    assert _saison(db, _abo(db, f, k)).faellig_am == date(2027, 12, 25)


@pytest.mark.parametrize(
    "vorhanden,verrechnen,verrechnet,offen,rest,status",
    [
        (Decimal("50.00"), "ja", Decimal("50.00"), Decimal("70.00"), Decimal("0.00"), "offen"),
        (Decimal("200.00"), "ja", Decimal("120.00"), Decimal("0.00"), Decimal("80.00"), "bezahlt"),
        (Decimal("50.00"), "nein", Decimal("0.00"), Decimal("120.00"), Decimal("50.00"), "offen"),
    ],
)
def test_saisonrechnung_verrechnet_guthaben(
    db: Session,
    welt,
    vorhanden: Decimal,
    verrechnen: str,
    verrechnet: Decimal,
    offen: Decimal,
    rest: Decimal,
    status: str,
) -> None:
    f, k = welt
    guthaben.buche(db, kunde=k, betrag=vorhanden, art="manuell")
    konfiguration.setze(db, "guthaben_auf_saisonrechnung", verrechnen)
    db.commit()
    r = _saison(db, _abo(db, f, k))
    db.refresh(k)
    # Volle Rechnung, Guthaben als Zahlung – keine Preisminderung (A-ZAHL-4).
    assert r.brutto == Decimal("120.00") and r.status == status
    assert rechnungen.verrechnet(db, r) == verrechnet
    assert rechnungen.offener_betrag(db, r) == offen
    assert k.guthaben == rest
    if verrechnet:
        z = db.scalars(select(Zahlung).where(Zahlung.rechnung_id == r.id)).one()
        assert z.provider == "guthaben" and z.betrag == verrechnet
        g = db.scalars(select(GuthabenBuchung).where(GuthabenBuchung.art == "verrechnung")).one()
        assert g.bezug_id == r.id and g.betrag == -verrechnet


def test_beenden_erzeugt_einen_korrekturbeleg(db: Session, welt) -> None:
    f, k = welt
    d = _abo(db, f, k)
    belege = dauerbuchungen.beende(db, d, ab=date(2027, 12, 15), admin_user_id=None)
    db.commit()
    assert len(belege) == 1 and belege[0].brutto == Decimal("-60.00")
    assert len(belege[0].positionen) == 2
    r = _saison(db, d)
    # Offene Saisonrechnung: Die Forderung sinkt, Guthaben entsteht keins (A-RECH-7).
    assert rechnungen.offener_betrag(db, r) == Decimal("60.00")
    db.refresh(k)
    assert k.guthaben == Decimal("0.00")
    storniert = [b for b in d.buchungen if b.status == "storniert"]
    assert {b.storno.korrektur_rechnung_id for b in storniert} == {belege[0].id}


def test_beenden_mit_bezahlter_saisonrechnung_schreibt_gut(db: Session, welt) -> None:
    f, k = welt
    d = _abo(db, f, k)
    rechnungen.setze_bezahlt(db, _saison(db, d), admin_user_id=None)
    db.commit()
    dauerbuchungen.beende(db, d, ab=date(2027, 12, 15), admin_user_id=None)
    db.commit()
    db.refresh(k)
    assert k.guthaben == Decimal("60.00")


def test_kein_monatslauf_mehr() -> None:
    assert not hasattr(rechnungen, "monatslauf")
    assert not hasattr(rechnungen, "erzeuge_sammelrechnung")
    assert not hasattr(jobs, "monatslauf_ausfuehren")
    assert "rechnung_tag_im_folgemonat" not in konfiguration.DEFAULTS
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_saisonrechnung.py`
Expected: FAIL – `KeyError: 'saison_zahlungsziel_tage'` bzw. `NoResultFound` (keine Saisonrechnung).

- [ ] **Step 3: Einstellungen**

`core/beachhub_core/services/konfiguration.py`:
- In `DEFAULTS` die Zeile `"rechnung_tag_im_folgemonat": (int, 3),` ersetzen durch:
```python
    "saison_zahlungsziel_tage": (int, 14),
    "guthaben_auf_saisonrechnung": (bool, True),
```
- In `BESCHREIBUNGEN` den Eintrag `"rechnung_tag_im_folgemonat"` löschen, den Eintrag `"rechnung_zahlungsziel_tage"` ersetzen und die neuen ergänzen:
```python
    "rechnung_zahlungsziel_tage": Beschreibung(
        "Zahlung und Rechnung",
        "Zahlungsziel von Einzelrechnungen",
        "Tage",
        "Für Buchungen und Events, die Sie selbst anlegen.",
    ),
    "saison_zahlungsziel_tage": Beschreibung(
        "Zahlung und Rechnung",
        "Zahlungsziel der Saisonrechnung",
        "Tage",
        "Die Saisonrechnung geht bei der Anlage eines Abos sofort an den Kunden; so lange hat er "
        "Zeit zu überweisen.",
    ),
    "guthaben_auf_saisonrechnung": Beschreibung(
        "Zahlung und Rechnung",
        "Guthaben mit der Saisonrechnung verrechnen",
        "",
        "Ja: Vorhandenes Guthaben des Kunden wird beim Erstellen der Saisonrechnung als Zahlung "
        "verrechnet; die Rechnung weist es aus. Nein: Sie verrechnen von Hand.",
    ),
```

- [ ] **Step 4: Saisonrechnung im Rechnungsdienst, Monatslauf entfernen**

`core/beachhub_core/services/rechnungen.py`:
- Importe: `from calendar import monthrange` entfernen; `Dauerbuchung` in die Modell-Importe, `Storno` entfernen, falls danach ungenutzt; `from beachhub_core.services import audit, guthaben, konfiguration`.
- `_neue_rechnung` bekommt den Parameter `zahlungsziel_tage: int | None = None` (nach `quelle`) und berechnet die Fälligkeit so:
```python
    ziel = (
        konfiguration.hole(db, "rechnung_zahlungsziel_tage")
        if zahlungsziel_tage is None
        else zahlungsziel_tage
    )
```
  und im Konstruktor `faellig_am=heute + timedelta(days=ziel),`.
- `MONATSLAUF_ZAHLUNGSARTEN`, `abrechenbare_buchungen`, `erzeuge_sammelrechnung` und `monatslauf` löschen.
- Nach `erzeuge_einzelrechnung` ergänzen:
```python
def verrechne_guthaben(db: Session, r: Rechnung, *, quelle: str) -> Decimal:
    """Verrechnet Guthaben des Kunden mit dem offenen Betrag – als Zahlung, nicht als
    Preisminderung (A-ZAHL-4). Deckt es alles, ist die Rechnung bezahlt. Einmal je Rechnung
    (provider_ref ist eindeutig)."""
    kunde = r.kunde
    db.execute(select(Kunde).where(Kunde.id == kunde.id).with_for_update())
    db.refresh(kunde)
    betrag = min(kunde.guthaben, offener_betrag(db, r))
    if betrag <= NULL:
        return NULL
    guthaben.buche(
        db,
        kunde=kunde,
        betrag=-betrag,
        art="verrechnung",
        bezug_id=r.id,
        notiz=f"Verrechnung mit Rechnung {r.nummer}",
        quelle=quelle,
    )
    db.add(
        Zahlung(
            kunde_id=kunde.id,
            rechnung_id=r.id,
            provider="guthaben",
            provider_ref=f"guthaben:{r.id}",
            betrag=betrag,
            status=Zahlung.BEZAHLT,
            empfangen_am=utcnow(),
        )
    )
    db.flush()
    if offener_betrag(db, r) == NULL:
        vorher = audit.als_dict(r)
        r.status, r.bezahlt_am = "bezahlt", utcnow()
        db.flush()
        audit.protokolliere(
            db,
            quelle=quelle,
            objekt_typ="rechnung",
            objekt_id=r.id,
            vorher=vorher,
            nachher={**audit.als_dict(r), "bezahlt_durch": "guthaben"},
        )
    return betrag


def erzeuge_saisonrechnung(db: Session, dauer: Dauerbuchung, *, quelle: str = "admin") -> Rechnung:
    """Genau eine Vorausrechnung über alle Termine einer Dauerbuchung bei ihrer Anlage
    (A-RECH-3): Leistungszeitraum erster bis letzter Termin, Zahlungsziel
    `saison_zahlungsziel_tage`. Ist es eingestellt, wird Guthaben sofort verrechnet (A-ZAHL-4).
    Kommen später Termine hinzu, ist das eine neue Dauerbuchung mit eigener Saisonrechnung."""
    if db.scalar(select(Rechnung.id).where(Rechnung.dauerbuchung_id == dauer.id)) is not None:
        raise RechnungsFehler("bereits_berechnet")
    termine = sorted(
        (b for b in dauer.buchungen if b.aktiv and b.rechnung_position_id is None),
        key=lambda b: b.beginn,
    )
    if not termine:
        raise RechnungsFehler("keine_termine")
    r = _neue_rechnung(
        db,
        dauer.kunde,
        "saison",
        [Posten(b, _positionstext(b), b.preis, b.ust_satz) for b in termine],
        lokales_datum(termine[0].beginn),
        lokales_datum(termine[-1].beginn),
        "offen",
        quelle=quelle,
        zahlungsziel_tage=konfiguration.hole(db, "saison_zahlungsziel_tage"),
    )
    r.dauerbuchung_id = dauer.id
    db.flush()
    if konfiguration.hole(db, "guthaben_auf_saisonrechnung"):
        verrechne_guthaben(db, r, quelle=quelle)
    return r
```

- [ ] **Step 5: Dauerbuchung legt die Saisonrechnung an und beendet mit einem Beleg**

`core/beachhub_core/services/dauerbuchungen.py`:
- Importe: `Rechnung` in die Modell-Importe; `rechnungen` in die Service-Importe.
- In `lege_an` nach `db.refresh(dauer)` (vor `audit.protokolliere`):
```python
    # Genau eine Saisonrechnung über alle Termine, in derselben Transaktion (A-RECH-3).
    rechnungen.erzeuge_saisonrechnung(db, dauer, quelle="admin")
```
- `beende` ersetzen:
```python
def beende(
    db: Session, dauer: Dauerbuchung, *, ab: date, admin_user_id: uuid.UUID | None
) -> list[Rechnung]:
    """Beendet eine Dauerbuchung ab einem Datum: künftige Termine werden kostenfrei storniert
    (A-DAUER-4) und die Saisonrechnung mit einem Korrekturbeleg über alle betroffenen Termine
    korrigiert (A-RECH-7). Liefert die Belege für den Versand nach dem Commit."""
    from beachhub_core.services import storno

    grenze = kombiniere(ab, time(0, 0))
    vorher = audit.als_dict(dauer)
    betroffen = [b for b in dauer.buchungen if b.beginn >= grenze and b.aktiv]
    for b in betroffen:
        storno.storniere(
            db,
            b,
            durch="betreiber",
            kostenfrei=True,
            grund="Dauerbuchung beendet",
            admin_user_id=admin_user_id,
            korrigieren=False,
        )
    belege = storno.gutschreiben_alle(
        db, betroffen, grund="Dauerbuchung beendet", quelle="admin", admin_user_id=admin_user_id
    )
    dauer.beendet_am = utcnow()
    dauer.beendet_ab = ab
    db.flush()
    audit.protokolliere(
        db,
        quelle="admin",
        objekt_typ="dauerbuchung",
        objekt_id=dauer.id,
        vorher=vorher,
        nachher=audit.als_dict(dauer),
        admin_user_id=admin_user_id,
    )
    return belege
```

`core/beachhub_core/templates/mail/dauerbuchung.txt`, vor „Viele Grüße“:
```
Die Saisonrechnung über alle Termine erhalten Sie mit einer eigenen E-Mail.

```

- [ ] **Step 6: Monatslauf aus Job, CLI und Oberfläche entfernen**

`core/beachhub_core/jobs.py`: `MARKER`, `monatslauf_faellig`, `monatslauf_fuer`, `monatslauf_ausfuehren`, `_job_monatslauf` und in `starte_scheduler` den `s.add_job(_job_monatslauf, …)`-Aufruf löschen. Danach ungenutzte Importe (`AppSetting`, `Rechnung`, `konfiguration`, `rechnung_pdf`, `rechnungen`, `RechnungsFehler`) mit `ruff check --fix core/beachhub_core/jobs.py` entfernen.

`core/beachhub_core/cli.py`: die Zeilen `m = sub.add_parser("monatslauf")`, `m.add_argument("monat", help="JJJJ-MM")` und den Zweig `elif args.cmd == "monatslauf": …` (bis einschließlich `print(f"{anzahl} Rechnungen erzeugt")`) löschen.

`core/beachhub_core/routes/rechnungen.py`: die Route `monatslauf` löschen; die Importe `jobs`, `pflicht`, `t_int` entfallen (`from beachhub_core import auth`; `from beachhub_core.routes._form import fehlertext, t_datum`).

`core/beachhub_core/templates/rechnungen/liste.html`: die Karte „Monatslauf“ (`<div class="karte schmal"> … </div>` mit dem Formular auf `/admin/rechnungen/monatslauf`) löschen.

`core/beachhub_core/routes/belegung.py`:
- Importe: `Rechnung` in die Modell-Importe.
- In `dauer_anlegen` die beiden in Task 1 ergänzten Zeilen ersetzen durch:
```python
    stornierte = [bid for bid, wahl in entscheidungen.items() if wahl == "stornieren"]
    saison = db.scalars(select(Rechnung.id).where(Rechnung.dauerbuchung_id == d.id)).all()
    benachrichtigung.belege_versenden(db, [*_belege_der_buchungen(db, stornierte), *saison])
```
- `dauer_detail`: vor `render(...)`
```python
    saison = db.scalar(select(Rechnung).where(Rechnung.dauerbuchung_id == d.id))
```
  und an `render(...)` `saison=saison, offen=rechnungen.offener_betrag(db, saison) if saison else None,` übergeben.
- `dauer_beenden`, den `try`-Block ersetzen und nach ihm die Belege versenden:
```python
    try:
        belege = dauerbuchungen.beende(
            db, d, ab=date.fromisoformat(ab), admin_user_id=admin.id
        )
        beleg_ids = [b.id for b in belege]
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return mit_flash(
            RedirectResponse(f"/admin/belegung/dauer/{d.id}", status_code=303),
            fehlertext(e, GRUND),
            "fehler",
        )
    benachrichtigung.belege_versenden(db, beleg_ids)
```
- `GRUND` ergänzen: `"keine_termine": "Keine Termine im Zeitraum",` existiert bereits; zusätzlich `"bereits_korrigiert": "Rechnungsposition wurde bereits korrigiert",`.

`core/beachhub_core/templates/belegung/dauer.html`, in der ersten Karte nach der PIN:
```html
  {% if saison %}<p>Saisonrechnung: <a href="/admin/rechnungen/{{ saison.id }}">{{ saison.nummer }}</a> · {{ saison.status }}{% if offen %} · offen {{ offen|euro }}{% endif %}</p>{% endif %}
```

- [ ] **Step 7: Bestehende Tests auf die Saisonrechnung umstellen**

`core/tests/test_jobs.py`: `test_monatslauf_faellig_nur_einmal_pro_monat` und `test_monatslauf_fuer_erzeugt_pdf_und_mail` löschen; ungenutzte Importe mit `ruff check --fix` entfernen. Die Tests zum Tageslauf der Mitgliedschaft (Plan 1a-I) bleiben.

`core/tests/test_rechnungen.py` (Importe: `dauerbuchungen` in die Service-Importe, `from sqlalchemy import select`):
- `test_sammelrechnung_und_monatslauf_idempotent` löschen.
- `_zwei_saetze` ersetzen:
```python
def _zwei_saetze(db: Session, welt) -> Rechnung:
    """Saisonrechnung mit einem Termin als Mitglied (7 %) und einem danach (19 %), A-RECH-8."""
    f, _, v1 = welt
    v1.mitglied_bis = date(2027, 12, 5)
    db.commit()
    dauerbuchungen.lege_an(
        db,
        kunde_id=v1.id,
        feld_id=f.id,
        wochentag=2,
        start=time(19),
        ende=time(20),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 8),
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
    )
    db.commit()
    return db.scalars(select(Rechnung).where(Rechnung.art == "saison")).one()
```
- `test_rundung_je_position` ersetzen:
```python
def test_rundung_je_position(db: Session, welt) -> None:
    """Drei Positionen zu 10 € bei 19 %: je Position 8,40 € netto, zusammen 25,20 € – nicht
    25,21 €, wie es die Rundung der Bruttosumme ergäbe (A-RECH-8)."""
    f, _, v1 = welt
    db.query(Tarif).update({Tarif.preis: Decimal("10.00")})
    db.commit()
    dauerbuchungen.lege_an(
        db,
        kunde_id=v1.id,
        feld_id=f.id,
        wochentag=2,
        start=time(19),
        ende=time(20),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 15),
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
    )
    db.commit()
    r = db.scalars(select(Rechnung).where(Rechnung.art == "saison")).one()
    assert [p.netto for p in r.positionen] == [Decimal("8.40")] * 3
    assert (r.netto, r.ust, r.brutto) == (Decimal("25.20"), Decimal("4.80"), Decimal("30.00"))
```
- `test_stornorechnung_gibt_buchungen_frei` ersetzen:
```python
def test_stornorechnung_gibt_buchungen_frei(db: Session, welt) -> None:
    f, _, v1 = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=v1.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b, status="offen")
    db.commit()
    s = rechnungen.storniere(db, r, admin_user_id=None, grund="Falscher Preis")
    db.commit()
    assert s.art == "storno" and s.brutto == Decimal("-30.00") and s.storniert_durch_id is None
    assert s.korrigiert_rechnung_id == r.id
    assert (
        r.status == "storniert" and r.storniert_durch_id == s.id and b.rechnung_position_id is None
    )
    assert db.query(Rechnung).count() == 2
```
- `test_setze_bezahlt_und_nicht_offen` ersetzen:
```python
def test_setze_bezahlt_und_nicht_offen(db: Session, welt) -> None:
    f, _, v1 = welt
    b = buchungen.lege_an(
        db,
        feld_id=f.id,
        kunde_id=v1.id,
        beginn=kombiniere(date(2027, 12, 1), time(19)),
        ende=kombiniere(date(2027, 12, 1), time(20)),
    )
    r = rechnungen.erzeuge_einzelrechnung(db, b, status="offen")
    db.commit()
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    db.commit()
    assert r.status == "bezahlt" and r.bezahlt_am is not None
    with pytest.raises(rechnungen.RechnungsFehler, match="nicht_offen"):
        rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    assert db.query(Audit).filter_by(objekt_typ="rechnung", objekt_id=r.id).count() > 0
```

`core/tests/test_dauerbuchungen.py` (Importe: `Rechnung` in die Modell-Importe, `from sqlalchemy import select`):
- In `test_anlegen_mit_auslassen_und_gemeinsamer_pin` am Ende ergänzen:
```python
    r = db.scalars(select(Rechnung).where(Rechnung.dauerbuchung_id == dauer.id)).one()
    assert r.art == "saison" and len(r.positionen) == 3
    assert all(b.rechnung_position_id is not None for b in dauer.buchungen)
```
- In `test_beenden_storniert_kuenftige` den Aufruf ersetzen und prüfen:
```python
    belege = dauerbuchungen.beende(db, dauer, ab=date(2027, 12, 20), admin_user_id=None)
    db.commit()
    assert len(belege) == 1 and belege[0].brutto == Decimal("-120.00")
```

`core/tests/test_ui_rechnungen.py` vollständig:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock, mail
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Kunde, Rechnung, Tarif
from beachhub_core.services import buchungen, kunden, rechnungen
from beachhub_shared.zeit import kombiniere
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


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
```

- [ ] **Step 8: Tests und Lint**

Run:
```bash
(cd core && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
grep -rn "monatslauf\|sammelrechnung\|rechnung_tag_im_folgemonat" core/beachhub_core core/tests
```
Expected: alles PASS; `grep` findet nur `test_saisonrechnung.py::test_kein_monatslauf_mehr` und `test_ui_rechnungen.py::test_kein_monatslauf_mehr`.

- [ ] **Step 9: Commit**

```bash
git add core
git commit -m "feat(core): Saisonrechnung bei Anlage der Dauerbuchung, Monatslauf entfällt"
```

---
## Task 3: Abo nur für Rechnungskunden und Mitglieder

Eine Dauerbuchung legt nur der Betreiber an, und nur für Rechnungskunden; das Anlageformular setzt das Kennzeichen auf Nachfrage mit (A-DAUER-2). Ist `abo_nur_mitglieder` an (Vorgabe ja), lehnt das System die Anlage ab, wenn die Mitgliedschaft des Kunden nicht bis zum letzten Termin reicht, und verweist auf die Kundenseite zum Verlängern (A-DAUER-5). Beide Prüfungen laufen, bevor irgendetwas geschrieben wird.

**Files:**
- Modify: `core/beachhub_core/services/konfiguration.py`, `services/dauerbuchungen.py`
- Modify: `core/beachhub_core/routes/belegung.py` (`_dauer_formular_ctx`, `dauer_anlegen`, `GRUND`)
- Modify: `core/beachhub_core/templates/belegung/dauer_neu.html`
- Test: `core/tests/test_dauerbuchungen.py`, `test_ui_belegung.py`, `test_rechnungen.py`, `test_online_buchung.py`

**Interfaces:**
- Consumes: `kundengruppen.ist_mitglied_am` (1a-I), `kunden.aendere`, `dauerbuchungen.plane`.
- Produces: Einstellung `abo_nur_mitglieder: bool = True` (Gruppe „Buchung und Storno“).
- Produces: `dauerbuchungen.lege_an(..., rechnungskunde_setzen: bool = False)` – Fehler `kein_rechnungskunde`, `mitgliedschaft_zu_kurz`; mit `rechnungskunde_setzen=True` wird der Kunde (mit Audit) zum Rechnungskunden.
- Produces: Formularfeld `rechnungskunde_setzen` in `POST /admin/belegung/dauer`; Kontext `kunde`, `mitglied_fehlt` in `belegung/dauer_neu.html`.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_dauerbuchungen.py` (Importe: `Rechnung` ist aus Task 2 da; `konfiguration` in die Service-Importe):
- Fixture `welt`: nach dem Anlegen von `k` die Zeile `k.mitglied_bis = date(2028, 4, 30)` ergänzen (Saisonabo nur für Mitglieder).
- Neue Tests:
```python
def _abo(db: Session, f, kunde, **extra):
    return dauerbuchungen.lege_an(
        db,
        kunde_id=kunde.id,
        feld_id=f.id,
        wochentag=1,
        start=time(19),
        ende=time(21),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 31),
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
        **extra,
    )


def test_abo_nur_fuer_rechnungskunden(db: Session, welt) -> None:
    f, _, k2 = welt  # k2 ist kein Rechnungskunde
    k2.mitglied_bis = date(2028, 4, 30)
    db.commit()
    with pytest.raises(dauerbuchungen.DauerbuchungsFehler, match="kein_rechnungskunde"):
        _abo(db, f, k2)
    db.rollback()
    assert db.query(Dauerbuchung).count() == 0 and db.query(Rechnung).count() == 0
    d = _abo(db, f, k2, rechnungskunde_setzen=True)
    db.commit()
    db.refresh(k2)
    assert k2.rechnungskunde is True and len(d.buchungen) == 4


def test_abo_nur_fuer_mitglieder_bis_zum_letzten_termin(db: Session, welt) -> None:
    f, k, _ = welt
    k.mitglied_bis = date(2027, 12, 20)  # endet vor dem 21. und 28.12.
    db.commit()
    with pytest.raises(dauerbuchungen.DauerbuchungsFehler, match="mitgliedschaft_zu_kurz"):
        _abo(db, f, k)
    db.rollback()
    assert db.query(Dauerbuchung).count() == 0
    konfiguration.setze(db, "abo_nur_mitglieder", "nein")
    db.commit()
    d = _abo(db, f, k)
    db.commit()
    # Ohne die Regel gelten die Konditionen am jeweiligen Termin (A-KUND-6).
    assert [b.ust_satz for b in d.buchungen] == [Decimal("7.00")] * 2 + [Decimal("19.00")] * 2
```

`core/tests/test_ui_belegung.py`:
- Fixture `welt`: nach dem Anlegen von `v1` `v1.mitglied_bis = date(2028, 4, 30)` ergänzen.
- Neue Tests:
```python
def _abo_daten(c: TestClient, f, kunde) -> dict[str, str]:
    return {
        "csrf_token": c.csrf,
        "kunde_id": str(kunde.id),
        "feld_id": str(f.id),
        "wochentag": "1",
        "start": "19:00",
        "ende": "21:00",
        "gueltig_von": "2027-12-01",
        "gueltig_bis": "2027-12-31",
    }


def test_ui_abo_ohne_mitgliedschaft_zeigt_link_zur_kundenseite(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    f, _, v1 = welt
    v1.mitglied_bis = None
    db.commit()
    c = eingeloggt
    daten = _abo_daten(c, f, v1)
    r = c.post("/admin/belegung/dauer/planen", data=daten)
    assert f'href="/admin/kunden/{v1.id}"' in r.text
    r = c.post("/admin/belegung/dauer", data=daten)
    assert "reicht nicht bis zum letzten Termin" in r.text
    assert db.query(Dauerbuchung).count() == 0


def test_ui_abo_rechnungskunde_markieren(eingeloggt: TestClient, db: Session, welt) -> None:
    f, a, _ = welt
    a.mitglied_bis = date(2028, 4, 30)
    db.commit()
    c = eingeloggt
    daten = _abo_daten(c, f, a)
    assert 'name="rechnungskunde_setzen"' in c.post("/admin/belegung/dauer/planen", data=daten).text
    r = c.post("/admin/belegung/dauer", data=daten)
    assert "nur für Rechnungskunden" in r.text
    r = c.post(
        "/admin/belegung/dauer",
        data={**daten, "rechnungskunde_setzen": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(a)
    assert a.rechnungskunde is True
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_dauerbuchungen.py tests/test_ui_belegung.py`
Expected: FAIL – `TypeError: lege_an() got an unexpected keyword argument 'rechnungskunde_setzen'`, `KeyError: 'abo_nur_mitglieder'`.

- [ ] **Step 3: Einstellung**

`core/beachhub_core/services/konfiguration.py`, in `DEFAULTS` nach `"storno_frist_stunden"`: `"abo_nur_mitglieder": (bool, True),` und in `BESCHREIBUNGEN` nach `"storno_frist_stunden"`:
```python
    "abo_nur_mitglieder": Beschreibung(
        "Buchung und Storno",
        "Saisonabo nur für Mitglieder",
        "",
        "Ja: Ein Abo lässt sich nur anlegen, wenn die Mitgliedschaft des Kunden bis zum letzten "
        "Termin reicht; alle Termine laufen dann zum Satz der Mitglieder. Nein: Auch "
        "Nicht-Mitglieder bekommen Abos zu ihren Konditionen.",
    ),
```

- [ ] **Step 4: Prüfungen bei der Anlage**

`core/beachhub_core/services/dauerbuchungen.py`:
- Importe: `kunden` in die Service-Importe (`from beachhub_core.services import audit, buchungen, konfiguration, kunden, kundengruppen, pin, rechnungen, sperren, tarife`).
- `lege_an` bekommt den Parameter `rechnungskunde_setzen: bool = False` (nach `entscheidungen`).
- In `lege_an` direkt nach `if not termine: raise DauerbuchungsFehler("keine_termine")`:
```python
    kunde = db.get(Kunde, kunde_id)
    assert kunde is not None  # plane() hat den Kunden geprüft
    # Abos gibt es nur für Rechnungskunden (A-DAUER-2) und – einstellbar – nur für Mitglieder,
    # deren Mitgliedschaft bis zum letzten Termin reicht (A-DAUER-5). Geprüft wird, bevor
    # irgendetwas geschrieben ist.
    if not kunde.rechnungskunde and not rechnungskunde_setzen:
        raise DauerbuchungsFehler("kein_rechnungskunde")
    if konfiguration.hole(db, "abo_nur_mitglieder") and not kundengruppen.ist_mitglied_am(
        kunde, termine[-1].datum
    ):
        raise DauerbuchungsFehler("mitgliedschaft_zu_kurz")
    if not kunde.rechnungskunde:
        kunden.aendere(db, kunde, admin_user_id=admin_user_id, rechnungskunde=True)
```

- [ ] **Step 5: Formular**

`core/beachhub_core/routes/belegung.py`:
- Importe: `kundengruppen` in die Service-Importe.
- `GRUND` ergänzen:
```python
    "kein_rechnungskunde": "Abos gibt es nur für Rechnungskunden – bitte „als Rechnungskunde "
    "markieren“ ankreuzen",
    "mitgliedschaft_zu_kurz": "Die Mitgliedschaft des Kunden reicht nicht bis zum letzten "
    "Termin. Bitte zuerst auf der Kundenseite verlängern.",
```
- `_dauer_formular_ctx` ersetzen:
```python
def _dauer_formular_ctx(
    db: Session, *, termine: Any, werte: dict[str, Any], fehler: str | None = None
) -> dict[str, Any]:
    try:
        kunde = db.get(Kunde, uuid.UUID(str(werte.get("kunde_id", ""))))
    except ValueError:
        kunde = None
    mitglied_fehlt = bool(
        termine
        and kunde is not None
        and konfiguration.hole(db, "abo_nur_mitglieder")
        and not kundengruppen.ist_mitglied_am(kunde, termine[-1].datum)
    )
    ctx: dict[str, Any] = {
        "termine": termine,
        "werte": werte,
        "kunden": _kunden_liste(db),
        "felder": _aktive_felder(db),
        "kunde": kunde,
        "mitglied_fehlt": mitglied_fehlt,
    }
    if fehler is not None:
        ctx["fehler"] = fehler
    return ctx
```
- In `dauer_anlegen` den Aufruf `dauerbuchungen.lege_an(...)` um `rechnungskunde_setzen=form.get("rechnungskunde_setzen") == "1",` ergänzen.

`core/beachhub_core/templates/belegung/dauer_neu.html`, vor den beiden Knöpfen am Formularende:
```html
  {% if kunde and not kunde.rechnungskunde %}
  <div class="karte schmal">
    <label><input type="checkbox" name="rechnungskunde_setzen" value="1" {% if werte.get("rechnungskunde_setzen") == "1" %}checked{% endif %}> {{ kunde.name }} als Rechnungskunde markieren – Abos gibt es nur für Rechnungskunden</label>
  </div>
  {% endif %}
  {% if mitglied_fehlt %}<p class="fehler">Die Mitgliedschaft von {{ kunde.name }} reicht nicht bis zum letzten Termin. <a href="/admin/kunden/{{ kunde.id }}">Mitgliedschaft auf der Kundenseite verlängern</a></p>{% endif %}
```

- [ ] **Step 6: Übrige Tests an die neuen Regeln anpassen**

- `core/tests/test_rechnungen.py`: in `_zwei_saetze` und `test_rundung_je_position` vor `dauerbuchungen.lege_an(...)` `konfiguration.setze(db, "abo_nur_mitglieder", "nein")` ergänzen (beide Tests rechnen bewusst mit Terminen außerhalb einer Mitgliedschaft).
- `core/tests/test_online_buchung.py::test_storno_dauerbuchungstermin_nicht_stornierbar`: vor `dauerbuchungen.lege_an(...)` `k.mitglied_bis = date(2028, 4, 30)` setzen und den Aufruf um `rechnungskunde_setzen=True,` ergänzen. (Task 4 ersetzt den Test.)

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
git commit -m "feat(core): Abo nur für Rechnungskunden und Mitglieder bis zum letzten Termin"
```

---
## Task 4: Freie Abo-Absagen und Absage im Portal

Kunden sagen Termine ihrer Dauerbuchung im Portal ab (Anfrage `buchung_stornieren`, Abweichung B-6); der Slot wird sofort frei. Kostenfrei sind je Dauerbuchung bis zu `abo_freie_absagen` Termine (Vorgabe 3; `0` = Saison fest bezahlt), und nur innerhalb der Stornofrist (A-DAUER-3). Eine kostenfreie Absage korrigiert die Saisonrechnung mit einem Teil-Storno (Task 1): bezahlt → Guthaben, offen → die Forderung sinkt. Gezählt werden nur freie Absagen durch den Kunden (`storno.freie_absage`); Kulanz, Sperren und Betreiber-Storno zählen nicht. Antwort, Konto-Dokument und Stornomail nennen die verbleibenden freien Absagen (A-MAIL-2).

**Files:**
- Modify: `shared/beachhub_shared/kanal.py` (`Antwort`), `shared/beachhub_shared/lesestand.py` (`KontoBuchung`)
- Test: `shared/tests/test_kanal.py`
- Modify: `core/beachhub_core/models/buchungen.py` (`Buchung.im_portal_stornierbar`)
- Modify: `core/beachhub_core/services/konfiguration.py`, `services/storno.py`, `services/online_buchung.py`, `services/lesestand.py`, `services/benachrichtigung.py`
- Modify: `core/beachhub_core/routes/belegung.py` (`dauer_detail`), `templates/belegung/dauer.html`, `templates/mail/storno.txt`
- Create: `core/tests/test_abo_absagen.py`
- Modify: `core/tests/test_online_buchung.py`

**Interfaces:**
- Consumes: `storno.gutschreiben` (Task 1), Saisonrechnung (Task 2), `lesestand.markiere_rechnungskunden` (1a-I).
- Produces (shared): `Antwort.freie_absage: bool | None = None`, `Antwort.verbleibende_freie_absagen: int | None = None`; `KontoBuchung.abo: bool = False`, `KontoBuchung.freie_absagen_rest: int | None = None`.
- Produces: Einstellung `abo_freie_absagen: int = 3` (Gruppe „Buchung und Storno“); Ändern markiert die Konto-Dokumente aller Rechnungskunden.
- Produces: `storno.freie_absagen_rest(db, dauer: Dauerbuchung) -> int`; `storno.storniere` setzt bei Abo-Terminen, die der Kunde ohne vorgegebenes `kostenfrei` storniert, `kostenfrei = freie_absage = (innerhalb der Frist und Rest > 0)`.
- Produces: `Buchung.im_portal_stornierbar` ist wahr für Portal-Buchungen **und** Termine von Dauerbuchungen.
- Produces: Antwort auf `buchung_stornieren` bei Abo-Terminen mit `freie_absage` und `verbleibende_freie_absagen`.

- [ ] **Step 1: Failing Tests in `shared` schreiben**

`shared/tests/test_kanal.py`:
```python
def test_antwort_und_konto_kennen_freie_absagen() -> None:
    a = kanal.Antwort(status="ok", kostenfrei=True, freie_absage=True, verbleibende_freie_absagen=2)
    d = a.model_dump(mode="json", exclude_none=True)
    assert d["freie_absage"] is True and d["verbleibende_freie_absagen"] == 2
    assert "freie_absage" not in kanal.Antwort(status="ok").model_dump(exclude_none=True)
    alt = {
        "id": "b",
        "feld_id": "f",
        "feld_name": "F1",
        "beginn": "2027-12-01T18:00:00Z",
        "ende": "2027-12-01T19:00:00Z",
        "status": "bestaetigt",
        "preis": "30.00",
        "pin": None,
        "storno": None,
    }
    b = KontoBuchung.model_validate(alt)
    assert b.abo is False and b.freie_absagen_rest is None
```

Run: `cd shared && pytest -q tests/test_kanal.py` – Expected: FAIL (`ValidationError`/`AttributeError`).

- [ ] **Step 2: Vertrag in `shared` erweitern**

`shared/beachhub_shared/kanal.py`, in `Antwort` nach `kostenfrei`:
```python
    # Absage eines Abo-Termins (A-DAUER-3): war sie eine der kostenfreien, wie viele bleiben?
    freie_absage: bool | None = None
    verbleibende_freie_absagen: int | None = None
```

`shared/beachhub_shared/lesestand.py`, in `KontoBuchung` nach `stornierbar`:
```python
    # Termin einer Dauerbuchung und die verbleibenden kostenfreien Absagen dieses Abos
    # (A-DAUER-3). Mit Vorgabe, damit ältere gespeicherte Dokumente gültig bleiben.
    abo: bool = False
    freie_absagen_rest: int | None = None
```

Run: `cd shared && pytest -q` – Expected: PASS.

- [ ] **Step 3: Failing Tests im Hauptsystem schreiben**

`core/tests/test_abo_absagen.py`:
```python
from datetime import date, time
from decimal import Decimal

import pytest
from beachhub_core import clock
from beachhub_core.models import (
    Betriebszeit,
    Dauerbuchung,
    Feld,
    FeldRaster,
    Kunde,
    Rechnung,
    Tarif,
)
from beachhub_core.services import (
    dauerbuchungen,
    konfiguration,
    kunden,
    lesestand,
    online_buchung,
    rechnungen,
    storno,
)
from beachhub_shared.zeit import kombiniere
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture
def welt(db: Session) -> tuple[Feld, Kunde]:
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("30.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="TSV", email="tsv@x.de", rechnungskunde=True)
    k.mitglied_bis = date(2028, 4, 30)
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    return f, k


def _abo(db: Session, f: Feld, k: Kunde) -> tuple[Dauerbuchung, Rechnung]:
    """Mittwochs 19–20 Uhr, 01.12. bis 29.12.2027: fünf Termine zu 30 €."""
    d = dauerbuchungen.lege_an(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=2,
        start=time(19),
        ende=time(20),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 29),
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
    )
    db.commit()
    return d, db.scalars(select(Rechnung).where(Rechnung.dauerbuchung_id == d.id)).one()


def _absagen(db: Session, k: Kunde, b) -> online_buchung.Ergebnis:
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=b.id)
    db.commit()
    return erg


def test_freie_absagen_werden_gezaehlt(db: Session, welt) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    termine = list(d.buchungen)
    antworten = [_absagen(db, k, b).antwort for b in termine[:4]]
    assert [(a.kostenfrei, a.freie_absage, a.verbleibende_freie_absagen) for a in antworten] == [
        (True, True, 2),
        (True, True, 1),
        (True, True, 0),
        (False, False, 0),
    ]
    # Auch die vierte Absage gibt den Platz frei (A-DAUER-3).
    assert all(b.status == "storniert" for b in termine[:4])
    # Offene Saisonrechnung: drei Teil-Stornos senken die Forderung um 3 × 30 €.
    assert rechnungen.offener_betrag(db, r) == Decimal("60.00")
    db.refresh(k)
    assert k.guthaben == Decimal("0.00")
    assert storno.freie_absagen_rest(db, d) == 0


def test_absage_nach_frist_kostet_und_zaehlt_nicht(db: Session, welt, monkeypatch) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    erster = d.buchungen[0]  # 01.12. 19:00
    monkeypatch.setattr(storno.clock, "now", lambda db: kombiniere(date(2027, 12, 1), time(10)))
    a = _absagen(db, k, erster).antwort
    assert a.kostenfrei is False and a.freie_absage is False
    assert a.verbleibende_freie_absagen == 3
    assert erster.status == "storniert"
    assert rechnungen.offener_betrag(db, r) == Decimal("150.00")


def test_null_freie_absagen_heisst_saison_fest_bezahlt(db: Session, welt) -> None:
    f, k = welt
    konfiguration.setze(db, "abo_freie_absagen", 0)
    db.commit()
    d, r = _abo(db, f, k)
    a = _absagen(db, k, d.buchungen[0]).antwort
    assert a.kostenfrei is False and a.verbleibende_freie_absagen == 0
    assert rechnungen.offener_betrag(db, r) == Decimal("150.00")


def test_kulanz_zaehlt_nicht_als_freie_absage(db: Session, welt, monkeypatch) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    monkeypatch.setattr(storno.clock, "now", lambda db: kombiniere(date(2027, 12, 1), time(10)))
    _absagen(db, k, d.buchungen[0])
    monkeypatch.undo()
    s = d.buchungen[0].storno
    storno.kulanz(db, s, admin_user_id=None, grund="Krankheit")
    # Ein Betreiber-Storno innerhalb der Frist ist kostenfrei, zählt aber ebenfalls nicht.
    storno.storniere(db, d.buchungen[1], durch="betreiber", grund="Turnier")
    db.commit()
    assert s.kostenfrei is True and s.freie_absage is False
    assert storno.freie_absagen_rest(db, d) == 3
    assert rechnungen.offener_betrag(db, r) == Decimal("90.00")


def test_freie_absage_bei_bezahlter_saisonrechnung_wird_guthaben(
    db: Session, welt, mail_ausgang: list
) -> None:
    f, k = welt
    d, r = _abo(db, f, k)
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    db.commit()
    erg = _absagen(db, k, d.buchungen[0])
    for schritt in erg.nach_commit:
        schritt(db)
    db.refresh(k)
    assert k.guthaben == Decimal("30.00")
    betreffs = [m["betreff"] for m in mail_ausgang]
    assert "Stornierung Ihrer Buchung" in betreffs
    assert any(b.startswith("Stornorechnung ") for b in betreffs)
    storno_mail = next(m for m in mail_ausgang if m["betreff"] == "Stornierung Ihrer Buchung")
    assert "es bleiben 2" in storno_mail["text"]


def test_konto_zeigt_abo_und_restliche_absagen(db: Session, welt) -> None:
    f, k = welt
    d, _ = _abo(db, f, k)
    _absagen(db, k, d.buchungen[0])
    je_id = {b.id: b for b in lesestand.baue_konto(db, k).buchungen}
    zweiter = je_id[str(d.buchungen[1].id)]
    assert zweiter.abo is True and zweiter.freie_absagen_rest == 2 and zweiter.stornierbar is True


def test_aenderung_der_freien_absagen_markiert_konten(db: Session, welt) -> None:
    f, k = welt
    _abo(db, f, k)
    lesestand.markiere_geaendert(db, f"konto:{k.id}")
    db.commit()
    from beachhub_core.models import LesestandVersion

    db.get(LesestandVersion, f"konto:{k.id}").geaendert = False
    db.commit()
    konfiguration.setze(db, "abo_freie_absagen", 5)
    db.commit()
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert
```

`core/tests/test_online_buchung.py`: `test_storno_dauerbuchungstermin_nicht_stornierbar` ersetzen durch
```python
def test_storno_dauerbuchungstermin_ist_freie_absage(db: Session, welt) -> None:
    # Abo-Termine sagt der Kunde im Portal ab (A-DAUER-3); die offene Saisonrechnung sinkt,
    # Guthaben entsteht keins.
    f, k, _ = welt
    k.mitglied_bis = date(2028, 4, 30)
    db.commit()
    dauer = dauerbuchungen.lege_an(
        db,
        kunde_id=k.id,
        feld_id=f.id,
        wochentag=1,
        start=time(19),
        ende=time(20),
        gueltig_von=date(2027, 12, 1),
        gueltig_bis=date(2027, 12, 10),
        admin_user_id=None,
        auslassen=set(),
        entscheidungen={},
        rechnungskunde_setzen=True,
    )
    db.commit()
    termin = dauer.buchungen[0]
    assert termin.zahlungsart == "saison"
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=termin.id)
    db.commit()
    assert erg.antwort.status == "ok" and erg.antwort.kostenfrei is True
    assert erg.antwort.freie_absage is True and erg.antwort.verbleibende_freie_absagen == 2
    assert db.get(Buchung, termin.id).status == "storniert"
    assert k.guthaben == Decimal("0.00")
    assert db.scalars(select(GuthabenBuchung)).all() == []
```

- [ ] **Step 4: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_abo_absagen.py tests/test_online_buchung.py`
Expected: FAIL – Antwort `nicht_stornierbar` bzw. `KeyError: 'abo_freie_absagen'`.

- [ ] **Step 5: Einstellung**

`core/beachhub_core/services/konfiguration.py`, in `DEFAULTS` nach `"abo_nur_mitglieder"`: `"abo_freie_absagen": (int, 3),`; in `BESCHREIBUNGEN` nach `"abo_nur_mitglieder"`:
```python
    "abo_freie_absagen": Beschreibung(
        "Buchung und Storno",
        "Kostenfreie Absagen im Abo",
        "je Abo",
        "So viele Termine eines Saisonabos kann der Kunde innerhalb der Stornofrist kostenfrei "
        "absagen; der Betrag wird gutgeschrieben bzw. die Forderung sinkt. 0 heißt: Die Saison "
        "ist fest bezahlt.",
    ),
```
Am Ende von `setze` die vorhandene Bedingung für `rechnungskunden_online_buchen` erweitern:
```python
    if schluessel in ("rechnungskunden_online_buchen", "abo_freie_absagen"):
        from beachhub_core.services import lesestand

        # Beide Werte stehen im Konto-Dokument der Rechnungskunden (online_buchen bzw. die
        # verbleibenden freien Absagen ihrer Abos).
        lesestand.markiere_rechnungskunden(db)
```

- [ ] **Step 6: Freie Absage im Storno**

`core/beachhub_core/services/storno.py`:
- Importe: `from sqlalchemy import func, select`; `Dauerbuchung` in die Modell-Importe.
- Vor `storniere`:
```python
def freie_absagen_rest(db: Session, dauer: Dauerbuchung) -> int:
    """Verbleibende kostenfreie Absagen eines Abos (A-DAUER-3): je Dauerbuchung
    `abo_freie_absagen`; gezählt werden nur freie Absagen des Kunden, nicht Kulanz, Sperren oder
    das Beenden durch den Betreiber."""
    genutzt = db.scalar(
        select(func.count(Storno.id))
        .join(Buchung, Storno.buchung_id == Buchung.id)
        .where(Buchung.dauerbuchung_id == dauer.id, Storno.freie_absage.is_(True))
    )
    return max(0, int(konfiguration.hole(db, "abo_freie_absagen")) - int(genutzt or 0))
```
- In `storniere` den Block `if kostenfrei is None: …` und die Anlage des Stornos ersetzen:
```python
    freie_absage = False
    if kostenfrei is None:
        frist = timedelta(hours=konfiguration.hole(db, "storno_frist_stunden"))
        kostenfrei = jetzt <= buchung.beginn - frist
        if buchung.dauerbuchung_id is not None and durch == "kunde":
            # Abo-Termine sind im Voraus bezahlt: Kostenfrei ist eine Absage nur innerhalb der
            # Frist und solange freie Absagen übrig sind (A-DAUER-3). Die Zeile der Dauerbuchung
            # wird gesperrt, damit zwei gleichzeitige Absagen nicht beide die letzte nehmen.
            dauer = db.scalar(
                select(Dauerbuchung)
                .where(Dauerbuchung.id == buchung.dauerbuchung_id)
                .with_for_update()
            )
            freie_absage = bool(kostenfrei and dauer and freie_absagen_rest(db, dauer) > 0)
            kostenfrei = freie_absage
    s = Storno(
        buchung_id=buchung.id,
        durch=durch,
        kostenfrei=kostenfrei,
        freie_absage=freie_absage,
        grund=grund,
    )
```

`core/beachhub_core/models/buchungen.py`, `im_portal_stornierbar` ersetzen:
```python
    @property
    def im_portal_stornierbar(self) -> bool:
        """Portal-Buchungen und Termine von Dauerbuchungen: Bei beiden kennt das System die
        Rechnung, über die ein kostenfreies Storno korrigiert wird (A-DAUER-3). Einzelbuchungen,
        die der Betreiber angelegt hat, storniert nur er."""
        return self.quelle == "portal" or self.dauerbuchung_id is not None
```

`core/beachhub_core/services/online_buchung.py`:
- Importe: `Dauerbuchung` in die Modell-Importe.
- In `storniere_fuer_kunde` den Kommentar vor `return abgelehnt("nicht_stornierbar")` ersetzen durch `# Einzelbuchungen des Betreibers storniert nur er.` und den Rückgabewert ersetzen:
```python
    rest = None
    if b.dauerbuchung_id is not None:
        dauer = db.get(Dauerbuchung, b.dauerbuchung_id)
        rest = storno.freie_absagen_rest(db, dauer) if dauer is not None else None
    antwort = kanal.Antwort(
        status="ok",
        kostenfrei=s.kostenfrei,
        freie_absage=s.freie_absage if b.dauerbuchung_id is not None else None,
        verbleibende_freie_absagen=rest,
    )
    return Ergebnis(antwort, [_storno_mail(s.id)])
```

- [ ] **Step 7: Konto-Dokument, Mail, Dauerbuchungsseite**

`core/beachhub_core/services/lesestand.py`, in `baue_konto`:
- vor der Schleife: `from beachhub_core.services import storno as storno_dienst` und `rest_je_abo: dict[uuid.UUID, int] = {}`; Import `Dauerbuchung` in die Modell-Importe.
- in der Schleife vor `out.append(...)`:
```python
        rest = None
        if b.dauerbuchung_id is not None:
            if b.dauerbuchung_id not in rest_je_abo:
                dauer = db.get(Dauerbuchung, b.dauerbuchung_id)
                rest_je_abo[b.dauerbuchung_id] = (
                    storno_dienst.freie_absagen_rest(db, dauer) if dauer is not None else 0
                )
            rest = rest_je_abo[b.dauerbuchung_id]
```
- im Konstruktor `schema.KontoBuchung(...)` ergänzen: `abo=b.dauerbuchung_id is not None, freie_absagen_rest=rest,`.

`core/beachhub_core/services/benachrichtigung.py`, `storno` ersetzen:
```python
def storno(db: Session, s: Storno) -> None:
    rest = None
    if s.buchung.dauerbuchung_id is not None:
        from beachhub_core.services import storno as storno_dienst  # Zyklus vermeiden

        dauer = db.get(Dauerbuchung, s.buchung.dauerbuchung_id)
        rest = storno_dienst.freie_absagen_rest(db, dauer) if dauer is not None else None
    mail.sende(
        s.buchung.kunde.email,
        "Stornierung Ihrer Buchung",
        _text("storno", s=s, b=s.buchung, rest=rest),
    )
```

`core/beachhub_core/templates/mail/storno.txt` vollständig:
```
Hallo {{ b.kunde.name }},

Ihre Buchung wurde storniert:
Feld {{ b.feld.name }}, {{ b.beginn|lokal }} bis {{ b.ende|uhrzeit }} Uhr

{% if s.kostenfrei -%}
Die Stornierung ist für Sie kostenfrei.{% if s.freie_absage %} Das war eine der kostenfreien Absagen in Ihrem Abo; es bleiben {{ rest }}.{% endif %}
{%- elif b.dauerbuchung_id -%}
Der Termin bleibt berechnet: {% if rest == 0 %}Ihre kostenfreien Absagen im Abo sind aufgebraucht.{% else %}Die Absagefrist war bereits abgelaufen.{% endif %} Der Platz ist trotzdem wieder frei.
{%- else -%}
Da die Stornofrist bereits abgelaufen war, bleibt der Betrag fällig.
{%- endif %}

Viele Grüße
{{ betreiber }}
```

`core/beachhub_core/routes/belegung.py`, `dauer_detail`: an `render(...)` übergeben `freie_absagen=storno.freie_absagen_rest(db, d), freie_absagen_max=konfiguration.hole(db, "abo_freie_absagen"),`.

`core/beachhub_core/templates/belegung/dauer.html`, in der ersten Karte nach der Saisonrechnung:
```html
  <p>Kostenfreie Absagen übrig: {{ freie_absagen }} von {{ freie_absagen_max }}</p>
```

- [ ] **Step 8: Tests und Lint**

Run:
```bash
(cd shared && pytest -q) && (cd core && pytest -q) && (cd portal && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS. Im Portal bleiben die bestehenden Tests grün: Abo-Termine erscheinen dort jetzt mit `stornierbar = true`; die Anzeige der freien Absagen folgt mit Stufe 2.

- [ ] **Step 9: Commit**

```bash
git add shared core
git commit -m "feat(core): Abo-Termine im Portal absagen, bis zu drei kostenfrei"
```

---
## Task 5: Teil-Stornorechnung von Hand und offene Beträge in der Rechnungsliste

Auf der Rechnungsseite wählt der Betreiber einzelne Positionen aus und erzeugt dafür eine Teil-Stornorechnung (A-ADM-4) – etwa wenn ein Abo-Termin wegen einer Hallensperre ausfiel und er ihn gutschreiben will. Bereits bezahlte Anteile werden wie bei jedem kostenfreien Storno Guthaben (`storno.gutschreiben_positionen`, Abweichung B-8). Die Rechnungsliste zeigt je Rechnung den offenen Betrag, damit offene Saisonrechnungen auffallen (A-RECH-3).

**Files:**
- Modify: `core/beachhub_core/routes/rechnungen.py` (`liste`, neue Route `teilstorno`, `GRUND`)
- Modify: `core/beachhub_core/templates/rechnungen/liste.html`, `templates/rechnungen/detail.html`
- Test: `core/tests/test_ui_rechnungen.py`

**Interfaces:**
- Consumes: `storno.gutschreiben_positionen`, `rechnungen.offener_betrag`, `benachrichtigung.belege_versenden` (Task 1).
- Produces: Route `POST /admin/rechnungen/{rechnung_id}/teilstorno` (Felder `positionen` mehrfach = IDs von `rechnung_position`, `grund`) → Weiterleitung auf den Korrekturbeleg.
- Produces: Kontext `offen_je_rechnung: dict[UUID, Decimal]` in `rechnungen/liste.html`.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_ui_rechnungen.py` (Importe ergänzen: `from sqlalchemy import select`):
```python
def test_teilstorno_ueber_ausgewaehlte_positionen(
    eingeloggt: TestClient, db: Session, welt
) -> None:
    c = eingeloggt
    _abo(c, *welt)
    r = db.query(Rechnung).one()
    rechnungen.setze_bezahlt(db, r, admin_user_id=None)
    db.commit()
    erste = r.positionen[0]
    mail.TEST_AUSGANG.clear()
    seite = c.get(f"/admin/rechnungen/{r.id}")
    assert f'name="positionen" value="{erste.id}"' in seite.text
    antwort = c.post(
        f"/admin/rechnungen/{r.id}/teilstorno",
        data={"csrf_token": c.csrf, "grund": "Halle gesperrt", "positionen": [str(erste.id)]},
        follow_redirects=False,
    )
    assert antwort.status_code == 303
    beleg = db.scalars(select(Rechnung).where(Rechnung.korrigiert_rechnung_id == r.id)).one()
    assert antwort.headers["location"] == f"/admin/rechnungen/{beleg.id}"
    assert beleg.brutto == -erste.brutto and len(beleg.positionen) == 1
    db.expire_all()
    assert db.get(Kunde, welt[1].id).guthaben == erste.brutto  # bezahlt → Guthaben
    assert [m["betreff"] for m in mail.TEST_AUSGANG] == [f"Stornorechnung {beleg.nummer}"]
    # Die korrigierte Position lässt sich nicht noch einmal auswählen.
    assert f'name="positionen" value="{erste.id}"' not in c.get(f"/admin/rechnungen/{r.id}").text


def test_teilstorno_ohne_auswahl_meldet_fehler(eingeloggt: TestClient, db: Session, welt) -> None:
    c = eingeloggt
    _abo(c, *welt)
    r = db.query(Rechnung).one()
    seite = c.post(
        f"/admin/rechnungen/{r.id}/teilstorno", data={"csrf_token": c.csrf, "grund": "x"}
    )
    assert "mindestens eine Position" in seite.text
    assert db.query(Rechnung).count() == 1


def test_liste_zeigt_offenen_betrag(eingeloggt: TestClient, db: Session, welt) -> None:
    c = eingeloggt
    _abo(c, *welt)
    seite = c.get("/admin/rechnungen")
    assert '<th class="rechts">Offen</th>' in seite.text
    assert seite.text.count("60,00 €") >= 2  # Brutto und offener Betrag der Saisonrechnung
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_ui_rechnungen.py`
Expected: FAIL – Route `teilstorno` antwortet 404/405; Spalte „Offen“ fehlt.

- [ ] **Step 3: Routen**

`core/beachhub_core/routes/rechnungen.py`:
- Importe: `from beachhub_core.services import benachrichtigung, rechnung_pdf, rechnungen, storno` (die Modell-Importe bleiben `AdminUser, Kunde, Rechnung`).
- `GRUND` ergänzen:
```python
    "bereits_korrigiert": "Eine der Positionen wurde bereits korrigiert",
    "keine_positionen": "Bitte mindestens eine Position auswählen",
    "verschiedene_rechnungen": "Die Positionen gehören nicht zu dieser Rechnung",
```
- In `liste` die Abfrage in eine Variable legen und den offenen Betrag mitgeben:
```python
    gefunden = db.scalars(stmt.limit(500)).all()
    return render(
        request,
        "rechnungen/liste.html",
        admin=admin,
        rechnungen=gefunden,
        offen_je_rechnung={r.id: rechnungen.offener_betrag(db, r) for r in gefunden},
        status=status,
        von=von,
        bis=bis,
        q=q,
    )
```
- Neue Route nach `storno`:
```python
@router.post("/rechnungen/{rechnung_id}/teilstorno", response_model=None)
async def teilstorno(
    request: Request,
    rechnung_id: uuid.UUID,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Teil-Stornorechnung über ausgewählte Positionen (A-ADM-4). Der bezahlte Anteil wird
    Guthaben, sonst sinkt die Forderung – wie bei jedem kostenfreien Storno (A-STORNO-6)."""
    r = db.get(Rechnung, rechnung_id)
    if r is None:
        return mit_flash(
            RedirectResponse("/admin/rechnungen", status_code=303), "Nicht gefunden", "fehler"
        )
    form = await request.form()
    try:
        ids = [uuid.UUID(str(v)) for v in form.getlist("positionen")]
        positionen = [p for p in r.positionen if p.id in ids]
        if len(positionen) != len(ids):
            raise RechnungsFehler("verschiedene_rechnungen")
        beleg = storno.gutschreiben_positionen(
            db,
            positionen,
            grund=str(form.get("grund", "")).strip() or "Teil-Storno",
            quelle="admin",
            admin_user_id=admin.id,
        )
        beleg_id = beleg.id
        db.commit()
    except (RechnungsFehler, ValueError, IntegrityError) as e:
        db.rollback()
        return mit_flash(
            RedirectResponse(f"/admin/rechnungen/{rechnung_id}", status_code=303),
            fehlertext(e, GRUND),
            "fehler",
        )
    benachrichtigung.belege_versenden(db, [beleg_id])
    return mit_flash(
        RedirectResponse(f"/admin/rechnungen/{beleg_id}", status_code=303),
        "Teil-Stornorechnung erzeugt",
    )
```

- [ ] **Step 4: Vorlagen**

`core/beachhub_core/templates/rechnungen/liste.html`, in der Tabelle die Kopfzeile und die Zeile ersetzen:
```html
  <tr><th>Nummer</th><th>Datum</th><th>Kunde</th><th>Art</th><th class="rechts">Brutto</th><th class="rechts">Offen</th><th>Status</th><th>Fällig</th><th></th></tr>
  {% for r in rechnungen %}<tr>
    <td>{{ r.nummer }}</td>
    <td>{{ r.datum|datum }}</td>
    <td>{{ r.adresse_snapshot.get("name", "") }}</td>
    <td>{{ r.art }}</td>
    <td class="rechts">{{ r.brutto|euro }}</td>
    <td class="rechts">{{ offen_je_rechnung[r.id]|euro if offen_je_rechnung[r.id] else "–" }}</td>
    <td>{{ r.status }}</td>
    <td>{{ r.faellig_am|datum }}</td>
    <td><a class="aktion" href="/admin/rechnungen/{{ r.id }}">öffnen</a> · <a class="aktion" href="/admin/rechnungen/{{ r.id }}/pdf">PDF</a></td>
  </tr>{% endfor %}
```

`core/beachhub_core/templates/rechnungen/detail.html`, in der Karte „Positionen“ die erste Tabelle ersetzen:
```html
  {% set auswaehlbar = r.art != "storno" and r.status != "storniert" %}
  <table>
    <tr>{% if auswaehlbar %}<th></th>{% endif %}<th>Text</th><th class="rechts">USt</th><th class="rechts">Menge</th><th class="rechts">Brutto</th></tr>
    {% for p in r.positionen %}<tr>
      {% if auswaehlbar %}<td>{% if p.korrigiert_durch_id is none %}<input type="checkbox" form="teilstorno" name="positionen" value="{{ p.id }}" aria-label="Position {{ p.reihenfolge }} auswählen">{% else %}<span class="badge">korrigiert</span>{% endif %}</td>{% endif %}
      <td>{{ p.text }}</td>
      <td class="rechts">{{ p.ust_satz|prozent }}</td>
      <td class="rechts">{{ p.menge }}</td>
      <td class="rechts">{{ p.brutto|euro }}</td>
    </tr>{% endfor %}
  </table>
  {% if auswaehlbar %}
  <form id="teilstorno" method="post" action="/admin/rechnungen/{{ r.id }}/teilstorno" class="zeile">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <label>Grund <input name="grund" placeholder="z. B. Halle gesperrt"></label>
    <button class="gefahr">Teil-Stornorechnung für ausgewählte Positionen</button>
  </form>
  <p class="small">Bereits bezahlte Beträge werden dem Kunden als Guthaben gutgeschrieben; bei offener Rechnung sinkt die Forderung.</p>
  {% endif %}
```

- [ ] **Step 5: Tests und Lint**

Run:
```bash
(cd core && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 6: Commit**

```bash
git add core
git commit -m "feat(core): Teil-Stornorechnung über ausgewählte Positionen, offener Betrag in der Liste"
```

---
## Task 6: Guthabenliste zum Saisonende

Unter Kunden → Guthabenliste stehen alle Kunden mit Guthaben; der Betreiber zahlt auf Wunsch aus und hakt die Auszahlung direkt in der Liste ab (A-ZAHL-4, A-ADM-3). Ab dem Stichtag `saisonende_guthabenliste` (Vorgabe 30.04., Abweichung B-5) bekommt er einmal im Jahr eine Mail mit Anzahl und Summe. Das System überweist nie selbst (A-ZAHL-6).

**Files:**
- Modify: `core/beachhub_core/services/konfiguration.py`, `services/guthaben.py`, `services/benachrichtigung.py`
- Modify: `core/beachhub_core/jobs.py`
- Modify: `core/beachhub_core/routes/kunden.py`, `navigation.py`
- Create: `core/beachhub_core/templates/kunden/guthabenliste.html`
- Create: `core/tests/test_ui_guthaben.py`
- Modify: `core/tests/test_ui_navigation.py`

**Interfaces:**
- Consumes: `konfiguration.TagMonat` (1a-I), `guthaben.buche`.
- Produces: Einstellung `saisonende_guthabenliste: TagMonat = "30.04."` (Gruppe „Zahlung und Rechnung“).
- Produces: `guthaben.guthabenliste(db) -> list[Kunde]` (Guthaben > 0, nicht anonymisiert, nach Name), `guthaben.SAISONENDE_MARKER = "guthabenliste_letzter"`, `guthaben.saisonende_faellig(db) -> tuple[int, Decimal] | None` (Anzahl und Summe, wenn der Stichtag in diesem Jahr erreicht und noch nicht gemeldet ist; setzt den Marker, committet nicht).
- Produces: `benachrichtigung.guthabenliste(anzahl: int, summe: Decimal)` (Betreff „[Beachhub] Guthabenliste zum Saisonende“), `jobs.saisonende_ausfuehren(db) -> None`, Scheduler-Job `saisonende` täglich 07:05.
- Produces: Routen `GET /admin/kunden/guthabenliste`, `POST /admin/kunden/guthabenliste/{kunde_id}/auszahlung` (Felder `betrag`, `notiz`); Navigation „Kunden“ → „Guthabenliste“.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_ui_guthaben.py`:
```python
from datetime import date
from decimal import Decimal

from beachhub_core import clock, jobs
from beachhub_core.models import GuthabenBuchung, Kunde
from beachhub_core.services import guthaben, konfiguration, kunden
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session


def _kunden(db: Session) -> tuple[Kunde, Kunde]:
    a = kunden.lege_an(db, name="Anna", email="anna@x.de")
    b = kunden.lege_an(db, name="Bea", email="bea@x.de")
    guthaben.buche(db, kunde=a, betrag=Decimal("30.00"), art="manuell")
    db.commit()
    return a, b


def test_guthabenliste_nur_mit_guthaben(db: Session) -> None:
    a, _ = _kunden(db)
    assert guthaben.guthabenliste(db) == [a]


def test_saisonende_einmal_im_jahr(db: Session) -> None:
    _kunden(db)
    clock.set_override(db, date(2028, 4, 29))
    assert guthaben.saisonende_faellig(db) is None
    clock.set_override(db, date(2028, 4, 30))
    assert guthaben.saisonende_faellig(db) == (1, Decimal("30.00"))
    db.commit()
    assert guthaben.saisonende_faellig(db) is None
    konfiguration.setze(db, "saisonende_guthabenliste", "31.03.")
    db.commit()
    clock.set_override(db, date(2029, 4, 1))
    assert guthaben.saisonende_faellig(db) == (1, Decimal("30.00"))


def test_saisonende_job_mailt_nach_commit(db: Session, mail_ausgang: list) -> None:
    _kunden(db)
    clock.set_override(db, date(2028, 5, 2))
    jobs.saisonende_ausfuehren(db)
    assert [m["betreff"] for m in mail_ausgang] == ["[Beachhub] Guthabenliste zum Saisonende"]
    assert "1 Kunden" in mail_ausgang[0]["text"] and "30,00 €" in mail_ausgang[0]["text"]
    jobs.saisonende_ausfuehren(db)
    assert len(mail_ausgang) == 1


def test_auszahlung_in_der_liste_abhaken(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    a, _ = _kunden(db)
    seite = c.get("/admin/kunden/guthabenliste")
    assert "Anna" in seite.text and "Bea" not in seite.text and "30,00 €" in seite.text
    r = c.post(
        f"/admin/kunden/guthabenliste/{a.id}/auszahlung",
        data={"csrf_token": c.csrf, "betrag": "30,00", "notiz": "überwiesen"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.expire_all()
    assert db.get(Kunde, a.id).guthaben == Decimal("0.00")
    g = db.scalars(select(GuthabenBuchung).where(GuthabenBuchung.art == "auszahlung")).one()
    assert g.betrag == Decimal("-30.00") and g.notiz == "überwiesen"
    assert "Niemand hat Guthaben" in c.get("/admin/kunden/guthabenliste").text


def test_auszahlung_ueber_guthaben_hinaus_meldet_fehler(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    a, _ = _kunden(db)
    seite = c.post(
        f"/admin/kunden/guthabenliste/{a.id}/auszahlung",
        data={"csrf_token": c.csrf, "betrag": "50,00", "notiz": ""},
    )
    assert "nicht gedeckt" in seite.text
    db.expire_all()
    assert db.get(Kunde, a.id).guthaben == Decimal("30.00")
```

`core/tests/test_ui_navigation.py`: `"/admin/kunden/guthabenliste"` in `SEITEN` ergänzen.

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_ui_guthaben.py tests/test_ui_navigation.py`
Expected: FAIL – `AttributeError: module 'beachhub_core.services.guthaben' has no attribute 'guthabenliste'`.

- [ ] **Step 3: Einstellung und Dienst**

`core/beachhub_core/services/konfiguration.py`, in `DEFAULTS` nach `"guthaben_auf_saisonrechnung"`: `"saisonende_guthabenliste": (TagMonat, TagMonat("30.04.")),`; in `BESCHREIBUNGEN` nach `"guthaben_auf_saisonrechnung"`:
```python
    "saisonende_guthabenliste": Beschreibung(
        "Zahlung und Rechnung",
        "Guthabenliste zum Saisonende",
        "Tag und Monat",
        "Ab diesem Tag bekommen Sie einmal im Jahr eine E-Mail mit allen Kunden, die noch "
        "Guthaben haben (Kunden → Guthabenliste). Wer es wünscht, bekommt es ausgezahlt.",
    ),
```
(`DEFAULTS` steht nach der Klasse `TagMonat`; die Reihenfolge in der Datei bleibt so.)

`core/beachhub_core/services/guthaben.py`:
- Importe: `from beachhub_core import clock`; `from beachhub_core.models import AppSetting, GuthabenBuchung, Kunde`; `from beachhub_core.services import audit, konfiguration`.
- Am Dateiende:
```python
SAISONENDE_MARKER = "guthabenliste_letzter"


def guthabenliste(db: Session) -> list[Kunde]:
    """Alle Kunden mit Guthaben – zum Saisonende zahlt der Betreiber auf Wunsch aus (A-ZAHL-4)."""
    return list(
        db.scalars(
            select(Kunde)
            .where(Kunde.guthaben > 0, Kunde.anonymisiert_am.is_(None))
            .order_by(Kunde.name)
        ).all()
    )


def saisonende_faellig(db: Session) -> tuple[int, Decimal] | None:
    """Einmal im Jahr ab dem Stichtag `saisonende_guthabenliste` (auch nachträglich, falls der Lauf
    am Stichtag ausfiel): Anzahl und Summe der Guthaben. Setzt den Marker, committet nicht."""
    heute = clock.today(db)
    stichtag = konfiguration.hole(db, "saisonende_guthabenliste").im_jahr(heute.year)
    marker = db.get(AppSetting, SAISONENDE_MARKER)
    if heute < stichtag or (marker is not None and marker.value == str(heute.year)):
        return None
    if marker is None:
        db.add(AppSetting(key=SAISONENDE_MARKER, value=str(heute.year)))
    else:
        marker.value = str(heute.year)
    db.flush()
    liste = guthabenliste(db)
    return len(liste), sum((k.guthaben for k in liste), Decimal("0.00"))
```

`core/beachhub_core/services/benachrichtigung.py`:
```python
def guthabenliste(anzahl: int, summe: Decimal) -> None:
    betreiber_alarm(
        "Guthabenliste zum Saisonende",
        f"Die Saison ist zu Ende. {anzahl} Kunden haben zusammen {euro(summe)} Guthaben.\n\n"
        "Die Liste steht in der Verwaltung unter Kunden → Guthabenliste. Wer es wünscht, bekommt "
        "sein Guthaben ausgezahlt; überweisen Sie selbst und haken Sie die Auszahlung dort ab. "
        "Alles andere wird mit der nächsten Saisonrechnung oder Buchung verrechnet.",
    )
```
(Import `from beachhub_core.templating import euro, templates` – `templates` wird schon importiert.)

`core/beachhub_core/jobs.py`:
- Import `guthaben` in die Service-Importe.
- Nach `mitgliedschaft_ausfuehren`:
```python
def saisonende_ausfuehren(db: Session) -> None:
    """Guthabenliste zum Saisonende melden (A-ZAHL-4) – erst committen, dann mailen."""
    lauf = guthaben.saisonende_faellig(db)
    db.commit()
    if lauf is not None:
        benachrichtigung.guthabenliste(*lauf)


def _job_saisonende() -> None:
    with SessionLocal() as db:
        try:
            saisonende_ausfuehren(db)
        except Exception:
            logger.exception("Guthabenliste zum Saisonende fehlgeschlagen")
```
- In `starte_scheduler` nach dem Job `mitgliedschaft`:
```python
    s.add_job(
        _job_saisonende, CronTrigger(hour=7, minute=5), id="saisonende", replace_existing=True
    )
```

- [ ] **Step 4: Seite**

`core/beachhub_core/routes/kunden.py` – **vor** `GET /kunden/{kunde_id}` (neben `antraege` und `abgleich`):
```python
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
    betrag: str = Form(...),
    notiz: str = Form(""),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """Hakt eine Auszahlung ab, die der Betreiber selbst überwiesen hat (A-ZAHL-6)."""
    zurueck = RedirectResponse("/admin/kunden/guthabenliste", status_code=303)
    k = db.get(Kunde, kunde_id)
    if k is None:
        return mit_flash(zurueck, "Kunde nicht gefunden", "fehler")
    try:
        wert = pflicht(t_betrag(betrag), "Betrag")
        if wert <= 0:
            raise ValueError("Betrag muss größer als 0 sein")
        guthaben.buche(
            db, kunde=k, betrag=-wert, art="auszahlung", notiz=notiz, admin_user_id=admin.id
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return mit_flash(zurueck, fehlertext(e, FEHLERTEXT), "fehler")
    return mit_flash(zurueck, f"Auszahlung an {k.name} abgehakt")
```
(Importe: `from decimal import Decimal, InvalidOperation`.)

`core/beachhub_core/templates/kunden/guthabenliste.html`:
```html
{% extends "base.html" %}{% block title %}Guthabenliste{% endblock %}
{% block content %}
<h1>Guthabenliste</h1>
<p class="hinweis">Alle Kunden mit Guthaben, zusammen {{ summe|euro }}. Zum Saisonende (Stichtag {{ stichtag }}, siehe Einstellungen) bekommen Sie eine Erinnerung. Wer es wünscht, bekommt sein Guthaben ausgezahlt: Überweisen Sie selbst und haken Sie die Auszahlung hier ab. Alles andere wird mit der nächsten Saisonrechnung oder Buchung verrechnet.</p>
<div class="karte">
<table>
  <thead><tr><th>Name</th><th>E-Mail</th><th class="rechts">Guthaben</th><th>Auszahlung abhaken</th></tr></thead>
  <tbody>
  {% for k in kunden %}<tr>
    <td><a href="/admin/kunden/{{ k.id }}">{{ k.name }}</a></td>
    <td>{{ k.email }}</td>
    <td class="rechts">{{ k.guthaben|euro }}</td>
    <td>
      <form method="post" action="/admin/kunden/guthabenliste/{{ k.id }}/auszahlung" class="zeile">
        <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
        <label>Betrag <input name="betrag" value="{{ k.guthaben|wert }}" required></label>
        <label>Notiz <input name="notiz" placeholder="überwiesen am …"></label>
        <button>Ausgezahlt</button>
      </form>
    </td>
  </tr>{% else %}
    <tr><td colspan="4" class="leer">Niemand hat Guthaben.</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endblock %}
```

`core/beachhub_core/navigation.py`, Bereich „Kunden“: `Punkt("Guthabenliste", "/admin/kunden/guthabenliste"),` als letzten Unterpunkt ergänzen.

- [ ] **Step 5: Tests und Lint**

Run:
```bash
(cd core && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 6: Commit**

```bash
git add core
git commit -m "feat(core): Guthabenliste zum Saisonende mit Auszahlung und jährlicher Erinnerung"
```

---
## Task 7: Betriebshandbuch, README, Spec-Abgleich und Gesamtprüfung

**Files:**
- Modify: `docs/betrieb/hauptsystem.md` (§ 4, § 5)
- Modify: `README.md` (Statuszeile)
- Modify: `docs/superpowers/specs/2026-09-05-beachhub-design.md` (A-STORNO-6, A-RECH-7, A-ZAHL-4, § 3.14, § 5)

**Interfaces:** keine neuen.

- [ ] **Step 1: Betriebshandbuch**

`docs/betrieb/hauptsystem.md`:
- In § 4 Punkt 8 ersetzen durch:
```markdown
8. Eine **Testrechnung** prüfen: Eine Buchung, die Sie selbst anlegen, bekommt sofort eine offene
   Rechnung; ein Testabo bekommt seine Saisonrechnung. PDF und Mail kontrollieren.
```
- In § 5 den Absatz, der mit „Der Monatslauf erzeugt bis zur Einführung der Saisonrechnung“ beginnt, den Absatz „Der Monatslauf kann bei Bedarf auch manuell angestoßen werden …“ und den Codeblock mit `beachhub-core monatslauf` ersetzen durch:
```markdown
**Rechnungen.** Es gibt keinen Monatslauf mehr. Jede Dauerbuchung bekommt bei ihrer Anlage genau
eine **Saisonrechnung** über alle Termine (Zahlungsziel `saison_zahlungsziel_tage`, Vorgabe 14
Tage); vorhandenes Guthaben wird dabei verrechnet, solange `guthaben_auf_saisonrechnung` an ist.
Den Zahlungseingang per Überweisung haken Sie auf der Rechnungsseite ab. Buchungen, die Sie selbst
anlegen, bekommen sofort eine offene Rechnung; Onlinebuchungen eine bezahlte.

**Korrekturen.** Wird eine berechnete Buchung kostenfrei storniert (innerhalb der Frist, als freie
Abo-Absage, per Kulanz, durch eine Sperre oder weil ein Abo endet), entsteht automatisch eine
Teil-Stornorechnung über ihre Position. War die Rechnung schon bezahlt, wird der Betrag Guthaben;
sonst sinkt der offene Betrag. Auf der Rechnungsseite können Sie zudem einzelne Positionen von Hand
korrigieren. Guthaben entsteht nie ohne einen solchen Beleg.

**Abo-Absagen.** Kunden sagen Abo-Termine im Portal ab. Bis zu `abo_freie_absagen` (Vorgabe 3)
Absagen je Abo innerhalb der Stornofrist sind kostenfrei; weitere Absagen geben den Platz frei, der
Termin bleibt aber berechnet.

**Guthabenliste.** Ab dem Stichtag `saisonende_guthabenliste` (Vorgabe 30.04.) kommt einmal im Jahr
um 07:05 Uhr eine Mail mit allen Guthaben. Unter Kunden → Guthabenliste haken Sie Auszahlungen ab,
die Sie selbst überwiesen haben.
```

- [ ] **Step 2: README**

`README.md`, in der Statuszeile `Stufe 1a-I (feste Kundengruppen mit Steuersatz, Mitgliedschaft mit Antrag, Abgleich und Klärungsliste, Rechnungskunden) umgesetzt, 1a-II (Saisonrechnung) und 1a-III (Gutscheine) folgen` ersetzen durch:
```markdown
Stufe 1a-I (feste Kundengruppen mit Steuersatz, Mitgliedschaft, Rechnungskunden) und 1a-II
(Saisonrechnung, freie Abo-Absagen, Korrekturbelege, Guthabenliste) umgesetzt, 1a-III (Gutscheine)
folgt
```

- [ ] **Step 3: Spec an die Umsetzung angleichen**

`docs/superpowers/specs/2026-09-05-beachhub-design.md`:
- A-STORNO-6: den kursiven Satz „*Dies korrigiert eine bestehende Lücke in `services/storno.py`, …*“ ersetzen durch: „Umgesetzt in `storno.gutschreiben_positionen`; jede Gutschrift verweist auf ihren Korrekturbeleg.“
- A-RECH-7: nach „ist sie noch offen, verringert sich die Forderung.“ einfügen: „Ist sie teilweise bezahlt (etwa durch verrechnetes Guthaben), sinkt zuerst die Forderung; nur der darüber hinausgehende Betrag wird Guthaben. Sind alle Positionen korrigiert, gilt die Rechnung als storniert.“
- A-ZAHL-4: `(Einstellung `saisonende_guthabenliste`, Vorgabe letzter Tag der Saison)` → `(Einstellung `saisonende_guthabenliste`, ein Stichtag, Vorgabe 30.04.; ab diesem Tag einmal im Jahr eine Mail an den Betreiber)`.
- § 3.14, Tabelle: nach der Zeile `guthaben_auf_saisonrechnung` einfügen:
  `| \`saisonende_guthabenliste\` | 30.04. | beliebiger Tag im Jahr | A-ZAHL-4 |`
- § 5, Zeile `storno`: am Ende `, korrektur_rechnung_id? (Korrekturbeleg, A-STORNO-6)` ergänzen; Zeile `rechnung_position`: am Ende `, korrigiert_durch_id? (Gegenposition der Stornorechnung)` ergänzen.

- [ ] **Step 4: Reste suchen**

Run:
```bash
grep -rn "_gutschrift\b\|monatslauf\|sammelrechnung\|erzeuge_sammel\|rechnung_tag_im_folgemonat\|MONATSLAUF" core shared portal e2e docs/betrieb README.md --include=*.py --include=*.html --include=*.md --include=*.txt
```
Expected: Treffer nur in `core/alembic/versions/` (alte Migrationen), in den beiden Tests `test_kein_monatslauf_mehr` und im Wort `storno_gutschrift`.

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
git commit -m "docs: Saisonrechnung, Korrekturbelege, Abo-Absagen und Guthabenliste"
```

Danach den Branch mit superpowers:finishing-a-development-branch abschließen: nach `main` mergen (`--no-ff`), pushen, Worktree und Branch entfernen.
