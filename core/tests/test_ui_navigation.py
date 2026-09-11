"""Die Navigation entsteht zentral in `templating.render` und sieht deshalb auf jeder
Admin-Seite gleich aus: Hauptmenü und Unternavigation stehen als zwei Zeilen direkt
untereinander im Kopfbereich, nicht im Seiteninhalt."""

import pytest
from beachhub_core.navigation import NAVIGATION, bereich_fuer_pfad
from fastapi.testclient import TestClient

ALLE_NAVIGATIONSZIELE = [
    ziel
    for bereich in NAVIGATION
    for ziel in ([bereich.pfad] + [u.pfad for u in bereich.unterpunkte])
]

# Seiten, die ohne angelegte Stammdaten erreichbar sind.
SEITEN = [
    "/admin",
    "/admin/belegung",
    "/admin/kunden",
    "/admin/rechnungen",
    "/admin/felder",
    "/admin/betriebszeiten",
    "/admin/ausnahmetage",
    "/admin/kundengruppen",
    "/admin/tarife",
    "/admin/konfiguration",
    "/admin/system",
    "/admin/system/stornos",
    "/admin/system/audit",
]

STAMMDATEN_UNTERPUNKTE = [
    "/admin/felder",
    "/admin/betriebszeiten",
    "/admin/ausnahmetage",
    "/admin/kundengruppen",
    "/admin/tarife",
    "/admin/konfiguration",
]


@pytest.mark.parametrize("seite", SEITEN)
def test_jede_seite_zeigt_das_hauptmenue(eingeloggt: TestClient, seite: str) -> None:
    antwort = eingeloggt.get(seite)
    assert antwort.status_code == 200, seite
    for bereich in NAVIGATION:
        assert f'href="{bereich.pfad}"' in antwort.text, f"{seite} verlinkt {bereich.name} nicht"


@pytest.mark.parametrize("seite", STAMMDATEN_UNTERPUNKTE)
def test_stammdatenseiten_zeigen_alle_unterpunkte(eingeloggt: TestClient, seite: str) -> None:
    text = eingeloggt.get(seite).text
    for ziel in STAMMDATEN_UNTERPUNKTE:
        assert f'href="{ziel}"' in text, f"{seite} verlinkt {ziel} nicht"


@pytest.mark.parametrize("seite", ["/admin/system", "/admin/system/stornos", "/admin/system/audit"])
def test_systemseiten_verlinken_stornos_und_protokoll(eingeloggt: TestClient, seite: str) -> None:
    text = eingeloggt.get(seite).text
    assert 'href="/admin/system/stornos"' in text
    assert 'href="/admin/system/audit"' in text


def test_unternavigation_steht_im_kopf_ueber_dem_inhalt(eingeloggt: TestClient) -> None:
    text = eingeloggt.get("/admin/tarife").text
    assert 'class="unternav"' in text
    assert text.index('class="unternav"') < text.index("<main")


def test_aktiver_bereich_und_unterpunkt_sind_markiert(eingeloggt: TestClient) -> None:
    text = eingeloggt.get("/admin/tarife").text
    assert 'href="/admin/felder" aria-current="page"' in text  # Bereich Stammdaten
    assert 'href="/admin/tarife" aria-current="page"' in text  # Unterpunkt Tarife


def test_bereich_ohne_unterpunkte_zeigt_keine_zweite_zeile(eingeloggt: TestClient) -> None:
    assert 'class="unternav"' not in eingeloggt.get("/admin/kunden").text


def test_kundengruppen_gehoert_zu_stammdaten_nicht_zu_kunden() -> None:
    """`/admin/kundengruppen` beginnt mit `/admin/kunden` – die Zuordnung darf sich davon
    nicht täuschen lassen, sonst zeigt die Seite die falsche Unternavigation."""
    assert bereich_fuer_pfad("/admin/kundengruppen").name == "Stammdaten"
    assert bereich_fuer_pfad("/admin/kunden").name == "Kunden"


def test_unterseiten_bleiben_im_richtigen_bereich() -> None:
    assert bereich_fuer_pfad("/admin/felder/abc-123").name == "Stammdaten"
    assert bereich_fuer_pfad("/admin/belegung/buchung/abc-123").name == "Belegung"
    assert bereich_fuer_pfad("/admin/rechnungen/abc-123/pdf").name == "Rechnungen"


def test_unbekannter_pfad_hat_keinen_bereich() -> None:
    assert bereich_fuer_pfad("/admin/gibtsnicht") is None


@pytest.mark.parametrize("ziel", ALLE_NAVIGATIONSZIELE)
def test_jedes_navigationsziel_ist_erreichbar(eingeloggt: TestClient, ziel: str) -> None:
    """Ein Menüpunkt, der ohne weiteren Kontext eine Fehlerseite liefert, ist ein Fehler.
    Geprüft wird bewusst ohne angelegte Stammdaten: Wer frisch installiert, klickt genau
    so durch das Menü."""
    antwort = eingeloggt.get(ziel, follow_redirects=True)
    assert antwort.status_code == 200, f"{ziel} antwortet mit {antwort.status_code}"


def test_unternavigation_ist_wie_das_hauptmenue_ausgerichtet(eingeloggt: TestClient) -> None:
    """Beide Zeilen brauchen denselben zentrierten Inhaltsbereich, sonst beginnt das
    Untermenü auf breiten Bildschirmen am linken Rand, während der Titel eingerückt ist."""
    text = eingeloggt.get("/admin/tarife").text
    assert 'class="unternavzeile"' in text
