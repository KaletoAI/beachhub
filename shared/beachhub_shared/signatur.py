"""Ed25519-Signaturen über kanonisches JSON. Schlüssel als Hex-Strings."""

from pathlib import Path
from typing import Any

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from beachhub_shared.canonical_json import dumps


def erzeuge_schluesselpaar() -> tuple[str, str]:
    sk = SigningKey.generate()
    return sk.encode().hex(), sk.verify_key.encode().hex()


def oeffentlicher_schluessel(privat_hex: str) -> str:
    return SigningKey(bytes.fromhex(privat_hex)).verify_key.encode().hex()


def lade_privatschluessel(pfad: Path) -> str:
    return pfad.read_text(encoding="utf-8").strip()


def signiere(inhalt: dict[str, Any], privat_hex: str) -> str:
    sk = SigningKey(bytes.fromhex(privat_hex))
    return sk.sign(dumps(inhalt)).signature.hex()


def pruefe(inhalt: dict[str, Any], signatur_hex: str, oeffentlich_hex: str) -> bool:
    vk = VerifyKey(bytes.fromhex(oeffentlich_hex))
    try:
        vk.verify(dumps(inhalt), bytes.fromhex(signatur_hex))
    except (BadSignatureError, ValueError):
        return False
    return True
