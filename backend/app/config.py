"""Application configuration loaded from environment / the repository-level .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QAI_", env_file=REPO_ROOT / ".env", env_file_encoding="utf-8-sig", extra="ignore"
    )

    database_url: str
    test_database_url: str | None = None
    api_token: SecretStr
    data_dir: Path = REPO_ROOT / "data"
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"
    ollama_base_url: str = "http://127.0.0.1:11434"
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    openai_api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")

    @field_validator("data_dir")
    @classmethod
    def _absolute_data_dir(cls, value: Path) -> Path:
        return value if value.is_absolute() else (REPO_ROOT / value).resolve()

    @field_validator("api_token")
    @classmethod
    def _strong_token(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 24 or value.get_secret_value().startswith("change-me"):
            raise ValueError("QAI_API_TOKEN must be a generated secret (run scripts/setup-env.ps1)")
        return value

    def provider_key(self, provider: str) -> str | None:
        """Return the API key for a paid provider, or None when it is not configured."""
        secret = {"anthropic": self.anthropic_api_key, "openai": self.openai_api_key}.get(provider)
        value = secret.get_secret_value().strip() if secret else ""
        return value or None

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def repairs_dir(self) -> Path:
        return self.data_dir / "repairs"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()  # values come from the environment / .env
    for directory in (
        settings.workspaces_dir,
        settings.evidence_dir,
        settings.repairs_dir,
        settings.reports_dir,
        settings.cache_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return settings
