# Hallendienst

Steuert Licht, Heizung und Tür der Halle über Home Assistant nach dem signierten 7-Tage-Plan des
Hauptsystems. Er arbeitet ohne Verbindung zum Hauptsystem weiter, bis der Plan abläuft.

- Design: `docs/superpowers/specs/2026-09-23-hallendienst-design.md`
- Betrieb und HA-Einrichtung: `docs/betrieb/hallendienst.md`

## Entwicklung

```bash
pip install -e shared[dev] -e hall[dev]
cd hall && pytest
```

Die Tests brauchen weder Home Assistant noch das Hauptsystem. `tests/ha_simulator.py` und
`tests/core_simulator.py` ersetzen beide, eine simulierte Uhr ersetzt das Warten.

Einen HA-Simulator für eigene Versuche startet `cd hall && python -m tests.ha_simulator`
(Port 8123, Token `ha-test-token`).
