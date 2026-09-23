import logging
import os
import ssl
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from beachhub_core import zertifikate
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _lade(pfad: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(pfad.read_bytes())


def _ca_schluessel(ordner: Path) -> ec.EllipticCurvePrivateKey:
    schluessel = serialization.load_pem_private_key((ordner / "ca.key").read_bytes(), password=None)
    assert isinstance(schluessel, ec.EllipticCurvePrivateKey)
    return schluessel


def _selbstsigniertes_ca_zertifikat(
    schluessel: ec.EllipticCurvePrivateKey, *, gueltig_von: datetime, gueltig_bis: datetime
) -> x509.Certificate:
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Beachhub interne CA")])
    return (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(schluessel.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(gueltig_von)
        .not_valid_after(gueltig_bis)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(schluessel, hashes.SHA256())
    )


def _mtls_handshake(
    *, server_cert: Path, server_key: Path, client_cert: Path, client_key: Path, ca: Path
) -> ssl.SSLObject:
    """Baut über Memory-BIOs einen echten TLS-Handshake auf und liefert die Server-Seite zurück.

    Server verlangt (wie das Portal-Caddyfile auf :8443) ein gültiges Client-Zertifikat gegen
    `ca`; der Client verifiziert das Server-Zertifikat nicht (das ist hier ein reines
    Testzertifikat – in Produktion hat das Portal ein öffentliches ACME-Zertifikat, siehe
    config.py PORTAL_CA-Kommentar). Das prüft genau das, was kanal.baue_client() tatsächlich tut:
    Client-Zertifikat + Schlüssel laden und damit gegen die CA authentifizieren.
    """
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(str(server_cert), str(server_key))
    server_ctx.load_verify_locations(cafile=str(ca))
    server_ctx.verify_mode = ssl.CERT_REQUIRED

    client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_ctx.check_hostname = False
    client_ctx.verify_mode = ssl.CERT_NONE
    client_ctx.load_cert_chain(str(client_cert), str(client_key))

    c_in, c_out, s_in, s_out = (ssl.MemoryBIO() for _ in range(4))
    client = client_ctx.wrap_bio(c_in, c_out)
    server = server_ctx.wrap_bio(s_in, s_out, server_side=True)

    client_fertig = server_fertig = False
    for _ in range(30):
        if not client_fertig:
            try:
                client.do_handshake()
                client_fertig = True
            except ssl.SSLWantReadError:
                pass
        if c_out.pending:
            s_in.write(c_out.read())
        if not server_fertig:
            try:
                server.do_handshake()
                server_fertig = True
            except ssl.SSLWantReadError:
                pass
        if s_out.pending:
            c_in.write(s_out.read())
        if client_fertig and server_fertig:
            return server
    raise AssertionError("mTLS-Handshake nicht innerhalb von 30 Runden abgeschlossen")


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


def test_zielordner_wird_mit_0700_angelegt(tmp_path: Path) -> None:
    ziel = tmp_path / "neu" / "zertifikate"
    zertifikate.erzeuge(ziel)
    assert stat.S_IMODE(ziel.stat().st_mode) == 0o700


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


def test_vorhandener_schluessel_wird_auf_0600_gesetzt(tmp_path: Path) -> None:
    zertifikate.erzeuge(tmp_path)
    os.chmod(tmp_path / "halle.key", 0o644)
    zertifikate.erzeuge(tmp_path, ["halle"])
    assert (tmp_path / "halle.key").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("name", ["../x", "Portal", "", "a" * 41])
def test_ungueltiger_name(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        zertifikate.erzeuge(tmp_path, [name])


def test_ca_zertifikat_ohne_schluessel_bricht_ab(tmp_path: Path) -> None:
    """Ein ca.crt ohne ca.key darf nie stillschweigend einen neuen (fremden) Schlüssel bekommen."""
    zertifikate.erzeuge(tmp_path)
    halle_vorher = (tmp_path / "halle.crt").read_bytes()
    (tmp_path / "ca.key").unlink()

    with pytest.raises(ValueError, match="ca.key"):
        zertifikate.erzeuge(tmp_path, ["halle"])

    assert not (tmp_path / "ca.key").exists()
    assert (tmp_path / "halle.crt").read_bytes() == halle_vorher


def test_ca_zertifikat_passt_nicht_zum_schluessel_bricht_ab(tmp_path: Path) -> None:
    zertifikate.erzeuge(tmp_path)
    ca_schluessel_vorher = (tmp_path / "ca.key").read_bytes()
    halle_vorher = (tmp_path / "halle.crt").read_bytes()

    fremder_schluessel = ec.generate_private_key(ec.SECP256R1())
    jetzt = datetime.now(UTC)
    fremdes_zertifikat = _selbstsigniertes_ca_zertifikat(
        fremder_schluessel,
        gueltig_von=jetzt - timedelta(minutes=5),
        gueltig_bis=jetzt + timedelta(days=365),
    )
    (tmp_path / "ca.crt").write_bytes(fremdes_zertifikat.public_bytes(serialization.Encoding.PEM))

    with pytest.raises(ValueError, match="passt nicht"):
        zertifikate.erzeuge(tmp_path, ["halle"])

    assert (tmp_path / "ca.key").read_bytes() == ca_schluessel_vorher
    assert (tmp_path / "halle.crt").read_bytes() == halle_vorher


def test_abgelaufene_ca_bricht_mit_klarer_meldung_ab(tmp_path: Path) -> None:
    zertifikate.erzeuge(tmp_path)
    schluessel = _ca_schluessel(tmp_path)
    halle_vorher = (tmp_path / "halle.crt").read_bytes()
    jetzt = datetime.now(UTC)
    abgelaufen = _selbstsigniertes_ca_zertifikat(
        schluessel, gueltig_von=jetzt - timedelta(days=400), gueltig_bis=jetzt - timedelta(days=1)
    )
    (tmp_path / "ca.crt").write_bytes(abgelaufen.public_bytes(serialization.Encoding.PEM))

    with pytest.raises(ValueError, match="abgelaufen"):
        zertifikate.erzeuge(tmp_path, ["halle"])

    assert (tmp_path / "halle.crt").read_bytes() == halle_vorher


def test_ca_bald_ablaufend_warnt_nur(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    zertifikate.erzeuge(tmp_path)
    schluessel = _ca_schluessel(tmp_path)
    jetzt = datetime.now(UTC)
    bald_ablaufend = _selbstsigniertes_ca_zertifikat(
        schluessel, gueltig_von=jetzt - timedelta(days=1), gueltig_bis=jetzt + timedelta(days=100)
    )
    (tmp_path / "ca.crt").write_bytes(bald_ablaufend.public_bytes(serialization.Encoding.PEM))

    # alembic/env.py ruft (via test_migrationen.py) logging.config.fileConfig() auf, das mit
    # seiner Vorgabe disable_existing_loggers=True jeden zu diesem Zeitpunkt schon vorhandenen
    # Logger stummschaltet – je nach Testreihenfolge auch diesen hier. Ohne den Reset würde
    # caplog dann nichts sehen, unabhängig vom gesetzten Level.
    logging.getLogger("beachhub_core.zertifikate").disabled = False
    caplog.set_level(logging.WARNING, logger="beachhub_core.zertifikate")
    zertifikate.erzeuge(tmp_path, ["halle"])  # kein Fehler, nur eine Warnung

    assert "Restlaufzeit" in caplog.text
    # Das Client-Zertifikat bleibt wie bisher auf das CA-Ablaufdatum gekappt.
    client = _lade(tmp_path / "halle.crt")
    assert client.not_valid_after_utc == bald_ablaufend.not_valid_after_utc


def _common_name(peer_cert: dict) -> str:
    for rdn in peer_cert["subject"]:
        for schluessel, wert in rdn:
            if schluessel == "commonName":
                return str(wert)
    raise AssertionError("kein commonName im Peer-Zertifikat")


def test_python_ssl_laedt_die_dateien_echter_handshake(tmp_path: Path) -> None:
    """httpx baut seinen TLS-Kontext mit dem ssl-Modul; ein echter mTLS-Handshake muss klappen."""
    zertifikate.erzeuge(tmp_path)
    server = _mtls_handshake(
        server_cert=tmp_path / "portal-kanal.crt",
        server_key=tmp_path / "portal-kanal.key",
        client_cert=tmp_path / "halle.crt",
        client_key=tmp_path / "halle.key",
        ca=tmp_path / "ca.crt",
    )
    peer = server.getpeercert()
    assert peer is not None
    assert _common_name(peer) == "halle"


def test_mtls_handshake_lehnt_fremde_ca_ab(tmp_path: Path) -> None:
    eigene, fremde = tmp_path / "eigene", tmp_path / "fremde"
    zertifikate.erzeuge(eigene)
    zertifikate.erzeuge(fremde)

    with pytest.raises(ssl.SSLError):
        _mtls_handshake(
            server_cert=eigene / "portal-kanal.crt",
            server_key=eigene / "portal-kanal.key",
            client_cert=fremde / "halle.crt",  # von einer fremden CA signiert
            client_key=fremde / "halle.key",
            ca=eigene / "ca.crt",
        )
