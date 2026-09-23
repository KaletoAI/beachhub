"""Einstellungen des Portals aus Umgebung/.env.

Alle Variablen tragen das Präfix PORTAL_, damit Portal und Hauptsystem im selben Prozess
(Ende-zu-Ende-Test) nicht dieselbe DATABASE_URL oder denselben SECRET_KEY lesen.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

INSECURE_SECRET = "change-me"  # noqa: S105 -- Marker-Wert, kein echtes Geheimnis


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PORTAL_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "postgresql+psycopg://beachhub:beachhub@localhost:5432/beachhub_portal"
    secret_key: str = INSECURE_SECRET
    app_env: str = "dev"  # dev | production
    cookie_secure: bool = False
    base_url: str = "http://127.0.0.1:8001"
    data_dir: Path = Path("./data")
    kanal_token: str = ""
    core_public_key: str = ""  # Ed25519, hex – `beachhub-core keygen` bzw. System-Seite
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = "portal@example.org"
    betreiber_name: str = "Beachhalle"
    betreiber_email: str = ""
    fake_zahlung: bool = False
    webhook_signatur_header: str = "Stripe-Signature"  # kommagetrennt
    enable_scheduler: bool = True

    @property
    def produktionsfehler(self) -> list[str]:
        """Einstellungen, mit denen das Portal im Produktivbetrieb nicht starten darf."""
        fehler: list[str] = []
        if self.secret_key == INSECURE_SECRET:
            fehler.append("PORTAL_SECRET_KEY ist der Standardwert")
        if self.fake_zahlung:
            fehler.append("PORTAL_FAKE_ZAHLUNG ist nur für die Entwicklung")
        if not self.kanal_token:
            fehler.append("PORTAL_KANAL_TOKEN fehlt")
        if not self.core_public_key:
            fehler.append("PORTAL_CORE_PUBLIC_KEY fehlt")
        if not self.cookie_secure:
            fehler.append("PORTAL_COOKIE_SECURE muss im Produktivbetrieb true sein")
        return fehler


settings = Settings()
