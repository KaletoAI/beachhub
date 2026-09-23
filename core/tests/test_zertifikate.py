import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from beachhub_core import zertifikate
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _lade(pfad: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(pfad.read_bytes())


def test_ca_und_client_zertifikate(tmp_path: Path) -> None:
    zertifikate.erzeuge(tmp_path)
    ca = _lade(tmp_path / "ca.crt")
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    for name in ("portal-kanal", "halle"):
        c = _lade(tmp_path / f"{name}.crt")
        c.verify_directly_issued_by(ca)
        eku = c.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        assert ExtendedKeyUsageOID.CLIENT_AUTH in eku
        assert c.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == name
        assert (tmp_path / f"{name}.key").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "ca.key").stat().st_mode & 0o777 == 0o600


def test_ca_bleibt_bei_rotation(tmp_path: Path) -> None:
    zertifikate.erzeuge(tmp_path)
    ca_vorher = (tmp_path / "ca.crt").read_bytes()
    halle_vorher = (tmp_path / "halle.crt").read_bytes()
    zertifikate.erzeuge(tmp_path, ["halle"])
    assert (tmp_path / "ca.crt").read_bytes() == ca_vorher
    assert (tmp_path / "halle.crt").read_bytes() != halle_vorher
    _lade(tmp_path / "halle.crt").verify_directly_issued_by(_lade(tmp_path / "ca.crt"))


def test_schluessel_werden_bei_rotation_nicht_ueberschrieben(tmp_path: Path) -> None:
    """Private Schlüssel werden exklusiv angelegt (wie erzeuge_schluessel) und nie überschrieben."""
    zertifikate.erzeuge(tmp_path)
    ca_schluessel_vorher = (tmp_path / "ca.key").read_bytes()
    halle_schluessel_vorher = (tmp_path / "halle.key").read_bytes()
    zertifikate.erzeuge(tmp_path, ["halle"])
    assert (tmp_path / "ca.key").read_bytes() == ca_schluessel_vorher
    assert (tmp_path / "halle.key").read_bytes() == halle_schluessel_vorher


@pytest.mark.parametrize("name", ["../x", "Portal", "", "a" * 41])
def test_ungueltiger_name(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        zertifikate.erzeuge(tmp_path, [name])


def test_python_ssl_laedt_die_dateien(tmp_path: Path) -> None:
    """httpx baut seinen TLS-Kontext mit dem ssl-Modul; die Dateien müssen dort ladbar sein."""
    zertifikate.erzeuge(tmp_path)
    ctx = ssl.create_default_context(cafile=str(tmp_path / "ca.crt"))
    ctx.load_cert_chain(str(tmp_path / "portal-kanal.crt"), str(tmp_path / "portal-kanal.key"))
    assert ctx.cert_store_stats()["x509_ca"] == 1

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_verify_locations(cafile=str(tmp_path / "ca.crt"))
    server_ctx.verify_mode = ssl.CERT_REQUIRED
    assert server_ctx.cert_store_stats()["x509_ca"] == 1

    ca = _lade(tmp_path / "ca.crt")
    client = _lade(tmp_path / "portal-kanal.crt")
    # wirft nichts: Signatur des Client-Zertifikats stammt tatsächlich von der CA.
    client.verify_directly_issued_by(ca)


def test_fremd_signiertes_zertifikat_faellt_durch(tmp_path: Path) -> None:
    """Ein Zertifikat mit dem Namen der CA, aber fremder Signatur, darf nicht verifizieren."""
    zertifikate.erzeuge(tmp_path)
    ca = _lade(tmp_path / "ca.crt")

    fremder_schluessel = ec.generate_private_key(ec.SECP256R1())
    jetzt = datetime.now(UTC)
    fremdes_zertifikat = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "portal-kanal")]))
        .issuer_name(ca.subject)
        .public_key(fremder_schluessel.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(jetzt - timedelta(minutes=5))
        .not_valid_after(jetzt + timedelta(days=30))
        .sign(fremder_schluessel, hashes.SHA256())
    )
    with pytest.raises(InvalidSignature):
        fremdes_zertifikat.verify_directly_issued_by(ca)
