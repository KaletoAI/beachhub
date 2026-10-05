# Stufe 1a-III: Gutscheine und Freischaltcodes – Implementierungsplan

> Historischer Entwurf: Für die Ausführung abgelöst durch [den aktualisierten Plan vom 05.10.2026](2026-10-05-stufe-1a-III-gutscheine.md). Insbesondere gilt beim kostenfreien Wertgutschein-Storno Kundenguthaben gemäß bestätigter Spezifikation.


> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kunden kaufen über das Portal Gutscheine (eine Buchungseinheit oder einen Eurobetrag) mit Online-Zahlung und Kaufrechnung bzw. Kaufbeleg, lösen gekaufte Gutscheine und vom Betreiber ausgestellte Freischaltcodes direkt in der Buchung ein (auch mehrere je Buchung), bekommen sie bei kostenfreiem Storno zurück; der Betreiber stellt Freischaltcodes einzeln und als Serie aus, sperrt, verlängert, reaktiviert und nimmt Gutscheine zurück.

**Architecture:** Zwei neue Tabellen `gutschein` und `gutschein_einloesung` nach Spec § 5 (dazu `gutschein_fehlversuch` für das Rate-Limit). Die Fachlogik liegt in `services/gutscheine.py` (Codes, Prüfung, Einlösung in der Buchungstransaktion, Rückgängigmachen, Freischaltcodes, Verfall, Sperre, Rückgabe, Rechnung einer Buchung mit Gutscheinanteil) und `services/gutschein_kauf.py` (Kauf über den Kanal, Zahlungseingang, Kaufrechnung bzw. Kaufbeleg). Die Einlösung hängt sich in `online_buchung.anfragen` ein: Buchung und Gutscheine entstehen im selben Savepoint, die Gutscheinzeilen werden nach `id` sortiert gesperrt (A-GUT-3). Storno und Kulanz rufen `gutscheine.storno_rueckgaengig`. Kaufbelege für Mehrzweckgutscheine bekommen einen eigenen Nummernkreis. Das Portal bekommt nur den Vertrag (`shared`) und Lesestand-Felder; seine Oberfläche folgt mit Stufe 2.

**Tech Stack:** Python 3.12, FastAPI 0.115.6, SQLAlchemy 2.0.36, Alembic 1.14, PostgreSQL 16, Jinja2, pydantic 2.10, APScheduler 3.11, WeasyPrint 63.1, pytest 8.3, ruff 0.8.4, mypy 1.13.

**Spec:** `docs/superpowers/specs/2026-09-05-beachhub-design.md` – § 3.9a (A-GUT-1 bis -9), § 3.7 (A-STORNO-7), § 3.8 (A-ZAHL-1 Zahlungsart `gutschein`), § 3.2 (A-KUND-7 Gutscheinkauf für Rechnungskunden), § 3.12 (A-MAIL-2), § 3.13 (A-ADM-8), § 3.14 (Gutschein-Einstellungen), § 5 (Tabellen `gutschein`, `gutschein_einloesung`, `zahlung`, `rechnung`), § 8.1 (`buchung_anfragen` mit `gutschein_codes`, `gutschein_kaufen`), § 9 (Abläufe „Einzelbuchung mit Gutschein“, „Gutscheinkauf“).

## Stufe 1a in drei Plänen

Stufe 1a ist in drei Pläne geteilt. Jeder wird für sich umgesetzt, geprüft und nach `main` gemergt:

1. **1a-I:** Kundengruppen, Steuersatz je Buchung und Rechnungsposition, Betreiberbuchung mit Event-Steuersatz, Mitgliedschaft mit Antrag, Klärungsliste, Jahresabgleich und Erinnerung, Rechnungskunden im Kanal.
2. **1a-II:** Saisonrechnung statt Monatslauf, Abo nur für Rechnungskunden und Mitglieder, freie Abo-Absagen, Teil-Stornorechnung mit Korrekturbeleg, Guthabenverrechnung auf der Saisonrechnung, Guthabenliste zum Saisonende.
3. **1a-III (dieser Plan):** Gutscheine und Freischaltcodes (Kauf, Einlösung in der Buchung, Storno, Verfall, Rückgabe, Admin-Seite).

**Voraussetzung:** Die Pläne 1a-I und 1a-II sind umgesetzt und gemergt. Dieser Plan benutzt aus 1a-II nur: Migration `0014`, `rechnungen.korrigiere(db, positionen, *, grund, quelle) -> Rechnung`, `rechnungen.storniere(db, rechnung, *, admin_user_id, grund) -> Rechnung`, `storno.gutschreiben(...)` (über `storno.storniere`/`storno.kulanz`), `RechnungPosition.korrigiert_durch_id`, `Rechnung.korrigiert_rechnung_id`. Wo dieser Plan in Funktionen von 1a-II eingreift (`storno.storniere`, `storno.kulanz`, `benachrichtigung.storno`, `online_buchung.storniere_fuer_kunde`), beschreibt er die Einfügestelle über ihr Verhalten. Weicht der Code nach 1a-II davon ab, gilt das beschriebene Verhalten. Hat 1a-II mehr als die Migration `0014` angelegt, zeigt `down_revision` von `0015` auf deren letzte.

Die Portal-Oberfläche für Gutscheinkauf, Gutscheinliste und Codes beim Buchen gehört zum Rest von Stufe 2.

## Global Constraints

- Python **3.12**; jedes Paket mit eigenem `pyproject.toml`, Abhängigkeiten mit exakten Versionen (`==`). Dieser Plan fügt **keine** neue Abhängigkeit hinzu.
- Geldbeträge und Steuersätze durchgehend `decimal.Decimal` (`DECIMAL(10, 2)` bzw. `DECIMAL(5, 2)`), nie `float` (N-7). Gerundet wird `ROUND_HALF_UP` auf Cent, **je Position** (A-RECH-8).
- Zeitstempel UTC-aware speichern, Anzeige in `Europe/Berlin` (N-7). Fachliche Tage („heute“, Leistungsdatum) immer über `clock.today(db)` bzw. `beachhub_shared.zeit.lokales_datum`.
- Oberfläche des Hauptsystems auf Deutsch, siezt, serverseitig gerendert, **kein Frontend-Build**, keine Inline-Skripte.
- Jede Änderung an Kunden, Buchungen, Rechnungen, Kundengruppen und Einstellungen erzeugt einen Audit-Eintrag (N-6) über `services/audit.py`. Dieser Plan protokolliert zusätzlich jede Änderung an Gutscheinen (`objekt_typ="gutschein"`).
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

- **A-1 Gutscheinpreise ohne Betrag heißen `0,00`.** `gutschein_preis_mitglied` und `gutschein_preis_nichtmitglied` sind Dezimal-Einstellungen mit Vorgabe `0.00`; `0` bedeutet „kein Verkauf“ (Spec: „leer“). Ein leeres Feld lässt sich in den Einstellungen nicht speichern, weil „leer“ dort „Vorgabe“ heißt.
- **A-2 Beträge im Modell `wert`:** neue Einstellung `gutschein_betraege` (Vorgabe `25,00; 50,00; 100,00`), Trennzeichen Semikolon. Die Spec nennt nur „eine konfigurierbare Liste“.
- **A-3 Gutschein-Zustände `offen` und `abgebrochen`.** Ein gekaufter Gutschein entsteht beim Kaufwunsch mit Status `offen` (Code noch nicht verschickt) und wird erst mit dem Zahlungseingang `aktiv`; verfällt die Zahlungsfrist, wird er `abgebrochen`. So trägt die Zahlung über `gutschein.zahlung_id` alles, was der Kauf braucht. Gültig ist er ab Zahlungseingang `gutschein_gueltig_tage` Tage.
- **A-4 Kein Guthaben beim Gutscheinkauf.** Guthaben wird beim Kauf nicht verrechnet (die Spec schweigt); der Kaufpreis geht vollständig über den Zahlungsdienst. Das hält Kaufrechnung und Zahlung deckungsgleich.
- **A-5 Storno im Modell `wert` bucht den Wert auf den Gutschein zurück** statt ihn als Guthaben gutzuschreiben (Spec A-GUT-6 sagt Guthaben). Grund: Bei `beim_kauf` gab es zur Einlösung keine Rechnung, ein Korrekturbeleg hätte keinen Bezug; Guthaben würde Gutscheinwert in Geldwert wandeln. Beide Modelle verhalten sich damit gleich: Kostenfreies Storno macht die Einlösung rückgängig.
- **A-6 Bemessungsgrundlage beim Mehrzweckgutschein (`bei_einloesung`) im Modell `einheit` ist der Kaufpreis,** nicht der Tarifwert der gedeckten Slots: Das ist die tatsächlich gezahlte Gegenleistung. Im Modell `wert` ist es der eingelöste Betrag. Die Einlösungsrechnung trägt den Satz des Einlösenden (Satz der Buchung).
- **A-7 Rate-Limit nur je Konto.** Das Hauptsystem kennt die IP des Kunden nicht; das Limit je IP gehört ins Portal (Stufe 2). Im Hauptsystem: höchstens 10 abgelehnte Codes je Kunde in 60 Minuten (`gutschein_fehlversuch`), danach `abgelehnt/zu_viele_versuche`.
- **A-8 Freischaltcodes decken immer eine Buchungseinheit** (A-GUT-2), auch wenn `gutschein_modell = wert` eingestellt ist; sie tragen `steuer = "keine"`. Ihr Zeitraum besteht aus zwei Tagen (`zeitraum_von`, `zeitraum_bis`), der Termin muss dazwischen liegen.
- **A-9 Gültigkeit wird am Tag der Einlösung geprüft,** nicht am Tag des Termins: Ein am 30.04. gültiger Code darf einen Termin am 05.05. decken.
- **A-10 Deckung im Modell `einheit` nur ganzer Slots.** Ein Gutschein deckt die Slots, die innerhalb seiner Minuten vom Beginn an vollständig liegen. Deckt ein Gutschein keinen einzigen Slot (Slot länger als `gutschein_minuten`), lautet die Ablehnung `zu_viele_gutscheine`. Bei gemischten Gutscheinen decken Einheiten (und Freischaltcodes) zuerst, Wertgutscheine danach den Rest.
- **A-11 Obergrenze je Buchung:** `gutscheine_je_buchung` ist `volle_einheiten` oder eine Zahl `N`; mit Zahl gilt `min(N, max(1, ⌊Dauer/gutschein_minuten⌋))` für Einheiten. Wertgutscheine zählen bei `volle_einheiten` nicht mit, bei einer Zahl schon.
- **A-12 Rückgabe nur unverbraucht.** Ein Gutschein lässt sich nur zurückgeben, solange keine Einlösung besteht (Modell `wert`: Restwert gleich Nennwert). Beim Kaufbeleg (`bei_einloesung`) wird der Beleg auf `storniert` gesetzt, ohne Stornorechnung (er weist keine Steuer aus).
- **A-13 Kaufbelege haben einen eigenen Nummernkreis** mit Präfix `GB-` (`GB-2027-00001`); `nummernkreis` bekommt dafür die Spalte `kreis`, `rechnung.nummer` wird auf 20 Zeichen verbreitert.
- **A-14 Mail „Gutschein wieder gültig“ ist eine eigene Mail** nach der Stornomail, damit die Stornovorlage (1a-II) unberührt bleibt.
- **A-15 Korrekturbeleg einer Mehrzweck-Einlösung geht nicht automatisch hinaus.** `gutscheine.storno_rueckgaengig` legt ihn an (Task 5); er steht in der Rechnungsliste, das PDF erzeugt der Betreiber dort bei Bedarf. Betrifft nur die Steuervariante `bei_einloesung`, die nicht die Vorgabe ist (Steuerberater bestätigt `beim_kauf`, Ⓞ-8). Soll er wie die Belege aus 1a-II mit PDF und Mail an den Kunden gehen, `benachrichtigung.belege_versenden` (1a-II) im Nachlauf des Stornos mit den Belegen aus `storno_rueckgaengig` aufrufen.

## Review Focus

1. **Zwei Kunden lösen denselben Code fast gleichzeitig ein** (oder ein Kunde schickt ihn zweimal in einer Anfrage) – erwartet: genau eine Einlösung; die zweite Buchung wird mit `gutschein_ungueltig` abgelehnt und hinterlässt weder Buchung noch Reservierung. *Tests: Task 4 `test_code_nur_einmal_einloesbar`, `test_doppelter_code_in_einer_anfrage_zaehlt_einmal`.*
2. **Mehr Gutscheine als volle Einheiten oder ein Gutschein, der nichts deckt** (3 h mit zwei Gutscheinen) – erwartet: `abgelehnt/zu_viele_gutscheine`, kein Gutschein verbraucht, keine Buchung. *Test: Task 4 `test_zu_viele_gutscheine_lehnt_alles_ab`.*
3. **Kostenfreies Storno einer teils mit Gutschein, teils online bezahlten Buchung** – erwartet: Gutschein wieder `aktiv` mit altem Ablaufdatum und dem Einlösenden als Inhaber, Guthaben nur für den online bezahlten Aufpreis (über die Teil-Stornorechnung), Mail „Gutschein wieder gültig“. Nach der Frist bleibt der Gutschein verbraucht. *Tests: Task 5 `test_storno_kostenfrei_gibt_gutschein_zurueck`, `test_storno_nach_frist_gutschein_bleibt_verbraucht`.*
4. **Zahlung für einen Gutschein trifft nach Ablauf der Zahlungsfrist oder doppelt ein** – erwartet: kein zweiter Gutschein, keine zweite Rechnung; nach Abbruch wird der Betrag Guthaben und der Betreiber bekommt eine Mail. *Tests: Task 6 `test_doppelte_rueckmeldung_kauf_ist_folgenlos`, `test_zahlung_nach_abbruch_wird_guthaben`.*
5. **Mitgliedsgutschein bei einem Nicht-Mitglied oder geratene Codes** – erwartet: `gutschein_nur_mitglieder` bzw. `gutschein_ungueltig`; nach zehn Fehlversuchen in einer Stunde `zu_viele_versuche`, ohne dass weitere Codes geprüft werden. *Tests: Task 4 `test_mitgliedsgutschein_nur_fuer_mitglieder`, `test_rate_limit_nach_zehn_fehlversuchen`.*

---

## Dateistruktur

```
shared/beachhub_shared/
  kanal.py                 ÄND  BuchungAnfragen.gutschein_codes; Anfragetyp gutschein_kaufen (GutscheinKaufen);
                                Antwort.gutschein_verrechnet, Antwort.gutschein_wieder_gueltig
  lesestand.py             ÄND  KontoGutschein, KontoInhalt.gutscheine/gutschein_kauf,
                                GutscheinAngebot, TarifeInhalt.gutschein
shared/tests/test_kanal.py, test_lesestand_schema.py  ÄND

core/beachhub_core/
  models/gutscheine.py     NEU  Gutschein, GutscheinEinloesung, GutscheinFehlversuch
  models/rechnungen.py     ÄND  Nummernkreis.kreis (PK kreis+jahr), Rechnung.nummer 20 Zeichen
  models/__init__.py       ÄND  Export
  services/gutscheine.py   NEU  Codes, Prüfung, Einlösung, Rückgängig, Rechnung je Buchung, Freischaltcodes,
                                Sperre, Gültigkeit, Verfall, Rückgabe, CSV
  services/gutschein_kauf.py NEU Kauf über den Kanal, Zahlungseingang, Abschluss, Abbruch
  services/konfiguration.py ÄND Obergrenze, Betragsliste, AUSWAHL, Gruppe „Gutscheine“, Schlüssel
  services/tarife.py       ÄND  preise_je_slot()
  services/rechnungen.py   ÄND  Nummernkreis je Art (Kaufbeleg GB-)
  services/online_buchung.py ÄND Einlösung in anfragen, Rechnung über gutscheine, Kaufzahlungen, Verfall
  services/anfragen.py     ÄND  buchung_anfragen mit Codes, gutschein_kaufen
  services/storno.py       ÄND  storno_rueckgaengig bei kostenfreiem Storno und Kulanz
  services/lesestand.py    ÄND  Konto-Gutscheine, Angebot im Tarif-Dokument
  services/benachrichtigung.py ÄND Gutschein gekauft/geschenkt/wieder gültig
  jobs.py                  ÄND  Verfall offener Käufe (minütlich), Verfall der Gutscheine (nachts)
  routes/gutscheine.py     NEU  Admin-Seiten A-ADM-8
  routes/belegung.py       ÄND  Mail nach Kulanz
  main.py, navigation.py   ÄND  Router, Bereich „Gutscheine“
  templating.py            ÄND  Filter `code`
  templates/gutscheine/liste.html, detail.html, freicodes.html  NEU
  templates/rechnung_pdf.html, stammdaten/konfiguration.html, mail/buchung_bestaetigt.txt  ÄND
  templates/mail/gutschein_gekauft.txt, gutschein_geschenkt.txt, gutschein_wieder_gueltig.txt  NEU
alembic/versions/0015_gutscheine.py  NEU
core/tests/hilfen_gutschein.py, test_gutscheine.py, test_gutschein_einloesung.py,
           test_gutschein_kauf.py, test_ui_gutscheine.py  NEU
core/tests/conftest.py, test_konfiguration.py, test_ui_stammdaten.py, test_ui_navigation.py,
           test_lesestand.py, test_tarife.py, test_rechnungen.py, test_jobs.py  ÄND
docs/betrieb/hauptsystem.md, README.md, docs/superpowers/specs/2026-09-05-beachhub-design.md  ÄND
```

---
## Task 1: Einstellungen für Gutscheine

Alle Gutschein-Entscheidungen des Betreibers werden Einstellungen (§ 3.14): Modell, Dauer einer Einheit, Obergrenze je Buchung, Preise je Gruppe, Beträge für Wertgutscheine, Einlösbarkeit von Mitgliedsgutscheinen, Steuervariante, Gültigkeiten, Gutscheinkauf für Rechnungskunden und der (noch wirkungslose) Gastkauf. Dafür bekommt die Einstellungsseite feste Auswahllisten und zwei geprüfte Werttypen.

**Files:**
- Modify: `core/beachhub_core/services/konfiguration.py`
- Modify: `core/beachhub_core/routes/stammdaten.py` (`_einstellungen`)
- Modify: `core/beachhub_core/templates/stammdaten/konfiguration.html`
- Test: `core/tests/test_konfiguration.py`, `core/tests/test_ui_stammdaten.py`

**Interfaces:**
- Consumes: `konfiguration.TagMonat`, `gruppiert`, `setze` ohne Leeränderung, `lesestand.markiere_rechnungskunden` (1a-I).
- Produces: `konfiguration.Obergrenze(str)` – `"volle_einheiten"` oder positive ganze Zahl; Eigenschaft `zahl -> int | None` (`None` bei `volle_einheiten`).
- Produces: `konfiguration.Betragsliste(str)` – Beträge durch Semikolon, normalisiert `"25,00; 50,00; 100,00"` (aufsteigend, ohne Dubletten); Eigenschaft `betraege -> list[Decimal]`.
- Produces: `konfiguration.AUSWAHL: dict[str, dict[str, str]]` (Wert → Anzeigetext) für `gutschein_modell` (`einheit`, `wert`) und `gutschein_steuer` (`beim_kauf`, `bei_einloesung`); `setze` lehnt andere Werte mit `ValueError("Bitte einen der vorgesehenen Werte wählen")` ab.
- Produces: `konfiguration.GUTSCHEIN_ANGEBOT: tuple[str, ...]` – Schlüssel, deren Änderung das Tarif-Dokument markiert.
- Produces: `gruppiert(...)` liefert als viertes Element die Darstellungsart `"bool" | "auswahl" | "text"` (bisher `bool`).
- Produces: Einstellungen (Gruppe „Gutscheine“): `gutschein_modell: str = "einheit"`, `gutschein_minuten: int = 120`, `gutscheine_je_buchung: Obergrenze = "volle_einheiten"`, `gutschein_preis_mitglied: Decimal = 0.00`, `gutschein_preis_nichtmitglied: Decimal = 0.00`, `gutschein_betraege: Betragsliste = "25,00; 50,00; 100,00"`, `mitgliedsgutschein_nur_mitglieder: bool = True`, `gutschein_steuer: str = "beim_kauf"`, `gutschein_gueltig_tage: int = 730`, `freicode_gueltig_tage: int = 90`, `rechnungskunden_gutscheinkauf: bool = True`, `gutschein_gastkauf: bool = False`.

- [ ] **Step 1: Failing Tests schreiben**

In `core/tests/test_konfiguration.py` (Importe: `LesestandVersion` aus `beachhub_core.models`, `from beachhub_core.services import konfiguration, kunden, lesestand`):
```python
@pytest.mark.parametrize(
    "roh,erwartet,zahl",
    [("volle_einheiten", "volle_einheiten", None), (" Volle Einheiten ", "volle_einheiten", None), ("2", "2", 2)],
)
def test_obergrenze(roh: str, erwartet: str, zahl: int | None) -> None:
    wert = konfiguration.Obergrenze(roh)
    assert wert == erwartet and wert.zahl == zahl


@pytest.mark.parametrize("roh", ["0", "-1", "zwei", ""])
def test_obergrenze_lehnt_ab(roh: str) -> None:
    with pytest.raises(ValueError):
        konfiguration.Obergrenze(roh)


def test_betragsliste() -> None:
    liste = konfiguration.Betragsliste("50; 25,5;100; 25,50")
    assert liste == "25,50; 50,00; 100,00"
    assert liste.betraege == [Decimal("25.50"), Decimal("50.00"), Decimal("100.00")]
    for falsch in ("", "abc", "10; -5", "0"):
        with pytest.raises(ValueError):
            konfiguration.Betragsliste(falsch)


def test_gutschein_vorgaben(db: Session) -> None:
    assert konfiguration.hole(db, "gutschein_modell") == "einheit"
    assert konfiguration.hole(db, "gutschein_minuten") == 120
    assert konfiguration.hole(db, "gutscheine_je_buchung").zahl is None
    assert konfiguration.hole(db, "gutschein_preis_mitglied") == Decimal("0.00")
    assert konfiguration.hole(db, "gutschein_betraege").betraege[0] == Decimal("25.00")
    assert konfiguration.hole(db, "gutschein_steuer") == "beim_kauf"
    assert konfiguration.hole(db, "rechnungskunden_gutscheinkauf") is True
    assert konfiguration.BESCHREIBUNGEN["gutschein_modell"].gruppe == "Gutscheine"


def test_auswahl_nur_vorgesehene_werte(db: Session) -> None:
    konfiguration.setze(db, "gutschein_modell", "wert")
    assert konfiguration.hole(db, "gutschein_modell") == "wert"
    with pytest.raises(ValueError, match="vorgesehenen Werte"):
        konfiguration.setze(db, "gutschein_steuer", "nie")


def test_angebot_markiert_tarife_und_rechnungskunden(db: Session) -> None:
    k = kunden.lege_an(db, name="TSV", email="tsv@x.de", rechnungskunde=True)
    db.commit()
    lesestand.markiere_geaendert(db, "tarife", f"konto:{k.id}")
    for name in ("tarife", f"konto:{k.id}"):
        db.get(LesestandVersion, name).geaendert = False
    db.commit()
    konfiguration.setze(db, "gutschein_preis_mitglied", "26")
    konfiguration.setze(db, "rechnungskunden_gutscheinkauf", "nein")
    db.commit()
    assert db.get(LesestandVersion, "tarife").geaendert
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert
```

In `core/tests/test_ui_stammdaten.py`:
```python
def test_einstellungen_auswahl_fuer_gutscheinmodell(eingeloggt: TestClient, db: Session) -> None:
    c = eingeloggt
    text = c.get("/admin/konfiguration").text
    assert '<select name="gutschein_modell">' in text and "Eurobetrag mit Restwert" in text
    r = c.post(
        "/admin/konfiguration",
        data={"csrf_token": c.csrf, "gutschein_modell": "wert", "gutschein_steuer": "beim_kauf"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert konfiguration.hole(db, "gutschein_modell") == "wert"
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_konfiguration.py tests/test_ui_stammdaten.py`
Expected: FAIL – `AttributeError: module 'beachhub_core.services.konfiguration' has no attribute 'Obergrenze'`.

- [ ] **Step 3: Werttypen, Auswahl und Schlüssel**

`core/beachhub_core/services/konfiguration.py`:
- Nach der Klasse `TagMonat`:
```python
class Obergrenze(str):
    """Höchstzahl Gutscheine je Buchung (A-GUT-1): "volle_einheiten" oder eine Zahl ab 1."""

    __slots__ = ()

    def __new__(cls, roh: str) -> "Obergrenze":
        wert = roh.strip().lower().replace(" ", "_")
        if wert == "volle_einheiten":
            return super().__new__(cls, wert)
        try:
            zahl = int(wert)
        except ValueError:
            zahl = 0
        if zahl < 1:
            raise ValueError("Bitte volle_einheiten oder eine ganze Zahl ab 1 angeben")
        return super().__new__(cls, str(zahl))

    @property
    def zahl(self) -> int | None:
        return None if self == "volle_einheiten" else int(self)


class Betragsliste(str):
    """Wählbare Beträge für Wertgutscheine, getrennt durch Semikolon (etwa "25; 50; 100")."""

    __slots__ = ()

    def __new__(cls, roh: str) -> "Betragsliste":
        betraege: set[Decimal] = set()
        for teil in (t.strip() for t in roh.split(";")):
            if not teil:
                continue
            text = teil.replace(".", "").replace(",", ".") if "," in teil else teil
            try:
                betrag = Decimal(text).quantize(Decimal("0.01"))
            except InvalidOperation as e:
                raise ValueError(f"Betrag ungültig: {teil}") from e
            if not betrag.is_finite() or betrag <= 0:
                raise ValueError(f"Betrag ungültig: {teil}")
            betraege.add(betrag)
        if not betraege:
            raise ValueError("Bitte mindestens einen Betrag angeben, getrennt durch Semikolon")
        return super().__new__(
            cls, "; ".join(f"{b:.2f}".replace(".", ",") for b in sorted(betraege))
        )

    @property
    def betraege(self) -> list[Decimal]:
        return [Decimal(t.replace(",", ".")) for t in self.split("; ")]
```
- In `DEFAULTS` am Ende:
```python
    "gutschein_modell": (str, "einheit"),
    "gutschein_minuten": (int, 120),
    "gutscheine_je_buchung": (Obergrenze, Obergrenze("volle_einheiten")),
    "gutschein_preis_mitglied": (Decimal, Decimal("0.00")),
    "gutschein_preis_nichtmitglied": (Decimal, Decimal("0.00")),
    "gutschein_betraege": (Betragsliste, Betragsliste("25; 50; 100")),
    "mitgliedsgutschein_nur_mitglieder": (bool, True),
    "gutschein_steuer": (str, "beim_kauf"),
    "gutschein_gueltig_tage": (int, 730),
    "freicode_gueltig_tage": (int, 90),
    "rechnungskunden_gutscheinkauf": (bool, True),
    "gutschein_gastkauf": (bool, False),
```
- `_TYP_NAME` ergänzen: `Obergrenze: "obergrenze", Betragsliste: "betraege"`.
- In `BESCHREIBUNGEN` am Ende:
```python
    "gutschein_modell": Beschreibung(
        "Gutscheine",
        "Gutscheinmodell",
        "",
        "Was ein gekaufter Gutschein deckt. Gilt für Gutscheine, die danach verkauft werden.",
    ),
    "gutschein_minuten": Beschreibung(
        "Gutscheine",
        "Dauer einer Buchungseinheit",
        "Minuten",
        "So viel Spielzeit ab Beginn der Buchung deckt ein Gutschein oder Freischaltcode.",
    ),
    "gutscheine_je_buchung": Beschreibung(
        "Gutscheine",
        "Gutscheine je Buchung",
        "",
        "volle_einheiten: einer je volle Buchungseinheit (3 Stunden → einer, 4 Stunden → zwei). "
        "Eine Zahl begrenzt zusätzlich; 1 heißt höchstens ein Gutschein je Buchung.",
    ),
    "gutschein_preis_mitglied": Beschreibung(
        "Gutscheine",
        "Gutscheinpreis für Mitglieder",
        "Euro",
        "Preis eines Gutscheins über eine Buchungseinheit für Mitglieder. 0 heißt: kein Verkauf.",
    ),
    "gutschein_preis_nichtmitglied": Beschreibung(
        "Gutscheine",
        "Gutscheinpreis für Nicht-Mitglieder",
        "Euro",
        "Preis eines Gutscheins über eine Buchungseinheit für alle anderen. 0 heißt: kein "
        "Verkauf.",
    ),
    "gutschein_betraege": Beschreibung(
        "Gutscheine",
        "Beträge für Wertgutscheine",
        "Euro, durch Semikolon getrennt",
        "Nur im Modell „Eurobetrag“: Zwischen diesen Beträgen wählt der Käufer.",
    ),
    "mitgliedsgutschein_nur_mitglieder": Beschreibung(
        "Gutscheine",
        "Mitgliedsgutscheine nur für Mitglieder",
        "",
        "Ja: Ein zum Mitgliedspreis gekaufter Gutschein ist nur von Mitgliedern einlösbar.",
    ),
    "gutschein_steuer": Beschreibung(
        "Gutscheine",
        "Umsatzsteuer auf Gutscheine",
        "",
        "Beim Kauf: Rechnung mit dem Satz des Käufers, die Einlösung ist steuerfrei (vom "
        "Steuerberater bestätigt). Bei der Einlösung: Kaufbeleg ohne Steuer, die Rechnung entsteht "
        "beim Einlösen.",
    ),
    "gutschein_gueltig_tage": Beschreibung(
        "Gutscheine",
        "Gültigkeit gekaufter Gutscheine",
        "Tage",
        "Ab Zahlungseingang. Abgelaufene Gutscheine lassen sich in der Verwaltung wieder "
        "aktivieren.",
    ),
    "freicode_gueltig_tage": Beschreibung(
        "Gutscheine",
        "Gültigkeit von Freischaltcodes",
        "Tage",
        "Vorbelegung beim Ausstellen; je Code änderbar.",
    ),
    "rechnungskunden_gutscheinkauf": Beschreibung(
        "Gutscheine",
        "Rechnungskunden kaufen Gutscheine",
        "",
        "Ja: Rechnungskunden kaufen im Portal Gutscheine und zahlen sie online.",
    ),
    "gutschein_gastkauf": Beschreibung(
        "Gutscheine",
        "Gutscheinkauf ohne Konto",
        "",
        "Für eine spätere Stufe vorgesehen; die Einstellung hat derzeit keine Wirkung.",
    ),
```
- `GRUPPEN` am Ende um `"Gutscheine"` ergänzen.
- Neu unter `HALLEN_KONFIG`:
```python
# Werte, die im Tarif-Dokument als Gutschein-Angebot ans Portal gehen.
GUTSCHEIN_ANGEBOT: tuple[str, ...] = (
    "gutschein_modell",
    "gutschein_minuten",
    "gutschein_preis_mitglied",
    "gutschein_preis_nichtmitglied",
    "gutschein_betraege",
    "gutschein_gueltig_tage",
)

# Einstellungen mit fester Auswahl: gespeicherter Wert → Anzeige in der Verwaltung.
AUSWAHL: dict[str, dict[str, str]] = {
    "gutschein_modell": {"einheit": "Buchungseinheit", "wert": "Eurobetrag mit Restwert"},
    "gutschein_steuer": {
        "beim_kauf": "beim Kauf (Einzweckgutschein)",
        "bei_einloesung": "bei der Einlösung (Mehrzweckgutschein)",
    },
}
```
- `gruppiert` ersetzen:
```python
def _darstellung(schluessel: str) -> str:
    if DEFAULTS[schluessel][0] is bool:
        return "bool"
    return "auswahl" if schluessel in AUSWAHL else "text"


def gruppiert(
    werte: dict[str, Any],
) -> list[tuple[str, list[tuple[str, Any, Beschreibung, str]]]]:
    """Ordnet die Werte den Gruppen zu, in der Reihenfolge von GRUPPEN. Das vierte Element ist die
    Darstellung: "bool" (ja/nein), "auswahl" (feste Werte aus AUSWAHL) oder "text"."""
    return [
        (
            gruppe,
            [
                (schluessel, werte[schluessel], BESCHREIBUNGEN[schluessel], _darstellung(schluessel))
                for schluessel in DEFAULTS
                if schluessel in werte and BESCHREIBUNGEN[schluessel].gruppe == gruppe
            ],
        )
        for gruppe in GRUPPEN
    ]
```
- In `setze` direkt nach `neu = str(_parse(typ, str(wert)))`:
```python
    if schluessel in AUSWAHL and neu not in AUSWAHL[schluessel]:
        raise ValueError("Bitte einen der vorgesehenen Werte wählen")
```
- Am Ende von `setze` die Bedingung `if schluessel == "rechnungskunden_online_buchen":` ersetzen durch `if schluessel in ("rechnungskunden_online_buchen", "rechnungskunden_gutscheinkauf"):` (Kommentar: „Beide Werte stehen im Konto-Dokument jedes Rechnungskunden.“) und darunter ergänzen:
```python
    if schluessel in GUTSCHEIN_ANGEBOT:
        from beachhub_core.services import lesestand

        lesestand.markiere_geaendert(db, "tarife")
```

`core/beachhub_core/routes/stammdaten.py`, in `_einstellungen` an `render(...)` `auswahl=konfiguration.AUSWAHL,` übergeben.

`core/beachhub_core/templates/stammdaten/konfiguration.html`, die Schleife über die Einträge ersetzen:
```html
    {% for schluessel, wert, b, art in eintraege %}
      {% if art == "bool" %}
      <label><span class="bezeichnung">{{ b.name }}</span><select name="{{ schluessel }}"><option value="ja" {% if wert %}selected{% endif %}>ja</option><option value="nein" {% if not wert %}selected{% endif %}>nein</option></select><span class="hinweis">Vorgabe: {{ "ja" if defaults[schluessel][1] else "nein" }}. {{ b.hilfe }}</span></label>
      {% elif art == "auswahl" %}
      <label><span class="bezeichnung">{{ b.name }}</span><select name="{{ schluessel }}">{% for w, text in auswahl[schluessel].items() %}<option value="{{ w }}" {% if w == wert %}selected{% endif %}>{{ text }}</option>{% endfor %}</select><span class="hinweis">Vorgabe: {{ auswahl[schluessel][defaults[schluessel][1]] }}. {{ b.hilfe }}</span></label>
      {% else %}
      <label><span class="bezeichnung">{{ b.name }}{% if b.einheit %} <span class="einheit">({{ b.einheit }})</span>{% endif %}</span><input name="{{ schluessel }}" value="{{ wert|wert }}" placeholder="{{ defaults[schluessel][1]|wert }}"><span class="hinweis">{{ b.hilfe }}</span></label>
      {% endif %}
    {% endfor %}
```

- [ ] **Step 4: Tests und Lint**

Run:
```bash
(cd core && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 5: Commit**

```bash
git add core
git commit -m "feat(core): Einstellungen für Gutscheine mit Auswahllisten, Obergrenze und Betragsliste"
```

---
## Task 2: Datenmodell, Migration 0015, Nummernkreis für Kaufbelege, Codes

Die Tabellen `gutschein` und `gutschein_einloesung` entstehen nach Spec § 5, dazu `gutschein_fehlversuch` für das Rate-Limit (Abweichung A-7). Kaufbelege von Mehrzweckgutscheinen laufen in einem eigenen Nummernkreis (A-GUT-5, Abweichung A-13). Der Dienst `gutscheine` bekommt seine Grundlagen: Codes mit mindestens 60 Bit aus einem Alphabet ohne verwechselbare Zeichen (A-GUT-7), Schreibweise für Anzeige und Eingabe, Hinweistext zur Gruppe (A-GUT-1a) und das Protokoll.

**Files:**
- Create: `core/beachhub_core/models/gutscheine.py`
- Modify: `core/beachhub_core/models/rechnungen.py` (`Nummernkreis`, `Rechnung.nummer`), `core/beachhub_core/models/__init__.py`
- Create: `core/alembic/versions/0015_gutscheine.py`
- Modify: `core/beachhub_core/services/rechnungen.py` (`naechste_nummer`, `_neue_rechnung`)
- Create: `core/beachhub_core/services/gutscheine.py`
- Modify: `core/beachhub_core/templating.py` (Filter `code`)
- Create: `core/tests/hilfen_gutschein.py`, `core/tests/test_gutscheine.py`
- Modify: `core/tests/conftest.py` (`pytest_plugins`), `core/tests/test_rechnungen.py`

**Interfaces:**
- Consumes: `Rechnung`, `RechnungPosition`, `Zahlung`, `Kunde`, `Feld`, `Kundengruppe`, `Buchung` (Bestand nach 1a-I/1a-II).
- Produces: `models.Gutschein` mit Konstanten `KAUF="kauf"`, `FREI="frei"`, `EINHEIT="einheit"`, `WERT="wert"`, `BEIM_KAUF="beim_kauf"`, `BEI_EINLOESUNG="bei_einloesung"`, `KEINE="keine"`, `OFFEN="offen"`, `ABGEBROCHEN="abgebrochen"`, `AKTIV="aktiv"`, `TEILWEISE="teilweise_eingeloest"`, `EINGELOEST="eingeloest"`, `VERFALLEN="verfallen"`, `GESPERRT="gesperrt"`, `EINLOESBAR=(AKTIV, TEILWEISE)`; Spalten `code, art, modell, steuer, minuten?, nennwert_brutto?, restwert?, kaufpreis_brutto?, kundengruppe_id?, ust_satz?, nur_mitglieder, feld_id?, zeitraum_von?, zeitraum_bis?, max_einloesungen, kaeufer_kunde_id?, inhaber_kunde_id?, zahlung_id?, rechnung_id?, ausgestellt_von_admin_id?, ausgestellt_am, gueltig_bis, status, empfaenger_email?, serie?, notiz`; Beziehung `einloesungen`.
- Produces: `models.GutscheinEinloesung(gutschein_id, buchung_id, kunde_id, minuten_gedeckt?, betrag, steuerbetrag?, rechnung_position_id?, zeitpunkt, rueckgaengig_am?, rueckgaengig_grund)`, Beziehung `gutschein`; `models.GutscheinFehlversuch(kunde_id, zeitpunkt)`.
- Produces: `Nummernkreis(kreis, jahr, letzte_nummer)` mit Primärschlüssel `(kreis, jahr)`; `rechnungen.PRAEFIX = {"rechnung": "", "gutschein_beleg": "GB-"}`; `rechnungen.naechste_nummer(db, jahr, kreis="rechnung") -> str`; `_neue_rechnung` vergibt bei `art == "gutschein_beleg"` Nummern aus dem Kreis `gutschein_beleg`.
- Produces: `services.gutscheine` mit `NULL`, `ALPHABET`, `CODE_LAENGE = 13`, `MITGLIEDSHINWEIS`, `ALLE_HINWEIS`, `GutscheinFehler(grund)` (Attribut `grund`), `normalisiere(roh: str) -> str`, `anzeige(code: str) -> str` (`ABCD-EFGH-JKLMN`), `neuer_code(db) -> str`, `hinweis(g) -> str`, `beschreibung(g) -> str`, `anzahl_aktive_einloesungen(db, g) -> int`, `status_nach_verbrauch(db, g) -> str`, `protokolliere(db, g, vorher, aktion, *, quelle, admin_user_id=None) -> None` (Audit `objekt_typ="gutschein"` und Markierung der Konto-Dokumente von Inhaber und Käufer).
- Produces: Jinja-Filter `code` (`templating.f_code`, gleiche Schreibweise wie `gutscheine.anzeige`).
- Produces (Tests): Fixture `gwelt` → `(feld, kunde)` mit 15 € je Stunde, Uhr 25.11.2027, Gutscheinpreisen 26 €/30 €; Funktionen `hilfen_gutschein.kaufgutschein(db, kunde, **felder) -> Gutschein` und `hilfen_gutschein.freicode(db, **felder) -> Gutschein`.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/hilfen_gutschein.py`:
```python
"""Testwelt für Gutscheine: ein Feld mit Stundenraster 9–23 Uhr zu 15 € je Stunde, eine Kundin,
Uhr am 25.11.2027, Gutscheinpreise 26 € (Mitglieder) und 30 € (Nicht-Mitglieder)."""

from datetime import date, time
from decimal import Decimal
from typing import Any

import pytest
from beachhub_core import clock
from beachhub_core.models import Betriebszeit, Feld, FeldRaster, Gutschein, Kunde, Tarif
from beachhub_core.services import gutscheine, konfiguration, kunden
from sqlalchemy.orm import Session


@pytest.fixture
def gwelt(db: Session) -> tuple[Feld, Kunde]:
    f = Feld(name="F1", reihenfolge=1)
    f.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add_all([f, Tarif(name="Std", preis=Decimal("15.00"))])
    for wt in range(7):
        db.add(Betriebszeit(wochentag=wt, oeffnet=time(9), schliesst=time(23)))
    db.flush()
    k = kunden.lege_an(db, name="Anna", email="anna@x.de")
    db.commit()
    clock.set_override(db, date(2027, 11, 25))
    konfiguration.setze(db, "gutschein_preis_mitglied", "26")
    konfiguration.setze(db, "gutschein_preis_nichtmitglied", "30")
    db.commit()
    return f, k


def kaufgutschein(db: Session, kunde: Kunde | None = None, **felder: Any) -> Gutschein:
    """Ein bezahlter Gutschein über eine Buchungseinheit (120 Minuten, 30 €, Einzweck, 19 %)."""
    werte: dict[str, Any] = {
        "code": gutscheine.neuer_code(db),
        "art": Gutschein.KAUF,
        "modell": Gutschein.EINHEIT,
        "steuer": Gutschein.BEIM_KAUF,
        "minuten": 120,
        "kaufpreis_brutto": Decimal("30.00"),
        "ust_satz": Decimal("19.00"),
        "nur_mitglieder": False,
        "max_einloesungen": 1,
        "kaeufer_kunde_id": kunde.id if kunde else None,
        "inhaber_kunde_id": kunde.id if kunde else None,
        "gueltig_bis": date(2029, 11, 25),
        "status": Gutschein.AKTIV,
    }
    werte.update(felder)
    g = Gutschein(**werte)
    db.add(g)
    db.flush()
    return g


def freicode(db: Session, **felder: Any) -> Gutschein:
    """Ein Freischaltcode über eine Buchungseinheit, einmal einlösbar, 90 Tage gültig."""
    werte: dict[str, Any] = {
        "code": gutscheine.neuer_code(db),
        "art": Gutschein.FREI,
        "modell": Gutschein.EINHEIT,
        "steuer": Gutschein.KEINE,
        "minuten": 120,
        "nur_mitglieder": False,
        "max_einloesungen": 1,
        "gueltig_bis": date(2028, 2, 23),
        "status": Gutschein.AKTIV,
    }
    werte.update(felder)
    g = Gutschein(**werte)
    db.add(g)
    db.flush()
    return g
```

`core/tests/conftest.py`: `pytest_plugins = ["hilfen_halle", "hilfen_gutschein"]` (Kommentar darüber um „und `gwelt` für Gutscheine“ ergänzen).

`core/tests/test_gutscheine.py`:
```python
from decimal import Decimal

import pytest
from beachhub_core.models import Audit, Gutschein, LesestandVersion
from beachhub_core.services import gutscheine
from hilfen_gutschein import freicode, kaufgutschein
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def test_codes_ohne_verwechselbare_zeichen(db: Session) -> None:
    codes = {gutscheine.neuer_code(db) for _ in range(300)}
    assert len(codes) == 300
    for code in codes:
        assert len(code) == gutscheine.CODE_LAENGE == 13
        assert set(code) <= set(gutscheine.ALPHABET)
        assert not set(code) & set("0O1I")
    # 32 Zeichen = 5 Bit je Stelle: 13 Stellen sind 65 Bit (A-GUT-7: mindestens 60).
    assert len(set(gutscheine.ALPHABET)) == 32


def test_schreibweise_fuer_eingabe_und_anzeige() -> None:
    assert gutscheine.normalisiere(" abcd-efgh jklmn ") == "ABCDEFGHJKLMN"
    assert gutscheine.anzeige("ABCDEFGHJKLMN") == "ABCD-EFGH-JKLMN"


def test_hinweis_und_beschreibung(db: Session, gwelt) -> None:
    _, k = gwelt
    mitglied = kaufgutschein(db, k, nur_mitglieder=True)
    alle = kaufgutschein(db, k)
    wert = kaufgutschein(
        db, k, modell=Gutschein.WERT, minuten=None, nennwert_brutto=Decimal("50.00"),
        restwert=Decimal("50.00"),
    )
    assert gutscheine.hinweis(mitglied) == "Mitgliedsgutschein – nur für DJK-Mitglieder einlösbar"
    assert gutscheine.hinweis(alle) == "Gutschein – für alle einlösbar"
    assert gutscheine.beschreibung(alle) == "über eine Buchungseinheit (120 Minuten)"
    assert gutscheine.beschreibung(wert) == "über 50,00 €"


def test_code_ist_eindeutig(db: Session, gwelt) -> None:
    g = freicode(db)
    db.commit()
    db.add(
        Gutschein(
            code=g.code, art=Gutschein.FREI, modell=Gutschein.EINHEIT, steuer=Gutschein.KEINE,
            minuten=120, max_einloesungen=1, gueltig_bis=g.gueltig_bis, status=Gutschein.AKTIV,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_protokoll_und_konto_markierung(db: Session, gwelt) -> None:
    _, k = gwelt
    g = kaufgutschein(db, k)
    vorher = {"status": "aktiv"}
    g.status = Gutschein.GESPERRT
    gutscheine.protokolliere(db, g, vorher, "gesperrt", quelle="admin")
    db.commit()
    a = db.query(Audit).filter_by(objekt_typ="gutschein", objekt_id=g.id).one()
    assert a.nachher_json["aktion"] == "gesperrt" and a.nachher_json["status"] == "gesperrt"
    assert db.get(LesestandVersion, f"konto:{k.id}").geaendert


def test_status_nach_verbrauch(db: Session, gwelt) -> None:
    _, k = gwelt
    assert gutscheine.status_nach_verbrauch(db, kaufgutschein(db, k)) == Gutschein.AKTIV
    teil = kaufgutschein(
        db, k, modell=Gutschein.WERT, minuten=None, nennwert_brutto=Decimal("50.00"),
        restwert=Decimal("20.00"),
    )
    assert gutscheine.status_nach_verbrauch(db, teil) == Gutschein.TEILWEISE
    teil.restwert = Decimal("0.00")
    assert gutscheine.status_nach_verbrauch(db, teil) == Gutschein.EINGELOEST
```

In `core/tests/test_rechnungen.py`:
```python
def test_kaufbelege_eigener_nummernkreis(db: Session) -> None:
    assert rechnungen.naechste_nummer(db, 2027) == "2027-00001"
    assert rechnungen.naechste_nummer(db, 2027, "gutschein_beleg") == "GB-2027-00001"
    assert rechnungen.naechste_nummer(db, 2027) == "2027-00002"
    assert rechnungen.naechste_nummer(db, 2027, "gutschein_beleg") == "GB-2027-00002"
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_gutscheine.py tests/test_rechnungen.py`
Expected: FAIL – `ImportError: cannot import name 'Gutschein' from 'beachhub_core.models'`.

- [ ] **Step 3: Modelle**

`core/beachhub_core/models/gutscheine.py`:
```python
"""Gutscheine und Freischaltcodes (Spec § 3.9a, § 5)."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    DECIMAL,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from beachhub_core.models.base import Base, UUIDMixin, ZeitstempelMixin, utcnow


class Gutschein(UUIDMixin, ZeitstempelMixin, Base):
    """Gekaufter Gutschein (`art = kauf`) oder Freischaltcode des Betreibers (`art = frei`).
    Modell, Steuervariante, Minuten, Preis, Gruppe und Satz werden beim Anlegen festgeschrieben;
    spätere Änderungen der Einstellungen betreffen nur neue Gutscheine (§ 3.14)."""

    __tablename__ = "gutschein"
    __table_args__ = (CheckConstraint("restwert >= 0", name="gutschein_restwert_nicht_negativ"),)

    KAUF, FREI = "kauf", "frei"
    EINHEIT, WERT = "einheit", "wert"
    BEIM_KAUF, BEI_EINLOESUNG, KEINE = "beim_kauf", "bei_einloesung", "keine"
    # offen: gekauft, aber noch nicht bezahlt (Abweichung A-3); abgebrochen: Zahlungsfrist verstrichen.
    OFFEN, ABGEBROCHEN = "offen", "abgebrochen"
    AKTIV, TEILWEISE, EINGELOEST = "aktiv", "teilweise_eingeloest", "eingeloest"
    VERFALLEN, GESPERRT = "verfallen", "gesperrt"
    EINLOESBAR = (AKTIV, TEILWEISE)

    code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    art: Mapped[str] = mapped_column(String(5), nullable=False)
    modell: Mapped[str] = mapped_column(String(7), nullable=False)
    steuer: Mapped[str] = mapped_column(String(14), nullable=False)
    minuten: Mapped[int | None] = mapped_column(Integer)
    nennwert_brutto: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2))
    restwert: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2))
    kaufpreis_brutto: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2))
    kundengruppe_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kundengruppe.id")
    )
    ust_satz: Mapped[Decimal | None] = mapped_column(DECIMAL(5, 2))
    nur_mitglieder: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    feld_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("feld.id"))
    zeitraum_von: Mapped[date | None] = mapped_column(Date)
    zeitraum_bis: Mapped[date | None] = mapped_column(Date)
    max_einloesungen: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    kaeufer_kunde_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id")
    )
    inhaber_kunde_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id")
    )
    zahlung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zahlung.id"), unique=True
    )
    rechnung_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rechnung.id")
    )
    ausgestellt_von_admin_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ausgestellt_am: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    gueltig_bis: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(22), nullable=False)
    empfaenger_email: Mapped[str | None] = mapped_column(String(200))
    serie: Mapped[str | None] = mapped_column(String(100), index=True)
    notiz: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    einloesungen: Mapped[list["GutscheinEinloesung"]] = relationship(
        back_populates="gutschein", order_by="GutscheinEinloesung.zeitpunkt"
    )


class GutscheinEinloesung(UUIDMixin, Base):
    """Eine Einlösung in einer Buchung. `betrag` ist der gedeckte Tarifwert (Modell `einheit`)
    bzw. der verbrauchte Wert (Modell `wert`); `steuerbetrag` nur bei Mehrzweckgutscheinen
    (Abweichung A-6) – er steht auf der Rechnung der Buchung in `rechnung_position_id`."""

    __tablename__ = "gutschein_einloesung"
    gutschein_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("gutschein.id"), nullable=False, index=True
    )
    buchung_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("buchung.id"), nullable=False, index=True
    )
    kunde_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False
    )
    minuten_gedeckt: Mapped[int | None] = mapped_column(Integer)
    betrag: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    steuerbetrag: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2))
    rechnung_position_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rechnung_position.id")
    )
    zeitpunkt: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    rueckgaengig_am: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rueckgaengig_grund: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    gutschein: Mapped[Gutschein] = relationship(back_populates="einloesungen")


class GutscheinFehlversuch(UUIDMixin, Base):
    """Ein abgelehnter Code (A-GUT-7, Abweichung A-7): Grundlage des Rate-Limits je Konto."""

    __tablename__ = "gutschein_fehlversuch"
    __table_args__ = (Index("ix_gutschein_fehlversuch_kunde_zeit", "kunde_id", "zeitpunkt"),)
    kunde_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("kunde.id"), nullable=False
    )
    zeitpunkt: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

`core/beachhub_core/models/rechnungen.py`:
- `Nummernkreis` ersetzen:
```python
class Nummernkreis(Base):
    """Lückenlose Nummern je Kreis und Jahr: `rechnung` für alle Rechnungen, `gutschein_beleg`
    für Kaufbelege von Mehrzweckgutscheinen (A-GUT-5)."""

    __tablename__ = "nummernkreis"
    kreis: Mapped[str] = mapped_column(String(20), primary_key=True, default="rechnung")
    jahr: Mapped[int] = mapped_column(Integer, primary_key=True)
    letzte_nummer: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
```
- In `Rechnung`: `nummer: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)`; den Kommentar an `art` um `| gutschein | gutschein_beleg` ergänzen.

`core/beachhub_core/models/__init__.py`: `from beachhub_core.models.gutscheine import Gutschein, GutscheinEinloesung, GutscheinFehlversuch` ergänzen und die drei Namen alphabetisch in `__all__` aufnehmen.

- [ ] **Step 4: Migration 0015**

`core/alembic/versions/0015_gutscheine.py`:
```python
"""gutscheine: Gutscheine, Einlösungen und Fehlversuche (Spec § 3.9a); Nummernkreis je Art für
Kaufbelege von Mehrzweckgutscheinen (Präfix GB-), Rechnungsnummern bis 20 Zeichen.

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("nummernkreis_pkey", "nummernkreis", type_="primary")
    op.add_column(
        "nummernkreis",
        sa.Column("kreis", sa.String(length=20), server_default="rechnung", nullable=False),
    )
    op.alter_column("nummernkreis", "kreis", server_default=None)
    op.create_primary_key("nummernkreis_pkey", "nummernkreis", ["kreis", "jahr"])
    op.alter_column(
        "rechnung",
        "nummer",
        type_=sa.String(length=20),
        existing_type=sa.String(length=12),
        existing_nullable=False,
    )

    op.create_table(
        "gutschein",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("art", sa.String(length=5), nullable=False),
        sa.Column("modell", sa.String(length=7), nullable=False),
        sa.Column("steuer", sa.String(length=14), nullable=False),
        sa.Column("minuten", sa.Integer(), nullable=True),
        sa.Column("nennwert_brutto", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("restwert", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("kaufpreis_brutto", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("kundengruppe_id", sa.UUID(), nullable=True),
        sa.Column("ust_satz", sa.DECIMAL(precision=5, scale=2), nullable=True),
        sa.Column("nur_mitglieder", sa.Boolean(), nullable=False),
        sa.Column("feld_id", sa.UUID(), nullable=True),
        sa.Column("zeitraum_von", sa.Date(), nullable=True),
        sa.Column("zeitraum_bis", sa.Date(), nullable=True),
        sa.Column("max_einloesungen", sa.Integer(), nullable=False),
        sa.Column("kaeufer_kunde_id", sa.UUID(), nullable=True),
        sa.Column("inhaber_kunde_id", sa.UUID(), nullable=True),
        sa.Column("zahlung_id", sa.UUID(), nullable=True),
        sa.Column("rechnung_id", sa.UUID(), nullable=True),
        sa.Column("ausgestellt_von_admin_id", sa.UUID(), nullable=True),
        sa.Column("ausgestellt_am", sa.DateTime(timezone=True), nullable=False),
        sa.Column("gueltig_bis", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=22), nullable=False),
        sa.Column("empfaenger_email", sa.String(length=200), nullable=True),
        sa.Column("serie", sa.String(length=100), nullable=True),
        sa.Column("notiz", sa.String(length=500), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("restwert >= 0", name="gutschein_restwert_nicht_negativ"),
        sa.ForeignKeyConstraint(["kundengruppe_id"], ["kundengruppe.id"]),
        sa.ForeignKeyConstraint(["feld_id"], ["feld.id"]),
        sa.ForeignKeyConstraint(["kaeufer_kunde_id"], ["kunde.id"]),
        sa.ForeignKeyConstraint(["inhaber_kunde_id"], ["kunde.id"]),
        sa.ForeignKeyConstraint(["zahlung_id"], ["zahlung.id"]),
        sa.ForeignKeyConstraint(["rechnung_id"], ["rechnung.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
        sa.UniqueConstraint("zahlung_id"),
    )
    op.create_index("ix_gutschein_serie", "gutschein", ["serie"])
    op.create_table(
        "gutschein_einloesung",
        sa.Column("gutschein_id", sa.UUID(), nullable=False),
        sa.Column("buchung_id", sa.UUID(), nullable=False),
        sa.Column("kunde_id", sa.UUID(), nullable=False),
        sa.Column("minuten_gedeckt", sa.Integer(), nullable=True),
        sa.Column("betrag", sa.DECIMAL(precision=10, scale=2), nullable=False),
        sa.Column("steuerbetrag", sa.DECIMAL(precision=10, scale=2), nullable=True),
        sa.Column("rechnung_position_id", sa.UUID(), nullable=True),
        sa.Column("zeitpunkt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rueckgaengig_am", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rueckgaengig_grund", sa.String(length=200), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["gutschein_id"], ["gutschein.id"]),
        sa.ForeignKeyConstraint(["buchung_id"], ["buchung.id"]),
        sa.ForeignKeyConstraint(["kunde_id"], ["kunde.id"]),
        sa.ForeignKeyConstraint(["rechnung_position_id"], ["rechnung_position.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gutschein_einloesung_gutschein_id", "gutschein_einloesung", ["gutschein_id"]
    )
    op.create_index("ix_gutschein_einloesung_buchung_id", "gutschein_einloesung", ["buchung_id"])
    op.create_table(
        "gutschein_fehlversuch",
        sa.Column("kunde_id", sa.UUID(), nullable=False),
        sa.Column("zeitpunkt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(["kunde_id"], ["kunde.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_gutschein_fehlversuch_kunde_zeit", "gutschein_fehlversuch", ["kunde_id", "zeitpunkt"]
    )


def downgrade() -> None:
    op.drop_index("ix_gutschein_fehlversuch_kunde_zeit", table_name="gutschein_fehlversuch")
    op.drop_table("gutschein_fehlversuch")
    op.drop_index("ix_gutschein_einloesung_buchung_id", table_name="gutschein_einloesung")
    op.drop_index("ix_gutschein_einloesung_gutschein_id", table_name="gutschein_einloesung")
    op.drop_table("gutschein_einloesung")
    op.drop_index("ix_gutschein_serie", table_name="gutschein")
    op.drop_table("gutschein")
    op.alter_column(
        "rechnung",
        "nummer",
        type_=sa.String(length=12),
        existing_type=sa.String(length=20),
        existing_nullable=False,
    )
    op.execute("DELETE FROM nummernkreis WHERE kreis <> 'rechnung'")
    op.drop_constraint("nummernkreis_pkey", "nummernkreis", type_="primary")
    op.drop_column("nummernkreis", "kreis")
    op.create_primary_key("nummernkreis_pkey", "nummernkreis", ["jahr"])
```

- [ ] **Step 5: Nummernkreis je Art**

`core/beachhub_core/services/rechnungen.py`:
- Unter `CENT`:
```python
# Nummernkreise (A-RECH-1): alle Rechnungen lückenlos je Jahr; Kaufbelege von
# Mehrzweckgutscheinen in einem eigenen Kreis mit Präfix (A-GUT-5).
PRAEFIX: dict[str, str] = {"rechnung": "", "gutschein_beleg": "GB-"}
```
- `naechste_nummer` ersetzen:
```python
def naechste_nummer(db: Session, jahr: int, kreis: str = "rechnung") -> str:
    db.execute(
        insert(Nummernkreis)
        .values(kreis=kreis, jahr=jahr, letzte_nummer=0)
        .on_conflict_do_nothing()
    )
    zeile = db.scalar(
        select(Nummernkreis)
        .where(Nummernkreis.kreis == kreis, Nummernkreis.jahr == jahr)
        .with_for_update()
    )
    if zeile is None:
        raise RuntimeError("Nummernkreis konnte nicht angelegt werden")
    zeile.letzte_nummer += 1
    db.flush()
    return f"{PRAEFIX[kreis]}{jahr}-{zeile.letzte_nummer:05d}"
```
- In `_neue_rechnung` die Nummernvergabe `nummer=naechste_nummer(db, heute.year),` ersetzen durch
```python
        nummer=naechste_nummer(
            db, heute.year, "gutschein_beleg" if art == "gutschein_beleg" else "rechnung"
        ),
```

- [ ] **Step 6: Grundlagen des Dienstes**

`core/beachhub_core/services/gutscheine.py`:
```python
"""Gutscheine und Freischaltcodes (Spec § 3.9a).

Ein gekaufter Gutschein (`art = kauf`) und ein Freischaltcode des Betreibers (`art = frei`) teilen
Code, Einlösung in der Buchungstransaktion, Zustände und Missbrauchsschutz. Was beim Anlegen galt
(Modell, Steuervariante, Minuten, Preis, Gruppe, Satz), steht am Gutschein.
"""

import re
import secrets
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from beachhub_core.models import Gutschein, GutscheinEinloesung
from beachhub_core.services import audit

NULL = Decimal("0.00")
# Ohne verwechselbare Zeichen 0/O und 1/I (A-GUT-7): 32 Zeichen, 5 Bit je Stelle.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LAENGE = 13  # 65 Bit
# Steht in der Mail, im Portal und auf der Kaufrechnung (A-GUT-1a, Betreiber 03.10.2026).
MITGLIEDSHINWEIS = "Mitgliedsgutschein – nur für DJK-Mitglieder einlösbar"
ALLE_HINWEIS = "Gutschein – für alle einlösbar"


class GutscheinFehler(Exception):  # noqa: N818
    def __init__(self, grund: str) -> None:
        super().__init__(grund)
        self.grund = grund


def normalisiere(roh: str) -> str:
    """Eingabe des Kunden: Leerzeichen und Bindestriche fallen weg, Groß- und Kleinschreibung
    zählt nicht."""
    return re.sub(r"[\s-]", "", roh).upper()


def anzeige(code: str) -> str:
    return f"{code[:4]}-{code[4:8]}-{code[8:]}"


def neuer_code(db: Session) -> str:
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LAENGE))
        if db.scalar(select(Gutschein.id).where(Gutschein.code == code)) is None:
            return code


def hinweis(g: Gutschein) -> str:
    return MITGLIEDSHINWEIS if g.nur_mitglieder else ALLE_HINWEIS


def _euro(betrag: Decimal) -> str:
    return f"{betrag:.2f}".replace(".", ",") + " €"


def beschreibung(g: Gutschein) -> str:
    if g.modell == Gutschein.WERT and g.nennwert_brutto is not None:
        return f"über {_euro(g.nennwert_brutto)}"
    return f"über eine Buchungseinheit ({g.minuten} Minuten)"


def anzahl_aktive_einloesungen(db: Session, g: Gutschein) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(GutscheinEinloesung)
            .where(
                GutscheinEinloesung.gutschein_id == g.id,
                GutscheinEinloesung.rueckgaengig_am.is_(None),
            )
        )
        or 0
    )


def status_nach_verbrauch(db: Session, g: Gutschein) -> str:
    """Zustand eines einlösbaren Gutscheins nach seinem Verbrauch (A-GUT-4)."""
    if g.art == Gutschein.KAUF and g.modell == Gutschein.WERT:
        if g.restwert is None or g.restwert <= NULL:
            return Gutschein.EINGELOEST
        if g.nennwert_brutto is not None and g.restwert < g.nennwert_brutto:
            return Gutschein.TEILWEISE
        return Gutschein.AKTIV
    if anzahl_aktive_einloesungen(db, g) >= g.max_einloesungen:
        return Gutschein.EINGELOEST
    return Gutschein.AKTIV


def protokolliere(
    db: Session,
    g: Gutschein,
    vorher: dict[str, Any] | None,
    aktion: str,
    *,
    quelle: str,
    admin_user_id: uuid.UUID | None = None,
) -> None:
    db.flush()
    audit.protokolliere(
        db,
        quelle=quelle,
        objekt_typ="gutschein",
        objekt_id=g.id,
        vorher=vorher,
        nachher={**audit.als_dict(g), "aktion": aktion},
        admin_user_id=admin_user_id,
    )
    konten = {i for i in (g.inhaber_kunde_id, g.kaeufer_kunde_id) if i is not None}
    if konten:
        from beachhub_core.services import lesestand

        lesestand.markiere_geaendert(db, *(f"konto:{i}" for i in sorted(konten, key=str)))
```

`core/beachhub_core/templating.py` (kein Import aus `services` – `templating` wird von `benachrichtigung` importiert, ein Import zurück ergäbe einen Zyklus über `services/__init__.py`):
```python
def f_code(v: str) -> str:
    """Gutscheincode in der Schreibweise von gutscheine.anzeige: ABCD-EFGH-JKLMN."""
    return f"{v[:4]}-{v[4:8]}-{v[8:]}"
```
und im `filters.update`-Block `"code": f_code,` ergänzen. Ein Test in `test_gutscheine.py` hält beide gleich:
```python
def test_filter_code_entspricht_anzeige() -> None:
    from beachhub_core.templating import f_code

    assert f_code("ABCDEFGHJKLMN") == gutscheine.anzeige("ABCDEFGHJKLMN")
```

- [ ] **Step 7: Tests und Lint**

Run:
```bash
(cd core && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS (auch `test_migrationen.py`).

- [ ] **Step 8: Commit**

```bash
git add core
git commit -m "feat(core): Datenmodell für Gutscheine, Codes und eigener Nummernkreis für Kaufbelege"
```

---
## Task 3: Freischaltcodes und Verwaltung der Gutscheine

Der Betreiber stellt Freischaltcodes einzeln oder als Serie aus (ohne Geldwert, eine Buchungseinheit, Vorgabe einmal einlösbar und 90 Tage gültig, optional Feld und Zeitraum; A-GUT-2) und exportiert eine Serie als CSV. Die Seite „Gutscheine“ listet alle Gutscheine mit Status, Käufer, Inhaber und Restwert; auf der Detailseite sperrt er einen Code, ändert die Gültigkeit oder aktiviert einen verfallenen Gutschein wieder (A-GUT-4, A-ADM-8). Ein nächtlicher Lauf lässt abgelaufene Gutscheine verfallen.

**Files:**
- Modify: `core/beachhub_core/services/gutscheine.py`
- Create: `core/beachhub_core/routes/gutscheine.py`
- Create: `core/beachhub_core/templates/gutscheine/liste.html`, `gutscheine/detail.html`, `gutscheine/freicodes.html`
- Modify: `core/beachhub_core/main.py`, `core/beachhub_core/navigation.py`, `core/beachhub_core/jobs.py`
- Test: `core/tests/test_gutscheine.py`, Create: `core/tests/test_ui_gutscheine.py`, Modify: `core/tests/test_ui_navigation.py`, `core/tests/test_jobs.py`

**Interfaces:**
- Consumes: `Gutschein`, `gutscheine.neuer_code`, `protokolliere`, `status_nach_verbrauch`, `anzeige` (Task 2); `konfiguration.hole("freicode_gueltig_tage")`, `hole("gutschein_minuten")` (Task 1); `rechnungen.csv_sicher` (1a-I).
- Produces: `gutscheine.stelle_freicodes_aus(db, *, anzahl: int, gueltig_bis: date | None, max_einloesungen: int = 1, feld_id: UUID | None = None, zeitraum_von: date | None = None, zeitraum_bis: date | None = None, serie: str = "", notiz: str = "", admin_user_id: UUID | None) -> list[Gutschein]` – Fehlergründe `anzahl_ungueltig` (1–1000), `max_einloesungen_ungueltig`, `gueltig_bis_vergangen`, `zeitraum_ungueltig`; ab zwei Codes bekommt die Serie einen Namen (Vorgabe „Serie JJJJ-MM-TT HH:MM:SS“).
- Produces: `gutscheine.sperre(db, g, *, grund: str, admin_user_id) -> None` (Grund `bereits_gesperrt`), `gutscheine.setze_gueltigkeit(db, g, *, gueltig_bis: date, admin_user_id) -> None` (Gründe `nicht_aenderbar`, `gueltig_bis_vergangen`; reaktiviert verfallene), `gutscheine.verfalle(db) -> int`, `gutscheine.serie_csv(db, serie: str) -> str`, `gutscheine.STATUS_TEXT: dict[str, str]`.
- Produces: `jobs.gutscheine_verfall_ausfuehren(db) -> int`; Scheduler-Job `gutscheine_verfall` täglich 00:20.
- Produces: Routen `GET /admin/gutscheine` (Filter `status`, `art`, `q` = Code oder Serie), `GET /admin/gutscheine/freicodes/neu`, `POST /admin/gutscheine/freicodes`, `GET /admin/gutscheine/serie.csv?serie=…`, `GET /admin/gutscheine/{id}`, `POST /admin/gutscheine/{id}/sperren` (Feld `grund`), `POST /admin/gutscheine/{id}/gueltigkeit` (Feld `gueltig_bis`); Bereich „Gutscheine“ mit Unterpunkten „Übersicht“ und „Freischaltcodes ausstellen“.

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_gutscheine.py` ergänzen (Importe: `from datetime import date`, `from beachhub_core import clock`):
```python
def test_freicode_einzeln_mit_vorgaben(db: Session, gwelt) -> None:
    (g,) = gutscheine.stelle_freicodes_aus(db, anzahl=1, gueltig_bis=None, admin_user_id=None)
    db.commit()
    assert (g.art, g.modell, g.steuer, g.minuten) == ("frei", "einheit", "keine", 120)
    assert g.gueltig_bis == date(2028, 2, 23)  # 25.11.2027 + 90 Tage
    assert g.status == "aktiv" and g.max_einloesungen == 1 and g.serie is None
    a = db.query(Audit).filter_by(objekt_typ="gutschein", objekt_id=g.id).one()
    assert a.nachher_json["aktion"] == "ausgestellt"


def test_freicode_serie_und_csv(db: Session, gwelt) -> None:
    f, _ = gwelt
    serie = gutscheine.stelle_freicodes_aus(
        db,
        anzahl=3,
        gueltig_bis=date(2028, 1, 31),
        max_einloesungen=2,
        feld_id=f.id,
        zeitraum_von=date(2027, 12, 1),
        zeitraum_bis=date(2027, 12, 31),
        serie="Eröffnung",
        admin_user_id=None,
    )
    db.commit()
    assert {g.serie for g in serie} == {"Eröffnung"} and len({g.code for g in serie}) == 3
    zeilen = gutscheine.serie_csv(db, "Eröffnung").strip().splitlines()
    assert zeilen[0] == "code;gueltig_bis;feld;zeitraum_von;zeitraum_bis;max_einloesungen;status"
    assert len(zeilen) == 4
    assert f"{gutscheine.anzeige(serie[0].code)};2028-01-31;F1;2027-12-01;2027-12-31;2;aktiv" in zeilen


@pytest.mark.parametrize(
    "felder,grund",
    [
        ({"anzahl": 0}, "anzahl_ungueltig"),
        ({"anzahl": 1001}, "anzahl_ungueltig"),
        ({"max_einloesungen": 0}, "max_einloesungen_ungueltig"),
        ({"gueltig_bis": date(2027, 11, 24)}, "gueltig_bis_vergangen"),
        ({"zeitraum_von": date(2027, 12, 2), "zeitraum_bis": date(2027, 12, 1)}, "zeitraum_ungueltig"),
    ],
)
def test_freicodes_pruefen_eingaben(db: Session, gwelt, felder: dict, grund: str) -> None:
    werte = {"anzahl": 1, "gueltig_bis": None, "admin_user_id": None, **felder}
    with pytest.raises(gutscheine.GutscheinFehler, match=grund):
        gutscheine.stelle_freicodes_aus(db, **werte)


def test_verfall_reaktivieren_sperren(db: Session, gwelt) -> None:
    g = freicode(db, gueltig_bis=date(2027, 11, 30))
    db.commit()
    assert gutscheine.verfalle(db) == 0
    clock.set_override(db, date(2027, 12, 1))
    assert gutscheine.verfalle(db) == 1
    db.commit()
    assert g.status == "verfallen"
    gutscheine.setze_gueltigkeit(db, g, gueltig_bis=date(2028, 1, 31), admin_user_id=None)
    db.commit()
    assert g.status == "aktiv" and g.gueltig_bis == date(2028, 1, 31)
    with pytest.raises(gutscheine.GutscheinFehler, match="gueltig_bis_vergangen"):
        gutscheine.setze_gueltigkeit(db, g, gueltig_bis=date(2027, 11, 30), admin_user_id=None)
    gutscheine.sperre(db, g, grund="Missbrauch", admin_user_id=None)
    db.commit()
    assert g.status == "gesperrt" and "Missbrauch" in g.notiz
    with pytest.raises(gutscheine.GutscheinFehler, match="bereits_gesperrt"):
        gutscheine.sperre(db, g, grund="x", admin_user_id=None)
    with pytest.raises(gutscheine.GutscheinFehler, match="nicht_aenderbar"):
        gutscheine.setze_gueltigkeit(db, g, gueltig_bis=date(2028, 3, 1), admin_user_id=None)
```

`core/tests/test_ui_gutscheine.py`:
```python
from beachhub_core.models import Gutschein
from beachhub_core.services import gutscheine
from fastapi.testclient import TestClient
from hilfen_gutschein import kaufgutschein
from sqlalchemy.orm import Session


def test_freicode_einzeln_ausstellen(eingeloggt: TestClient, db: Session, gwelt) -> None:
    c = eingeloggt
    formular = c.get("/admin/gutscheine/freicodes/neu")
    assert 'name="gueltig_bis" type="date" value="2028-02-23"' in formular.text
    r = c.post(
        "/admin/gutscheine/freicodes",
        data={"csrf_token": c.csrf, "anzahl": "1", "gueltig_bis": "", "max_einloesungen": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    g = db.query(Gutschein).one()
    assert r.headers["location"] == f"/admin/gutscheine/{g.id}"
    seite = c.get(r.headers["location"])
    assert gutscheine.anzeige(g.code) in seite.text and "Freischaltcode" in seite.text


def test_serie_ausstellen_liste_und_csv(eingeloggt: TestClient, db: Session, gwelt) -> None:
    c = eingeloggt
    r = c.post(
        "/admin/gutscheine/freicodes",
        data={
            "csrf_token": c.csrf,
            "anzahl": "3",
            "gueltig_bis": "2028-01-31",
            "max_einloesungen": "1",
            "serie": "Eröffnung",
            "notiz": "Flyer",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    liste = c.get(r.headers["location"])
    for g in db.query(Gutschein).all():
        assert gutscheine.anzeige(g.code) in liste.text
    csv = c.get("/admin/gutscheine/serie.csv", params={"serie": "Eröffnung"})
    assert csv.headers["content-type"].startswith("text/csv")
    assert len(csv.text.strip().splitlines()) == 4


def test_freicode_fehler_zeigt_meldung(eingeloggt: TestClient, gwelt) -> None:
    c = eingeloggt
    r = c.post(
        "/admin/gutscheine/freicodes",
        data={"csrf_token": c.csrf, "anzahl": "0", "gueltig_bis": "", "max_einloesungen": "1"},
    )
    assert r.status_code == 200 and "zwischen 1 und 1000" in r.text


def test_sperren_und_gueltigkeit_ueber_ui(eingeloggt: TestClient, db: Session, gwelt) -> None:
    c = eingeloggt
    _, k = gwelt
    g = kaufgutschein(db, k)
    db.commit()
    seite = c.get(f"/admin/gutscheine/{g.id}")
    assert "Anna" in seite.text and "aktiv" in seite.text
    r = c.post(
        f"/admin/gutscheine/{g.id}/gueltigkeit",
        data={"csrf_token": c.csrf, "gueltig_bis": "2030-01-31"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    r = c.post(
        f"/admin/gutscheine/{g.id}/sperren",
        data={"csrf_token": c.csrf, "grund": "Missbrauch"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(g)
    assert g.status == "gesperrt" and str(g.gueltig_bis) == "2030-01-31"
    assert "gesperrt" in c.get("/admin/gutscheine?status=gesperrt").text
```

`core/tests/test_ui_navigation.py`: `"/admin/gutscheine"` und `"/admin/gutscheine/freicodes/neu"` in `SEITEN` ergänzen.

`core/tests/test_jobs.py` (Importe: `from beachhub_core.models import Gutschein`, `from beachhub_core.services import gutscheine`):
```python
def test_gutscheine_verfall_nachts(db: Session) -> None:
    clock.set_override(db, date(2027, 11, 25))
    (g,) = gutscheine.stelle_freicodes_aus(
        db, anzahl=1, gueltig_bis=date(2027, 11, 30), admin_user_id=None
    )
    db.commit()
    clock.set_override(db, date(2027, 12, 1))
    assert jobs.gutscheine_verfall_ausfuehren(db) == 1
    assert db.get(Gutschein, g.id).status == "verfallen"
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_gutscheine.py tests/test_ui_gutscheine.py tests/test_ui_navigation.py tests/test_jobs.py`
Expected: FAIL – `AttributeError: module 'beachhub_core.services.gutscheine' has no attribute 'stelle_freicodes_aus'`, 404 auf `/admin/gutscheine`.

- [ ] **Step 3: Dienst**

`core/beachhub_core/services/gutscheine.py`:
- Importe ergänzen: `import csv`, `import io`, `from datetime import date, timedelta`, `from beachhub_shared.zeit import lokal`, `from beachhub_core import clock`, `from beachhub_core.models import Feld, Gutschein, GutscheinEinloesung`, `from beachhub_core.services import audit, konfiguration`, `from beachhub_core.services.rechnungen import csv_sicher`.
- Unter `ALLE_HINWEIS`:
```python
STATUS_TEXT: dict[str, str] = {
    Gutschein.OFFEN: "unbezahlt",
    Gutschein.ABGEBROCHEN: "Kauf abgebrochen",
    Gutschein.AKTIV: "aktiv",
    Gutschein.TEILWEISE: "teilweise eingelöst",
    Gutschein.EINGELOEST: "eingelöst",
    Gutschein.VERFALLEN: "verfallen",
    Gutschein.GESPERRT: "gesperrt",
}
```
- Am Dateiende:
```python
def _notiz(alt: str, neu: str) -> str:
    return (f"{alt} | {neu}" if alt else neu)[:500]


def stelle_freicodes_aus(
    db: Session,
    *,
    anzahl: int,
    gueltig_bis: date | None,
    max_einloesungen: int = 1,
    feld_id: uuid.UUID | None = None,
    zeitraum_von: date | None = None,
    zeitraum_bis: date | None = None,
    serie: str = "",
    notiz: str = "",
    admin_user_id: uuid.UUID | None,
) -> list[Gutschein]:
    """Freischaltcodes (A-GUT-2): ohne Geldwert und steuerfrei, sie decken eine Buchungseinheit
    (Abweichung A-8). Ab zwei Codes bilden sie eine Serie, exportierbar als CSV."""
    heute = clock.today(db)
    if not 1 <= anzahl <= 1000:
        raise GutscheinFehler("anzahl_ungueltig")
    if max_einloesungen < 1:
        raise GutscheinFehler("max_einloesungen_ungueltig")
    bis = gueltig_bis or heute + timedelta(days=konfiguration.hole(db, "freicode_gueltig_tage"))
    if bis < heute:
        raise GutscheinFehler("gueltig_bis_vergangen")
    if zeitraum_von is not None and zeitraum_bis is not None and zeitraum_bis < zeitraum_von:
        raise GutscheinFehler("zeitraum_ungueltig")
    jetzt = clock.now(db)
    name = serie.strip()[:100] or (f"Serie {lokal(jetzt):%Y-%m-%d %H:%M:%S}" if anzahl > 1 else "")
    minuten = konfiguration.hole(db, "gutschein_minuten")
    codes: list[Gutschein] = []
    for _ in range(anzahl):
        g = Gutschein(
            code=neuer_code(db),
            art=Gutschein.FREI,
            modell=Gutschein.EINHEIT,
            steuer=Gutschein.KEINE,
            minuten=minuten,
            nur_mitglieder=False,
            feld_id=feld_id,
            zeitraum_von=zeitraum_von,
            zeitraum_bis=zeitraum_bis,
            max_einloesungen=max_einloesungen,
            ausgestellt_von_admin_id=admin_user_id,
            ausgestellt_am=jetzt,
            gueltig_bis=bis,
            status=Gutschein.AKTIV,
            serie=name or None,
            notiz=notiz.strip()[:500],
        )
        db.add(g)
        db.flush()
        protokolliere(db, g, None, "ausgestellt", quelle="admin", admin_user_id=admin_user_id)
        codes.append(g)
    return codes


def sperre(db: Session, g: Gutschein, *, grund: str, admin_user_id: uuid.UUID | None) -> None:
    """Sperrt einen Code, etwa bei Missbrauch (A-GUT-4). Gesperrte Codes sind nicht einlösbar."""
    if g.status == Gutschein.GESPERRT:
        raise GutscheinFehler("bereits_gesperrt")
    vorher = audit.als_dict(g)
    g.status = Gutschein.GESPERRT
    g.notiz = _notiz(g.notiz, f"Gesperrt: {grund.strip()}")
    protokolliere(db, g, vorher, "gesperrt", quelle="admin", admin_user_id=admin_user_id)


def setze_gueltigkeit(
    db: Session, g: Gutschein, *, gueltig_bis: date, admin_user_id: uuid.UUID | None
) -> None:
    """Ändert die Gültigkeit; ein verfallener Gutschein wird damit wieder aktiv (A-GUT-4: Kulanz
    nach Ablauf, Auditeintrag)."""
    if g.status not in (*Gutschein.EINLOESBAR, Gutschein.VERFALLEN):
        raise GutscheinFehler("nicht_aenderbar")
    if gueltig_bis < clock.today(db):
        raise GutscheinFehler("gueltig_bis_vergangen")
    vorher = audit.als_dict(g)
    war_verfallen = g.status == Gutschein.VERFALLEN
    g.gueltig_bis = gueltig_bis
    if war_verfallen:
        g.status = status_nach_verbrauch(db, g)
    aktion = "reaktiviert" if war_verfallen else "gueltigkeit_geaendert"
    protokolliere(db, g, vorher, aktion, quelle="admin", admin_user_id=admin_user_id)


def verfalle(db: Session) -> int:
    """Nachtjob (A-GUT-4): Einlösbare Gutscheine mit abgelaufener Gültigkeit verfallen."""
    abgelaufen = db.scalars(
        select(Gutschein)
        .where(
            Gutschein.status.in_(Gutschein.EINLOESBAR),
            Gutschein.gueltig_bis < clock.today(db),
        )
        .with_for_update(skip_locked=True)
    ).all()
    for g in abgelaufen:
        vorher = audit.als_dict(g)
        g.status = Gutschein.VERFALLEN
        protokolliere(db, g, vorher, "verfallen", quelle="system")
    return len(abgelaufen)


def serie_csv(db: Session, serie: str) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(
        ["code", "gueltig_bis", "feld", "zeitraum_von", "zeitraum_bis", "max_einloesungen", "status"]
    )
    for g in db.scalars(
        select(Gutschein).where(Gutschein.serie == serie).order_by(Gutschein.ausgestellt_am, Gutschein.code)
    ):
        feld = db.get(Feld, g.feld_id) if g.feld_id else None
        w.writerow(
            [
                anzeige(g.code),
                g.gueltig_bis.isoformat(),
                csv_sicher(feld.name) if feld else "",
                g.zeitraum_von.isoformat() if g.zeitraum_von else "",
                g.zeitraum_bis.isoformat() if g.zeitraum_bis else "",
                g.max_einloesungen,
                g.status,
            ]
        )
    return buf.getvalue()
```

- [ ] **Step 4: Verwaltungsseiten**

`core/beachhub_core/routes/gutscheine.py`:
```python
"""Verwaltung der Gutscheine und Freischaltcodes (A-ADM-8)."""

import uuid
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from beachhub_core import auth, clock
from beachhub_core.database import get_db
from beachhub_core.models import AdminUser, Feld, Gutschein, Kunde
from beachhub_core.routes._form import fehlertext, pflicht, t_datum, t_int, t_uuid
from beachhub_core.services import gutscheine, konfiguration
from beachhub_core.services.gutscheine import GutscheinFehler
from beachhub_core.templating import mit_flash, render

router = APIRouter()

GRUND = {
    "anzahl_ungueltig": "Bitte eine Anzahl zwischen 1 und 1000 angeben",
    "max_einloesungen_ungueltig": "Einlösungen je Code: mindestens 1",
    "gueltig_bis_vergangen": "Das Datum liegt in der Vergangenheit",
    "zeitraum_ungueltig": "Der Zeitraum endet vor seinem Beginn",
    "bereits_gesperrt": "Der Gutschein ist bereits gesperrt",
    "nicht_aenderbar": "In diesem Zustand lässt sich die Gültigkeit nicht ändern",
    "kein_kaufgutschein": "Nur gekaufte Gutscheine lassen sich zurückgeben",
    "bereits_eingeloest": "Der Gutschein wurde bereits (teilweise) eingelöst",
}
FORM_FEHLER = (GutscheinFehler, ValueError, IntegrityError)


def _kunden_namen(db: Session, liste: list[Gutschein]) -> dict[uuid.UUID, str]:
    ids = {i for g in liste for i in (g.kaeufer_kunde_id, g.inhaber_kunde_id) if i is not None}
    if not ids:
        return {}
    return {k.id: k.name for k in db.scalars(select(Kunde).where(Kunde.id.in_(ids)))}


@router.get("/gutscheine", response_class=HTMLResponse)
def liste(
    request: Request,
    status: str = "",
    art: str = "",
    q: str = "",
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    stmt = select(Gutschein).order_by(Gutschein.ausgestellt_am.desc(), Gutschein.code)
    if status:
        stmt = stmt.where(Gutschein.status == status)
    if art:
        stmt = stmt.where(Gutschein.art == art)
    if q.strip():
        stmt = stmt.where(
            or_(
                Gutschein.code.ilike(f"%{gutscheine.normalisiere(q)}%"),
                Gutschein.serie.ilike(f"%{q.strip()}%"),
            )
        )
    eintraege = list(db.scalars(stmt.limit(500)).all())
    return render(
        request,
        "gutscheine/liste.html",
        admin=admin,
        gutscheine=eintraege,
        namen=_kunden_namen(db, eintraege),
        status_text=gutscheine.STATUS_TEXT,
        status=status,
        art=art,
        q=q,
    )


def _formular(
    request: Request, admin: AdminUser, db: Session, werte: dict[str, Any], fehler: str | None = None
) -> HTMLResponse:
    vorgabe = clock.today(db) + timedelta(days=konfiguration.hole(db, "freicode_gueltig_tage"))
    return render(
        request,
        "gutscheine/freicodes.html",
        admin=admin,
        felder=db.scalars(select(Feld).where(Feld.aktiv.is_(True)).order_by(Feld.reihenfolge)).all(),
        vorgabe_bis=vorgabe,
        werte=werte,
        fehler=fehler,
    )


@router.get("/gutscheine/freicodes/neu", response_class=HTMLResponse)
def freicodes_neu(
    request: Request,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    return _formular(request, admin, db, {})


@router.post("/gutscheine/freicodes", response_model=None)
async def freicodes_ausstellen(
    request: Request,
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    werte = {k: str(v) for k, v in form.items()}
    try:
        codes = gutscheine.stelle_freicodes_aus(
            db,
            anzahl=pflicht(t_int(werte.get("anzahl")), "Anzahl"),
            gueltig_bis=t_datum(werte.get("gueltig_bis")),
            max_einloesungen=t_int(werte.get("max_einloesungen")) or 1,
            feld_id=t_uuid(werte.get("feld_id")),
            zeitraum_von=t_datum(werte.get("zeitraum_von")),
            zeitraum_bis=t_datum(werte.get("zeitraum_bis")),
            serie=werte.get("serie", ""),
            notiz=werte.get("notiz", ""),
            admin_user_id=admin.id,
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return _formular(request, admin, db, werte, fehlertext(e, GRUND))
    if len(codes) == 1:
        ziel = f"/admin/gutscheine/{codes[0].id}"
        text = "Freischaltcode ausgestellt"
    else:
        ziel = f"/admin/gutscheine?q={quote(codes[0].serie or '')}"
        text = f"{len(codes)} Freischaltcodes ausgestellt (Serie „{codes[0].serie}“)"
    return mit_flash(RedirectResponse(ziel, status_code=303), text)


@router.get("/gutscheine/serie.csv")
def serie_csv(
    serie: str,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> Response:
    dateiname = quote(f"freischaltcodes_{serie}.csv")
    return Response(
        gutscheine.serie_csv(db, serie),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{dateiname}"},
    )


def _detail(
    request: Request, admin: AdminUser, db: Session, g: Gutschein, fehler: str | None = None
) -> HTMLResponse:
    namen = _kunden_namen(db, [g])
    einloeser = {e.kunde_id for e in g.einloesungen}
    if einloeser:
        namen |= {k.id: k.name for k in db.scalars(select(Kunde).where(Kunde.id.in_(einloeser)))}
    return render(
        request,
        "gutscheine/detail.html",
        admin=admin,
        g=g,
        namen=namen,
        feld=db.get(Feld, g.feld_id) if g.feld_id else None,
        status_text=gutscheine.STATUS_TEXT,
        hinweis=gutscheine.hinweis(g),
        beschreibung=gutscheine.beschreibung(g),
        fehler=fehler,
    )


def _nicht_gefunden() -> RedirectResponse:
    return mit_flash(
        RedirectResponse("/admin/gutscheine", status_code=303), "Gutschein nicht gefunden", "fehler"
    )


@router.get("/gutscheine/{gutschein_id}", response_class=HTMLResponse, response_model=None)
def detail(
    request: Request,
    gutschein_id: uuid.UUID,
    admin: AdminUser = Depends(auth.aktueller_admin),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    g = db.get(Gutschein, gutschein_id)
    if g is None:
        return _nicht_gefunden()
    return _detail(request, admin, db, g)


@router.post("/gutscheine/{gutschein_id}/sperren", response_model=None)
def sperren(
    request: Request,
    gutschein_id: uuid.UUID,
    grund: str = Form(...),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    g = db.get(Gutschein, gutschein_id)
    if g is None:
        return _nicht_gefunden()
    try:
        gutscheine.sperre(db, g, grund=grund, admin_user_id=admin.id)
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return _detail(request, admin, db, g, fehlertext(e, GRUND))
    return mit_flash(RedirectResponse(f"/admin/gutscheine/{g.id}", status_code=303), "Gesperrt")


@router.post("/gutscheine/{gutschein_id}/gueltigkeit", response_model=None)
def gueltigkeit(
    request: Request,
    gutschein_id: uuid.UUID,
    gueltig_bis: str = Form(...),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    g = db.get(Gutschein, gutschein_id)
    if g is None:
        return _nicht_gefunden()
    try:
        gutscheine.setze_gueltigkeit(
            db, g, gueltig_bis=pflicht(t_datum(gueltig_bis), "Gültig bis"), admin_user_id=admin.id
        )
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return _detail(request, admin, db, g, fehlertext(e, GRUND))
    return mit_flash(
        RedirectResponse(f"/admin/gutscheine/{g.id}", status_code=303), "Gültigkeit gespeichert"
    )
```

`core/beachhub_core/templates/gutscheine/liste.html`:
```html
{% extends "base.html" %}{% block title %}Gutscheine{% endblock %}
{% block content %}
<h1>Gutscheine</h1>
<form method="get" action="/admin/gutscheine" class="karte zeile">
  <label>Status <select name="status">
    <option value="" {% if not status %}selected{% endif %}>alle</option>
    {% for wert, text in status_text.items() %}<option value="{{ wert }}" {% if status == wert %}selected{% endif %}>{{ text }}</option>{% endfor %}
  </select></label>
  <label>Art <select name="art">
    <option value="" {% if not art %}selected{% endif %}>alle</option>
    <option value="kauf" {% if art == "kauf" %}selected{% endif %}>gekauft</option>
    <option value="frei" {% if art == "frei" %}selected{% endif %}>Freischaltcode</option>
  </select></label>
  <label>Code oder Serie <input name="q" value="{{ q }}"></label>
  <button>Filtern</button>
  {% if q and gutscheine and gutscheine[0].serie %}<a class="aktion" href="/admin/gutscheine/serie.csv?serie={{ gutscheine[0].serie|urlencode }}">Serie als CSV</a>{% endif %}
</form>
<div class="karte">
<table>
  <thead><tr><th>Code</th><th>Art</th><th>Umfang</th><th>Status</th><th>Käufer</th><th>Inhaber</th><th class="rechts">Restwert</th><th>Gültig bis</th><th>Serie</th><th class="rechts"></th></tr></thead>
  <tbody>
  {% for g in gutscheine %}<tr>
    <td>{{ g.code|code }}</td>
    <td>{{ "gekauft" if g.art == "kauf" else "Freischaltcode" }}</td>
    <td>{% if g.modell == "wert" and g.art == "kauf" %}{{ g.nennwert_brutto|euro }}{% else %}{{ g.minuten }} Min.{% endif %}</td>
    <td>{{ status_text[g.status] }}</td>
    <td>{{ namen.get(g.kaeufer_kunde_id, "") }}</td>
    <td>{{ namen.get(g.inhaber_kunde_id, "") }}</td>
    <td class="rechts">{% if g.restwert is not none %}{{ g.restwert|euro }}{% endif %}</td>
    <td>{{ g.gueltig_bis|datum }}</td>
    <td>{{ g.serie or "" }}</td>
    <td class="rechts"><a class="aktion" href="/admin/gutscheine/{{ g.id }}">öffnen</a></td>
  </tr>{% else %}
    <tr><td colspan="10" class="leer">Keine Gutscheine.</td></tr>
  {% endfor %}
  </tbody>
</table>
</div>
{% endblock %}
```

`core/beachhub_core/templates/gutscheine/freicodes.html`:
```html
{% extends "base.html" %}{% block title %}Freischaltcodes ausstellen{% endblock %}
{% block content %}
<h1>Freischaltcodes ausstellen</h1>
<p class="hinweis">Ein Freischaltcode schaltet ohne Bezahlung ein Feld für eine Buchungseinheit frei – für Werbung, als Entschädigung oder für eine Aktion. Er hat keinen Geldwert und löst keine Steuer aus. Ohne Vorgabe wählt der Kunde Feld und Termin selbst.</p>
{% if fehler %}<p class="fehler">{{ fehler }}</p>{% endif %}
<form method="post" action="/admin/gutscheine/freicodes" class="karte schmal">
  <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
  <label>Anzahl <input name="anzahl" type="number" min="1" max="1000" value="{{ werte.get('anzahl', '1') }}" required></label>
  <label>Gültig bis <input name="gueltig_bis" type="date" value="{{ werte.get('gueltig_bis') or vorgabe_bis.isoformat() }}"></label>
  <label>Einlösungen je Code <input name="max_einloesungen" type="number" min="1" value="{{ werte.get('max_einloesungen', '1') }}"></label>
  <label>Nur für Feld <select name="feld_id"><option value="">jedes Feld</option>{% for f in felder %}<option value="{{ f.id }}" {% if werte.get('feld_id') == f.id|string %}selected{% endif %}>{{ f.name }}</option>{% endfor %}</select></label>
  <label>Termin frühestens am <input name="zeitraum_von" type="date" value="{{ werte.get('zeitraum_von', '') }}"></label>
  <label>Termin spätestens am <input name="zeitraum_bis" type="date" value="{{ werte.get('zeitraum_bis', '') }}"></label>
  <label>Serie <input name="serie" value="{{ werte.get('serie', '') }}" placeholder="etwa Eröffnung 2027"></label>
  <label>Notiz <input name="notiz" value="{{ werte.get('notiz', '') }}"></label>
  <button>Ausstellen</button>
</form>
{% endblock %}
```

`core/beachhub_core/templates/gutscheine/detail.html`:
```html
{% extends "base.html" %}{% block title %}Gutschein {{ g.code|code }}{% endblock %}
{% block content %}
<h1>{{ "Gutschein" if g.art == "kauf" else "Freischaltcode" }} {{ g.code|code }}</h1>
<p><span class="badge">{{ status_text[g.status] }}</span></p>
{% if fehler %}<p class="fehler">{{ fehler }}</p>{% endif %}
<div class="zeile">
<div class="karte">
  <h2>Daten</h2>
  <p>
    {{ beschreibung }}{% if g.art == "kauf" %} · {{ hinweis }}{% endif %}<br>
    Gültig bis {{ g.gueltig_bis|datum }}<br>
    {% if g.art == "kauf" %}
    Modell: {{ "Buchungseinheit" if g.modell == "einheit" else "Eurobetrag" }} · Umsatzsteuer {{ "beim Kauf" if g.steuer == "beim_kauf" else "bei der Einlösung" }}<br>
    {% if g.kaufpreis_brutto is not none %}Kaufpreis {{ g.kaufpreis_brutto|euro }}{% if g.ust_satz is not none %} ({{ g.ust_satz|prozent }}){% endif %}<br>{% endif %}
    {% if g.restwert is not none %}Restwert {{ g.restwert|euro }}<br>{% endif %}
    Käufer: {{ namen.get(g.kaeufer_kunde_id, "–") }}{% if g.empfaenger_email %} · verschenkt an {{ g.empfaenger_email }}{% endif %}<br>
    {% if g.rechnung_id %}<a class="aktion" href="/admin/rechnungen/{{ g.rechnung_id }}">Kaufrechnung bzw. Kaufbeleg</a><br>{% endif %}
    {% else %}
    Einlösungen je Code: {{ g.max_einloesungen }}<br>
    Feld: {{ feld.name if feld else "jedes" }}{% if g.zeitraum_von or g.zeitraum_bis %} · Termine {{ g.zeitraum_von|datum if g.zeitraum_von else "…" }} bis {{ g.zeitraum_bis|datum if g.zeitraum_bis else "…" }}{% endif %}<br>
    {% if g.serie %}Serie: <a href="/admin/gutscheine?q={{ g.serie|urlencode }}">{{ g.serie }}</a><br>{% endif %}
    {% endif %}
    Inhaber: {{ namen.get(g.inhaber_kunde_id, "–") }}<br>
    Ausgestellt am {{ g.ausgestellt_am|lokal }}
    {% if g.notiz %}<br>Notiz: {{ g.notiz }}{% endif %}
  </p>
</div>
<div class="karte schmal">
  {% if g.status in ("aktiv", "teilweise_eingeloest", "verfallen") %}
  <h2>{{ "Wieder aktivieren" if g.status == "verfallen" else "Gültigkeit ändern" }}</h2>
  <form method="post" action="/admin/gutscheine/{{ g.id }}/gueltigkeit">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <label>Gültig bis <input type="date" name="gueltig_bis" value="{{ g.gueltig_bis.isoformat() }}" required></label>
    <button>Speichern</button>
  </form>
  {% endif %}
  {% if g.status != "gesperrt" %}
  <h2>Sperren</h2>
  <form method="post" action="/admin/gutscheine/{{ g.id }}/sperren">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <label>Grund <input name="grund" required></label>
    <button class="gefahr">Sperren</button>
  </form>
  {% endif %}
</div>
</div>
<div class="karte">
  <h2>Einlösungen</h2>
  <table>
    <thead><tr><th>Zeitpunkt</th><th>Kunde</th><th>Buchung</th><th class="rechts">Gedeckt</th><th>Minuten</th><th>Rückgängig</th></tr></thead>
    <tbody>
    {% for e in g.einloesungen %}<tr>
      <td>{{ e.zeitpunkt|lokal }}</td>
      <td>{{ namen.get(e.kunde_id, "") }}</td>
      <td><a class="aktion" href="/admin/belegung/buchung/{{ e.buchung_id }}">anzeigen</a></td>
      <td class="rechts">{{ e.betrag|euro }}</td>
      <td>{{ e.minuten_gedeckt or "" }}</td>
      <td>{% if e.rueckgaengig_am %}{{ e.rueckgaengig_am|lokal }} ({{ e.rueckgaengig_grund }}){% endif %}</td>
    </tr>{% else %}
      <tr><td colspan="6" class="leer">Noch nicht eingelöst.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

`core/beachhub_core/main.py`: `gutscheine` in die Routen-Importe (`from beachhub_core.routes import (…, gutscheine, …)`) und nach dem Router für Rechnungen:
```python
app.include_router(gutscheine.router, prefix="/admin", dependencies=csrf)
```

`core/beachhub_core/navigation.py`, nach dem Bereich „Rechnungen“:
```python
    Bereich(
        "Gutscheine",
        "/admin/gutscheine",
        [
            Punkt("Übersicht", "/admin/gutscheine"),
            Punkt("Freischaltcodes ausstellen", "/admin/gutscheine/freicodes/neu"),
        ],
    ),
```

- [ ] **Step 5: Nächtlicher Verfall**

`core/beachhub_core/jobs.py` (`gutscheine` in die Service-Importe):
```python
def gutscheine_verfall_ausfuehren(db: Session) -> int:
    """Abgelaufene Gutscheine und Freischaltcodes verfallen lassen (A-GUT-4)."""
    anzahl = gutscheine.verfalle(db)
    db.commit()
    return anzahl


def _job_gutscheine() -> None:
    with SessionLocal() as db:
        try:
            gutscheine_verfall_ausfuehren(db)
        except Exception:
            logger.exception("Verfall der Gutscheine fehlgeschlagen")
```
In `starte_scheduler`:
```python
    s.add_job(
        _job_gutscheine,
        CronTrigger(hour=0, minute=20),
        id="gutscheine_verfall",
        replace_existing=True,
    )
```

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
git commit -m "feat(core): Freischaltcodes ausstellen, Gutscheine verwalten, sperren, reaktivieren, verfallen"
```

---
## Task 4: Einlösung in der Buchung

`buchung_anfragen` trägt eine Liste `gutschein_codes` (A-GUT-3). Das Hauptsystem legt die Buchung an und verrechnet im selben Savepoint die Gutscheine: Zeilen nach `id` sortiert sperren, jeden Code einzeln prüfen (Gültigkeit, Mitgliedsgutschein, Feld und Zeitraum, Einlösungen), die Obergrenze je Buchung einhalten, Einheiten decken die Slots vom Beginn an, Wertgutscheine danach den Rest (A-GUT-1, -1b, -2). Erst danach wird Guthaben verrechnet; eine Bezahlsitzung gibt es nur über den Rest. Der Gutscheinanteil ist eine Zahlung mit `provider = "gutschein"`; ist die Buchung voll gedeckt, hat sie die Zahlungsart `gutschein` (A-ZAHL-1). Die Rechnung der Buchung weist nur den online gezahlten Rest aus – bei Mehrzweckgutscheinen zusätzlich die Einlösung mit dem Satz des Einlösenden (A-GUT-5). Abgelehnte Codes zählen für das Rate-Limit je Konto (A-GUT-7, Abweichung A-7).

**Files:**
- Modify: `shared/beachhub_shared/kanal.py`; Test: `shared/tests/test_kanal.py`
- Modify: `core/beachhub_core/services/tarife.py` (`preise_je_slot`)
- Modify: `core/beachhub_core/services/gutscheine.py`
- Modify: `core/beachhub_core/services/online_buchung.py` (`anfragen`, `_bestaetige`, `_bestaetigung_versenden`)
- Modify: `core/beachhub_core/services/anfragen.py` (`_buchung_anfragen`)
- Modify: `core/beachhub_core/services/benachrichtigung.py` (`buchung_bestaetigt`), `templates/mail/buchung_bestaetigt.txt`
- Create: `core/tests/test_gutschein_einloesung.py`; Modify: `core/tests/test_tarife.py`

**Interfaces:**
- Consumes: `Gutschein`, `GutscheinEinloesung`, `GutscheinFehlversuch`, `gutscheine.normalisiere`, `status_nach_verbrauch`, `protokolliere`, `anzahl_aktive_einloesungen` (Task 2); Einstellungen `gutschein_minuten`, `gutscheine_je_buchung` (Task 1); `kundengruppen.ist_mitglied_am`, `kunden.darf_online_buchen` (1a-I); `rechnungen._neue_rechnung`, `rechnungen.Posten`, `rechnungen._positionstext`, `RechnungsFehler`.
- Produces (shared): `BuchungAnfragen.gutschein_codes: list[str] = []` (je 1–40 Zeichen, höchstens 10); `Antwort.gutschein_verrechnet: Decimal | None`, `Antwort.gutschein_wieder_gueltig: bool | None`.
- Produces: `tarife.preise_je_slot(db, *, feld_id, beginn, ende, kundengruppe_id) -> list[tuple[Slot, Decimal]] | None`; `ermittle_preis` summiert darüber.
- Produces: `gutscheine.FEHLVERSUCH_GRUENDE = ("gutschein_ungueltig", "gutschein_nur_mitglieder")`, `FEHLVERSUCHE_MAX = 10`, `FEHLVERSUCH_FENSTER = timedelta(hours=1)`, `Deckung(betrag: Decimal, einloesungen: list[GutscheinEinloesung])`, `merke_fehlversuch(db, kunde)`, `loese_ein(db, buchung, kunde, codes: Sequence[str]) -> Deckung` (Gründe `gutschein_ungueltig`, `gutschein_nur_mitglieder`, `zu_viele_gutscheine`, `zu_viele_versuche`), `buche_deckung(db, buchung, betrag)`, `aktive_einloesungen(db, buchung) -> list[GutscheinEinloesung]`, `gedeckt(db, buchung) -> Decimal`, `rechnung_fuer_buchung(db, buchung, *, quelle: str) -> Rechnung | None`.
- Produces: `online_buchung.anfragen(..., gutschein_codes: Sequence[str] = ())`; `_bestaetigung_versenden(buchung_id, rechnung_id: UUID | None)`.

- [ ] **Step 1: Failing Tests in `shared`**

`shared/tests/test_kanal.py`:
```python
def test_buchung_anfragen_mit_gutscheincodes() -> None:
    basis = {
        "feld_id": str(uuid.uuid4()),
        "beginn": "2027-12-01T18:00:00Z",
        "ende": "2027-12-01T20:00:00Z",
    }
    assert kanal.BuchungAnfragen.model_validate(basis).gutschein_codes == []
    n = kanal.BuchungAnfragen.model_validate({**basis, "gutschein_codes": ["ABCD-EFGH-JKLMN"]})
    assert n.gutschein_codes == ["ABCD-EFGH-JKLMN"]
    for falsch in (["x" * 41], [""], ["A"] * 11):
        with pytest.raises(ValidationError):
            kanal.BuchungAnfragen.model_validate({**basis, "gutschein_codes": falsch})


def test_antwort_gutscheinfelder_optional() -> None:
    assert kanal.Antwort(status="ok").gutschein_verrechnet is None
    a = kanal.Antwort(status="ok", gutschein_verrechnet=Decimal("30.00"), gutschein_wieder_gueltig=True)
    assert kanal.Antwort.model_validate(a.model_dump(mode="json", exclude_none=True)) == a
```
Run: `cd shared && pytest -q` – Expected: FAIL.

- [ ] **Step 2: Vertrag in `shared`**

`shared/beachhub_shared/kanal.py`:
- Import `from typing import Annotated, Any, Literal`.
- `BuchungAnfragen` ersetzen:
```python
class BuchungAnfragen(BaseModel):
    feld_id: uuid.UUID
    beginn: AwareDatetime
    ende: AwareDatetime
    # Die Einlösung ist Teil der Buchung (A-GUT-3). Mit Vorgabe für Anfragen ohne Gutscheinfeld.
    gutschein_codes: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(
        default_factory=list, max_length=10
    )
```
- In `Antwort` nach `kostenfrei` (und den Feldern aus 1a-II):
```python
    gutschein_verrechnet: Decimal | None = None
    gutschein_wieder_gueltig: bool | None = None
```
Run: `cd shared && pytest -q` – Expected: PASS.

- [ ] **Step 3: Failing Tests im Hauptsystem**

`core/tests/test_tarife.py`:
```python
def test_preise_je_slot(db: Session, basis) -> None:
    f, privat, _ = basis
    db.add_all(
        [
            Tarif(name="Standard", preis=Decimal("30.00")),
            Tarif(name="Abend", preis=Decimal("40.00"), uhrzeit_von=time(18), uhrzeit_bis=time(23)),
        ]
    )
    db.commit()
    preise = tarife.preise_je_slot(
        db,
        feld_id=f.id,
        beginn=kombiniere(MI, time(17)),
        ende=kombiniere(MI, time(19)),
        kundengruppe_id=privat.id,
    )
    assert [(s.beginn, p) for s, p in preise] == [
        (kombiniere(MI, time(17)), Decimal("30.00")),
        (kombiniere(MI, time(18)), Decimal("40.00")),
    ]
```

`core/tests/test_gutschein_einloesung.py`:
```python
import json
import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal

from beachhub_core.models import (
    Betriebszeit,
    Buchung,
    Feld,
    FeldRaster,
    GutscheinEinloesung,
    GutscheinFehlversuch,
    Rechnung,
    Zahlung,
)
from beachhub_core.services import anfragen, gutscheine, online_buchung
from beachhub_shared import kanal
from beachhub_shared.zeit import kombiniere
from hilfen_gutschein import freicode, kaufgutschein
from sqlalchemy import select
from sqlalchemy.orm import Session

D = date(2027, 12, 1)
RUECK = "/zahlung/zurueck?anfrage=x"


def _buche(db: Session, f, k, von: int, bis: int, codes=(), tag: date = D):
    return online_buchung.anfragen(
        db,
        kunde=k,
        anfrage_id=uuid.uuid4(),
        feld_id=f.id,
        beginn=kombiniere(tag, time(von)),
        ende=kombiniere(tag, time(bis)),
        rueckkehr_url=RUECK,
        gutschein_codes=list(codes),
    )


def _bezahle(db: Session, buchung_id: uuid.UUID, betrag: str):
    z = db.scalar(
        select(Zahlung).where(Zahlung.buchung_id == buchung_id, Zahlung.provider == "fake")
    )
    rohdaten = json.dumps({"ref": z.provider_ref, "ergebnis": "bezahlt", "betrag": betrag})
    return online_buchung.zahlung_eingegangen(
        db, kanal.ZahlungEingegangen(provider="fake", rohdaten=rohdaten)
    )


def _nachlauf(db: Session, erg) -> None:
    db.commit()
    for schritt in erg.nach_commit:
        schritt(db)


def test_zwei_stunden_voll_gedeckt(db: Session, gwelt, mail_ausgang: list) -> None:
    f, k = gwelt
    g = kaufgutschein(db, k)
    db.commit()
    erg = _buche(db, f, k, 19, 21, [gutscheine.anzeige(g.code).lower()])
    _nachlauf(db, erg)
    a = erg.antwort
    assert a.status == "bestaetigt" and a.gutschein_verrechnet == Decimal("30.00")
    b = db.get(Buchung, a.buchung_id)
    assert b.zahlungsart == "gutschein" and b.rechnung_position_id is None
    assert db.query(Rechnung).count() == 0  # Einzweckgutschein: Einlösung ohne Rechnung (A-GUT-5)
    z = db.scalar(select(Zahlung).where(Zahlung.buchung_id == b.id))
    assert (z.provider, z.betrag, z.status) == ("gutschein", Decimal("30.00"), "bezahlt")
    db.refresh(g)
    assert g.status == "eingeloest" and g.inhaber_kunde_id == k.id
    (e,) = g.einloesungen
    assert (e.betrag, e.minuten_gedeckt, e.steuerbetrag) == (Decimal("30.00"), 120, None)
    assert [m["betreff"].split(":")[0] for m in mail_ausgang] == ["Buchung bestätigt"]
    assert "davon mit Gutschein gedeckt: 30,00 €" in mail_ausgang[0]["text"]


def test_drei_stunden_ein_gutschein_rest_online(db: Session, gwelt) -> None:
    f, k = gwelt
    g = kaufgutschein(db, k)
    db.commit()
    a = _buche(db, f, k, 19, 22, [g.code]).antwort
    db.commit()
    assert a.status == "reserviert" and a.zu_zahlen == Decimal("15.00")
    assert a.gutschein_verrechnet == Decimal("30.00")
    _nachlauf(db, _bezahle(db, a.buchung_id, "15.00"))
    b = db.get(Buchung, a.buchung_id)
    assert b.status == "bestaetigt" and b.zahlungsart == "online"
    r = db.query(Rechnung).one()
    assert [(p.brutto, p.buchung_id) for p in r.positionen] == [(Decimal("15.00"), b.id)]
    assert "Restbetrag nach Gutschein" in r.positionen[0].text


def test_vier_stunden_zwei_gutscheine(db: Session, gwelt) -> None:
    f, k = gwelt
    g1, g2 = kaufgutschein(db, k), kaufgutschein(db, k)
    db.commit()
    a = _buche(db, f, k, 17, 21, [g1.code, g2.code]).antwort
    db.commit()
    assert a.status == "bestaetigt" and a.gutschein_verrechnet == Decimal("60.00")
    assert sorted(e.minuten_gedeckt for e in db.query(GutscheinEinloesung)) == [120, 120]


def test_zu_viele_gutscheine_lehnt_alles_ab(db: Session, gwelt) -> None:
    f, k = gwelt
    g1, g2 = kaufgutschein(db, k), kaufgutschein(db, k)
    db.commit()
    a = _buche(db, f, k, 19, 22, [g1.code, g2.code]).antwort  # 3 h = eine volle Einheit
    db.commit()
    assert a.status == "abgelehnt" and a.grund == "zu_viele_gutscheine"
    assert db.query(Buchung).count() == 0 and db.query(GutscheinEinloesung).count() == 0
    db.refresh(g1)
    db.refresh(g2)
    assert g1.status == g2.status == "aktiv"
    assert db.query(GutscheinFehlversuch).count() == 0


def test_kurze_buchung_verbraucht_gutschein_ganz(db: Session, gwelt) -> None:
    f, k = gwelt
    g = kaufgutschein(db, k)
    db.commit()
    assert _buche(db, f, k, 19, 20, [g.code]).antwort.status == "bestaetigt"
    db.commit()
    db.refresh(g)
    (e,) = g.einloesungen
    assert g.status == "eingeloest" and (e.betrag, e.minuten_gedeckt) == (Decimal("15.00"), 60)


def test_code_nur_einmal_einloesbar(db: Session, gwelt) -> None:
    f, k = gwelt
    g = kaufgutschein(db, k)
    db.commit()
    assert _buche(db, f, k, 19, 21, [g.code]).antwort.status == "bestaetigt"
    db.commit()
    a = _buche(db, f, k, 21, 23, [g.code]).antwort
    db.commit()
    assert a.status == "abgelehnt" and a.grund == "gutschein_ungueltig"
    assert db.query(Buchung).count() == 1


def test_doppelter_code_in_einer_anfrage_zaehlt_einmal(db: Session, gwelt) -> None:
    f, k = gwelt
    g = kaufgutschein(db, k)
    db.commit()
    a = _buche(db, f, k, 19, 21, [g.code, gutscheine.anzeige(g.code).lower()]).antwort
    db.commit()
    assert a.status == "bestaetigt" and a.gutschein_verrechnet == Decimal("30.00")
    assert db.query(GutscheinEinloesung).count() == 1


def test_rate_limit_nach_zehn_fehlversuchen(db: Session, gwelt) -> None:
    f, k = gwelt
    for _ in range(gutscheine.FEHLVERSUCHE_MAX):
        a = _buche(db, f, k, 19, 21, ["ZZZZ-ZZZZ-ZZZZZ"]).antwort
        db.commit()
        assert a.grund == "gutschein_ungueltig"
    g = kaufgutschein(db, k)
    db.commit()
    a = _buche(db, f, k, 19, 21, [g.code]).antwort
    db.commit()
    assert a.grund == "zu_viele_versuche"
    db.refresh(g)
    assert g.status == "aktiv"
    assert db.query(GutscheinFehlversuch).count() == gutscheine.FEHLVERSUCHE_MAX
    # Ohne Code bucht der Kunde weiter.
    assert _buche(db, f, k, 19, 21).antwort.status == "reserviert"


def test_mitgliedsgutschein_nur_fuer_mitglieder(db: Session, gwelt) -> None:
    f, k = gwelt
    g = kaufgutschein(
        db, k, nur_mitglieder=True, ust_satz=Decimal("7.00"), kaufpreis_brutto=Decimal("26.00")
    )
    db.commit()
    a = _buche(db, f, k, 19, 21, [g.code]).antwort
    db.commit()
    assert a.grund == "gutschein_nur_mitglieder"
    assert db.query(GutscheinFehlversuch).count() == 1
    k.mitglied_bis = date(2028, 4, 30)
    db.commit()
    assert _buche(db, f, k, 19, 21, [g.code]).antwort.status == "bestaetigt"


def test_freicode_feld_zeitraum_und_mehrfach(db: Session, gwelt) -> None:
    f, k = gwelt
    f2 = Feld(name="F2", reihenfolge=2)
    f2.raster.append(FeldRaster(wochentag=None, modus="dauer", slot_minuten=60, fenster_json=[]))
    db.add(f2)
    db.flush()
    fremd = freicode(db, feld_id=f2.id)
    spaeter = freicode(db, zeitraum_von=date(2027, 12, 2))
    zweimal = freicode(db, max_einloesungen=2)
    db.commit()
    assert _buche(db, f, k, 19, 21, [fremd.code]).antwort.grund == "gutschein_ungueltig"
    assert _buche(db, f, k, 19, 21, [spaeter.code]).antwort.grund == "gutschein_ungueltig"
    db.commit()
    assert _buche(db, f, k, 19, 21, [zweimal.code]).antwort.status == "bestaetigt"
    db.commit()
    assert zweimal.status == "aktiv"
    assert _buche(db, f, k, 21, 23, [zweimal.code]).antwort.status == "bestaetigt"
    db.commit()
    assert zweimal.status == "eingeloest"
    assert _buche(db, f, k, 17, 19, [zweimal.code]).antwort.grund == "gutschein_ungueltig"


def test_wertgutschein_teil_und_rest(db: Session, gwelt) -> None:
    f, k = gwelt
    wert = {"modell": "wert", "minuten": None}
    klein = kaufgutschein(
        db, k, **wert, nennwert_brutto=Decimal("20.00"), restwert=Decimal("20.00"),
        kaufpreis_brutto=Decimal("20.00"),
    )
    gross = kaufgutschein(
        db, k, **wert, nennwert_brutto=Decimal("50.00"), restwert=Decimal("50.00"),
        kaufpreis_brutto=Decimal("50.00"),
    )
    db.commit()
    a = _buche(db, f, k, 19, 21, [klein.code]).antwort
    db.commit()
    assert a.status == "reserviert" and a.zu_zahlen == Decimal("10.00")
    assert (klein.restwert, klein.status) == (Decimal("0.00"), "eingeloest")
    assert _buche(db, f, k, 21, 23, [gross.code]).antwort.status == "bestaetigt"
    db.commit()
    assert (gross.restwert, gross.status) == (Decimal("20.00"), "teilweise_eingeloest")


def test_mehrzweckgutschein_rechnung_bei_einloesung(db: Session, gwelt) -> None:
    f, k = gwelt
    g = kaufgutschein(db, k, steuer="bei_einloesung", kaufpreis_brutto=Decimal("28.00"))
    db.commit()
    erg = _buche(db, f, k, 19, 21, [g.code])
    _nachlauf(db, erg)
    b = db.get(Buchung, erg.antwort.buchung_id)
    r = db.query(Rechnung).one()
    (p,) = r.positionen
    assert (p.brutto, p.ust_satz, p.buchung_id) == (Decimal("28.00"), Decimal("19.00"), None)
    assert "Mehrzweckgutschein" in p.text and r.status == "bezahlt"
    e = db.query(GutscheinEinloesung).one()
    assert e.steuerbetrag == Decimal("28.00") and e.rechnung_position_id == p.id
    assert b.rechnung_position_id is None


def test_abgelaufen_gesperrt_unbezahlt_ungueltig(db: Session, gwelt) -> None:
    f, k = gwelt
    codes = [
        kaufgutschein(db, k, gueltig_bis=date(2027, 11, 24)).code,
        kaufgutschein(db, k, status="gesperrt").code,
        kaufgutschein(db, k, status="offen").code,
    ]
    db.commit()
    for code in codes:
        assert _buche(db, f, k, 19, 21, [code]).antwort.grund == "gutschein_ungueltig"
        db.commit()


def test_buchung_anfragen_ueber_den_kanal(db: Session, gwelt) -> None:
    f, k = gwelt
    k.portal_konto_id = uuid.uuid4()
    g = kaufgutschein(db, k)
    db.commit()
    anfrage = kanal.Anfrage(
        anfrage_id=uuid.uuid4(),
        typ="buchung_anfragen",
        konto_id=k.portal_konto_id,
        nutzlast={
            "feld_id": str(f.id),
            "beginn": kombiniere(D, time(19)).isoformat(),
            "ende": kombiniere(D, time(21)).isoformat(),
            "gutschein_codes": [gutscheine.anzeige(g.code)],
        },
        erstellt_am=datetime.now(UTC),
    )
    antwort, _ = anfragen.bearbeite(db, anfrage)
    assert antwort.status == "bestaetigt" and antwort.gutschein_verrechnet == Decimal("30.00")
```
(Der Import `Betriebszeit` wird von `ruff check --fix` entfernt, falls ungenutzt.)

- [ ] **Step 4: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_gutschein_einloesung.py tests/test_tarife.py`
Expected: FAIL – `TypeError: anfragen() got an unexpected keyword argument 'gutschein_codes'`, `AttributeError: … 'preise_je_slot'`.

- [ ] **Step 5: Preise je Slot**

`core/beachhub_core/services/tarife.py`, `ermittle_preis` ersetzen:
```python
def preise_je_slot(
    db: Session,
    *,
    feld_id: uuid.UUID,
    beginn: datetime,
    ende: datetime,
    kundengruppe_id: uuid.UUID | None,
) -> list[tuple[Slot, Decimal]] | None:
    """Preis je Slot des Zeitraums – None, wenn er keine Slotfolge ist oder ein Slot keinen Tarif
    hat. Die Gutscheindeckung (A-GUT-1) rechnet slotweise."""
    feld = db.get(Feld, feld_id)
    if feld is None:
        return None
    tages = slots_db.tages_slots(db, feld, lokales_datum(beginn))
    if not zeitraum_ist_slotfolge(beginn, ende, tages):
        return None
    out: list[tuple[Slot, Decimal]] = []
    for slot in slots_im_zeitraum(beginn, ende, tages):
        regel = regel_fuer_slot(db, feld_id=feld_id, slot=slot, kundengruppe_id=kundengruppe_id)
        if regel is None:
            return None
        out.append((slot, regel.preis))
    return out


def ermittle_preis(
    db: Session,
    *,
    feld_id: uuid.UUID,
    beginn: datetime,
    ende: datetime,
    kundengruppe_id: uuid.UUID | None,
) -> Decimal | None:
    preise = preise_je_slot(
        db, feld_id=feld_id, beginn=beginn, ende=ende, kundengruppe_id=kundengruppe_id
    )
    return None if preise is None else sum((p for _, p in preise), Decimal("0.00"))
```

- [ ] **Step 6: Einlösung im Dienst**

`core/beachhub_core/services/gutscheine.py`:
- Importe ergänzen: `from collections.abc import Sequence`, `from dataclasses import dataclass, field`, `from beachhub_shared.zeit import lokal, lokales_datum`, `from beachhub_core.models import Buchung, Feld, Gutschein, GutscheinEinloesung, GutscheinFehlversuch, Kunde, Rechnung, Zahlung, utcnow`, `from beachhub_core.services import audit, konfiguration, kundengruppen, rechnungen, tarife`, `from beachhub_core.services.rechnungen import RechnungsFehler, csv_sicher`.
- Unter `STATUS_TEXT`:
```python
# Abgelehnte Codes, die für das Rate-Limit zählen (A-GUT-7, Abweichung A-7).
FEHLVERSUCH_GRUENDE: tuple[str, ...] = ("gutschein_ungueltig", "gutschein_nur_mitglieder")
FEHLVERSUCHE_MAX = 10
FEHLVERSUCH_FENSTER = timedelta(hours=1)


@dataclass
class Deckung:
    betrag: Decimal = NULL
    einloesungen: list[GutscheinEinloesung] = field(default_factory=list)
```
- Am Dateiende:
```python
def merke_fehlversuch(db: Session, kunde: Kunde) -> None:
    db.add(GutscheinFehlversuch(kunde_id=kunde.id, zeitpunkt=clock.now(db)))
    db.flush()


def _zu_viele_fehlversuche(db: Session, kunde: Kunde) -> bool:
    seit = clock.now(db) - FEHLVERSUCH_FENSTER
    anzahl = db.scalar(
        select(func.count())
        .select_from(GutscheinFehlversuch)
        .where(GutscheinFehlversuch.kunde_id == kunde.id, GutscheinFehlversuch.zeitpunkt >= seit)
    )
    return int(anzahl or 0) >= FEHLVERSUCHE_MAX


def _pruefe(db: Session, g: Gutschein, kunde: Kunde, buchung: Buchung, heute: date) -> None:
    """Jeder Code einzeln (A-GUT-1, -1b, -2). Gültig muss er am Tag der Einlösung sein
    (Abweichung A-9); Mitgliedschaft, Feld und Zeitraum zählen am Tag des Termins."""
    tag = lokales_datum(buchung.beginn)
    if g.status not in Gutschein.EINLOESBAR or g.gueltig_bis < heute:
        raise GutscheinFehler("gutschein_ungueltig")
    if g.art == Gutschein.FREI:
        if g.feld_id is not None and g.feld_id != buchung.feld_id:
            raise GutscheinFehler("gutschein_ungueltig")
        if g.zeitraum_von is not None and tag < g.zeitraum_von:
            raise GutscheinFehler("gutschein_ungueltig")
        if g.zeitraum_bis is not None and tag > g.zeitraum_bis:
            raise GutscheinFehler("gutschein_ungueltig")
        if anzahl_aktive_einloesungen(db, g) >= g.max_einloesungen:
            raise GutscheinFehler("gutschein_ungueltig")
    elif g.nur_mitglieder and not kundengruppen.ist_mitglied_am(kunde, tag):
        raise GutscheinFehler("gutschein_nur_mitglieder")


def _pruefe_anzahl(db: Session, buchung: Buchung, einheiten: int, werte: int) -> None:
    """Höchstens ein Gutschein je volle Buchungseinheit, mindestens einer (A-GUT-1, A-11)."""
    dauer = int((buchung.ende - buchung.beginn).total_seconds()) // 60
    volle = max(1, dauer // konfiguration.hole(db, "gutschein_minuten"))
    grenze = konfiguration.hole(db, "gutscheine_je_buchung").zahl
    if einheiten > (volle if grenze is None else min(volle, grenze)):
        raise GutscheinFehler("zu_viele_gutscheine")
    if grenze is not None and einheiten + werte > grenze:
        raise GutscheinFehler("zu_viele_gutscheine")


def _einloese(
    db: Session,
    g: Gutschein,
    buchung: Buchung,
    kunde: Kunde,
    betrag: Decimal,
    minuten: int | None,
) -> GutscheinEinloesung:
    vorher = audit.als_dict(g)
    steuerbetrag = None
    if g.art == Gutschein.KAUF and g.steuer == Gutschein.BEI_EINLOESUNG:
        # Mehrzweckgutschein: Bemessungsgrundlage ist die gezahlte Gegenleistung (A-6).
        steuerbetrag = betrag if g.modell == Gutschein.WERT else g.kaufpreis_brutto
    e = GutscheinEinloesung(
        gutschein_id=g.id,
        buchung_id=buchung.id,
        kunde_id=kunde.id,
        minuten_gedeckt=minuten,
        betrag=betrag,
        steuerbetrag=steuerbetrag,
        zeitpunkt=utcnow(),
    )
    db.add(e)
    if g.art == Gutschein.KAUF and g.modell == Gutschein.WERT:
        g.restwert = (g.restwert or NULL) - betrag
    # Wer einlöst, ist ab jetzt Inhaber – auch eines geschenkten Gutscheins (A-GUT-6).
    g.inhaber_kunde_id = kunde.id
    db.flush()
    g.status = status_nach_verbrauch(db, g)
    protokolliere(db, g, vorher, "eingeloest", quelle="portal")
    return e


def loese_ein(db: Session, buchung: Buchung, kunde: Kunde, codes: Sequence[str]) -> Deckung:
    """Verrechnet Gutscheine in der Buchungstransaktion (A-GUT-3). Der Aufrufer ruft im selben
    Savepoint wie `buchungen.lege_an`; ein GutscheinFehler nimmt beides zurück. Einheiten und
    Freischaltcodes decken die Slots lückenlos vom Beginn an, Wertgutscheine danach den Rest
    (Abweichung A-10). Die Reihenfolge der Eingabe bestimmt die Reihenfolge der Deckung."""
    normal = list(dict.fromkeys(c for c in (normalisiere(roh) for roh in codes) if c))
    if not normal:
        return Deckung()
    if _zu_viele_fehlversuche(db, kunde):
        raise GutscheinFehler("zu_viele_versuche")
    # Nach id sortiert sperren: Zwei Buchungen mit denselben Codes warten aufeinander statt
    # sich gegenseitig zu blockieren.
    gesperrt = db.scalars(
        select(Gutschein)
        .where(Gutschein.code.in_(normal))
        .order_by(Gutschein.id)
        .with_for_update()
    ).all()
    je_code = {g.code: g for g in gesperrt}
    if len(je_code) != len(normal):
        raise GutscheinFehler("gutschein_ungueltig")
    reihe = [je_code[c] for c in normal]
    heute = clock.today(db)
    for g in reihe:
        _pruefe(db, g, kunde, buchung, heute)
    einheiten = [g for g in reihe if g.art == Gutschein.FREI or g.modell == Gutschein.EINHEIT]
    werte = [g for g in reihe if g.art == Gutschein.KAUF and g.modell == Gutschein.WERT]
    _pruefe_anzahl(db, buchung, len(einheiten), len(werte))
    preise = tarife.preise_je_slot(
        db,
        feld_id=buchung.feld_id,
        beginn=buchung.beginn,
        ende=buchung.ende,
        kundengruppe_id=buchung.kundengruppe_id,
    )
    if preise is None:  # lege_an hat den Preis eben ermittelt; das kann nicht vorkommen
        raise GutscheinFehler("gutschein_ungueltig")
    deckung = Deckung()
    offen = list(preise)
    for g in einheiten:
        grenze = offen[0][0].beginn + timedelta(minutes=g.minuten or 0) if offen else buchung.ende
        anzahl = 0
        for slot, _ in offen:
            if slot.ende > grenze:
                break
            anzahl += 1
        if anzahl == 0:
            raise GutscheinFehler("zu_viele_gutscheine")
        gedeckt, offen = offen[:anzahl], offen[anzahl:]
        minuten = int(sum((s.ende - s.beginn).total_seconds() for s, _ in gedeckt)) // 60
        betrag = sum((p for _, p in gedeckt), NULL)
        deckung.einloesungen.append(_einloese(db, g, buchung, kunde, betrag, minuten))
    rest = sum((p for _, p in offen), NULL)
    for g in werte:
        if rest <= NULL or g.restwert is None or g.restwert <= NULL:
            raise GutscheinFehler("zu_viele_gutscheine")
        betrag = min(g.restwert, rest)
        rest -= betrag
        deckung.einloesungen.append(_einloese(db, g, buchung, kunde, betrag, None))
    deckung.betrag = sum((e.betrag for e in deckung.einloesungen), NULL)
    return deckung


def buche_deckung(db: Session, buchung: Buchung, betrag: Decimal) -> None:
    """Der Gutscheinanteil als Zahlung (`provider = gutschein`): Guthaben, Gutscheine und
    Restzahlung einer Buchung ergeben so stets ihren Preis (§ 5, Tabelle `zahlung`)."""
    db.add(
        Zahlung(
            kunde_id=buchung.kunde_id,
            buchung_id=buchung.id,
            provider="gutschein",
            provider_ref=f"gutschein:{buchung.id}",
            betrag=betrag,
            status=Zahlung.BEZAHLT,
            empfangen_am=utcnow(),
        )
    )
    if betrag >= buchung.preis:
        buchung.zahlungsart = "gutschein"  # vollständig durch Gutscheine gedeckt (A-ZAHL-1)
    db.flush()


def aktive_einloesungen(db: Session, buchung: Buchung) -> list[GutscheinEinloesung]:
    return list(
        db.scalars(
            select(GutscheinEinloesung)
            .where(
                GutscheinEinloesung.buchung_id == buchung.id,
                GutscheinEinloesung.rueckgaengig_am.is_(None),
            )
            .order_by(GutscheinEinloesung.zeitpunkt)
        ).all()
    )


def gedeckt(db: Session, buchung: Buchung) -> Decimal:
    return sum((e.betrag for e in aktive_einloesungen(db, buchung)), NULL)


def rechnung_fuer_buchung(db: Session, buchung: Buchung, *, quelle: str) -> Rechnung | None:
    """Rechnung einer online bezahlten Buchung (A-RECH-2) mit Gutscheinanteil (A-GUT-5): eine
    Position über den Rest nach Gutscheinen (mit der Buchung verknüpft) und je Einlösung eines
    Mehrzweckgutscheins eine Position mit dem Satz des Einlösenden. Ist alles mit
    Einzweckgutscheinen gedeckt, gibt es keine Rechnung – nur die Buchungsbestätigung."""
    if buchung.rechnung_position_id is not None:
        raise RechnungsFehler("bereits_berechnet")
    einloesungen = aktive_einloesungen(db, buchung)
    posten: list[rechnungen.Posten] = []
    rest = buchung.preis - sum((e.betrag for e in einloesungen), NULL)
    if rest > NULL:
        zusatz = " (Restbetrag nach Gutschein)" if einloesungen else ""
        posten.append(
            rechnungen.Posten(
                buchung, rechnungen._positionstext(buchung, zusatz), rest, buchung.ust_satz
            )
        )
    mehrzweck = [e for e in einloesungen if e.steuerbetrag is not None and e.steuerbetrag > NULL]
    for e in mehrzweck:
        text = rechnungen._positionstext(
            buchung, f", Einlösung Mehrzweckgutschein …{e.gutschein.code[-4:]}"
        )
        posten.append(rechnungen.Posten(None, text, e.steuerbetrag or NULL, buchung.ust_satz))
    if not posten:
        return None
    tag = lokales_datum(buchung.beginn)
    r = rechnungen._neue_rechnung(
        db, buchung.kunde, "einzel", posten, tag, tag, "bezahlt", quelle=quelle
    )
    for e, pos in zip(mehrzweck, r.positionen[len(posten) - len(mehrzweck) :], strict=True):
        e.rechnung_position_id = pos.id
    db.flush()
    return r
```

- [ ] **Step 7: Einlösung in der Online-Buchung**

`core/beachhub_core/services/online_buchung.py`:
- Importe: `from collections.abc import Sequence`; `gutscheine` in die Service-Importe.
- `_bestaetigung_versenden` ersetzen (Rest der Funktion ab `nummer = r.nummer` bleibt):
```python
def _bestaetigung_versenden(buchung_id: uuid.UUID, rechnung_id: uuid.UUID | None) -> Nachlauf:
    def lauf(db: Session) -> None:
        b = db.get(Buchung, buchung_id)
        if b is None:
            return
        benachrichtigung.buchung_bestaetigt(db, b)
        # Voll mit Einzweckgutscheinen gedeckt: keine Rechnung (A-GUT-5).
        r = db.get(Rechnung, rechnung_id) if rechnung_id is not None else None
        if r is None:
            return
        nummer = r.nummer
        ...
```
- `_bestaetige` ersetzen:
```python
def _bestaetige(db: Session, b: Buchung, antwort: kanal.Antwort) -> Ergebnis:
    buchungen.setze_status(db, b, Buchung.BESTAETIGT, quelle="portal")
    b.reserviert_bis = None
    r = gutscheine.rechnung_fuer_buchung(db, b, quelle="portal")
    return Ergebnis(antwort, [_bestaetigung_versenden(b.id, r.id if r else None)])
```
- In `anfragen`: Parameter `gutschein_codes: Sequence[str] = (),` an die Signatur anhängen und den Teil von `try:` bis zur Berechnung von `rest` ersetzen:
```python
    try:
        # Buchung und Gutscheine im selben Savepoint (A-GUT-3): Scheitert ein Code, verschwindet
        # auch die Buchung; von zwei Kunden mit demselben Code gewinnt genau einer.
        with db.begin_nested():
            b = buchungen.lege_an(
                db,
                feld_id=feld_id,
                kunde_id=kunde.id,
                beginn=beginn,
                ende=ende,
                quelle="portal",
                pruefe_fenster=True,
                status=Buchung.RESERVIERT,
                anfrage_id=anfrage_id,
                zahlungsart="online",
            )
            deckung = gutscheine.loese_ein(db, b, kunde, gutschein_codes)
    except buchungen.BuchungsFehler as e:
        return abgelehnt(_GRUND.get(e.grund, e.grund))
    except IntegrityError:
        # Exklusionsconstraint: Ein gleichzeitiger Kunde war schneller.
        return abgelehnt("belegt")
    except gutscheine.GutscheinFehler as e:
        if e.grund in gutscheine.FEHLVERSUCH_GRUENDE:
            gutscheine.merke_fehlversuch(db, kunde)
        return abgelehnt(e.grund)

    if deckung.betrag > NULL:
        gutscheine.buche_deckung(db, b, deckung.betrag)
    gutschein_verrechnet = deckung.betrag if deckung.betrag > NULL else None
    db.refresh(kunde)
    offen = b.preis - deckung.betrag
    verrechnet = max(NULL, min(kunde.guthaben, offen))
    if verrechnet > NULL:
        guthaben.buche(
            db,
            kunde=kunde,
            betrag=-verrechnet,
            art="verrechnung",
            bezug_id=b.id,
            notiz="Verrechnung mit Portal-Buchung",
            quelle="portal",
        )
    rest = offen - verrechnet
```
  In den beiden folgenden `kanal.Antwort(...)` (Status `bestaetigt` und `reserviert`) jeweils `gutschein_verrechnet=gutschein_verrechnet,` ergänzen. Der Rest der Funktion bleibt.

`core/beachhub_core/services/anfragen.py`, in `_buchung_anfragen` beim Aufruf `online_buchung.anfragen(...)` `gutschein_codes=n.gutschein_codes,` ergänzen.

`core/beachhub_core/services/benachrichtigung.py`, `buchung_bestaetigt` ersetzen:
```python
def buchung_bestaetigt(db: Session, b: Buchung) -> None:
    from beachhub_core.services import gutscheine  # Zyklus vermeiden

    vorlauf = konfiguration.hole(db, "zutritt_vorlauf_minuten")
    mail.sende(
        b.kunde.email,
        f"Buchung bestätigt: {templates.env.filters['lokal'](b.beginn)}",
        _text(
            "buchung_bestaetigt",
            b=b,
            pin=pin.entschluessele(b.pin_verschluesselt or ""),
            vorlauf=vorlauf,
            gutschein=gutscheine.gedeckt(db, b),
        ),
    )
```

`core/beachhub_core/templates/mail/buchung_bestaetigt.txt`, die Zeile `Preis: {{ b.preis|euro }}` ersetzen durch:
```
Preis: {{ b.preis|euro }}{% if gutschein %} (davon mit Gutschein gedeckt: {{ gutschein|euro }}){% endif %}
```

- [ ] **Step 8: Tests und Lint**

Run:
```bash
(cd shared && pytest -q) && (cd core && pytest -q) && (cd portal && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS – auch die bestehenden Online-Buchungstests: Ohne Codes ist `Deckung().betrag` null, und `rechnung_fuer_buchung` erzeugt dieselbe Einzelrechnung wie bisher.

- [ ] **Step 9: Commit**

```bash
git add shared core
git commit -m "feat(core): Gutscheine und Freischaltcodes in der Buchung einlösen"
```

---
## Task 5: Storno, Kulanz und Verfall geben Gutscheine zurück

Wird eine mit Gutschein gedeckte Buchung kostenfrei storniert (innerhalb der Frist, durch Kulanz oder weil eine Reservierung verfällt), werden alle Einlösungen der Buchung rückgängig gemacht: Jeder Gutschein ist wieder einlösbar, mit seinem ursprünglichen Ablaufdatum und dem Einlösenden als Inhaber (A-GUT-6). Es entsteht kein Guthaben aus Gutscheinen (A-STORNO-7); ein online gezahlter Aufpreis läuft den normalen Weg über `storno.gutschreiben` (1a-II, A-STORNO-6). Mehrzweck-Positionen der Rechnung werden mit einem Korrekturbeleg aufgehoben. Nach der Frist bleibt der Gutschein verbraucht. Der Kunde bekommt eine Mail „Ihr Gutschein ist wieder gültig“ (A-MAIL-2, Abweichung A-14), das Portal das Kennzeichen `gutschein_wieder_gueltig`.

**Files:**
- Modify: `core/beachhub_core/services/gutscheine.py`
- Modify: `core/beachhub_core/services/storno.py` (`storniere`, `kulanz`)
- Modify: `core/beachhub_core/services/online_buchung.py` (`storniere_fuer_kunde`, `verfalle_abgelaufene`)
- Modify: `core/beachhub_core/services/benachrichtigung.py` (`storno`, neu `gutscheine_wieder_gueltig`)
- Create: `core/beachhub_core/templates/mail/gutschein_wieder_gueltig.txt`
- Modify: `core/beachhub_core/routes/belegung.py` (`buchung_kulanz`), `core/beachhub_core/jobs.py` (`verfall_ausfuehren`)
- Test: `core/tests/test_gutschein_einloesung.py`

**Interfaces:**
- Consumes: `gutscheine.aktive_einloesungen`, `status_nach_verbrauch`, `protokolliere` (Tasks 2, 4); aus 1a-II `rechnungen.korrigiere(db, positionen, *, grund, quelle) -> Rechnung`, `RechnungPosition.korrigiert_durch_id`, `Rechnung.korrigiert_rechnung_id`, `storno.gutschreiben` (wird von `storniere`/`kulanz` schon gerufen).
- Produces: `gutscheine.storno_rueckgaengig(db, buchung, *, grund: str) -> list[Gutschein]`, `gutscheine.wieder_gueltige(db, buchung) -> list[Gutschein]`.
- Produces: `benachrichtigung.gutscheine_wieder_gueltig(db, buchung) -> None` (Betreff „Ihr Gutschein ist wieder gültig“, nur wenn es solche Gutscheine gibt).
- Produces: `kanal.Antwort.gutschein_wieder_gueltig = True` in der Antwort auf `buchung_stornieren`, wenn Gutscheine zurückgegeben wurden.

- [ ] **Step 1: Failing Tests schreiben**

In `core/tests/test_gutschein_einloesung.py` (Importe ergänzen: `from beachhub_core import clock, jobs`, `RechnungPosition` in die Modell-Importe, `storno` in die Service-Importe):
```python
def test_storno_kostenfrei_gibt_gutschein_zurueck(
    db: Session, gwelt, mail_ausgang: list
) -> None:
    f, k = gwelt
    g = kaufgutschein(db, k)
    db.commit()
    a = _buche(db, f, k, 19, 22, [g.code]).antwort
    db.commit()
    _nachlauf(db, _bezahle(db, a.buchung_id, "15.00"))
    mail_ausgang.clear()
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id)
    _nachlauf(db, erg)
    assert erg.antwort.kostenfrei is True and erg.antwort.gutschein_wieder_gueltig is True
    db.refresh(g)
    assert g.status == "aktiv" and g.gueltig_bis == date(2029, 11, 25)
    assert g.inhaber_kunde_id == k.id
    assert db.query(GutscheinEinloesung).one().rueckgaengig_am is not None
    z = db.scalar(select(Zahlung).where(Zahlung.provider == "gutschein"))
    assert z.status == "abgebrochen"
    db.refresh(k)
    assert k.guthaben == Decimal("15.00")  # nur der online gezahlte Rest wird Guthaben
    assert "Ihr Gutschein ist wieder gültig" in [m["betreff"] for m in mail_ausgang]
    assert gutscheine.anzeige(g.code) in mail_ausgang[-1]["text"]
    assert _buche(db, f, k, 19, 21, [g.code]).antwort.status == "bestaetigt"


def test_storno_nach_frist_gutschein_bleibt_verbraucht(db: Session, gwelt, monkeypatch) -> None:
    f, k = gwelt
    g = kaufgutschein(db, k)
    db.commit()
    a = _buche(db, f, k, 19, 21, [g.code]).antwort
    db.commit()
    # 23 h vor Beginn: innerhalb der Stornofrist von 24 h, also kostenpflichtig.
    monkeypatch.setattr(
        online_buchung.clock, "now", lambda db: kombiniere(date(2027, 11, 30), time(20))
    )
    erg = online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id)
    db.commit()
    assert erg.antwort.kostenfrei is False and not erg.antwort.gutschein_wieder_gueltig
    db.refresh(g)
    assert g.status == "eingeloest"
    monkeypatch.undo()
    # Kulanz des Betreibers stellt das Storno frei und gibt den Gutschein zurück (A-STORNO-4).
    b = db.get(Buchung, a.buchung_id)
    storno.kulanz(db, b.storno, admin_user_id=None, grund="Krankheit")
    db.commit()
    db.refresh(g)
    assert g.status == "aktiv"


def test_verfall_der_reservierung_gibt_gutschein_zurueck(
    db: Session, gwelt, mail_ausgang: list
) -> None:
    f, k = gwelt
    g = kaufgutschein(db, k)
    db.commit()
    assert _buche(db, f, k, 19, 22, [g.code]).antwort.status == "reserviert"
    db.commit()
    clock.set_override(db, date(2027, 11, 26))
    assert jobs.verfall_ausfuehren(db) == 1
    db.refresh(g)
    assert g.status == "aktiv"
    assert "Ihr Gutschein ist wieder gültig" in [m["betreff"] for m in mail_ausgang]


def test_mehrzweck_storno_korrigiert_einloesungsrechnung(db: Session, gwelt) -> None:
    f, k = gwelt
    g = kaufgutschein(db, k, steuer="bei_einloesung", kaufpreis_brutto=Decimal("28.00"))
    db.commit()
    a = _buche(db, f, k, 19, 21, [g.code]).antwort
    db.commit()
    _nachlauf(db, online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id))
    pos = (
        db.query(RechnungPosition)
        .filter(RechnungPosition.korrigiert_durch_id.is_not(None))
        .one()
    )
    assert pos.brutto == Decimal("28.00")
    korrektur = db.scalar(
        select(Rechnung).where(Rechnung.korrigiert_rechnung_id == pos.rechnung_id)
    )
    assert korrektur is not None and korrektur.brutto == Decimal("-28.00")
    db.refresh(k)
    assert k.guthaben == Decimal("0.00")
    db.refresh(g)
    assert g.status == "aktiv"


def test_wertgutschein_storno_bucht_wert_zurueck(db: Session, gwelt) -> None:
    f, k = gwelt
    g = kaufgutschein(
        db, k, modell="wert", minuten=None, nennwert_brutto=Decimal("50.00"),
        restwert=Decimal("50.00"), kaufpreis_brutto=Decimal("50.00"),
    )
    db.commit()
    a = _buche(db, f, k, 19, 21, [g.code]).antwort
    db.commit()
    assert g.restwert == Decimal("20.00")
    _nachlauf(db, online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id))
    db.refresh(g)
    assert (g.restwert, g.status) == (Decimal("50.00"), "aktiv")
    db.refresh(k)
    assert k.guthaben == Decimal("0.00")


def test_freicode_nach_storno_wieder_einloesbar(db: Session, gwelt) -> None:
    f, k = gwelt
    g = freicode(db)
    db.commit()
    a = _buche(db, f, k, 19, 21, [g.code]).antwort
    db.commit()
    assert g.status == "eingeloest"
    _nachlauf(db, online_buchung.storniere_fuer_kunde(db, kunde=k, buchung_id=a.buchung_id))
    db.refresh(g)
    assert g.status == "aktiv"
    assert _buche(db, f, k, 19, 21, [g.code]).antwort.status == "bestaetigt"
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_gutschein_einloesung.py`
Expected: FAIL – Gutschein bleibt `eingeloest`, `gutschein_wieder_gueltig` ist `None`.

- [ ] **Step 3: Rückgängig im Dienst**

`core/beachhub_core/services/gutscheine.py` (Import `RechnungPosition` in die Modell-Importe), am Dateiende:
```python
def storno_rueckgaengig(db: Session, buchung: Buchung, *, grund: str) -> list[Gutschein]:
    """Macht die Einlösungen einer kostenfrei stornierten oder verfallenen Buchung rückgängig
    (A-GUT-6, A-STORNO-7): Jeder Gutschein ist wieder einlösbar – mit seinem ursprünglichen
    Ablaufdatum und dem Einlösenden als Inhaber; im Modell `wert` kehrt der Wert auf den Gutschein
    zurück (Abweichung A-5). Guthaben entsteht daraus nie. Mehrzweck-Positionen der Rechnung
    werden korrigiert, weil die Leistung nicht erbracht wird. Nach der Frist ruft niemand diese
    Funktion: Der Gutschein bleibt verbraucht."""
    einloesungen = aktive_einloesungen(db, buchung)
    if not einloesungen:
        return []
    gesperrt = db.scalars(
        select(Gutschein)
        .where(Gutschein.id.in_({e.gutschein_id for e in einloesungen}))
        .order_by(Gutschein.id)
        .with_for_update()
    ).all()
    je_id = {g.id: g for g in gesperrt}
    jetzt = utcnow()
    zu_korrigieren: dict[uuid.UUID, list[RechnungPosition]] = {}
    for e in einloesungen:
        g = je_id[e.gutschein_id]
        vorher = audit.als_dict(g)
        e.rueckgaengig_am = jetzt
        e.rueckgaengig_grund = grund[:200]
        if g.art == Gutschein.KAUF and g.modell == Gutschein.WERT:
            g.restwert = (g.restwert or NULL) + e.betrag
        g.inhaber_kunde_id = e.kunde_id
        db.flush()
        if g.status in (*Gutschein.EINLOESBAR, Gutschein.EINGELOEST):
            g.status = status_nach_verbrauch(db, g)
        protokolliere(db, g, vorher, "einloesung_rueckgaengig", quelle="system")
        if e.rechnung_position_id is not None:
            pos = db.get(RechnungPosition, e.rechnung_position_id)
            if pos is not None and pos.korrigiert_durch_id is None:
                zu_korrigieren.setdefault(pos.rechnung_id, []).append(pos)
    for positionen in zu_korrigieren.values():
        rechnungen.korrigiere(
            db, positionen, grund=f"Gutschein wieder gültig ({grund})", quelle="system"
        )
    zahlung = db.scalar(select(Zahlung).where(Zahlung.provider_ref == f"gutschein:{buchung.id}"))
    if zahlung is not None:
        zahlung.status = Zahlung.ABGEBROCHEN
    db.flush()
    return list(gesperrt)


def wieder_gueltige(db: Session, buchung: Buchung) -> list[Gutschein]:
    """Gutscheine, deren Einlösung für diese Buchung rückgängig gemacht wurde und die wieder
    einlösbar sind – für die Mail an den Kunden und die Antwort ans Portal."""
    return list(
        db.scalars(
            select(Gutschein)
            .join(GutscheinEinloesung, GutscheinEinloesung.gutschein_id == Gutschein.id)
            .where(
                GutscheinEinloesung.buchung_id == buchung.id,
                GutscheinEinloesung.rueckgaengig_am.is_not(None),
                Gutschein.status.in_(Gutschein.EINLOESBAR),
            )
            .distinct()
            .order_by(Gutschein.code)
        ).all()
    )
```

- [ ] **Step 4: Storno, Kulanz, Verfall**

`core/beachhub_core/services/storno.py`:
- In `storniere`: nachdem das Storno angelegt und – bei kostenfreiem Storno – `gutschreiben` gerufen ist (also nach der Stelle, an der `storniere` über „kostenfrei“ entschieden und die Gutschrift erledigt hat, vor dem Audit-Eintrag des Stornos), ergänzen:
```python
    if s.kostenfrei:
        from beachhub_core.services import gutscheine  # Zyklus über services/__init__ vermeiden

        # Mit Gutschein gedeckte Anteile kehren auf den Gutschein zurück (A-GUT-6, A-STORNO-7).
        gutscheine.storno_rueckgaengig(db, buchung, grund="Storno")
```
- In `kulanz`: unmittelbar nachdem `s.kostenfrei = True` gesetzt und die Gutschrift erledigt ist:
```python
    from beachhub_core.services import gutscheine  # Zyklus über services/__init__ vermeiden

    gutscheine.storno_rueckgaengig(db, s.buchung, grund="Kulanz")
```

`core/beachhub_core/services/online_buchung.py`:
- In `verfalle_abgelaufene` in der Schleife nach `_buche_verrechnung_zurueck(db, b, quelle="system")`:
```python
        gutscheine.storno_rueckgaengig(db, b, grund="Reservierung verfallen")
```
- In `storniere_fuer_kunde` die Antwort für den Erfolgsfall (Status `ok`, mit `kostenfrei=…` und den Feldern aus 1a-II) um das Feld ergänzen:
```python
        gutschein_wieder_gueltig=True if gutscheine.wieder_gueltige(db, b) else None,
```

`core/beachhub_core/services/benachrichtigung.py`:
```python
def gutscheine_wieder_gueltig(db: Session, b: Buchung) -> None:
    from beachhub_core.services import gutscheine  # Zyklus vermeiden

    zurueck = gutscheine.wieder_gueltige(db, b)
    if not zurueck:
        return
    mail.sende(
        b.kunde.email,
        "Ihr Gutschein ist wieder gültig",
        _text("gutschein_wieder_gueltig", b=b, gutscheine=zurueck),
    )
```
und am Ende von `storno(db, s)` (nach dem Versand der Stornomail):
```python
    gutscheine_wieder_gueltig(db, s.buchung)
```

`core/beachhub_core/templates/mail/gutschein_wieder_gueltig.txt`:
```
Hallo {{ b.kunde.name }},

Ihre Buchung am {{ b.beginn|lokal }} findet nicht statt. {% if gutscheine|length == 1 %}Der Gutschein, mit dem Sie sie bezahlt hatten, ist{% else %}Die Gutscheine, mit denen Sie sie bezahlt hatten, sind{% endif %} wieder gültig:

{% for g in gutscheine %}  {{ g.code|code }} – gültig bis {{ g.gueltig_bis|datum }}
{% endfor %}
Sie können {{ "ihn" if gutscheine|length == 1 else "sie" }} bei Ihrer nächsten Buchung im Portal einlösen.

Viele Grüße
{{ betreiber }}
```

`core/beachhub_core/routes/belegung.py`, in `buchung_kulanz` nach dem erfolgreichen `db.commit()` (vor dem Redirect):
```python
    benachrichtigung.gutscheine_wieder_gueltig(db, b)
```

`core/beachhub_core/jobs.py`, in `verfall_ausfuehren` in der Schleife nach `benachrichtigung.zahlungsfrist_abgelaufen(db, b)`:
```python
        benachrichtigung.gutscheine_wieder_gueltig(db, b)
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
git commit -m "feat(core): Gutscheine bei kostenfreiem Storno, Kulanz und Verfall zurückgeben"
```

---
## Task 6: Gutscheinkauf über den Kanal

Der Kunde kauft im Portal einen Gutschein (`gutschein_kaufen`, A-GUT-8: nur mit Konto). Das Hauptsystem legt ihn im Zustand `offen` an – mit Preis, Gruppe und Satz des Käufers am Kaufdatum (A-GUT-1a), Modell und Steuervariante aus den Einstellungen – und öffnet eine Bezahlsitzung. Mit dem Zahlungseingang wird er `aktiv`, bekommt seine Kaufrechnung (Einzweckgutschein, Satz des Käufers) bzw. seinen Kaufbeleg (Mehrzweckgutschein, ohne Steuer, eigener Nummernkreis; A-GUT-5), und Code samt Rechnung gehen an den Käufer, der Code auf Wunsch auch an einen Empfänger (A-MAIL-2). Rechnungskunden kaufen nur, wenn `rechnungskunden_gutscheinkauf` an ist (A-KUND-7). Unbezahlte Käufe brechen nach der Zahlungsfrist ab; eine spätere Zahlung wird Guthaben, der Betreiber bekommt eine Mail.

**Files:**
- Modify: `shared/beachhub_shared/kanal.py`; Test: `shared/tests/test_kanal.py`
- Create: `core/beachhub_core/services/gutschein_kauf.py`
- Modify: `core/beachhub_core/services/online_buchung.py` (`zahlung_eingegangen`), `services/anfragen.py`, `services/benachrichtigung.py`, `jobs.py` (`verfall_ausfuehren`)
- Modify: `core/beachhub_core/templates/rechnung_pdf.html`
- Create: `core/beachhub_core/templates/mail/gutschein_gekauft.txt`, `mail/gutschein_geschenkt.txt`
- Create: `core/tests/test_gutschein_kauf.py`

**Interfaces:**
- Consumes: `Gutschein`, `gutscheine.neuer_code`, `protokolliere`, `beschreibung`, `hinweis`, `GutscheinFehler` (Task 2); Einstellungen aus Task 1; `kundengruppen.effektive_gruppe`; `rechnungen._neue_rechnung`, `Posten` (mit Nummernkreis aus Task 2); `zahlung.anbieter()`; `rechnung_pdf.erzeuge`, `rechnung_pdf.html`.
- Produces (shared): Anfragetyp `"gutschein_kaufen"` mit `GutscheinKaufen(empfaenger_email: str | None (3–200 Zeichen), betrag: Decimal | None (> 0, nur Modell `wert`))`.
- Produces: `gutschein_kauf.lege_an(db, kunde, *, empfaenger_email: str | None, betrag: Decimal | None) -> Gutschein` (Gründe `kein_gutscheinpreis`, `betrag_ungueltig`), `gutschein_kauf.anfragen(db, *, kunde, empfaenger_email, betrag, rueckkehr_url) -> Ergebnis` (`reserviert` mit `preis`, `zu_zahlen`, `checkout_url`, `reserviert_bis`; abgelehnt `konto_gesperrt`, `rechnungskunde`, `kein_gutscheinpreis`, `betrag_ungueltig`), `gutschein_kauf.abschliessen(db, g, *, quelle="portal") -> Rechnung`, `gutschein_kauf.zahlung_eingegangen(db, z, betrag) -> Ergebnis | None` (None: keine Gutscheinzahlung), `gutschein_kauf.brich_abgelaufene_ab(db) -> int`.
- Produces: `benachrichtigung.gutschein_gekauft(db, g, r)` (Betreff „Ihr Gutschein“, PDF im Anhang), `benachrichtigung.gutschein_geschenkt(db, g)` (Betreff „Ein Gutschein von <Name>“).
- Produces: Rechnungs-PDF mit Überschrift „Kaufbeleg“ und ohne Steuerzeilen bei `art = "gutschein_beleg"`, Hinweis zum Einzweckgutschein bei `art = "gutschein"`.

- [ ] **Step 1: Failing Tests in `shared`**

`shared/tests/test_kanal.py`:
```python
def test_gutschein_kaufen_validiert() -> None:
    assert kanal.NUTZLAST["gutschein_kaufen"].model_validate({}) == kanal.GutscheinKaufen()
    n = kanal.GutscheinKaufen.model_validate({"empfaenger_email": "bea@x.de", "betrag": "50.00"})
    assert n.betrag == Decimal("50.00")
    for falsch in ({"empfaenger_email": "x"}, {"betrag": "0"}, {"betrag": "12.345"}):
        with pytest.raises(ValidationError):
            kanal.GutscheinKaufen.model_validate(falsch)
```
Run: `cd shared && pytest -q` – Expected: FAIL.

- [ ] **Step 2: Vertrag in `shared`**

`shared/beachhub_shared/kanal.py`:
- In `ANFRAGETYPEN` `"gutschein_kaufen",` ergänzen.
- Nach `MitgliedschaftBeantragen`:
```python
class GutscheinKaufen(BaseModel):
    # Der Code geht auf Wunsch zusätzlich an eine beschenkte Person (Hauptspec § 9).
    empfaenger_email: str | None = Field(default=None, min_length=3, max_length=200)
    # Nur im Modell `wert`: einer der Beträge aus der Einstellung gutschein_betraege.
    betrag: Decimal | None = Field(default=None, gt=0, max_digits=8, decimal_places=2)
```
- In `NUTZLAST`: `"gutschein_kaufen": GutscheinKaufen,`.

Run: `cd shared && pytest -q` – Expected: PASS.

- [ ] **Step 3: Failing Tests im Hauptsystem**

`core/tests/test_gutschein_kauf.py`:
```python
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from beachhub_core import clock, jobs
from beachhub_core.models import Gutschein, Rechnung, Zahlung
from beachhub_core.services import (
    anfragen,
    gutschein_kauf,
    gutscheine,
    konfiguration,
    online_buchung,
    rechnung_pdf,
)
from beachhub_shared import kanal
from sqlalchemy.orm import Session

RUECK = "/zahlung/zurueck?anfrage=x"
HEUTE = date(2027, 11, 25)


def _kaufe(db: Session, k, *, empfaenger_email: str | None = None, betrag: Decimal | None = None):
    return gutschein_kauf.anfragen(
        db, kunde=k, empfaenger_email=empfaenger_email, betrag=betrag, rueckkehr_url=RUECK
    )


def _bezahle(db: Session, g: Gutschein, betrag: str):
    z = db.get(Zahlung, g.zahlung_id)
    rohdaten = json.dumps({"ref": z.provider_ref, "ergebnis": "bezahlt", "betrag": betrag})
    return online_buchung.zahlung_eingegangen(
        db, kanal.ZahlungEingegangen(provider="fake", rohdaten=rohdaten)
    )


def _nachlauf(db: Session, erg) -> None:
    db.commit()
    for schritt in erg.nach_commit:
        schritt(db)


def test_kauf_mitglied_mit_kaufrechnung(db: Session, gwelt, mail_ausgang: list) -> None:
    _, k = gwelt
    k.mitglied_bis = date(2028, 4, 30)
    db.commit()
    a = _kaufe(db, k).antwort
    db.commit()
    assert a.status == "reserviert" and a.preis == a.zu_zahlen == Decimal("26.00")
    assert a.checkout_url and a.reserviert_bis is not None
    g = db.query(Gutschein).one()
    assert (g.status, g.art, g.modell, g.steuer) == ("offen", "kauf", "einheit", "beim_kauf")
    assert (g.nur_mitglieder, g.ust_satz, g.kaufpreis_brutto) == (
        True,
        Decimal("7.00"),
        Decimal("26.00"),
    )
    assert mail_ausgang == []  # kein Code vor dem Zahlungseingang
    _nachlauf(db, _bezahle(db, g, "26.00"))
    db.refresh(g)
    assert g.status == "aktiv" and g.gueltig_bis == HEUTE + timedelta(days=730)
    r = db.get(Rechnung, g.rechnung_id)
    assert r.art == "gutschein" and r.status == "bezahlt" and not r.nummer.startswith("GB-")
    (p,) = r.positionen
    assert (p.brutto, p.ust_satz, p.buchung_id) == (Decimal("26.00"), Decimal("7.00"), None)
    assert "Mitgliedsgutschein – nur für DJK-Mitglieder einlösbar" in p.text
    assert g.code not in p.text and g.code[-4:] in p.text
    (m,) = mail_ausgang
    assert m["betreff"] == "Ihr Gutschein" and m["anhaenge"] == [f"{r.nummer}.pdf"]
    assert gutscheine.anzeige(g.code) in m["text"] and "nur für DJK-Mitglieder" in m["text"]
    assert "Einzweckgutschein" in rechnung_pdf.html(r) and "zzgl. 7 % USt" in rechnung_pdf.html(r)


def test_kaufpreis_bei_ausgabe_festgeschrieben(db: Session, gwelt) -> None:
    _, k = gwelt
    _kaufe(db, k)
    db.commit()
    konfiguration.setze(db, "gutschein_preis_nichtmitglied", "35")
    db.commit()
    g = db.query(Gutschein).one()
    _nachlauf(db, _bezahle(db, g, "30.00"))
    db.refresh(g)
    assert g.kaufpreis_brutto == Decimal("30.00") and g.status == "aktiv"
    assert db.get(Rechnung, g.rechnung_id).brutto == Decimal("30.00")


def test_ohne_preis_kein_verkauf(db: Session, gwelt) -> None:
    _, k = gwelt
    konfiguration.setze(db, "gutschein_preis_nichtmitglied", "0")
    db.commit()
    a = _kaufe(db, k).antwort
    assert a.status == "abgelehnt" and a.grund == "kein_gutscheinpreis"
    assert db.query(Gutschein).count() == 0


def test_rechnungskunde_nach_einstellung(db: Session, gwelt) -> None:
    _, k = gwelt
    k.rechnungskunde = True
    konfiguration.setze(db, "rechnungskunden_gutscheinkauf", "nein")
    db.commit()
    assert _kaufe(db, k).antwort.grund == "rechnungskunde"
    konfiguration.setze(db, "rechnungskunden_gutscheinkauf", "ja")
    db.commit()
    assert _kaufe(db, k).antwort.status == "reserviert"


def test_wertgutschein_kaufen(db: Session, gwelt) -> None:
    _, k = gwelt
    konfiguration.setze(db, "gutschein_modell", "wert")
    db.commit()
    assert _kaufe(db, k, betrag=Decimal("30")).antwort.grund == "betrag_ungueltig"
    assert _kaufe(db, k).antwort.grund == "betrag_ungueltig"
    a = _kaufe(db, k, betrag=Decimal("50")).antwort
    db.commit()
    assert a.preis == Decimal("50.00")
    g = db.query(Gutschein).one()
    _nachlauf(db, _bezahle(db, g, "50.00"))
    db.refresh(g)
    assert (g.modell, g.minuten, g.nennwert_brutto, g.restwert) == (
        "wert",
        None,
        Decimal("50.00"),
        Decimal("50.00"),
    )
    assert g.status == "aktiv" and g.nur_mitglieder is False


def test_kaufbeleg_beim_mehrzweckgutschein(db: Session, gwelt) -> None:
    _, k = gwelt
    k.mitglied_bis = date(2028, 4, 30)
    konfiguration.setze(db, "gutschein_steuer", "bei_einloesung")
    db.commit()
    _kaufe(db, k)
    db.commit()
    g = db.query(Gutschein).one()
    assert g.nur_mitglieder is False  # A-GUT-1b braucht der Mehrzweckgutschein nicht
    _nachlauf(db, _bezahle(db, g, "26.00"))
    r = db.get(Rechnung, g.rechnung_id)
    assert r.art == "gutschein_beleg" and r.nummer.startswith("GB-2027-")
    (p,) = r.positionen
    assert (p.ust_satz, p.ust, r.ust) == (Decimal("0.00"), Decimal("0.00"), Decimal("0.00"))
    html = rechnung_pdf.html(r)
    assert "Kaufbeleg" in html and "Mehrzweckgutschein" in html and "zzgl." not in html


def test_geschenk_mail_an_empfaenger(db: Session, gwelt, mail_ausgang: list) -> None:
    _, k = gwelt
    _kaufe(db, k, empfaenger_email=" Bea@X.de ")
    db.commit()
    g = db.query(Gutschein).one()
    assert g.empfaenger_email == "bea@x.de"
    _nachlauf(db, _bezahle(db, g, "30.00"))
    assert [m["betreff"] for m in mail_ausgang] == ["Ihr Gutschein", "Ein Gutschein von Anna"]
    assert mail_ausgang[1]["an"] == "bea@x.de" and mail_ausgang[1]["anhaenge"] == []
    assert gutscheine.anzeige(g.code) in mail_ausgang[1]["text"]


def test_doppelte_rueckmeldung_kauf_ist_folgenlos(db: Session, gwelt) -> None:
    _, k = gwelt
    _kaufe(db, k)
    db.commit()
    g = db.query(Gutschein).one()
    _nachlauf(db, _bezahle(db, g, "30.00"))
    zweite = _bezahle(db, g, "30.00")
    db.commit()
    assert zweite.antwort.status == "ok" and zweite.nach_commit == []
    assert db.query(Rechnung).count() == 1 and db.query(Gutschein).count() == 1


def test_zahlung_nach_abbruch_wird_guthaben(db: Session, gwelt, mail_ausgang: list) -> None:
    _, k = gwelt
    _kaufe(db, k)
    db.commit()
    clock.set_override(db, date(2027, 11, 26))
    jobs.verfall_ausfuehren(db)
    g = db.query(Gutschein).one()
    assert g.status == "abgebrochen"
    _nachlauf(db, _bezahle(db, g, "30.00"))
    db.refresh(g)
    db.refresh(k)
    assert g.status == "abgebrochen" and k.guthaben == Decimal("30.00")
    assert [m["betreff"] for m in mail_ausgang] == [
        "[Beachhub] Zahlung nach Abbruch des Gutscheinkaufs"
    ]


def test_unterzahlung_bleibt_offen(db: Session, gwelt) -> None:
    _, k = gwelt
    _kaufe(db, k)
    db.commit()
    g = db.query(Gutschein).one()
    _nachlauf(db, _bezahle(db, g, "10.00"))
    db.refresh(g)
    db.refresh(k)
    assert g.status == "offen" and g.rechnung_id is None and k.guthaben == Decimal("10.00")


def test_gutschein_kaufen_ueber_den_kanal(db: Session, gwelt) -> None:
    _, k = gwelt
    k.portal_konto_id = uuid.uuid4()
    db.commit()

    def anfrage(**nutzlast) -> kanal.Anfrage:
        return kanal.Anfrage(
            anfrage_id=uuid.uuid4(),
            typ="gutschein_kaufen",
            konto_id=k.portal_konto_id,
            nutzlast=nutzlast,
            erstellt_am=datetime.now(UTC),
        )

    antwort, _ = anfragen.bearbeite(db, anfrage(empfaenger_email="bea@x.de"))
    assert antwort.status == "reserviert" and antwort.preis == Decimal("30.00")
    antwort, _ = anfragen.bearbeite(db, anfrage(empfaenger_email="x"))
    assert antwort.status == "abgelehnt" and antwort.grund == "ungueltig"
```

- [ ] **Step 4: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_gutschein_kauf.py`
Expected: FAIL – `ImportError: cannot import name 'gutschein_kauf'`.

- [ ] **Step 5: Dienst `gutschein_kauf`**

`core/beachhub_core/services/gutschein_kauf.py`:
```python
"""Gutscheinkauf über das Portal (A-GUT-1a, A-GUT-5, A-GUT-8, A-KUND-7).

Der Kaufwunsch legt einen Gutschein im Zustand `offen` an und öffnet eine Bezahlsitzung über den
Kaufpreis (Abweichung A-3). Erst der Zahlungseingang macht ihn `aktiv`, erzeugt die Kaufrechnung
(Einzweckgutschein mit dem Satz des Käufers) bzw. den Kaufbeleg (Mehrzweckgutschein, ohne Steuer)
und verschickt den Code. Guthaben wird beim Kauf nicht verrechnet (Abweichung A-4).
"""

import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from beachhub_shared import kanal
from sqlalchemy import select
from sqlalchemy.orm import Session

from beachhub_core import clock, zahlung
from beachhub_core.models import Gutschein, Kunde, Rechnung, Zahlung
from beachhub_core.services import (
    audit,
    benachrichtigung,
    guthaben,
    gutscheine,
    konfiguration,
    kundengruppen,
    rechnung_pdf,
    rechnungen,
)
from beachhub_core.services.ergebnis import Ergebnis, Nachlauf, abgelehnt

logger = logging.getLogger(__name__)
NULL = Decimal("0.00")


def _alarm(betreff: str, text: str) -> Nachlauf:
    def lauf(db: Session) -> None:
        benachrichtigung.betreiber_alarm(betreff, text)

    return lauf


def lege_an(
    db: Session, kunde: Kunde, *, empfaenger_email: str | None, betrag: Decimal | None
) -> Gutschein:
    """Der Kaufwunsch: Preis, Gruppe und Satz des Käufers am Kaufdatum (A-GUT-1a), Modell und
    Steuervariante aus den Einstellungen – alles am Gutschein festgeschrieben (§ 3.14)."""
    heute = clock.today(db)
    gruppe = kundengruppen.effektive_gruppe(db, kunde, heute)
    modell = konfiguration.hole(db, "gutschein_modell")
    steuer = konfiguration.hole(db, "gutschein_steuer")
    nennwert: Decimal | None = None
    minuten: int | None = None
    if modell == Gutschein.EINHEIT:
        schluessel = (
            "gutschein_preis_mitglied" if gruppe.ist_mitglied else "gutschein_preis_nichtmitglied"
        )
        preis: Decimal = konfiguration.hole(db, schluessel)
        if preis <= NULL:
            raise gutscheine.GutscheinFehler("kein_gutscheinpreis")
        minuten = konfiguration.hole(db, "gutschein_minuten")
    else:
        if betrag is None or betrag not in konfiguration.hole(db, "gutschein_betraege").betraege:
            raise gutscheine.GutscheinFehler("betrag_ungueltig")
        preis = nennwert = betrag.quantize(Decimal("0.01"))
    g = Gutschein(
        code=gutscheine.neuer_code(db),
        art=Gutschein.KAUF,
        modell=modell,
        steuer=steuer,
        minuten=minuten,
        nennwert_brutto=nennwert,
        restwert=nennwert,
        kaufpreis_brutto=preis,
        kundengruppe_id=gruppe.id,
        ust_satz=gruppe.ust_satz,
        # A-GUT-1b: Nur beim Einzweckgutschein steht der Satz bei Ausgabe fest und muss zur
        # späteren Leistung passen; der Mehrzweckgutschein braucht die Regel nicht.
        nur_mitglieder=(
            gruppe.ist_mitglied
            and steuer == Gutschein.BEIM_KAUF
            and bool(konfiguration.hole(db, "mitgliedsgutschein_nur_mitglieder"))
        ),
        max_einloesungen=1,
        kaeufer_kunde_id=kunde.id,
        inhaber_kunde_id=kunde.id,
        ausgestellt_am=clock.now(db),
        gueltig_bis=heute + timedelta(days=konfiguration.hole(db, "gutschein_gueltig_tage")),
        status=Gutschein.OFFEN,
        empfaenger_email=(empfaenger_email or "").strip().lower() or None,
    )
    db.add(g)
    db.flush()
    gutscheine.protokolliere(db, g, None, "kauf_angefragt", quelle="portal")
    return g


def anfragen(
    db: Session,
    *,
    kunde: Kunde,
    empfaenger_email: str | None,
    betrag: Decimal | None,
    rueckkehr_url: str,
) -> Ergebnis:
    if kunde.anonymisiert_am is not None:
        return abgelehnt("konto_gesperrt")
    if kunde.rechnungskunde and not konfiguration.hole(db, "rechnungskunden_gutscheinkauf"):
        return abgelehnt("rechnungskunde")
    try:
        g = lege_an(db, kunde, empfaenger_email=empfaenger_email, betrag=betrag)
    except gutscheine.GutscheinFehler as e:
        return abgelehnt(e.grund)
    preis = g.kaufpreis_brutto or NULL
    anbieter = zahlung.anbieter()
    ablauf = clock.now(db) + timedelta(minutes=konfiguration.hole(db, "zahlungsfrist_minuten"))
    sitzung = anbieter.erzeuge_sitzung(
        betrag=preis, referenz=g.id, ablauf=ablauf, rueckkehr_url=rueckkehr_url
    )
    z = Zahlung(
        kunde_id=kunde.id,
        provider=anbieter.name,
        provider_ref=sitzung.provider_ref,
        betrag=preis,
        checkout_url=sitzung.checkout_url,
    )
    db.add(z)
    db.flush()
    g.zahlung_id = z.id
    db.flush()
    return Ergebnis(
        kanal.Antwort(
            status="reserviert",
            preis=preis,
            zu_zahlen=preis,
            checkout_url=sitzung.checkout_url,
            reserviert_bis=ablauf,
        )
    )


def abschliessen(db: Session, g: Gutschein, *, quelle: str = "portal") -> Rechnung:
    """Zahlungseingang: Der Gutschein wird aktiv und gilt ab heute `gutschein_gueltig_tage`
    Tage. Beim Einzweckgutschein entsteht eine reguläre Rechnung mit dem Satz des Käufers, beim
    Mehrzweckgutschein ein Kaufbeleg ohne Steuer in eigenem Nummernkreis (A-GUT-5)."""
    heute = clock.today(db)
    vorher = audit.als_dict(g)
    kaeufer = db.get(Kunde, g.kaeufer_kunde_id)
    if kaeufer is None or g.kaufpreis_brutto is None or g.ust_satz is None:
        raise gutscheine.GutscheinFehler("gutschein_ungueltig")
    g.status = Gutschein.AKTIV
    g.gueltig_bis = heute + timedelta(days=konfiguration.hole(db, "gutschein_gueltig_tage"))
    text = (
        f"Gutschein {gutscheine.beschreibung(g)}, Code …{g.code[-4:]}, gültig bis "
        f"{g.gueltig_bis:%d.%m.%Y} – {gutscheine.hinweis(g)}"
    )
    if g.steuer == Gutschein.BEIM_KAUF:
        art, satz = "gutschein", g.ust_satz
    else:
        art, satz = "gutschein_beleg", NULL
    r = rechnungen._neue_rechnung(
        db,
        kaeufer,
        art,
        [rechnungen.Posten(None, text, g.kaufpreis_brutto, satz)],
        heute,
        heute,
        "bezahlt",
        quelle=quelle,
    )
    g.rechnung_id = r.id
    gutscheine.protokolliere(db, g, vorher, "gekauft", quelle=quelle)
    return r


def _kauf_versenden(gutschein_id: uuid.UUID, rechnung_id: uuid.UUID) -> Nachlauf:
    def lauf(db: Session) -> None:
        g, r = db.get(Gutschein, gutschein_id), db.get(Rechnung, rechnung_id)
        if g is None or r is None:
            return
        try:
            rechnung_pdf.erzeuge(db, r)
            db.commit()
        except Exception:
            # Ohne PDF geht der Code trotzdem hinaus – ohne ihn hätte der Kunde nichts gekauft.
            logger.exception("PDF-Erzeugung für %s fehlgeschlagen", r.nummer)
            db.rollback()
            benachrichtigung.betreiber_alarm(
                "Rechnungs-PDF nicht erzeugt",
                f"Das PDF zu {r.nummer} (Gutscheinkauf) konnte nicht erzeugt werden; der Kunde "
                "hat den Code ohne Rechnung bekommen. Details im Log des Hauptsystems.",
            )
        benachrichtigung.gutschein_gekauft(db, g, r)
        benachrichtigung.gutschein_geschenkt(db, g)

    return lauf


def zahlung_eingegangen(db: Session, z: Zahlung, betrag: Decimal) -> Ergebnis | None:
    """Verarbeitet einen bestätigten Zahlungseingang, falls er zu einem Gutscheinkauf gehört;
    sonst None. Ist der Kauf abgebrochen oder zu wenig gezahlt, wird der Betrag Guthaben und der
    Betreiber entscheidet (wie bei verfallenen Reservierungen)."""
    g = db.scalar(select(Gutschein).where(Gutschein.zahlung_id == z.id).with_for_update())
    if g is None:
        return None
    if g.status == Gutschein.OFFEN and betrag >= z.betrag:
        r = abschliessen(db, g)
        nachlauf = [_kauf_versenden(g.id, r.id)]
        ueberzahlt = betrag - z.betrag
        if ueberzahlt > NULL:
            guthaben.buche(
                db,
                kunde=z.kunde,
                betrag=ueberzahlt,
                art="ueberzahlung",
                bezug_id=z.id,
                notiz="Überzahlung beim Gutscheinkauf",
                quelle="portal",
            )
            nachlauf.append(
                _alarm(
                    "Überzahlung beim Gutscheinkauf",
                    f"Die Zahlung {z.provider_ref} über {betrag} € überzahlt den Gutschein "
                    f"({z.betrag} €) um {ueberzahlt} €; die Differenz ist Guthaben von "
                    f"{z.kunde.name} <{z.kunde.email}>.",
                )
            )
        return Ergebnis(kanal.Antwort(status="ok"), nachlauf)
    grund = (
        "Zahlung nach Abbruch des Gutscheinkaufs"
        if betrag >= z.betrag
        else "Zahlung unter dem Gutscheinpreis"
    )
    guthaben.buche(
        db,
        kunde=z.kunde,
        betrag=betrag,
        art="ueberzahlung",
        bezug_id=z.id,
        notiz=grund,
        quelle="portal",
    )
    text = (
        f"Die Zahlung {z.provider_ref} über {betrag} € für einen Gutschein ist eingegangen, "
        f"konnte aber keinen Gutschein ausstellen ({grund}). Der Betrag wurde {z.kunde.name} "
        f"<{z.kunde.email}> als Guthaben gutgeschrieben."
    )
    return Ergebnis(kanal.Antwort(status="ok"), [_alarm(grund, text)])


def brich_abgelaufene_ab(db: Session) -> int:
    """Unbezahlte Käufe brechen nach der Zahlungsfrist ab (Abweichung A-3)."""
    grenze = clock.now(db) - timedelta(minutes=konfiguration.hole(db, "zahlungsfrist_minuten"))
    offen = db.scalars(
        select(Gutschein)
        .where(Gutschein.status == Gutschein.OFFEN, Gutschein.ausgestellt_am < grenze)
        .with_for_update(skip_locked=True)
    ).all()
    for g in offen:
        vorher = audit.als_dict(g)
        g.status = Gutschein.ABGEBROCHEN
        gutscheine.protokolliere(db, g, vorher, "kauf_abgebrochen", quelle="system")
        z = db.get(Zahlung, g.zahlung_id) if g.zahlung_id else None
        if z is not None and z.status == Zahlung.OFFEN:
            z.status = Zahlung.ABGEBROCHEN
    return len(offen)
```

- [ ] **Step 6: Einhängen in Zahlungseingang, Kanal und Job**

`core/beachhub_core/services/online_buchung.py`, Import `gutschein_kauf` in die Service-Importe; in `zahlung_eingegangen` direkt nach den Zeilen, die die Zahlung als bezahlt markieren (`z.status = Zahlung.BEZAHLT`, `z.empfangen_am = …`, `z.rohdaten_json = …`):
```python
    kauf = gutschein_kauf.zahlung_eingegangen(db, z, betrag)
    if kauf is not None:
        return kauf
```

`core/beachhub_core/services/anfragen.py`, Import `gutschein_kauf` in die Service-Importe; vor `_MIT_KUNDE`:
```python
def _gutschein_kaufen(
    db: Session, kunde: Kunde, anfrage: kanal.Anfrage, n: kanal.GutscheinKaufen
) -> Ergebnis:
    basis = settings.portal_oeffentliche_url.rstrip("/")
    return gutschein_kauf.anfragen(
        db,
        kunde=kunde,
        empfaenger_email=n.empfaenger_email,
        betrag=n.betrag,
        rueckkehr_url=f"{basis}/zahlung/zurueck?anfrage={anfrage.anfrage_id}",
    )
```
und in `_MIT_KUNDE`: `"gutschein_kaufen": _gutschein_kaufen,`.

`core/beachhub_core/jobs.py`, `gutschein_kauf` in die Service-Importe; in `verfall_ausfuehren` vor `db.commit()`:
```python
    gutschein_kauf.brich_abgelaufene_ab(db)
```

- [ ] **Step 7: Mails und PDF**

`core/beachhub_core/services/benachrichtigung.py` (`Gutschein`, `Kunde` in die Modell-Importe):
```python
def gutschein_gekauft(db: Session, g: Gutschein, r: Rechnung) -> None:
    from beachhub_core.services import gutscheine  # Zyklus vermeiden

    kaeufer = db.get(Kunde, g.kaeufer_kunde_id)
    if kaeufer is None:
        return
    anhang = [(f"{r.nummer}.pdf", Path(r.pdf_pfad).read_bytes())] if r.pdf_pfad else []
    mail.sende(
        kaeufer.email,
        "Ihr Gutschein",
        _text(
            "gutschein_gekauft",
            g=g,
            r=r,
            kaeufer=kaeufer,
            beschreibung=gutscheine.beschreibung(g),
            hinweis=gutscheine.hinweis(g),
        ),
        anhaenge=anhang,
    )


def gutschein_geschenkt(db: Session, g: Gutschein) -> None:
    from beachhub_core.services import gutscheine  # Zyklus vermeiden

    kaeufer = db.get(Kunde, g.kaeufer_kunde_id)
    if kaeufer is None or not g.empfaenger_email:
        return
    mail.sende(
        g.empfaenger_email,
        f"Ein Gutschein von {kaeufer.name}",
        _text(
            "gutschein_geschenkt",
            g=g,
            kaeufer=kaeufer,
            beschreibung=gutscheine.beschreibung(g),
            hinweis=gutscheine.hinweis(g),
        ),
    )
```

`core/beachhub_core/templates/mail/gutschein_gekauft.txt`:
```
Hallo {{ kaeufer.name }},

vielen Dank für Ihren Gutscheinkauf. Ihr Gutscheincode:

    {{ g.code|code }}

Gutschein {{ beschreibung }} – {{ hinweis }}.
Gültig bis {{ g.gueltig_bis|datum }}. Den Code geben Sie im Buchungsportal bei der Buchung ein.
{% if g.empfaenger_email %}
Wie gewünscht haben wir den Code auch an {{ g.empfaenger_email }} geschickt.
{% endif %}
{% if r.art == "gutschein_beleg" %}Ihren Kaufbeleg {{ r.nummer }}{% else %}Ihre Rechnung {{ r.nummer }}{% endif %} finden Sie im Anhang dieser E-Mail.

Viele Grüße
{{ betreiber }}
```

`core/beachhub_core/templates/mail/gutschein_geschenkt.txt`:
```
Hallo,

{{ kaeufer.name }} schenkt Ihnen einen Gutschein für die Beachhalle:

    {{ g.code|code }}

Gutschein {{ beschreibung }} – {{ hinweis }}.
Gültig bis {{ g.gueltig_bis|datum }}. Zum Einlösen legen Sie im Buchungsportal ein Konto an und
geben den Code bei der Buchung ein.

Viele Grüße
{{ betreiber }}
```

`core/beachhub_core/templates/rechnung_pdf.html`:
- Die Überschrift bekommt einen ersten Zweig für Kaufbelege; die übrigen Zweige (Stornorechnung usw.) bleiben, wie 1a-II sie hinterlassen hat:
```html
<h1>{% if r.art == "gutschein_beleg" %}Kaufbeleg{% elif r.art == "storno" %}Stornorechnung{% else %}Rechnung{% endif %} {{ r.nummer }}</h1>
```
- In der Positionstabelle die Zelle mit dem Steuersatz ersetzen durch:
```html
<td class="r">{% if r.art == "gutschein_beleg" %}–{% else %}{{ p.ust_satz|prozent }}{% endif %}</td>
```
- In der Tabelle `summe` die Schleife über `steuer` in `{% if r.art != "gutschein_beleg" %} … {% endif %}` einschließen (die Zeile „Gesamtbetrag“ bleibt außerhalb).
- Vor dem Absatz „Diese Stornorechnung …“ bzw. „Der Betrag wurde bereits bezahlt …“ einfügen:
```html
{% if r.art == "gutschein" %}<p>Einzweckgutschein (§ 3 Abs. 14 UStG): Die Umsatzsteuer ist mit dieser Rechnung abgegolten; die Einlösung löst keine weitere Rechnung aus.</p>{% endif %}
{% if r.art == "gutschein_beleg" %}<p>Mehrzweckgutschein (§ 3 Abs. 15 UStG): Dieser Beleg weist keine Umsatzsteuer aus. Die Rechnung mit Umsatzsteuer entsteht bei der Einlösung.</p>{% endif %}
```

- [ ] **Step 8: Tests und Lint**

Run:
```bash
(cd shared && pytest -q) && (cd core && pytest -q) && (cd portal && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 9: Commit**

```bash
git add shared core
git commit -m "feat(core): Gutscheinkauf über den Kanal mit Kaufrechnung oder Kaufbeleg"
```

---
## Task 7: Rückgabe eines gekauften Gutscheins

Zahlt der Betreiber einen gekauften Gutschein zurück (etwa nach Widerruf), sperrt er ihn auf der Detailseite. Beim Einzweckgutschein entsteht dabei eine Stornorechnung zur Kaufrechnung, ein Kaufbeleg (Mehrzweckgutschein) wird storniert; das Geld überweist der Betreiber selbst (A-GUT-9, A-ZAHL-6). Zurückgeben lässt sich nur ein unverbrauchter Gutschein (Abweichung A-12).

**Files:**
- Modify: `core/beachhub_core/services/gutscheine.py`
- Modify: `core/beachhub_core/routes/gutscheine.py`, `templates/gutscheine/detail.html`
- Test: `core/tests/test_gutscheine.py`, `core/tests/test_ui_gutscheine.py`

**Interfaces:**
- Consumes: `gutschein_kauf.lege_an`, `gutschein_kauf.abschliessen` (Task 6, in Tests); `rechnungen.storniere(db, rechnung, *, admin_user_id, grund) -> Rechnung` (1a-II: über `korrigiere`, setzt `korrigiert_rechnung_id`); `rechnung_pdf.erzeuge`, `benachrichtigung.rechnung`.
- Produces: `gutscheine.gib_zurueck(db, g, *, grund: str, admin_user_id) -> Rechnung | None` (Stornorechnung beim Einzweckgutschein, sonst None; Gründe `kein_kaufgutschein`, `bereits_eingeloest`).
- Produces: Route `POST /admin/gutscheine/{id}/rueckgabe` (Feld `grund`).

- [ ] **Step 1: Failing Tests schreiben**

`core/tests/test_gutscheine.py` (Importe: `Rechnung` in die Modell-Importe, `from beachhub_core.services import gutschein_kauf, gutscheine, konfiguration`):
```python
def _gekauft(db: Session, kunde) -> tuple[Gutschein, Rechnung]:
    g = gutschein_kauf.lege_an(db, kunde, empfaenger_email=None, betrag=None)
    r = gutschein_kauf.abschliessen(db, g)
    db.commit()
    return g, r


def test_rueckgabe_mit_stornorechnung(db: Session, gwelt) -> None:
    _, k = gwelt
    g, kauf = _gekauft(db, k)
    s = gutscheine.gib_zurueck(db, g, grund="Widerruf", admin_user_id=None)
    db.commit()
    assert g.status == "gesperrt" and "Zurückgegeben: Widerruf" in g.notiz
    assert s is not None and s.brutto == Decimal("-30.00")
    assert s.korrigiert_rechnung_id == kauf.id
    db.refresh(kauf)
    assert kauf.status == "storniert"


def test_rueckgabe_kaufbeleg_ohne_stornorechnung(db: Session, gwelt) -> None:
    _, k = gwelt
    konfiguration.setze(db, "gutschein_steuer", "bei_einloesung")
    g, beleg = _gekauft(db, k)
    assert gutscheine.gib_zurueck(db, g, grund="Widerruf", admin_user_id=None) is None
    db.commit()
    db.refresh(beleg)
    assert beleg.status == "storniert" and db.query(Rechnung).count() == 1


def test_rueckgabe_nur_unverbraucht(db: Session, gwelt) -> None:
    _, k = gwelt
    g, _ = _gekauft(db, k)
    g.status = Gutschein.EINGELOEST
    db.commit()
    with pytest.raises(gutscheine.GutscheinFehler, match="bereits_eingeloest"):
        gutscheine.gib_zurueck(db, g, grund="x", admin_user_id=None)
    with pytest.raises(gutscheine.GutscheinFehler, match="kein_kaufgutschein"):
        gutscheine.gib_zurueck(db, freicode(db), grund="x", admin_user_id=None)
```

`core/tests/test_ui_gutscheine.py` (Import `from beachhub_core.services import gutschein_kauf, gutscheine`):
```python
def test_rueckgabe_ueber_ui(eingeloggt: TestClient, db: Session, gwelt, mail_ausgang: list) -> None:
    c = eingeloggt
    _, k = gwelt
    g = gutschein_kauf.lege_an(db, k, empfaenger_email=None, betrag=None)
    gutschein_kauf.abschliessen(db, g)
    db.commit()
    assert "Zurückgeben" in c.get(f"/admin/gutscheine/{g.id}").text
    r = c.post(
        f"/admin/gutscheine/{g.id}/rueckgabe",
        data={"csrf_token": c.csrf, "grund": "Widerruf"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(g)
    assert g.status == "gesperrt"
    assert any(m["betreff"].startswith("Rechnung ") for m in mail_ausgang)
```

- [ ] **Step 2: Fehlschlag prüfen**

Run: `cd core && pytest -q tests/test_gutscheine.py tests/test_ui_gutscheine.py`
Expected: FAIL – `AttributeError: … has no attribute 'gib_zurueck'`.

- [ ] **Step 3: Dienst**

`core/beachhub_core/services/gutscheine.py`, am Dateiende:
```python
def gib_zurueck(
    db: Session, g: Gutschein, *, grund: str, admin_user_id: uuid.UUID | None
) -> Rechnung | None:
    """Rückgabe eines gekauften Gutscheins, etwa nach Widerruf (A-GUT-9): Er wird gesperrt; beim
    Einzweckgutschein entsteht eine Stornorechnung zur Kaufrechnung, ein Kaufbeleg ohne Steuer
    wird storniert (Abweichung A-12). Das Geld überweist der Betreiber selbst (A-ZAHL-6)."""
    if g.art != Gutschein.KAUF or g.rechnung_id is None:
        raise GutscheinFehler("kein_kaufgutschein")
    unverbraucht = (
        g.status in (Gutschein.AKTIV, Gutschein.VERFALLEN)
        and anzahl_aktive_einloesungen(db, g) == 0
        and (g.modell != Gutschein.WERT or g.restwert == g.nennwert_brutto)
    )
    if not unverbraucht:
        raise GutscheinFehler("bereits_eingeloest")
    vorher = audit.als_dict(g)
    g.status = Gutschein.GESPERRT
    g.notiz = _notiz(g.notiz, f"Zurückgegeben: {grund.strip()}")
    kauf = db.get(Rechnung, g.rechnung_id)
    storno: Rechnung | None = None
    if kauf is not None and kauf.art == "gutschein":
        storno = rechnungen.storniere(
            db, kauf, admin_user_id=admin_user_id, grund=f"Rückgabe Gutschein: {grund.strip()}"
        )
    elif kauf is not None and kauf.status != "storniert":
        vorher_beleg = audit.als_dict(kauf)
        kauf.status = "storniert"
        db.flush()
        audit.protokolliere(
            db,
            quelle="admin",
            objekt_typ="rechnung",
            objekt_id=kauf.id,
            vorher=vorher_beleg,
            nachher={**audit.als_dict(kauf), "grund": f"Rückgabe Gutschein: {grund.strip()}"},
            admin_user_id=admin_user_id,
        )
    protokolliere(db, g, vorher, "zurueckgegeben", quelle="admin", admin_user_id=admin_user_id)
    return storno
```

- [ ] **Step 4: Route und Detailseite**

`core/beachhub_core/routes/gutscheine.py`:
- Importe: `import logging`, `from beachhub_core.services import benachrichtigung, gutscheine, konfiguration, rechnung_pdf`, `from beachhub_core.services.rechnungen import RechnungsFehler`; `logger = logging.getLogger(__name__)` unter `router`.
- Am Dateiende:
```python
@router.post("/gutscheine/{gutschein_id}/rueckgabe", response_model=None)
def rueckgabe(
    request: Request,
    gutschein_id: uuid.UUID,
    grund: str = Form(...),
    admin: AdminUser = Depends(auth.nur_admin_rolle),
    db: Session = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    g = db.get(Gutschein, gutschein_id)
    if g is None:
        return _nicht_gefunden()
    try:
        storno = gutscheine.gib_zurueck(db, g, grund=grund, admin_user_id=admin.id)
        db.commit()
    except FORM_FEHLER as e:
        db.rollback()
        return _detail(request, admin, db, g, fehlertext(e, GRUND))
    text = "Gutschein zurückgenommen und gesperrt"
    if storno is not None:
        try:
            rechnung_pdf.erzeuge(db, storno)
            db.commit()
        except RechnungsFehler:
            logger.exception("PDF-Erzeugung für Stornorechnung %s fehlgeschlagen", storno.nummer)
            db.rollback()
        else:
            benachrichtigung.rechnung(db, storno)
        text += f", Stornorechnung {storno.nummer}"
    return mit_flash(RedirectResponse(f"/admin/gutscheine/{g.id}", status_code=303), text)
```

`core/beachhub_core/templates/gutscheine/detail.html`, in der Aktionskarte nach dem Formular „Sperren“:
```html
  {% if g.art == "kauf" and g.rechnung_id and g.status in ("aktiv", "verfallen") %}
  <h2>Zurückgeben</h2>
  <p class="small">Nach Widerruf oder auf Wunsch: Der Gutschein wird gesperrt, zur Kaufrechnung entsteht eine Stornorechnung. Das Geld überweisen Sie selbst.</p>
  <form method="post" action="/admin/gutscheine/{{ g.id }}/rueckgabe">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <label>Grund <input name="grund" required></label>
    <button class="gefahr">Zurückgeben</button>
  </form>
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
git commit -m "feat(core): Rückgabe gekaufter Gutscheine mit Stornorechnung"
```

---
## Task 8: Gutscheine im Lesestand des Portals

Das Portal bekommt, was es für Gutscheinkauf und -anzeige in Stufe 2 braucht: im Konto-Dokument die einlösbaren Gutscheine, deren Inhaber der Kunde ist (mit Code, Umfang, Restwert, Gültigkeit und Hinweis zur Gruppe, A-GUT-1a), und ob er Gutscheine kaufen darf (A-KUND-7); im Tarif-Dokument das Angebot (Modell, Minuten, Preise je Gruppe bzw. Beträge, Gültigkeit). Markiert werden die Dokumente schon heute: Konto über `gutscheine.protokolliere`, Tarife über `konfiguration.setze` (Tasks 1, 2).

**Files:**
- Modify: `shared/beachhub_shared/lesestand.py`; Test: `shared/tests/test_lesestand_schema.py`
- Modify: `core/beachhub_core/services/lesestand.py` (`baue_konto`, `baue_tarife`)
- Test: `core/tests/test_lesestand.py`

**Interfaces:**
- Consumes: `Gutschein.EINLOESBAR`, `gutscheine.anzeige`, `gutscheine.hinweis` (Task 2); Einstellungen aus Task 1.
- Produces (shared): `KontoGutschein(code: str, art: Literal["kauf","frei"], modell: Literal["einheit","wert"], status: Literal["aktiv","teilweise_eingeloest"], gueltig_bis: date, minuten: int | None = None, restwert: Decimal | None = None, nur_mitglieder: bool = False, hinweis: str = "")`; `KontoInhalt.gutscheine: list[KontoGutschein] = []`, `KontoInhalt.gutschein_kauf: bool = True`; `GutscheinAngebot(modell, minuten: int, preis_mitglied: Decimal | None = None, preis_nichtmitglied: Decimal | None = None, betraege: list[Decimal] = [], gueltig_tage: int)`; `TarifeInhalt.gutschein: GutscheinAngebot | None = None`.

- [ ] **Step 1: Failing Tests in `shared`**

`shared/tests/test_lesestand_schema.py` (Importe: `GutscheinAngebot`, `KontoGutschein`):
```python
def test_konto_gutscheine_mit_vorgabe() -> None:
    alt = {
        "kunde_id": "k",
        "kundengruppe": "Nicht-Mitglied",
        "guthaben": "0.00",
        "buchungen": [],
        "rechnungen": [],
    }
    k = KontoInhalt.model_validate(alt)
    assert k.gutscheine == [] and k.gutschein_kauf is True
    g = KontoGutschein(
        code="ABCD-EFGH-JKLMN", art="kauf", modell="einheit", status="aktiv",
        gueltig_bis=date(2029, 11, 24), minuten=120,
    )
    neu = KontoInhalt.model_validate({**alt, "gutscheine": [g.model_dump(mode="json")]})
    assert neu.gutscheine == [g]


def test_tarife_gutscheinangebot_optional() -> None:
    assert TarifeInhalt(regeln=[]).gutschein is None
    a = GutscheinAngebot(modell="wert", minuten=120, betraege=[Decimal("25.00")], gueltig_tage=730)
    t = TarifeInhalt.model_validate({"regeln": [], "gutschein": a.model_dump(mode="json")})
    assert t.gutschein == a
```
Run: `cd shared && pytest -q` – Expected: FAIL (`ImportError: cannot import name 'GutscheinAngebot'`).

- [ ] **Step 2: Vertrag in `shared`**

`shared/beachhub_shared/lesestand.py`:
- `from pydantic import BaseModel, Field`.
- Vor `TarifeInhalt`:
```python
class GutscheinAngebot(BaseModel):
    """Was das Portal zum Gutscheinkauf anbietet (A-GUT-1, -1a). Preise fehlen, wenn der Betreiber
    für die Gruppe keinen eingetragen hat (kein Verkauf)."""

    modell: Literal["einheit", "wert"]
    minuten: int
    preis_mitglied: Decimal | None = None
    preis_nichtmitglied: Decimal | None = None
    betraege: list[Decimal] = Field(default_factory=list)
    gueltig_tage: int
```
- In `TarifeInhalt` ergänzen: `gutschein: GutscheinAngebot | None = None`.
- Vor `KontoInhalt`:
```python
class KontoGutschein(BaseModel):
    # Anzeigeform ABCD-EFGH-JKLMN: Der Inhaber braucht den Code zum Einlösen oder Verschenken.
    code: str
    art: Literal["kauf", "frei"]
    modell: Literal["einheit", "wert"]
    status: Literal["aktiv", "teilweise_eingeloest"]
    gueltig_bis: date
    minuten: int | None = None
    restwert: Decimal | None = None
    nur_mitglieder: bool = False
    # Gruppe des Gutscheins, wie sie auf Mail und Rechnung steht (A-GUT-1a).
    hinweis: str = ""
```
- In `KontoInhalt` ergänzen:
```python
    gutscheine: list[KontoGutschein] = Field(default_factory=list)
    gutschein_kauf: bool = True
```
Run: `cd shared && pytest -q` – Expected: PASS.

- [ ] **Step 3: Failing Tests im Hauptsystem**

`core/tests/test_lesestand.py` (Importe: `from decimal import Decimal` ist da; `from beachhub_core.services import gutscheine, konfiguration`; `from hilfen_gutschein import kaufgutschein`):
```python
def test_konto_zeigt_eigene_einloesbare_gutscheine(db: Session, welt) -> None:
    _, a, b = welt
    aktiv = kaufgutschein(db, a, nur_mitglieder=True)
    teil = kaufgutschein(
        db, a, modell="wert", minuten=None, nennwert_brutto=Decimal("50.00"),
        restwert=Decimal("20.00"), status="teilweise_eingeloest",
    )
    kaufgutschein(db, a, status="eingeloest")
    kaufgutschein(db, b)
    db.commit()
    k = lesestand.baue_konto(db, a)
    je_code = {g.code: g for g in k.gutscheine}
    assert set(je_code) == {gutscheine.anzeige(aktiv.code), gutscheine.anzeige(teil.code)}
    assert je_code[gutscheine.anzeige(aktiv.code)].hinweis.startswith("Mitgliedsgutschein")
    assert je_code[gutscheine.anzeige(teil.code)].restwert == Decimal("20.00")
    assert k.gutschein_kauf is True
    a.rechnungskunde = True
    konfiguration.setze(db, "rechnungskunden_gutscheinkauf", "nein")
    db.commit()
    assert lesestand.baue_konto(db, a).gutschein_kauf is False


def test_tarife_mit_gutscheinangebot(db: Session, welt) -> None:
    assert lesestand.baue_tarife(db).gutschein is None  # ohne Preis kein Verkauf
    konfiguration.setze(db, "gutschein_preis_mitglied", "26")
    db.commit()
    a = lesestand.baue_tarife(db).gutschein
    assert a is not None and (a.modell, a.minuten, a.gueltig_tage) == ("einheit", 120, 730)
    assert (a.preis_mitglied, a.preis_nichtmitglied) == (Decimal("26.00"), None)
    konfiguration.setze(db, "gutschein_modell", "wert")
    db.commit()
    w = lesestand.baue_tarife(db).gutschein
    assert w is not None and w.betraege == [Decimal("25.00"), Decimal("50.00"), Decimal("100.00")]
```
Run: `cd core && pytest -q tests/test_lesestand.py` – Expected: FAIL (`AttributeError: … 'gutscheine'`).

- [ ] **Step 4: Lesestand bauen**

`core/beachhub_core/services/lesestand.py`:
- Importe: `Gutschein` in die Modell-Importe; `from beachhub_core.services import gutscheine as gutschein_dienst` (der Name `gutscheine` steht in `baue_konto` für die Liste).
- Vor `baue_tarife`:
```python
def _gutschein_angebot(db: Session) -> schema.GutscheinAngebot | None:
    minuten = konfiguration.hole(db, "gutschein_minuten")
    gueltig_tage = konfiguration.hole(db, "gutschein_gueltig_tage")
    if konfiguration.hole(db, "gutschein_modell") == Gutschein.WERT:
        return schema.GutscheinAngebot(
            modell="wert",
            minuten=minuten,
            betraege=konfiguration.hole(db, "gutschein_betraege").betraege,
            gueltig_tage=gueltig_tage,
        )
    mitglied = konfiguration.hole(db, "gutschein_preis_mitglied")
    nicht = konfiguration.hole(db, "gutschein_preis_nichtmitglied")
    if mitglied <= 0 and nicht <= 0:
        return None  # ohne Preis kein Verkauf (A-GUT-1a)
    return schema.GutscheinAngebot(
        modell="einheit",
        minuten=minuten,
        preis_mitglied=mitglied if mitglied > 0 else None,
        preis_nichtmitglied=nicht if nicht > 0 else None,
        gueltig_tage=gueltig_tage,
    )
```
- In `baue_tarife` im `return schema.TarifeInhalt(...)` ergänzen: `gutschein=_gutschein_angebot(db),`.
- In `baue_konto` vor dem `return`:
```python
    eigene = db.scalars(
        select(Gutschein)
        .where(
            Gutschein.inhaber_kunde_id == kunde.id,
            Gutschein.status.in_(Gutschein.EINLOESBAR),
        )
        .order_by(Gutschein.gueltig_bis, Gutschein.code)
    ).all()
```
  und im `return schema.KontoInhalt(...)` ergänzen:
```python
        gutscheine=[
            schema.KontoGutschein(
                code=gutschein_dienst.anzeige(g.code),
                art=g.art,
                modell=g.modell,
                status=g.status,
                gueltig_bis=g.gueltig_bis,
                minuten=g.minuten,
                restwert=g.restwert,
                nur_mitglieder=g.nur_mitglieder,
                hinweis=gutschein_dienst.hinweis(g) if g.art == Gutschein.KAUF else "Freischaltcode",
            )
            for g in eigene
        ],
        gutschein_kauf=(
            not kunde.rechnungskunde
            or bool(konfiguration.hole(db, "rechnungskunden_gutscheinkauf"))
        ),
```

- [ ] **Step 5: Tests und Lint**

Run:
```bash
(cd shared && pytest -q) && (cd core && pytest -q) && (cd portal && pytest -q) && pytest -q e2e
ruff format . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 6: Commit**

```bash
git add shared core
git commit -m "feat(core): Gutscheine und Gutscheinangebot im Lesestand fürs Portal"
```

---
## Task 9: Betriebshandbuch, README, Spec-Abgleich und Gesamtprüfung

**Files:**
- Modify: `docs/betrieb/hauptsystem.md`
- Modify: `README.md` (Statuszeile)
- Modify: `docs/superpowers/specs/2026-09-05-beachhub-design.md` (§ 3.9a, § 3.14, § 5)

**Interfaces:** keine neuen.

- [ ] **Step 1: Betriebshandbuch**

`docs/betrieb/hauptsystem.md`:
- In § 4 (Ersteinrichtung) nach dem Punkt zu den Einstellungen einen Punkt ergänzen:
```markdown
   **Gutscheine:** Unter Einstellungen → Gutscheine die Preise für Mitglieder und
   Nicht-Mitglieder eintragen – solange dort 0 steht, verkauft das Portal keine Gutscheine.
   Modell (Buchungseinheit oder Eurobetrag) und Umsatzsteuer (beim Kauf oder bei der Einlösung)
   gelten jeweils für danach verkaufte Gutscheine.
```
- In § 5 nach dem Abschnitt „Mitgliedschaft“ einfügen:
```markdown
### Gutscheine

- **Verwaltung** unter „Gutscheine“: Liste mit Status, Käufer, Inhaber und Restwert, Detailseite
  mit Einlösungen. Dort sperren (etwa bei Missbrauch), Gültigkeit ändern bzw. einen verfallenen
  Gutschein wieder aktivieren und gekaufte Gutscheine zurückgeben (Stornorechnung zur Kaufrechnung,
  Kaufbelege werden storniert; das Geld überweisen Sie selbst).
- **Freischaltcodes** unter „Gutscheine → Freischaltcodes ausstellen“, einzeln oder als Serie
  (bis 1000 Codes, CSV zum Drucken). Ohne Vorgabe wählt der Kunde Feld und Termin.
- **Läufe:** Unbezahlte Gutscheinkäufe brechen nach der Zahlungsfrist ab (minütlich mit dem Verfall
  der Reservierungen); abgelaufene Gutscheine verfallen täglich um 00:20 Uhr.
- **Mehrzweckgutscheine** (Umsatzsteuer bei der Einlösung) bekommen beim Kauf einen Kaufbeleg ohne
  Steuer im eigenen Nummernkreis `GB-JJJJ-NNNNN`; die Rechnung mit Steuer entsteht bei der Einlösung.
- **Missbrauchsschutz:** Nach zehn abgelehnten Codes in einer Stunde nimmt das Hauptsystem von
  diesem Konto vorübergehend keine Codes mehr an.
```

- [ ] **Step 2: README**

`README.md`, in der Statuszeile „1a-III (Gutscheine) folgen“ bzw. die entsprechende Angabe ersetzen durch „1a-III (Gutscheine und Freischaltcodes im Hauptsystem) umgesetzt; die Portal-Oberfläche dafür folgt mit Stufe 2“.

- [ ] **Step 3: Spec an die Umsetzung angleichen**

`docs/superpowers/specs/2026-09-05-beachhub-design.md`:
- A-GUT-1a: „Ohne Betrag kein Verkauf.“ → „Ohne Betrag (Einstellung 0) kein Verkauf.“ und nach „aus einer konfigurierbaren Liste“ ergänzen: „(`gutschein_betraege`)“.
- A-GUT-6, Satz zum Modell `wert`: „Der Wertanteil wird dem Einlösenden als Guthaben gutgeschrieben, zusammen mit Korrekturbeleg (A-STORNO-6).“ ersetzen durch „Der verbrauchte Wert kehrt auf den Gutschein zurück, der Einlösende wird Inhaber; es entsteht kein Guthaben. Beim Mehrzweckgutschein wird die Rechnung der Einlösung mit einem Korrekturbeleg aufgehoben.“
- A-GUT-7: anhängen „Im Hauptsystem gilt das Limit je Konto (10 abgelehnte Codes je Stunde, Tabelle `gutschein_fehlversuch`); das Limit je IP setzt das Portal.“
- A-GUT-4: nach „`aktiv → eingeloest`“ ergänzen: „; ein gekaufter Gutschein ist bis zum Zahlungseingang `offen` und nach verstrichener Zahlungsfrist `abgebrochen`“.
- § 3.14, Tabelle: Zeile `gutschein_preis_mitglied`, `gutschein_preis_nichtmitglied` – Vorgabe „0 (kein Verkauf) – der Betreiber trägt die Beträge ein“; danach neue Zeile `| \`gutschein_betraege\` | 25; 50; 100 | beliebige Beträge, durch Semikolon getrennt | A-GUT-1a |`.
- § 5, Zeile `gutschein`: Status um `offen`/`abgebrochen` ergänzen, `serie?` ergänzen; Zeile `gutschein_einloesung`: `steuerbetrag?, rechnung_position_id?` ergänzen; neue Zeile `| \`gutschein_fehlversuch\` | kunde_id, zeitpunkt (Rate-Limit je Konto, A-GUT-7) |`; Zeile zu `rechnung` bzw. Nummernkreis: „Kaufbelege (`gutschein_beleg`) im Nummernkreis `GB-JJJJ-NNNNN` (`nummernkreis.kreis`)“.

- [ ] **Step 4: Gesamtprüfung**

Run:
```bash
(cd shared && pytest -q) && (cd hall && pytest -q) && (cd core && pytest -q) && (cd portal && pytest -q) && pytest -q e2e
ruff format --check . && ruff check . && mypy
```
Expected: alles PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md docs
git commit -m "docs: Gutscheine und Freischaltcodes im Betriebshandbuch und in der Spec"
```

Danach den Branch mit superpowers:finishing-a-development-branch abschließen: nach `main` mergen (`--no-ff`), pushen, Worktree und Branch entfernen.
