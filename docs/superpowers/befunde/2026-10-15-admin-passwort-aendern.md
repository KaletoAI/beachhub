# Befund: Admin kann sein Passwort nicht ändern

**Gemeldet:** 15.10.2026 von Kai beim manuellen Test nach der Nachreview Stufe 1a-II
**Status:** offen
**Teilsystem:** `core` (Admin-UI)

## Befund

In der Admin-UI gibt es keine Möglichkeit, das eigene Passwort zu ändern. Auch ein Zurücksetzen
durch einen anderen Admin fehlt. Die Kommandozeile `beachhub-core` kennt nur `create-admin`,
`keygen` und `zertifikate`.

Beim Test musste das Passwort des Entwicklungs-Admins direkt in der Datenbank neu gesetzt werden
(`passwort_hash` über `auth.hash_passwort`, Sitzungen des Admins gelöscht).

## Ist

- `core/beachhub_core/routes/admin_auth.py` bietet nur Login und Logout.
- A-ADM-7 (`docs/superpowers/specs/2026-09-05-beachhub-design.md`) verlangt Login mit Passwort
  und TOTP sowie mehrere Admins mit den Rollen `admin` und `lesend`, regelt aber keine
  Passwortänderung.

## Soll (zu klären)

- Ein angemeldeter Admin ändert sein Passwort selbst: altes Passwort und TOTP-Code bestätigen,
  Mindestlänge 12 Zeichen wie bei `create-admin`.
- Danach die übrigen Sitzungen dieses Admins beenden.
- Offen: Gilt das auch für die Rolle `lesend`? Braucht es einen Notfallweg per Kommandozeile
  (Passwort oder TOTP zurücksetzen), falls kein Admin mehr Zugang hat?
- Spezifikation (A-ADM-7), `core/README.md` und Betriebshandbuch ergänzen.
