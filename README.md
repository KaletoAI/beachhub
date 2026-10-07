# Beachhub

Buchung, Abrechnung und Steuerung einer Beachvolleyballhalle im Winterbetrieb.
Drei Teilsysteme in einem Repository: Buchungsportal (`portal/`), Hauptsystem (`core/`), Hallendienst (`hall/`).

Status: Stufe 1 (Hauptsystem) fertig; Stufe 1a-I (feste Kundengruppen mit Steuersatz,
Mitgliedschaft mit Antrag, Abgleich und Klärungsliste, Rechnungskunden) und 1a-II
(Saisonrechnung, freie Abo-Absagen, Korrekturbelege, Guthabenliste) und 1a-III
(Gutscheine und Freischaltcodes im Hauptsystem) umgesetzt; Portal-Kern mit signiertem
Gutscheinbestand und Kanalverträgen implementiert. Mitgliedschaftsoberfläche, Gutscheinverkauf,
Codeeingabe und echter Zahlungsanbieter folgen im weiteren Ausbau der Stufe 2.
Stufe 3 (Hallendienst) ist implementiert und
gegen simuliertes Home Assistant getestet, die Anbindung an die echte Hallentechnik folgt, sobald
der Hallenhersteller die Schnittstellen festlegt.

Stufe 1a-III umfasst beide Gutscheinmodelle (`einheit`, `wert`) und Steuerzeitpunkte,
Freischaltcodes mit Serienexport, Kauf über den Hauptsystem-Kanal, atomare Einlösung,
Storno/Kulanz und manuelle Rückgabe. Revision `0015` folgt direkt auf `0014` und erhält
die bestehenden Rechnungszähler; steuerfreie Mehrzweck-Kaufbelege erhalten den eigenen
Nummernkreis `GB-JJJJ-NNNNN`. Kaufbedingungen und Fristen bleiben als Snapshot erhalten.
Ein kostenfreier bestätigter Einheitenstorno gibt die Nutzung unter ursprünglicher Gültigkeit
zurück; verbrauchter Wert wird mit Gegenbeleg Guthaben des Einlösers, ohne den Gutscheinrest
zu erhöhen. Die Rückgabe vollständig ungenutzter Kaufgutscheine sperrt dauerhaft und benennt
die manuelle Erstattung an den ursprünglichen Käufer; sie erzeugt kein Kundenguthaben.
Käufer und Einlöser behalten getrennte Belegrechte. Signierter Bestand und offene Käufe
werden über den bestehenden Kanal verteilt; offene Kaufcodes bleiben geheim.
Details stehen im [Betriebshandbuch](docs/betrieb/hauptsystem.md#5c-gutscheine-und-freischaltcodes)
und im [aktuellen Umsetzungsplan](docs/superpowers/plans/2026-10-05-stufe-1a-III-gutscheine.md).
Der historische Plan vom 03.10.2026 bleibt als abgelöster Entwurf erhalten.

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
