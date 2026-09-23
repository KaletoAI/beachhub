"""Interne CA und Client-Zertifikate für die mTLS-Kanäle (Portal, Halle).

Die CA bleibt bestehen, wenn sie schon existiert; Client-Zertifikate werden bei jedem Aufruf
neu ausgestellt. Laufzeit 1 Jahr, Rotation durch erneuten Aufruf (Hauptspec § 10). Private
Schlüssel (CA wie Client) werden nur beim ersten Aufruf erzeugt und danach nie überschrieben –
nur das jeweilige Zertifikat wird neu ausgestellt.
"""

import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_TAGE = 5 * 365
CLIENT_TAGE = 365
_NAME = re.compile(r"[a-z0-9-]{1,40}")


def _name(cn: str) -> x509.Name:
    return x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Beachhub"),
            x509.NameAttribute(NameOID.COMMON_NAME, cn),
        ]
    )


def _lade_oder_erzeuge_schluessel(pfad: Path) -> ec.EllipticCurvePrivateKey:
    if pfad.exists():
        schluessel = serialization.load_pem_private_key(pfad.read_bytes(), password=None)
        if not isinstance(schluessel, ec.EllipticCurvePrivateKey):
            raise ValueError(f"{pfad} ist kein EC-Schlüssel")
        return schluessel
    schluessel = ec.generate_private_key(ec.SECP256R1())
    daten = schluessel.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    # O_EXCL sorgt für ein atomares "nur anlegen, wenn noch nicht vorhanden" inklusive
    # FileExistsError, ohne die Lücke zwischen exists()-Prüfung und write (wie erzeuge_schluessel
    # in services/lesestand.py).
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(daten)
    return schluessel


def _key_usage(*, ca: bool) -> x509.KeyUsage:
    return x509.KeyUsage(
        digital_signature=not ca,
        content_commitment=False,
        key_encipherment=False,
        data_encipherment=False,
        key_agreement=False,
        key_cert_sign=ca,
        crl_sign=ca,
        encipher_only=False,
        decipher_only=False,
    )


def _lade_oder_erzeuge_ca(ordner: Path) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
    key_pfad, crt_pfad = ordner / "ca.key", ordner / "ca.crt"
    key = _lade_oder_erzeuge_schluessel(key_pfad)
    if crt_pfad.exists():
        return key, x509.load_pem_x509_certificate(crt_pfad.read_bytes())
    jetzt = datetime.now(UTC)
    name = _name("Beachhub interne CA")
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(jetzt - timedelta(minutes=5))
        .not_valid_after(jetzt + timedelta(days=CA_TAGE))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(_key_usage(ca=True), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    crt_pfad.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return key, cert


def erzeuge(ordner: Path, namen: Sequence[str] = ("portal-kanal", "halle")) -> list[Path]:
    for name in namen:
        if not _NAME.fullmatch(name):
            raise ValueError(f"Ungültiger Zertifikatsname: {name!r}")
    ordner.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = _lade_oder_erzeuge_ca(ordner)
    pfade = [ordner / "ca.crt"]
    for name in namen:
        key = _lade_oder_erzeuge_schluessel(ordner / f"{name}.key")
        jetzt = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(_name(name))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(jetzt - timedelta(minutes=5))
            .not_valid_after(min(jetzt + timedelta(days=CLIENT_TAGE), ca_cert.not_valid_after_utc))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(_key_usage(ca=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        (ordner / f"{name}.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        pfade += [ordner / f"{name}.crt", ordner / f"{name}.key"]
    return pfade
