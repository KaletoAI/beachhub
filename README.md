# Beachhub

Buchung, Abrechnung und Steuerung einer Beachvolleyballhalle im Winterbetrieb.
Drei Teilsysteme in einem Repository: Buchungsportal (`portal/`), Hauptsystem (`core/`), Hallendienst (`hall/`).

Status: Stufe 1 (Hauptsystem) in Arbeit. Monorepo-Grundgerüst (`shared/`, `core/`) mit Konfiguration,
Datenbankanbindung und CI steht.

- Technische Spezifikation: `docs/superpowers/specs/2026-09-05-beachhub-design.md`
- Dokument für den Betreiber (nicht technisch): `docs/betreiber/Beachhub-Anforderungen-und-Loesungskonzept.pdf`
  (Quelle: `docs/betreiber/anforderungen-und-loesungskonzept.html`, erzeugt mit WeasyPrint)

## Entwicklung (core)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e shared[dev] -e core[dev]
cp core/.env.example core/.env
cd core && TEST_DATABASE_URL=postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_test pytest -q
```

Für PostgreSQL per Docker siehe `core/docker-compose.yml`.

## Lizenz

Beachhub steht unter der [PolyForm Noncommercial License 1.0.0](LICENSE.md).

- **Erlaubt:** private Nutzung, Nutzung durch gemeinnützige Organisationen und Vereine, Forschung, Lehre, Ausprobieren, Weitergabe und Änderung, solange der Zweck nicht kommerziell ist.
- **Nicht erlaubt ohne Genehmigung:** jede kommerzielle Nutzung, zum Beispiel der Betrieb für eine andere gewerblich betriebene Halle oder der Einbau in ein kommerzielles Angebot.
- **Kommerzielle Lizenz:** auf Anfrage beim Rechteinhaber über ein GitHub-Issue oder die im Profil hinterlegte Kontaktadresse.

Urheber und Rechteinhaber ist der Autor des Projekts. Die Halle, für die Beachhub entwickelt wird, ist Anwender, nicht Auftraggeber im Sinne einer Auftragsentwicklung.
