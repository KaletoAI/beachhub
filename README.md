# Beachhub

Buchung, Abrechnung und Steuerung einer Beachvolleyballhalle im Winterbetrieb.
Drei Teilsysteme in einem Repository: Buchungsportal (`portal/`), Hauptsystem (`core/`), Hallendienst (`hall/`).

Status: Stufe 1 (Hauptsystem) fertig implementiert. Buchung, Tarife, Sperren, Stornos, Monatslauf
mit Sammelrechnungen und signiertem Lesestand laufen im Admin-UI (`core/`); Betrieb hinter
WireGuard mit Caddy ist dokumentiert (`docs/betrieb/hauptsystem.md`). Stufe 2 (Portal) und Stufe 3
(Halle) folgen.

- Technische Spezifikation: `docs/superpowers/specs/2026-09-05-beachhub-design.md`
- Dokument für den Betreiber (nicht technisch): `docs/betreiber/Beachhub-Anforderungen-und-Loesungskonzept.pdf`
  (Quelle: `docs/betreiber/anforderungen-und-loesungskonzept.html`, erzeugt mit WeasyPrint)
- Betriebshandbuch (Inbetriebnahme, Backup, WireGuard): `docs/betrieb/hauptsystem.md`
- Entwickler-Kurzstart: `core/README.md`

## Entwicklung

```bash
pip install -e shared[dev] -e core[dev]
docker compose -f core/docker-compose.yml up -d db
cd core && pytest
```

Details (venv, `.env`, Migrationen, CLI) siehe `core/README.md`.

## Lizenz

Beachhub steht unter der [PolyForm Noncommercial License 1.0.0](LICENSE.md).

- **Erlaubt:** private Nutzung, Nutzung durch gemeinnützige Organisationen und Vereine, Forschung, Lehre, Ausprobieren, Weitergabe und Änderung, solange der Zweck nicht kommerziell ist.
- **Nicht erlaubt ohne Genehmigung:** jede kommerzielle Nutzung, zum Beispiel der Betrieb für eine andere gewerblich betriebene Halle oder der Einbau in ein kommerzielles Angebot.
- **Kommerzielle Lizenz:** auf Anfrage beim Rechteinhaber über ein GitHub-Issue oder die im Profil hinterlegte Kontaktadresse.

Urheber und Rechteinhaber ist der Autor des Projekts. Die Halle, für die Beachhub entwickelt wird, ist Anwender, nicht Auftraggeber im Sinne einer Auftragsentwicklung.
