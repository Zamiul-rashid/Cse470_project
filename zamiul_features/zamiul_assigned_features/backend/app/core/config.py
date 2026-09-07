"""Application settings.

Every value has a working default so a missing .env is never fatal; see
.env.example at the repo root for the overridable set.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> core -> app -> backend -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "NoteVault"
    api_prefix: str = "/api/v1"

    # --- auth ---------------------------------------------------------
    secret_key: str = "change-me-before-you-demo-this"
    algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 14

    # --- database -----------------------------------------------------
    database_url: str = f"sqlite+aiosqlite:///{REPO_ROOT / 'notevault.db'}"

    # --- uploads (FR 1.1 / 1.4) --------------------------------------
    max_upload_mb: int = 25
    preview_pages: int = 3

    # --- moderation (FR 4.1) -----------------------------------------
    auto_approve_listings: bool = False

    # --- QR handoff (FR 3.3) -----------------------------------------
    qr_ttl_minutes: int = 10

    # --- rental reminders (FR 3.2) -----------------------------------
    reminder_hour: int = 8
    enable_scheduler: bool = True

    # --- recommendation engine (FR 1.5 / 3.1) ------------------------
    duplicate_title_threshold: int = 85
    price_min_sample: int = 3
    price_lookback_days: int = 180

    # --- analytics (FR 4.2 / 4.4) ------------------------------------
    trending_window_days: int = 30
    analytics_cache_seconds: int = 300

    # --- serving ------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    host: str = "0.0.0.0"
    port: int = 8000

    @field_validator("database_url")
    @classmethod
    def _resolve_relative_sqlite_path(cls, value: str) -> str:
        """Make ``sqlite+aiosqlite:///./notevault.db`` independent of cwd.

        Without this, running uvicorn from backend/ and pytest from the repo
        root would silently use two different database files.
        """
        prefix = "sqlite+aiosqlite:///"
        if value.startswith(prefix):
            path_part = value[len(prefix) :]
            if path_part.startswith("./") or not path_part.startswith("/"):
                resolved = (REPO_ROOT / path_part.lstrip("./")).resolve()
                return f"{prefix}{resolved}"
        return value

    # --- derived ------------------------------------------------------
    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    @property
    def storage_dir(self) -> Path:
        return REPO_ROOT / "storage"

    @property
    def materials_dir(self) -> Path:
        return self.storage_dir / "materials"

    @property
    def previews_dir(self) -> Path:
        return self.storage_dir / "previews"

    @property
    def qr_dir(self) -> Path:
        return self.storage_dir / "qr"

    @property
    def frontend_dist(self) -> Path:
        return REPO_ROOT / "frontend" / "dist"

    @property
    def sync_database_url(self) -> str:
        """Alembic runs migrations synchronously; derive its URL from ours."""
        return self.database_url.replace("+aiosqlite", "")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def ensure_storage_dirs(self) -> None:
        for directory in (self.materials_dir, self.previews_dir, self.qr_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
