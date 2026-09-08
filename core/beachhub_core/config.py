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
    betreiber_name: str = "Beachhalle"
    betreiber_adresse: str = ""
    betreiber_ust_id: str = ""
    betreiber_bank: str = ""

    @property
    def has_insecure_defaults(self) -> bool:
        return self.secret_key == INSECURE_SECRET or self.pin_schluessel == INSECURE_SECRET


settings = Settings()
