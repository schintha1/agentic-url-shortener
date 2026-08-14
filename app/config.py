from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///data/shortener.db"
    runs_dir: str = "runs"
    base_url: str = "http://localhost:8000"
    rate_limit_per_minute: int = 30
    allow_private_targets: bool = True
