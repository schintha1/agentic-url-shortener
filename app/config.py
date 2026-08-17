from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///data/shortener.db"
    runs_dir: str = "runs"
    base_url: str = "http://localhost:8000"
    rate_limit_per_minute: int = 30
    allow_private_targets: bool = True
    domain_test_target: str = "tests/test_shortener.py"
    # When set, every /sdlc route requires this key in X-API-Key. Unset means the
    # control plane is open, which is acceptable only for a local demo.
    sdlc_api_key: str = ""
    click_retention_days: int = 30
    log_level: str = "INFO"
    allow_failure_injection: bool = False
