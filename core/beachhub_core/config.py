"""Einstellungen aus Umgebung/.env. Ein Import, eine Instanz: `settings`."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET = "change-me"  # noqa: S105 -- Marker-Wert, kein echtes Geheimnis


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub"
    secret_key: str = INSECURE_SECRET
    pin_schluessel: str = INSECURE_SECRET  # 32 Byte base64 für PIN-Verschlüsselung
    app_env: str = "dev"  # dev | production
    cookie_secure: bool = False
    base_url: str = "http://127.0.0.1:8000"
    data_dir: Path = Path("./data")
    signatur_privatschluessel_pfad: Path = Path("./data/signatur.key")
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = "beachhub@example.org"
    enable_scheduler: bool = True
    hall_token: str = ""  # leer = Hallenschnittstelle abgeschaltet (404)
    betreiber_name: str = "Beachhalle"
    betreiber_adresse: str = ""
    betreiber_ust_id: str = ""
    betreiber_bank: str = ""

    # Kanal zum Portal (Spec Portal-Kern § 3). Ohne PORTAL_URL läuft kein Kanal.
    portal_url: str = ""
    kanal_token: str = ""
    portal_client_cert: str = ""
    portal_client_key: str = ""
    portal_ca: str = ""  # leer: System-CAs (Portal mit öffentlichem Zertifikat)
    enable_kanal: bool = True
    zahlung_provider: str = "fake"
    # Öffentliche Adresse des Portals für den Kunden-Browser (Rückkehr nach Zahlung, Task 5).
    # portal_url bleibt ausschließlich für den Kanal (:8443) und ist dafür nicht browsertauglich.
    portal_oeffentliche_url: str = ""

    @property
    def has_insecure_defaults(self) -> bool:
        return self.secret_key == INSECURE_SECRET or self.pin_schluessel == INSECURE_SECRET

    @property
    def produktionsfehler(self) -> list[str]:
        """Einstellungen, mit denen das Hauptsystem im Produktivbetrieb nicht starten darf."""
        fehler: list[str] = []
        if self.portal_url and not self.kanal_token:
            fehler.append("KANAL_TOKEN fehlt, obwohl PORTAL_URL gesetzt ist")
        if self.portal_url and self.zahlung_provider == "fake":
            fehler.append("ZAHLUNG_PROVIDER=fake ist nur für die Entwicklung")
        if self.portal_url and not self.portal_oeffentliche_url:
            fehler.append("PORTAL_OEFFENTLICHE_URL fehlt, obwohl PORTAL_URL gesetzt ist")
        # mTLS ist zum Portal Pflicht (A-2): Der Kanal darf im Produktivbetrieb nie ohne
        # Client-Zertifikat laufen.
        if self.portal_url and not self.portal_client_cert:
            fehler.append("PORTAL_CLIENT_CERT fehlt, obwohl PORTAL_URL gesetzt ist")
        if self.portal_url and not self.portal_client_key:
            fehler.append("PORTAL_CLIENT_KEY fehlt, obwohl PORTAL_URL gesetzt ist")
        # PORTAL_CA ist kein Pflichtfeld: Sie ist der Vertrauensanker für das Server-Zertifikat
        # des Portals; das Portal hat aber ein öffentliches (ACME-)Zertifikat auf :8443, dem die
        # System-CAs bereits vertrauen (kanal.baue_client() fällt dann auf sie zurück).
        return fehler


settings = Settings()
