"""Owner-editable execution settings (resource limits, authorised targets)."""

from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.models import Setting
from app.security.ssrf import normalise_allow_entry

_KEY = "execution"


class ExecutionSettings(BaseModel):
    """Defaults are conservative for a 16 GB machine where Docker is capped at 4 GB."""

    allowed_targets: list[str] = Field(default_factory=list, max_length=50)
    sandbox_cpus: float = Field(default=1.0, ge=0.25, le=4)
    sandbox_memory_mb: int = Field(default=768, ge=128, le=3072)
    sandbox_pids_limit: int = Field(default=256, ge=32, le=1024)
    sandbox_tmpfs_mb: int = Field(default=256, ge=16, le=2048)
    sandbox_install_timeout_s: int = Field(default=420, ge=30, le=1800)
    sandbox_test_timeout_s: int = Field(default=300, ge=10, le=1800)
    scanner_timeout_s: int = Field(default=600, ge=30, le=1800)
    browser_max_pages: int = Field(default=8, ge=1, le=50)
    browser_page_timeout_s: int = Field(default=20, ge=5, le=120)
    api_request_timeout_s: int = Field(default=10, ge=1, le=60)
    api_max_requests_per_run: int = Field(default=150, ge=1, le=1000)
    perf_samples: int = Field(default=10, ge=3, le=50)
    perf_slow_threshold_ms: int = Field(default=800, ge=50, le=30_000)
    max_upload_mb: int = Field(default=100, ge=1, le=1024)
    max_extracted_mb: int = Field(default=300, ge=1, le=4096)
    max_files: int = Field(default=20_000, ge=10, le=200_000)

    @field_validator("allowed_targets")
    @classmethod
    def _normalise(cls, entries: list[str]) -> list[str]:
        return sorted({normalise_allow_entry(entry) for entry in entries if entry.strip()})


def get_execution_settings(db: Session) -> ExecutionSettings:
    row = db.get(Setting, _KEY)
    return ExecutionSettings.model_validate(row.value) if row else ExecutionSettings()


def save_execution_settings(db: Session, settings: ExecutionSettings) -> ExecutionSettings:
    row = db.get(Setting, _KEY)
    if row:
        row.value = settings.model_dump()
    else:
        db.add(Setting(key=_KEY, value=settings.model_dump()))
    return settings
