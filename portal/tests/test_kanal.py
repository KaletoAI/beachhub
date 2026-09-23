import threading
import time
import uuid

import pytest
from beachhub_portal.config import settings
from beachhub_portal.database import SessionLocal
from beachhub_portal.models import Anfrage, KanalKontakt, Konto, Lesestand
from beachhub_portal.services import anfragen, lesestand
from fastapi.testclient import TestClient
from hilfen import KUNDE_ID, belegung, konto, signiert, tarife
from sqlalchemy.orm import Session

KOPF = {"Authorization": "Bearer test-kanal-token"}


@pytest.fixture
def kanal_client(client: TestClient) -> TestClient:
    client.headers.update(KOPF)
    return client


def _stelle(typ: str = "konto_geaendert", konto_id: uuid.UUID | None = None, **nutzlast) -> Anfrage:
    with SessionLocal() as db:
        return anfragen.stelle(
            db, typ=typ, konto_id=konto_id, nutzlast=nutzlast or {"anzeigename": "A", "bisher": "B"}
        )


def test_ohne_oder_mit_falschem_token_401(client: TestClient) -> None:
    assert client.get("/core/anfragen?warten=0").status_code == 401
    falsch = {"Authorization": "Bearer falsch"}
    assert client.get("/core/anfragen?warten=0", headers=falsch).status_code == 401


def test_ohne_eingerichteten_kanal_404(
    kanal_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "kanal_token", "")
    assert kanal_client.get("/core/anfragen?warten=0").status_code == 404


def test_stelle_prueft_nutzlast(db: Session) -> None:
    with pytest.raises(ValueError):
        anfragen.stelle(db, typ="gibtsnicht", konto_id=None, nutzlast={})
    with pytest.raises(ValueError):
        anfragen.stelle(db, typ="buchung_anfragen", konto_id=None, nutzlast={"feld_id": "x"})


def test_abholen_aelteste_zuerst_und_nur_einmal(kanal_client: TestClient, uhr_steht) -> None:
    erste = _stelle()
    uhr_steht.weiter(seconds=1)
    zweite = _stelle()
    liste = kanal_client.get("/core/anfragen?warten=0").json()["anfragen"]
    assert [a["anfrage_id"] for a in liste] == [str(erste.id), str(zweite.id)]
    assert kanal_client.get("/core/anfragen?warten=0").json() == {"anfragen": []}


def test_unbeantwortete_nach_60_s_erneut(kanal_client: TestClient, uhr_steht) -> None:
    a = _stelle()
    kanal_client.get("/core/anfragen?warten=0")
    uhr_steht.weiter(seconds=59)
    assert kanal_client.get("/core/anfragen?warten=0").json()["anfragen"] == []
    uhr_steht.weiter(seconds=2)
    liste = kanal_client.get("/core/anfragen?warten=0").json()["anfragen"]
    assert [x["anfrage_id"] for x in liste] == [str(a.id)]


def test_long_poll_wacht_bei_neuer_anfrage_auf(kanal_client: TestClient) -> None:
    threading.Timer(0.3, _stelle).start()
    beginn = time.monotonic()
    r = kanal_client.get("/core/anfragen?warten=5")
    assert len(r.json()["anfragen"]) == 1
    assert time.monotonic() - beginn < 2


def test_long_poll_ohne_anfrage_wartet_und_liefert_leer(kanal_client: TestClient) -> None:
    beginn = time.monotonic()
    assert kanal_client.get("/core/anfragen?warten=1").json() == {"anfragen": []}
    assert 0.9 <= time.monotonic() - beginn < 3


def test_warten_ist_begrenzt(kanal_client: TestClient) -> None:
    assert kanal_client.get("/core/anfragen?warten=31").status_code == 422


def test_abholen_liefert_kunde_und_merkt_kontakt(
    kanal_client: TestClient, db: Session, uhr_steht
) -> None:
    k = Konto(email="a@x.de", anzeigename="A", kunde_id=KUNDE_ID)
    db.add(k)
    db.commit()
    _stelle(konto_id=k.id)
    a = kanal_client.get("/core/anfragen?warten=0").json()["anfragen"][0]
    assert a["kunde_id"] == str(KUNDE_ID) and a["konto_id"] == str(k.id)
    assert db.get(KanalKontakt, 1).letzter_abruf == uhr_steht.jetzt


def test_antwort_konto_angelegt_setzt_kunde(kanal_client: TestClient, db: Session) -> None:
    k = Konto(email="a@x.de", anzeigename="A")
    db.add(k)
    db.commit()
    a = _stelle("konto_angelegt", k.id, email="a@x.de", anzeigename="A")
    antworten = [
        {"anfrage_id": str(a.id), "antwort": {"status": "ok", "kunde_id": str(KUNDE_ID)}},
        {"anfrage_id": str(uuid.uuid4()), "antwort": {"status": "ok"}},
    ]
    assert kanal_client.post("/core/antworten", json={"antworten": antworten}).json() == {"ok": 1}
    db.expire_all()
    assert db.get(Konto, k.id).kunde_id == KUNDE_ID
    zeile = db.get(Anfrage, a.id)
    assert zeile.status == "beantwortet" and zeile.antwort_json["kunde_id"] == str(KUNDE_ID)


def test_zweite_antwort_wird_ignoriert(kanal_client: TestClient, db: Session) -> None:
    a = _stelle()
    ok = {"anfrage_id": str(a.id), "antwort": {"status": "ok"}}
    fehler = {"anfrage_id": str(a.id), "antwort": {"status": "fehler"}}
    kanal_client.post("/core/antworten", json={"antworten": [ok]})
    assert kanal_client.post("/core/antworten", json={"antworten": [fehler]}).json() == {"ok": 0}
    assert db.get(Anfrage, a.id).antwort_json == {"status": "ok"}


def test_lesestand_uebernehmen_und_version(kanal_client: TestClient, db: Session) -> None:
    r = kanal_client.post(
        "/core/lesestand", json={"dokumente": [signiert("belegung", 2, belegung())]}
    )
    assert r.status_code == 200 and r.json()["uebernommen"] == ["belegung"]
    assert lesestand.belegung(db).fenster_tage == 14
    alt = signiert("belegung", 2, belegung(fenster_tage=7))
    r = kanal_client.post("/core/lesestand", json={"dokumente": [alt]})
    assert r.json()["verworfen"] == [{"dokument": "belegung", "grund": "version_alt"}]
    assert kanal_client.get("/core/lesestand/versionen").json() == {"belegung": 2}


def test_lesestand_falsche_signatur_422(kanal_client: TestClient, db: Session) -> None:
    dok = signiert("belegung", 1, belegung())
    dok["inhalt"]["fenster_tage"] = 99
    r = kanal_client.post(
        "/core/lesestand", json={"dokumente": [dok, signiert("tarife", 1, tarife())]}
    )
    assert r.status_code == 422
    assert r.json()["verworfen"] == [{"dokument": "belegung", "grund": "signatur"}]
    assert r.json()["uebernommen"] == ["tarife"]
    assert lesestand.belegung(db) is None


def test_lesestand_nur_erlaubte_dokumente(kanal_client: TestClient, db: Session) -> None:
    r = kanal_client.post(
        "/core/lesestand", json={"dokumente": [signiert("hallenplan", 1, {"x": 1})]}
    )
    assert r.status_code == 200
    assert r.json()["verworfen"] == [{"dokument": "hallenplan", "grund": "unbekannt"}]
    assert db.get(Lesestand, "hallenplan") is None


def test_konto_dokument_lesen(kanal_client: TestClient, db: Session) -> None:
    dok = signiert(f"konto:{KUNDE_ID}", 1, konto())
    kanal_client.post("/core/lesestand", json={"dokumente": [dok]})
    assert lesestand.konto(db, KUNDE_ID).kundengruppe == "Privat"
    assert lesestand.konto(db, None) is None
