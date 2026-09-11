"""Durchgängige Gestaltung der Verwaltungsoberfläche.

Die Tests halten drei Zusagen fest, die sonst bei jeder neuen Seite wieder verloren gehen:
Aktionen in Tabellen sehen überall gleich aus, lange Formulare sind in benannte Gruppen
geteilt, und die Zuordnung von Geräten zur Haussteuerung steht nicht in diesem System.
"""

import re

import pytest
from beachhub_core.models import Feld
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

SEITEN_MIT_TABELLEN = [
    "/admin/felder",
    "/admin/betriebszeiten",
    "/admin/ausnahmetage",
    "/admin/kundengruppen",
    "/admin/tarife",
    "/admin/kunden",
    "/admin/rechnungen",
    "/admin/system",
    "/admin/system/stornos",
    "/admin/system/audit",
]

_ZELLE = re.compile(r"<td\b[^>]*>(.*?)</td>", re.DOTALL)
_LINK_OHNE_KLASSE = re.compile(r"<a (?![^>]*\bclass=)[^>]*href=", re.DOTALL)


@pytest.mark.parametrize("seite", SEITEN_MIT_TABELLEN)
def test_aktionen_in_tabellen_sind_gestaltet(eingeloggt: TestClient, seite: str) -> None:
    """Ein nacktes <a> in einer Tabellenzelle erbt die Browser-Darstellung und fällt neben
    den gestalteten Schaltflächen auf. Jede Aktion trägt deshalb eine Klasse."""
    text = eingeloggt.get(seite).text
    for zelle in _ZELLE.findall(text):
        assert not _LINK_OHNE_KLASSE.search(zelle), f"{seite}: ungestalteter Link in {zelle!r}"


@pytest.mark.parametrize("seite", ["/admin/tarife", "/admin/konfiguration"])
def test_lange_formulare_sind_in_gruppen_geteilt(eingeloggt: TestClient, seite: str) -> None:
    text = eingeloggt.get(seite).text
    assert text.count("<fieldset") >= 2, f"{seite} reiht die Felder ohne Gliederung untereinander"
    assert "<legend" in text, f"{seite} benennt seine Gruppen nicht"


def test_konfiguration_zeigt_klarnamen_statt_schluessel(eingeloggt: TestClient) -> None:
    text = eingeloggt.get("/admin/konfiguration").text
    assert "Buchungsfenster" in text
    assert "Stornofrist" in text
    # Der technische Schlüssel bleibt als Formularname nötig, darf aber nicht die Beschriftung sein.
    assert ">fenster_tage<" not in text


def test_feldseite_ordnet_keine_geraete_zu(eingeloggt: TestClient, db: Session) -> None:
    """Welche Lampe zu welchem Feld gehört, weiß das Addon auf dem Home-Assistant-Server –
    nicht das Hauptsystem."""
    feld = Feld(name="Feld 1", reihenfolge=1)
    db.add(feld)
    db.commit()
    text = eingeloggt.get(f"/admin/felder/{feld.id}").text
    for weg in ("ha_licht_entity", "ha_praesenz_entity", "heizzone", "Home-Assistant"):
        assert weg not in text, f"Feldseite zeigt weiterhin {weg}"


def test_feldmodell_kennt_keine_geraetezuordnung() -> None:
    for weg in ("ha_licht_entity", "ha_praesenz_entity", "heizzone"):
        assert not hasattr(Feld, weg), f"Feld hat weiterhin die Spalte {weg}"
