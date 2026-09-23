import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from beachhub_core import kanal
from beachhub_core.config import settings
from beachhub_core.models import Kunde, Kundengruppe
from beachhub_core.services import anfragen, kunden, lesestand
from beachhub_shared import kanal as vertrag
from sqlalchemy import select
from sqlalchemy.orm import Session

TOKEN = "t"


class FakePortal:
    """Spielt das Portal: liefert vorbereitete Anfragen und merkt sich alles, was ankommt."""

    def __init__(self) -> None:
        self.anfragen: list[dict] = []
        self.aufrufe: list[tuple[str, str]] = []
        self.antworten: list[dict] = []
        self.dokumente: list[dict] = []
        self.versionen: dict[str, int] = {}
        self.lesestand_status = 200
        self.kaputt = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.kaputt:
            raise httpx.ConnectError("Portal weg", request=request)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        pfad = request.url.path
        self.aufrufe.append((request.method, pfad))
        if pfad == "/core/anfragen":
            liste, self.anfragen = self.anfragen, []
            return httpx.Response(200, json={"anfragen": liste})
        if pfad == "/core/antworten":
            neu = json.loads(request.content)["antworten"]
            self.antworten += neu
            return httpx.Response(200, json={"ok": len(neu)})
        if pfad == "/core/lesestand":
            self.dokumente += json.loads(request.content)["dokumente"]
            return httpx.Response(self.lesestand_status, json={"uebernommen": [], "verworfen": []})
        if pfad == "/core/lesestand/versionen":
            return httpx.Response(200, json=self.versionen)
        return httpx.Response(404)


class Uhr:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def portal() -> FakePortal:
    return FakePortal()


@pytest.fixture
def uhr() -> Uhr:
    return Uhr()


@pytest.fixture
def k(portal: FakePortal, uhr: Uhr) -> kanal.Kanal:
    client = httpx.Client(
        transport=httpx.MockTransport(portal),
        base_url="http://portal",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    return kanal.Kanal(client, warten=0, uhr=uhr)


@pytest.fixture
def welt(db: Session) -> None:
    Path(settings.signatur_privatschluessel_pfad).unlink(missing_ok=True)
    lesestand.erzeuge_schluessel()
    db.add(Kundengruppe(name="Privat"))
    db.commit()


def konto_angelegt(email: str = "anna@x.de") -> dict:
    return vertrag.Anfrage(
        anfrage_id=uuid.uuid4(),
        typ="konto_angelegt",
        konto_id=uuid.uuid4(),
        nutzlast={"email": email, "anzeigename": "Anna"},
        erstellt_am=datetime.now(UTC),
    ).model_dump(mode="json")


def test_abholen_verarbeitet_und_antwortet(db: Session, welt, portal: FakePortal, k) -> None:
    portal.anfragen = [konto_angelegt()]
    assert k.abholen() == 1
    kunde = db.scalar(select(Kunde))
    assert portal.antworten[0]["antwort"] == {"status": "ok", "kunde_id": str(kunde.id)}
    assert f"konto:{kunde.id}" in [d["dokument"] for d in portal.dokumente]
    # Erst der Lesestand, dann die Antwort: Das Portal soll zeigen können, was es bestätigt.
    assert portal.aufrufe.index(("POST", "/core/lesestand")) < portal.aufrufe.index(
        ("POST", "/core/antworten")
    )


def test_leere_runde_sendet_nichts(welt, portal: FakePortal, k) -> None:
    assert k.abholen() == 0
    assert portal.aufrufe == [("GET", "/core/anfragen")]


def test_erneute_zustellung_gleiche_antwort(db: Session, welt, portal: FakePortal, k) -> None:
    a = konto_angelegt()
    portal.anfragen = [a]
    k.abholen()
    portal.anfragen = [a]
    k.abholen()
    assert portal.antworten[0] == portal.antworten[1]
    assert len(db.scalars(select(Kunde)).all()) == 1


def test_fehler_wird_beantwortet_und_alarmiert(
    welt, portal: FakePortal, k, monkeypatch: pytest.MonkeyPatch, mail_ausgang
) -> None:
    def kaputt(*args, **kwargs):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(anfragen, "_konto_angelegt", kaputt)
    portal.anfragen = [konto_angelegt()]
    k.abholen()
    assert portal.antworten[0]["antwort"] == {"status": "fehler"}
    assert any(m["betreff"] == "[Beachhub] Portal-Anfrage fehlgeschlagen" for m in mail_ausgang)


def test_verteilen_nur_portal_dokumente(
    db: Session, welt, portal: FakePortal, k, monkeypatch: pytest.MonkeyPatch
) -> None:
    lesestand.markiere_geaendert(db, "belegung")
    db.commit()
    lesestand.verarbeite_geaenderte(db)
    beleg = lesestand.lade("belegung")
    hallenplan = beleg.model_copy(update={"dokument": "hallenplan"})
    monkeypatch.setattr(lesestand, "verarbeite_geaenderte", lambda db: ["belegung", "hallenplan"])
    monkeypatch.setattr(
        lesestand, "lade", lambda name: {"belegung": beleg, "hallenplan": hallenplan}[name]
    )
    assert k.verteilen() == 1
    assert [d["dokument"] for d in portal.dokumente] == ["belegung"]


def test_verteilen_sendet_fremd_veroeffentlichtes_dokument(
    db: Session, welt, portal: FakePortal, k
) -> None:
    # K1: Was der 5-Minuten-Job (jobs._job_lesestand) oder der Admin-Knopf "Lesestand erzeugen"
    # veröffentlicht, soll der Kanal beim nächsten verteilen() auch ohne eigenen Aufruf von
    # verarbeite_geaenderte erkennen und senden – nicht erst beim stündlichen Abgleich.
    lesestand.markiere_geaendert(db, "belegung")
    db.commit()
    lesestand.verarbeite_geaenderte(db)
    assert k.verteilen() == 1
    assert [d["dokument"] for d in portal.dokumente] == ["belegung"]


def test_verteilen_erneut_aufgerufen_sendet_nicht_doppelt(
    db: Session, welt, portal: FakePortal, k
) -> None:
    lesestand.markiere_geaendert(db, "belegung")
    db.commit()
    assert k.verteilen() == 1
    assert k.verteilen() == 0
    assert len(portal.dokumente) == 1


def test_verteilen_ohne_portal_konto_wird_nicht_gesendet(
    db: Session, welt, portal: FakePortal, k
) -> None:
    gruppe = db.scalar(select(Kundengruppe))
    kunde = kunden.lege_an(db, name="Abo", email="abo@x.de", kundengruppe_id=gruppe.id)
    db.commit()
    assert kunde.portal_konto_id is None
    lesestand.markiere_geaendert(db, f"konto:{kunde.id}")
    db.commit()
    assert k.verteilen() == 0
    assert portal.dokumente == []


def test_abgleich_ohne_portal_konto_wird_nicht_gesendet(
    db: Session, welt, portal: FakePortal, k
) -> None:
    gruppe = db.scalar(select(Kundengruppe))
    kunde = kunden.lege_an(db, name="Abo", email="abo@x.de", kundengruppe_id=gruppe.id)
    db.commit()
    lesestand.markiere_geaendert(db, f"konto:{kunde.id}")
    db.commit()
    lesestand.verarbeite_geaenderte(db)
    assert k.abgleichen() == 0
    assert portal.dokumente == []


def test_abgleich_schickt_fehlende_und_aeltere(db: Session, welt, portal: FakePortal, k) -> None:
    lesestand.markiere_geaendert(db, "belegung", "tarife")
    db.commit()
    lesestand.verarbeite_geaenderte(db)
    beleg = lesestand.lade("belegung")
    (settings.data_dir / "lesestand" / "hallenplan.json").write_text(
        beleg.model_copy(update={"dokument": "hallenplan"}).model_dump_json(), encoding="utf-8"
    )
    portal.versionen = {"belegung": beleg.version}
    assert k.abgleichen() == 1
    assert [d["dokument"] for d in portal.dokumente] == ["tarife"]


def test_lesestand_abgelehnt_alarmiert(
    db: Session, welt, portal: FakePortal, k, mail_ausgang
) -> None:
    portal.lesestand_status = 422
    lesestand.markiere_geaendert(db, "belegung")
    db.commit()
    k.verteilen()
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] Lesestand vom Portal abgelehnt"


def test_portal_ausfall_alarm_nach_30_minuten(
    welt, portal: FakePortal, k, uhr: Uhr, mail_ausgang
) -> None:
    portal.kaputt = True
    assert k.runde() == 1.0
    assert k.runde() == 2.0
    uhr.t += 29 * 60
    k.runde()
    assert mail_ausgang == []
    uhr.t += 2 * 60
    k.runde()
    k.runde()
    assert [m["betreff"] for m in mail_ausgang] == ["[Beachhub] Portal nicht erreichbar"]
    portal.kaputt = False
    assert k.runde() == 0.0
    assert mail_ausgang[-1]["betreff"] == "[Beachhub] Portal wieder erreichbar"


def test_verteiler_runde_wirft_nie_und_gleicht_danach_ab(welt, portal: FakePortal, k) -> None:
    portal.kaputt = True
    k.verteiler_runde()
    portal.kaputt = False
    k.verteiler_runde()
    assert ("GET", "/core/lesestand/versionen") in portal.aufrufe


def test_threads_laufen_und_stoppen(db: Session, welt, portal: FakePortal, k) -> None:
    portal.anfragen = [konto_angelegt()]
    k.starte()
    try:
        ende = time.monotonic() + 5
        while not portal.antworten and time.monotonic() < ende:
            time.sleep(0.05)
    finally:
        k.stoppe()
    assert portal.antworten[0]["antwort"]["status"] == "ok"


def test_baue_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "portal_url", "https://portal.example:8443")
    monkeypatch.setattr(settings, "kanal_token", "geheim")
    c = kanal.baue_client()
    assert c.base_url.host == "portal.example" and c.base_url.port == 8443
    assert c.headers["authorization"] == "Bearer geheim"
