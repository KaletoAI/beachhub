# Beachhub

Buchung, Abrechnung und Steuerung einer Beachvolleyballhalle im Winterbetrieb.
Drei Teilsysteme in einem Repository: Buchungsportal (`portal/`), Hauptsystem (`core/`), Hallendienst (`hall/`).

Status: Stufe 1 (Hauptsystem) fertig; Stufe 1a-I (feste Kundengruppen mit Steuersatz,
Mitgliedschaft mit Antrag, Abgleich und Klärungsliste, Rechnungskunden) umgesetzt, 1a-II
(Saisonrechnung) und 1a-III (Gutscheine) folgen; Portal-Kern (Stufe 2 ohne Mitgliedschaft,
Gutscheine und echten Zahlungsanbieter) implementiert; Stufe 3 (Hallendienst) implementiert und
gegen simuliertes Home Assistant getestet, die Anbindung an die echte Hallentechnik folgt, sobald
der Hallenhersteller die Schnittstellen festlegt.

- Technische Spezifikation: `docs/superpowers/specs/2026-09-05-beachhub-design.md`
- Dokument für den Betreiber (nicht technisch), Version 0.4: `docs/betreiber/Beachhub-Anforderungen-und-Loesungskonzept.pdf`
  (Quelle: `docs/betreiber/anforderungen-und-loesungskonzept.html`, erzeugt mit WeasyPrint)
- Antworten des Betreibers vom 10.09.2026: `docs/betreiber/Beachhub_Anforderungsprofil Antwort.pdf`
- Antworten des Betreibers vom 23.09.2026 auf Runde 2: `docs/betreiber/Fragenkatalog 2_BeachAugsburg Buchungssystem.docx`
  (frühere Rückfragen: `docs/betreiber/Beachhub-Rueckfragen-Runde-2.pdf`)
- Rückfragen Runde 3: `docs/betreiber/Beachhub-Rueckfragen-Runde-3.pdf` (Quelle: `docs/betreiber/rueckfragen-runde-3.html`);
  die Antworten vom 03.10.2026 stehen in der Spezifikation, Abschnitt 12.1b
- Betriebshandbuch (Inbetriebnahme, Backup, WireGuard): `docs/betrieb/hauptsystem.md`
- Entwickler-Kurzstart: `core/README.md`
- Portal: Design docs/superpowers/specs/2026-09-23-portal-kern-design.md, Betrieb docs/betrieb/portal.md, Kurzstart portal/README.md
- Hallendienst: `hall/README.md`, Betrieb und HA-Einrichtung `docs/betrieb/hallendienst.md`

## Entwicklung

```bash
pip install -e shared[dev] -e core[dev] -e hall[dev]
docker compose -f core/docker-compose.yml up -d db
cd core && pytest
```

Details (venv, `.env`, Migrationen, CLI) siehe `core/README.md`.

Hauptsystem und Portal lokal starten (je ein Terminal; das Skript führt vorher die Migrationen aus,
lauscht auf allen Schnittstellen und nennt die Adresse mit der IP des Rechners; beim ersten
Portal-Start richtet es `portal/.env` und den Kanal in `core/.env` ein):

```bash
./start.sh core      # Admin-UI: http://<IP>:8000/admin/login
./start.sh portal    # Portal:   http://<IP>:8001
```

```bash
pip install -e portal[dev]
cd portal && pytest          # TEST_PORTAL_DATABASE_URL setzen
pytest e2e                   # aus der Repo-Wurzel, beide Test-Datenbanken
```

## Lizenz

Beachhub steht unter der [PolyForm Noncommercial License 1.0.0](LICENSE.md).

- **Erlaubt:** private Nutzung, Nutzung durch gemeinnützige Organisationen und Vereine, Forschung, Lehre, Ausprobieren, Weitergabe und Änderung, solange der Zweck nicht kommerziell ist.
- **Nicht erlaubt ohne Genehmigung:** jede kommerzielle Nutzung, zum Beispiel der Betrieb für eine andere gewerblich betriebene Halle oder der Einbau in ein kommerzielles Angebot.
- **Kommerzielle Lizenz:** auf Anfrage beim Rechteinhaber über ein GitHub-Issue oder die im Profil hinterlegte Kontaktadresse.

Urheber und Rechteinhaber ist der Autor des Projekts. Die Halle, für die Beachhub entwickelt wird, ist Anwender, nicht Auftraggeber im Sinne einer Auftragsentwicklung.
