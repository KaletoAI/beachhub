from pathlib import Path

from beachhub_shared.signatur import (
    erzeuge_schluesselpaar,
    lade_privatschluessel,
    oeffentlicher_schluessel,
    pruefe,
    signiere,
)


def test_signatur_ist_pruefbar() -> None:
    priv, pub = erzeuge_schluesselpaar()
    doc = {"dokument": "belegung", "version": 3, "inhalt": {"felder": []}}
    sig = signiere(doc, priv)
    assert pruefe(doc, sig, pub)


def test_manipulation_faellt_auf() -> None:
    priv, pub = erzeuge_schluesselpaar()
    doc = {"version": 1}
    sig = signiere(doc, priv)
    assert not pruefe({"version": 2}, sig, pub)


def test_falscher_schluessel_faellt_auf() -> None:
    priv, _ = erzeuge_schluesselpaar()
    _, pub2 = erzeuge_schluesselpaar()
    assert not pruefe({"a": 1}, signiere({"a": 1}, priv), pub2)


def test_schluessel_laden_und_ableiten(tmp_path: Path) -> None:
    priv, pub = erzeuge_schluesselpaar()
    pfad = tmp_path / "signatur.key"
    pfad.write_text(priv + "\n")
    assert lade_privatschluessel(pfad) == priv
    assert oeffentlicher_schluessel(priv) == pub
